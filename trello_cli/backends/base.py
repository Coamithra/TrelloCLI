"""The Backend interface — the contract every data source implements.

The CLI talks only to this ABC (via `get_backend()`); commands and `fmt.py`
never touch a concrete backend or its transport. Both the Trello REST client
and the planned local file store return the same Trello-shaped dicts, so all
formatting and command logic stay backend-agnostic. The method set is exactly
the operations `main.py` invokes — nothing more. See DESIGN.md.

A backend may add *transient* keys to a returned dict that aren't part of the
stored shape, as long as consumers treat them as optional. The only one today:
`local`'s `update_card` / `update_list` set `rebalanced: True` when a `pos`
update respread the list, so the web client reloads the now-stale siblings;
it is never persisted, the CLI ignores it, and `trello` never sets it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Backend(ABC):
    # --- Boards ---

    @abstractmethod
    def get_boards(self) -> list[dict]: ...

    @abstractmethod
    def get_board(self, board_id: str) -> dict: ...

    @abstractmethod
    def create_board(self, name: str, desc: str | None = None,
                     default_lists: bool = True) -> dict: ...

    # --- Lists ---

    @abstractmethod
    def get_lists(self, board_id: str) -> list[dict]: ...

    @abstractmethod
    def create_list(self, board_id: str, name: str, pos: str | None = None) -> dict: ...

    @abstractmethod
    def archive_list(self, list_id: str) -> dict: ...

    @abstractmethod
    def update_list(self, list_id: str, **fields: Any) -> dict: ...

    @abstractmethod
    def rename_list(self, list_id: str, name: str) -> dict: ...

    # --- Cards ---

    @abstractmethod
    def get_board_cards(self, board_id: str, card_filter: str = "visible") -> list[dict]: ...

    @abstractmethod
    def get_cards_in_list(self, list_id: str,
                          with_latest_comment: bool = False) -> list[dict]: ...

    @abstractmethod
    def get_card(self, card_id: str) -> dict: ...

    @abstractmethod
    def get_my_cards(self) -> list[dict]: ...

    @abstractmethod
    def create_card(self, list_id: str, name: str, desc: str | None = None,
                    due: str | None = None, labels: list[str] | None = None,
                    pos: str = "top") -> dict: ...

    @abstractmethod
    def move_card(self, card_id: str, list_id: str) -> dict: ...

    @abstractmethod
    def archive_card(self, card_id: str) -> dict: ...

    @abstractmethod
    def unarchive_card(self, card_id: str) -> dict: ...

    @abstractmethod
    def update_card(self, card_id: str, **fields: Any) -> dict: ...

    @abstractmethod
    def grab_top_card(self, source_list_id: str,
                      dest_list_id: str) -> dict | None:
        """Atomically claim the top open card of `source_list_id`, move it to
        `dest_list_id`, and return it — or `None` if nothing could be claimed
        (an empty source list, or — on Trello — every candidate lost to a
        concurrent claimer). "Atomic" so many callers racing the same list each
        get a *distinct* card. `LocalBackend` does this for real under the store
        lock; `TrelloBackend`, with no atomic primitive, fakes it with the
        claim-comment handshake (see CONTRIBUTING.md)."""
        ...

    # --- Comments ---

    @abstractmethod
    def add_comment(self, card_id: str, text: str) -> dict: ...

    @abstractmethod
    def get_comments(self, card_id: str, limit: int = 10) -> list[dict]: ...

    @abstractmethod
    def update_comment(self, action_id: str, text: str) -> dict: ...

    @abstractmethod
    def delete_comment(self, action_id: str) -> None: ...

    # --- Labels ---

    @abstractmethod
    def get_labels(self, board_id: str) -> list[dict]: ...

    @abstractmethod
    def create_label(self, board_id: str, name: str, color: str | None = None) -> dict: ...

    @abstractmethod
    def update_label(self, label_id: str, **fields: Any) -> dict: ...

    @abstractmethod
    def delete_label(self, label_id: str) -> None: ...

    @abstractmethod
    def add_label_to_card(self, card_id: str, label_id: str) -> None: ...

    @abstractmethod
    def remove_label_from_card(self, card_id: str, label_id: str) -> None: ...

    # --- Members ---

    @abstractmethod
    def get_members(self, board_id: str) -> list[dict]: ...

    # --- Activity ---

    @abstractmethod
    def get_activity(self, board_id: str, limit: int = 10) -> list[dict]: ...

    @abstractmethod
    def get_actions_since(self, board_id: str, since: str,
                          action_types: str | None = None,
                          page: int = 1000) -> list[dict]: ...

    # --- Checklists ---

    @abstractmethod
    def get_checklists(self, card_id: str) -> list[dict]: ...

    @abstractmethod
    def create_checklist(self, card_id: str, name: str) -> dict: ...

    @abstractmethod
    def delete_checklist(self, checklist_id: str) -> None: ...

    @abstractmethod
    def rename_checklist(self, checklist_id: str, name: str) -> dict: ...

    @abstractmethod
    def add_checkitem(self, checklist_id: str, name: str) -> dict: ...

    @abstractmethod
    def delete_checkitem(self, checklist_id: str, item_id: str) -> None: ...

    @abstractmethod
    def update_checkitem(self, card_id: str, item_id: str, **fields: Any) -> dict: ...

    # --- Attachments ---

    @abstractmethod
    def get_attachments(self, card_id: str) -> list[dict]: ...

    @abstractmethod
    def add_attachment_url(self, card_id: str, url: str, name: str | None = None) -> dict: ...

    @abstractmethod
    def add_attachment_file(self, card_id: str, file_path: str,
                            name: str | None = None) -> dict: ...

    @abstractmethod
    def delete_attachment(self, card_id: str, attachment_id: str) -> None: ...

    @abstractmethod
    def download_attachment(self, url: str, dest: str, authed: bool = True) -> None: ...
