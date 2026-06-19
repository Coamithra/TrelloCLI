"""Thin HTTP client for the Trello REST API."""

from __future__ import annotations

import os
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


def create_board(name: str, desc: str | None = None,
                 default_lists: bool = True) -> dict:
    return _post(
        "/boards/",
        name=name,
        desc=desc,
        defaultLists="true" if default_lists else "false",
    )


# --- Lists ---

def get_lists(board_id: str) -> list[dict]:
    return _get(f"/boards/{board_id}/lists", fields="id,name,pos", filter="open")


def create_list(board_id: str, name: str, pos: str | None = None) -> dict:
    return _post("/lists", name=name, idBoard=board_id, pos=pos)


def archive_list(list_id: str) -> dict:
    return _put(f"/lists/{list_id}/closed", value="true")


def update_list(list_id: str, **fields: Any) -> dict:
    return _put(f"/lists/{list_id}", **fields)


def rename_list(list_id: str, name: str) -> dict:
    return update_list(list_id, name=name)


# --- Cards ---

def get_board_cards(board_id: str, card_filter: str = "visible") -> list[dict]:
    return _get(
        f"/boards/{board_id}/cards",
        fields="id,name,shortUrl,labels,due,idList,idMembers,shortId,dateLastActivity",
        filter=card_filter,
    )


def get_cards_in_list(list_id: str, with_latest_comment: bool = False) -> list[dict]:
    kw: dict[str, Any] = {
        "fields": "id,name,shortUrl,labels,due,idMembers,shortId,dateLastActivity,pos",
    }
    if with_latest_comment:
        kw["actions"] = "commentCard"
        kw["actions_limit"] = "1"
    return _get(f"/lists/{list_id}/cards", **kw)


def get_card(card_id: str) -> dict:
    return _get(
        f"/cards/{card_id}",
        fields="id,name,desc,shortUrl,labels,due,dueComplete,idList,idMembers,shortId,dateLastActivity",
        checklists="all",
        attachments="true",
        attachment_fields="id,name,url,mimeType,bytes,isUpload",
    )


def get_my_cards() -> list[dict]:
    return _get(
        "/members/me/cards",
        fields="id,name,shortUrl,labels,due,idBoard,idList,shortId,dateLastActivity",
    )


def create_card(list_id: str, name: str, desc: str | None = None,
                due: str | None = None, labels: list[str] | None = None,
                pos: str = "top") -> dict:
    kw = dict(idList=list_id, name=name, pos=pos)
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


def unarchive_card(card_id: str) -> dict:
    return _put(f"/cards/{card_id}", closed="false")


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


def create_label(board_id: str, name: str, color: str | None = None) -> dict:
    kw: dict[str, Any] = {"name": name, "idBoard": board_id}
    if color:
        kw["color"] = color
    return _post("/labels", **kw)


def update_label(label_id: str, **fields: Any) -> dict:
    return _put(f"/labels/{label_id}", **fields)


def delete_label(label_id: str) -> None:
    _delete(f"/labels/{label_id}")


def add_label_to_card(card_id: str, label_id: str) -> None:
    _post(f"/cards/{card_id}/idLabels", value=label_id)


def remove_label_from_card(card_id: str, label_id: str) -> None:
    r = httpx.delete(
        f"{BASE}/cards/{card_id}/idLabels/{label_id}",
        params=_params(), timeout=15,
    )
    r.raise_for_status()


# --- Members ---

def get_members(board_id: str) -> list[dict]:
    return _get(f"/boards/{board_id}/members", fields="id,fullName,username")


# --- Activity ---

def get_activity(board_id: str, limit: int = 10) -> list[dict]:
    return _get(f"/boards/{board_id}/actions", limit=str(limit))


def get_actions_since(board_id: str, since: str,
                      action_types: str | None = None,
                      page: int = 1000) -> list[dict]:
    """All board actions since `since` (ISO date or Trello id), newest first.

    Trello caps a single response at 1000 actions, so we page backwards with
    `before` (the oldest id seen) until a short page signals we've drained it.
    `action_types` is an optional comma-separated Trello action filter
    (e.g. "commentCard,updateCard")."""
    out: list[dict] = []
    before: str | None = None
    while True:
        kw: dict[str, Any] = {"limit": str(page), "since": since}
        if action_types:
            kw["filter"] = action_types
        if before:
            kw["before"] = before
        batch = _get(f"/boards/{board_id}/actions", **kw)
        out.extend(batch)
        if len(batch) < page:
            break
        before = batch[-1]["id"]
    return out


# --- Checklists ---

def get_checklists(card_id: str) -> list[dict]:
    return _get(f"/cards/{card_id}/checklists")


def create_checklist(card_id: str, name: str) -> dict:
    return _post(f"/cards/{card_id}/checklists", name=name)


def delete_checklist(checklist_id: str) -> None:
    _delete(f"/checklists/{checklist_id}")


def rename_checklist(checklist_id: str, name: str) -> dict:
    return _put(f"/checklists/{checklist_id}", name=name)


def add_checkitem(checklist_id: str, name: str) -> dict:
    return _post(f"/checklists/{checklist_id}/checkItems", name=name)


def delete_checkitem(checklist_id: str, item_id: str) -> None:
    _delete(f"/checklists/{checklist_id}/checkItems/{item_id}")


def update_checkitem(card_id: str, item_id: str, **fields: Any) -> dict:
    return _put(f"/cards/{card_id}/checkItem/{item_id}", **fields)


# --- Attachments ---

def get_attachments(card_id: str) -> list[dict]:
    return _get(
        f"/cards/{card_id}/attachments",
        fields="id,name,url,mimeType,bytes,date,isUpload",
    )


def add_attachment_url(card_id: str, url: str, name: str | None = None) -> dict:
    return _post(f"/cards/{card_id}/attachments", url=url, name=name)


def add_attachment_file(card_id: str, file_path: str, name: str | None = None) -> dict:
    key, token = get_auth()
    params = {"key": key, "token": token}
    if name:
        params["name"] = name
    with open(file_path, "rb") as fh:
        files = {"file": (os.path.basename(file_path), fh)}
        r = httpx.post(
            f"{BASE}/cards/{card_id}/attachments",
            params=params, files=files, timeout=60,
        )
    r.raise_for_status()
    return r.json()


def delete_attachment(card_id: str, attachment_id: str) -> None:
    r = httpx.delete(
        f"{BASE}/cards/{card_id}/attachments/{attachment_id}",
        params=_params(), timeout=15,
    )
    r.raise_for_status()


def download_attachment(url: str, dest: str, authed: bool = True) -> None:
    """Stream an attachment to `dest`. Trello-hosted uploads require the OAuth
    header (`authed=True`); external URL attachments are fetched without it."""
    headers = {}
    if authed:
        key, token = get_auth()
        headers["Authorization"] = (
            f'OAuth oauth_consumer_key="{key}", oauth_token="{token}"'
        )
    with httpx.stream(
        "GET", url, headers=headers, timeout=60, follow_redirects=True,
    ) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_bytes():
                fh.write(chunk)
