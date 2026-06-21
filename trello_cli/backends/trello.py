"""TrelloBackend — the Trello REST API implementation of the Backend contract.

This is the original `api.py` httpx client, moved behind the Backend ABC. The
public `api.py` is now a thin facade forwarding to whichever backend
`get_backend()` selects (Trello-only for now). See DESIGN.md.
"""

from __future__ import annotations

import os
import random
import secrets
import time
from datetime import datetime, timedelta
from typing import Any

import httpx

from ..config import get_auth
from .base import Backend

BASE = "https://api.trello.com/1"

# `grab_top_card` claim-handshake tunables (Trello has no atomic primitive, so we
# fake it like CONTRIBUTING.md). The marker phrase (em-dash and all) must match
# CONTRIBUTING's exactly — other agents and hand-run claims scan for this string.
_CLAIM_MARKER = "I am doing this now — claim "
_GRAB_WAIT_RANGE = (10.0, 30.0)   # randomized blocking wait, seconds
_GRAB_CLAIM_WINDOW = timedelta(seconds=60)  # ignore claims older than this (stale)
_GRAB_MAX_ATTEMPTS = 50           # infinite-loop guard across retried cards


def _parse_dt(value: str | None) -> datetime | None:
    """Parse a Trello action timestamp (ISO 8601, often `…Z`) to an aware
    datetime, or None if absent/malformed."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class TrelloBackend(Backend):
    # --- transport ---

    def _params(self, **kw: Any) -> dict[str, Any]:
        key, token = get_auth()
        return {"key": key, "token": token, **{k: v for k, v in kw.items() if v is not None}}

    def _get(self, path: str, **kw: Any) -> Any:
        r = httpx.get(f"{BASE}{path}", params=self._params(**kw), timeout=15)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, **kw: Any) -> Any:
        r = httpx.post(f"{BASE}{path}", params=self._params(**kw), timeout=15)
        r.raise_for_status()
        return r.json()

    def _put(self, path: str, **kw: Any) -> Any:
        r = httpx.put(f"{BASE}{path}", params=self._params(**kw), timeout=15)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> None:
        r = httpx.delete(f"{BASE}{path}", params=self._params(), timeout=15)
        r.raise_for_status()

    # --- Boards ---

    def get_boards(self) -> list[dict]:
        return self._get("/members/me/boards", fields="id,name,shortUrl,closed", filter="open")

    def get_board(self, board_id: str) -> dict:
        return self._get(f"/boards/{board_id}", fields="id,name,shortUrl,desc")

    def create_board(self, name: str, desc: str | None = None,
                     default_lists: bool = True) -> dict:
        return self._post(
            "/boards/",
            name=name,
            desc=desc,
            defaultLists="true" if default_lists else "false",
        )

    # --- Lists ---

    def get_lists(self, board_id: str) -> list[dict]:
        return self._get(f"/boards/{board_id}/lists", fields="id,name,pos", filter="open")

    def create_list(self, board_id: str, name: str, pos: str | None = None) -> dict:
        return self._post("/lists", name=name, idBoard=board_id, pos=pos)

    def archive_list(self, list_id: str) -> dict:
        return self._put(f"/lists/{list_id}/closed", value="true")

    def update_list(self, list_id: str, **fields: Any) -> dict:
        # Persisted per-list `sort` is a local-backend-only feature — Trello has
        # no native field for it. Drop it so a `sort` PATCH from the web UI is a
        # clean no-op on a Trello board rather than an unknown-param request.
        fields.pop("sort", None)
        if not fields:
            return self._get(f"/lists/{list_id}", fields="id,name,pos")
        return self._put(f"/lists/{list_id}", **fields)

    def rename_list(self, list_id: str, name: str) -> dict:
        return self.update_list(list_id, name=name)

    # --- Cards ---

    def get_board_cards(self, board_id: str, card_filter: str = "visible") -> list[dict]:
        return self._get(
            f"/boards/{board_id}/cards",
            fields="id,name,shortUrl,labels,due,dueComplete,idList,idMembers,shortId,dateLastActivity,pos",
            filter=card_filter,
        )

    def get_cards_in_list(self, list_id: str,
                          with_latest_comment: bool = False) -> list[dict]:
        kw: dict[str, Any] = {
            "fields": "id,name,shortUrl,labels,due,idMembers,shortId,dateLastActivity,pos",
        }
        if with_latest_comment:
            kw["actions"] = "commentCard"
            kw["actions_limit"] = "1"
        return self._get(f"/lists/{list_id}/cards", **kw)

    def get_card(self, card_id: str) -> dict:
        return self._get(
            f"/cards/{card_id}",
            fields="id,name,desc,shortUrl,labels,due,dueComplete,idList,idMembers,shortId,dateLastActivity",
            checklists="all",
            attachments="true",
            attachment_fields="id,name,url,mimeType,bytes,isUpload",
        )

    def get_my_cards(self) -> list[dict]:
        return self._get(
            "/members/me/cards",
            fields="id,name,shortUrl,labels,due,idBoard,idList,shortId,dateLastActivity",
        )

    def create_card(self, list_id: str, name: str, desc: str | None = None,
                    due: str | None = None, labels: list[str] | None = None,
                    pos: str = "top") -> dict:
        kw = dict(idList=list_id, name=name, pos=pos)
        if desc:
            kw["desc"] = desc
        if due:
            kw["due"] = due
        if labels:
            kw["idLabels"] = ",".join(labels)
        return self._post("/cards", **kw)

    def move_card(self, card_id: str, list_id: str) -> dict:
        return self._put(f"/cards/{card_id}", idList=list_id)

    def grab_top_card(self, source_list_id: str,
                      dest_list_id: str) -> dict | None:
        # Trello has no atomic move-and-return, so fake it with CONTRIBUTING.md's
        # claim handshake: grab the top card, stake a claim comment, wait, then
        # let the *earliest* claim win. On a loss, retract our claim, leave the
        # card with the winner, and try the next card down.
        claim_id = secrets.token_hex(4)
        lost: set[str] = set()
        # Safety cap on how many distinct cards we'll try (each loss moves on to
        # the next). Not an infinite-loop guard — the loop already ends when the
        # source list runs out of un-lost candidates; this just bounds a
        # pathologically contended list.
        for _ in range(_GRAB_MAX_ATTEMPTS):
            cards = sorted(self.get_cards_in_list(source_list_id),
                           key=lambda c: c.get("pos", 0))
            candidates = [c for c in cards if c["id"] not in lost]
            if not candidates:
                return None
            card_id = candidates[0]["id"]
            self.move_card(card_id, dest_list_id)          # fast grab
            try:
                mine = self.add_comment(card_id, f"{_CLAIM_MARKER}{claim_id}")
                my_date = _parse_dt(mine.get("date"))
                time.sleep(random.uniform(*_GRAB_WAIT_RANGE))
                if self._won_claim(card_id, claim_id, my_date):
                    return self.get_card(card_id)
                self.delete_comment(mine["id"])            # legit loss: back off
            except Exception:
                # Anything after the fast grab failed (e.g. a transport error).
                # Don't strand the card in dest with no claim — move it back so
                # it stays grabbable, then surface the original error.
                try:
                    self.move_card(card_id, source_list_id)
                except Exception:
                    pass
                raise
            lost.add(card_id)
        return None

    def _won_claim(self, card_id: str, claim_id: str,
                   my_date: datetime | None) -> bool:
        """True if our claim is the earliest among the live (in-window) claims on
        the card. Ties on the exact timestamp break deterministically by claim
        id. Claims older than the window are stale (a past session) and ignored,
        so a re-grabbed card isn't blocked by its history."""
        if my_date is None:
            return True  # no comparable timestamp; assume we hold it
        floor = my_date - _GRAB_CLAIM_WINDOW
        # The 50 most-recent comments: a claim posted seconds ago is always in
        # that window unless 50+ comments landed in the same few seconds, which
        # isn't a real claim-race scenario.
        for c in self.get_comments(card_id, limit=50):
            text = (c.get("data") or {}).get("text") or ""
            if _CLAIM_MARKER not in text:
                continue
            rest = text.split(_CLAIM_MARKER, 1)[1].split()
            other_id = rest[0] if rest else ""
            if other_id == claim_id:
                continue  # our own claim
            other_date = _parse_dt(c.get("date"))
            if other_date is None or other_date < floor:
                continue  # malformed or stale
            if other_date < my_date or (other_date == my_date
                                        and other_id < claim_id):
                return False  # someone else claimed first
        return True

    def archive_card(self, card_id: str) -> dict:
        return self._put(f"/cards/{card_id}", closed="true")

    def unarchive_card(self, card_id: str) -> dict:
        return self._put(f"/cards/{card_id}", closed="false")

    def update_card(self, card_id: str, **fields: Any) -> dict:
        return self._put(f"/cards/{card_id}", **fields)

    # --- Comments ---

    def add_comment(self, card_id: str, text: str) -> dict:
        return self._post(f"/cards/{card_id}/actions/comments", text=text)

    def get_comments(self, card_id: str, limit: int = 10) -> list[dict]:
        return self._get(
            f"/cards/{card_id}/actions",
            filter="commentCard",
            limit=str(limit),
        )

    def update_comment(self, action_id: str, text: str) -> dict:
        return self._put(f"/actions/{action_id}", text=text)

    def delete_comment(self, action_id: str) -> None:
        self._delete(f"/actions/{action_id}")

    # --- Labels ---

    def get_labels(self, board_id: str) -> list[dict]:
        return self._get(f"/boards/{board_id}/labels", fields="id,name,color")

    def create_label(self, board_id: str, name: str, color: str | None = None) -> dict:
        kw: dict[str, Any] = {"name": name, "idBoard": board_id}
        if color:
            kw["color"] = color
        return self._post("/labels", **kw)

    def update_label(self, label_id: str, **fields: Any) -> dict:
        return self._put(f"/labels/{label_id}", **fields)

    def delete_label(self, label_id: str) -> None:
        self._delete(f"/labels/{label_id}")

    def add_label_to_card(self, card_id: str, label_id: str) -> None:
        self._post(f"/cards/{card_id}/idLabels", value=label_id)

    def remove_label_from_card(self, card_id: str, label_id: str) -> None:
        r = httpx.delete(
            f"{BASE}/cards/{card_id}/idLabels/{label_id}",
            params=self._params(), timeout=15,
        )
        r.raise_for_status()

    # --- Members ---

    def get_members(self, board_id: str) -> list[dict]:
        return self._get(f"/boards/{board_id}/members", fields="id,fullName,username")

    # --- Activity ---

    def get_activity(self, board_id: str, limit: int = 10) -> list[dict]:
        return self._get(f"/boards/{board_id}/actions", limit=str(limit))

    def get_actions_since(self, board_id: str, since: str,
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
            batch = self._get(f"/boards/{board_id}/actions", **kw)
            out.extend(batch)
            if len(batch) < page:
                break
            before = batch[-1]["id"]
        return out

    # --- Checklists ---

    def get_checklists(self, card_id: str) -> list[dict]:
        return self._get(f"/cards/{card_id}/checklists")

    def create_checklist(self, card_id: str, name: str) -> dict:
        return self._post(f"/cards/{card_id}/checklists", name=name)

    def delete_checklist(self, checklist_id: str) -> None:
        self._delete(f"/checklists/{checklist_id}")

    def rename_checklist(self, checklist_id: str, name: str) -> dict:
        return self._put(f"/checklists/{checklist_id}", name=name)

    def add_checkitem(self, checklist_id: str, name: str) -> dict:
        return self._post(f"/checklists/{checklist_id}/checkItems", name=name)

    def delete_checkitem(self, checklist_id: str, item_id: str) -> None:
        self._delete(f"/checklists/{checklist_id}/checkItems/{item_id}")

    def update_checkitem(self, card_id: str, item_id: str, **fields: Any) -> dict:
        return self._put(f"/cards/{card_id}/checkItem/{item_id}", **fields)

    # --- Attachments ---

    def get_attachments(self, card_id: str) -> list[dict]:
        return self._get(
            f"/cards/{card_id}/attachments",
            fields="id,name,url,mimeType,bytes,date,isUpload",
        )

    def add_attachment_url(self, card_id: str, url: str, name: str | None = None) -> dict:
        return self._post(f"/cards/{card_id}/attachments", url=url, name=name)

    def add_attachment_file(self, card_id: str, file_path: str,
                            name: str | None = None) -> dict:
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

    def delete_attachment(self, card_id: str, attachment_id: str) -> None:
        r = httpx.delete(
            f"{BASE}/cards/{card_id}/attachments/{attachment_id}",
            params=self._params(), timeout=15,
        )
        r.raise_for_status()

    def download_attachment(self, url: str, dest: str, authed: bool = True) -> None:
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
