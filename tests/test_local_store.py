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
# A fork re-ids the snapshot so the copy is a board in its own right rather than
# the source's mirror. Two hazards drive these tests:
#   - the board id is a *path component* (<root>/<bid>/attachments/<cardId>/), so
#     every attachment step has to run against the destination id or the fork
#     points at the source's blobs;
#   - `_locate_card` & friends scan every board and take the first hit, so ids
#     must stay unique store-wide or id-addressed writes land on the wrong board.

SRC_BID = "aaaaaaaaaaaaaaaaaaaaaaaa"
SRC_LIST = "bbbbbbbbbbbbbbbbbbbbbbbb"
SRC_CARD = "cccccccccccccccccccccccc"
SRC_ATT = "dddddddddddddddddddddddd"
SRC_LABEL = "eeeeeeeeeeeeeeeeeeeeeeee"
SRC_COMMENT = "ffffffffffffffffffffffff"
SRC_CHECKLIST = "111111111111111111111111"
SRC_ITEM = "222222222222222222222222"
# Every id the snapshot below carries — a fork must reuse none of them.
SRC_IDS = {SRC_BID, SRC_LIST, SRC_CARD, SRC_ATT, SRC_LABEL, SRC_COMMENT,
           SRC_CHECKLIST, SRC_ITEM}


def _snapshot(att_url: str | None = None) -> tuple[dict, list, list, list]:
    """A Trello-shaped board snapshot, as `_gather_board` would return it: one
    card carrying one of everything that holds a store id. With `att_url`, its
    attachment is an upload at that url."""
    board = {"id": SRC_BID, "name": "Source Board", "desc": "", "closed": False,
             "shortUrl": "https://trello.com/b/src"}
    lists = [{"id": SRC_LIST, "name": "To Do", "pos": 65536, "closed": False}]
    labels = [{"id": SRC_LABEL, "name": "bug", "color": "red"}]
    card = {
        "id": SRC_CARD, "idList": SRC_LIST, "name": "A card", "desc": "",
        "pos": 65536, "closed": False, "idLabels": [SRC_LABEL],
        "comments": [{"id": SRC_COMMENT, "text": "hi", "date": "2026-01-01"}],
        "checklists": [{"id": SRC_CHECKLIST, "idCard": SRC_CARD, "name": "Steps",
                        "checkItems": [{"id": SRC_ITEM, "idChecklist": SRC_CHECKLIST,
                                        "name": "step one", "state": "incomplete"}]}],
        "attachments": [],
    }
    if att_url:
        card["attachments"] = [{"id": SRC_ATT, "name": "note.txt",
                                "url": att_url, "isUpload": True, "bytes": 5}]
    return board, lists, labels, [card]


def _all_ids(backend, bid: str) -> set[str]:
    """Every store id the board holds, from the raw card files (the enriched read
    resolves labels and drops comments)."""
    ids = {bid} | {l["id"] for l in backend.get_lists(bid)}
    ids |= {lb["id"] for lb in backend.get_labels(bid)}
    for card in backend.store.cards(bid):
        ids |= {card["id"], *card.get("idLabels", [])}
        ids |= {c["id"] for c in card.get("comments", [])}
        ids |= {a["id"] for a in card.get("attachments", [])}
        for cl in card.get("checklists", []):
            ids |= {cl["id"], *(it["id"] for it in cl.get("checkItems", []))}
    return ids


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


def test_import_board_board_id_override(board):
    """The backend override lands the snapshot elsewhere and leaves the source
    board alone. It re-ids only the BOARD — re-iding the contents is the export
    command's job (`_fork_snapshot`), since blob paths depend on it."""
    backend, bid, lists = board
    card = backend.create_card(lists[0]["id"], "somewhere-else")
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
    raw = json.loads(backend.store.card_file(fork_id, card["id"]).read_text())
    assert raw["idBoard"] == fork_id  # re-parented onto the new board


