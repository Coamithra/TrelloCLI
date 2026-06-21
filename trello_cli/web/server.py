"""FastAPI web app — a local drag-drop kanban over the Backend seam.

A thin JSON API mapping 1:1 onto the `api` facade, so it renders whichever
backend `--backend` selected (Trello or local) identically, plus a static
vanilla-JS + SortableJS frontend served from `static/`. Booted by `trello serve`.

Web deps are an optional extra (`pip install trello-cli[web]`); importing this
module requires them. See DESIGN.md.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import api, config
from . import live

STATIC_DIR = Path(__file__).parent / "static"

# The board and detail panel let the browser move/reorder cards, rename them,
# edit the description, and set/clear the due date; the board also archives a
# column (`closed`) and sets a column's persisted sort (`sort`). The API accepts
# exactly those fields — nothing that could (un)archive a card via the raw PATCH
# endpoint (card delete has its own DELETE route). Widen these only alongside a
# matching UI control.
_CARD_PATCH_FIELDS = {"idList", "pos", "name", "desc", "due", "dueComplete"}
_LIST_PATCH_FIELDS = {"pos", "closed", "sort"}

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_WILDCARD_HOSTS = {"0.0.0.0", "::", ""}


def _request_token(request: Request) -> str | None:
    """The token a request presents, via `Authorization: Bearer <t>` (clean for
    CLI/automation) or a `?token=<t>` query param (the only channel a browser's
    initial navigation and EventSource can use, since neither can set headers)."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip()
    return request.query_params.get("token")


