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


# ── claim comment body / claim id round-trip ──────────────────────────

def test_claim_text_starts_with_marker_and_id():
    """The marker + id prefix is the adjudication key; the explanation appended
    after it must not disturb parsing (in either direction — a rival on an older
    build posts the bare id and still parses)."""
    body = trello_mod._claim_text("abc123")
    assert body.startswith(f"{_CLAIM_MARKER}abc123")
    assert trello_mod._parse_claim(body) == "abc123"
    assert trello_mod._parse_claim(f"{_CLAIM_MARKER}abc123") == "abc123"


def test_claim_text_explains_itself_to_a_cold_reader():
    """The winner's comment is never retracted, so it must say what it is."""
    body = trello_mod._claim_text("abc123")
    assert "trello grab" in body
    assert "Claim: abc123" in body          # points at what the grab printed
    assert "in-progress list" in body       # where the real claim lives
    assert f"{int(trello_mod._GRAB_CLAIM_WINDOW.total_seconds())}s" in body


def test_won_claim_ranks_rival_posting_the_verbose_body():
    my = datetime(2026, 6, 1, 12, 0, 5, tzinfo=timezone.utc)
    earlier = (my - timedelta(seconds=2)).isoformat()
    be = _backend_with_comments([
        _comment(trello_mod._claim_text("mine"), my.isoformat()),
        _comment(trello_mod._claim_text("rival"), earlier),
    ])
    assert be._won_claim("c", "mine", my) is False


def _grab_backend(monkeypatch, cards, *, wins):
    """A TrelloBackend with the network stubbed out, recording the comments it
    posts. `wins` is consumed one adjudication at a time."""
    monkeypatch.setattr(trello_mod.time, "sleep", lambda *_: None)
    be = TrelloBackend()
    be._auth = ("k", "t")
    posted: list[str] = []
    moved: list[tuple[str, str]] = []
    verdicts = list(wins)

    be.get_cards_in_list = lambda lid: [c for c in cards if c["idList"] == lid]  # type: ignore
    def _move(card_id, list_id):
        moved.append((card_id, list_id))
        for c in cards:
            if c["id"] == card_id:
                c["idList"] = list_id
                return c
        raise AssertionError(card_id)
    be.move_card = _move  # type: ignore
    def _add_comment(card_id, text):
        posted.append(text)
        return {"id": f"action{len(posted)}", "date": "2026-06-01T12:00:00.000Z"}
    be.add_comment = _add_comment  # type: ignore
    be.delete_comment = lambda action_id: None  # type: ignore
    be._won_claim = lambda *a, **k: verdicts.pop(0)  # type: ignore
    be.get_card = lambda card_id: next(  # type: ignore
        {**c, "desc": "full"} for c in cards if c["id"] == card_id)
    return be, posted, moved


def test_grab_win_returns_the_id_it_posted(monkeypatch):
    """The whole point of the card: the caller must be handed the claim id so it
    can later recognize the lingering comment as its own."""
    cards = [{"id": "c1", "name": "First", "idList": "src", "pos": 1}]
    be, posted, _ = _grab_backend(monkeypatch, cards, wins=[True])

    got = be.grab_top_card("src", "dst")

    assert got is not None
    assert got["id"] == "c1" and got["desc"] == "full"   # the full re-fetched card
    assert got["claimId"] == trello_mod._parse_claim(posted[0])


def test_grab_claim_id_survives_a_lost_first_card(monkeypatch):
    """One claim id spans the whole grab; a loss on card 1 then a win on card 2
    still reports the id that is actually on the card we came away with."""
    cards = [
        {"id": "c1", "name": "First", "idList": "src", "pos": 1},
        {"id": "c2", "name": "Second", "idList": "src", "pos": 2},
    ]
    be, posted, _ = _grab_backend(monkeypatch, cards, wins=[False, True])

    got = be.grab_top_card("src", "dst")

    assert got is not None and got["id"] == "c2"
    assert got["claimId"] == trello_mod._parse_claim(posted[1])


def test_grab_empty_list_has_no_claim(monkeypatch):
    be, _, _ = _grab_backend(monkeypatch, [], wins=[])
    assert be.grab_top_card("src", "dst") is None


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
