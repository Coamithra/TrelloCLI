"""HttpBackend — the CLI on a hosted trellno server.

Implements the `Backend` ABC over the web app's JSON API (`trello serve` on a
remote box), so every CLI command — including the atomic `grab`, which executes
under the *server's* store lock — works from any machine that can reach the
server: another laptop, a CI job, a Claude cloud session. The server is the
single source of truth; this backend holds no local state at all.

Transport: almost every op goes through `POST /api/rpc` (the ABC serialized as
`{"op", "args", "kwargs"}` — see server.py), which keeps this class a thin,
mechanical shim that can't drift from the ABC. The two file-transfer ops are
the exception, since a client file path means nothing to the server:
`add_attachment_file` posts multipart to the browser's upload route, and
`download_attachment` streams store-relative urls from `GET /api/blob`
(absolute external urls are fetched directly, mirroring the other backends).

Errors: any non-2xx becomes a `SystemExit` carrying the server's `detail`
message, so remote failures read exactly like native CLI errors. Connection
failures name the server so a wrong URL / down box is obvious.

Auth: a `serve --token` server requires the token on every request; it comes
from `trello configure-http <url> <token>` (persisted) or the
`TRELLO_SERVER_TOKEN` env var, sent as `Authorization: Bearer`.
"""

from __future__ import annotations

from typing import Any

import httpx

from .base import Backend

_TIMEOUT = 30.0


