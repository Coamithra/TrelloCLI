"""Area 4 — TrelloBackend pure logic (mocked, no network)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from trello_cli.backends import trello as trello_mod
from trello_cli.backends.trello import _CLAIM_MARKER, TrelloBackend


def _mk(handler):
    be = TrelloBackend()
    be._auth = ("key", "token")
    be._client = httpx.Client(base_url="https://api.trello.com/1",
                              transport=httpx.MockTransport(handler))
    return be


def _comment(text, date):
    return {"id": "x", "date": date, "data": {"text": text}}


# ── _won_claim ────────────────────────────────────────────────────────

def _backend_with_comments(comments):
    be = TrelloBackend()
    be._auth = ("k", "t")
    be.get_comments = lambda card_id, limit=10: comments  # type: ignore
    return be


def test_won_claim_win_no_rivals():
    my = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    be = _backend_with_comments([_comment(f"{_CLAIM_MARKER}mine", my.isoformat())])
    assert be._won_claim("c", "mine", my) is True


def test_won_claim_loses_to_earlier():
    my = datetime(2026, 6, 1, 12, 0, 5, tzinfo=timezone.utc)
    earlier = (my - timedelta(seconds=2)).isoformat()
    be = _backend_with_comments([
        _comment(f"{_CLAIM_MARKER}mine", my.isoformat()),
        _comment(f"{_CLAIM_MARKER}rival", earlier),
    ])
    assert be._won_claim("c", "mine", my) is False


def test_won_claim_unparseable_own_date_is_loss():
    be = _backend_with_comments([])
    assert be._won_claim("c", "mine", None) is False


def test_won_claim_naive_aware_mix_no_crash():
    my = datetime(2026, 6, 1, 12, 0, 5, tzinfo=timezone.utc)
    # A rival claim with a NAIVE timestamp must be normalized, not raise.
    naive_earlier = "2026-06-01T12:00:00"   # no tz
    be = _backend_with_comments([
        _comment(f"{_CLAIM_MARKER}mine", my.isoformat()),
        _comment(f"{_CLAIM_MARKER}rival", naive_earlier),
    ])
    assert be._won_claim("c", "mine", my) is False


def test_won_claim_ignores_marker_phrase_in_prose():
    my = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    earlier = (my - timedelta(seconds=2)).isoformat()
    # A comment that merely quotes the marker mid-sentence is not a claim.
    prose = f"As I said, {_CLAIM_MARKER}whatever — but I am not really claiming."
    be = _backend_with_comments([
        _comment(f"{_CLAIM_MARKER}mine", my.isoformat()),
        _comment(prose, earlier),
    ])
    assert be._won_claim("c", "mine", my) is True


def test_won_claim_stale_rival_ignored():
    my = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    stale = (my - timedelta(seconds=120)).isoformat()   # outside the 60s window
    be = _backend_with_comments([
        _comment(f"{_CLAIM_MARKER}mine", my.isoformat()),
        _comment(f"{_CLAIM_MARKER}rival", stale),
    ])
    assert be._won_claim("c", "mine", my) is True


# ── _request error translation ────────────────────────────────────────

def test_request_404_is_not_found():
    be = _mk(lambda r: httpx.Response(404, json={}))
    with pytest.raises(SystemExit) as ei:
        be._get("/cards/nope")
    assert "Not found" in str(ei.value)


def test_request_401_hints_credentials():
    be = _mk(lambda r: httpx.Response(401, json={}))
    with pytest.raises(SystemExit) as ei:
        be._get("/members/me/boards")
    assert "401" in str(ei.value)


def test_request_429_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(trello_mod.time, "sleep", lambda *_: None)
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"ok": True})

    be = _mk(handler)
    assert be._get("/x") == {"ok": True}
    assert state["n"] == 2


def test_request_transport_error_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(trello_mod.time, "sleep", lambda *_: None)
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"ok": 1})

    be = _mk(handler)
    assert be._get("/x") == {"ok": 1}
    assert state["n"] == 2


def test_request_transport_error_exhausts_to_systemexit(monkeypatch):
    monkeypatch.setattr(trello_mod.time, "sleep", lambda *_: None)

    def handler(request):
        raise httpx.ConnectError("down", request=request)

    be = _mk(handler)
    with pytest.raises(SystemExit) as ei:
        be._get("/x")
    assert "Network error" in str(ei.value)
