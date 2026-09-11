# Upstream notes

A running record of how these libraries actually behave, versus what the docs or the
design handoff assumed. Verified by running code on **Windows 11 + Python 3.12.1**.

## Resolved at M0

### Kernel cwd follows the server process, not `root_dir`

The handoff's workspace decision says "Kernel cwd = that directory", assuming
`ServerApp.root_dir` sets it. **It does not.** With `root_dir` alone, kernels start in
whatever directory the app process was launched from.

Fix: spawn the `jupyter server` subprocess with `cwd=<workspace>`. Covered by
`tests/test_m0_kernel.py::test_kernel_cwd_is_the_workspace`.

Separately, the kernels API *does* honour a per-kernel `path` (relative to `root_dir`),
which gives a per-dialog cwd if nested workspace folders ever land. Verified in
`test_per_kernel_path_sets_cwd`.

### Spawn `python -m jupyter_server`, not `python -m jupyter server`

The `jupyter` dispatcher shells out through `jupyter-server.exe`, leaving an
eight-process chain between our `Popen` handle and the actual server. `python -m
jupyter_server` starts it directly. `taskkill /F /T` cleaned up either shape without
orphans, but the short chain makes the pid we hold mean something.

### `ipymini` is Apache-2.0

Its PyPI metadata declares no license, but `AnswerDotAI/ipymini` carries an Apache-2.0
`LICENSE` file. The risk noted in the handoff is closed. We still default to `ipykernel`;
the option is simply no longer blocked.

## Resolved at M1

### `atomic_save` cannot overwrite on Windows — the big one

`aidialog.write_ipynb(dlg, path)` writes through fastcore's `atomic_save`, which finishes
with `Path(tmp).rename(dest)`. On Windows `rename` raises `FileExistsError` when the
destination exists, so **a dialog could be created but never saved again**. Every write
after the first failed.

`store.Store.save` therefore does its own atomic write: `write_ipynb(dlg)` with no
filename returns the notebook JSON, which we put in a temp file beside the target and move
into place with `os.replace` — atomic, and it overwrites, on every platform.

### FastHTML infers the request method from `nested_name`, which closures break

`@rt('/path')` on a handler named `patch` normally registers PATCH. The check is
`nested_name(func) in all_meths`, and for a handler defined inside a factory function
`nested_name` returns `create_app_patch`. That is not in `all_meths`, so inference falls
back to `['get','post']` — silently. PATCH and DELETE routes answered 405 while looking
completely correct.

Since all routes live inside `create_app`, every one now passes `methods=` explicitly.
Worth doing regardless: it puts the method next to the path.

### fasthtml bundles only the htmx-4 SSE extension

`htmx_exts` has `sse4` (for htmx 4) but no plain `sse`, while `fast_app` defaults to htmx
2.0.7. Passing `exts='sse'` raises `KeyError`. We add
`htmx-ext-sse@2.2.3` as a pinned `Script` header instead, and stay on htmx 2.

Verified in a real browser: `sse-swap` + `hx-swap="beforeend"` appends chunks, and
`sse-close="done"` closes the connection — the server sees exactly one `GET /stream` per
run, so nothing re-executes. The `EventSource` error logged in the console on close is
benign.

### `fasttransport` uses `httpx2`, not `httpx`

`jupyasyncclient` talks HTTP through `fasttransport.AsyncTransport`, which imports
`httpx2`. Plain `httpx` is not installed. Relevant if we ever need to share a client.
(Starlette's `TestClient` works regardless.)

## Our own bugs worth remembering

### Two process leaks around server startup

Both found by counting `jupyter_server` processes after a test run — sixteen had piled up.

1. **Fire-and-forget warm-up.** Opening a dialog starts its kernel in a background task.
   Shutdown did not know about those tasks, so it could finish while a spawn was still in
   flight, and the process that appeared afterwards belonged to nobody. Cancelling is *not*
   a fix: `asyncio.to_thread` keeps running after its awaiter is cancelled. Shutdown now
   waits for them.
2. **Unlocked `start()`.** Two dialogs opening at once both saw `running == False` and
   each spawned a server; only one was ever tracked. `start()` now holds a lock and
   re-checks a `_closed` flag after spawning.

`tests/test_m1_lifecycle.py` asserts the process count directly, including the
shutdown-during-startup ordering.

### Textareas post CRLF

Browser form submission converts newlines to `

`, which went straight into cell
sources. Normalised at the one place browser text enters (the PATCH route), so notebooks
keep plain `
` like every other tool writes.

## Confirmed at M3 (Anthropic SDK)

Verified against the live API on this account, `anthropic` 1.5.0:

- **Adaptive thinking with `display: "summarized"` streams real reasoning** as
  `thinking_delta` events ahead of the answer. Left at the default the thinking text comes
  back empty, which in a streaming UI reads as a long unexplained pause — so the display is
  set explicitly. Reasoning is shown live and never persisted: the dialog is the context,
  and reasoning is not part of the dialog.
- **`budget_tokens` is gone** on current models; `output_config: {effort}` replaces it.
- **Server-side refusal fallbacks work on this account** (`fallbacks: "default"` with beta
  `server-side-fallback-2026-07-01`, on `client.beta.messages.stream`). The provider still
  drops the parameter and retries once if a 400 names it, so an account without the
  entitlement loses the feature rather than every prompt.
- **`stop_reason: "refusal"` is an HTTP 200** with no usable content, so it must be checked
  before reading content blocks.
- The provider module is `providers/anthropic.py` and does `import anthropic`. Absolute
  imports mean that resolves to the installed SDK, not itself — deliberate, but worth
  knowing before anyone "fixes" it.

## Open

### `lisette` 0.1.36 is broken

Imports `toolslm.funccall`, which moved to `fastcore.funccall` in `toolslm` 0.3.48.
Avoided: the OpenAI SDK will be used directly. Not yet re-checked against a newer
`lisette`.