def test_fork_shares_no_id_with_its_source(export_cli):
    """The invariant that matters: `_locate_card` / `_locate_list` /
    `_locate_comment` / `_locate_checklist` scan EVERY board and take the first
    hit, so a duplicated id silently routes writes to the wrong board. A fork
    must therefore re-id everything, not just the board."""
    url = "https://trello.com/1/cards/x/att/note.txt"   # so SRC_ATT is in play
    export_cli(["--fork"], _snapshot(url))       # fork first...
    backend = export_cli([], _snapshot(url))     # ...then a mirror alongside it
    fork_id = next(b["id"] for b in backend.get_boards() if b["id"] != SRC_BID)

    fork_ids, mirror_ids = _all_ids(backend, fork_id), _all_ids(backend, SRC_BID)
    assert not fork_ids & mirror_ids
    assert not fork_ids & SRC_IDS                # nothing reused from the source
    assert mirror_ids >= SRC_IDS                 # ...while the mirror preserves

    # The cross-reference rewrite has to be complete, or the fork's own cards
    # dangle: each must sit in one of ITS lists and carry one of ITS labels.
    card = backend.get_board_cards(fork_id)[0]
    assert card["idList"] in {l["id"] for l in backend.get_lists(fork_id)}
    assert [lb["id"] for lb in card["labels"]] == \
        [lb["id"] for lb in backend.get_labels(fork_id)]
    raw = backend.store.cards(fork_id)[0]
    checklist = raw["checklists"][0]
    assert checklist["idCard"] == raw["id"]
    assert checklist["checkItems"][0]["idChecklist"] == checklist["id"]


def test_fork_and_mirror_writes_stay_on_their_own_board(export_cli):
    """The failure the id remint prevents, end to end: renaming the fork's card
    must not land on the mirror's copy."""
    export_cli(["--fork"])
    backend = export_cli([])
    fork_id = next(b["id"] for b in backend.get_boards() if b["id"] != SRC_BID)
    fork_card = backend.get_board_cards(fork_id)[0]

    backend.update_card(fork_card["id"], name="RENAMED VIA FORK")

    assert backend.get_board_cards(fork_id)[0]["name"] == "RENAMED VIA FORK"
    assert backend.get_board_cards(SRC_BID)[0]["name"] == "A card"


def test_fork_downloads_blobs_under_the_new_ids(export_cli):
    """The wrinkle: blobs are fetched BEFORE import_board, into a dir keyed by
    board id and named by attachment id. Re-id too late and they land under the
    source's ids, leaving the forked board pointing at nothing."""
    backend = export_cli(["--fork"],
                         _snapshot("https://trello.com/1/cards/x/att/note.txt"))

    boards = backend.get_boards()
    assert len(boards) == 1
    fork_id = boards[0]["id"]

    card = backend.get_board_cards(fork_id)[0]
    att = card["attachments"][0]
    assert att["url"] == (f"{fork_id}/attachments/{card['id']}/"
                          f"{att['id']}-note.txt")
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
    card = backend.get_board_cards(fork_id)[0]
    att = card["attachments"][0]
    assert att["url"].startswith(f"{fork_id}/attachments/{card['id']}/")
    assert (Path(backend.store.root) / att["url"]).is_file()


def test_fork_no_attachments_does_not_cross_link(export_cli, capsys):
    """--no-attachments skips the download, so the url still points at the
    source — it must NOT be rewritten to a path under the fork that holds no
    blob. Permanent, since no later export tracks a fork: say so on stderr."""
    src_url = f"{SRC_BID}/attachments/{SRC_CARD}/{SRC_ATT}-note.txt"
    backend = export_cli(["--fork", "--no-attachments"], _snapshot(src_url))

    fork_id = backend.get_boards()[0]["id"]
    att = backend.get_board_cards(fork_id)[0]["attachments"][0]
    assert att["url"] == src_url  # untouched, not re-rooted under the fork
    assert not backend.store.attachments_root(fork_id).exists()
    assert "no later export tracks a forked board" in capsys.readouterr().err


