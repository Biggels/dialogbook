"""The real Anthropic provider, on the official SDK.

The handoff named `claudette`, and this deviates. `claudette` wraps this same SDK; what it
adds is a tool loop that runs functions in the *app's* Python namespace — and requirement 5
puts tool execution in the dialog's Jupyter kernel instead, so that loop is not merely
unused but the wrong shape. What is left is a thin layer over `anthropic`, and this stack
has already shown what a thin layer costs when it drifts (`lisette` 0.1.36). So: the SDK.

Streaming is not optional here. Answers land in a notebook cell and a reasoning model can
think for a long time before its first visible token, so both the reasoning and the answer
have to arrive as they are produced.
"""
import logging

import anthropic

from .base import Done, Failed, TextDelta, Thinking, ToolUse

log = logging.getLogger('dialogbook.providers.anthropic')

# Server-side refusal fallbacks: on a policy decline the API re-runs the same request on a
# fallback model within the same call, rather than handing back a dead turn.
FALLBACK_BETA = 'server-side-fallback-2026-07-01'


def has_api_key():
    "Whether the SDK can find a credential. The key itself is never read or logged."
    try:
        anthropic.AsyncAnthropic()
        return True
    except Exception:
        return False


class AnthropicProvider:
    "Streams one Anthropic request per prompt run, yielding `Event`s."

    name = 'anthropic'

    def __init__(self, model='claude-opus-5', max_tokens=64_000, effort='high',
                 fallbacks=True, client=None, system=None):
        self.model, self.max_tokens, self.effort = model, max_tokens, effort
        self.fallbacks = fallbacks
        self.system = system
        self._client = client          # injected in tests; never constructed there

    @property
    def client(self):
        if self._client is None: self._client = anthropic.AsyncAnthropic()
        return self._client

    def _kwargs(self, messages, tools):
        kw = dict(model=self.model, max_tokens=self.max_tokens, messages=messages,
                  # Adaptive is the only on-mode on current models, and is the default on
                  # Opus 5. `summarized` is an explicit opt-in: the default omits the text,
                  # which in a streaming UI reads as a long unexplained pause.
                  thinking={'type': 'adaptive', 'display': 'summarized'},
                  output_config={'effort': self.effort})
        if self.system: kw['system'] = self.system
        if tools: kw['tools'] = tools
        return kw

    def _stream_ctx(self, messages, tools, fallbacks):
        "The streaming context manager, on the beta endpoint only when fallbacks are on."
        kw = self._kwargs(messages, tools)
        if not fallbacks: return self.client.messages.stream(**kw)
        return self.client.beta.messages.stream(betas=[FALLBACK_BETA], fallbacks='default', **kw)

    async def stream(self, messages, tools=None, **_):
        """Yield events until exactly one `Done` or `Failed`.

        Never raises: the caller is an SSE generator, and a half-open stream in the browser
        is worse than an error rendered into the cell.
        """
        use_fallbacks = self.fallbacks
        while True:
            try:
                async for ev in self._run(messages, tools, use_fallbacks):
                    yield ev
                return
            except anthropic.BadRequestError as e:
                # The account may not have the fallback beta. Drop it once and retry, so a
                # missing entitlement degrades rather than breaking every prompt.
                if use_fallbacks and _is_fallback_rejection(e):
                    log.warning('server-side fallbacks unavailable, continuing without: %s',
                                _msg(e))
                    self.fallbacks = use_fallbacks = False
                    continue
                yield Failed('The request was rejected.', _msg(e))
                return
            except anthropic.AuthenticationError:
                yield Failed('Anthropic rejected the API key.',
                             'Check ANTHROPIC_API_KEY in your .env.')
                return
            except anthropic.RateLimitError as e:
                yield Failed('Rate limited by Anthropic.', _msg(e))
                return
            except anthropic.APIConnectionError as e:
                yield Failed('Could not reach Anthropic.', _msg(e))
                return
            except anthropic.APIStatusError as e:
                yield Failed(f'Anthropic returned {e.status_code}.', _msg(e))
                return
            except Exception as e:                     # noqa: BLE001 - see docstring
                log.exception('anthropic stream failed')
                yield Failed(f'{type(e).__name__} while streaming.', str(e))
                return

    async def _run(self, messages, tools, fallbacks):
        async with self._stream_ctx(messages, tools, fallbacks) as stream:
            async for event in stream:
                if event.type != 'content_block_delta': continue
                d = event.delta
                if d.type == 'text_delta': yield TextDelta(d.text)
                elif d.type == 'thinking_delta': yield Thinking(d.thinking)
            final = await stream.get_final_message()

        # A refusal is an HTTP 200 with no usable content, so it has to be checked before
        # the content blocks are read.
        if final.stop_reason == 'refusal':
            det = getattr(final, 'stop_details', None)
            yield Failed('Claude declined this request.',
                         getattr(det, 'explanation', '') or getattr(det, 'category', '') or '')
            return

        for block in final.content:
            if block.type == 'tool_use':
                yield ToolUse(id=block.id, name=block.name, input=dict(block.input or {}))

        yield Done(usage=_usage(final), stop_reason=final.stop_reason)


def _usage(final):
    u = getattr(final, 'usage', None)
    if u is None: return {}
    out = {k: v for k in ('input_tokens', 'output_tokens', 'cache_read_input_tokens',
                          'cache_creation_input_tokens')
           if (v := getattr(u, k, None)) is not None}
    out['model'] = getattr(final, 'model', '')
    return out


def _msg(e):
    return getattr(e, 'message', None) or str(e)


def _is_fallback_rejection(e):
    "Does this 400 complain about the fallbacks parameter or its beta, rather than our input?"
    t = _msg(e).lower()
    return 'fallback' in t or ('beta' in t and 'server-side' in t)
