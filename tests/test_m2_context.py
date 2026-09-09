"""M2: the assembler. Pure functions, so this is where the detail lives.

What the model sees is decided entirely here, and every cell above a prompt is editable,
so these tests are the specification of "what will be sent".
"""
import pytest
from aidialog.dialog import Dialog, code_output, prompt_output, scode, snote, sprompt

from dialogbook.context import (ELISION, Part, approx_tokens, assemble, cap, evict,
                                parts_for, render_code, to_messages)


def dialog(*specs):
    """Build a dialog from `(type, content, output, **flags)` tuples.

    `output` is the reply text for a prompt, or the text/plain result for a code cell.
    """
    d = Dialog(name='t')
    for spec in specs:
        typ, content, out = spec[0], spec[1], spec[2]
        flags = spec[3] if len(spec) > 3 else {}
        if typ == sprompt: out = prompt_output(out) if out else ''
        elif typ == scode: out = code_output(out) if out else ''
        d.mk_message(content, msg_type=typ, output=out, **flags)
    return d


def target_of(d): return d.messages[-1]


# ---------------------------------------------------------------- capping

def test_short_output_is_untouched():
    assert cap('hello', 100) == 'hello'
    assert cap('', 10) == '' and cap(None, 10) == ''


def test_long_output_keeps_both_ends():
    text = 'HEAD' + 'x'*5000 + 'TAIL'
    out = cap(text, 100)
    assert out.startswith('HEAD') and out.endswith('TAIL')
    assert len(out) == 100 + len(ELISION.format(n=len(text) - 100))
    assert 'elided' in out


def test_capping_is_what_stops_one_cell_eating_the_budget():
    "A runaway loop must not be able to evict the rest of the dialog."
    d = dialog((scode, 'runaway()', 'y'*200_000),
               (sprompt, 'what happened?', ''))
    ctx = assemble(d, target_of(d), budget=100_000)
    assert ctx.evicted == [], 'nothing needed evicting because the output was capped'
    assert ctx.tokens < 2_000


# ---------------------------------------------------------------- rendering

def test_code_renders_as_a_fenced_block_with_its_output():
    d = dialog((scode, 'x = 41\nx + 1', '42'))
    out = render_code(d.messages[0])
    assert out == '```python\nx = 41\nx + 1\n```\n\nOutput:\n```\n42\n```'


def test_code_without_output_has_no_output_section():
    d = dialog((scode, 'x = 1', ''))
    assert render_code(d.messages[0]) == '```python\nx = 1\n```'


def test_answered_prompt_becomes_two_turns():
    "Not one flattened blob: tool calls need genuine assistant turns to round-trip."
    d = dialog((sprompt, 'what is 6*7?', 'It is 42.'))
    parts = parts_for(d.messages[0])
    assert [(p.role, p.text, p.kind) for p in parts] == [
        ('user', 'what is 6*7?', 'prompt'), ('assistant', 'It is 42.', 'reply')]
    assert len({p.msg_id for p in parts}) == 1, 'both parts belong to the same cell'


def test_unanswered_prompt_contributes_only_the_request():
    d = dialog((sprompt, 'pending?', ''))
    assert [p.role for p in parts_for(d.messages[0])] == ['user']


def test_empty_cells_contribute_nothing():
    d = dialog((snote, '   ', ''), (scode, '', ''), (sprompt, '', ''))
    assert all(parts_for(m) == [] for m in d.messages)


# ---------------------------------------------------------------- selection

def test_only_cells_above_the_target_are_included():
    d = dialog((snote, 'before', ''), (sprompt, 'ask', ''), (snote, 'after', ''))
    ctx = assemble(d, d.messages[1])
    assert 'before' in ctx.messages[0]['content']
    assert 'after' not in str(ctx.messages)


def test_hidden_cells_are_dropped_but_reported():
    d = dialog((snote, 'visible', ''), (snote, 'secret', '', {'skipped': 1}),
               (sprompt, 'ask', ''))
    ctx = assemble(d, target_of(d))
    assert 'secret' not in str(ctx.messages)
    assert 'visible' in str(ctx.messages)
    assert ctx.hidden == [d.messages[1].id]


def test_the_targets_own_previous_reply_is_not_resent():
    "Re-running a prompt must not feed the model its own last answer as context."
    d = dialog((snote, 'ctx', ''), (sprompt, 'ask again', 'stale answer'))
    ctx = assemble(d, target_of(d))
    assert 'stale answer' not in str(ctx.messages)
    assert ctx.messages[-1]['content'].endswith('ask again')


def test_a_note_only_dialog_still_produces_one_user_turn():
    d = dialog((snote, 'just a note', ''), (sprompt, 'go', ''))
    ctx = assemble(d, target_of(d))
    assert [m['role'] for m in ctx.messages] == ['user']
    assert ctx.messages[0]['content'] == 'just a note\n\ngo'


# ---------------------------------------------------------------- role mapping

def test_consecutive_user_items_merge_into_one_turn():
    d = dialog((snote, 'note one', ''), (scode, 'code()', ''), (snote, 'note two', ''),
               (sprompt, 'the question', ''))
    ctx = assemble(d, target_of(d))
    assert [m['role'] for m in ctx.messages] == ['user']
    body = ctx.messages[0]['content']
    assert body.index('note one') < body.index('code()') < body.index('note two') < body.index('the question')


