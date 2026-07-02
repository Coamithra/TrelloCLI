"""Backend selection.

`get_backend()` returns the active data-source backend, chosen per-invocation via
the `--backend` flag / `TRELLO_BACKEND` env var (default `trello`) — no persisted
default, so concurrent invocations never fight over shared state. See DESIGN.md.
"""

from __future__ import annotations

from functools import lru_cache

from .base import Backend
from .trello import TrelloBackend

__all__ = ["Backend", "TrelloBackend", "get_backend"]


@lru_cache(maxsize=None)
def get_backend() -> Backend:
    """Return the active backend (a cached singleton for this invocation)."""
    from .. import config

    name = config.get_backend_name()
    if name == "trello":
        return TrelloBackend()
    if name == "local":
        from .local import LocalBackend

        return LocalBackend(config.get_local_root())
    if name == "http":
        from .http import HttpBackend

        return HttpBackend(config.get_server_url(), config.get_server_token())
    raise SystemExit(
        f"Unknown backend: {name!r} (use 'trello', 'local' or 'http')."
    )
