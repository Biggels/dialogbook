# dialogbook

A personal, self-hosted dialog-engineering notebook: one document mixing **notes**
(markdown), **code** (Python, persistent kernel), and **prompts** (messages to a model),
where the AI's context *is the dialog*. Single user, localhost only.

Design and milestones: [docs/plans/handoff.md](docs/plans/handoff.md).
Upstream findings and breakage: [docs/notes-upstream.md](docs/notes-upstream.md).

## Status

**M0 complete** — dependencies pinned, foundations verified on Windows + Python 3.12.
Nothing is built yet; M1 (the walking skeleton) is next.

## Develop

```bash
uv sync
uv run pytest
```

The kernel tests spawn a real `jupyter server` subprocess and are Windows-specific in
their teardown (`taskkill /F /T`).