def test_prior_prompts_produce_alternating_turns():
    d = dialog((snote, 'setup', ''),
               (sprompt, 'first question', 'first answer'),
               (scode, 'more()', ''),
               (sprompt, 'second question', ''))
    ctx = assemble(d, target_of(d))
    assert [m['role'] for m in ctx.messages] == ['user', 'assistant', 'user']
    assert ctx.messages[0]['content'] == 'setup\n\nfirst question'
    assert ctx.messages[1]['content'] == 'first answer'
    assert ctx.messages[2]['content'] == '```python\nmore()\n```\n\nsecond question'


def test_the_first_turn_is_always_user():
    "Anthropic rejects a leading assistant turn; a prompt's request always precedes its reply."
    d = dialog((sprompt, 'q', 'a'), (sprompt, 'q2', ''))
    ctx = assemble(d, target_of(d))
    assert ctx.messages[0]['role'] == 'user'


def test_to_messages_merges_only_adjacent_same_roles():
    parts = [Part('1', 'user', 'a', 'note'), Part('2', 'user', 'b', 'note'),
             Part('3', 'assistant', 'c', 'reply'), Part('4', 'user', 'd', 'note')]
    assert to_messages(parts) == [{'role': 'user', 'content': 'a\n\nb'},
                                  {'role': 'assistant', 'content': 'c'},
                                  {'role': 'user', 'content': 'd'}]


# ---------------------------------------------------------------- eviction

def big(msg_id, n, pinned=False, kind='note'):
    return Part(msg_id, 'user', 'x'*n, kind, pinned)


def test_nothing_evicts_when_it_fits():
    parts = [big('a', 400), big('b', 400)]
    kept, evicted = evict(parts, budget=1000)
    assert kept == parts and evicted == []


def test_oldest_non_pinned_evicts_first():
    parts = [big('a', 4000), big('b', 4000), big('c', 4000)]
    kept, evicted = evict(parts, budget=2000)   # 1000 tokens each, room for two
    assert evicted == ['a']
    assert [p.msg_id for p in kept] == ['b', 'c']


def test_pinned_never_evicts_even_when_it_is_oldest():
    parts = [big('a', 4000, pinned=True), big('b', 4000), big('c', 4000)]
    kept, evicted = evict(parts, budget=2000)
    assert evicted == ['b']
    assert [p.msg_id for p in kept] == ['a', 'c']


def test_the_target_never_evicts():
    parts = [big('a', 4000), big('t', 40_000, kind='target')]
    kept, evicted = evict(parts, budget=1000)
    assert evicted == ['a']
    assert [p.msg_id for p in kept] == ['t'], 'the target survives even alone over budget'


def test_an_answered_prompt_evicts_as_a_whole():
    "Half a turn — a request with no reply, or a reply with no request — is worse than none."
    parts = [Part('p', 'user', 'x'*4000, 'prompt'), Part('p', 'assistant', 'y'*4000, 'reply'),
             big('later', 4000)]
    kept, evicted = evict(parts, budget=1500)
    assert evicted == ['p']
    assert [p.msg_id for p in kept] == ['later']


def test_over_budget_on_pinned_alone_still_sends_with_a_warning():
    "Surfacing the problem beats silently mangling the context."
    d = dialog((snote, 'padding '*5000, '', {'pinned': 1}), (sprompt, 'go', ''))
    ctx = assemble(d, target_of(d), budget=100)
    assert ctx.evicted == [], 'nothing was evictable'
    assert ctx.over_budget
    assert 'Sending anyway' in ctx.warning and 'unpin' in ctx.warning
    assert 'padding' in str(ctx.messages), 'pinned content is still sent'


def test_assemble_reports_eviction_end_to_end():
    d = dialog((snote, 'old '*3000, ''), (snote, 'keep me', '', {'pinned': 1}),
               (snote, 'recent', ''), (sprompt, 'ask', ''))
    ctx = assemble(d, target_of(d), budget=200)
    assert ctx.evicted == [d.messages[0].id]
    assert 'keep me' in str(ctx.messages) and 'recent' in str(ctx.messages)
    assert not ctx.over_budget and ctx.warning == ''


# ---------------------------------------------------------------- accounting

def test_token_estimate_is_chars_over_four():
    assert approx_tokens('') == 0
    assert approx_tokens('x'*400) == 100


def test_the_reported_total_is_what_is_actually_sent():
    """The header total must equal the per-turn totals the preview shows.

    Counting parts instead of merged messages misses the separators merging inserts, so
    the panel's own numbers would not add up.
    """
    d = dialog((snote, 'a'*400, ''), (scode, 'b = 1', ''), (sprompt, 'q', ''))
    ctx = assemble(d, target_of(d))
    assert ctx.tokens == sum(approx_tokens(m['content']) for m in ctx.messages)
    assert ctx.budget == 100_000 and not ctx.over_budget


def test_running_a_non_prompt_cell_is_a_programming_error():
    d = dialog((scode, 'x = 1', ''))
    with pytest.raises(AssertionError):
        assemble(d, d.messages[0])
