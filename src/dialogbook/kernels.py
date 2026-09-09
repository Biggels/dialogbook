"""The managed `jupyter server` child, and one kernel per open dialog.

Two rules shape this module:

- **A kernel dies only when asked.** No idle reaping. Silently killing a kernel destroys
  accumulated state, which is the one thing a notebook must never do. One Python process
  per open dialog is fine for a single user.
- **The child must not outlive us.** Windows has no POSIX process groups, so teardown is
  `taskkill /F /T` (or `killpg` elsewhere), and a pidfile lets the next run clean up after
  a crash instead of leaking a process that still holds a port.
"""
import asyncio
import json
import logging
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import psutil
from jupyasyncclient import JupyAsyncMultiKernelManager

log = logging.getLogger('dialogbook.kernels')

STARTUP_TIMEOUT = 60
# How long shutdown waits for an in-flight kernel warm-up before giving up on it.
STARTUP_GRACE = 30
IS_WINDOWS = sys.platform == 'win32'


def free_port():
    "Ask the OS for an unused port. Racy in principle; fine for one local process."
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def kill_tree(pid, timeout=20):
    "Kill `pid` and every descendant. Returns True if the tree is gone."
    if IS_WINDOWS:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)], capture_output=True)
    else:
        try: os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError): pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not psutil.pid_exists(pid): return True
        time.sleep(0.1)
    return not psutil.pid_exists(pid)


class JupyterServer:
    """A headless `jupyter server` subprocess we own end to end.

    Spawned with `cwd=workspace`: the kernel inherits the *server process's* working
    directory, not `ServerApp.root_dir`, so this is what makes relative paths in code
    cells resolve against the workspace. See docs/notes-upstream.md.
    """

    def __init__(self, workspace, statedir=None):
        self.workspace = Path(workspace)
        self.pidfile = Path(statedir or self.workspace)/'.dialogbook-server.json'
        self.proc = self.port = self.token = None
        self._closed = False
        self._lock = asyncio.Lock()

    @property
    def base_url(self): return f'http://127.0.0.1:{self.port}'

    @property
    def running(self): return self.proc is not None and self.proc.poll() is None

    def reap_orphan(self):
        """Kill a server left behind by a crashed run.

        The pid is matched against its recorded creation time, so a recycled pid
        belonging to some unrelated process is never killed.
        """
        try: rec = json.loads(self.pidfile.read_text())
        except (OSError, ValueError): return False
        pid = rec.get('pid')
        try: proc = psutil.Process(pid)
        except (psutil.NoSuchProcess, ValueError, TypeError):
            self.pidfile.unlink(missing_ok=True)
            return False
        if abs(proc.create_time() - rec.get('create_time', -1)) > 1:
            self.pidfile.unlink(missing_ok=True)  # pid was recycled; not ours
            return False
        log.warning('reaping orphaned jupyter server pid=%s from a previous run', pid)
        kill_tree(pid)
        self.pidfile.unlink(missing_ok=True)
        return True

    def _spawn(self):
        self.port, self.token = free_port(), secrets.token_urlsafe(32)
        cmd = [sys.executable, '-m', 'jupyter_server', '--no-browser',
               f'--ServerApp.port={self.port}', '--ServerApp.ip=127.0.0.1',
               f'--IdentityProvider.token={self.token}',
               f'--ServerApp.root_dir={self.workspace}',
               '--ServerApp.open_browser=False', '--ServerApp.disable_check_xsrf=True']
        kw = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if IS_WINDOWS else {'start_new_session': True}
        # `python -m jupyter_server`, not `python -m jupyter server`: the dispatcher adds
        # several intermediate processes between this handle and the actual server.
        self.proc = subprocess.Popen(cmd, cwd=self.workspace, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, **kw)
        try:
            p = psutil.Process(self.proc.pid)
            self.pidfile.write_text(json.dumps({'pid': p.pid, 'create_time': p.create_time(),
                                                'port': self.port}))
        except psutil.NoSuchProcess:
            pass

    def _await_ready(self, timeout=STARTUP_TIMEOUT):
        req = urllib.request.Request(f'{self.base_url}/api/status',
                                     headers={'Authorization': f'token {self.token}'})
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f'jupyter server exited with code {self.proc.returncode}')
            try:
                if urllib.request.urlopen(req, timeout=2).status == 200: return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.15)
        self._kill()   # not `stop()`: a slow start should not permanently close the server
        raise TimeoutError(f'jupyter server did not become ready within {timeout}s')

    async def start(self):
        """Idempotent, and safe to call concurrently.

        The lock matters: without it two dialogs opening at once each see `running` as
        False and spawn a server, and only one of them is ever tracked — the other leaks a
        process holding a port. The `_closed` checks matter for the same reason at the
        other end: `stop()` can land while a spawn is in flight, and the process that
        appears afterwards would never be killed.
        """
        async with self._lock:
            if self._closed: raise RuntimeError('jupyter server has been shut down')
            if self.running: return self
            self.reap_orphan()
            await asyncio.to_thread(self._spawn)
            if self._closed:                      # stop() ran while we were spawning
                self._kill()
                raise RuntimeError('jupyter server shut down during startup')
            await asyncio.to_thread(self._await_ready)
            log.info('jupyter server ready at %s (pid %s)', self.base_url, self.proc.pid)
            return self

    def _kill(self):
        if self.proc is not None and self.proc.poll() is None: kill_tree(self.proc.pid)
        self.pidfile.unlink(missing_ok=True)
        self.proc = None

    def stop(self):
        "Kill the child and refuse further starts. Safe to call twice."
        self._closed = True
        self._kill()


