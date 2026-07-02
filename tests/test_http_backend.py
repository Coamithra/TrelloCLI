"""Area 6 — HttpBackend over the web API (TestClient, no sockets).

The full remote round-trip, hermetically: the FastAPI app runs a LocalBackend
on a tmp store, a Starlette TestClient (an httpx.Client driving the ASGI app
in-process) is injected into HttpBackend, and every assertion goes
backend → /api/rpc (or the file routes) → facade → local store.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from trello_cli.backends.http import HttpBackend
from trello_cli.web.server import create_app
from tests.conftest import use_local_cli


@pytest.fixture
def http(store_root):
    """(hb, be, board_id, lists) — an HttpBackend talking to an in-process app
    whose facade is a LocalBackend on `store_root`."""
    be = use_local_cli(store_root)
    b = be.create_board("Http Board")
    bid = b["id"]
    lists = be.get_lists(bid)
    app = create_app(host="127.0.0.1")
    client = TestClient(app, base_url="http://127.0.0.1")
    hb = HttpBackend("http://127.0.0.1", client=client)
    return hb, be, bid, lists


# ── read round-trip fidelity ──────────────────────────────────────────

def test_reads_match_local_backend(http):
    hb, be, bid, lists = http
    be.create_card(lists[0]["id"], "Card A", desc="d", due="2026-08-01")
    assert hb.get_boards() == be.get_boards()
    assert hb.get_board(bid) == be.get_board(bid)
    assert hb.get_lists(bid) == be.get_lists(bid)
    assert hb.get_board_cards(bid) == be.get_board_cards(bid)
    card = be.get_cards_in_list(lists[0]["id"])[0]
    assert hb.get_card(card["id"]) == be.get_card(card["id"])


# ── card lifecycle via the remote backend ─────────────────────────────

def test_card_crud(http):
    hb, be, bid, lists = http
    card = hb.create_card(lists[0]["id"], "Remote card", desc="hello")
    assert card["name"] == "Remote card"
    renamed = hb.update_card(card["id"], name="Renamed")
    assert renamed["name"] == "Renamed"
    moved = hb.move_card(card["id"], lists[1]["id"])
    assert moved["idList"] == lists[1]["id"]
    hb.archive_card(card["id"])
    assert be.get_card(card["id"])["closed"] is True
    hb.unarchive_card(card["id"])
    assert be.get_card(card["id"])["closed"] is False


def test_grab_top_card(http):
    hb, be, bid, lists = http
    src, dst = lists[0]["id"], lists[1]["id"]
    be.create_card(src, "Bottom", pos="bottom")
    top = be.create_card(src, "Top", pos="top")
    got = hb.grab_top_card(src, dst)
    assert got["id"] == top["id"]
    assert be.get_card(top["id"])["idList"] == dst


def test_grab_empty_list_is_none(http):
    hb, _, _, lists = http
    assert hb.grab_top_card(lists[0]["id"], lists[1]["id"]) is None


def test_comments_labels_checklists(http):
    hb, be, bid, lists = http
    card = hb.create_card(lists[0]["id"], "C")
    hb.add_comment(card["id"], "hi from http")
    assert hb.get_comments(card["id"])[0]["data"]["text"] == "hi from http"
    lab = hb.create_label(bid, "urgent", color="red")
    hb.add_label_to_card(card["id"], lab["id"])
    assert [l["id"] for l in hb.get_card(card["id"])["labels"]] == [lab["id"]]
    hb.remove_label_from_card(card["id"], lab["id"])
    cl = hb.create_checklist(card["id"], "Steps")
    item = hb.add_checkitem(cl["id"], "step 1")
    hb.update_checkitem(card["id"], item["id"], state="complete")
    (got,) = hb.get_checklists(card["id"])
    assert got["checkItems"][0]["state"] == "complete"


# ── attachments: the two non-rpc file routes ──────────────────────────

def test_attachment_upload_download_roundtrip(http, tmp_path):
    hb, be, bid, lists = http
    card = hb.create_card(lists[0]["id"], "With file")
    src = tmp_path / "note.txt"
    src.write_bytes(b"attachment bytes")
    att = hb.add_attachment_file(card["id"], str(src))
    assert att["isUpload"] is True and att["name"] == "note.txt"
    assert hb.get_attachments(card["id"])[0]["id"] == att["id"]
    dest = tmp_path / "back.txt"
    hb.download_attachment(att["url"], str(dest), authed=True)
    assert dest.read_bytes() == b"attachment bytes"
    hb.delete_attachment(card["id"], att["id"])
    assert hb.get_attachments(card["id"]) == []


def test_blob_route_refuses_absolute_url(http):
    hb, *_ = http
    r = hb._client.get("/api/blob", params={"url": "https://evil.example/x"})
    assert r.status_code == 400


# ── error + auth mapping ──────────────────────────────────────────────

def test_not_found_is_clean_systemexit(http):
    hb, *_ = http
    with pytest.raises(SystemExit, match="not found"):
        hb.get_card("ffffffffffffffffffffffff")


def test_rpc_rejects_unknown_and_local_only_ops(http):
    hb, *_ = http
    for op in ("delete_board", "import_board", "gc", "__init__", "nope"):
        r = hb._client.post("/api/rpc", json={"op": op, "args": [], "kwargs": {}})
        assert r.status_code == 400, op
        assert "Unknown rpc op" in r.json()["detail"]


def test_rpc_bad_arity_is_400_not_500(http):
    hb, *_ = http
    r = hb._client.post("/api/rpc", json={"op": "get_card", "args": [],
                                          "kwargs": {"bogus": 1}})
    assert r.status_code == 400


def test_token_gate_maps_to_systemexit(store_root):
    be = use_local_cli(store_root)
    be.create_board("Tok")
    app = create_app(token="s3cret", host="127.0.0.1")
    bad = HttpBackend(
        "http://127.0.0.1", client=TestClient(app, base_url="http://127.0.0.1")
    )
    with pytest.raises(SystemExit, match="token"):
        bad.get_boards()
    good_client = TestClient(app, base_url="http://127.0.0.1")
    good_client.headers["Authorization"] = "Bearer s3cret"
    good = HttpBackend("http://127.0.0.1", client=good_client)
    assert good.get_boards()[0]["name"] == "Tok"


def test_unconfigured_server_is_clear_error():
    with pytest.raises(SystemExit, match="configure-http"):
        HttpBackend(None)
