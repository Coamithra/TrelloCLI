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
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

POS_STEP = 65536.0  # Trello's default spacing between adjacent positions

# Rebalance a list once any two adjacent positions get this close. Float `pos`
# has finite precision: dropping a card into the same gap halves it each time,
# and after ~50 halvings two adjacent positions collapse to the *same* float,
# making order undefined. MIN_GAP is enormous next to that floor (~2^35 ULPs of
# headroom near the working range), so we respread long before a true collapse —
# which also means a requested midpoint is always strictly between its
# neighbours, never equal to one, keeping insertion order unambiguous.
MIN_GAP = 1.0


def new_id() -> str:
    """A fresh 24-char hex id — matches Trello's id length, so `short_id` and the
    24-char resolver short-circuit behave identically across backends."""
    return secrets.token_hex(12)


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string (for `dateLastActivity` / activity)."""
    return datetime.now(timezone.utc).isoformat()


def pos_below(m: float) -> float:
    """A position strictly below `m`: min/2 while positive (the common case),
    else a whole step below. The shared "insert at the top" rule, used by
    `resolve_pos` and LocalBackend's sorted auto-placement."""
    return m / 2 if m > 0 else m - POS_STEP


def resolve_pos(existing: list[float], pos: Any) -> float:
    """Resolve a position request to a concrete float.

    `pos` is a number (used as-is), or the keyword "top" / "bottom". "top" lands
    before the current minimum (min/2), "bottom" after the current maximum
    (max+STEP); an empty list yields STEP. This is the same float-midpoint model
    the `card pos` / `list pos` commands assume. An unrecognized keyword raises
    SystemExit rather than silently landing at the bottom (a stray value should
    surface as a clean error, not a wrong-but-successful move)."""
    if isinstance(pos, bool):
        pos = "bottom" if pos else "top"
    if isinstance(pos, (int, float)):
        return float(pos)
    s = str(pos).strip().lower()
    if s == "top":
        return pos_below(min(existing)) if existing else POS_STEP
    if s == "bottom":
        return max(existing) + POS_STEP if existing else POS_STEP
    try:
        return float(s)
    except ValueError:
        raise SystemExit(
            f"Invalid position: {pos!r}. Use a number, 'top', or 'bottom'."
        )


def needs_rebalance(positions: list[float]) -> bool:
    """True if any two adjacent positions (once sorted) are closer than MIN_GAP,
    i.e. the gap math is approaching the float-collapse floor and the list should
    be respread to even spacing. False for an empty or single-element list."""
    s = sorted(positions)
    return any(b - a < MIN_GAP for a, b in zip(s, s[1:]))


def even_positions(n: int) -> list[float]:
    """`n` evenly-spaced positions (POS_STEP, 2*POS_STEP, ...) — the target layout
    a rebalance assigns, in the caller's already-sorted order."""
    return [POS_STEP * (i + 1) for i in range(n)]


# ── Transient file-lock retries (synced folders / antivirus) ─────────
#
# A store file that another program has momentarily open cannot be replaced or
# read: on Windows that surfaces as WinError 5 (access denied) or 32/33
# (sharing/lock violation). On a Dropbox- or OneDrive-synced store this happens
# routinely and briefly — the sync client, the Search indexer and Defender all
# open files behind our back for a few hundred milliseconds — so every store
# read and write retries before giving up.
#
# This is not cosmetic. Unretried, one such blip aborted `grab_top_card`
# mid-move: the card never left the source list, so the *next* agent to grab
# claimed the very same card, and the raw traceback (which names the card file)
# read enough like success that the first agent thought it held the card too.
# Two agents, one card — observed 2026-07-24 on the RotEA26 board.

_T = TypeVar("_T")

# ~1.4s all told, which comfortably outlasts a real sync/scan hold (hundreds of
# ms). Deliberately not longer: a mutator retries while HOLDING the store lock,
# so this budget is also every other process's added queueing delay — several
# queued writers each stalling the full budget must still fit inside
# LOCK_TIMEOUT (15s) or the waiters start failing instead.
LOCK_RETRY_DELAYS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.4, 0.6)


def is_transient_lock(e: BaseException) -> bool:
    """True for the OS errors another program's open handle produces.
    `PermissionError` is what a synced folder raises on every platform;
    WinError 32/33 (sharing / lock violation) arrive as plain `OSError`."""
    if isinstance(e, PermissionError):
        return True
    return isinstance(e, OSError) and getattr(e, "winerror", None) in (32, 33)


