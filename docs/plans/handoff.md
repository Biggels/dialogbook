# Dialogbook — design handoff

**Date:** 2026-09-09
**Status:** design validated, no code written
**Target machine:** personal (`C:\Users\biggels\projects\dialogbook`)

> **How to use this document.** Start a fresh Claude Code session with its working
> directory at `C:\Users\biggels\projects\dialogbook` and hand it this file. It is
> self-contained: everything below was settled in a prior brainstorming session on a
> different machine. Nothing has been built yet — the first action is M0.

---

## 0. Carried-over unknowns — RESOLVED 2026-09-09

Both were checked on this machine at M0. Findings recorded here; details in
[`docs/notes-upstream.md`](../notes-upstream.md).

- [x] **Git / GitHub identity on this machine.** Personal, and working. There is no
      `~/.ssh/config` and none is needed: `ssh -T git@github.com` authenticates as
      **`Biggels`** via the default `~/.ssh/id_ed25519`, and git's global identity is
      `Biggels <haackra@gmail.com>`. The work machine's `rhaack-subseven` situation does
      not carry over. Note that **`gh` is not installed here** — pushing over SSH needs
      no extra tooling, but creating a repo through the API does.

- [x] **`ipymini` license.** Apache-2.0. The PyPI metadata declares none, but
      `AnswerDotAI/ipymini` carries a full Apache-2.0 `LICENSE` file. The risk is closed.
      `ipykernel` remains the default; the option is simply no longer blocked.

The three findings in section 2 were re-verified on **Windows 11 + Python 3.12.1**. All
three hold. One assumption *outside* that list did not — see the Workspace row below.

---

## 1. What this is

A personal, self-hosted "dialog engineering" notebook. A single-user web app, run on
localhost, pointed at cloud model APIs.

The working model:

- One document (a "dialog") mixes three cell types: **notes** (markdown), **code**
  (Python, persistent kernel), and **prompts** (messages to a model).
- The AI's context *is the dialog*: every note, every code cell and its output, every
  earlier prompt and response above the current prompt. No chat sidebar — the notebook
  is the context window.
- Small steps by design. Write one or two lines, run them, read the output, ask the next
  question. The opposite of "generate 300 lines and hope."
- Everything stays editable, including AI responses. Dialogs are living documents, not
  append-only logs.
- Cells can be **pinned** (always in context) or **hidden** (excluded from context, still
  visible to the user).
- Python functions defined in the dialog can be exposed as tools; tool calls execute
  *inside that dialog's kernel*, so the model acts on live state.

This is a from-scratch build inspired by the working model of Answer.AI's Solveit.
**Do not use the name "Solveit"** — that is their brand. The project is `dialogbook`.

---

## 2. Prior research (verified by running code, not by reading blog posts)

**The Solveit server and frontend are private.** `AnswerDotAI/solveit` returns 404 to the
public. The server and UI must be built. What *is* open source (Apache-2.0 unless noted):

| Package | What it gives us | Status |
|---|---|---|
| `aidialog` | The `Dialog`/`Message` data model and `.ipynb` round-tripping. A dialog *is* a standard Jupyter notebook: prompts are code cells whose source starts with `%%prompt` and carry `solveit_ai: true` metadata; notes are markdown cells; pin/hide are `pinned`/`skipped` cell metadata. Verified: writes valid nbformat-4 and reads back with prompt output, pinned and hidden state intact, no server involved. | **Use as-is** |
| `jupyasyncclient` + `jupywire` | Async kernel client speaking the standard Jupyter Server REST/WebSocket API (`/api/kernels`). Verified: runs cells with persistent state against a *stock* `jupyter server`, on both `ipykernel` and `ipymini`. Kernel connect ~0.5s. | **Use as-is** |
| `ipymini` / `kernmini` | Answer.AI's small Jupyter-protocol kernel. **No license declared in PyPI metadata** — check the repo before depending on it. Plain `ipykernel` works fine. | Optional |
| `toolslm` | Tool-schema generation from Python functions; folder/repo-to-context helpers. | Use |
| `ipykernel_helper` | `read_url`, output formatting for AI consumption. | Use selectively |
| `claudette` | Anthropic chat wrapper; its `Chat` class already implements a tool-calling loop against a Python namespace. | Use for Anthropic |
| `lisette` | LiteLLM-based multi-provider wrapper. **Currently broken:** `lisette` 0.1.36 imports `toolslm.funccall`, which moved to `fastcore.funccall` in `toolslm` 0.3.48. | **Avoid**; use the OpenAI SDK directly |
| `dialoghelper` | Mostly a *client* library hitting ~a dozen Solveit server endpoints (`add_html_`, `create_dialog_`, `run_msgs_`, `restart_kernel_`, `pop_data_blocking_`) plus the cells API. Useful as an informal spec of what a server exposes. | Reference only |
| `fasthtml`, `monsterui` | The web framework Solveit's own UI is built on. | Use for UI |

