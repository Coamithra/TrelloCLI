"""Area 3 — cross-backend dict-shape conformance (no network).

The TrelloBackend is driven by an httpx.MockTransport returning realistic
Trello-shaped JSON built from the exact `fields=` lists trello.py requests. For
each shared read op we assert:
  * the shared contract keys (what fmt.py / app.js consume) are present in BOTH
    backends' dicts, and
  * for card-ish ops, LocalBackend's key set is a superset of TrelloBackend's
    (local enriches; it must never provide *fewer* keys).
"""

from __future__ import annotations

import httpx
import pytest

from trello_cli.backends.trello import TrelloBackend


# ── realistic Trello-shaped JSON, keyed by the fields trello.py asks for ──

_ISO = "2026-06-01T12:00:00.000Z"


def _trello_card_full():  # get_card fields + checklists + attachments
    return {
        "id": "c" * 24, "name": "Card", "desc": "d", "shortUrl": "http://x/c",
        "labels": [{"id": "l" * 24, "name": "urgent", "color": "red"}],
        "due": _ISO, "dueComplete": False, "idBoard": "b" * 24,
        "idList": "t" * 24, "idMembers": [], "dateLastActivity": _ISO,
        "checklists": [], "attachments": [],
    }


def _trello_board_card():  # get_board_cards fields
    return {
        "id": "c" * 24, "name": "Card", "shortUrl": "http://x/c",
        "labels": [], "due": _ISO, "dueComplete": False, "idList": "t" * 24,
        "idMembers": [], "dateLastActivity": _ISO, "pos": 65536,
    }


def _trello_list_card():  # get_cards_in_list fields (note: no idList)
    return {
        "id": "c" * 24, "name": "Card", "shortUrl": "http://x/c",
        "labels": [], "due": _ISO, "dueComplete": False,
        "idMembers": [], "dateLastActivity": _ISO, "pos": 65536,
    }


def _trello_my_card():  # get_my_cards fields
    return {
        "id": "c" * 24, "name": "Card", "shortUrl": "http://x/c",
        "labels": [], "due": _ISO, "dueComplete": False, "idBoard": "b" * 24,
        "idList": "t" * 24, "dateLastActivity": _ISO,
    }


def _trello_action():  # get_comments (unnarrowed action dict)
    return {
        "id": "a" * 24, "type": "commentCard", "date": _ISO,
        "idMemberCreator": "m" * 24,
        "memberCreator": {"id": "m" * 24, "username": "me", "fullName": "Me"},
        "data": {"text": "hi", "card": {"id": "c" * 24, "name": "Card"}},
    }


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/members/me/boards"):
        body = [{"id": "b" * 24, "name": "B", "shortUrl": "http://x/b", "closed": False}]
    elif path.endswith("/members/me/cards"):
        body = [_trello_my_card()]
    elif path.endswith("/lists"):                 # /boards/{id}/lists
        body = [{"id": "t" * 24, "name": "To Do", "pos": 65536}]
    elif path.endswith("/labels"):                # /boards/{id}/labels
        body = [{"id": "l" * 24, "name": "urgent", "color": "red"}]
    elif path.endswith("/cards") and "/boards/" in path:
        body = [_trello_board_card()]
    elif path.endswith("/cards") and "/lists/" in path:
        body = [_trello_list_card()]
    elif path.endswith("/actions"):               # /cards/{id}/actions
        body = [_trello_action()]
    elif "/cards/" in path:                        # /cards/{id}
        body = _trello_card_full()
    else:
        return httpx.Response(404, json={"error": path})
    return httpx.Response(200, json=body)


@pytest.fixture
def trello():
    be = TrelloBackend()
    be._auth = ("key", "token")
    be._client = httpx.Client(base_url="https://api.trello.com/1",
                              transport=httpx.MockTransport(_handler))
    return be


@pytest.fixture
def local(board):
    """A populated local board: one card (with label + comment) in the top list."""
    backend, bid, lists = board
    todo = lists[0]["id"]
    label = backend.create_label(bid, "urgent", "red")
    card = backend.create_card(todo, "Card", labels=[label["id"]])
    backend.add_comment(card["id"], "hi")
    return backend, bid, todo, card["id"]


# Shared contract keys consumed by fmt.py / app.js.
CARD_CONTRACT = {"id", "name", "pos", "idList", "due", "dueComplete", "labels"}
DETAIL_CONTRACT = {"id", "name", "desc", "labels", "due", "dueComplete",
                   "idBoard", "checklists", "attachments"}
BOARD_CONTRACT = {"id", "name", "closed"}
LIST_CONTRACT = {"id", "name", "pos"}
LABEL_CONTRACT = {"id", "name", "color"}
COMMENT_CONTRACT = {"id", "date", "data", "memberCreator"}


def _keys(dicts):
    assert dicts, "expected at least one dict to compare"
    return set(dicts[0].keys())


def test_get_boards_conformance(trello, local):
    backend, bid, _, _ = local
    tk = _keys(trello.get_boards())
    lk = _keys(backend.get_boards())
    assert BOARD_CONTRACT <= tk
    assert BOARD_CONTRACT <= lk


def test_get_lists_conformance(trello, local):
    backend, bid, _, _ = local
    tk = _keys(trello.get_lists(bid))
    lk = _keys(backend.get_lists(bid))
    assert LIST_CONTRACT <= tk
    assert LIST_CONTRACT <= lk
    assert tk <= lk        # local (adds closed/sort) is a superset


def test_get_labels_conformance(trello, local):
    backend, bid, _, _ = local
    tk = _keys(trello.get_labels(bid))
    lk = _keys(backend.get_labels(bid))
    assert LABEL_CONTRACT <= tk
    assert LABEL_CONTRACT <= lk


def test_get_card_conformance(trello, local):
    backend, bid, _, cid = local
    tk = _keys([trello.get_card(cid)])
    lk = _keys([backend.get_card(cid)])
    assert DETAIL_CONTRACT <= tk
    assert DETAIL_CONTRACT <= lk
    assert tk <= lk        # local get_card supplies every field Trello does


def test_get_board_cards_conformance(trello, local):
    backend, bid, _, _ = local
    tk = _keys(trello.get_board_cards(bid))
    lk = _keys(backend.get_board_cards(bid))
    assert CARD_CONTRACT <= tk
    assert CARD_CONTRACT <= lk
    assert tk <= lk


def test_get_cards_in_list_conformance(trello, local):
    backend, bid, todo, _ = local
    tk = _keys(trello.get_cards_in_list(todo))
    lk = _keys(backend.get_cards_in_list(todo))
    # get_cards_in_list contract (Trello omits idList here, so exclude it)
    contract = CARD_CONTRACT - {"idList"}
    assert contract <= tk
    assert contract <= lk
    assert tk <= lk        # local additionally carries idList


def test_get_my_cards_conformance(trello, local):
    backend, bid, _, _ = local
    tk = _keys(trello.get_my_cards())
    lk = _keys(backend.get_my_cards())
    # Trello's members/me/cards read doesn't request `pos` (the my-cards view /
    # _card_row never uses it), so pos is not part of this op's shared contract.
    contract = (CARD_CONTRACT - {"pos"}) | {"idBoard"}
    assert contract <= tk
    assert contract <= lk
    assert tk <= lk


def test_get_comments_conformance(trello, local):
    backend, bid, _, cid = local
    tk = _keys(trello.get_comments(cid))
    lk = _keys(backend.get_comments(cid))
    assert COMMENT_CONTRACT <= tk
    assert COMMENT_CONTRACT <= lk
