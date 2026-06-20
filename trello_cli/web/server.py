"""FastAPI web app — a local drag-drop kanban over the Backend seam.

A thin JSON API mapping 1:1 onto the `api` facade, so it renders whichever
backend `--backend` selected (Trello or local) identically, plus a static
vanilla-JS + SortableJS frontend served from `static/`. Booted by `trello serve`.

Web deps are an optional extra (`pip install trello-cli[web]`); importing this
module requires them. See DESIGN.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import api, config
from . import live

STATIC_DIR = Path(__file__).parent / "static"

# The browser only moves/reorders cards and reorders columns, so the API accepts
# exactly those fields — nothing that could archive or rename via the raw
# endpoint. Widen these only alongside a matching UI control.
_CARD_PATCH_FIELDS = {"idList", "pos"}
_LIST_PATCH_FIELDS = {"pos"}

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_WILDCARD_HOSTS = {"0.0.0.0", "::", ""}


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


def create_app() -> FastAPI:
    app = FastAPI(title="TrelloCLI Web", docs_url=None, redoc_url=None)

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

    @app.get("/api/cards/{card_id}")
    def get_card(card_id: str) -> dict:
        card = _ok(api.get_card, card_id)
        return {**card, "comments": _ok(api.get_comments, card_id, limit=20)}

    @app.patch("/api/cards/{card_id}")
    def patch_card(card_id: str, fields: dict[str, Any]) -> dict:
        return _ok(api.update_card, card_id, **_guard(fields, _CARD_PATCH_FIELDS))

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
        watching = (
            live.start_watching(config.get_local_root())
            if config.get_backend_name() == "local"
            else False
        )

        async def gen() -> AsyncIterator[str]:
            yield ": connected\n\n"
            last = live.get_version()
            idle = 0
            while True:
                await asyncio.sleep(1.0)
                if watching:
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

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def serve(host: str = "127.0.0.1", port: int = 8787, open_browser: bool = True) -> None:
    """Boot the web server (blocking).

    Single-process — no uvicorn reload/workers — so the in-process `--backend`
    selection (a module global set by `main()`) stays valid for every request.
    Opens the browser shortly after start unless disabled. Binds 127.0.0.1 by
    default; a non-loopback host exposes the read/write API with no auth, so it
    warns loudly (put remote access behind a VPN / reverse proxy)."""
    import threading
    import webbrowser

    import uvicorn

    app = create_app()
    browse_host = "127.0.0.1" if host in _WILDCARD_HOSTS else host
    browse_url = f"http://{browse_host}:{port}/"
    if host not in _LOOPBACK_HOSTS:
        print(
            f"WARNING: binding {host!r} exposes this board on the network with NO "
            "authentication — anyone who can reach the port can read and edit it. "
            "Prefer 127.0.0.1 and reach it remotely via a VPN / reverse proxy."
        )
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(browse_url)).start()
    print(
        f"TrelloCLI web on {browse_url}  "
        f"(backend: {config.get_backend_name()})  — Ctrl-C to stop"
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
