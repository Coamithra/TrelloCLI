"""Area 1 — LocalBackend end-to-end + store math, all on a tmp file store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trello_cli.backends import store
from trello_cli.backends.store import (
    POS_STEP,
    even_positions,
    needs_rebalance,
    resolve_pos,
)


# ── CRUD ──────────────────────────────────────────────────────────────

def test_board_list_card_crud(backend):
    b = backend.create_board("My Board")
    bid = b["id"]
    assert b["name"] == "My Board"

    # default lists
    lists = backend.get_lists(bid)
    assert [l["name"] for l in lists] == ["To Do", "Doing", "Done"]

    # get_boards / get_board
    assert any(x["id"] == bid for x in backend.get_boards())
    assert backend.get_board(bid)["name"] == "My Board"

    todo = lists[0]["id"]
    card = backend.create_card(todo, "Buy milk", desc="2%")
    cid = card["id"]
    assert card["name"] == "Buy milk"
    assert card["idList"] == todo

    got = backend.get_card(cid)
    assert got["id"] == cid and got["desc"] == "2%"

    # rename / desc via update_card
    backend.update_card(cid, name="Buy oat milk")
    assert backend.get_card(cid)["name"] == "Buy oat milk"

    # move
    doing = lists[1]["id"]
    backend.move_card(cid, doing)
    assert backend.get_card(cid)["idList"] == doing

    # rename a list
    backend.rename_list(todo, "Backlog")
    assert backend.get_lists(bid)[0]["name"] == "Backlog"

    # archive a card -> gone from visible board cards
    backend.archive_card(cid)
    assert cid not in [c["id"] for c in backend.get_board_cards(bid, "visible")]
    assert cid in [c["id"] for c in backend.get_board_cards(bid, "closed")]


# ── grab_top_card ─────────────────────────────────────────────────────

def test_grab_returns_distinct_cards_then_none(board):
    backend, bid, lists = board
    src, dst = lists[0]["id"], lists[1]["id"]
    backend.create_card(src, "A")
    backend.create_card(src, "B")

    first = backend.grab_top_card(src, dst)
    second = backend.grab_top_card(src, dst)
    assert first is not None and second is not None
    assert first["id"] != second["id"]
    assert first["idList"] == dst and second["idList"] == dst

    # source drained -> None
    assert backend.grab_top_card(src, dst) is None


def test_grab_empty_list_returns_none(board):
    backend, bid, lists = board
    assert backend.grab_top_card(lists[0]["id"], lists[1]["id"]) is None


def test_grab_has_no_claim_id(board):
    """`claimId` identifies the claim COMMENT the Trello handshake leaves behind.
    The local backend claims under the store lock and posts no comment, so the
    key is absent — not a null, which would read as 'a claim exists, id unknown'
    (see base.py's transient-key contract)."""
    backend, bid, lists = board
    src, dst = lists[0]["id"], lists[1]["id"]
    backend.create_card(src, "A")
    got = backend.grab_top_card(src, dst)
    assert got is not None and "claimId" not in got


# ── Transient Windows sharing violations (Dropbox / antivirus) ────────
#
# A store on a synced folder gets its files momentarily opened by Dropbox, the
# Search indexer or Defender. On Windows that makes `os.replace` fail with
# WinError 5/32 — a *transient* error. Unretried, it crashed `grab` mid-move and
# left the card in the source list, so the next agent grabbed the same card
# (observed 2026-07-24: two agents both claimed card 2e0f908b).

def _flaky(monkeypatch, attr, fail_times, match=".json"):
    """Make `store.os.<attr>` / `Path.<attr>` raise a Windows-style sharing
    violation the first `fail_times` calls that touch a matching path, then
    behave normally. Returns a counter dict so a test can assert it fired."""
    real = getattr(store.os, attr)
    state = {"failures": 0}

    def fake(src, dst, *a, **kw):
        if match in str(dst) and state["failures"] < fail_times:
            state["failures"] += 1
            raise PermissionError(13, "Access is denied", str(dst))
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(store.os, attr, fake)
    return state


def test_grab_survives_transient_file_lock(board, monkeypatch):
    """The bug: a transient lock on the card file aborted the move, leaving the
    card in the source list — so two grabbers in a row got the SAME card."""
    backend, bid, lists = board
    src, dst = lists[0]["id"], lists[1]["id"]
    backend.create_card(src, "A")
    backend.create_card(src, "B")

    state = _flaky(monkeypatch, "replace", fail_times=2)
    first = backend.grab_top_card(src, dst)
    second = backend.grab_top_card(src, dst)

    assert state["failures"] == 2, "the simulated lock never fired"
    assert first is not None and second is not None
    assert first["id"] != second["id"], "two grabbers claimed the same card"
    assert first["idList"] == dst and second["idList"] == dst
    assert backend.get_cards_in_list(src) == []


def test_store_write_gives_up_cleanly_when_lock_never_clears(board, monkeypatch):
    """A permanent lock must fail as a clean CLI error that says nothing moved —
    not a traceback naming the card file (which an agent misreads as a claim) —
    and must leave the store and its temp files untouched."""
    backend, bid, lists = board
    src, dst = lists[0]["id"], lists[1]["id"]
    card = backend.create_card(src, "A")
    cards_dir = store.LocalStore(str(backend.store.root)).cards_dir(bid)

    _flaky(monkeypatch, "replace", fail_times=10_000)
    with pytest.raises(SystemExit) as e:
        backend.grab_top_card(src, dst)

    msg = str(e.value)
    assert "locked" in msg.lower() and "nothing was changed" in msg.lower()
    assert backend.get_card(card["id"])["idList"] == src, "card moved anyway"
    assert list(cards_dir.glob("*.tmp")) == [], "left a temp file behind"


def test_store_lock_releases_when_the_lock_file_cannot_be_opened(tmp_path, monkeypatch):
    """If opening `.lock` fails outright, the in-process RLock must be released.
    Leaking it would deadlock a long-lived process (the web server) on its next
    mutation instead of surfacing the error."""
    lock = store.StoreLock(tmp_path / ".lock", timeout=0.1)
    monkeypatch.setattr(store, "LOCK_RETRY_DELAYS", (0.0,))

    def boom(*a, **kw):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr("builtins.open", boom)
    with pytest.raises(PermissionError):
        with lock:
            pass
    monkeypatch.undo()

    acquired = lock._rlock.acquire(blocking=False)
    assert acquired, "the RLock leaked — the next mutation would deadlock"
    lock._rlock.release()


def test_transient_lock_does_not_make_a_card_vanish(board, monkeypatch):
    """A transient lock on a *read* must not silently drop the card from the
    list (which would hand the next grabber a different card than the top one)."""
    backend, bid, lists = board
    src = lists[0]["id"]
    top = backend.create_card(src, "A")

    real_read = store.Path.read_text
    state = {"failures": 0}

    def flaky_read(self, *a, **kw):
        if self.suffix == ".json" and "cards" in str(self) and state["failures"] < 2:
            state["failures"] += 1
            raise PermissionError(13, "Access is denied", str(self))
        return real_read(self, *a, **kw)

    monkeypatch.setattr(store.Path, "read_text", flaky_read)
    names = [c["id"] for c in backend.get_cards_in_list(src)]
    assert state["failures"] == 2, "the simulated lock never fired"
    assert names == [top["id"]], "a transiently locked card vanished from the list"


# ── update_card returns an ENRICHED dict (X4 fix) ─────────────────────

def test_update_card_returns_enriched_shape(board):
    backend, bid, lists = board
    label = backend.create_label(bid, "urgent", "red")
    card = backend.create_card(lists[0]["id"], "C", labels=[label["id"]])

    out = backend.update_card(card["id"], name="C2")
    # enriched keys present
    assert "labels" in out and "idBoard" in out and "dueComplete" in out
    assert out["labels"] and out["labels"][0]["id"] == label["id"]
    # store-only keys must NOT leak
    assert "idLabels" not in out
    assert "comments" not in out


# ── update_card idList validation (X5 fix) ────────────────────────────

def test_update_card_rejects_unknown_list(board):
    backend, bid, lists = board
    card = backend.create_card(lists[0]["id"], "C")
    with pytest.raises(SystemExit):
        backend.update_card(card["id"], idList="ffffffffffffffffffffffff")


def test_update_card_rejects_archived_list(board):
    backend, bid, lists = board
    card = backend.create_card(lists[0]["id"], "C")
    backend.archive_list(lists[2]["id"])
    with pytest.raises(SystemExit):
        backend.update_card(card["id"], idList=lists[2]["id"])


# ── attachment path traversal (X1 fix) ────────────────────────────────

def test_download_attachment_refuses_traversal(backend, store_root):
    # relative traversal out of the store
    with pytest.raises(SystemExit):
        backend.download_attachment("../../../etc/hostname",
                                    str(backend.store.root / "out"), authed=True)
    # absolute path outside the store
    with pytest.raises(SystemExit):
        backend.download_attachment("/etc/hostname",
                                    str(backend.store.root / "out"), authed=True)


def test_delete_attachment_refuses_traversal(board):
    backend, bid, lists = board
    card = backend.create_card(lists[0]["id"], "C")
    cid = card["id"]
    # Inject a malicious upload attachment straight into the stored card JSON
    # (mirrors a Dropbox-shared / imported card carrying a traversal url).
    _, raw = backend._load_card(cid)
    raw["attachments"] = [{
        "id": "a" * 24, "name": "evil", "isUpload": True,
        "url": "../../../etc/hostname", "mimeType": "", "bytes": None,
    }]
    backend._save_card(bid, raw)
    with pytest.raises(SystemExit):
        backend.delete_attachment(cid, "a" * 24)
    # metadata not half-removed — the attachment is still there
    assert backend.get_attachments(cid)[0]["id"] == "a" * 24


def test_uploaded_blob_pinned_under_card_dir(board, tmp_path):
    backend, bid, lists = board
    card = backend.create_card(lists[0]["id"], "C")
    src = tmp_path / "note.txt"
    src.write_text("hello")
    att = backend.add_attachment_file(card["id"], str(src))
    assert att["isUpload"] is True
    # url is root-relative and pinned to attachments/<cardId>/
    assert att["url"].startswith(f"{bid}/attachments/{card['id']}/")
    # resolves inside the store
    resolved = backend._blob_path(att["url"])
    assert backend.store.root.resolve() in resolved.parents


# ── corrupt card JSON is skipped with a warning ───────────────────────

def test_corrupt_card_skipped_board_reads_survive(board, capsys):
    backend, bid, lists = board
    good = backend.create_card(lists[0]["id"], "Good")
    # Write a truncated/corrupt card file alongside the good one.
    bad_path = backend.store.card_file(bid, "b" * 24)
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text("{ this is not json")

    cards = backend.get_board_cards(bid, "visible")
    assert good["id"] in [c["id"] for c in cards]
    err = capsys.readouterr().err
    assert "skipping" in err.lower()


def test_corrupt_card_on_one_board_does_not_break_comment_on_another(backend):
    b1 = backend.create_board("B1")
    b2 = backend.create_board("B2")
    l2 = backend.get_lists(b2["id"])[0]["id"]
    card = backend.create_card(l2, "hello")
    cm = backend.add_comment(card["id"], "first")

    # Corrupt a card on board 1 (empty file counts as corrupt).
    bad = backend.store.card_file(b1["id"], "c" * 24)
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("")

    # The comment locator scans every board; it must survive the corrupt file.
    updated = backend.update_comment(cm["id"], "edited")
    assert updated["data"]["text"] == "edited"


# ── pos math + rebalance ──────────────────────────────────────────────

def test_resolve_pos_keywords_and_numbers():
    assert resolve_pos([], "top") == POS_STEP
    assert resolve_pos([], "bottom") == POS_STEP
    assert resolve_pos([100.0], "top") == 50.0
    assert resolve_pos([100.0], "bottom") == 100.0 + POS_STEP
    assert resolve_pos([1.0, 2.0], 42.5) == 42.5


def test_resolve_pos_rejects_bogus_keyword():
    with pytest.raises(SystemExit):
        resolve_pos([1.0], "middle")


def test_needs_rebalance_and_even_positions():
    assert needs_rebalance([1.0, 1.5]) is True        # gap < MIN_GAP
    assert needs_rebalance([POS_STEP, 2 * POS_STEP]) is False
    assert needs_rebalance([]) is False
    assert needs_rebalance([5.0]) is False
    assert even_positions(3) == [POS_STEP, 2 * POS_STEP, 3 * POS_STEP]


def test_create_top_and_bottom_order(board):
    backend, bid, lists = board
    lst = lists[0]["id"]
    a = backend.create_card(lst, "A", pos="bottom")
    b = backend.create_card(lst, "B", pos="top")
    cards = backend.get_cards_in_list(lst)
    # B is at the top (smallest pos), A at the bottom
    assert [c["name"] for c in cards] == ["B", "A"]
    assert b["pos"] < a["pos"]


def test_rebalance_flag_transient_not_persisted(board):
    backend, bid, lists = board
    lst = lists[0]["id"]
    # Two open cards with a wide gap.
    a = backend.create_card(lst, "A", pos="bottom")   # pos == POS_STEP
    b = backend.create_card(lst, "B", pos="bottom")   # pos == 2*POS_STEP
    # Force A right next to B (gap 0.4 < MIN_GAP) -> respread fires.
    target = b["pos"] + 0.4
    out = backend.update_card(a["id"], pos=target)
    assert out.get("rebalanced") is True

    # The transient flag is never written to disk.
    raw = json.loads(backend.store.card_file(bid, a["id"]).read_text())
    assert "rebalanced" not in raw
    # And the list was actually respread to even spacing.
    positions = sorted(c["pos"] for c in backend.get_cards_in_list(lst))
    assert positions == even_positions(len(positions))


# ── import_board preserves per-list sort on re-import ─────────────────

def test_import_board_preserves_list_sort(board):
    backend, bid, lists = board
    todo = lists[0]["id"]
    backend.update_list(todo, sort="name")
    assert next(l for l in backend.get_lists(bid) if l["id"] == todo)["sort"] == "name"

    # Re-import a Trello-shaped snapshot that carries NO sort field.
    snap_board = {"id": bid, "name": "Test Board", "desc": "", "closed": False}
    snap_lists = [{"id": l["id"], "name": l["name"], "pos": l["pos"], "closed": False}
                  for l in lists]
    backend.import_board(snap_board, snap_lists, [], [])

    after = next(l for l in backend.get_lists(bid) if l["id"] == todo)
    assert after["sort"] == "name"


# ── archived list excludes its cards ──────────────────────────────────

def test_archived_list_cards_excluded(board):
    backend, bid, lists = board
    lst = lists[0]["id"]
    card = backend.create_card(lst, "orphan-to-be")
    backend.archive_list(lst)
    assert card["id"] not in [c["id"] for c in backend.get_board_cards(bid, "visible")]
    assert card["id"] not in [c["id"] for c in backend.get_my_cards()]


# ── unarchive gets a fresh bottom pos ─────────────────────────────────

def test_unarchive_lands_at_bottom(board):
    backend, bid, lists = board
    lst = lists[0]["id"]
    a = backend.create_card(lst, "A", pos="bottom")
    b = backend.create_card(lst, "B", pos="bottom")
    backend.archive_card(a["id"])
    restored = backend.unarchive_card(a["id"])
    # A comes back below B (largest pos among open cards).
    open_positions = [c["pos"] for c in backend.get_cards_in_list(lst)]
    assert restored["pos"] == max(open_positions)
    assert restored["pos"] > b["pos"]


# ── export --to local --fork ──────────────────────────────────────────
#
# A fork writes the snapshot under a NEW board id instead of the source's, so
# the copy is a board in its own right rather than the source's mirror. The
# hazard the tests below guard is that the board id is a *path component*
# (<root>/<bid>/attachments/<cardId>/): every attachment step has to run against
# the destination id, or the fork ends up pointing at the source's blobs.

SRC_BID = "aaaaaaaaaaaaaaaaaaaaaaaa"
SRC_LIST = "bbbbbbbbbbbbbbbbbbbbbbbb"
SRC_CARD = "cccccccccccccccccccccccc"
SRC_ATT = "dddddddddddddddddddddddd"


def _snapshot(att_url: str | None = None) -> tuple[dict, list, list, list]:
    """A minimal Trello-shaped board snapshot, as `_gather_board` would return
    it. With `att_url`, its one card carries one uploaded attachment."""
    board = {"id": SRC_BID, "name": "Source Board", "desc": "", "closed": False,
             "shortUrl": "https://trello.com/b/src"}
    lists = [{"id": SRC_LIST, "name": "To Do", "pos": 65536, "closed": False}]
    card = {"id": SRC_CARD, "idList": SRC_LIST, "name": "A card", "desc": "",
            "pos": 65536, "closed": False, "idLabels": [], "checklists": [],
            "comments": [], "attachments": []}
    if att_url:
        card["attachments"] = [{"id": SRC_ATT, "name": "note.txt",
                                "url": att_url, "isUpload": True, "bytes": 5}]
    return board, lists, [], [card]


@pytest.fixture
def export_cli(monkeypatch, store_root):
    """Drive `main.cmd_export` with a fake remote source.

    Export refuses a local *source* (it is a pull into the store), so the source
    backend is 'trello' with `_gather_board` / `download_attachment` stubbed —
    no network, and the destination is a real LocalBackend on the tmp store."""
    from trello_cli import api, config, main
    from trello_cli.backends.local import LocalBackend

    config.set_backend_override("trello")
    config.set_local_root_override(store_root)
    monkeypatch.setattr(main, "_require_board", lambda: SRC_BID)

    def run(args: list[str], snapshot=None):
        snap = snapshot if snapshot is not None else _snapshot()
        monkeypatch.setattr(main, "_gather_board", lambda _bid: snap)
        main.cmd_export(args)
        return LocalBackend(store_root)

    def fake_download(url, path, authed=False):
        Path(path).write_bytes(b"blob!")

    monkeypatch.setattr(api, "download_attachment", fake_download)
    return run


def test_import_board_fork_writes_under_a_new_id(board):
    """The backend override lands the snapshot elsewhere and leaves the source
    board alone — card ids stay, only the board id is reminted."""
    backend, bid, lists = board
    card = backend.create_card(lists[0]["id"], "keep-my-id")
    snap_board = {"id": bid, "name": "Test Board", "desc": "", "closed": False}
    snap_lists = [{"id": l["id"], "name": l["name"], "pos": l["pos"],
                   "closed": False} for l in lists]

    fork_id = store.new_id()
    out = backend.import_board(snap_board, snap_lists, [], [card],
                               board_id=fork_id)

    assert out["id"] == fork_id
    ids = {b["id"] for b in backend.get_boards()}
    assert fork_id in ids and bid in ids  # the source survives untouched
    assert [c["id"] for c in backend.get_board_cards(bid)] == [card["id"]]

    forked = backend.get_board_cards(fork_id)
    assert [c["id"] for c in forked] == [card["id"]]  # card ids preserved
    raw = json.loads(backend.store.card_file(fork_id, card["id"]).read_text())
    assert raw["idBoard"] == fork_id  # ...but re-parented


def test_fork_downloads_blobs_under_the_new_board_id(export_cli):
    """The wrinkle: blobs are fetched BEFORE import_board, into a dir keyed by
    board id. Mint it too late and they land under the source id, leaving the
    forked board pointing at nothing."""
    backend = export_cli(["--fork"],
                         _snapshot("https://trello.com/1/cards/x/att/note.txt"))

    boards = backend.get_boards()
    assert len(boards) == 1
    fork_id = boards[0]["id"]
    assert fork_id != SRC_BID

    att = backend.get_card(SRC_CARD)["attachments"][0]
    assert att["url"].startswith(f"{fork_id}/attachments/{SRC_CARD}/")
    blob = Path(backend.store.root) / att["url"]
    assert blob.is_file() and blob.read_bytes() == b"blob!"
    # Nothing was written under the source board's id.
    assert not (Path(backend.store.root) / SRC_BID).exists()


def test_fork_refetches_store_relative_blobs(export_cli):
    """An http source serves attachments out of its own store, so their urls are
    already store-relative — rooted at the SOURCE board's id. A mirror can leave
    those alone (same id, same path); a fork must re-fetch them, or its cards
    point into a board that isn't theirs."""
    backend = export_cli(
        ["--fork"], _snapshot(f"{SRC_BID}/attachments/{SRC_CARD}/{SRC_ATT}-note.txt"))

    fork_id = backend.get_boards()[0]["id"]
    att = backend.get_card(SRC_CARD)["attachments"][0]
    assert att["url"].startswith(f"{fork_id}/attachments/{SRC_CARD}/")
    assert (Path(backend.store.root) / att["url"]).is_file()


def test_fork_no_attachments_does_not_cross_link(export_cli, capsys):
    """--no-attachments skips the download, so the url still points at the
    source — it must NOT be rewritten to a path under the fork that holds no
    blob. Permanent, since no later export tracks a fork: say so on stderr."""
    src_url = f"{SRC_BID}/attachments/{SRC_CARD}/{SRC_ATT}-note.txt"
    backend = export_cli(["--fork", "--no-attachments"], _snapshot(src_url))

    fork_id = backend.get_boards()[0]["id"]
    att = backend.get_card(SRC_CARD)["attachments"][0]
    assert att["url"] == src_url  # untouched, not re-rooted under the fork
    assert not backend.store.attachments_root(fork_id).exists()
    assert "no later export tracks a forked board" in capsys.readouterr().err


def test_fork_twice_makes_two_boards(export_cli):
    """Create-new-each-time, like `export --to trello`: a fork is orphaned from
    its source, so there is nothing to refresh in place."""
    export_cli(["--fork"])
    backend = export_cli(["--fork"])
    ids = {b["id"] for b in backend.get_boards()}
    assert len(ids) == 2 and SRC_BID not in ids


def test_fork_renames_and_reports_its_source(export_cli, capsys):
    backend = export_cli(["--fork", "--name", "My Fork"])
    assert backend.get_boards()[0]["name"] == "My Fork"
    out = capsys.readouterr().out
    assert "Forked 'My Fork'" in out
    assert "not a mirror of" in out


def test_plain_export_still_mirrors_the_source_id(export_cli):
    backend = export_cli([])
    assert [b["id"] for b in backend.get_boards()] == [SRC_BID]


@pytest.mark.parametrize("args, expected", [
    # --name on a mirror would be undone by the next re-export.
    (["--name", "Nope"], "--fork"),
    # --to trello is already create-new-each-time; --fork would imply otherwise.
    (["--to", "trello", "--fork"], "already"),
    # An invented flag must never become the board's name.
    (["--fork", "--name", "--oops"], "got the flag --oops"),
])
def test_fork_flag_misuse_is_refused(args, expected):
    from trello_cli import main
    with pytest.raises(SystemExit) as e:
        main.cmd_export(args)
    assert expected in str(e.value)
