"""M1: the walking skeleton, end to end through real HTTP.

Real kernel, stub model. These drive the app the way the browser does — form posts, HTMX
fragment requests, SSE streams — and assert on what comes back and on what lands in the
`.ipynb` on disk.
"""
import json
import re

import pytest
from starlette.testclient import TestClient

from dialogbook.app import create_app
from dialogbook.config import Config
from dialogbook.providers import StubProvider


@pytest.fixture
def client(tmp_path):
    app = create_app(Config(workspace=tmp_path, save_debounce=0),
                     provider=StubProvider(reply='canned reply', delay=0))
    with TestClient(app) as c:
        c.workspace = tmp_path
        yield c


def new_dialog(client, title='Test Dialog'):
    r = client.post('/dialogs', data={'title': title}, follow_redirects=False)
    assert r.status_code == 303
    return r.headers['location'].removeprefix('/d/')


def add_cell(client, did, type, content=''):
    r = client.post(f'/d/{did}/cells', params={'type': type, 'after': ''})
    assert r.status_code == 200
    cid = re.search(r'id="cell-([0-9a-f]+)"', r.text).group(1)
    if content:
        r = client.patch(f'/d/{did}/cells/{cid}', data={'content': content})
        assert r.status_code == 200
    return cid


def sse_chunks(client, url):
    "Collect the data payloads of every `chunk` event, in order."
    with client.stream('GET', url) as r:
        assert r.status_code == 200
        body = ''.join(r.iter_text())
    out, event = [], None
    for line in body.splitlines():
        if line.startswith('event: '): event = line[7:]
        elif line.startswith('data: ') and event == 'chunk': out.append(line[6:])
        elif not line: event = None
    return out, body


def nb(client, did):
    return json.loads((client.workspace/f'{did}.ipynb').read_text(encoding='utf-8'))


def text(v):
    "nbformat stores multi-line strings as lists of lines."
    return ''.join(v) if isinstance(v, (list, tuple)) else (v or '')


# ---------------------------------------------------------------- dialogs

def test_index_lists_created_dialogs(client):
    assert 'No dialogs yet' in client.get('/').text
    new_dialog(client, 'My Notebook')
    r = client.get('/')
    assert 'my-notebook' in r.text


def test_create_writes_a_valid_notebook(client):
    did = new_dialog(client, 'My Notebook')
    assert did == 'my-notebook'
    doc = nb(client, did)
    assert doc['nbformat'] == 4
    assert doc['cells'][0]['cell_type'] == 'markdown'
    assert '# My Notebook' in ''.join(doc['cells'][0]['source'])


def test_open_unknown_dialog_redirects_home(client):
    assert client.get('/d/nope', follow_redirects=False).status_code == 303


# ---------------------------------------------------------------- cells

def test_add_edit_delete_round_trips_to_disk(client):
    did = new_dialog(client)
    cid = add_cell(client, did, 'code', 'x = 1')
    assert 'x = 1' in client.get(f'/d/{did}/cells/{cid}').text

    sources = [''.join(c['source']) for c in nb(client, did)['cells']]
    assert 'x = 1' in sources

    assert client.delete(f'/d/{did}/cells/{cid}').text == ''
    assert 'x = 1' not in str(nb(client, did))


def test_crlf_from_the_browser_is_normalised(client):
    "Textareas post CRLF; notebooks everywhere else store plain \\n."
    did = new_dialog(client)
    cid = add_cell(client, did, 'code', 'a = 1\r\nb = 2\r\n')
    src = {c['id']: ''.join(c['source']) for c in nb(client, did)['cells']}[cid]
    assert '\r' not in src and src == 'a = 1\nb = 2\n'


def test_the_three_cell_types_render_distinctly(client):
    did = new_dialog(client)
    note = add_cell(client, did, 'note', '# Heading')
    code = add_cell(client, did, 'code', 'print(1)')
    prompt = add_cell(client, did, 'prompt', 'what is this?')
    page = client.get(f'/d/{did}').text
    assert 'cell-note' in page and 'cell-code' in page and 'cell-prompt' in page
    assert '<h1' in page.lower(), 'the note renders as markdown, not raw text'

    cells = {c['id']: c for c in nb(client, did)['cells']}
    assert cells[note]['cell_type'] == 'markdown'
    assert cells[code]['cell_type'] == 'code' and 'solveit_ai' not in cells[code]['metadata']
    assert cells[prompt]['metadata']['solveit_ai'] is True
    assert ''.join(cells[prompt]['source']).startswith('%%prompt\n')


def test_pin_and_hide_persist_as_cell_metadata(client):
    did = new_dialog(client)
    cid = add_cell(client, did, 'note', 'context')
    client.post(f'/d/{did}/cells/{cid}/pin')
    client.post(f'/d/{did}/cells/{cid}/hide')
    meta = {c['id']: c['metadata'] for c in nb(client, did)['cells']}[cid]
    assert meta['pinned'] == 1 and meta['skipped'] == 1

    client.post(f'/d/{did}/cells/{cid}/pin')          # toggles back off
    meta = {c['id']: c['metadata'] for c in nb(client, did)['cells']}[cid]
    assert 'pinned' not in meta and meta['skipped'] == 1


