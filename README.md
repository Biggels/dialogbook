# dialogbook

A personal, self-hosted dialog-engineering notebook: one document mixing **notes**
(markdown), **code** (Python, persistent kernel), and **prompts** (messages to a model),
where the AI's context *is the dialog*. Single user, localhost only.

Design and milestones: [docs/plans/handoff.md](docs/plans/handoff.md).
Upstream findings and breakage: [docs/notes-upstream.md](docs/notes-upstream.md).

## Status

**M2 complete** — context assembly landed. List, create and open dialogs; add, edit,
reorder, pin, hide and delete the three cell types; run code cells against a real Jupyter
kernel with output streaming in over SSE; ask a prompt cell and watch the **stub** provider
stream a canned reply. Everything saves to `.ipynb` as you go.

A prompt now sends the dialog above it: notes as markdown, code cells as fenced blocks
with their real outputs, and earlier prompts as genuine user/assistant turns. Hidden cells
are excluded, pinned cells survive eviction, and each output is capped so one runaway loop
cannot push everything else out. **Context** on any prompt cell shows exactly what will be
sent, turn by turn, with the token accounting behind it.

Real kernel, stub model — no tokens are spent and no API key is needed. Next is M3, the
real Anthropic provider.

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
