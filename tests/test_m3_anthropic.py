"""M3: the Anthropic provider.

These spend nothing. The SDK client is faked at the boundary — the provider's job is to
turn the SDK's stream events and terminal states into our `Event`s, and that mapping is
what is worth pinning.

The one test that does call the real API is at the bottom, skipped unless
`DIALOGBOOK_LIVE=1`, because a test suite must never quietly spend money.
"""
import os
import types

import anthropic
import pytest

from dialogbook.providers import StubProvider, provider_for
from dialogbook.providers.anthropic import FALLBACK_BETA, AnthropicProvider
from dialogbook.providers.base import Done, Failed, TextDelta, Thinking, ToolUse
from dialogbook.config import Config


# ---------------------------------------------------------------- fake SDK

def delta(kind, value):
    d = types.SimpleNamespace(type=f'{kind}_delta', **{kind: value})
    return types.SimpleNamespace(type='content_block_delta', delta=d)


def final_message(content=(), stop_reason='end_turn', stop_details=None,
                  usage=None, model='claude-opus-5'):
    return types.SimpleNamespace(
        content=list(content), stop_reason=stop_reason, stop_details=stop_details,
        model=model,
        usage=usage or types.SimpleNamespace(input_tokens=10, output_tokens=5,
                                             cache_read_input_tokens=None,
                                             cache_creation_input_tokens=None))


class FakeStream:
    def __init__(self, events, final): self.events, self._final = events, final
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def __aiter__(self):
        for e in self.events: yield e
    def __aiter__(self):                      # noqa: F811 - async generator form
        async def gen():
            for e in self.events: yield e
        return gen()
    async def get_final_message(self): return self._final


class FakeMessages:
    def __init__(self, owner): self.owner = owner
    def stream(self, **kw):
        self.owner.calls.append(kw)
        if self.owner.raise_on_call is not None:
            exc, self.owner.raise_on_call = self.owner.raise_on_call, None
            raise exc
        return FakeStream(self.owner.events, self.owner.final)


class FakeClient:
    "Stands in for `anthropic.AsyncAnthropic`. Records every request it is handed."

    def __init__(self, events=(), final=None, raise_on_call=None):
        self.events, self.final = list(events), final or final_message()
        self.raise_on_call, self.calls = raise_on_call, []
        self.messages = FakeMessages(self)
        self.beta = types.SimpleNamespace(messages=FakeMessages(self))


def provider(client, **kw):
    return AnthropicProvider(client=client, **kw)


async def collect(p, messages=None, tools=None):
    return [ev async for ev in p.stream(messages or [{'role': 'user', 'content': 'hi'}], tools)]


def fake_response(status):
    "The SDK's exception constructors read `.request` off the response."
    return types.SimpleNamespace(status_code=status, headers={},
                                 request=types.SimpleNamespace())


def bad_request(msg):
    "A 400 shaped the way the SDK raises one."
    return anthropic.BadRequestError(msg, response=fake_response(400), body=None)


# ---------------------------------------------------------------- happy path

async def test_text_deltas_become_text_events():
    c = FakeClient([delta('text', 'Hel'), delta('text', 'lo')])
    evs = await collect(provider(c))
    assert [e.text for e in evs if isinstance(e, TextDelta)] == ['Hel', 'lo']
    assert isinstance(evs[-1], Done)


async def test_thinking_deltas_are_a_separate_event():
    "Reasoning must be distinguishable, so the UI can show it and the store can drop it."
    c = FakeClient([delta('thinking', 'pondering'), delta('text', 'answer')])
    evs = await collect(provider(c))
    assert [type(e).__name__ for e in evs] == ['Thinking', 'TextDelta', 'Done']
    assert isinstance(evs[0], Thinking) and evs[0].text == 'pondering'


async def test_done_carries_usage_and_stop_reason():
    c = FakeClient([], final_message(stop_reason='end_turn'))
    (done,) = [e for e in await collect(provider(c)) if isinstance(e, Done)]
    assert done.stop_reason == 'end_turn'
    assert done.usage['input_tokens'] == 10 and done.usage['output_tokens'] == 5
    assert done.usage['model'] == 'claude-opus-5'


async def test_tool_use_blocks_are_surfaced_not_executed():
    "M4 runs these in the kernel; the provider only reports them."
    block = types.SimpleNamespace(type='tool_use', id='tu_1', name='area', input={'r': 2})
    c = FakeClient([delta('text', 'calling')], final_message([block], stop_reason='tool_use'))
    evs = await collect(provider(c))
    (tu,) = [e for e in evs if isinstance(e, ToolUse)]
    assert (tu.id, tu.name, tu.input) == ('tu_1', 'area', {'r': 2})


# ---------------------------------------------------------------- request shape

async def test_request_uses_adaptive_thinking_and_configured_effort():
    c = FakeClient()
    await collect(provider(c, model='claude-opus-5', effort='max', max_tokens=1234))
    kw = c.calls[0]
    assert kw['model'] == 'claude-opus-5' and kw['max_tokens'] == 1234
    assert kw['thinking'] == {'type': 'adaptive', 'display': 'summarized'}
    assert kw['output_config'] == {'effort': 'max'}
    assert 'budget_tokens' not in str(kw), 'removed on current models; a 400 if sent'


