"""TrelloBackend — the Trello REST API implementation of the Backend contract.

This is the original `api.py` httpx client, moved behind the Backend ABC. The
public `api.py` is now a thin facade forwarding to whichever backend
`get_backend()` selects — either this `TrelloBackend` or the self-hosted
`LocalBackend` file store (see `local.py` / CLAUDE.md). Both return the same
Trello-shaped dicts. See DESIGN.md.
"""

from __future__ import annotations

import os
import random
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..config import get_auth
from .base import Backend

BASE = "https://api.trello.com/1"

# `grab_top_card` claim-handshake tunables (Trello has no atomic primitive, so we
# fake it like CONTRIBUTING.md). The marker phrase (em-dash and all) must match
# CONTRIBUTING's exactly — other agents and hand-run claims scan for this string.
# NOTE: this handshake is NOT verified against live Trello (no creds here); it
# mirrors CONTRIBUTING.md's known-good algorithm. See CLAUDE.md.
_CLAIM_MARKER = "I am doing this now — claim "
_GRAB_WAIT_RANGE = (10.0, 30.0)   # randomized blocking wait, seconds
_GRAB_CLAIM_WINDOW = timedelta(seconds=60)  # ignore claims older than this (stale)
# Cap on how many distinct cards we'll try under contention (each loss moves on
# to the next). NOT an infinite-loop guard — the loop already ends when the
# source list drains; this just bounds a pathologically contended list.
_GRAB_MAX_ATTEMPTS = 50

# Shared-transport retry tunables.
_MAX_RETRIES = 3            # attempts beyond the first for 429 / transient errors
_RETRY_BACKOFF = 0.5       # seconds; exponential base (0.5, 1.0, 2.0, …)
_RETRY_AFTER_CAP = 30.0    # clamp a hostile Retry-After header


