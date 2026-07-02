"""Area 1 — LocalBackend end-to-end + store math, all on a tmp file store."""

from __future__ import annotations

import json

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
