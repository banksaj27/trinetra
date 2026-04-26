"""Configuration for the TriNetra cascade agent.

Loads settings from environment variables, falling back to a local `.env`
file (parsed manually — no `python-dotenv` dependency). Call `validate()`
explicitly from the agent entry point to fail fast on misconfiguration.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

PLACEHOLDER_SEED = "REPLACE_ME_WITH_RANDOM_SEED"
_ENV_PATH = Path(__file__).parent / ".env"


def _load_dotenv(path: Path = _ENV_PATH) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file. Missing file → empty dict."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip a single layer of matching surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


_DOTENV = _load_dotenv()


def _get(key: str, default: T | None = None, cast: Callable[[str], T] = str) -> T | None:
    """Resolve a setting from os.environ first, then .env, then default."""
    raw = os.environ.get(key)
    if raw is None or raw == "":
        raw = _DOTENV.get(key)
    if raw is None or raw == "":
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid value for {key}={raw!r}: {exc}") from exc


# ASI:One LLM
ASI_API_KEY: str = _get("ASI_API_KEY", "") or ""
ASI_BASE_URL: str = _get("ASI_BASE_URL", "https://api.asi1.ai/v1") or ""
ASI_MODEL: str = _get("ASI_MODEL", "asi1") or ""

# uAgent identity
AGENT_NAME: str = _get("AGENT_NAME", "trinetra-cascade-agent") or ""
AGENT_SEED: str = _get("AGENT_SEED", "") or ""
AGENT_PORT: int = _get("AGENT_PORT", 8001, int) or 8001

# TriNetra cascade FastAPI
TRINETRA_API_BASE: str = _get("TRINETRA_API_BASE", "http://localhost:8000") or ""
TRINETRA_API_TIMEOUT: float = _get("TRINETRA_API_TIMEOUT", 30.0, float) or 30.0


def validate() -> None:
    """Fail fast if required settings are missing or still placeholders."""
    missing: list[str] = []

    if not ASI_API_KEY:
        missing.append(
            "ASI_API_KEY is not set. Get a key from https://asi1.ai and add it "
            "to trinetra-agent/.env (see .env.example)."
        )

    if not AGENT_SEED:
        missing.append(
            "AGENT_SEED is not set. Add a long random string to "
            "trinetra-agent/.env as AGENT_SEED=... (see .env.example)."
        )
    elif AGENT_SEED == PLACEHOLDER_SEED:
        missing.append(
            f"AGENT_SEED is still the placeholder value {PLACEHOLDER_SEED!r}. "
            "Replace it with a unique random string in trinetra-agent/.env."
        )

    if missing:
        raise RuntimeError("Configuration error:\n  - " + "\n  - ".join(missing))