class HttpBackend(Backend):
    def __init__(self, base_url: str | None, token: str | None = None,
                 client: httpx.Client | None = None) -> None:
        # `client` is a test seam: the suite injects a Starlette TestClient
        # (an httpx.Client running the ASGI app in-process) so the full
        # backend↔server round-trip is exercised with no sockets.
        if not base_url:
            raise SystemExit(
                "No trellno server configured for the http backend.\n"
                "Run: trello configure-http <server_url> [<token>]\n"
                "or set TRELLO_SERVER (and TRELLO_SERVER_TOKEN) env vars."
            )
        self._base = base_url.rstrip("/")
        if client is not None:
            self._client = client
        else:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            self._client = httpx.Client(
                base_url=self._base, headers=headers, timeout=_TIMEOUT
            )

    # ── transport ────────────────────────────────────────────────────

    def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            resp = self._client.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise SystemExit(f"Cannot reach trellno server at {self._base}: {e}")
        return self._checked(resp)

    def _checked(self, resp: httpx.Response) -> httpx.Response:
        if resp.status_code < 400:
            return resp
        try:
            detail = resp.json().get("detail")
        except ValueError:
            detail = None
        detail = detail or resp.text or f"HTTP {resp.status_code}"
        if resp.status_code == 401:
            raise SystemExit(
                f"Trellno server rejected the request: {detail}\n"
                "Check the token (trello configure-http <url> <token>, or "
                "TRELLO_SERVER_TOKEN)."
            )
        raise SystemExit(str(detail))

    def _rpc(self, op: str, *args: Any, **kwargs: Any) -> Any:
        resp = self._request(
            "POST", "/api/rpc", json={"op": op, "args": list(args), "kwargs": kwargs}
        )
        return resp.json()["result"]

    # ── Boards ───────────────────────────────────────────────────────

    def get_boards(self, include_closed: bool = False) -> list[dict]:
        return self._rpc("get_boards", include_closed=include_closed)

    def get_board(self, board_id: str) -> dict:
        return self._rpc("get_board", board_id)

    def create_board(self, name: str, desc: str | None = None,
                     default_lists: bool = True) -> dict:
        return self._rpc("create_board", name, desc=desc,
                         default_lists=default_lists)

    def update_board(self, board_id: str, name: str | None = None,
                     closed: bool | None = None) -> dict:
        return self._rpc("update_board", board_id, name=name, closed=closed)

    # ── Lists ────────────────────────────────────────────────────────

    def get_lists(self, board_id: str) -> list[dict]:
        return self._rpc("get_lists", board_id)

    def create_list(self, board_id: str, name: str, pos: str | None = None) -> dict:
        return self._rpc("create_list", board_id, name, pos=pos)

    def archive_list(self, list_id: str) -> dict:
        return self._rpc("archive_list", list_id)

    def update_list(self, list_id: str, **fields: Any) -> dict:
        return self._rpc("update_list", list_id, **fields)

    def rename_list(self, list_id: str, name: str) -> dict:
        return self._rpc("rename_list", list_id, name)

    # ── Cards ────────────────────────────────────────────────────────

    def get_board_cards(self, board_id: str, card_filter: str = "visible") -> list[dict]:
        return self._rpc("get_board_cards", board_id, card_filter=card_filter)

    def get_cards_in_list(self, list_id: str,
                          with_latest_comment: bool = False) -> list[dict]:
        return self._rpc("get_cards_in_list", list_id,
                         with_latest_comment=with_latest_comment)

    def get_card(self, card_id: str) -> dict:
        return self._rpc("get_card", card_id)

    def get_my_cards(self) -> list[dict]:
        return self._rpc("get_my_cards")

    def create_card(self, list_id: str, name: str, desc: str | None = None,
                    due: str | None = None, labels: list[str] | None = None,
                    pos: str = "top") -> dict:
        return self._rpc("create_card", list_id, name, desc=desc, due=due,
                         labels=labels, pos=pos)

    def move_card(self, card_id: str, list_id: str) -> dict:
        return self._rpc("move_card", card_id, list_id)

    def archive_card(self, card_id: str) -> dict:
        return self._rpc("archive_card", card_id)

    def unarchive_card(self, card_id: str) -> dict:
        return self._rpc("unarchive_card", card_id)

    def update_card(self, card_id: str, **fields: Any) -> dict:
        return self._rpc("update_card", card_id, **fields)

    def grab_top_card(self, source_list_id: str,
                      dest_list_id: str) -> dict | None:
        # Atomic on the server (its backend's store lock / claim handshake),
        # which is the whole point: concurrent grabbers on different machines
        # each get a distinct card.
        return self._rpc("grab_top_card", source_list_id, dest_list_id)

    # ── Comments ─────────────────────────────────────────────────────

    def add_comment(self, card_id: str, text: str) -> dict:
        return self._rpc("add_comment", card_id, text)

    def get_comments(self, card_id: str, limit: int = 10) -> list[dict]:
        return self._rpc("get_comments", card_id, limit=limit)

    def update_comment(self, action_id: str, text: str) -> dict:
        return self._rpc("update_comment", action_id, text)

    def delete_comment(self, action_id: str) -> None:
        self._rpc("delete_comment", action_id)

    # ── Labels ───────────────────────────────────────────────────────

    def get_labels(self, board_id: str) -> list[dict]:
        return self._rpc("get_labels", board_id)

    def create_label(self, board_id: str, name: str, color: str | None = None) -> dict:
        return self._rpc("create_label", board_id, name, color=color)

    def update_label(self, label_id: str, **fields: Any) -> dict:
        return self._rpc("update_label", label_id, **fields)

    def delete_label(self, label_id: str) -> None:
        self._rpc("delete_label", label_id)

    def add_label_to_card(self, card_id: str, label_id: str) -> None:
        self._rpc("add_label_to_card", card_id, label_id)

    def remove_label_from_card(self, card_id: str, label_id: str) -> None:
        self._rpc("remove_label_from_card", card_id, label_id)

    # ── Members ──────────────────────────────────────────────────────

    def get_members(self, board_id: str) -> list[dict]:
        return self._rpc("get_members", board_id)

    # ── Activity ─────────────────────────────────────────────────────

    def get_activity(self, board_id: str, limit: int = 10) -> list[dict]:
        return self._rpc("get_activity", board_id, limit=limit)

    def get_actions_since(self, board_id: str, since: str,
                          action_types: str | None = None,
                          page: int = 1000) -> list[dict]:
        return self._rpc("get_actions_since", board_id, since,
                         action_types=action_types, page=page)

    # ── Checklists ───────────────────────────────────────────────────

    def get_checklists(self, card_id: str) -> list[dict]:
        return self._rpc("get_checklists", card_id)

    def create_checklist(self, card_id: str, name: str) -> dict:
        return self._rpc("create_checklist", card_id, name)

    def delete_checklist(self, checklist_id: str) -> None:
        self._rpc("delete_checklist", checklist_id)

    def rename_checklist(self, checklist_id: str, name: str) -> dict:
        return self._rpc("rename_checklist", checklist_id, name)

    def add_checkitem(self, checklist_id: str, name: str) -> dict:
        return self._rpc("add_checkitem", checklist_id, name)

    def delete_checkitem(self, checklist_id: str, item_id: str) -> None:
        self._rpc("delete_checkitem", checklist_id, item_id)

    def update_checkitem(self, card_id: str, item_id: str, **fields: Any) -> dict:
        return self._rpc("update_checkitem", card_id, item_id, **fields)

    # ── Attachments ──────────────────────────────────────────────────

    def get_attachments(self, card_id: str) -> list[dict]:
        return self._rpc("get_attachments", card_id)

    def add_attachment_url(self, card_id: str, url: str, name: str | None = None) -> dict:
        return self._rpc("add_attachment_url", card_id, url, name=name)

    def add_attachment_file(self, card_id: str, file_path: str,
                            name: str | None = None) -> dict:
        # The one op that can't ride the rpc channel: the file lives on THIS
        # machine, so it goes up as multipart to the browser's upload route.
        # The route returns the fresh card with the created attachment under
        # the transient `_attachment` key (see server.py); fall back to the
        # newest attachment for an older server that predates the key.
        import os

        with open(file_path, "rb") as fh:
            files = {"file": (os.path.basename(file_path), fh)}
            data = {"name": name} if name else {}
            resp = self._request(
                "POST", f"/api/cards/{card_id}/attachments/file",
                files=files, data=data,
            )
        card = resp.json()
        att = card.get("_attachment")
        if att is None:
            atts = self.get_attachments(card_id)
            att = atts[-1] if atts else {}
        return att

    def delete_attachment(self, card_id: str, attachment_id: str) -> None:
        self._request(
            "DELETE", f"/api/cards/{card_id}/attachments/{attachment_id}"
        )

    def download_attachment(self, url: str, dest: str, authed: bool = True) -> None:
        # A store-relative url (an upload living in the server's file store)
        # streams from the token-gated /api/blob route. An absolute url is an
        # external link attachment: fetch it directly, no token — mirroring
        # the local backend's split. `authed` is ignored beyond that split;
        # the server decides what needs its token.
        if url.lower().startswith(("http://", "https://")):
            try:
                with httpx.stream("GET", url, timeout=60,
                                  follow_redirects=True) as r:
                    r.raise_for_status()
                    with open(dest, "wb") as out:
                        for chunk in r.iter_bytes():
                            out.write(chunk)
            except httpx.HTTPError as e:
                raise SystemExit(f"Cannot download attachment {url}: {e}")
            return
        try:
            with self._client.stream("GET", "/api/blob",
                                     params={"url": url}) as r:
                if r.status_code >= 400:
                    r.read()
                    self._checked(r)
                with open(dest, "wb") as out:
                    for chunk in r.iter_bytes():
                        out.write(chunk)
        except httpx.HTTPError as e:
            raise SystemExit(f"Cannot reach trellno server at {self._base}: {e}")
