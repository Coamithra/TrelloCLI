"""Thin HTTP client for the Trello REST API."""

from __future__ import annotations

from typing import Any

import httpx

from .config import get_auth

BASE = "https://api.trello.com/1"


def _params(**kw: Any) -> dict[str, Any]:
    key, token = get_auth()
    return {"key": key, "token": token, **{k: v for k, v in kw.items() if v is not None}}


def _get(path: str, **kw: Any) -> Any:
    r = httpx.get(f"{BASE}{path}", params=_params(**kw), timeout=15)
    r.raise_for_status()
    return r.json()


def _post(path: str, **kw: Any) -> Any:
    r = httpx.post(f"{BASE}{path}", params=_params(**kw), timeout=15)
    r.raise_for_status()
    return r.json()


def _put(path: str, **kw: Any) -> Any:
    r = httpx.put(f"{BASE}{path}", params=_params(**kw), timeout=15)
    r.raise_for_status()
    return r.json()


def _delete(path: str) -> None:
    r = httpx.delete(f"{BASE}{path}", params=_params(), timeout=15)
    r.raise_for_status()


# --- Boards ---

def get_boards() -> list[dict]:
    return _get("/members/me/boards", fields="id,name,shortUrl,closed", filter="open")


def get_board(board_id: str) -> dict:
    return _get(f"/boards/{board_id}", fields="id,name,shortUrl,desc")


# --- Lists ---

def get_lists(board_id: str) -> list[dict]:
    return _get(f"/boards/{board_id}/lists", fields="id,name", filter="open")


def create_list(board_id: str, name: str) -> dict:
    return _post("/lists", name=name, idBoard=board_id)


def archive_list(list_id: str) -> dict:
    return _put(f"/lists/{list_id}/closed", value="true")


def rename_list(list_id: str, name: str) -> dict:
    return _put(f"/lists/{list_id}", name=name)


# --- Cards ---

def get_board_cards(board_id: str) -> list[dict]:
    return _get(
        f"/boards/{board_id}/cards",
        fields="id,name,shortUrl,labels,due,idList,idMembers,shortId",
    )


def get_cards_in_list(list_id: str) -> list[dict]:
    return _get(
        f"/lists/{list_id}/cards",
        fields="id,name,shortUrl,labels,due,idMembers,shortId",
    )


def get_card(card_id: str) -> dict:
    return _get(
        f"/cards/{card_id}",
        fields="id,name,desc,shortUrl,labels,due,dueComplete,idList,idMembers,shortId",
        checklists="all",
    )


def get_my_cards() -> list[dict]:
    return _get(
        "/members/me/cards",
        fields="id,name,shortUrl,labels,due,idBoard,idList,shortId",
    )


def create_card(list_id: str, name: str, desc: str | None = None,
                due: str | None = None, labels: list[str] | None = None) -> dict:
    kw = dict(idList=list_id, name=name)
    if desc:
        kw["desc"] = desc
    if due:
        kw["due"] = due
    if labels:
        kw["idLabels"] = ",".join(labels)
    return _post("/cards", **kw)


def move_card(card_id: str, list_id: str) -> dict:
    return _put(f"/cards/{card_id}", idList=list_id)


def archive_card(card_id: str) -> dict:
    return _put(f"/cards/{card_id}", closed="true")


def update_card(card_id: str, **fields) -> dict:
    return _put(f"/cards/{card_id}", **fields)


# --- Comments ---

def add_comment(card_id: str, text: str) -> dict:
    return _post(f"/cards/{card_id}/actions/comments", text=text)


def get_comments(card_id: str, limit: int = 10) -> list[dict]:
    return _get(
        f"/cards/{card_id}/actions",
        filter="commentCard",
        limit=str(limit),
    )


def update_comment(action_id: str, text: str) -> dict:
    return _put(f"/actions/{action_id}", text=text)


def delete_comment(action_id: str) -> None:
    _delete(f"/actions/{action_id}")


# --- Labels ---

def get_labels(board_id: str) -> list[dict]:
    return _get(f"/boards/{board_id}/labels", fields="id,name,color")


# --- Members ---

def get_members(board_id: str) -> list[dict]:
    return _get(f"/boards/{board_id}/members", fields="id,fullName,username")


# --- Activity ---

def get_activity(board_id: str, limit: int = 10) -> list[dict]:
    return _get(f"/boards/{board_id}/actions", limit=str(limit))


# --- Checklists ---

def get_checklists(card_id: str) -> list[dict]:
    return _get(f"/cards/{card_id}/checklists")
