"""Context assembly: turning the dialog above a prompt into messages for the model.

This is the feature. There is no chat sidebar — the notebook *is* the context window — so
what this module decides is exactly what the model knows.

Everything here is a pure function of a `Dialog` and a target message. Nothing is cached:
context is recomputed per run, because every cell above the prompt is editable and a stale
context would be a lie about what was sent.

The shape of a run:

1. Walk the messages *above* the target, in document order.
2. Drop anything hidden (`skipped`).
3. Render each into `Part`s — notes as markdown, code as a fenced block plus its outputs,
   prior prompts as their request *and* their reply.
4. Cap each individual output. One runaway loop printing 50k lines must not be able to
   evict the whole dialog.
5. Estimate tokens, and while over budget evict the oldest non-pinned message. Pinned
   never evicts; the target never evicts. If that still does not fit, send anyway and say
   so rather than silently mangling it.
"""
from dataclasses import dataclass, field

from aidialog.dialog import render_outputs_ai, scode, snote, sprompt

# Per-output cap, in characters. Generous enough for a real traceback or a table, small
# enough that one runaway cell cannot dominate the budget.
MAX_OUTPUT_CHARS = 4000
ELISION = '\n\n… [{n:,} characters elided] …\n\n'


def approx_tokens(text):
    "chars/4. Zero latency, no API call; the budget is set conservatively to absorb the error."
    return len(text)//4


def cap(text, limit=MAX_OUTPUT_CHARS):
    "Head and tail of `text` with an elision marker between, if it exceeds `limit`."
    if text is None: return ''
    if len(text) <= limit: return text
    marker = ELISION.format(n=len(text) - limit)
    # Half from each end: a traceback puts the exception last, a print loop matters at both.
    head = limit//2
    return text[:head] + marker + text[-(limit - head):]


@dataclass(frozen=True)
class Part:
    """One rendered piece of context, still tied to the cell it came from.

    Parts, not raw strings, because eviction and the preview both need to say *which cell*
    something came from — and a prompt contributes two parts (request and reply) that must
    be evicted together.
    """
    msg_id: str
    role: str          # 'user' or 'assistant'
    text: str
    kind: str          # note | code | prompt | reply | raw | target
    pinned: bool = False

    @property
    def tokens(self): return approx_tokens(self.text)


@dataclass(frozen=True)
class Assembled:
    "What will be sent, and the accounting behind it. The preview panel renders this."
    messages: list = field(default_factory=list)   # [{'role','content'}], ready for a provider
    parts: list = field(default_factory=list)      # what survived, in document order
    evicted: list = field(default_factory=list)    # message ids dropped for budget
    hidden: list = field(default_factory=list)     # message ids dropped because hidden
    budget: int = 0
    warning: str = ''

    @property
    def tokens(self):
        """Counted from the merged messages, not the parts.

        The two differ by the separators merging inserts. Counting what is actually sent
        is both more accurate and the only way the preview's per-turn numbers add up to
        its header — a panel that exists to be trusted about numbers has to add up.
        """
        return sum(approx_tokens(m['content']) for m in self.messages)

    @property
    def over_budget(self): return self.tokens > self.budget


# ---------------------------------------------------------------- rendering

def render_code(msg, limit=MAX_OUTPUT_CHARS):
    "A code cell as a fenced block, plus its outputs as the model should see them."
    src = (msg.content or '').strip()
    out = cap(render_outputs_ai(msg.output) if msg.output else '', limit)
    block = f'```python\n{src}\n```' if src else ''
    if out.strip(): block += ('\n\n' if block else '') + f'Output:\n```\n{out.strip()}\n```'
    return block


def parts_for(msg, limit=MAX_OUTPUT_CHARS, kind=None):
    """The `Part`s one message contributes — zero, one, or (for an answered prompt) two.

    A prompt keeps genuine `user` and `assistant` turns rather than being flattened into
    one blob. Requirement 5 forces this: a tool call has to round-trip as an assistant
    `tool_use` followed by a user `tool_result`, which is only expressible if assistant
    turns stay assistant turns.
    """
    pinned = bool(msg.pinned)
    if msg.msg_type == sprompt:
        out = []
        if (req := (msg.content or '').strip()):
            out.append(Part(msg.id, 'user', req, kind or 'prompt', pinned))
        if (rep := (msg.ai_res or '').strip()):
            out.append(Part(msg.id, 'assistant', cap(rep, limit), 'reply', pinned))
        return out
    text = render_code(msg, limit) if msg.msg_type == scode else (msg.content or '').strip()
    if not text.strip(): return []
    return [Part(msg.id, 'user', text, kind or ('code' if msg.msg_type == scode else
                                                'note' if msg.msg_type == snote else 'raw'), pinned)]


# ---------------------------------------------------------------- budget

def evict(parts, budget):
    """Drop whole messages, oldest non-pinned first, until the total fits.

    Returns `(kept, evicted_ids)`. Eviction is by message id, so an answered prompt loses
    its request and its reply together — half a turn is worse than none.

    Whether the result actually fits is decided by the caller against the *merged*
    messages; this only decides what to drop.
    """
    total = sum(p.tokens for p in parts)
    if total <= budget: return list(parts), []

    order = []
    for p in parts:
        if p.kind == 'target' or p.pinned: continue
        if p.msg_id not in order: order.append(p.msg_id)

    kept, evicted = list(parts), []
    for mid in order:
        if total <= budget: break
        total -= sum(p.tokens for p in kept if p.msg_id == mid)
        kept = [p for p in kept if p.msg_id != mid]
        evicted.append(mid)
    return kept, evicted


def to_messages(parts):
    "Merge consecutive same-role parts into single turns, as the APIs expect."
    msgs = []
    for p in parts:
        if msgs and msgs[-1]['role'] == p.role: msgs[-1]['content'] += '\n\n' + p.text
        else: msgs.append({'role': p.role, 'content': p.text})
    return msgs


# ---------------------------------------------------------------- entry point

def assemble(dlg, target, budget=100_000, limit=MAX_OUTPUT_CHARS):
    "Everything above `target` that fits, plus `target` itself, as provider-ready messages."
    assert target.msg_type == sprompt, f'can only run prompt cells, got {target.msg_type}'

    parts, hidden = [], []
    for m in dlg.messages:
        if m.id == target.id: break          # only what is *above* the prompt
        if m.skipped:
            hidden.append(m.id)
            continue
        parts += parts_for(m, limit)
    # The target's own request, never evictable and never carrying its previous reply.
    parts += [Part(target.id, 'user', (target.content or '').strip(), 'target', bool(target.pinned))]

    kept, evicted = evict(parts, budget)
    ctx = Assembled(messages=to_messages(kept), parts=kept, evicted=evicted, hidden=hidden,
                    budget=budget)
    if not ctx.over_budget: return ctx
    # Nothing evictable is left — only pinned cells and the prompt. Say so rather than
    # silently mangling the context.
    warning = (f'{ctx.tokens:,} tokens against a {budget:,} budget, with only pinned cells and '
               f'the prompt left. Sending anyway — unpin something or raise the budget.')
    return Assembled(messages=ctx.messages, parts=kept, evicted=evicted, hidden=hidden,
                     budget=budget, warning=warning)
