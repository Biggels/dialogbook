"""M2 through the app: the preview panel, and prompts running on real assembled context.

The unit tests in `test_m2_context.py` pin the assembler's behaviour. These check it is
actually wired up — that what the preview shows is what the provider receives.
"""
import pytest
from starlette.testclient import TestClient

from dialogbook.app import create_app
from dialogbook.config import Config
from dialogbook.providers.base import Done, TextDelta

from test_m1_app import add_cell, new_dialog, sse_chunks


class RecordingProvider:
    "A stub that keeps the messages it was handed, so tests can assert on the real context."

    name = 'recording'

    def __init__(self): self.calls = []

    async def stream(self, messages, tools=None, **kw):
        self.calls.append(messages)
        yield TextDelta('ok')
        yield Done()

    @property
    def last(self): return self.calls[-1]


@pytest.fixture
def rec(): return RecordingProvider()


@pytest.fixture
def client(tmp_path, rec):
    app = create_app(Config(workspace=tmp_path, save_debounce=0), provider=rec)
    with TestClient(app) as c:
        c.workspace = tmp_path
        yield c


def ask(client, did, cid):
    client.post(f'/d/{did}/cells/{cid}/run')
    return sse_chunks(client, f'/d/{did}/cells/{cid}/stream')


# ---------------------------------------------------------------- the wiring

def test_a_prompt_receives_the_cells_above_it(client, rec):
    did = new_dialog(client)
    add_cell(client, did, 'note', 'Remember: the answer is 42.')
    p = add_cell(client, did, 'prompt', 'What is the answer?')
    ask(client, did, p)

    assert len(rec.calls) == 1
    body = rec.last[0]['content']
    assert 'Remember: the answer is 42.' in body
    assert body.endswith('What is the answer?')


def test_code_output_reaches_the_model(client, rec):
    "The whole point of a persistent kernel: the model sees what the code actually printed."
    did = new_dialog(client)
    c = add_cell(client, did, 'code', 'print("the kernel says hello")')
    client.post(f'/d/{did}/cells/{c}/run')
    sse_chunks(client, f'/d/{did}/cells/{c}/stream')

    p = add_cell(client, did, 'prompt', 'What did it say?')
    ask(client, did, p)
    body = rec.last[0]['content']
    assert '```python' in body and 'the kernel says hello' in body


def test_hidden_cells_never_reach_the_model(client, rec):
    did = new_dialog(client)
    h = add_cell(client, did, 'note', 'DO-NOT-SEND')
    client.post(f'/d/{did}/cells/{h}/hide')
    p = add_cell(client, did, 'prompt', 'go')
    ask(client, did, p)
    assert 'DO-NOT-SEND' not in str(rec.last)


def test_an_earlier_reply_comes_back_as_an_assistant_turn(client, rec):
    did = new_dialog(client)
    p1 = add_cell(client, did, 'prompt', 'first')
    ask(client, did, p1)
    p2 = add_cell(client, did, 'prompt', 'second')
    ask(client, did, p2)

    roles = [m['role'] for m in rec.last]
    assert roles == ['user', 'assistant', 'user']
    assert rec.last[1]['content'] == 'ok', "the first prompt's reply"


def test_context_is_recomputed_not_cached(client, rec):
    "Editing a cell above a prompt must change what the next run sends."
    did = new_dialog(client)
    n = add_cell(client, did, 'note', 'first version')
    p = add_cell(client, did, 'prompt', 'go')
    ask(client, did, p)
    assert 'first version' in str(rec.last)

    client.patch(f'/d/{did}/cells/{n}', data={'content': 'second version'})
    ask(client, did, p)
    assert 'second version' in str(rec.last) and 'first version' not in str(rec.last)


# ---------------------------------------------------------------- the panel

def test_preview_shows_the_turns_that_will_be_sent(client):
    did = new_dialog(client)
    add_cell(client, did, 'note', 'a pinned fact')
    p = add_cell(client, did, 'prompt', 'the question')
    r = client.get(f'/d/{did}/cells/{p}/preview')
    assert r.status_code == 200
    assert 'Context preview' in r.text
    assert 'a pinned fact' in r.text and 'the question' in r.text
    assert 'tokens' in r.text and 'cells included' in r.text


def test_preview_matches_what_the_provider_receives(client, rec):
    "If these ever diverge, the panel is lying — which is the one thing it must not do."
    did = new_dialog(client)
    add_cell(client, did, 'note', 'context line')
    add_cell(client, did, 'code', 'x = 1')
    p = add_cell(client, did, 'prompt', 'what now?')

    preview = client.get(f'/d/{did}/cells/{p}/preview').text
    ask(client, did, p)
    for turn in rec.last:
        for line in turn['content'].splitlines():
            if line.strip() and '`' not in line:
                assert line.strip() in preview, f'{line!r} was sent but not shown in the preview'


def test_preview_reports_hidden_cells(client):
    did = new_dialog(client)
    h = add_cell(client, did, 'note', 'hidden thing')
    client.post(f'/d/{did}/cells/{h}/hide')
    p = add_cell(client, did, 'prompt', 'go')
    r = client.get(f'/d/{did}/cells/{p}/preview')
    assert '1 hidden' in r.text
    assert 'hidden thing' not in r.text


def test_preview_warns_when_over_budget(tmp_path, rec):
    "A pinned cell too big for the budget must be announced, not silently truncated."
    app = create_app(Config(workspace=tmp_path, save_debounce=0, token_budget=50), provider=rec)
    with TestClient(app) as c:
        c.workspace = tmp_path
        did = new_dialog(c)
        n = add_cell(c, did, 'note', 'padding '*500)
        c.post(f'/d/{did}/cells/{n}/pin')
        p = add_cell(c, did, 'prompt', 'go')
        r = c.get(f'/d/{did}/cells/{p}/preview')
        assert 'Sending anyway' in r.text and 'unpin' in r.text

        chunks, _ = ask(c, did, p)
        assert any('Sending anyway' in ch for ch in chunks), 'the run surfaces it too'
        assert 'padding' in str(rec.last), 'pinned content is still sent'


def test_preview_closes(client):
    did = new_dialog(client)
    p = add_cell(client, did, 'prompt', 'go')
    r = client.get(f'/d/{did}/cells/{p}/preview/close')
    assert 'Context preview' not in r.text and f'preview-{p}' in r.text


def test_only_prompt_cells_offer_a_preview(client):
    did = new_dialog(client)
    c = add_cell(client, did, 'code', 'x = 1')
    p = add_cell(client, did, 'prompt', 'go')
    assert 'Show exactly what will be sent' not in client.get(f'/d/{did}/cells/{c}').text
    assert 'Show exactly what will be sent' in client.get(f'/d/{did}/cells/{p}').text
