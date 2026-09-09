"""Routes. All rendering lives in `ui.py`, all model access behind `providers`.

Two streaming endpoints, not one: Jupyter delivers iopub messages incrementally and model
output streams, so the same SSE mechanism serves both, but what they *do* with a chunk
differs enough to keep them apart.
"""
import asyncio
import logging

from fasthtml.common import (EventStream, Div, RedirectResponse, Script, Span, Style,
                             fast_app, serve, sse_message)
from monsterui.all import Theme

from aidialog.dialog import mk_error, mk_stream, prompt_output, scode, smsg_types, snote, sprompt

from . import ui
from .config import Config
from .context import assemble
from .kernels import STARTUP_GRACE, JupyterServer, KernelRegistry
from .providers import Done, Failed, TextDelta, get_provider
from .store import OpenDialogs, Store

log = logging.getLogger('dialogbook.app')

# htmx 2's SSE extension. fasthtml bundles only the htmx-4 build, so it is pinned here.
SSE_EXT = Script(src='https://cdn.jsdelivr.net/npm/htmx-ext-sse@2.2.3/sse.js')

STYLE = Style("""
:root { --line:#e3e3e6; --muted:#6b7280; --code-bg:#f7f7f9; --accent:#2563eb; }
@media (prefers-color-scheme: dark) {
  :root { --line:#2c2f36; --muted:#9aa1ad; --code-bg:#16181d; --accent:#7aa2f7; }
}
body { max-width: 60rem; margin: 0 auto; padding: 1rem; }
.muted { color: var(--muted); } .small { font-size: .85em; }
.dialog-bar, .new-form { display:flex; gap:.5rem; align-items:center; margin:.5rem 0 1rem; }
.title-input { flex:1; }
.dlg-list { list-style:none; padding:0; }
.dlg-row { display:flex; justify-content:space-between; gap:1rem; padding:.4rem 0;
           border-bottom:1px solid var(--line); }
.cell { border:1px solid var(--line); border-radius:6px; margin:.75rem 0; }
.cell.is-pinned { border-left:3px solid var(--accent); }
.cell.is-hidden { opacity:.55; }
.cell-prompt { background:color-mix(in srgb, var(--accent) 5%, transparent); }
.toolbar { display:flex; gap:.3rem; align-items:center; padding:.3rem .5rem;
           border-bottom:1px solid var(--line); flex-wrap:wrap; }
.badge { font-size:.7rem; text-transform:uppercase; letter-spacing:.05em; color:var(--muted);
         margin-right:auto; }
.tb { font-size:.78rem; padding:.15rem .5rem; line-height:1.6; }
.tb-primary { background:var(--accent); color:#fff; }
.src { padding:.5rem .75rem; cursor:text; }
.src-code { font-family:ui-monospace,Consolas,monospace; background:var(--code-bg); margin:0;
            white-space:pre-wrap; }
.editor { width:100%; font-family:ui-monospace,Consolas,monospace; font-size:.9rem; }
.editor-actions { display:flex; gap:.4rem; padding:0 0 .4rem .5rem; }
.outputs:empty { display:none; }
.outputs { border-top:1px solid var(--line); padding:.25rem .75rem; }
.out-stream, .out-error { font-family:ui-monospace,Consolas,monospace; font-size:.85rem;
                          white-space:pre-wrap; margin:.25rem 0; }
.out-stderr { color:#b45309; } .out-error { color:#dc2626; }
.out-img img { max-width:100%; }
.addbar { display:flex; gap:.3rem; padding:.2rem .5rem .4rem; opacity:0; transition:opacity .12s; }
.cell:hover .addbar, .addbar:focus-within { opacity:1; }
.add { font-size:.72rem; padding:.1rem .45rem; color:var(--muted); background:none;
       border:1px dashed var(--line); }
.pill { font-size:.75rem; color:var(--muted); }
.spinner::after { content:'▍'; animation:blink 1s steps(2) infinite; }
@keyframes blink { 50% { opacity:0; } }
.preview { border-top:1px solid var(--line); padding:.5rem .75rem; background:var(--code-bg); }
.prev-head { display:flex; gap:.5rem; align-items:center; }
.prev-title { font-weight:600; font-size:.85rem; margin-right:auto; }
.prev-warn { color:#b45309; font-size:.82rem; margin:.35rem 0;
             border-left:3px solid #b45309; padding-left:.5rem; }
.meter { height:3px; background:var(--line); border-radius:2px; margin:.35rem 0; }
.meter-fill { height:100%; background:var(--accent); border-radius:2px; }
.turn { margin:.5rem 0; }
.turn-head { display:flex; gap:.5rem; align-items:baseline; }
.role { font-size:.68rem; text-transform:uppercase; letter-spacing:.06em; padding:.05rem .35rem;
        border-radius:3px; background:var(--line); }
.role-assistant { background:color-mix(in srgb, var(--accent) 30%, transparent); }
.turn-body { font-family:ui-monospace,Consolas,monospace; font-size:.78rem; white-space:pre-wrap;
             margin:.2rem 0 0; max-height:22rem; overflow:auto; background:transparent;
             border:1px solid var(--line); border-radius:4px; padding:.4rem .5rem; }
""")