def retry_on_lock(op: Callable[[], _T]) -> _T:
    """Run `op`, retrying while another program holds the file open.

    Re-raises the original error if the lock never clears (callers decide
    whether that is fatal), and any non-lock error immediately — a real
    permission or disk problem must not be papered over with a 1.4s stall.
    """
    for delay in LOCK_RETRY_DELAYS:
        try:
            return op()
        except BaseException as e:
            if not is_transient_lock(e):
                raise
            time.sleep(delay)
    return op()  # last attempt: its error propagates as-is


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(retry_on_lock(lambda: path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as e:
        # A store file can be externally corrupted (e.g. a Dropbox conflict copy);
        # fail with a clean message rather than a traceback. Used for the
        # structural files (board.json / lists.json / labels.json) where a bad
        # file must fail fast; per-card reads use `read_json_tolerant` instead.
        raise SystemExit(f"Corrupt store file {path}: {e}")


def read_json_tolerant(path: Path) -> Any:
    """Like `read_json`, but returns None (with a one-line stderr warning) on a
    decode error or unreadable/empty file instead of raising. Per-card reads use
    this so one corrupt or half-synced card file (a Dropbox mid-sync writes a
    zero-byte file; `json.loads("")` raises) never aborts a whole-board scan or a
    cross-board comment/checklist lookup. Mirrors the tolerant `read_activity`.

    A *transiently* locked file is retried first: skipping it would silently
    drop a real card from its list, which for `grab_top_card` means handing the
    caller a different card than the one actually on top."""
    if not path.exists():
        return None
    try:
        return json.loads(retry_on_lock(lambda: path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        print(f"warning: skipping unreadable store file {path}: {e}", file=sys.stderr)
        return None


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically (temp file in the same dir + os.replace),
    so a Dropbox-synced folder never observes a half-written file. The temp file
    is cleaned up if the write or replace fails, so a mid-write crash doesn't
    leave a `.tmp` stray behind (`gc` sweeps any that a hard crash does).

    A transient lock on the destination (sync client / antivirus holding it
    open) is retried; if it never clears the write fails as a clean `SystemExit`
    saying nothing changed, rather than a traceback. That wording is load-
    bearing: an agent that read the raw traceback — which names the card file —
    took it for a successful claim and worked a card another agent then grabbed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")

    def write_once() -> None:
        tmp.write_text(text, encoding="utf-8")  # truncates: a retry restarts clean
        os.replace(tmp, path)

    try:
        retry_on_lock(write_once)
    except BaseException as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass  # the stray .tmp is `gc`'s problem; don't mask the real error
        if is_transient_lock(e):
            raise SystemExit(
                f"Could not write {path}: another program (Dropbox/OneDrive "
                f"sync, antivirus, or an open editor) held the file locked for "
                f"{sum(LOCK_RETRY_DELAYS):.1f}s. Nothing was changed; run the "
                f"command again."
            ) from e
        raise


def atomic_write_json(path: Path, obj: Any) -> None:
    """Write `obj` as pretty JSON to `path` atomically (temp + os.replace)."""
    atomic_write_text(path, json.dumps(obj, indent=2, ensure_ascii=False))


# ── Cross-process store lock ─────────────────────────────────────────
#
# Atomic writes stop a reader from seeing a half-written file, but they do
# nothing for two *writers*: every mutator is a read-modify-write over a whole
# file, so concurrent CLI processes both load, edit, and save — and the second
# save silently clobbers the first (a lost update). The fix is to serialize the
# whole load→modify→save of each mutation behind an advisory lock on a per-store
# `.lock` file. OS advisory locks are released automatically when the holding
# process dies, so a crash never strands a stale lock.

if sys.platform == "win32":
    import msvcrt

    def _os_trylock(fh) -> bool:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _os_unlock(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _os_trylock(fh) -> bool:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _os_unlock(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


LOCK_TIMEOUT = 15.0  # seconds to wait for the store lock before giving up
_LOCK_POLL = 0.05


class StoreLock:
    """A re-entrant, cross-process advisory lock guarding a store root.

    Cross-process exclusion comes from an OS advisory lock on a `.lock` file
    (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows) — the OS drops it if
    the holder dies, so a crashed process never leaves a stale lock behind.
    In-process re-entrancy and thread-safety come from a `threading.RLock`, so
    nested mutators (`archive_card` → `update_card`) and the multi-threaded web
    server don't self-deadlock on a second handle to the same file. Acquisition
    blocks, polling until `timeout`, then raises `SystemExit` — bounded
    "locking and waiting" rather than an unbounded hang.

    One instance per backend, reused across every `with lock:` — the recursion
    depth and the held file handle live on it.
    """

    def __init__(self, path: str | os.PathLike, timeout: float = LOCK_TIMEOUT) -> None:
        self._path = Path(path)
        self._timeout = timeout
        self._rlock = threading.RLock()
        self._fh: Any = None
        self._depth = 0

    def __enter__(self) -> "StoreLock":
        self._rlock.acquire()  # same thread re-enters freely; other threads wait
        if self._depth == 0:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            try:
                # A sync client / antivirus can hold `.lock` open for a moment;
                # retry rather than failing the mutation before it starts. Any
                # failure here must release the RLock we just took, or a
                # long-lived process (the web server) deadlocks on the next
                # mutation instead of surfacing the error.
                fh = retry_on_lock(lambda: open(self._path, "a+"))
            except BaseException:
                self._rlock.release()
                raise
            deadline = time.monotonic() + self._timeout
            while not _os_trylock(fh):
                if time.monotonic() >= deadline:
                    fh.close()
                    self._rlock.release()
                    raise SystemExit(
                        f"Timed out after {self._timeout:g}s waiting for the store lock "
                        f"({self._path}). Another process may be stuck holding it."
                    )
                time.sleep(_LOCK_POLL)
            self._fh = fh
        self._depth += 1
        return self

    def __exit__(self, *exc: Any) -> None:
        self._depth -= 1
        if self._depth == 0:
            try:
                _os_unlock(self._fh)
            except OSError:
                pass  # closing the handle below releases the OS lock regardless
            finally:
                self._fh.close()
                self._fh = None
        self._rlock.release()


_LOCKS: dict[str, StoreLock] = {}
_LOCKS_GUARD = threading.Lock()


def get_store_lock(path: str | os.PathLike, timeout: float = LOCK_TIMEOUT) -> StoreLock:
    """The process-wide `StoreLock` for `path`, created once and shared.

    Sharing one instance per lock-file path makes the lock re-entrant across
    *every* backend in a process — not just nested calls on one instance. Two
    `LocalBackend`s built for the same root (e.g. the source + target the
    `export` command juggles) reuse the same RLock and file handle instead of
    self-colliding on the OS lock (a second handle to the same byte range fails
    on Windows). Cross-process exclusion is unaffected — that's the OS lock's job."""
    key = os.path.abspath(os.path.expanduser(os.fspath(path)))
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = StoreLock(key, timeout)
            _LOCKS[key] = lock
        return lock


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

    def labels_file(self, board_id: str) -> Path:
        return self.board_dir(board_id) / "labels.json"

    def cards_dir(self, board_id: str) -> Path:
        return self.board_dir(board_id) / "cards"

    def card_file(self, board_id: str, card_id: str) -> Path:
        return self.cards_dir(board_id) / f"{card_id}.json"

    def attachments_root(self, board_id: str) -> Path:
        """Folder holding every card's uploaded attachment blobs on a board."""
        return self.board_dir(board_id) / "attachments"

    def attachments_dir(self, board_id: str, card_id: str) -> Path:
        """Folder holding a card's uploaded attachment blobs."""
        return self.attachments_root(board_id) / card_id

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
        """Load every card dict on a board (any list, any closed state).

        One corrupt / half-synced / empty card file is skipped with a stderr
        warning rather than aborting the whole scan (see `read_json_tolerant`).
        A file whose stem doesn't match the card id it carries is a Dropbox
        "conflicted copy" phantom — the same id under a `... (conflicted copy)`
        name — which would otherwise read as a duplicate card that never
        converges; skip it so only the canonical `<id>.json` counts."""
        cdir = self.cards_dir(board_id)
        if not cdir.exists():
            return []
        out = []
        for p in sorted(cdir.glob("*.json")):
            c = read_json_tolerant(p)
            if not c:
                continue
            if c.get("id") and p.stem != c["id"]:
                print(
                    f"warning: skipping card file {p} "
                    f"(id {c['id']} != filename)",
                    file=sys.stderr,
                )
                continue
            out.append(c)
        return out

    # --- activity log ---

    def append_activity(self, board_id: str, entry: dict) -> None:
        path = self.activity_file(board_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False) + "\n"

        def append_once() -> None:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)

        # The log is the last step of a mutation that has already been saved, so
        # a lock that never clears must not undo it: warn and drop the entry.
        try:
            retry_on_lock(append_once)
        except OSError as e:
            if not is_transient_lock(e):
                raise
            print(f"warning: activity log locked, entry not recorded: {e}",
                  file=sys.stderr)

    def read_activity(self, board_id: str) -> list[dict]:
        """Every activity-log entry, oldest first (file order). Blank or
        unparseable lines are skipped so a partially-synced log still reads."""
        path = self.activity_file(board_id)
        if not path.exists():
            return []
        out = []
        for line in retry_on_lock(lambda: path.read_text(encoding="utf-8")).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def activity_line_count(self, board_id: str) -> int:
        """Number of non-blank lines in the activity log (0 if none)."""
        path = self.activity_file(board_id)
        if not path.exists():
            return 0
        text = retry_on_lock(lambda: path.read_text(encoding="utf-8"))
        return sum(1 for line in text.splitlines() if line.strip())

    def tail_activity(self, board_id: str, keep: int) -> int:
        """Trim the activity log to its newest `keep` non-blank lines (atomic
        rewrite; `keep=0` clears it). Returns how many lines were dropped."""
        path = self.activity_file(board_id)
        if keep < 0 or not path.exists():
            return 0
        text = retry_on_lock(lambda: path.read_text(encoding="utf-8"))
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) <= keep:
            return 0
        kept = lines[len(lines) - keep:] if keep else []
        atomic_write_text(path, "".join(line + "\n" for line in kept))
        return len(lines) - keep
