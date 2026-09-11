"""Model providers, behind one protocol.

`get_provider` is the only place that knows which concrete provider exists; nothing above
this package changes when another is added.
"""
import logging

from .base import (Done, Event, Failed, Provider, TextDelta, Thinking, ToolResult, ToolUse)
from .stub import StubProvider

log = logging.getLogger('dialogbook.providers')

__all__ = ['Done', 'Event', 'Failed', 'Provider', 'StubProvider', 'TextDelta', 'Thinking',
           'ToolResult', 'ToolUse', 'get_provider', 'provider_for']


def get_provider(name='stub', **kw):
    if name == 'stub': return StubProvider(**kw)
    if name == 'anthropic':
        from .anthropic import AnthropicProvider
        return AnthropicProvider(**kw)
    raise ValueError(f'unknown provider {name!r} (known: stub, anthropic)')


def provider_for(cfg):
    """The provider `cfg` asks for, degrading to the stub when it cannot be used.

    Running with no API key should give you a working notebook that says so, not a stack
    trace on every prompt — the kernel and the dialog are useful on their own.
    """
    if cfg.provider == 'stub': return StubProvider()
    if cfg.provider == 'anthropic':
        from .anthropic import has_api_key
        if not has_api_key():
            log.warning('no Anthropic credential found; falling back to the stub provider. '
                        'Set ANTHROPIC_API_KEY in .env to use %s.', cfg.model)
            return StubProvider()
        return get_provider('anthropic', model=cfg.model, max_tokens=cfg.max_tokens,
                            effort=cfg.effort, fallbacks=cfg.fallbacks)
    return get_provider(cfg.provider)