def test_fork_note_stays_quiet_without_uploads(export_cli, capsys):
    """Nothing is being skipped when the board has no uploads — don't warn."""
    export_cli(["--fork", "--no-attachments"])
    assert "no later export tracks a forked board" not in capsys.readouterr().err


def test_fork_twice_makes_two_boards(export_cli):
    """Create-new-each-time, like `export --to trello`: a fork is orphaned from
    its source, so there is nothing to refresh in place."""
    first = export_cli(["--fork"]).get_boards()[0]["id"]
    backend = export_cli(["--fork"])
    ids = {b["id"] for b in backend.get_boards()}
    assert ids == {first, next(i for i in ids if i != first)} and len(ids) == 2
    assert SRC_BID not in ids


def test_fork_renames_and_reports_its_source(export_cli, capsys):
    backend = export_cli(["--fork", "--name", "My Fork"])
    assert backend.get_boards()[0]["name"] == "My Fork"
    out = capsys.readouterr().out
    assert "Forked 'My Fork'" in out
    assert "not a mirror of" in out


def test_fork_json_reports_fork_and_source(export_cli, capsys, monkeypatch):
    """`forked` / `sourceId` are always present — the same stable-shape rule the
    zeroed `attachments` block follows — so a caller can branch on them."""
    from trello_cli import main

    monkeypatch.setattr(main, "_JSON_MODE", True)
    backend = export_cli(["--fork"])
    forked = json.loads(capsys.readouterr().out)
    export_cli([])
    mirrored = json.loads(capsys.readouterr().out)

    assert forked["forked"] is True
    assert forked["sourceId"] == SRC_BID
    assert forked["id"] not in (SRC_BID, "")
    assert mirrored["forked"] is False
    assert mirrored["sourceId"] == mirrored["id"] == SRC_BID
    assert len(backend.get_boards()) == 2


def test_plain_export_still_mirrors_the_source_id(export_cli):
    backend = export_cli([])
    assert [b["id"] for b in backend.get_boards()] == [SRC_BID]


def test_export_help_documents_fork():
    """`--fork` is irreversible and this usage text is where it's written down;
    answering --help with "Unknown flag" hides it."""
    from trello_cli import main

    main.cmd_export(["--help"])  # must not raise


