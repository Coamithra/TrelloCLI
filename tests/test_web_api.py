"""Area 5 — web API (FastAPI TestClient over a local tmp store)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from trello_cli import magnet
from trello_cli.web import server as server_mod
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


# ── card magnet (the web UI's 🔗 Link control) ────────────────────────

def test_card_detail_carries_a_magnet(web):
    """The detail response is where the web client gets the string it copies —
    built server-side so `magnet.py` stays the only implementation of the
    grammar."""
    client, cid, _, bid = web
    body = client.get(f"/api/cards/{cid}").json()
    parsed = magnet.parse(body["_magnet"])
    assert parsed == {
        "type": "card", "id": cid, "board": bid,
        "backend": "local", "server": None, "slug": "card",
    }


def test_the_magnet_is_transient_and_never_stored(web, store_root):
    """Same contract as the upload route's `_attachment`: a response-only key. A
    stored one would end up in an export and outlive the backend it names."""
    client, cid, *_ = web
    assert "_magnet" in client.get(f"/api/cards/{cid}").json()
    assert "_magnet" not in use_local_cli(store_root).get_card(cid)


# The remaining cases are on `_card_magnet` directly: they turn on the backend
# name, and monkeypatching THAT through the route would also re-point
# `get_backend()`, i.e. test a different server than the one being described.

@pytest.mark.parametrize("card", [
    {"id": "a" * 24, "name": "No board"},                    # no idBoard at all
    {"id": "a" * 24, "idBoard": "short", "name": "Truncated"},  # not a 24-hex id
])
def test_a_card_with_no_usable_board_id_gets_no_magnet(card):
    assert server_mod._card_magnet(card) is None


def test_an_http_server_with_no_upstream_url_gets_no_magnet(monkeypatch):
    """`trello://card/http/…` needs the server segment to resolve anywhere else,
    so half a token is worse than none. The card still opens; the panel just
    shows no link (see openLinkPopover)."""
    monkeypatch.setattr(server_mod.config, "get_backend_name", lambda: "http")
    monkeypatch.setattr(server_mod.config, "get_server_url", lambda: None)
    assert server_mod._card_magnet({"id": "a" * 24, "idBoard": "b" * 24}) is None


def test_an_http_magnet_carries_the_server_url_and_never_the_token(monkeypatch):
    monkeypatch.setattr(server_mod.config, "get_backend_name", lambda: "http")
    monkeypatch.setattr(server_mod.config, "get_server_url",
                        lambda: "https://trellno.example.com")
    monkeypatch.setattr(server_mod.config, "get_server_token", lambda: "s3cret")
    token = server_mod._card_magnet({"id": "a" * 24, "idBoard": "b" * 24})
    assert magnet.parse(token)["server"] == "https://trellno.example.com"
    assert "s3cret" not in token


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


def test_allow_host_accepts_proxy_domain(store_root):
    # The hosted deployment: loopback bind behind a reverse proxy forwarding
    # the public domain in Host — allowed via extra_hosts, others still 400.
    use_local_cli(store_root)
    app = create_app(host="127.0.0.1", extra_hosts=("trellno.example.com",))
    ok = TestClient(app, base_url="http://trellno.example.com")
    assert ok.get("/api/boards").status_code == 200
    evil = TestClient(app, base_url="http://evil.example")
    assert evil.get("/api/boards").status_code == 400


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
