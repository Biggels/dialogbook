"""Configuration, read once from the environment.

Everything here has a working default, so `python -m dialogbook` runs with no setup.
"""
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_WORKSPACE = Path.home()/'dialogbook'


def _env_path(name, default):
    v = os.environ.get(name)
    return Path(v).expanduser() if v else default


def _env_int(name, default):
    v = os.environ.get(name)
    return int(v) if v else default


@dataclass(frozen=True)
class Config:
    "Runtime settings. `from_env` is the only intended constructor outside tests."
    workspace: Path = DEFAULT_WORKSPACE
    host: str = '127.0.0.1'  # localhost only: tool calls run unsandboxed Python
    port: int = 5001
    model: str = 'claude-sonnet-4-5'
    # Deliberately conservative. Token counting is approximate (chars/4), so the budget
    # has to absorb the error rather than sit near a real context limit.
    token_budget: int = 100_000
    kernel_name: str = 'python3'
    # Seconds to coalesce edits before writing. 0 writes through synchronously, which is
    # what the tests want: a mutation is on disk the moment the request returns.
    save_debounce: float = 0.75

    @classmethod
    def from_env(cls):
        return cls(
            workspace=_env_path('DIALOGBOOK_WORKSPACE', DEFAULT_WORKSPACE),
            host=os.environ.get('DIALOGBOOK_HOST', '127.0.0.1'),
            port=_env_int('DIALOGBOOK_PORT', 5001),
            model=os.environ.get('DIALOGBOOK_MODEL', 'claude-sonnet-4-5'),
            token_budget=_env_int('DIALOGBOOK_TOKEN_BUDGET', 100_000),
            kernel_name=os.environ.get('DIALOGBOOK_KERNEL', 'python3'),
            save_debounce=float(os.environ.get('DIALOGBOOK_SAVE_DEBOUNCE', 0.75)),
        )

    def ensure_workspace(self):
        self.workspace.mkdir(parents=True, exist_ok=True)
        return self.workspace
