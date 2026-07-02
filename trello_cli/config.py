"""Config and state management for Trello CLI.

Stores credentials and the local-backend root in ~/.trello-cli.json. Board and
backend *selection* are per-invocation only — the `--board` / `--backend` flags
and the `TRELLO_BOARD` / `TRELLO_BACKEND` env vars — never persisted. The CLI is
used by many agents and projects concurrently, so it keeps no shared mutable
session state (no "active board"); see the Statelessness guideline in CLAUDE.md.
"""

import json
import os
from pathlib import Path

CONFIG_PATH = Path.home() / ".trello-cli.json"
DEFAULT_LOCAL_ROOT = Path.home() / "Dropbox" / "trello-cli"

_board_override: str | None = None
_backend_override: str | None = None
_local_root_override: str | None = None


def set_board_override(value: str) -> None:
    """Set a per-invocation board override (from --board flag)."""
    global _board_override
    _board_override = value


def get_board_override() -> str | None:
    """Return board override: --board flag > TRELLO_BOARD env var > None."""
    return _board_override or os.environ.get("TRELLO_BOARD")


def set_backend_override(value: str) -> None:
    """Set a per-invocation backend override (from --backend flag)."""
    global _backend_override
    _backend_override = value


def get_backend_name() -> str:
    """Return the selected backend: --backend flag > TRELLO_BACKEND env > 'trello'.

    Deliberately not persisted — a stored default would be shared mutable state
    across concurrent invocations. Selection is always per-invocation."""
    return (_backend_override or os.environ.get("TRELLO_BACKEND") or "trello").lower()


def _load() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, ValueError) as e:
        # A half-synced or hand-edited config shouldn't spew a raw JSON traceback
        # from every command — surface a clean one-liner instead.
        raise SystemExit(f"Corrupt config file {CONFIG_PATH}: {e}")


def _save(data: dict) -> None:
    # Atomic write (temp + os.replace) so a crash mid-write can't leave a
    # truncated config, mirroring store.py's writes. The file holds credentials,
    # so chmod it owner-only (0o600); chmod is best-effort — a no-op on Windows.
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, CONFIG_PATH)


def get_auth() -> tuple[str, str]:
    """Return (api_key, token) from env vars or config."""
    cfg = _load()
    key = os.environ.get("TRELLO_API_KEY") or cfg.get("api_key")
    token = os.environ.get("TRELLO_TOKEN") or cfg.get("token")
    if not key or not token:
        raise SystemExit(
            "Missing Trello credentials.\n"
            "Set TRELLO_API_KEY and TRELLO_TOKEN env vars,\n"
            "or run: trello configure <api_key> <token>"
        )
    return key, token


def set_local_root_override(value: str) -> None:
    """Set a per-invocation local-root override (from --local-root flag)."""
    global _local_root_override
    _local_root_override = value


def get_local_root() -> str:
    """Root directory for the local file backend.

    --local-root flag > TRELLO_LOCAL_ROOT env > config 'local_root' > ~/Dropbox/
    trello-cli. The flag/env are per-invocation overrides; the config value is a
    stable data location (like credentials), not session state, so it persists."""
    root = (
        _local_root_override
        or os.environ.get("TRELLO_LOCAL_ROOT")
        or _load().get("local_root")
        or str(DEFAULT_LOCAL_ROOT)
    )
    return os.path.expanduser(root)


def set_local_root(path: str) -> None:
    cfg = _load()
    cfg["local_root"] = path
    _save(cfg)


def save_credentials(api_key: str, token: str) -> None:
    cfg = _load()
    cfg["api_key"] = api_key
    cfg["token"] = token
    _save(cfg)