**Architectural inference:** Solveit is Jupyter Server + a Jupyter-protocol kernel +
`.ipynb` persistence + a custom FastHTML frontend replacing JupyterLab. We are not
*choosing* Jupyter as a foundation; we are reusing the foundation the original sits on.

**Rejected — marimo.** Its reactive model forbids defining the same variable in more than
one cell, executes in DAG order derived from variable references, and stores notebooks as
`.py`. All three conflict structurally with an iterative, redefine-freely, edit-anything,
prompts-as-cells workflow, and would discard `aidialog`'s data model. Reactivity is a
different product.

**Rejected — JupyterLab AI extensions** (jupyter-ai, Notebook Intelligence). Chat sidebars
bolted onto JupyterLab. None make the prompt a first-class cell whose context is the
notebook. That cell-level integration is the entire point.

**Ecosystem churn is high.** These packages release constantly (dialoghelper: 124
releases) and break against each other, as with `lisette`. Pin every version in the
lockfile and expect to write small shims.

---

## 3. Decisions locked

| Area | Decision |
|---|---|
| **Name / location** | `dialogbook`, at `C:\Users\biggels\projects\dialogbook`. Personal project — explicitly **not** in the subseven work tree. |
| **Python** | Pin **3.12** via `uv` (`.python-version`). Widest wheel coverage for this stack; removes "is it the library or the interpreter?" from a project whose deps are already churny. |
| **Frontend** | Fresh, minimal FastHTML + HTMX. Single user, localhost only. Monaco for cell editing (CDN fine). SSE for streaming. |
| **Execution** | Headless stock `jupyter server` as a managed subprocess, driven by `jupyasyncclient`. One kernel per open dialog. Start with `ipykernel`. (Talking to kernels directly via `jupyter_client` and skipping HTTP loses `jupyasyncclient`'s reconnect logic and the cells API — revisit only if the subprocess becomes painful.) |
| **Kernel lifetime** | Started on dialog open. Stopped **only** on explicit close, explicit restart, or app shutdown. **No idle timeout** — silently killing a kernel destroys accumulated state, the one thing a notebook must never do. One Python process per open dialog is acceptable for a single user. |
| **Workspace** | `C:\Users\biggels\dialogbook` (confirmed 2026-09-09) — one flat directory of `.ipynb` files, filename derived from title. Kernel cwd = that directory, so relative paths in code cells resolve predictably. **Correction:** `ServerApp.root_dir` does *not* set kernel cwd; the server subprocess must be spawned with `cwd=<workspace>`. Foldering deferred. |
| **Persistence** | `aidialog` in memory, `.ipynb` on disk — every dialog stays openable in JupyterLab or VS Code as a fallback, and compatible with Solveit's own exports. |
| **Models** | **Revised 2026-09-11 (M3):** Anthropic via the **official `anthropic` SDK**, not `claudette`. `claudette` wraps this same SDK; what it adds is a tool loop that runs functions in the *app's* Python namespace, and requirement 5 puts tool execution in the dialog's Jupyter kernel — so that loop is the wrong shape, not merely unused. What remained was a thin layer over the SDK, and this stack has already shown what a thin layer costs when it drifts (`lisette`). OpenAI via its official SDK second. Key from `.env` / environment. Default `claude-opus-5`, adaptive thinking, effort `high`, all overridable by env var. |
| **Token counting** | Approximate, `chars/4`. Zero latency, no API calls. Eviction only needs "roughly how full"; set the budget conservatively (~100k) to absorb error. Swappable behind one function. |
| **Security** | Tool calls run arbitrary Python in the kernel with no sandbox. Acceptable for a personal tool bound to localhost. **Never expose beyond localhost without revisiting this.** |
| **Git remote** | No longer deferred (2026-09-09): a personal remote was requested. SSH push works today as `Biggels`; only *creating* the repo needs API auth, and `gh` is not installed here. |

---

## 4. Design

### 4.1 Process model and layout

**One app process, one managed child.** `python -m dialogbook` starts a uvicorn/FastHTML
app bound to `127.0.0.1`. On first dialog open it spawns a headless `jupyter server` as a
subprocess — random free port, generated token, `--no-browser`, `root_dir` set to the
workspace — and drives it over HTTP/WS with `jupyasyncclient`. The app owns that child's
lifetime: killed on shutdown, with a pidfile so a crashed run does not orphan a Python
process holding a port.

> **Windows caveat:** there are no POSIX process groups, so teardown needs an explicit
> `taskkill /T`-style kill rather than `killpg`. Verify this early (M0).

**Kernel registry.** `dialog_id -> kernel_id`, one entry per open dialog, created on open,
torn down only on explicit close/restart or app shutdown. No idle reaping.

**Layout:**

```
dialogbook/
  pyproject.toml  uv.lock  .python-version   # 3.12, everything pinned
  README.md
  docs/plans/
  src/dialogbook/
    __init__.py
    __main__.py     # python -m dialogbook -> uvicorn
    config.py       # workspace dir, model, token budget - from env
    store.py        # list/create/load/save .ipynb  (aidialog)
    kernels.py      # jupyter subprocess + per-dialog kernel registry
    context.py      # context assembly  <- the core feature
    providers/
      base.py       # Protocol: stream(messages, tools) -> async iterator of events
      stub.py       # canned responses, no network
      anthropic.py  # claudette
    tools.py        # tool-reference parsing, toolslm schemas, exec in kernel
    app.py          # FastHTML routes
    ui/
  tests/
```

**Why a provider protocol from day one:** the stub and the real Anthropic client implement
the same `stream(messages, tools) -> async iterator of events` interface. That is what
lets the whole UI and kernel path be built against canned responses and then swap in
`claudette` without touching `app.py`. It is also the seam where `lisette`'s breakage
stops mattering.

### 4.2 Data model, persistence, context assembly

**In-memory `aidialog.Dialog`, `.ipynb` on disk.** Cell `id` (nbformat 4.5) is the message
identity that HTMX targets, so re-rendering one cell does not touch the rest. Every
mutation — add, edit, delete, reorder, run, prompt completion — marks dirty and triggers a
debounced atomic save (write temp, `os.replace`). The file stays valid nbformat
throughout, so JupyterLab remains a working fallback.

**The assembler**, given a target prompt:

1. Walk messages *above* it in document order.
2. Drop anything flagged hidden (`skipped`).
3. Render each: notes as their markdown; code cells as a fenced `python` block plus
   outputs through `aidialog`'s `render_outputs_ai`; prior prompts as their user text and
   the response.
4. Cap each individual output — head + tail with an elision marker. One runaway loop
   printing 50k lines must not be able to evict the entire dialog.
5. Estimate at `chars/4`. While over budget, evict the oldest **non-pinned** message.
   Pinned never evicts; the target prompt never evicts. If pinned-only still exceeds
   budget, send anyway and surface a warning rather than silently mangling it.

**Role mapping.** Notes and code cells are not conversation turns. Decision: prior prompts
keep their real `user`/`assistant` turns, and notes/code cells become `user`-role messages
at their document position, with consecutive user-role items merged into one.

The alternative — flattening everything above into a single user blob — is simpler but
**breaks tool calling**: a prior prompt's tool use must round-trip as an `assistant`
`tool_use` block followed by a `user` `tool_result`, which is only expressible if
assistant turns stay genuine assistant turns. Requirement 5 (tools) forces this choice.

Context is recomputed per prompt run, not cached.

### 4.3 HTTP surface, UI, tools

**Routes.** `GET /` list, `POST /dialogs` create, `GET /d/{id}` open (starts kernel).
Cells: `POST /d/{id}/cells`, `PATCH|DELETE /d/{id}/cells/{cid}`, plus `/move`, `/run`,
`/pin`, `/hide`. Two SSE endpoints — one for code execution, one for prompts — because
Jupyter delivers iopub messages incrementally and model output streams; the same mechanism
serves both.

**Monaco, lazily.** An editor instance per cell will crawl on a long dialog. Unfocused
cells render as static highlighted HTML; Monaco is instantiated on focus, disposed on blur.

**Selection mode** is a small JS module holding `(selected_cell_id, mode)` — Jupyter's
modal editing: `j/k` navigate, `a/b` add above/below, `dd` cut, `x/c/v` cut/copy/paste,
`Enter`/`Esc` mode switch, `m/y/p` change type, `shift+P` pin, `shift+H` hide. Plus a
button on prompt output to extract fenced code blocks into new code cells.

**Tools.** Scan message text for the tool-reference convention — an ampersand followed by
a backticked function name, or a backticked list of names. The functions live *in the
kernel*, so schema generation must happen there: at kernel start, inject a helper that
imports `toolslm` and returns a JSON schema for a named symbol. Tool calls execute as a
call in that same kernel, result captured and fed back as `tool_result`.

---

## 5. Milestones

- **M0 — Verify, build nothing.** `uv` project, pinned deps, and prove on
  **Windows + Python 3.12**: (a) `aidialog` round-trips a dialog through `.ipynb` with
  prompt output, pinned and hidden state intact; (b) `jupyasyncclient` runs cells with
  persistent state against a stock `jupyter server` subprocess; (c) subprocess teardown
  leaves no orphan. Nothing else.
- **M1 — The walking skeleton (the first slice).** List/create/open dialogs; render the
  three cell types; add/edit/delete; run code cells against the **real kernel**; the
  **stub provider** streams canned text into a prompt cell; saves to `.ipynb`.
  *Real kernel, stub model* — exercises the hardest plumbing (subprocess lifecycle,
  websockets, mime bundles) while it is still cheap to change, and costs no tokens.
- **M2 — Context assembly** plus a preview panel showing exactly what will be sent.
- **M3 — Real Anthropic provider** via `claudette`.
- **M4 — Tools.**
- **M5 — Keyboard selection mode, pin/hide, extract fenced blocks, reorder.**

**Testing.** The assembler is pure functions and carries the bulk of the unit tests. Store
and kernels get integration tests against a real `jupyter server`. UI gets smoke tests only.

---

## 6. Explicitly deferred

Symbol browser; collapsible headings with token counts; ghost-text completion; inline
"super edit"; real-time collaboration; per-message undo; publishing/export views;
terminal; file browser; implementing `dialoghelper`'s server endpoints; local model
support; nested workspace folders; per-dialog model selection.

---

## 7. Risk register

| Risk | Note |
|---|---|
| `lisette` 0.1.36 broken | Imports `toolslm.funccall`; moved to `fastcore.funccall` in `toolslm` 0.3.48. Use the OpenAI SDK directly. |
| `ipymini` license | None declared in PyPI metadata. Check the repo before depending. `ipykernel` avoids the question. |
| Ecosystem churn | Pin everything in `uv.lock`. Expect shims. Keep a running note of upstream breakage. |
| Windows subprocess teardown | No POSIX process groups. Verify orphan-free kill in M0. |
| fastcore idioms | `@patch`, `L` lists, `store_attr`. Thin docs across this stack — **read the source, do not guess the API**. |
| Monaco + HTMX | Swapping HTML out from under a live Monaco instance will leak editors. Dispose on blur. |

---

## 8. How to work on this

- Small steps, in the spirit of the thing being built.
- Prefer reusing the verified libraries above over reimplementing them; prefer reading
  their source over guessing their API.
- Pin dependencies. Keep a note of any upstream breakage encountered.
- **Verify claims by running code.** When a library does not behave as its README says,
  trust the running code and say so.
- **Ask before making a design decision not covered here.** Do not fill gaps with
  assumptions.

---

## 9. Bootstrap

```bash
mkdir -p /c/Users/biggels/projects/dialogbook && cd /c/Users/biggels/projects/dialogbook
git init
uv init --python 3.12 --lib --name dialogbook
```

Then M0. Before adding dependencies, check the personal machine's git/GitHub setup:

```bash
cat ~/.ssh/config; gh auth status
```
