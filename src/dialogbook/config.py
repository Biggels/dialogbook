"""Configuration, read once from the environment.

Everything here has a working default, so `python -m dialogbook` runs with no setup.
A `.env` beside the project is loaded first, which is where the API key lives.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_WORKSPACE = Path.home()/'dialogbook'
DEFAULT_MODEL = 'claude-opus-5'


def _env_path(name, default):
    v = os.environ.get(name)
    return Path(v).expanduser() if v else default


def _env_int(name, default):
    v = os.environ.get(name)
    return int(v) if v else default


def _env_bool(name, default):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ('1', 'true', 'yes', 'on')


@dataclass(frozen=True)
class Config:
    "Runtime settings. `from_env` is the only intended constructor outside tests."
    workspace: Path = DEFAULT_WORKSPACE
    host: str = '127.0.0.1'  # localhost only: tool calls run unsandboxed Python
    port: int = 5001

    provider: str = 'anthropic'   # 'stub' to work without spending anything
    model: str = DEFAULT_MODEL
    # A ceiling, not a target — you are billed for what is generated, not for the cap.
    # Streaming means a high cap costs nothing but removes the risk of truncated answers.
    max_tokens: int = 64_000
    effort: str = 'high'          # low | medium | high | xhigh | max
    # Server-side refusal fallbacks. Disabled automatically for the session if the account
    # does not have the beta, so a missing entitlement degrades instead of breaking.
    fallbacks: bool = True

    # Deliberately conservative. Token counting is approximate (chars/4), so the budget
    # has to absorb the error rather than sit near a real context limit.
    token_budget: int = 100_000
    kernel_name: str = 'python3'
    # Seconds to coalesce edits before writing. 0 writes through synchronously, which is
    # what the tests want: a mutation is on disk the moment the request returns.
    save_debounce: float = 0.75

    @classmethod
    def from_env(cls, dotenv=True):
        if dotenv: load_dotenv()   # does not overwrite variables already set
        return cls(
            workspace=_env_path('DIALOGBOOK_WORKSPACE', DEFAULT_WORKSPACE),
            host=os.environ.get('DIALOGBOOK_HOST', '127.0.0.1'),
            port=_env_int('DIALOGBOOK_PORT', 5001),
            provider=os.environ.get('DIALOGBOOK_PROVIDER', 'anthropic'),
            model=os.environ.get('DIALOGBOOK_MODEL', DEFAULT_MODEL),
            max_tokens=_env_int('DIALOGBOOK_MAX_TOKENS', 64_000),
            effort=os.environ.get('DIALOGBOOK_EFFORT', 'high'),
            fallbacks=_env_bool('DIALOGBOOK_FALLBACKS', True),
            token_budget=_env_int('DIALOGBOOK_TOKEN_BUDGET', 100_000),
            kernel_name=os.environ.get('DIALOGBOOK_KERNEL', 'python3'),
            save_debounce=float(os.environ.get('DIALOGBOOK_SAVE_DEBOUNCE', 0.75)),
        )

    def ensure_workspace(self):
        self.workspace.mkdir(parents=True, exist_ok=True)
        return self.workspace
