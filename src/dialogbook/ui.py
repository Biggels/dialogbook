"""Rendering. Everything the browser sees is built here; `app.py` only routes.

Cells render in *view* mode by default and swap to an editor on demand, so a long dialog
is mostly static HTML. M1 uses plain textareas; Monaco replaces them later, instantiated
on focus and disposed on blur (an editor per cell would crawl).
"""
from fastcore.ansi import strip_ansi
from fasthtml.common import (A, Button, Div, Form, H1, H3, Input, Label, Li, Main, NotStr,
                             P, Pre, Script, Span, Textarea, Titled, Ul)
from monsterui.all import render_md

from aidialog.dialog import scode, snote, sprompt

TYPE_LABEL = {snote: 'note', scode: 'code', sprompt: 'prompt'}

# Cells are addressed by nbformat cell id, so HTMX can replace one without touching the rest.
def cid(msg): return f'cell-{msg.id}'


def _esc(s): return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# ---------------------------------------------------------------- outputs

def render_output(out):
    "One nbformat output as FT. Unknown mime types fall back to text/plain rather than vanishing."
    t = out.get('output_type')
    if t == 'stream':
        cls = 'out-stream out-stderr' if out.get('name') == 'stderr' else 'out-stream'
        return Pre(out.get('text', ''), cls=cls)
    if t == 'error':
        tb = strip_ansi('\n'.join(out.get('traceback') or [])) or \
             f"{out.get('ename', 'Error')}: {out.get('evalue', '')}"
        return Pre(tb, cls='out-error')
    data = out.get('data') or {}
    if (md := data.get('text/markdown')) is not None:
        return Div(render_md(_join(md)), cls='out-md')
    if (html := data.get('text/html')) is not None:
        return Div(NotStr(_join(html)), cls='out-html')
    for mime in ('image/png', 'image/jpeg'):
        if (img := data.get(mime)) is not None:
            return Div(NotStr(f'<img src="data:{mime};base64,{_join(img).strip()}" alt="output">'),
                       cls='out-img')
    if (svg := data.get('image/svg+xml')) is not None:
        return Div(NotStr(_join(svg)), cls='out-img')
    if (txt := data.get('text/plain')) is not None:
        return Pre(_join(txt), cls='out-stream')
    return None


def _join(v): return ''.join(v) if isinstance(v, (list, tuple)) else (v or '')


def outputs_view(msg):
    "The output area of a code or prompt cell. Always present, so SSE has a target to fill."
    outs = msg.output if isinstance(msg.output, (list, tuple)) else []
    rendered = [o for o in (render_output(o) for o in outs) if o is not None]
    return Div(*rendered, id=f'out-{msg.id}', cls='outputs')


# ---------------------------------------------------------------- cells

def _toolbar(msg, did):
    def act(path, label, title, **kw):
        return Button(label, title=title, cls='tb', hx_post=f'/d/{did}/cells/{msg.id}/{path}',
                      hx_target=f'#{cid(msg)}', hx_swap='outerHTML', **kw)
    # Run/Ask POST returns a *new* output div wired for SSE; see `streaming_outputs`.
    run = []
    if msg.msg_type in (scode, sprompt):
        label, title = (('▶ Run', 'Run this cell in the kernel') if msg.msg_type == scode
                        else ('▶ Ask', 'Send this prompt to the model'))
        run = [Button(label, cls='tb tb-run', title=title,
                      hx_post=f'/d/{did}/cells/{msg.id}/run', hx_target=f'#out-{msg.id}',
                      hx_swap='outerHTML')]
    return Div(
        Span(TYPE_LABEL.get(msg.msg_type, msg.msg_type), cls=f'badge badge-{msg.msg_type}'),
        *run,
        Button('Edit', cls='tb', title='Edit this cell',
               hx_get=f'/d/{did}/cells/{msg.id}/edit', hx_target=f'#{cid(msg)}', hx_swap='outerHTML'),
        act('pin', '📌' if msg.pinned else 'Pin', 'Always keep this cell in context'),
        act('hide', '🙈' if msg.skipped else 'Hide', 'Exclude this cell from the model context'),
        act('move?dir=up', '↑', 'Move up'),
        act('move?dir=down', '↓', 'Move down'),
        Button('✕', cls='tb tb-del', title='Delete this cell',
               hx_delete=f'/d/{did}/cells/{msg.id}', hx_target=f'#{cid(msg)}', hx_swap='outerHTML',
               hx_confirm='Delete this cell?'),
        cls='toolbar')


