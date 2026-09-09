"""The seam between the app and any model API.

Everything upstream of this file — routes, SSE, kernel plumbing, the UI — is written
against `Provider` alone. That is what lets M1 be built and exercised against canned
responses, and what keeps a broken or churning vendor wrapper from reaching `app.py`.

A provider yields *events*, not strings, because a prompt run is not only text: it also
carries tool calls (M4) and a terminal result. Text-only providers simply never yield the
tool events.
"""
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable


@dataclass(frozen=True)
class TextDelta:
    "A chunk of assistant text. Chunks concatenate; no chunk is a complete unit of anything."
    text: str


@dataclass(frozen=True)
class ToolUse:
    "The model asked to call a tool. The caller runs it and feeds back a `ToolResult`."
    id: str
    name: str
    input: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    "The outcome of a `ToolUse`, as it will be sent back to the model."
    id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class Done:
    "The run finished. `usage` is provider-shaped and only ever displayed, never parsed."
    usage: dict = field(default_factory=dict)
    stop_reason: str | None = None


@dataclass(frozen=True)
class Failed:
    "The run failed. Providers yield this instead of raising, so a stream always ends cleanly."
    message: str
    detail: str = ''


Event = TextDelta | ToolUse | ToolResult | Done | Failed


@runtime_checkable
class Provider(Protocol):
    "What `app.py` is allowed to know about a model."

    name: str

    def stream(self, messages: list[dict], tools: list[dict] | None = None,
               **kw: Any) -> AsyncIterator[Event]:
        """Run `messages` and yield `Event`s until exactly one `Done` or `Failed`.

        `messages` is in Anthropic's shape: a list of `{'role': ..., 'content': ...}`,
        where content is a string or a list of typed blocks.
        """
        ...