@pytest.mark.parametrize("args, expected", [
    # --name on a mirror would be undone by the next re-export.
    (["--name", "Nope"], "--fork"),
    # ...including an empty one, which is a misuse rather than a no-op.
    (["--name", ""], "--fork"),
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


# ── per-list auto-sort: creation clock, aliases, arrivals ─────────────

def test_new_card_stamps_date_created(board):
    backend, bid, lists = board
    card = backend.create_card(lists[0]["id"], "stamped")
    raw = json.loads(backend.store.card_file(bid, card["id"]).read_text())
    assert raw["dateCreated"]


def test_card_created_backfills_from_a_trello_id(board):
    """A pre-`dateCreated` card imported from Trello knows its own creation time:
    a Trello id encodes it, and `shortLink` is what says the id is Trello's."""
    from trello_cli.backends.local import card_created

    trello_card = {"id": "4d5ea62fd76aa1136000000c", "shortLink": "abc12345",
                   "dateLastActivity": "2026-01-01T00:00:00.000Z"}
    assert card_created(trello_card).startswith("2011-02-")

    # No provenance → the id is a random local one, so it must NOT be decoded.
    local_card = {"id": "4d5ea62fd76aa1136000000c",
                  "dateLastActivity": "2026-01-01T00:00:00.000Z"}
    assert card_created(local_card) == "2026-01-01T00:00:00.000Z"


def test_card_created_backfills_from_the_activity_log(board):
    """Step 3 of the chain, and the one that matters on a real store: a
    pre-`dateCreated` card made locally has no Trello id to decode, but the
    board's log recorded when it was created."""
    from trello_cli.backends.local import card_created

    backend, bid, lists = board
    card = backend.create_card(lists[0]["id"], "logged")
    raw = json.loads(backend.store.card_file(bid, card["id"]).read_text())
    raw.pop("dateCreated")                       # a card written before the field
    raw["dateLastActivity"] = "2026-01-01T00:00:00.000Z"   # edited long after
    backend.store.card_file(bid, card["id"]).write_text(json.dumps(raw))

    index = backend._created_index(bid)
    assert index[card["id"]] != raw["dateLastActivity"]
    assert card_created(raw, index) == index[card["id"]]
    # Without the index there is nothing left but the last-resort fallback.
    assert card_created(raw) == "2026-01-01T00:00:00.000Z"


def test_card_created_prefers_a_trello_id_over_the_log(board):
    """Precedence: an imported card's own id is the authority, and a re-import
    can log a `createCard` for it long after Trello really made it."""
    from trello_cli.backends.local import card_created

    trello_card = {"id": "4d5ea62fd76aa1136000000c", "shortLink": "abc12345",
                   "dateLastActivity": "2026-01-01T00:00:00.000Z"}
    index = {"4d5ea62fd76aa1136000000c": "2026-05-05T00:00:00.000Z"}
    assert card_created(trello_card, index).startswith("2011-02-")


def test_created_index_takes_the_first_entry_for_a_card(board):
    """A re-import can log a second `createCard` for a card that already
    existed; the earliest is the true one."""
    backend, bid, lists = board
    card = backend.create_card(lists[0]["id"], "twice")
    first = backend._created_index(bid)[card["id"]]
    backend._log(bid, "createCard",
                 {"card": {"id": card["id"], "name": "twice"}})
    backend._created_indexes.clear()
    assert backend._created_index(bid)[card["id"]] == first


def test_created_sort_orders_by_the_log_not_last_activity(board):
    """The bug the split was opened for, at the column level: touching an old
    card must not move it under a `created-*` sort."""
    backend, bid, lists = board
    lst = lists[0]["id"]
    old = backend.create_card(lst, "old")
    new = backend.create_card(lst, "new")
    for card, activity in ((old, "2030-01-01T00:00:00.000Z"),
                           (new, "2020-01-01T00:00:00.000Z")):
        raw = json.loads(backend.store.card_file(bid, card["id"]).read_text())
        raw.pop("dateCreated")          # pre-field cards, so the log answers
        raw["dateLastActivity"] = activity
        backend.store.card_file(bid, card["id"]).write_text(json.dumps(raw))

    backend.update_list(lst, sort="created-newest")
    assert [c["name"] for c in backend.get_cards_in_list(lst)] == ["new", "old"]
    backend.update_list(lst, sort="activity-newest")
    assert [c["name"] for c in backend.get_cards_in_list(lst)] == ["old", "new"]


def test_import_resolves_created_before_a_fork_remints_the_id(export_cli):
    """A fork keeps the source's stale `shortLink` but mints a fresh random id;
    decoding THAT would file the card under a garbage date."""
    snap = _snapshot()
    card = snap[3][0]
    card["id"] = "4d5ea62fd76aa1136000000c"   # a real Trello id: 2011-02-19
    card["shortLink"] = "abc12345"
    backend = export_cli(["--fork"], snap)
    fork_id = next(b["id"] for b in backend.get_boards() if b["id"] != SRC_BID)
    assert backend.get_board_cards(fork_id)[0]["dateCreated"].startswith("2011-02-")


@pytest.mark.parametrize("stored, canonical", [
    ("newest", "activity-newest"),
    ("oldest", "activity-oldest"),
    ("name", "name"),
])
def test_pre_split_sort_values_normalize(board, stored, canonical):
    backend, bid, lists = board
    lst = lists[0]["id"]
    backend.update_list(lst, sort=stored)
    assert next(l for l in backend.get_lists(bid) if l["id"] == lst)["sort"] == canonical


def test_unknown_stored_sort_degrades_to_manual(board):
    """A store written by a newer build must still read, not crash."""
    backend, bid, lists = board
    raw = backend._load_lists(bid)
    raw[0]["sort"] = "by-vibes"
    backend._save_lists(bid, raw)
    assert next(l for l in backend.get_lists(bid) if l["id"] == raw[0]["id"])["sort"] == "manual"


def test_update_list_rejects_a_bogus_sort(board):
    backend, _, lists = board
    with pytest.raises(SystemExit) as e:
        backend.update_list(lists[0]["id"], sort="sideways")
    assert "created-newest" in str(e.value)


def test_move_into_an_activity_sorted_list_lands_on_top(board):
    """The reported bug: a card moved to a "newest first" Done sat at the bottom."""
    backend, _, lists = board
    todo, done = lists[0]["id"], lists[2]["id"]
    backend.create_card(done, "old-1", pos="bottom")
    backend.create_card(done, "old-2", pos="bottom")
    backend.update_list(done, sort="activity-newest")
    moved = backend.move_card(backend.create_card(todo, "arriving")["id"], done)
    assert [c["name"] for c in backend.get_cards_in_list(done)][0] == "arriving"
    assert moved["pos"] == min(c["pos"] for c in backend.get_cards_in_list(done))


def test_move_into_a_name_sorted_list_lands_alphabetically(board):
    backend, _, lists = board
    todo, done = lists[0]["id"], lists[2]["id"]
    backend.create_card(done, "apple", pos="bottom")
    backend.create_card(done, "zebra", pos="bottom")
    backend.update_list(done, sort="name")
    backend.move_card(backend.create_card(todo, "mango")["id"], done)
    assert [c["name"] for c in backend.get_cards_in_list(done)] == ["apple", "mango", "zebra"]


def test_an_explicit_pos_still_wins_over_the_list_sort(board):
    """The web drag sends {idList, pos} and clears the sort right after — the card
    has to land where the user dropped it, not where the sort wants it."""
    backend, _, lists = board
    todo, done = lists[0]["id"], lists[2]["id"]
    backend.create_card(done, "a", pos="bottom")
    backend.create_card(done, "b", pos="bottom")
    backend.update_list(done, sort="name")
    card = backend.create_card(todo, "aaa-would-sort-first")
    backend.update_card(card["id"], idList=done, pos="bottom")
    assert [c["name"] for c in backend.get_cards_in_list(done)][-1] == "aaa-would-sort-first"


def test_unarchive_into_a_sorted_list_lands_in_its_slot(board):
    backend, _, lists = board
    lst = lists[0]["id"]
    backend.create_card(lst, "apple", pos="bottom")
    card = backend.create_card(lst, "mango", pos="bottom")
    backend.create_card(lst, "zebra", pos="bottom")
    backend.archive_card(card["id"])
    backend.update_list(lst, sort="name")
    backend.unarchive_card(card["id"])
    assert [c["name"] for c in backend.get_cards_in_list(lst)] == ["apple", "mango", "zebra"]


def test_created_and_activity_sorts_disagree(board):
    """The whole point of the split: touching an old card moves it under
    `activity-newest` but not under `created-newest`."""
    backend, _, lists = board
    lst = lists[0]["id"]
    first = backend.create_card(lst, "first", pos="bottom")
    backend.create_card(lst, "second", pos="bottom")
    backend.update_card(first["id"], name="first-edited")  # bumps dateLastActivity only

    backend.update_list(lst, sort="created-newest")
    assert [c["name"] for c in backend.get_cards_in_list(lst)] == ["second", "first-edited"]

    backend.update_list(lst, sort="activity-newest")
    assert [c["name"] for c in backend.get_cards_in_list(lst)] == ["first-edited", "second"]
