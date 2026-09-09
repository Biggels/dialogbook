"""Model providers, behind one protocol.

`get_provider` is the only place that knows which concrete provider exists. M3 adds the
Anthropic one here; nothing above this package changes when it does.
"""
from .base import Done, Event, Failed, Provider, TextDelta, ToolResult, ToolUse
from .stub import StubProvider

__all__ = ['Done', 'Event', 'Failed', 'Provider', 'StubProvider', 'TextDelta', 'ToolResult',
           'ToolUse', 'get_provider']


def get_provider(name='stub', **kw):
    if name == 'stub': return StubProvider(**kw)
    raise ValueError(f'unknown provider {name!r} (M1 ships the stub only)')
