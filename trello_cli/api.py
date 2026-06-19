"""Facade over the active backend.

Historically this module was the Trello REST client; that code now lives in
`backends/trello.py`. `main.py` still calls `api.<op>(...)`, and each call here
forwards to `get_backend().<op>(...)` — so the ~60 call sites stay untouched
while the underlying data source becomes pluggable. See DESIGN.md.
"""

from __future__ import annotations

from typing import Any

from .backends import get_backend


# --- Boards ---

def get_boards() -> list[dict]:
    return get_backend().get_boards()


def get_board(board_id: str) -> dict:
    return get_backend().get_board(board_id)


def create_board(name: str, desc: str | None = None,
                 default_lists: bool = True) -> dict:
    return get_backend().create_board(name, desc=desc, default_lists=default_lists)


# --- Lists ---

def get_lists(board_id: str) -> list[dict]:
    return get_backend().get_lists(board_id)


def create_list(board_id: str, name: str, pos: str | None = None) -> dict:
    return get_backend().create_list(board_id, name, pos=pos)


def archive_list(list_id: str) -> dict:
    return get_backend().archive_list(list_id)


def update_list(list_id: str, **fields: Any) -> dict:
    return get_backend().update_list(list_id, **fields)


def rename_list(list_id: str, name: str) -> dict:
    return get_backend().rename_list(list_id, name)


# --- Cards ---

def get_board_cards(board_id: str, card_filter: str = "visible") -> list[dict]:
    return get_backend().get_board_cards(board_id, card_filter=card_filter)


def get_cards_in_list(list_id: str, with_latest_comment: bool = False) -> list[dict]:
    return get_backend().get_cards_in_list(list_id, with_latest_comment=with_latest_comment)


def get_card(card_id: str) -> dict:
    return get_backend().get_card(card_id)


def get_my_cards() -> list[dict]:
    return get_backend().get_my_cards()


def create_card(list_id: str, name: str, desc: str | None = None,
                due: str | None = None, labels: list[str] | None = None,
                pos: str = "top") -> dict:
    return get_backend().create_card(list_id, name, desc=desc, due=due,
                                     labels=labels, pos=pos)


def move_card(card_id: str, list_id: str) -> dict:
    return get_backend().move_card(card_id, list_id)


def archive_card(card_id: str) -> dict:
    return get_backend().archive_card(card_id)


def unarchive_card(card_id: str) -> dict:
    return get_backend().unarchive_card(card_id)


def update_card(card_id: str, **fields: Any) -> dict:
    return get_backend().update_card(card_id, **fields)


# --- Comments ---

def add_comment(card_id: str, text: str) -> dict:
    return get_backend().add_comment(card_id, text)


def get_comments(card_id: str, limit: int = 10) -> list[dict]:
    return get_backend().get_comments(card_id, limit=limit)


def update_comment(action_id: str, text: str) -> dict:
    return get_backend().update_comment(action_id, text)


def delete_comment(action_id: str) -> None:
    get_backend().delete_comment(action_id)


# --- Labels ---

def get_labels(board_id: str) -> list[dict]:
    return get_backend().get_labels(board_id)


def create_label(board_id: str, name: str, color: str | None = None) -> dict:
    return get_backend().create_label(board_id, name, color=color)


def update_label(label_id: str, **fields: Any) -> dict:
    return get_backend().update_label(label_id, **fields)


def delete_label(label_id: str) -> None:
    get_backend().delete_label(label_id)


def add_label_to_card(card_id: str, label_id: str) -> None:
    get_backend().add_label_to_card(card_id, label_id)


def remove_label_from_card(card_id: str, label_id: str) -> None:
    get_backend().remove_label_from_card(card_id, label_id)


# --- Members ---

def get_members(board_id: str) -> list[dict]:
    return get_backend().get_members(board_id)


# --- Activity ---

def get_activity(board_id: str, limit: int = 10) -> list[dict]:
    return get_backend().get_activity(board_id, limit=limit)


def get_actions_since(board_id: str, since: str,
                      action_types: str | None = None,
                      page: int = 1000) -> list[dict]:
    return get_backend().get_actions_since(board_id, since,
                                           action_types=action_types, page=page)


# --- Checklists ---

def get_checklists(card_id: str) -> list[dict]:
    return get_backend().get_checklists(card_id)


def create_checklist(card_id: str, name: str) -> dict:
    return get_backend().create_checklist(card_id, name)


def delete_checklist(checklist_id: str) -> None:
    get_backend().delete_checklist(checklist_id)


def rename_checklist(checklist_id: str, name: str) -> dict:
    return get_backend().rename_checklist(checklist_id, name)


def add_checkitem(checklist_id: str, name: str) -> dict:
    return get_backend().add_checkitem(checklist_id, name)


def delete_checkitem(checklist_id: str, item_id: str) -> None:
    get_backend().delete_checkitem(checklist_id, item_id)


def update_checkitem(card_id: str, item_id: str, **fields: Any) -> dict:
    return get_backend().update_checkitem(card_id, item_id, **fields)


# --- Attachments ---

def get_attachments(card_id: str) -> list[dict]:
    return get_backend().get_attachments(card_id)


def add_attachment_url(card_id: str, url: str, name: str | None = None) -> dict:
    return get_backend().add_attachment_url(card_id, url, name=name)


def add_attachment_file(card_id: str, file_path: str, name: str | None = None) -> dict:
    return get_backend().add_attachment_file(card_id, file_path, name=name)


def delete_attachment(card_id: str, attachment_id: str) -> None:
    get_backend().delete_attachment(card_id, attachment_id)


def download_attachment(url: str, dest: str, authed: bool = True) -> None:
    get_backend().download_attachment(url, dest, authed=authed)