def _source_view(msg, did):
    "The editable half of a cell, rendered read-only. Click to edit."
    edit = dict(hx_get=f'/d/{did}/cells/{msg.id}/edit', hx_target=f'#{cid(msg)}',
                hx_swap='outerHTML', cls='src', title='Click to edit')
    if msg.msg_type == snote:
        return Div(render_md(msg.content or '*empty note*'), **edit)
    if msg.msg_type == sprompt:
        return Div(render_md(msg.content or '*empty prompt*'), **{**edit, 'cls': 'src src-prompt'})
    return Pre(NotStr(_esc(msg.content)), **{**edit, 'cls': 'src src-code'})


def cell(msg, did):
    "One message, in view mode."
    flags = ' '.join(f'is-{f}' for f, on in (('pinned', msg.pinned), ('hidden', msg.skipped)) if on)
    return Div(_toolbar(msg, did), _source_view(msg, did), outputs_view(msg),
               add_bar(did, after=msg.id),
               id=cid(msg), cls=f'cell cell-{msg.msg_type} {flags}'.strip())


def cell_editor(msg, did):
    "One message, in edit mode. Ctrl/Cmd+Enter saves."
    return Div(
        _toolbar(msg, did),
        Form(
            Textarea(msg.content or '', name='content', rows=max(3, (msg.content or '').count('\n') + 2),
                     cls='editor', autofocus=True),
            Div(Button('Save', type='submit', cls='tb tb-primary'),
                Button('Cancel', type='button', cls='tb', hx_get=f'/d/{did}/cells/{msg.id}',
                       hx_target=f'#{cid(msg)}', hx_swap='outerHTML'),
                cls='editor-actions'),
            hx_patch=f'/d/{did}/cells/{msg.id}', hx_target=f'#{cid(msg)}', hx_swap='outerHTML'),
        outputs_view(msg),
        add_bar(did, after=msg.id),
        id=cid(msg), cls=f'cell cell-{msg.msg_type} editing')


def add_bar(did, after=''):
    "The thin 'add a cell here' strip that sits under every cell."
    tgt, swap = (f'#cell-{after}', 'afterend') if after else ('#cells', 'beforeend')
    def add(t, label):
        return Button(label, cls='add', hx_post=f'/d/{did}/cells?type={t}&after={after}',
                      hx_target=tgt, hx_swap=swap)
    return Div(add(snote, '+ note'), add(scode, '+ code'), add(sprompt, '+ prompt'), cls='addbar')


def streaming_outputs(msg, did):
    """An output div that opens an SSE connection and appends what arrives.

    The stream sends `chunk` events (appended verbatim) and finally a `done` event, which
    `sse-close` uses to close the connection. The last chunk is a self-loading element
    that re-fetches the whole cell, so the finished DOM shows properly rendered output —
    markdown, images, tracebacks — rather than the raw text that streamed in.
    """
    return Div(Span(cls='spinner'),
               id=f'out-{msg.id}', cls='outputs streaming',
               hx_ext='sse', sse_connect=f'/d/{did}/cells/{msg.id}/stream',
               sse_swap='chunk', hx_swap='beforeend', sse_close='done')


def stream_end(msg, did):
    "The final chunk: re-render the cell now that the run is finished and saved."
    return Div(hx_get=f'/d/{did}/cells/{msg.id}', hx_target=f'#{cid(msg)}',
               hx_swap='outerHTML', hx_trigger='load')


# ---------------------------------------------------------------- pages

def dialog_page(dlg, did, provider_name):
    cells = [cell(m, did) for m in dlg.messages]
    return Titled(
        dlg.name,
        Div(A('← all dialogs', href='/', cls='back'),
            Span(f'provider: {provider_name}', cls='pill'),
            Button('Restart kernel', cls='tb', hx_post=f'/d/{did}/restart', hx_target='#kernel-status',
                   hx_swap='innerHTML', hx_confirm='Restart the kernel? All variables are lost.'),
            Span('', id='kernel-status', cls='pill'),
            cls='dialog-bar'),
        Main(Div(*cells, id='cells'),
             add_bar(did) if not cells else '',
             cls='dialog'))


def index_page(infos, workspace):
    rows = [Li(A(i.title, href=f'/d/{i.id}', cls='dlg-link'),
               Span(i.modified.strftime('%Y-%m-%d %H:%M'), cls='muted'),
               cls='dlg-row') for i in infos]
    return Titled(
        'dialogbook',
        Main(
            Form(Input(name='title', placeholder='New dialog title…', cls='title-input',
                       autofocus=True, required=True),
                 Button('Create', type='submit', cls='tb tb-primary'),
                 method='post', action='/dialogs', cls='new-form'),
            H3('Dialogs') if rows else '',
            Ul(*rows, cls='dlg-list') if rows else P('No dialogs yet.', cls='muted'),
            P(f'Workspace: {workspace}', cls='muted small'),
            cls='index'))
