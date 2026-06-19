"""Backend selection.

`get_backend()` returns the active data-source backend. Phase 0 is Trello-only;
backend *selection* (a config `backend` key + `--backend` flag, plus the local
file store) arrives in Phase 1. See DESIGN.md.
"""

from __future__ import annotations

from functools import lru_cache

from .base import Backend
from .trello import TrelloBackend

__all__ = ["Backend", "TrelloBackend", "get_backend"]


@lru_cache(maxsize=None)
def get_backend() -> Backend:
    """Return the active backend (a cached singleton)."""
    return TrelloBackend()
