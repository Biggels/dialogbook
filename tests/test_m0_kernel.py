"""M0(b) and M0(c): a stock `jupyter server` subprocess, driven by `jupyasyncclient`.

(b) cells run with persistent state, and the kernel's cwd is the workspace.
(c) teardown leaves no orphan process — Windows has no POSIX process groups, so this
    uses `taskkill /F /T`, which is what the app's shutdown path will have to do.
"""
import ast
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import psutil
import pytest
from pathlib import Path
from jupyasyncclient import JupyAsyncMultiKernelManager, start_new_server_kernel

pytestmark = pytest.mark.asyncio


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def kill_tree(proc):
    "Windows has no `killpg`; `taskkill /T` is the equivalent."
    subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True)
    proc.wait(timeout=20)


def kernel_cwd(out):
    "`os.getcwd()` comes back as a Python repr in text/plain; parse it rather than unescaping by hand."
    return Path(ast.literal_eval(out[0]['data']['text/plain'])).resolve()


def still_running(pid):
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@pytest.fixture
def server(tmp_path):
    "A headless `jupyter server` on a random port with a generated token, rooted at a temp workspace."
    ws, port, token = tmp_path, free_port(), secrets.token_urlsafe(24)
    proc = subprocess.Popen(
        [sys.executable, '-m', 'jupyter_server', '--no-browser',
         f'--ServerApp.port={port}', '--ServerApp.ip=127.0.0.1',
         f'--IdentityProvider.token={token}', f'--ServerApp.root_dir={ws}',
         '--ServerApp.open_browser=False', '--ServerApp.disable_check_xsrf=True'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        # The kernel inherits the *server process's* cwd, not `root_dir`. See test below.
        cwd=ws, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    base = f'http://127.0.0.1:{port}'
    req = urllib.request.Request(f'{base}/api/status', headers={'Authorization': f'token {token}'})
    for _ in range(200):
        try:
            if urllib.request.urlopen(req, timeout=2).status == 200: break
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.25)
    else:
        kill_tree(proc)
        pytest.fail('jupyter server never came up')
    try:
        yield base, token, ws, proc
    finally:
        if proc.poll() is None: kill_tree(proc)


async def test_persistent_state_and_outputs(server):
    base, token, ws, _ = server
    km, kc = await start_new_server_kernel(base, token=token)
    try:
        assert await kc.exec_outs('x = 41') == []
        out = await kc.exec_outs('x + 1')
        assert out[0]['data']['text/plain'] == '42', 'state persists across executions'

        out = await kc.exec_outs('print("hi"); import sys; print("bad", file=sys.stderr)')
        assert [(o['name'], o['text']) for o in out] == [('stdout', 'hi\n'), ('stderr', 'bad\n')]

        out = await kc.exec_outs('1/0')
        assert [o['ename'] for o in out] == ['ZeroDivisionError']
    finally:
        await kc.aclose()
        await km.shutdown_kernel(now=True)


async def test_kernel_cwd_is_the_workspace(server):
    """The kernel's cwd follows the *server subprocess's* cwd, NOT `ServerApp.root_dir`.

    The design handoff assumed `root_dir` set it. It does not: with root_dir alone the
    kernel started in whatever directory the app was launched from. The server must be
    spawned with `cwd=workspace` (as the fixture does) for relative paths in code cells
    to resolve predictably.
    """
    base, token, ws, _ = server
    km, kc = await start_new_server_kernel(base, token=token)
    try:
        out = await kc.exec_outs('import os; os.getcwd()')
        assert kernel_cwd(out) == ws.resolve()
    finally:
        await kc.aclose()
        await km.shutdown_kernel(now=True)


async def test_per_kernel_path_sets_cwd(server):
    "The kernels API honours a per-kernel `path` relative to `root_dir` — a per-dialog cwd, if ever wanted."
    base, token, ws, _ = server
    (ws/'sub').mkdir()
    mm = JupyAsyncMultiKernelManager(base, token=token)
    kid = await mm.start_kernel(path='sub')
    kc = mm.client(kid).start_channels()
    try:
        await kc.wait_for_ready(timeout=60)
        out = await kc.exec_outs('import os; os.getcwd()')
        assert kernel_cwd(out) == (ws/'sub').resolve()
    finally:
        await kc.aclose()
        await mm.shutdown_kernel(kid, now=True)


async def test_teardown_leaves_no_orphans(server):
    "Killing the server must take its kernel subprocesses with it."
    base, token, ws, proc = server
    km, kc = await start_new_server_kernel(base, token=token)
    await kc.exec_outs('x = 1')

    tree = [proc.pid] + [p.pid for p in psutil.Process(proc.pid).children(recursive=True)]
    assert len(tree) > 1, 'expected at least one kernel child to exist before teardown'

    await kc.aclose()
    kill_tree(proc)

    deadline, alive = time.time() + 15, None
    while time.time() < deadline:
        alive = [p for p in tree if still_running(p)]
        if not alive: break
        time.sleep(0.3)
    assert alive == [], f'orphaned processes survived teardown: {alive}'
