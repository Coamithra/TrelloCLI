"""Config and state management for Trello CLI.

Stores active board/workspace in ~/.trello-cli.json.
Auth comes from env vars TRELLO_API_KEY and TRELLO_TOKEN.
"""

import json
import os
from pathlib import Path

CONFIG_PATH = Path.home() / ".trello-cli.json"


def _load() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def _save(data: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(data, indent=2))


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


def get_active_board() -> str | None:
    return _load().get("active_board")


def set_active_board(board_id: str, board_name: str = "") -> None:
    cfg = _load()
    cfg["active_board"] = board_id
    if board_name:
        cfg["active_board_name"] = board_name
    _save(cfg)


def get_active_board_name() -> str:
    return _load().get("active_board_name", "")


def save_credentials(api_key: str, token: str) -> None:
    cfg = _load()
    cfg["api_key"] = api_key
    cfg["token"] = token
    _save(cfg)