def _guard(fields: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    out = {k: v for k, v in fields.items() if k in allowed}
    if not out:
        raise HTTPException(
            status_code=400, detail=f"No updatable fields. Allowed: {sorted(allowed)}"
        )
    return out


def _ok(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Call a backend op, translating its errors into HTTP responses.

    Backends raise SystemExit for not-found / bad input (Starlette won't catch a
    BaseException like SystemExit itself, so it would otherwise crash the
    worker); the Trello backend can also raise httpx errors for upstream
    failures, which become the upstream status (or 502) rather than an opaque
    500 with a stack trace."""
    try:
        return fn(*args, **kwargs)
    except SystemExit as e:
        raise HTTPException(status_code=404, detail=str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Upstream error: {e.response.text[:200]}",
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Backend request failed: {e}")


def create_app(token: str | None = None) -> FastAPI:
    app = FastAPI(title="Trellno Web", docs_url=None, redoc_url=None)

    if token:
        @app.middleware("http")
        async def require_token(request: Request, call_next: Any) -> Any:
            # Gate the data plane (`/api/*`, incl. the SSE stream) only — the
            # static shell (index.html/app.js/style.css) carries no board data,
            # so leaving it public lets the browser load app.js without a token,
            # which then supplies the token on every API call.
            if request.url.path.startswith("/api"):
                supplied = _request_token(request)
                # Compare as bytes: secrets.compare_digest rejects non-ASCII str
                # with a TypeError, which a hostile `?token=` could otherwise turn
                # into a 500 instead of a clean 401.
                if not supplied or not secrets.compare_digest(
                    supplied.encode(), token.encode()
                ):
                    return JSONResponse(
                        {"detail": "Unauthorized: missing or invalid token."},
                        status_code=401,
                    )
            return await call_next(request)

    # ── JSON API (1:1 with the api facade / Backend ABC) ─────────────

    @app.get("/api/boards")
    def list_boards() -> list[dict]:
        return _ok(api.get_boards)

    @app.get("/api/boards/{board_id}")
    def get_board(board_id: str) -> dict:
        return {
            "board": _ok(api.get_board, board_id),
            "lists": _ok(api.get_lists, board_id),
            "cards": _ok(api.get_board_cards, board_id),
        }

    @app.post("/api/boards/{board_id}/lists")
    def add_list(board_id: str, body: dict[str, Any]) -> dict:
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="List name is required.")
        # New columns land at the bottom (rightmost), matching Trello's
        # "Add another list" affordance.
        return _ok(api.create_list, board_id, name, pos="bottom")

    @app.get("/api/boards/{board_id}/labels")
    def list_labels(board_id: str) -> list[dict]:
        return _ok(api.get_labels, board_id)

    @app.post("/api/boards/{board_id}/labels")
    def create_label(board_id: str, body: dict[str, Any]) -> dict:
        name = (body.get("name") or "").strip()
        color = (body.get("color") or "").strip() or None
        if not name and not color:
            raise HTTPException(
                status_code=400, detail="A label name or color is required."
            )
        return _ok(api.create_label, board_id, name, color=color)

    @app.get("/api/cards/{card_id}")
    def get_card(card_id: str) -> dict:
        card = _ok(api.get_card, card_id)
        return {**card, "comments": _ok(api.get_comments, card_id, limit=20)}

    @app.patch("/api/cards/{card_id}")
    def patch_card(card_id: str, fields: dict[str, Any]) -> dict:
        return _ok(api.update_card, card_id, **_guard(fields, _CARD_PATCH_FIELDS))

    @app.delete("/api/cards/{card_id}")
    def delete_card(card_id: str) -> dict:
        # Soft delete (archive), matching the CLI's `card archive` — the card
        # drops out of the board's visible cards but stays recoverable.
        return _ok(api.archive_card, card_id)

    @app.post("/api/cards/{card_id}/comments")
    def add_comment(card_id: str, body: dict[str, Any]) -> dict:
        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="Comment text is required.")
        return _ok(api.add_comment, card_id, text)

    @app.post("/api/cards/{card_id}/labels")
    def add_card_label(card_id: str, body: dict[str, Any]) -> dict:
        label_id = (body.get("idLabel") or "").strip()
        if not label_id:
            raise HTTPException(status_code=400, detail="idLabel is required.")
        _ok(api.add_label_to_card, card_id, label_id)
        return _ok(api.get_card, card_id)

    @app.delete("/api/cards/{card_id}/labels/{label_id}")
    def remove_card_label(card_id: str, label_id: str) -> dict:
        _ok(api.remove_label_from_card, card_id, label_id)
        return _ok(api.get_card, card_id)

    @app.patch("/api/lists/{list_id}")
    def patch_list(list_id: str, fields: dict[str, Any]) -> dict:
        return _ok(api.update_list, list_id, **_guard(fields, _LIST_PATCH_FIELDS))

    @app.post("/api/lists/{list_id}/cards")
    def add_card(list_id: str, body: dict[str, Any]) -> dict:
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Card name is required.")
        # The composer only sends a name; new cards land at the bottom.
        return _ok(api.create_card, list_id, name, pos="bottom")

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        """Server-Sent Events stream powering live refresh.

        For the local backend, watch the store root and emit `event: change`
        whenever a file under it changes (a Dropbox sync, or another
        `--backend local` CLI mutation) so the browser reloads the board. The
        Trello backend has no local files, so its stream is keep-alive only.
        EventSource on the client auto-reconnects if the connection drops."""
        is_local = config.get_backend_name() == "local"

        async def gen() -> AsyncIterator[str]:
            # Runs until the client disconnects: Starlette cancels the task, which
            # raises CancelledError at the await below and ends the generator.
            yield ": connected\n\n"
            last = live.get_version()
            idle = 0
            while True:
                await asyncio.sleep(1.0)
                # Re-arm each tick (idempotent): if the store root didn't exist at
                # connect time, the watcher starts as soon as it appears.
                if is_local and live.start_watching(config.get_local_root()):
                    cur = live.get_version()
                    if cur != last:
                        last = cur
                        idle = 0
                        yield "event: change\ndata: {}\n\n"
                        continue
                idle += 1
                if idle >= 15:  # ~15s keep-alive holds the connection open
                    idle = 0
                    yield ": keep-alive\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Static frontend ──────────────────────────────────────────────

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/favicon.ico")
    def favicon() -> FileResponse:
        # index.html links the SVG icon directly, but browsers also probe
        # /favicon.ico unprompted; serve the same pink board glyph there so it
        # resolves instead of 404ing.
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def serve(host: str = "127.0.0.1", port: int = 8787, open_browser: bool = True,
          token: str | None = None) -> None:
    """Boot the web server (blocking).

    Single-process — no uvicorn reload/workers — so the in-process `--backend`
    selection (a module global set by `main()`) stays valid for every request.
    Opens the browser shortly after start unless disabled. Binds 127.0.0.1 by
    default. A non-loopback bind exposes the read/write API to the network, so it
    is token-gated: if no `token` is given for such a bind one is auto-generated,
    and the token is required (Bearer header or `?token=`) on every API request.
    Loopback stays token-free unless a token is passed explicitly."""
    import threading
    import webbrowser

    import uvicorn

    is_loopback = host in _LOOPBACK_HOSTS
    if not is_loopback and not token:
        token = secrets.token_urlsafe(16)

    app = create_app(token=token)
    browse_host = "127.0.0.1" if host in _WILDCARD_HOSTS else host
    browse_url = f"http://{browse_host}:{port}/"
    if token:
        browse_url += f"?token={token}"
    if not is_loopback:
        print(
            f"Binding {host!r} exposes this board on the network — access is "
            "token-gated; the token below is required on every API request "
            "(put remote access behind a VPN / reverse proxy too). Token: "
            f"{token}"
        )
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(browse_url)).start()
    print(
        f"Trellno web on {browse_url}  "
        f"(backend: {config.get_backend_name()})  — Ctrl-C to stop"
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