class KernelRegistry:
    """`dialog_id -> (kernel_id, client)`, one entry per open dialog.

    Entries are created on open and removed only on explicit close, restart, or app
    shutdown.
    """

    def __init__(self, server, kernel_name='python3'):
        self.server, self.kernel_name = server, kernel_name
        self._mm = None
        self._kernels = {}          # dialog_id -> (kernel_id, client)
        self._locks = {}            # dialog_id -> asyncio.Lock, so one open cannot race another

    def _lock(self, dialog_id):
        return self._locks.setdefault(dialog_id, asyncio.Lock())

    @property
    def open_dialogs(self): return set(self._kernels)

    def is_open(self, dialog_id): return dialog_id in self._kernels

    async def _mgr(self):
        await self.server.start()
        if self._mm is None:
            self._mm = JupyAsyncMultiKernelManager(self.server.base_url, token=self.server.token,
                                                   kernel_name=self.kernel_name)
        return self._mm

    async def client_for(self, dialog_id):
        "The live client for `dialog_id`, starting a kernel on first use."
        async with self._lock(dialog_id):
            if (entry := self._kernels.get(dialog_id)): return entry[1]
            mm = await self._mgr()
            kid = await mm.start_kernel()
            kc = mm.client(kid).start_channels()
            try:
                await kc.wait_for_ready(timeout=STARTUP_TIMEOUT)
            except Exception:
                await kc.aclose()
                await mm.shutdown_kernel(kid, now=True)
                raise
            self._kernels[dialog_id] = (kid, kc)
            log.info('kernel %s started for dialog %s', kid, dialog_id)
            return kc

    async def restart(self, dialog_id):
        "Explicit restart: the state is discarded because the user asked for it."
        async with self._lock(dialog_id):
            entry = self._kernels.pop(dialog_id, None)
        if entry is None: return None
        kid, kc = entry
        await kc.aclose()
        mm = await self._mgr()
        await mm.shutdown_kernel(kid, now=True)
        return await self.client_for(dialog_id)

    async def close(self, dialog_id):
        async with self._lock(dialog_id):
            entry = self._kernels.pop(dialog_id, None)
        if entry is None: return
        kid, kc = entry
        await kc.aclose()
        try: await (await self._mgr()).shutdown_kernel(kid, now=True)
        except Exception as e: log.warning('shutting down kernel %s: %s', kid, e)
        log.info('kernel %s closed for dialog %s', kid, dialog_id)

    async def shutdown_all(self):
        "Close every kernel, then the server. Safe to call twice."
        for did in list(self._kernels):
            try: await self.close(did)
            except Exception as e: log.warning('closing dialog %s: %s', did, e)
        self.server.stop()
