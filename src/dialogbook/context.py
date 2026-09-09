"""Context assembly: turning the dialog above a prompt into messages for the model.

**This is the M1 placeholder.** It sends only the target prompt, so the walking skeleton
has something to hand a provider. The real assembler — walking messages above the target,
dropping hidden ones, rendering code outputs, capping runaway output, estimating tokens
and evicting oldest-non-pinned — is M2, and is where the bulk of the unit tests will live.

Keeping it behind this function now means M2 changes one file, not `app.py`.
"""
from aidialog.dialog import sprompt


def approx_tokens(text):
    "chars/4. Zero latency, no API call. The budget is set conservatively to absorb the error."
    return len(text)//4


def assemble(dlg, target, budget=100_000):
    "Messages to send for `target`. M1: the target prompt alone."
    assert target.msg_type == sprompt, f'can only run prompt cells, got {target.msg_type}'
    return [{'role': 'user', 'content': target.content or ''}]
