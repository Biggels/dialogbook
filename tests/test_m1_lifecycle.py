"""The app must not leak `jupyter server` processes.

This is the failure the design calls out by name: a crashed or careless run leaving a
Python process holding a port. It was a real bug — opening a dialog fires a background
kernel warm-up, and shutdown could complete while that spawn was still in flight, so the
process that appeared a moment later was never owned by anyone.
"""
import asyncio

import psutil
import pytest
from starlette.testclient import TestClient

from dialogbook.app import create_app
from dialogbook.config import Config
from dialogbook.kernels import JupyterServer, KernelRegistry
from dialogbook.providers import StubProvider


def jupyter_pids():
    "Every live `python -m jupyter_server`, whoever started it."
    out = set()
    for p in psutil.process_iter(['cmdline']):
        cl = p.info['cmdline'] or []
        if len(cl) >= 3 and cl[1] == '-m' and cl[2] == 'jupyter_server': out.add(p.pid)
    return out


def test_app_lifecycle_leaves_no_jupyter_server(tmp_path):
    "Open a dialog (which warms a kernel), then shut down. Nothing may survive."
    before = jupyter_pids()
    app = create_app(Config(workspace=tmp_path, save_debounce=0), provider=StubProvider(delay=0))
    with TestClient(app) as c:
        r = c.post('/dialogs', data={'title': 'Leak Check'}, follow_redirects=False)
        did = r.headers['location'].removeprefix('/d/')
        assert c.get(f'/d/{did}').status_code == 200
        cid_page = c.get(f'/d/{did}').text
        assert 'cell-' in cid_page
    assert jupyter_pids() - before == set(), 'a jupyter server outlived the app'


def test_shutdown_during_startup_still_kills_the_server(tmp_path):
    """The nastiest ordering: `stop()` lands while the spawn is in flight.

    Cancelling the task is not enough — `asyncio.to_thread` keeps running — so the server
    checks `_closed` after spawning and kills what it just started.
    """
    before = jupyter_pids()

    async def go():
        server = JupyterServer(tmp_path)
        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.4)     # let Popen happen, but not readiness
        server.stop()
        with pytest.raises(Exception):
            await task
        return server

    server = asyncio.run(go())
    assert not server.running
    assert jupyter_pids() - before == set(), 'the in-flight server survived shutdown'


def test_concurrent_opens_start_exactly_one_server(tmp_path):
    "Without a lock each caller spawns its own server and all but one leak."
    before = jupyter_pids()

    async def go():
        server = JupyterServer(tmp_path)
        reg = KernelRegistry(server)
        try:
            await asyncio.gather(*(reg.client_for(f'd{i}') for i in range(3)))
            assert len(jupyter_pids() - before) <= 2, 'more than one server tree was started'
            assert len(reg.open_dialogs) == 3, 'each dialog still gets its own kernel'
        finally:
            await reg.shutdown_all()

    asyncio.run(go())
    assert jupyter_pids() - before == set()


def test_a_closed_server_refuses_to_restart(tmp_path):
    async def go():
        server = JupyterServer(tmp_path)
        server.stop()
        with pytest.raises(RuntimeError):
            await server.start()

    asyncio.run(go())
