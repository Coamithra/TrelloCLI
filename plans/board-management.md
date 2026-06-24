# Plan: Board management (archive / restore / rename / purge)

## Context
Card **"Board management page"** (`bee5b6fa`): the user wants to archive boards (soft delete,
files kept — recoverable, with a "recycling bin"), rename boards, and "whatever else makes sense."
Confirmed scope: **CLI + web**, with a full **archive → restore → permanently delete** recycling bin.

Today boards already carry a `closed` flag (`get_boards` filters it out at `local.py:291`), but
*nothing ever sets it*. There is no `update_board` op anywhere. Hard delete exists only as the
local-only `delete_board` behind the CLI `local rm`. The web has no board-write path at all.

## Design

### Backend ABC (`backends/base.py`)
- New abstract `update_board(self, board_id, name=None, closed=None) -> dict` (after `create_board`).
- Widen `get_boards(self, include_closed: bool = False) -> list[dict]` (default keeps current behavior).

### LocalBackend (`backends/local.py`)
- `get_boards(include_closed=False)`: only drop the `not closed` filter when asked. Always carry `closed`.
- `update_board(board_id, name=None, closed=None)`: under the store lock — load board, set `name`/`closed`
  when provided, atomic write, `_log("updateBoard", ...)`, return `{id, name, shortUrl, desc, closed}`.
  Add `"update_board"` to `_MUTATORS`.
- `delete_board` already exists (the purge). No change.

### TrelloBackend (`backends/trello.py`)
- `get_boards(include_closed=False)`: `filter="all" if include_closed else "open"`.
- `update_board(board_id, name=None, closed=None)`: `PUT /boards/{id}` with `name` and/or
  `closed` ("true"/"false"); if neither given, GET the board. Best-effort (unverifiable here),
  mirroring the other Trello write paths.

### Facade (`api.py`)
- Forward `get_boards(include_closed=False)` and `update_board(board_id, name=None, closed=None)`.
- Add `delete_board(board_id, apply=False)` forwarding — but **guarded**: if the active backend has no
  `delete_board` (i.e. Trello), raise `SystemExit("Permanent board delete is only supported on the local backend.")`.
  Keeps purge local-only (as it already is) while giving the web route a facade entry to call.

### CLI (`main.py`)
- `boards` (`cmd_boards`): add `--archived` (closed only) and `--all` (open + closed) flags; default unchanged.
  In archived/all mode add an "Archived" marker column.
- `board` group (`cmd_board`): extend the hand-rolled dispatch (keep bare `board` → show, `board add`):
  - `board rename <new name…>` → `api.update_board(board, name=...)`
  - `board archive` → `api.update_board(board, closed=True)`
  - `board restore` → `api.update_board(board, closed=False)`
  - Permanent delete stays `local rm <board> --yes` (already exists; reference it in help).

### Web API (`web/server.py`)
- `GET /api/boards` accepts `?include_closed=true` (default false → current behavior).
- `PATCH /api/boards/{id}`: `_guard` against `_BOARD_PATCH_FIELDS = {"name", "closed"}` → `api.update_board`; returns fresh board.
- `DELETE /api/boards/{id}`: `api.delete_board(id, apply=True)` (purge); returns the report. Local-only via the facade guard.

### Web UI (`web/static/`)
- A **"Manage boards"** entry point in the topbar (a ⚙/"Boards…" button near the picker) opens an overlay
  **panel** (modeled on the existing popovers — no routing, no build step):
  - **Active boards**: each row = name (inline rename) + Archive.
  - **Archived**: each row = name + Restore + Permanently delete (behind a confirm).
  - Fetches `GET /api/boards?include_closed=true` and splits on `closed`.
- After a mutation: refresh `allBoards`, re-`renderNav()`; if the **current** board was archived/purged,
  `selectBoard` to the first remaining open board. Rename updates nav/picker labels live.

## Tests (Phase 5)
- Scratch board (`board add "scratch-bm"`): `board rename`, `board archive`, `boards --archived`, `boards --all`,
  `board restore`, then `local rm scratch-bm --yes`. Check `--json` on `boards`/`board`.
- Web: `trello serve` on the local backend, manually exercise the manage-boards panel (rename/archive/restore/purge),
  confirm nav refresh + current-board switch on archive. (Web drag/visuals need a manual browser check — flag it.)
- Trello backend writes (`update_board` PUT, `filter=all`) are unverifiable here — flag as needing a live board.

## Out of scope
- No "active board" state (statelessness). Board selection stays per-invocation.
- No board *create* changes (already exists). No board description editing (not requested) — can add if trivial.
- Cross-machine Dropbox concurrency stays last-write-wins (unchanged).
