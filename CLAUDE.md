# TrelloCLI

Compact Trello CLI tool — wraps the Trello REST API with concise output formatting.

## Management

- Trello board with tasks: 6a353ffc
- When implementing tickets from Trello, use CONTRIBUTING.md for workflow

## Architecture

- `trello_cli/config.py` — credentials + local-backend root, stored in `~/.trello-cli.json`. Board and backend *selection* are per-invocation only (flags/env), never persisted (see Statelessness)
- `trello_cli/api.py` — thin **facade**; each operation forwards to the active backend via `get_backend()`. `main.py`'s `api.<op>(...)` call sites stay unchanged
- `trello_cli/backends/` — pluggable data sources behind a common interface:
  - `base.py` — the `Backend` ABC: the ~40 operations the CLI needs, all returning Trello-shaped dicts
  - `trello.py` — `TrelloBackend`, the httpx client over the Trello REST API (requests only the fields each command needs)
  - `local.py` — `LocalBackend`, a self-hosted file store. Backs **every** CLI command: boards/lists/cards (CRUD + move/pos/archive/rename/desc/due), labels, checklists, comments, attachments, members, `card mine`, and `activity`/`updates` read from the log. Labels are stored on cards as `idLabels` and resolved to full dicts from `labels.json` at read time (so `label edit`/`delete` reflect everywhere); comments/checklists/attachments live inline in the card JSON; members are a single OS-username user, so `mine` returns every open card. Also `import_board(...)` — local-only (not on the ABC), the target of `trello export`: writes a source backend's Trello-shaped board/lists/labels/cards into the store, preserving ids and pruning cards deleted upstream (plus their attachment blob dirs). Two more local-only methods back maintenance: `gc(...)` (the `local gc` sweep — orphaned attachment dirs/files, optional `activity.log` trim) and `delete_board(...)` (the `local rm` board-folder delete); both report-then-delete so the command can offer a dry run
  - `store.py` — file-store primitives for `LocalBackend`: 24-hex id gen, atomic JSON/text writes (temp + `os.replace`), float `pos` midpoint math (step 65536), append-only `activity.log` (JSONL; written, read, and `tail_activity`-trimmed for retention), and the on-disk layout (`<root>/<boardId>/{board.json, lists.json, labels.json, cards/<id>.json, attachments/<cardId>/…, activity.log}`)
  - `__init__.py` — `get_backend()` factory (cached singleton); selects `trello` (default) or `local` from `--backend` / `TRELLO_BACKEND` (see DESIGN.md)
- `trello_cli/fmt.py` — compact table/detail formatting and small helpers (`short_id`, `truncate`, `due_str`, `label_str`, `is_image`, `size_str`, `print_json`); backend-agnostic (formats plain dicts)
- `trello_cli/main.py` — CLI entry point, noun-group dispatch (`card`, `list`, `label`, `checklist`, `comment`, `attachment`), name/ID prefix resolution. The `export` command pulls a board from the selected backend into the local store (reads via the `api` facade, writes via a directly-instantiated `LocalBackend` — two backends in one invocation, so it bypasses the `get_backend()` singleton)
- `trello_cli/web/` — optional web app (the `[web]` extra: fastapi, uvicorn, watchdog), launched by `trello serve`:
  - `server.py` — FastAPI JSON API mapping 1:1 onto the `api` facade, so it renders **either** backend with no per-backend code. Endpoints: `GET /api/boards`, `GET /api/boards/{id}` (board+lists+cards), `GET /api/cards/{id}` (detail+comments), `PATCH /api/cards/{id}` (drag move/reorder), `PATCH /api/lists/{id}` (column reorder), `POST /api/lists/{id}/cards` (create), `GET /api/events` (SSE live-refresh stream). Backend `SystemExit` (not-found) is translated to HTTP 404; mutating fields are whitelisted
  - `live.py` — live-refresh plumbing: a `watchdog` observer on the local store root bumps a monotonic version counter; the `GET /api/events` async generator polls it and emits `event: change` (a counter polled across the thread boundary, no event-loop hand-off). Local-backend only — Trello has no local files, so its stream is keep-alive only
  - `static/` — vanilla JS + SortableJS (vendored under `static/vendor/`, no build step): columns + cards, drag-drop reorder/move computing the float `pos` midpoint client-side, a read-only card detail panel, and an `EventSource` on `/api/events` that reloads the board on a `change` event (skipped mid-drag)

## Conventions

- **Noun-group dispatch** — `_dispatch(group, subcmds, args)` routes verbs within a group. Bare nouns (or nouns followed by a non-verb) fall back to `ls` if the group has one, so `trello list` ≡ `trello list ls`.
- **Resolvers** — every domain has a `_resolve_*` helper that accepts an ID, an ID prefix, or a case-insensitive name prefix, and raises `SystemExit` on miss/ambiguity.
- **Board scope** — `_require_board()` resolves the board from `--board <name_or_id>` (parsed in `main()`) or the `TRELLO_BOARD` env var, and errors if neither is set. There is no stored "active board" (see Statelessness).
- **Backend scope** — `get_backend_name()` selects the data source from `--backend <trello|local>` (parsed in `main()`) or the `TRELLO_BACKEND` env var, defaulting to `trello`. The local store folder resolves as `--local-root` flag > `TRELLO_LOCAL_ROOT` env > config `local_root` > `~/Dropbox/trello-cli`; `trello local init [path]` creates it and persists `local_root`. The `local` group's other verbs — `local gc` (stale-data sweep + temp-cache prune) and `local rm <board>` (delete a board folder) — are local-store maintenance: like `init`/`export` they operate on `get_local_root()` directly via a `LocalBackend(...)`, independent of `--backend` selection, and both default to a dry run (`--apply` / `--yes` to act).
- **Statelessness (design guideline)** — the CLI is used by many agents and projects concurrently, so it keeps **no shared mutable session state**. Selection (board, backend) is always per-invocation via flags/env; only stable config (credentials, `local_root`) is persisted. Don't add "active X" state — that creates cross-invocation conflicts. (The legacy active-board feature was removed for exactly this reason; everything uses `--board`.)
- **Output mode** — `--json` is stripped in `main()` and toggles `_JSON_MODE`; read commands branch on `_is_json()` to emit raw JSON via `print_json` instead of formatted tables.
- **Backend seam** — commands never touch a concrete backend or transport. They call `api.<op>(...)`, which forwards to `get_backend()`. Every backend returns the same Trello-shaped dicts (the keys `fmt.py` reads), so adding a backend means implementing the `Backend` ABC — no command or formatter changes.

## Install

```bash
pip install -e .          # local dev (editable)
pip install git+<url>     # from GitHub on another machine
```