def create_app(cfg=None, provider=None):
    "Build the ASGI app. Tests call this directly with a temp workspace and a stub provider."
    cfg = cfg or Config.from_env()
    cfg.ensure_workspace()
    store = Store(cfg.workspace)
    dialogs = OpenDialogs(store, debounce=cfg.save_debounce)
    server = JupyterServer(cfg.workspace)
    kernels = KernelRegistry(server, kernel_name=cfg.kernel_name)
    provider = provider if provider is not None else get_provider('stub')

    # Background kernel warm-ups. They must be waited for, not cancelled: cancelling an
    # `asyncio.to_thread` does not stop the thread, so a subprocess spawned a moment later
    # would never be killed. Tracking them is also what stops the set being GC'd mid-flight.
    background = set()

    def _bg(coro):
        t = asyncio.create_task(coro)
        background.add(t)
        t.add_done_callback(background.discard)
        return t

    async def on_shutdown():
        if background:
            await asyncio.wait(background, timeout=STARTUP_GRACE)
        await dialogs.flush()
        await kernels.shutdown_all()

    # Every route states its method explicitly. FastHTML infers the method from the
    # handler's name, but only via `nested_name`, which renders a closure-defined `patch`
    # as `create_app_patch` — not in `all_meths` — so inference silently falls back to
    # GET+POST and PATCH/DELETE 405. See docs/notes-upstream.md.
    app, rt = fast_app(hdrs=(Theme.blue.headers(), SSE_EXT, STYLE), pico=False,
                       on_shutdown=[on_shutdown], title='dialogbook')

    # ------------------------------------------------------------ helpers

    def _dlg(did):
        dlg = dialogs.get(did)
        if dlg is None: raise LookupError(f'no dialog {did!r}')
        return dlg

    def _msg(did, cid):
        dlg = _dlg(did)
        m = next((m for m in dlg.messages if m.id == cid), None)
        if m is None: raise LookupError(f'no cell {cid!r} in {did!r}')
        return dlg, m

    # ------------------------------------------------------------ dialogs

    @rt('/', methods='get')
    def index(): return ui.index_page(store.list(), cfg.workspace)

    @rt('/dialogs', methods='post')
    async def post(title: str):
        dlg = store.create(title.strip() or 'Untitled')
        return RedirectResponse(f'/d/{dlg.name}', status_code=303)

    @rt('/d/{did}', methods='get')
    async def get(did: str):
        dlg = dialogs.get(did)
        if dlg is None: return RedirectResponse('/', status_code=303)
        # Opening a dialog starts its kernel. It lives until explicitly closed or restarted.
        _bg(_warm(did))
        return ui.dialog_page(dlg, did, provider.name)

    async def _warm(did):
        try: await kernels.client_for(did)
        except Exception as e: log.error('starting kernel for %s: %s', did, e)

    @rt('/d/{did}/restart', methods='post')
    async def post(did: str):
        await kernels.restart(did)
        return Span('kernel restarted', cls='pill')

    @rt('/d/{did}/close', methods='post')
    async def post(did: str):
        await dialogs.close(did)
        await kernels.close(did)
        return RedirectResponse('/', status_code=303)

    # ------------------------------------------------------------ cells

    @rt('/d/{did}/cells', methods='post')
    async def post(did: str, type: str = scode, after: str = ''):
        dlg = _dlg(did)
        if type not in smsg_types: type = scode
        m = dlg.mk_message('', msg_type=type, after=after or None)
        dialogs.touch(did)
        return ui.cell_editor(m, did)

    @rt('/d/{did}/cells/{cid}', methods='get')
    def get(did: str, cid: str):
        _, m = _msg(did, cid)
        return ui.cell(m, did)

    @rt('/d/{did}/cells/{cid}/edit', methods='get')
    def get(did: str, cid: str):
        _, m = _msg(did, cid)
        return ui.cell_editor(m, did)

    @rt('/d/{did}/cells/{cid}', methods='patch')
    async def patch(did: str, cid: str, content: str = ''):
        _, m = _msg(did, cid)
        # Browsers submit textarea content with CRLF line endings. Normalising here keeps
        # the notebook's sources plain `\n`, as every other notebook tool writes them.
        m.content = content.replace('\r\n', '\n').replace('\r', '\n')
        dialogs.touch(did)
        return ui.cell(m, did)

    @rt('/d/{did}/cells/{cid}', methods='delete')
    async def delete(did: str, cid: str):
        dlg, m = _msg(did, cid)
        dlg.remove_msgs([m])
        dialogs.touch(did)
        return ''

    @rt('/d/{did}/cells/{cid}/pin', methods='post')
    async def post(did: str, cid: str):
        _, m = _msg(did, cid)
        m.pinned = 0 if m.pinned else 1
        dialogs.touch(did)
        return ui.cell(m, did)

    @rt('/d/{did}/cells/{cid}/hide', methods='post')
    async def post(did: str, cid: str):
        _, m = _msg(did, cid)
        m.skipped = 0 if m.skipped else 1
        dialogs.touch(did)
        return ui.cell(m, did)

    @rt('/d/{did}/cells/{cid}/move', methods='post')
    async def post(did: str, cid: str, dir: str = 'up'):
        dlg, m = _msg(did, cid)
        # By id, not `list.index`: `Msgs` is a fastcore `L`, whose index semantics are its own.
        i = next(k for k, x in enumerate(dlg.messages) if x.id == cid)
        j = i - 1 if dir == 'up' else i + 1
        if 0 <= j < len(dlg.messages):
            dlg.messages[i], dlg.messages[j] = dlg.messages[j], dlg.messages[i]
            dialogs.touch(did)
            # Reordering changes more than one cell, so re-render the whole list.
            return Div(*[ui.cell(x, did) for x in dlg.messages], id='cells',
                       hx_swap_oob='outerHTML:#cells')
        return ui.cell(m, did)

    @rt('/d/{did}/cells/{cid}/preview', methods='get')
    async def get(did: str, cid: str):
        "Exactly what an Ask would send, assembled fresh — never a cached view."
        dlg, m = _msg(did, cid)
        return ui.context_preview(assemble(dlg, m, budget=cfg.token_budget), m, did)

    @rt('/d/{did}/cells/{cid}/preview/close', methods='get')
    async def get(did: str, cid: str):
        _, m = _msg(did, cid)
        return ui.preview_slot(m)

    # ------------------------------------------------------------ running

    @rt('/d/{did}/cells/{cid}/run', methods='post')
    async def post(did: str, cid: str):
        "Hand back an output div wired for SSE; the work happens on the stream connection."
        _, m = _msg(did, cid)
        m.output = []
        return ui.streaming_outputs(m, did)

    @rt('/d/{did}/cells/{cid}/stream', methods='get')
    async def get(did: str, cid: str):
        _, m = _msg(did, cid)
        gen = _run_code(did, m) if m.msg_type == scode else _run_prompt(did, m)
        return EventStream(gen)

    async def _run_code(did, m):
        "Execute a code cell, streaming outputs as they arrive and collecting them for the file."
        outs = []
        try:
            kc = await kernels.client_for(did)
            async for jmsg in kc.run(m.content or ''):
                from fastcore.nbio import msg2out
                if jmsg['msg_type'] not in ('stream', 'error', 'execute_result', 'display_data'):
                    continue
                out = msg2out(jmsg)
                outs.append(out)
                if (el := ui.render_output(out)) is not None:
                    yield sse_message(el, event='chunk')
        except Exception as e:
            log.exception('running cell %s', m.id)
            err = mk_error(type(e).__name__, str(e))
            outs.append({k: v for k, v in err.items() if k != 'msg_type'} | {'output_type': 'error'})
            yield sse_message(ui.render_output(outs[-1]), event='chunk')
        m.output = outs
        dialogs.touch(did)
        await dialogs.flush(did)
        yield sse_message(ui.stream_end(m, did), event='chunk')
        yield sse_message('', event='done')

    async def _run_prompt(did, m):
        "Stream a model reply into a prompt cell, then store it as the cell's output."
        text = ''
        try:
            ctx = assemble(_dlg(did), m, budget=cfg.token_budget)
            if ctx.warning:
                log.warning('context for %s: %s', m.id, ctx.warning)
                yield sse_message(Div(f'⚠ {ctx.warning}', cls='prev-warn'), event='chunk')
            log.info('context for %s: %s tokens, %s evicted, %s hidden',
                     m.id, ctx.tokens, len(ctx.evicted), len(ctx.hidden))
            async for ev in provider.stream(ctx.messages):
                if isinstance(ev, TextDelta):
                    text += ev.text
                    yield sse_message(Span(ev.text), event='chunk')
                elif isinstance(ev, Failed):
                    text += f'\n\n**Error:** {ev.message}'
                    yield sse_message(Div(ev.message, cls='out-error'), event='chunk')
                elif isinstance(ev, Done):
                    log.info('prompt %s finished: %s', m.id, ev.usage)
        except Exception as e:
            log.exception('prompting cell %s', m.id)
            text += f'\n\n**Error:** {e}'
            yield sse_message(Div(str(e), cls='out-error'), event='chunk')
        m.output = prompt_output(text)
        dialogs.touch(did)
        await dialogs.flush(did)
        yield sse_message(ui.stream_end(m, did), event='chunk')
        yield sse_message('', event='done')

    app.state.cfg, app.state.store, app.state.dialogs = cfg, store, dialogs
    app.state.kernels, app.state.provider = kernels, provider
    return app


app = None


def main():
    global app
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    cfg = Config.from_env()
    app = create_app(cfg)
    print(f'dialogbook: workspace {cfg.workspace}  ->  http://{cfg.host}:{cfg.port}')
    serve(app='app', appname='dialogbook.app', host=cfg.host, port=cfg.port, reload=False)
