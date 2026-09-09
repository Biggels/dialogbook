"""M0(a): a dialog round-trips through `.ipynb` with prompt output, pinned and hidden state intact.

Verifies the on-disk form the handoff describes: prompts are code cells whose source
starts with `%%prompt` and carry `solveit_ai: true`; notes are markdown cells; pin/hide
are `pinned`/`skipped` cell metadata. No server involved.
"""
import json

import nbformat
import pytest
from aidialog.dialog import Dialog, code_output, prompt_output, render_outputs_ai, scode, snote, sprompt
from aidialog.ipynb import read_ipynb, write_ipynb


@pytest.fixture
def dlg():
    d = Dialog(name='m0')
    d.mk_message('# A note\n\nSome *markdown*.', msg_type=snote)
    d.mk_message('x = 41\nx + 1', msg_type=scode, output=code_output('42'))
    d.mk_message('What is x?', msg_type=sprompt, output=prompt_output('It is 41.'))
    d.mk_message('PINNED context', msg_type=snote, pinned=1)
    d.mk_message('HIDDEN from ai', msg_type=scode, output=code_output('shh'), skipped=1)
    return d


@pytest.fixture
def written(dlg, tmp_path):
    f = tmp_path/'m0.ipynb'
    write_ipynb(dlg, f)
    return f


def test_file_is_valid_nbformat4(written):
    "JupyterLab and VS Code stay a working fallback, so the file must validate."
    nb = nbformat.reads(written.read_text(encoding='utf-8'), as_version=4)
    nbformat.validate(nb)
    assert nb.nbformat == 4


def test_on_disk_cell_shape(written):
    "The literal encoding of each cell type, as the handoff specifies it."
    cells = json.loads(written.read_text(encoding='utf-8'))['cells']
    note, code, prompt, pinned, hidden = cells
    assert note['cell_type'] == 'markdown'
    assert code['cell_type'] == 'code' and 'solveit_ai' not in code['metadata']
    assert prompt['cell_type'] == 'code'
    assert prompt['metadata']['solveit_ai'] is True
    assert ''.join(prompt['source']).startswith('%%prompt\n')
    assert pinned['metadata']['pinned'] == 1
    assert hidden['metadata']['skipped'] == 1


def test_round_trip_preserves_everything(dlg, written):
    d2 = read_ipynb(written)
    assert [m.msg_type for m in d2.messages] == [snote, scode, sprompt, snote, scode]
    assert [m.id for m in d2.messages] == [m.id for m in dlg.messages], 'cell ids are the HTMX identity'
    assert d2.messages[0].content == '# A note\n\nSome *markdown*.'
    assert d2.messages[1].content == 'x = 41\nx + 1'
    assert d2.messages[2].content == 'What is x?', 'the %%prompt magic is stripped on read'
    assert d2.messages[2].ai_res == 'It is 41.', 'prompt output survives'
    assert render_outputs_ai(d2.messages[1].output) == '42', 'code output survives'
    assert (d2.messages[3].pinned, d2.messages[3].skipped) == (1, 0)
    assert (d2.messages[4].pinned, d2.messages[4].skipped) == (0, 1)
    assert (d2.messages[0].pinned, d2.messages[0].skipped) == (0, 0)
