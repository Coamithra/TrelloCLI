"""LocalBackend — a self-hosted file store behind the Backend contract.

Phase 1 (core): boards, lists, and cards CRUD + move / pos / archive / unarchive
/ rename / desc / due, returning the same Trello-shaped dicts as TrelloBackend so
every command and `fmt.py` work untouched against `--backend local`. Labels,
checklists, comments, attachments, members, `card mine`, and reading the activity
log arrive in Phase 2 — those ops raise a clean error here for now (the class
stays concrete so it instantiates). See DESIGN.md.
"""

from __future__ import annotations

from typing import Any

from .base import Backend
from .store import (
    POS_STEP,
    LocalStore,
    atomic_write_json,
    new_id,
    now_iso,
    read_json,
    resolve_pos,
)

DEFAULT_LISTS = ("To Do", "Doing", "Done")


def _as_bool(value: Any) -> bool:
    """Coerce a closed-flag (bool or Trello's 'true'/'false' string) to bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


class LocalBackend(Backend):
    def __init__(self, root: str) -> None:
        self.store = LocalStore(root)

    # ── internal helpers ────────────────────────────────────────────

    def _unsupported(self, op: str) -> Any:
        raise SystemExit(
            f"The local backend doesn't support '{op}' yet (coming in a later phase). "
            "Run it against Trello with --backend trello."
        )

    def _load_board(self, board_id: str) -> dict:
        board = read_json(self.store.board_file(board_id))
        if board is None:
            raise SystemExit(f"Board not found: {board_id}")
        return board

    def _load_lists(self, board_id: str) -> list[dict]:
        return read_json(self.store.lists_file(board_id), []) or []

    def _save_lists(self, board_id: str, lists: list[dict]) -> None:
        atomic_write_json(self.store.lists_file(board_id), lists)

    def _locate_list(self, list_id: str) -> tuple[str, dict]:
        for bid in self.store.board_ids():
            for lst in self._load_lists(bid):
                if lst["id"] == list_id:
                    return bid, lst
        raise SystemExit(f"List not found: {list_id}")

    def _locate_card(self, card_id: str) -> str:
        for bid in self.store.board_ids():
            if self.store.card_file(bid, card_id).exists():
                return bid
        raise SystemExit(f"Card not found: {card_id}")

    def _load_card(self, card_id: str) -> tuple[str, dict]:
        bid = self._locate_card(card_id)
        return bid, read_json(self.store.card_file(bid, card_id))

    def _save_card(self, board_id: str, card: dict) -> None:
        atomic_write_json(self.store.card_file(board_id, card["id"]), card)

    def _list_positions(self, board_id: str, list_id: str, exclude: str | None = None) -> list[float]:
        """Open cards' positions in a list (optionally excluding one card)."""
        return [
            c["pos"] for c in self.store.cards(board_id)
            if c.get("idList") == list_id and not c.get("closed") and c["id"] != exclude
        ]

    def _log(self, board_id: str, action_type: str, data: dict) -> None:
        self.store.append_activity(board_id, {
            "id": new_id(),
            "type": action_type,
            "date": now_iso(),
            "data": data,
        })

    # ── Boards ───────────────────────────────────────────────────────

    def get_boards(self) -> list[dict]:
        out = []
        for bid in self.store.board_ids():
            b = read_json(self.store.board_file(bid))
            if b and not b.get("closed"):
                out.append({
                    "id": b["id"],
                    "name": b["name"],
                    "shortUrl": b.get("shortUrl", ""),
                    "closed": b.get("closed", False),
                })
        return out

    def get_board(self, board_id: str) -> dict:
        b = self._load_board(board_id)
        return {
            "id": b["id"],
            "name": b["name"],
            "shortUrl": b.get("shortUrl", ""),
            "desc": b.get("desc", ""),
        }

    def create_board(self, name: str, desc: str | None = None,
                     default_lists: bool = True) -> dict:
        bid = new_id()
        board = {"id": bid, "name": name, "desc": desc or "", "closed": False, "shortUrl": ""}
        atomic_write_json(self.store.board_file(bid), board)
        lists = []
        if default_lists:
            for i, lname in enumerate(DEFAULT_LISTS, start=1):
                lists.append({"id": new_id(), "name": lname, "pos": POS_STEP * i, "closed": False})
        self._save_lists(bid, lists)
        self._log(bid, "createBoard", {"board": {"id": bid, "name": name}})
        return {"id": bid, "name": name, "shortUrl": "", "desc": desc or ""}

    # ── Lists ────────────────────────────────────────────────────────

    def get_lists(self, board_id: str) -> list[dict]:
        self._load_board(board_id)  # 404 if the board is missing
        lists = [l for l in self._load_lists(board_id) if not l.get("closed")]
        lists.sort(key=lambda l: l.get("pos", 0))
        return lists

    def create_list(self, board_id: str, name: str, pos: str | None = None) -> dict:
        self._load_board(board_id)
        lists = self._load_lists(board_id)
        existing = [l["pos"] for l in lists if not l.get("closed")]
        new = {
            "id": new_id(),
            "name": name,
            "pos": resolve_pos(existing, pos if pos is not None else "top"),
            "closed": False,
        }
        lists.append(new)
        self._save_lists(board_id, lists)
        self._log(board_id, "createList", {"list": {"id": new["id"], "name": name}})
        return new

    def archive_list(self, list_id: str) -> dict:
        return self.update_list(list_id, closed=True)

    def update_list(self, list_id: str, **fields: Any) -> dict:
        board_id, _ = self._locate_list(list_id)
        lists = self._load_lists(board_id)
        target = next(l for l in lists if l["id"] == list_id)
        if "name" in fields:
            target["name"] = fields["name"]
        if "pos" in fields:
            existing = [l["pos"] for l in lists if l["id"] != list_id and not l.get("closed")]
            target["pos"] = resolve_pos(existing, fields["pos"])
        if "closed" in fields:
            target["closed"] = _as_bool(fields["closed"])
        self._save_lists(board_id, lists)
        self._log(board_id, "updateList", {"list": {"id": list_id, "name": target["name"]}})
        return target

    def rename_list(self, list_id: str, name: str) -> dict:
        return self.update_list(list_id, name=name)

    # ── Cards ────────────────────────────────────────────────────────

    def _new_card(self, board_id: str, list_id: str, name: str, desc: str | None,
                  due: str | None, pos: Any) -> dict:
        existing = self._list_positions(board_id, list_id)
        return {
            "id": new_id(),
            "idBoard": board_id,
            "idList": list_id,
            "name": name,
            "desc": desc or "",
            "pos": resolve_pos(existing, pos),
            "due": due,
            "dueComplete": False,
            "labels": [],
            "idMembers": [],
            "checklists": [],
            "attachments": [],
            "comments": [],
            "closed": False,
            "shortUrl": "",
            "shortLink": "",
            "idShort": len(self.store.cards(board_id)) + 1,
            "dateLastActivity": now_iso(),
        }

    def get_board_cards(self, board_id: str, card_filter: str = "visible") -> list[dict]:
        self._load_board(board_id)
        cards = self.store.cards(board_id)
        if card_filter == "visible":
            cards = [c for c in cards if not c.get("closed")]
        elif card_filter == "closed":
            cards = [c for c in cards if c.get("closed")]
        cards.sort(key=lambda c: c.get("pos", 0))
        return cards

    def get_cards_in_list(self, list_id: str,
                          with_latest_comment: bool = False) -> list[dict]:
        board_id, _ = self._locate_list(list_id)
        cards = [
            c for c in self.store.cards(board_id)
            if c.get("idList") == list_id and not c.get("closed")
        ]
        cards.sort(key=lambda c: c.get("pos", 0))
        return cards  # comments (and thus --with-comment actions) are Phase 2

    def get_card(self, card_id: str) -> dict:
        _, card = self._load_card(card_id)
        return card

    def get_my_cards(self) -> list[dict]:
        return self._unsupported("card mine")

    def create_card(self, list_id: str, name: str, desc: str | None = None,
                    due: str | None = None, labels: list[str] | None = None,
                    pos: str = "top") -> dict:
        board_id, _ = self._locate_list(list_id)
        card = self._new_card(board_id, list_id, name, desc, due, pos)
        self._save_card(board_id, card)
        self._log(board_id, "createCard",
                  {"card": {"id": card["id"], "name": name}, "list": {"id": list_id}})
        return card

    def move_card(self, card_id: str, list_id: str) -> dict:
        return self.update_card(card_id, idList=list_id)

    def archive_card(self, card_id: str) -> dict:
        return self.update_card(card_id, closed=True)

    def unarchive_card(self, card_id: str) -> dict:
        return self.update_card(card_id, closed=False)

    def update_card(self, card_id: str, **fields: Any) -> dict:
        board_id, card = self._load_card(card_id)
        if "name" in fields:
            card["name"] = fields["name"]
        if "desc" in fields:
            card["desc"] = fields["desc"] or ""
        if "due" in fields:
            card["due"] = fields["due"] or None  # "" clears the due date
        if "idList" in fields:
            card["idList"] = fields["idList"]
            # Land at the bottom of the destination list; reorder with `card pos`.
            existing = self._list_positions(board_id, card["idList"], exclude=card_id)
            card["pos"] = resolve_pos(existing, "bottom")
        if "pos" in fields:
            existing = self._list_positions(board_id, card["idList"], exclude=card_id)
            card["pos"] = resolve_pos(existing, fields["pos"])
        if "closed" in fields:
            card["closed"] = _as_bool(fields["closed"])
        card["dateLastActivity"] = now_iso()
        self._save_card(board_id, card)
        self._log(board_id, "updateCard", {"card": {"id": card_id, "name": card["name"]}})
        return card

    # ── Comments (Phase 2; get_comments returns none so `card show` renders) ──

    def add_comment(self, card_id: str, text: str) -> dict:
        return self._unsupported("comment add")

    def get_comments(self, card_id: str, limit: int = 10) -> list[dict]:
        return []

    def update_comment(self, action_id: str, text: str) -> dict:
        return self._unsupported("comment edit")

    def delete_comment(self, action_id: str) -> None:
        self._unsupported("comment delete")

    # ── Labels (Phase 2) ─────────────────────────────────────────────

    def get_labels(self, board_id: str) -> list[dict]:
        return self._unsupported("labels")

    def create_label(self, board_id: str, name: str, color: str | None = None) -> dict:
        return self._unsupported("label add")

    def update_label(self, label_id: str, **fields: Any) -> dict:
        return self._unsupported("label edit")

    def delete_label(self, label_id: str) -> None:
        self._unsupported("label delete")

    def add_label_to_card(self, card_id: str, label_id: str) -> None:
        self._unsupported("label set")

    def remove_label_from_card(self, card_id: str, label_id: str) -> None:
        self._unsupported("label unset")

    # ── Members (Phase 2) ────────────────────────────────────────────

    def get_members(self, board_id: str) -> list[dict]:
        return self._unsupported("members")

    # ── Activity (the log is written on every mutation; reading is Phase 2) ──

    def get_activity(self, board_id: str, limit: int = 10) -> list[dict]:
        return self._unsupported("activity")

    def get_actions_since(self, board_id: str, since: str,
                          action_types: str | None = None,
                          page: int = 1000) -> list[dict]:
        return self._unsupported("updates")

    # ── Checklists (Phase 2) ─────────────────────────────────────────

    def get_checklists(self, card_id: str) -> list[dict]:
        return self._unsupported("checklist ls")

    def create_checklist(self, card_id: str, name: str) -> dict:
        return self._unsupported("checklist add")

    def delete_checklist(self, checklist_id: str) -> None:
        self._unsupported("checklist delete")

    def rename_checklist(self, checklist_id: str, name: str) -> dict:
        return self._unsupported("checklist rename")

    def add_checkitem(self, checklist_id: str, name: str) -> dict:
        return self._unsupported("checklist item add")

    def delete_checkitem(self, checklist_id: str, item_id: str) -> None:
        self._unsupported("checklist item delete")

    def update_checkitem(self, card_id: str, item_id: str, **fields: Any) -> dict:
        return self._unsupported("checklist item")

    # ── Attachments (Phase 2) ────────────────────────────────────────

    def get_attachments(self, card_id: str) -> list[dict]:
        return self._unsupported("attachment ls")

    def add_attachment_url(self, card_id: str, url: str, name: str | None = None) -> dict:
        return self._unsupported("attachment add")

    def add_attachment_file(self, card_id: str, file_path: str,
                            name: str | None = None) -> dict:
        return self._unsupported("attachment add")

    def delete_attachment(self, card_id: str, attachment_id: str) -> None:
        self._unsupported("attachment rm")

    def download_attachment(self, url: str, dest: str, authed: bool = True) -> None:
        self._unsupported("attachment download")