def _parse_dt(value: str | None) -> datetime | None:
    """Parse a Trello action timestamp (ISO 8601, often `…Z`) to an aware
    datetime, or None if absent/malformed."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_utc(dt: datetime) -> datetime:
    """Normalize a datetime to aware UTC so aware/naive values compare without
    raising TypeError (Trello sends `…Z`, but be defensive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_claim(text: str) -> str | None:
    """Extract the claim id from a claim comment, or None if `text` isn't one.

    A claim comment must *start* with the exact marker (so a comment merely
    quoting the phrase mid-sentence isn't mistaken for a claim) and be followed
    by a non-empty id token."""
    body = text.strip()
    if not body.startswith(_CLAIM_MARKER):
        return None
    rest = body[len(_CLAIM_MARKER):].split()
    return rest[0] if rest else None


class TrelloBackend(Backend):
    # --- transport ---

    def __init__(self) -> None:
        # Lazily-populated so importing this module / constructing the backend
        # with no creds or no network need never fails.
        self._auth: tuple[str, str] | None = None
        self._client: httpx.Client | None = None

    def _get_auth(self) -> tuple[str, str]:
        # Cache credentials for the life of this backend instance. The CLI is
        # one-shot per process, so re-reading the config file on every request
        # is pure waste; load once, lazily.
        if self._auth is None:
            self._auth = get_auth()
        return self._auth

    def _get_client(self) -> httpx.Client:
        # One shared, connection-pooled client per backend instance, created
        # lazily. Safe to keep open: one process, one CLI invocation.
        if self._client is None:
            self._client = httpx.Client(base_url=BASE, timeout=15)
        return self._client

    def _params(self, **kw: Any) -> dict[str, Any]:
        key, token = self._get_auth()
        return {"key": key, "token": token, **{k: v for k, v in kw.items() if v is not None}}

    @staticmethod
    def _retry_delay(r: httpx.Response, attempt: int) -> float:
        """Seconds to wait before retrying a 429: honor `Retry-After` if present
        (clamped), else exponential backoff."""
        ra = r.headers.get("Retry-After")
        if ra:
            try:
                return min(float(ra), _RETRY_AFTER_CAP)
            except ValueError:
                pass
        return _RETRY_BACKOFF * (2 ** attempt)

    @staticmethod
    def _http_error_message(method: str, path: str, r: httpx.Response) -> str:
        """A clean one-line message for an HTTP error, matching the local
        backend's SystemExit style (no raw httpx traceback)."""
        code = r.status_code
        m = method.upper()
        if code == 404:
            return f"Not found: {m} {path}"
        if code == 401:
            return ("Trello rejected the credentials (401). Check TRELLO_API_KEY /"
                    " TRELLO_TOKEN, or re-run: trello configure <api_key> <token>")
        if code == 429:
            return "Trello rate limit exceeded (429); try again shortly."
        return f"Trello API error {code} on {m} {path}"

    def _request(self, method: str, path: str, *,
                 params: dict[str, Any] | None = None, **kw: Any) -> httpx.Response:
        """Issue an HTTP request through the shared client with simple retry/
        backoff for 429s and transient transport errors, translating any HTTP or
        network failure into a clean SystemExit instead of a raw httpx traceback.
        This is what lets the web server's SystemExit->404 translation work for
        the Trello backend too."""
        client = self._get_client()
        for attempt in range(_MAX_RETRIES + 1):
            try:
                r = client.request(method, path, params=params, **kw)
            except httpx.TransportError as e:
                # Network-level failure (DNS, connect, read timeout): retry a few
                # times, then surface a clean one-liner.
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_BACKOFF * (2 ** attempt))
                    continue
                raise SystemExit(f"Network error contacting Trello: {e}") from e
            # Rate-limited: back off and retry until the cap, then fall through to
            # raise_for_status below (which yields the clean 429 message).
            if r.status_code == 429 and attempt < _MAX_RETRIES:
                time.sleep(self._retry_delay(r, attempt))
                continue
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise SystemExit(self._http_error_message(method, path, r)) from e
            return r
        # Unreachable: the final 429 attempt takes the raise_for_status path.
        raise SystemExit("Trello rate limit exceeded (429); try again shortly.")

    def _get(self, path: str, **kw: Any) -> Any:
        return self._request("GET", path, params=self._params(**kw)).json()

    def _post(self, path: str, **kw: Any) -> Any:
        return self._request("POST", path, params=self._params(**kw)).json()

    def _put(self, path: str, **kw: Any) -> Any:
        return self._request("PUT", path, params=self._params(**kw)).json()

    def _delete(self, path: str) -> None:
        self._request("DELETE", path, params=self._params())

    # --- Boards ---

    def get_boards(self, include_closed: bool = False) -> list[dict]:
        return self._get("/members/me/boards", fields="id,name,shortUrl,closed",
                         filter="all" if include_closed else "open")

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

    def update_board(self, board_id: str, name: str | None = None,
                     closed: bool | None = None) -> dict:
        fields: dict[str, Any] = {}
        if name is not None:
            fields["name"] = name
        if closed is not None:
            fields["closed"] = "true" if closed else "false"
        if not fields:
            return self.get_board(board_id)
        return self._put(f"/boards/{board_id}", **fields)

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
            fields="id,name,shortUrl,labels,due,dueComplete,idList,idMembers,dateLastActivity,pos",
            filter=card_filter,
        )

    def get_cards_in_list(self, list_id: str,
                          with_latest_comment: bool = False) -> list[dict]:
        kw: dict[str, Any] = {
            "fields": "id,name,shortUrl,labels,due,dueComplete,idMembers,dateLastActivity,pos",
        }
        if with_latest_comment:
            kw["actions"] = "commentCard"
            kw["actions_limit"] = "1"
        return self._get(f"/lists/{list_id}/cards", **kw)

    def get_card(self, card_id: str) -> dict:
        return self._get(
            f"/cards/{card_id}",
            fields="id,name,desc,shortUrl,labels,due,dueComplete,idBoard,idList,idMembers,dateLastActivity",
            checklists="all",
            attachments="true",
            attachment_fields="id,name,url,mimeType,bytes,isUpload",
        )

    def get_my_cards(self) -> list[dict]:
        return self._get(
            "/members/me/cards",
            fields="id,name,shortUrl,labels,due,dueComplete,idBoard,idList,dateLastActivity",
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
        # claim handshake, structured as two strictly-separated phases so a
        # transient error can't undo a competitor's legitimate win:
        #   Phase 1 (pre-adjudication): move the card to dest, stake a claim
        #     comment, wait out the window. We have NOT won yet, so ANY failure
        #     here — including Ctrl-C during the sleep — rolls everything back
        #     (retract our claim, return the card to the source list).
        #   Phase 2 (adjudicated): once _won_claim has ruled, we NEVER move the
        #     card back. On a win we own it (a blip reading it back is not a
        #     rollback trigger); on a loss the winner owns it, so we only retract
        #     our own claim and move to the next card.
        # NOTE: not verified against live Trello (see module docstring).
        claim_id = secrets.token_hex(4)
        lost: set[str] = set()
        for _ in range(_GRAB_MAX_ATTEMPTS):
            cards = sorted(self.get_cards_in_list(source_list_id),
                           key=lambda c: c.get("pos", 0))
            candidates = [c for c in cards if c["id"] not in lost]
            if not candidates:
                return None  # source list drained — genuinely nothing to grab
            card = candidates[0]
            card_id = card["id"]

            # --- Phase 1: fast grab + claim + wait (pre-adjudication) ---
            self.move_card(card_id, dest_list_id)
            mine: dict | None = None
            try:
                mine = self.add_comment(card_id, f"{_CLAIM_MARKER}{claim_id}")
                my_date = _parse_dt(mine.get("date"))
                time.sleep(random.uniform(*_GRAB_WAIT_RANGE))
                won = self._won_claim(card_id, claim_id, my_date)
            except BaseException:
                # Pre-adjudication failure (transport error, or KeyboardInterrupt
                # during the sleep): we haven't won, so unwind the grab and
                # re-raise. BaseException so Ctrl-C doesn't strand the card.
                self._rollback_grab(card_id, source_list_id, mine)
                raise

            # --- Phase 2: adjudicated — never move the card back from here ---
            if won:
                # We own it. A blip reading the full card must NOT roll back;
                # fall back to the minimal card info we already hold.
                try:
                    return self.get_card(card_id)
                except SystemExit:
                    return card
            # Legit loss: the winner owns the card now. Retract our own claim so
            # it doesn't linger, but do NOT move the card back. If the retraction
            # fails, just warn and move on to the next card.
            try:
                self.delete_comment(mine["id"])
            except BaseException as e:
                print(f"warning: could not retract lost claim on {card_id}: {e}",
                      file=sys.stderr)
            lost.add(card_id)

        # Exhausted the attempt cap on a pathologically contended list. Distinct
        # from an empty list (None -> "Nothing to grab"): surface it so the CLI
        # doesn't mislead the caller into thinking the list is empty.
        raise SystemExit(
            f"Gave up grabbing after {_GRAB_MAX_ATTEMPTS} contended attempts; "
            "the source list is busy — try again."
        )

    def _rollback_grab(self, card_id: str, source_list_id: str,
                       mine: dict | None) -> None:
        """Best-effort undo of a *pre-adjudication* grab: retract our claim
        comment (if we posted one) and move the card back to the source list so
        it stays grabbable. Secondary failures are swallowed — we're already
        unwinding and about to re-raise."""
        if mine is not None:
            try:
                self.delete_comment(mine["id"])
            except BaseException:
                pass
        try:
            self.move_card(card_id, source_list_id)
        except BaseException:
            pass

    def _won_claim(self, card_id: str, claim_id: str,
                   my_date: datetime | None) -> bool:
        """True if our claim is the earliest among the live (in-window) claims on
        the card. Ties on the exact timestamp break deterministically by claim
        id. Claims older than the window are stale (a past session) and ignored,
        so a re-grabbed card isn't blocked by its history."""
        if my_date is None:
            # Our own claim timestamp is unreadable — we can't prove we were
            # first, so fail safe and treat it as a LOSS rather than risk two
            # racers both "winning" the same card.
            return False
        my_date = _as_utc(my_date)
        floor = my_date - _GRAB_CLAIM_WINDOW
        # The 50 most-recent comments: a claim posted seconds ago is always in
        # that window unless 50+ comments landed in the same few seconds, which
        # isn't a real claim-race scenario.
        for c in self.get_comments(card_id, limit=50):
            text = (c.get("data") or {}).get("text") or ""
            other_id = _parse_claim(text)
            if other_id is None:
                continue  # not a well-formed claim comment (marker prefix + id)
            if other_id == claim_id:
                continue  # our own claim
            other_date = _parse_dt(c.get("date"))
            if other_date is None:
                continue  # their claim has no rankable timestamp
            other_date = _as_utc(other_date)  # normalize so aware/naive compare
            if other_date < floor:
                continue  # stale (a past session)
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
        self._delete(f"/cards/{card_id}/idLabels/{label_id}")

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
        with open(file_path, "rb") as fh:
            files = {"file": (os.path.basename(file_path), fh)}
            # Route through the shared helper for auth caching + error translation;
            # override the default timeout since an upload can be large.
            r = self._request(
                "POST", f"/cards/{card_id}/attachments",
                params=self._params(name=name), files=files, timeout=60,
            )
        return r.json()

    def delete_attachment(self, card_id: str, attachment_id: str) -> None:
        self._delete(f"/cards/{card_id}/attachments/{attachment_id}")

    def download_attachment(self, url: str, dest: str, authed: bool = True) -> None:
        """Stream an attachment to `dest`. Trello-hosted uploads require the OAuth
        header (`authed=True`); external URL attachments are fetched without it."""
        headers = {}
        if authed:
            key, token = self._get_auth()
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
