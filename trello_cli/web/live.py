"""Live-refresh plumbing for the local-backend web app.

Watches the local file-store root with `watchdog` and exposes a monotonic
version counter. The SSE endpoint (`GET /api/events`) polls `get_version()`; when
it moves, the browser reloads the board — so a Dropbox sync (or another
`--backend local` CLI mutation) shows up without a manual refresh.

The watchdog observer runs on its own thread; the only thing shared with the
async SSE generator is an integer behind a lock. That deliberately avoids any
event-loop hand-off (`call_soon_threadsafe`) — a counter the generator polls once
a second is simpler and naturally coalesces the burst of events an atomic write
(temp file + os.replace) produces into a single reload. Trello has no local files,
so this is a local-backend-only feature; the SSE endpoint just never starts a
watcher for the Trello backend.
"""

from __future__ import annotations

import threading
from pathlib import Path

_lock = threading.Lock()
_version = 0
_observer = None  # the running watchdog Observer (started at most once)
_watched_root: str | None = None


def _bump() -> None:
    global _version
    with _lock:
        _version += 1


def get_version() -> int:
    """Current change counter — increments on any file event under the root."""
    with _lock:
        return _version


def start_watching(root: str) -> bool:
    """Start watching `root` for changes (idempotent).

    Returns True if a watcher is active for this root afterwards. No-ops if the
    root doesn't exist yet, if watchdog isn't installed, or if a watcher is
    already running (the first root wins — the web server watches one store)."""
    global _observer, _watched_root
    if _observer is not None:
        return True
    p = Path(root).expanduser()
    if not p.exists():
        return False
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ModuleNotFoundError:
        return False

    class _Handler(FileSystemEventHandler):
        def on_any_event(self, event: object) -> None:
            _bump()

    obs = Observer()
    obs.schedule(_Handler(), str(p), recursive=True)
    obs.daemon = True
    obs.start()
    _observer = obs
    _watched_root = str(p)
    return True
