"""A provider that costs nothing and never touches the network.

This exists so the whole app — routes, SSE, saving, the UI — can be built and exercised
before a single token is spent, and so the tests can assert on exact output.

It is deliberately *slightly* useful: it echoes back what it was actually sent, which
makes it a working preview of context assembly (M2) as well as a stand-in for a model.
"""
import asyncio

from .base import Done, Failed, TextDelta

CHUNK = 24  # characters per delta, so the UI's streaming path gets genuinely exercised


def _last_user_text(messages):
    for m in reversed(messages):
        if m.get('role') != 'user': continue
        c = m.get('content')
        if isinstance(c, str): return c
        if isinstance(c, list):
            parts = [b.get('text', '') for b in c if isinstance(b, dict) and b.get('type') == 'text']
            if parts: return '\n'.join(parts)
    return ''


class StubProvider:
    "Canned responses, streamed in chunks. Set `reply` to script an exact answer."

    name = 'stub'

    def __init__(self, reply=None, delay=0.02, fail=None):
        self.reply, self.delay, self.fail = reply, delay, fail

    def _text(self, messages):
        if self.reply is not None: return self.reply
        asked = _last_user_text(messages).strip()
        n = sum(len(str(m.get('content', ''))) for m in messages)
        return (f'**Stub reply.** No model was called.\n\n'
                f'I received {len(messages)} message(s), {n} characters of context.\n\n'
                f'The last thing you said was:\n\n> {asked or "(nothing)"}\n')

    async def stream(self, messages, tools=None, **kw):
        if self.fail:
            yield Failed(self.fail)
            return
        text = self._text(messages)
        for i in range(0, len(text), CHUNK):
            if self.delay: await asyncio.sleep(self.delay)
            yield TextDelta(text[i:i + CHUNK])
        yield Done(usage={'input_chars': sum(len(str(m.get('content', ''))) for m in messages),
                          'output_chars': len(text)},
                   stop_reason='end_turn')