def test_move_reorders_and_saves(client):
    did = new_dialog(client)
    a = add_cell(client, did, 'code', 'first')
    b = add_cell(client, did, 'code', 'second')
    client.post(f'/d/{did}/cells/{b}/move', params={'dir': 'up'})
    ids = [c['id'] for c in nb(client, did)['cells']]
    assert ids.index(b) < ids.index(a)


def test_move_past_the_end_is_a_no_op(client):
    did = new_dialog(client)
    a = add_cell(client, did, 'code', 'only')
    before = [c['id'] for c in nb(client, did)['cells']]
    client.post(f'/d/{did}/cells/{a}/move', params={'dir': 'down'})
    assert [c['id'] for c in nb(client, did)['cells']] == before


# ---------------------------------------------------------------- running

def test_code_cell_runs_against_the_real_kernel(client):
    did = new_dialog(client)
    cid = add_cell(client, did, 'code', 'print("from the kernel")')
    client.post(f'/d/{did}/cells/{cid}/run')
    chunks, body = sse_chunks(client, f'/d/{did}/cells/{cid}/stream')
    assert any('from the kernel' in c for c in chunks)
    assert 'event: done' in body, 'the stream must end with `done` so the client closes it'

    outs = {c['id']: c.get('outputs') for c in nb(client, did)['cells']}[cid]
    assert text(outs[0]['text']) == 'from the kernel\n', 'output is persisted to the notebook'


def test_kernel_state_persists_between_cells(client):
    did = new_dialog(client)
    a = add_cell(client, did, 'code', 'val = 6 * 7')
    b = add_cell(client, did, 'code', 'val')
    client.post(f'/d/{did}/cells/{a}/run')
    sse_chunks(client, f'/d/{did}/cells/{a}/stream')
    client.post(f'/d/{did}/cells/{b}/run')
    chunks, _ = sse_chunks(client, f'/d/{did}/cells/{b}/stream')
    assert any('42' in c for c in chunks)


def test_kernel_error_is_shown_and_saved(client):
    did = new_dialog(client)
    cid = add_cell(client, did, 'code', 'raise ValueError("boom")')
    client.post(f'/d/{did}/cells/{cid}/run')
    chunks, _ = sse_chunks(client, f'/d/{did}/cells/{cid}/stream')
    assert any('boom' in c for c in chunks)
    outs = {c['id']: c.get('outputs') for c in nb(client, did)['cells']}[cid]
    assert outs[0]['output_type'] == 'error' and outs[0]['ename'] == 'ValueError'


def test_rerunning_replaces_previous_output(client):
    did = new_dialog(client)
    cid = add_cell(client, did, 'code', 'print("one")')
    client.post(f'/d/{did}/cells/{cid}/run')
    sse_chunks(client, f'/d/{did}/cells/{cid}/stream')
    client.patch(f'/d/{did}/cells/{cid}', data={'content': 'print("two")'})
    client.post(f'/d/{did}/cells/{cid}/run')
    sse_chunks(client, f'/d/{did}/cells/{cid}/stream')
    outs = {c['id']: c.get('outputs') for c in nb(client, did)['cells']}[cid]
    assert len(outs) == 1 and text(outs[0]['text']) == 'two\n'


def test_prompt_streams_the_stub_reply_and_saves_it(client):
    did = new_dialog(client)
    cid = add_cell(client, did, 'prompt', 'hello there')
    client.post(f'/d/{did}/cells/{cid}/run')
    chunks, body = sse_chunks(client, f'/d/{did}/cells/{cid}/stream')
    assert 'canned reply' in ''.join(chunks).replace('</span>', '').replace('<span>', '')
    assert 'event: done' in body

    cell = {c['id']: c for c in nb(client, did)['cells']}[cid]
    out = cell['outputs'][0]
    assert out['metadata']['is_ai_res'] is True
    assert ''.join(out['data']['text/markdown']) == 'canned reply'
    assert client.get(f'/d/{did}').text.count('canned reply') >= 1, 'reply survives a reload'


def test_provider_failure_reaches_the_page_instead_of_500ing(client, tmp_path):
    app = create_app(Config(workspace=tmp_path/'ws2'), provider=StubProvider(fail='no api key'))
    with TestClient(app) as c:
        c.workspace = tmp_path/'ws2'
        did = new_dialog(c)
        cid = add_cell(c, did, 'prompt', 'hi')
        c.post(f'/d/{did}/cells/{cid}/run')
        chunks, body = sse_chunks(c, f'/d/{did}/cells/{cid}/stream')
        assert any('no api key' in ch for ch in chunks)
        assert 'event: done' in body