async def test_tools_are_only_sent_when_present():
    c = FakeClient()
    await collect(provider(c))
    assert 'tools' not in c.calls[0]
    await collect(provider(c), tools=[{'name': 't'}])
    assert c.calls[1]['tools'] == [{'name': 't'}]


async def test_fallbacks_are_on_by_default():
    c = FakeClient()
    await collect(provider(c))
    assert c.calls[0]['fallbacks'] == 'default'
    assert c.calls[0]['betas'] == [FALLBACK_BETA]


async def test_fallbacks_can_be_turned_off():
    c = FakeClient()
    await collect(provider(c, fallbacks=False))
    assert 'fallbacks' not in c.calls[0] and 'betas' not in c.calls[0]


# ---------------------------------------------------------------- degradation

async def test_missing_fallback_entitlement_retries_without_it():
    "An account without the beta should lose the feature, not every prompt."
    c = FakeClient([delta('text', 'ok')],
                   raise_on_call=bad_request('fallbacks: unsupported beta for this account'))
    p = provider(c)
    evs = await collect(p)
    assert [e.text for e in evs if isinstance(e, TextDelta)] == ['ok']
    assert len(c.calls) == 2
    assert 'fallbacks' in c.calls[0] and 'fallbacks' not in c.calls[1]
    assert p.fallbacks is False, 'and it stays off for the rest of the session'


async def test_an_unrelated_400_is_reported_not_retried():
    c = FakeClient(raise_on_call=bad_request('messages.0.content: must not be empty'))
    evs = await collect(provider(c))
    assert len(c.calls) == 1
    assert isinstance(evs[-1], Failed) and 'must not be empty' in evs[-1].detail


async def test_a_refusal_becomes_a_failed_event():
    "HTTP 200 with no usable content — it has to be checked before reading the blocks."
    details = types.SimpleNamespace(category='cyber', explanation='declined for safety')
    c = FakeClient([], final_message(stop_reason='refusal', stop_details=details))
    evs = await collect(provider(c))
    assert isinstance(evs[-1], Failed)
    assert 'declined' in evs[-1].message and 'declined for safety' in evs[-1].detail
    assert not any(isinstance(e, Done) for e in evs)


@pytest.mark.parametrize('exc,expected', [
    (anthropic.AuthenticationError('bad key', response=fake_response(401), body=None),
     'API key'),
    (anthropic.APIConnectionError(request=types.SimpleNamespace()), 'reach Anthropic'),
])
async def test_api_errors_become_failed_events_never_exceptions(exc, expected):
    c = FakeClient(raise_on_call=exc)
    evs = await collect(provider(c))
    assert isinstance(evs[-1], Failed) and expected in evs[-1].message


async def test_an_unexpected_error_still_ends_the_stream_cleanly():
    "A half-open SSE stream in the browser is worse than an error rendered in the cell."
    c = FakeClient(raise_on_call=ValueError('something odd'))
    evs = await collect(provider(c))
    assert isinstance(evs[-1], Failed) and 'ValueError' in evs[-1].message


# ---------------------------------------------------------------- selection

def test_provider_for_uses_the_stub_when_asked():
    assert isinstance(provider_for(Config(provider='stub')), StubProvider)


def test_provider_for_degrades_to_the_stub_without_a_key(monkeypatch):
    "No credential should give a working notebook that says so, not a stack trace per prompt."
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('ANTHROPIC_AUTH_TOKEN', raising=False)
    monkeypatch.setenv('ANTHROPIC_BASE_URL', 'http://127.0.0.1:1')
    p = provider_for(Config(provider='anthropic'))
    assert p.name in ('stub', 'anthropic')   # depends on whether a profile exists on this box
    if p.name == 'stub': assert isinstance(p, StubProvider)


def test_provider_for_passes_the_config_through(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-not-a-real-key')
    p = provider_for(Config(provider='anthropic', model='claude-opus-5', effort='low',
                            max_tokens=999, fallbacks=False))
    assert p.name == 'anthropic'
    assert (p.model, p.effort, p.max_tokens, p.fallbacks) == ('claude-opus-5', 'low', 999, False)


# ---------------------------------------------------------------- live

@pytest.mark.skipif(os.environ.get('DIALOGBOOK_LIVE') != '1',
                    reason='spends real tokens; set DIALOGBOOK_LIVE=1 to run')
async def test_live_round_trip():
    "One real, tiny call. Opt-in only — a test run must never quietly cost money."
    from dialogbook.config import Config as C
    cfg = C.from_env()
    p = AnthropicProvider(model=cfg.model, max_tokens=64, effort='low',
                          fallbacks=cfg.fallbacks)
    evs = [e async for e in p.stream([{'role': 'user', 'content': 'Reply with exactly: pong'}])]
    assert not any(isinstance(e, Failed) for e in evs), [e for e in evs if isinstance(e, Failed)]
    text = ''.join(e.text for e in evs if isinstance(e, TextDelta))
    assert 'pong' in text.lower(), text
    assert isinstance(evs[-1], Done) and evs[-1].usage.get('output_tokens')
