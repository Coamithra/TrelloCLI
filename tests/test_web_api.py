"""Area 5 — web API (FastAPI TestClient over a local tmp store)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from trello_cli.web.server import create_app
from tests.conftest import use_local_cli


@pytest.fixture
def web(store_root):
    """(client, card_id, list_id, board_id) — a local board wired into the app.

    The TestClient uses a 127.0.0.1 base_url so the Host header passes the
    default TrustedHostMiddleware allow-list.
    """
    be = use_local_cli(store_root)
    b = be.create_board("Web Board")
    bid = b["id"]
    lists = be.get_lists(bid)
    card = be.create_card(lists[0]["id"], "Card")
    app = create_app(host="127.0.0.1")
    client = TestClient(app, base_url="http://127.0.0.1")
    return client, card["id"], lists[0]["id"], bid


# ── _guard ────────────────────────────────────────────────────────────

def test_guard_rejects_unknown_field(web):
    client, cid, *_ = web
    r = client.patch(f"/api/cards/{cid}", json={"bogus": 1})
    assert r.status_code == 400
    assert "bogus" in r.json()["detail"]


def test_guard_no_updatable_fields(web):
    client, _, lid, _ = web
    # `sort` is allowed for lists but an empty body has nothing to update.
    r = client.patch(f"/api/lists/{lid}", json={})
    assert r.status_code == 400


# ── validation vs not-found mapping ───────────────────────────────────

def test_bad_sort_is_400(web):
    client, _, lid, _ = web
    r = client.patch(f"/api/lists/{lid}", json={"sort": "weird"})
    assert r.status_code == 400


def test_missing_card_is_404(web):
    client, *_ = web
    r = client.get("/api/cards/ffffffffffffffffffffffff")
    assert r.status_code == 404


def test_valid_card_patch_ok(web):
    client, cid, *_ = web
    r = client.patch(f"/api/cards/{cid}", json={"name": "Renamed"})
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"
    # response is the enriched shape, not leaking store-only keys
    assert "idLabels" not in r.json()


# ── Host-header (DNS-rebinding) guard ─────────────────────────────────

def test_evil_host_rejected(store_root):
    use_local_cli(store_root)
    app = create_app(host="127.0.0.1")
    client = TestClient(app, base_url="http://evil.example")
    r = client.get("/api/boards")
    assert r.status_code == 400


def test_loopback_host_ok(web):
    client, *_ = web
    r = client.get("/api/boards")
    assert r.status_code == 200


# ── token gate ────────────────────────────────────────────────────────

@pytest.fixture
def token_web(store_root):
    be = use_local_cli(store_root)
    be.create_board("Tok")
    app = create_app(token="s3cret", host="127.0.0.1")
    return TestClient(app, base_url="http://127.0.0.1")


def test_token_missing_rejected(token_web):
    assert token_web.get("/api/boards").status_code == 401


def test_token_bearer_accepted(token_web):
    r = token_web.get("/api/boards", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200


def test_token_query_param_accepted(token_web):
    r = token_web.get("/api/boards?token=s3cret")
    assert r.status_code == 200


def test_token_static_shell_public(token_web):
    # The static shell must stay reachable without a token so app.js can load.
    assert token_web.get("/").status_code == 200


# ── DELETE board confirm gate ─────────────────────────────────────────

def test_delete_board_without_confirm_refused(web):
    client, _, _, bid = web
    r = client.delete(f"/api/boards/{bid}")
    assert r.status_code == 400
    assert "confirm" in r.json()["detail"].lower()


def test_delete_board_with_confirm_purges(web):
    client, _, _, bid = web
    r = client.delete(f"/api/boards/{bid}?confirm=true")
    assert r.status_code == 200
    # gone afterwards
    assert client.get(f"/api/boards/{bid}").status_code == 404
