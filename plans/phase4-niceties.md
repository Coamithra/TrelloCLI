# Phase 4: Niceties — live refresh + export/import

Card: **Phase 4: Niceties** — `6a35439396b0154d631ca0d9`. Branch `feat/phase4-niceties`.

## Context

Phase 4 is the quality-of-life layer on top of the now-shipped web app (Phase 3)
and local backend (Phases 1–2). Three deliverables from the card:

1. **Live refresh** — file-watch (`watchdog`) on the local store root → SSE push so
   the browser kanban updates when Dropbox syncs a remote change (or another
   `--backend local` CLI command mutates the store).
2. **Export/import** — `trello export <board> --to local`: pull a board from the
   selected backend (default Trello) into the local file store. "Near-free since
   both backends share the entity shape."
3. **Remote-access docs** — Tailscale / reverse-proxy + token guidance; never the
   default. (The server already warns on non-loopback bind.)

## Design

### 1. Live refresh (local backend only)

Trello has no local files to watch; live refresh is a **local-backend** feature.
For the Trello backend the SSE stream stays open with keep-alives but never emits
a change (out of scope to poll Trello).

- **New module `trello_cli/web/live.py`** — encapsulates the watcher:
  - A module-level monotonic `_version` int + lock; a `watchdog` `Observer`
    schedules a `FileSystemEventHandler` whose `on_any_event` bumps `_version`.
  - `start_watching(root)` — idempotent (starts the observer once; no-op if the
    root doesn't exist yet or already watching). `get_version()` returns the count.
  - Bridging watchdog's background thread → the async SSE generator is done by a
    simple shared counter (atomic int read under a lock), polled by the generator.
    No `loop.call_soon_threadsafe` needed → robust and simple. Atomic writes
    (temp + `os.replace`) produce a burst of events; the 1 s poll naturally
    coalesces them into a single reload.
- **`server.py`** — add `GET /api/events` (async, `StreamingResponse`,
  `media_type="text/event-stream"`):
  - If backend is `local`, `live.start_watching(config.get_local_root())`.
  - Generator: remember last version; every 1 s, if version changed emit
    `event: change\ndata: {}\n\n`; otherwise emit a `: keep-alive` comment every
    ~15 s. `Cache-Control: no-cache`, `X-Accel-Buffering: no`.
  - Single-process uvicorn already (required for the in-process backend global), so
    an async long-lived endpoint is fine and doesn't tie up the sync threadpool.
- **`static/app.js`** — open `new EventSource('/api/events')` in `init()`; on a
  `change` event reload the current board (`loadBoard(picker.value)`), guarded by a
  `dragging` flag (set in Sortable `onStart`/`onEnd`) so a live reload never yanks a
  card mid-drag. EventSource auto-reconnects on drop. Status line shows "Updated".
- **`pyproject.toml`** — add `watchdog>=4` to the `[web]` extra (only needed at
  serve time).

### 2. Export: `trello export <board> --to local`

`--board` picks the **source** board; `--backend` picks the **source** backend
(default `trello`); the **target** is the local store at `--local-root` / config.
Source reads go through the `api` facade (the selected backend); the target is a
**directly instantiated** `LocalBackend(target_root)` — we need two backends at
once, so the export command can't rely on the single `get_backend()` singleton.

- **`LocalBackend.import_board(board, lists, labels, cards)`** (new, local-only —
  NOT on the `Backend` ABC; export explicitly targets local):
  - Writes `board.json`, `lists.json`, `labels.json`, and `cards/<id>.json` via the
    existing `LocalStore` + `atomic_write_json`, **preserving source ids** (both
    backends use 24-hex ids), so re-export overwrites in place (idempotent snapshot)
    and all cross-references (label ids, comment/checklist ids) stay valid.
  - Maps each Trello-shaped card → the on-disk store shape: `labels` (full dicts) →
    `idLabels` (ids); keep `comments` / `checklists` / `attachments` **inline**
    (already the store's shape); fill every field `_enrich_card` / commands read
    (idBoard, idList, pos, due, dueComplete, closed, dateLastActivity, …) so reads
    don't `KeyError`.
  - Appends one `importBoard` activity-log entry (so local `activity` reflects it).
  - Returns a summary dict (counts) for the CLI to print.
- **`main.py`** — `cmd_export(args)`:
  - `_parse_flags(args, value_flags=("--to",))`; require `--board`; `--to` defaults
    to `local`, error on any other value ("only --to local is supported").
  - Source reads: `api.get_board`, `api.get_lists`, `api.get_labels`,
    `api.get_board_cards(visible)` + `(closed)`, and per card `api.get_comments`,
    `api.get_checklists`, `api.get_attachments`.
  - Instantiate `LocalBackend(config.get_local_root())`, call `import_board(...)`,
    print `Exported '<name>' (<id>) -> <root>/<id>: N lists, M cards, K labels, …`.
  - Register in `COMMANDS` + add a `Web`/`Data` block to `USAGE`.

**Scope cut (proposed):** export **attachment metadata** only (inline, url
unchanged). Trello *uploaded* blobs keep their remote (auth-required) URL — they
won't be locally openable without auth. Reason: keeps export a pure shape-copy with
no network/auth failure modes in the export path; matches "near-free since both
share the entity shape." Blob-pull (download uploads into `attachments/<cardId>/`,
rewrite url root-relative) → **follow-up card**. URL attachments export fully.

**Direction:** `--to local` only. `--to trello` (reverse import, writes real cloud
objects) is out of scope → noted as future.

**Closed lists:** the facade exposes only open lists, so export covers open lists +
visible/closed cards. Minor documented limitation.

### 3. Remote-access docs

Doc-only. Add a "Remote access" section to `README.md`: prefer Tailscale (private
tailnet, no public exposure); or a reverse proxy (caddy/nginx) terminating TLS +
an auth token / basic-auth in front; never bind `0.0.0.0` unprotected. Cross-link
the server's existing non-loopback warning.

### Docs to update
- `README.md` — `export` command, live-refresh note, Remote-access section.
- `CLAUDE.md` — new `web/live.py` module, `export` command, `watchdog` dep,
  `import_board` local-only method.
- `DESIGN.md` — tick Phase 4 progress in the phase table (living roadmap).

## Tests (Phase 5)
- **Export** (source read-only, target = scratch local root): `trello --board 6a353ffc
  export --to local --local-root <scratch>`, then `trello --backend local --local-root
  <scratch> board` / `list ls` / `card ls "To Do"` (+ `--json`) to confirm lists,
  cards, labels, comments landed with correct shape. Re-run export → confirms
  idempotent overwrite.
- **Live refresh, automatable without a browser**: `trello --backend local
  --local-root <scratch> serve --no-browser` in the background; `curl -N
  http://127.0.0.1:8787/api/events` in another shell; mutate the store (`card add`)
  → observe an `event: change` line. Then a manual browser check that the board
  visibly reloads (flag for the user).
- **Trello backend unaffected**: `trello serve` still boots; `/api/events` streams
  keep-alives without errors.

## Out of scope
- Uploaded-attachment blob download on export (follow-up card).
- `--to trello` reverse import.
- Polling Trello for live changes (live refresh is local-store only).
- Closed-list export (facade exposes open lists only).
