"""Local file-store primitives for the LocalBackend.

Backend-agnostic building blocks — 24-hex id generation, atomic JSON writes,
float-`pos` midpoint math, and an append-only JSONL activity log — plus a
`LocalStore` that knows the on-disk layout:

    <root>/<boardId>/
        board.json              {id, name, desc, closed, shortUrl}
        lists.json              [{id, name, pos, closed}]
        cards/<cardId>.json     full Trello-shaped card dict
        activity.log            append-only JSONL (one mutation per line)

Atomic writes (temp file in the same dir + os.replace) keep a Dropbox-synced
folder from ever observing a half-written file. See DESIGN.md.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

POS_STEP = 65536.0  # Trello's default spacing between adjacent positions


def new_id() -> str:
    """A fresh 24-char hex id — matches Trello's id length, so `short_id` and the
    24-char resolver short-circuit behave identically across backends."""
    return secrets.token_hex(12)


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string (for `dateLastActivity` / activity)."""
    return datetime.now(timezone.utc).isoformat()


def resolve_pos(existing: list[float], pos: Any) -> float:
    """Resolve a position request to a concrete float.

    `pos` is a number (used as-is), or the keyword "top" / "bottom". "top" lands
    before the current minimum (min/2), "bottom" after the current maximum
    (max+STEP); an empty list yields STEP. This is the same float-midpoint model
    the `card pos` / `list pos` commands assume."""
    if isinstance(pos, bool):
        pos = "bottom" if pos else "top"
    if isinstance(pos, (int, float)):
        return float(pos)
    s = str(pos).strip().lower()
    if s == "top":
        if not existing:
            return POS_STEP
        # Always land strictly below the current minimum. min/2 does that while
        # staying positive in the common case; if a non-positive pos was ever
        # set explicitly, step below it instead (min/2 wouldn't be "above").
        m = min(existing)
        return m / 2 if m > 0 else m - POS_STEP
    if s == "bottom":
        return max(existing) + POS_STEP if existing else POS_STEP
    try:
        return float(s)
    except ValueError:
        # Unknown keyword: append at the bottom rather than raise — keeps a
        # mutation from hard-failing on a stray value.
        return max(existing) + POS_STEP if existing else POS_STEP


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        # A store file can be externally corrupted (e.g. a Dropbox conflict copy);
        # fail with a clean message rather than a traceback.
        raise SystemExit(f"Corrupt store file {path}: {e}")


def atomic_write_json(path: Path, obj: Any) -> None:
    """Write `obj` as pretty JSON to `path` atomically (temp + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class LocalStore:
    """On-disk layout + read/write helpers rooted at `root`."""

    def __init__(self, root: str | os.PathLike) -> None:
        self.root = Path(root).expanduser()

    # --- paths ---

    def board_dir(self, board_id: str) -> Path:
        return self.root / board_id

    def board_file(self, board_id: str) -> Path:
        return self.board_dir(board_id) / "board.json"

    def lists_file(self, board_id: str) -> Path:
        return self.board_dir(board_id) / "lists.json"

    def cards_dir(self, board_id: str) -> Path:
        return self.board_dir(board_id) / "cards"

    def card_file(self, board_id: str, card_id: str) -> Path:
        return self.cards_dir(board_id) / f"{card_id}.json"

    def activity_file(self, board_id: str) -> Path:
        return self.board_dir(board_id) / "activity.log"

    # --- discovery ---

    def board_ids(self) -> list[str]:
        """Every board directory under the root (those with a board.json)."""
        if not self.root.exists():
            return []
        return sorted(
            p.name for p in self.root.iterdir()
            if p.is_dir() and (p / "board.json").exists()
        )

    def cards(self, board_id: str) -> list[dict]:
        """Load every card dict on a board (any list, any closed state)."""
        cdir = self.cards_dir(board_id)
        if not cdir.exists():
            return []
        out = []
        for p in sorted(cdir.glob("*.json")):
            c = read_json(p)
            if c:
                out.append(c)
        return out

    # --- activity log ---

    def append_activity(self, board_id: str, entry: dict) -> None:
        path = self.activity_file(board_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
