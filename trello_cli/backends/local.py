"""LocalBackend — a self-hosted file store behind the Backend contract.

Boards, lists, and cards CRUD + move / pos / archive / unarchive / rename / desc
/ due (Phase 1), plus labels, checklists, comments, attachments, members,
`card mine`, and activity / updates reads (Phase 2) — returning the same
Trello-shaped dicts as TrelloBackend so every command and `fmt.py` work untouched
against `--backend local`. See DESIGN.md.

Label storage follows Trello's model: a card keeps `idLabels` (ids); the full
label dicts are resolved from `labels.json` at read time, so `label edit` /
`label delete` reflect on cards without rewriting every card. Comments,
checklists, and attachments live inline in the card JSON (one file per card keeps
Dropbox conflict scope tiny). The single-user model derives one local member from
the OS username; `card mine` therefore returns every open card.
"""

from __future__ import annotations

import getpass
import hashlib
import mimetypes
import shutil
from datetime import datetime
from pathlib import Path
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
        user = self._local_user()
        self.store.append_activity(board_id, {
            "id": new_id(),
            "type": action_type,
            "date": now_iso(),
            "idMemberCreator": user["id"],
            "memberCreator": user,  # stamped at write time so synced logs attribute correctly
            "data": data,
        })

    def _local_user(self) -> dict:
        """The single local member, derived from the OS username. The id is a
        stable 24-hex hash so it looks like (and resolves like) a Trello id."""
        name = getpass.getuser() or "local"
        uid = hashlib.sha1(f"trello-cli-local:{name}".encode()).hexdigest()[:24]
        return {"id": uid, "username": name, "fullName": name}

    def _load_labels(self, board_id: str) -> list[dict]:
        return read_json(self.store.labels_file(board_id), []) or []

    def _save_labels(self, board_id: str, labels: list[dict]) -> None:
        atomic_write_json(self.store.labels_file(board_id), labels)

    def _enrich_card(self, board_id: str, card: dict) -> dict:
        """Return a Trello-shaped copy of a stored card: resolve `idLabels` to
        full label dicts and drop the store-only `idLabels` / inline `comments`
        keys (comments are delivered as actions via get_comments)."""
        by_id = {lb["id"]: lb for lb in self._load_labels(board_id)}
        out = dict(card)
        out["labels"] = [by_id[i] for i in card.get("idLabels", []) if i in by_id]
        out.pop("idLabels", None)
        out.pop("comments", None)
        return out

    def _locate_comment(self, action_id: str) -> tuple[str, dict, dict]:
        """Find the (board_id, card, comment) holding an inline comment by id."""
        for bid in self.store.board_ids():
            for card in self.store.cards(bid):
                for c in card.get("comments", []):
                    if c["id"] == action_id:
                        return bid, card, c
        raise SystemExit(f"Comment not found: {action_id}")

    def _locate_checklist(self, checklist_id: str) -> tuple[str, dict, dict]:
        """Find the (board_id, card, checklist) holding a checklist by id."""
        for bid in self.store.board_ids():
            for card in self.store.cards(bid):
                for cl in card.get("checklists", []):
                    if cl["id"] == checklist_id:
                        return bid, card, cl
        raise SystemExit(f"Checklist not found: {checklist_id}")

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

    # ── Import (local-only; target of `trello export`) ───────────────

    def _to_store_card(self, board_id: str, card: dict) -> dict:
        """Map a source backend's Trello-shaped card to the on-disk store shape.

        Full `labels` collapse to `idLabels` (resolved back from labels.json on
        read); comments / checklists / attachments stay inline. Mirrors the field
        set `_new_card` writes so every read path (`_enrich_card`, commands) finds
        what it expects rather than `KeyError`-ing on a missing key."""
        label_ids = card.get("idLabels")
        if label_ids is None:
            label_ids = [lb["id"] for lb in card.get("labels", []) if lb.get("id")]
        return {
            "id": card["id"],
            "idBoard": board_id,
            "idList": card.get("idList", ""),
            "name": card.get("name", ""),
            "desc": card.get("desc", "") or "",
            "pos": card.get("pos", 0),
            "due": card.get("due"),
            "dueComplete": _as_bool(card.get("dueComplete", False)),
            "idLabels": list(label_ids),
            "idMembers": list(card.get("idMembers", [])),
            "checklists": card.get("checklists", []) or [],
            "attachments": card.get("attachments", []) or [],
            "comments": card.get("comments", []) or [],
            "closed": _as_bool(card.get("closed", False)),
            "shortUrl": card.get("shortUrl", ""),
            "shortLink": card.get("shortLink", ""),
            "dateLastActivity": card.get("dateLastActivity") or now_iso(),
        }

    def import_board(self, board: dict, lists: list[dict], labels: list[dict],
                     cards: list[dict]) -> dict:
        """Write a board pulled from another backend into the local store.

        Local-only — not part of the `Backend` ABC; the `export` command targets
        the file store explicitly. Source ids are preserved (both backends use
        24-hex ids), so a re-export overwrites the same board in place and every
        cross-reference (label / comment / checklist id) stays valid. Stale card
        files from a prior export of this board are pruned, so the result is a
        clean snapshot. Attachment blobs are not downloaded (uploaded attachments
        keep their source url). Returns counts for the caller to print."""
        bid = board["id"]
        atomic_write_json(self.store.board_file(bid), {
            "id": bid,
            "name": board.get("name", ""),
            "desc": board.get("desc", "") or "",
            "closed": _as_bool(board.get("closed", False)),
            "shortUrl": board.get("shortUrl", ""),
        })
        self._save_lists(bid, [
            {
                "id": l["id"],
                "name": l.get("name", ""),
                "pos": l.get("pos", 0),
                "closed": _as_bool(l.get("closed", False)),
            }
            for l in lists
        ])
        self._save_labels(bid, [
            {"id": lb["id"], "name": lb.get("name", ""), "color": lb.get("color", "")}
            for lb in labels
        ])
        kept: set[str] = set()
        n_comments = 0
        for card in cards:
            stored = self._to_store_card(bid, card)
            kept.add(stored["id"])
            n_comments += len(stored["comments"])
            self._save_card(bid, stored)
        cdir = self.store.cards_dir(bid)
        if cdir.exists():
            for p in cdir.glob("*.json"):
                if p.stem not in kept:
                    p.unlink()
        self._log(bid, "importBoard",
                  {"board": {"id": bid, "name": board.get("name", "")}})
        return {
            "id": bid,
            "name": board.get("name", ""),
            "lists": len(lists),
            "labels": len(labels),
            "cards": len(cards),
            "comments": n_comments,
        }

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
                  due: str | None, pos: Any, labels: list[str] | None = None) -> dict:
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
            "idLabels": list(labels or []),
            "idMembers": [],
            "checklists": [],
            "attachments": [],
            "comments": [],
            "closed": False,
            "shortUrl": "",
            "shortLink": "",
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
        return [self._enrich_card(board_id, c) for c in cards]

    def get_cards_in_list(self, list_id: str,
                          with_latest_comment: bool = False) -> list[dict]:
        board_id, _ = self._locate_list(list_id)
        cards = [
            c for c in self.store.cards(board_id)
            if c.get("idList") == list_id and not c.get("closed")
        ]
        cards.sort(key=lambda c: c.get("pos", 0))
        out = []
        for c in cards:
            enriched = self._enrich_card(board_id, c)
            if with_latest_comment:
                latest = sorted(c.get("comments", []),
                                key=lambda x: x.get("date", ""), reverse=True)[:1]
                enriched["actions"] = latest
            out.append(enriched)
        return out

    def get_card(self, card_id: str) -> dict:
        board_id, card = self._load_card(card_id)
        return self._enrich_card(board_id, card)

    def get_my_cards(self) -> list[dict]:
        # Single-user store: every open card is "mine". get_my_cards has no board
        # scope (Trello's is cross-board), so gather from every local board.
        out = []
        for bid in self.store.board_ids():
            for c in self.store.cards(bid):
                if not c.get("closed"):
                    out.append(self._enrich_card(bid, c))
        out.sort(key=lambda c: c.get("dateLastActivity") or "", reverse=True)
        return out

    def create_card(self, list_id: str, name: str, desc: str | None = None,
                    due: str | None = None, labels: list[str] | None = None,
                    pos: str = "top") -> dict:
        board_id, _ = self._locate_list(list_id)
        card = self._new_card(board_id, list_id, name, desc, due, pos, labels)
        self._save_card(board_id, card)
        self._log(board_id, "createCard",
                  {"card": {"id": card["id"], "name": name}, "list": {"id": list_id}})
        return self._enrich_card(board_id, card)

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

    # ── Comments (inline in the card JSON, action-shaped) ────────────

    def _comment_action(self, card: dict, text: str) -> dict:
        user = self._local_user()
        return {
            "id": new_id(),
            "type": "commentCard",
            "date": now_iso(),
            "idMemberCreator": user["id"],
            "memberCreator": user,
            "data": {"text": text, "card": {"id": card["id"], "name": card["name"]}},
        }

    def add_comment(self, card_id: str, text: str) -> dict:
        board_id, card = self._load_card(card_id)
        action = self._comment_action(card, text)
        card.setdefault("comments", []).append(action)
        card["dateLastActivity"] = now_iso()
        self._save_card(board_id, card)
        self._log(board_id, "commentCard",
                  {"text": text, "card": {"id": card_id, "name": card["name"]}})
        return action

    def get_comments(self, card_id: str, limit: int = 10) -> list[dict]:
        _, card = self._load_card(card_id)
        comments = sorted(card.get("comments", []),
                          key=lambda c: c.get("date", ""), reverse=True)
        return comments[:limit]

    def update_comment(self, action_id: str, text: str) -> dict:
        board_id, card, comment = self._locate_comment(action_id)
        comment.setdefault("data", {})["text"] = text
        self._save_card(board_id, card)
        return comment

    def delete_comment(self, action_id: str) -> None:
        board_id, card, _ = self._locate_comment(action_id)
        card["comments"] = [c for c in card["comments"] if c["id"] != action_id]
        self._save_card(board_id, card)

    # ── Labels (labels.json; cards reference them by id in idLabels) ──

    def get_labels(self, board_id: str) -> list[dict]:
        self._load_board(board_id)
        return self._load_labels(board_id)

    def create_label(self, board_id: str, name: str, color: str | None = None) -> dict:
        self._load_board(board_id)
        labels = self._load_labels(board_id)
        label = {"id": new_id(), "name": name, "color": color or ""}
        labels.append(label)
        self._save_labels(board_id, labels)
        self._log(board_id, "createLabel", {"label": {"id": label["id"], "name": name}})
        return label

    def update_label(self, label_id: str, **fields: Any) -> dict:
        for bid in self.store.board_ids():
            labels = self._load_labels(bid)
            target = next((lb for lb in labels if lb["id"] == label_id), None)
            if target is None:
                continue
            if "name" in fields:
                target["name"] = fields["name"]
            if "color" in fields:
                target["color"] = fields["color"] or ""
            self._save_labels(bid, labels)
            return target
        raise SystemExit(f"Label not found: {label_id}")

    def delete_label(self, label_id: str) -> None:
        for bid in self.store.board_ids():
            labels = self._load_labels(bid)
            if not any(lb["id"] == label_id for lb in labels):
                continue
            self._save_labels(bid, [lb for lb in labels if lb["id"] != label_id])
            # Drop the now-dangling id from every card that referenced it.
            for card in self.store.cards(bid):
                if label_id in card.get("idLabels", []):
                    card["idLabels"] = [i for i in card["idLabels"] if i != label_id]
                    self._save_card(bid, card)
            return
        raise SystemExit(f"Label not found: {label_id}")

    def add_label_to_card(self, card_id: str, label_id: str) -> None:
        board_id, card = self._load_card(card_id)
        ids = card.setdefault("idLabels", [])
        if label_id not in ids:
            ids.append(label_id)
            self._save_card(board_id, card)
            self._log(board_id, "addLabelToCard",
                      {"card": {"id": card_id, "name": card["name"]},
                       "label": {"id": label_id}})

    def remove_label_from_card(self, card_id: str, label_id: str) -> None:
        board_id, card = self._load_card(card_id)
        if label_id in card.get("idLabels", []):
            card["idLabels"] = [i for i in card["idLabels"] if i != label_id]
            self._save_card(board_id, card)

    # ── Members (single local user from the OS username) ─────────────

    def get_members(self, board_id: str) -> list[dict]:
        self._load_board(board_id)
        return [self._local_user()]

    # ── Activity / updates (read the append-only JSONL log) ──────────

    @staticmethod
    def _parse_iso(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None

    def get_activity(self, board_id: str, limit: int = 10) -> list[dict]:
        self._load_board(board_id)
        user = self._local_user()
        actions = self.store.read_activity(board_id)
        for a in actions:
            a.setdefault("memberCreator", user)
        actions.reverse()  # log is oldest-first; show newest first
        return actions[:limit]

    def get_actions_since(self, board_id: str, since: str,
                          action_types: str | None = None,
                          page: int = 1000) -> list[dict]:
        self._load_board(board_id)
        user = self._local_user()
        wanted = set(action_types.split(",")) if action_types else None
        cutoff = self._parse_iso(since)
        out = []
        for a in self.store.read_activity(board_id):
            if wanted is not None and a.get("type") not in wanted:
                continue
            when = self._parse_iso(a.get("date", ""))
            if cutoff is not None and when is not None:
                try:
                    if when < cutoff:
                        continue
                except TypeError:  # naive/aware mismatch — keep rather than drop
                    pass
            a.setdefault("memberCreator", user)
            out.append(a)
        out.reverse()  # newest first
        return out

    # ── Checklists (inline in the card JSON) ─────────────────────────

    def get_checklists(self, card_id: str) -> list[dict]:
        _, card = self._load_card(card_id)
        return card.get("checklists", [])

    def create_checklist(self, card_id: str, name: str) -> dict:
        board_id, card = self._load_card(card_id)
        checklists = card.setdefault("checklists", [])
        cl = {
            "id": new_id(),
            "idCard": card_id,
            "name": name,
            "pos": POS_STEP * (len(checklists) + 1),
            "checkItems": [],
        }
        checklists.append(cl)
        self._save_card(board_id, card)
        self._log(board_id, "addChecklistToCard",
                  {"card": {"id": card_id, "name": card["name"]},
                   "checklist": {"id": cl["id"], "name": name}})
        return cl

    def delete_checklist(self, checklist_id: str) -> None:
        board_id, card, _ = self._locate_checklist(checklist_id)
        card["checklists"] = [cl for cl in card["checklists"] if cl["id"] != checklist_id]
        self._save_card(board_id, card)

    def rename_checklist(self, checklist_id: str, name: str) -> dict:
        board_id, card, cl = self._locate_checklist(checklist_id)
        cl["name"] = name
        self._save_card(board_id, card)
        return cl

    def add_checkitem(self, checklist_id: str, name: str) -> dict:
        board_id, card, cl = self._locate_checklist(checklist_id)
        items = cl.setdefault("checkItems", [])
        item = {
            "id": new_id(),
            "idChecklist": checklist_id,
            "name": name,
            "state": "incomplete",
            "pos": POS_STEP * (len(items) + 1),
        }
        items.append(item)
        self._save_card(board_id, card)
        return item

    def delete_checkitem(self, checklist_id: str, item_id: str) -> None:
        board_id, card, cl = self._locate_checklist(checklist_id)
        cl["checkItems"] = [it for it in cl.get("checkItems", []) if it["id"] != item_id]
        self._save_card(board_id, card)

    def update_checkitem(self, card_id: str, item_id: str, **fields: Any) -> dict:
        board_id, card = self._load_card(card_id)
        for cl in card.get("checklists", []):
            for it in cl.get("checkItems", []):
                if it["id"] == item_id:
                    if "name" in fields:
                        it["name"] = fields["name"]
                    if "state" in fields:
                        it["state"] = fields["state"]
                    self._save_card(board_id, card)
                    return it
        raise SystemExit(f"Check item not found: {item_id}")

    # ── Attachments (inline metadata; uploaded blobs under attachments/) ──

    def _blob_path(self, url: str) -> Path:
        """Absolute path of an uploaded blob. New stores keep `url` relative to
        the store root (so the folder is portable across machines / Dropbox);
        older stores may have an absolute path — honour it as-is."""
        p = Path(url)
        return p if p.is_absolute() else self.store.root / url

    def get_attachments(self, card_id: str) -> list[dict]:
        _, card = self._load_card(card_id)
        return card.get("attachments", [])

    def _add_attachment(self, card_id: str, att: dict) -> dict:
        board_id, card = self._load_card(card_id)
        card.setdefault("attachments", []).append(att)
        card["dateLastActivity"] = now_iso()
        self._save_card(board_id, card)
        self._log(board_id, "addAttachmentToCard",
                  {"card": {"id": card_id, "name": card["name"]},
                   "attachment": {"id": att["id"], "name": att.get("name")}})
        return att

    def add_attachment_url(self, card_id: str, url: str, name: str | None = None) -> dict:
        return self._add_attachment(card_id, {
            "id": new_id(),
            "name": name or url,
            "url": url,
            "mimeType": "",
            "bytes": None,
            "isUpload": False,
            "date": now_iso(),
        })

    def add_attachment_file(self, card_id: str, file_path: str,
                            name: str | None = None) -> dict:
        board_id = self._locate_card(card_id)
        src = Path(file_path)
        if not src.is_file():
            raise SystemExit(f"File not found: {file_path}")
        att_id = new_id()
        dest_dir = self.store.attachments_dir(board_id, card_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{att_id}-{src.name}"
        shutil.copyfile(src, dest)
        return self._add_attachment(card_id, {
            "id": att_id,
            "name": name or src.name,
            "url": dest.relative_to(self.store.root).as_posix(),  # portable, root-relative
            "mimeType": mimetypes.guess_type(src.name)[0] or "",
            "bytes": dest.stat().st_size,
            "isUpload": True,
            "date": now_iso(),
        })

    def delete_attachment(self, card_id: str, attachment_id: str) -> None:
        board_id, card = self._load_card(card_id)
        removed = None
        keep = []
        for a in card.get("attachments", []):
            if a["id"] == attachment_id:
                removed = a
            else:
                keep.append(a)
        if removed is None:
            raise SystemExit(f"Attachment not found: {attachment_id}")
        card["attachments"] = keep
        self._save_card(board_id, card)
        if removed.get("isUpload"):  # URL attachments have nothing local to remove
            blob = self._blob_path(removed.get("url", ""))
            try:
                if blob.is_file():
                    blob.unlink()
                blob.parent.rmdir()  # drop the card's attachment dir if now empty
            except OSError:
                pass

    def download_attachment(self, url: str, dest: str, authed: bool = True) -> None:
        """Fetch an external URL attachment over http, or copy an uploaded blob
        (its `url` is a root-relative path) to `dest`. `authed` is unused locally
        — the file store has no Trello OAuth."""
        if url.lower().startswith(("http://", "https://")):
            import httpx

            with httpx.stream("GET", url, timeout=60, follow_redirects=True) as r:
                r.raise_for_status()
                with open(dest, "wb") as fh:
                    for chunk in r.iter_bytes():
                        fh.write(chunk)
            return
        src = self._blob_path(url)
        if src.is_file():
            shutil.copyfile(src, dest)
            return
        raise SystemExit(f"Cannot download attachment (no local blob or URL): {url}")
