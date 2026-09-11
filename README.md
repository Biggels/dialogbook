# dialogbook

A personal, self-hosted dialog-engineering notebook: one document mixing **notes**
(markdown), **code** (Python, persistent kernel), and **prompts** (messages to a model),
where the AI's context *is the dialog*. Single user, localhost only.

Design and milestones: [docs/plans/handoff.md](docs/plans/handoff.md).
Upstream findings and breakage: [docs/notes-upstream.md](docs/notes-upstream.md).

## Status

**M3 complete** — it talks to a real model. List, create and open dialogs; add, edit,
reorder, pin, hide and delete the three cell types; run code cells against a real Jupyter
kernel with output streaming in over SSE; ask a prompt cell and watch the **stub** provider
stream a canned reply. Everything saves to `.ipynb` as you go.

A prompt now sends the dialog above it: notes as markdown, code cells as fenced blocks
with their real outputs, and earlier prompts as genuine user/assistant turns. Hidden cells
are excluded, pinned cells survive eviction, and each output is capped so one runaway loop
cannot push everything else out. **Context** on any prompt cell shows exactly what will be
sent, turn by turn, with the token accounting behind it.

Prompts go to **Claude via the official `anthropic` SDK** (`claude-opus-5` by default,
adaptive thinking, effort `high`). Reasoning streams into the cell as it happens, in muted
italics, and is discarded when the answer lands — the dialog is the context, and reasoning
is not part of the dialog.

Put `ANTHROPIC_API_KEY` in a `.env` beside the project. With no key the app degrades to the
stub provider and still runs: the kernel and the notebook are useful on their own.

Next is M4, tools — Python functions defined in a dialog, callable by the model, executing
in that dialog's kernel.

### Environment

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | From `.env`. Without it, the stub provider is used. |
| `DIALOGBOOK_PROVIDER` | `anthropic` | `stub` to work without spending anything. |
| `DIALOGBOOK_MODEL` | `claude-opus-5` | |
| `DIALOGBOOK_EFFORT` | `high` | `low` / `medium` / `high` / `xhigh` / `max`. |
| `DIALOGBOOK_MAX_TOKENS` | `64000` | A ceiling, not a target — you pay for what is generated. |
| `DIALOGBOOK_TOKEN_BUDGET` | `100000` | Context budget, estimated at chars/4. |
| `DIALOGBOOK_WORKSPACE` | `~/dialogbook` | |
| `DIALOGBOOK_PORT` | `5001` | |

```bash
uv run python -m dialogbook
```

Then open http://127.0.0.1:5001. Dialogs live in `~/dialogbook/`
(`DIALOGBOOK_WORKSPACE` overrides it).

## Develop

```bash
uv sync
uv run pytest
```

The kernel tests spawn a real `jupyter server` subprocess and are Windows-specific in
their teardown (`taskkill /F /T`).
