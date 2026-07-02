# Dual-Backend Kanban — Design

Status: **Phases 0-3 implemented** (Trello + local backends with full CLI parity,
plus the drag-drop web app). **Phase 4 (niceties) remaining.**

Expand TrelloCLI from a Trello-only CLI into a tool with two interchangeable
backends — **Trello** (the current REST client) and a **self-hosted file store**
(JSON files in a Dropbox-synced folder) — plus a **local web app** that renders a
drag-and-drop kanban board against either backend.

Scope target (decided): the local backend aims for **parity with the CLI**, not
with Trello itself. The CLI's command surface *is* the spec. Trello-only concepts
(real members, a hosted activity feed) get lightweight local equivalents only where
a command needs them.

Sharing/security model: **Dropbox**. The app is local; Dropbox syncs the folder.

---

## Core idea

Today `main.py` calls `api.*` directly and `api.py` is hardwired to Trello. The
whole expansion hinges on one move: **insert a `Backend` interface between the
commands and the data source.** The file store and the web app both plug into it.

Two facts make this cheap:

- **`fmt.py` is already backend-agnostic** — it formats plain dicts keyed by
  Trello-ish names (`id`, `name`, `idList`, `pos`, `labels`, `checkItems`,
  `state`, ...). If the local backend returns the *same dict shape*, all
  formatting and most command logic work untouched.
- **The interface = the CLI's needs.** The `Backend` ABC is exactly the ~40
  operations the commands invoke — nothing more.

---

## Architecture

```
trello_cli/
  main.py          # CLI dispatch - unchanged logic, calls get_backend() instead of api
  config.py        # + backend selection (trello|local), local_root path
  fmt.py           # UNCHANGED (already dict-shaped)
  backends/
    __init__.py    # get_backend(name) factory + selection logic
    base.py        # Backend ABC - the contract both implement
    trello.py      # TrelloBackend  (today's api.py httpx code moves here)
    local.py       # LocalBackend   (file store)
    store.py       # atomic file I/O, 24-hex id gen, pos math, activity log
  web/
    server.py      # FastAPI app (optional extra) - thin JSON API over a Backend
    static/        # index.html + app.js + style.css (vanilla JS + SortableJS, no build step)
```

`api.py` becomes a thin **facade** forwarding to the active backend
(`api.get_lists(...) -> get_backend().get_lists(...)`), so `main.py`'s ~60 call
sites barely change in Phase 0 — pure refactor, zero behavior change.

### Backend contract (sketch)

```python
class Backend(Protocol):
    # boards
    def get_boards(self) -> list[dict]: ...
    def create_board(self, name, desc=None, default_lists=True) -> dict: ...
    # lists
    def get_lists(self, board_id) -> list[dict]: ...
    def create_list(self, board_id, name, pos=None) -> dict: ...
    def update_list(self, list_id, **fields) -> dict: ...
    # cards
    def get_card(self, card_id) -> dict: ...
    def create_card(self, list_id, name, desc=None, due=None, pos="top") -> dict: ...
    def update_card(self, card_id, **fields) -> dict:  # name/desc/due/pos/idList/idBoard/closed
        ...
    # labels / checklists / comments / attachments / activity ...
```

Both backends return Trello-shaped dicts. The local backend generates **24-char
hex IDs** (so `short_id` and the ID-prefix resolvers behave identically) and uses
**float `pos`** (so the `card pos` / `list pos` midpoint logic works unchanged).

---

## File store (local backend)

Per-card files, not one big JSON — the key Dropbox decision: editing one card
rewrites only that card's file, so conflict scope stays tiny and isolated.

```
<root>/                         # default ~/Dropbox/trello-cli  (configurable)
  <boardId>/
    board.json                  # {id, name, desc, closed}
    lists.json                  # ordered [{id, name, pos, closed}]  (small, structural)
    labels.json                 # [{id, name, color}]
    cards/<cardId>.json         # full card: name, desc, pos, due, idList,
                                #   labels[], checklists[], comments[], attachments[]
    attachments/<cardId>/...    # uploaded file blobs (URL attachments just store the url)
    activity.log                # append-only JSONL - powers `activity` / `updates`
```

- **Comments & checklists live inline** in the card JSON (matches Trello's
  `get_card(checklists=all)` shape; everything for a card in one file).
- **Atomic writes** (temp file + `os.replace`) so Dropbox never syncs a
  half-written file. Atomicity stops *torn reads*, not *lost writes* — for that
  see the store lock below.
- **Activity log**: every mutating op appends a JSONL line -> gives
  `activity` / `updates` a real local equivalent, plus a free audit trail
  (diff-friendly if the folder is also a git repo).
- **Attachments** are simpler locally than on Trello — `attachment view/open/
  download` just resolve a local path, no auth fetch.
- **Members / `card mine`**: single-user model — `get_members` returns one local
  user (default = OS username); `mine` returns cards tagged to it. This is where
  "parity with the CLI, not Trello" lets us stub lightly.

Concurrency (same machine): the CLI is run by many agents at once, so every
mutator — a read-modify-write over a whole file — is serialized behind a
**store lock** (`StoreLock` in `store.py`). It's a cross-process OS advisory lock
on `<root>/.lock` (`fcntl.flock` / `msvcrt.locking`, auto-released if the holder
dies) plus an in-process re-entrant `threading.RLock`, acquired around the whole
load→modify→save with a bounded blocking wait. Without it, concurrent writers lose
updates (the second save clobbers the first) and concurrent inserts compute
colliding `pos` values — and on Windows the racing `os.replace` calls outright
crash with `PermissionError`. A file lock (not a DB) keeps the human-readable,
Dropbox-friendly per-file layout intact. Reads stay lock-free — atomic writes
already give each file a consistent point-in-time view.

Conflict model (cross machine): last-write-wins with per-card granularity. OS
locks don't cross machines, so genuine simultaneous two-machine edits still
produce a Dropbox "conflicted copy" the user resolves manually — a documented
limitation; this is not a real-time collab tool.

---

## Backend selection

Mirrors the existing per-invocation `--board` / `TRELLO_BOARD` pattern, adding a
backend dimension. **Selection is stateless** — the CLI is used by many agents and
projects concurrently, so nothing about *which* board or backend is persisted
(that would be shared mutable state and cause cross-invocation conflicts):

- Backend is chosen per-invocation: `--backend trello|local` (parsed in `main()`
  alongside `--board` / `--json`) or the `TRELLO_BACKEND` env var. Default `trello`.
  **No persisted "default backend".**
- `~/.trello-cli.json` persists only stable config: credentials and
  `"local_root": "<path>"` (a data location, like a credential — not selection state).
  `TRELLO_LOCAL_ROOT` overrides it per-invocation.
- `trello local init [path]` sets up the root (default `~/Dropbox/trello-cli`) and
  records `local_root`; `trello configure` stays for Trello creds.
- **No "active board".** The legacy active-board state was removed; board scope is
  always `--board` / `TRELLO_BOARD`. The resolvers operate within the selected backend.

---

## Web app

A local **FastAPI** server talking to the *same* `Backend` interface — so it
renders **both** local and Trello boards for free.

- **API**: small JSON endpoints mapping 1:1 to backend methods
  (`GET /api/boards/{id}` -> lists+cards; `PATCH /api/cards/{id}` -> move/pos/
  rename/etc.).
- **Frontend**: **vanilla JS + SortableJS** served as static files — *no build
  step*. Columns + cards, drag-drop to reorder/move (computes float `pos` via the
  same midpoint rule), click a card for a detail panel (desc, due, labels,
  checklist, comments).
- **Launch**: `trello serve [--backend local] [--port 8787]` boots the server and
  opens the browser. Binds `127.0.0.1` by default (local-only); remote access is a
  documented opt-in (Tailscale / reverse proxy + token), never the default.
- **Dependency hygiene**: web deps go in an optional extra
  (`pip install trello-cli[web]`) so the core CLI stays httpx-only.

---

## HTTP backend — a hosted trellno as the canonical store

The deployment story for "one board, many machines/agents" (including Claude
cloud sessions, which can't reach a Dropbox folder): run `trello serve` on a
server over its own local file store, and point every client at it with
`--backend http`. The server becomes the **single source of truth**, and
because its store lock lives on one machine, every write — including
`grab_top_card` — is truly atomic for *all* clients; the Dropbox cross-machine
last-write-wins caveat disappears for boards that move there.

- **Transport, not a store**: `backends/http.py` implements the `Backend` ABC
  by calling the web app's API; it holds no state. Two channels:
  - `POST /api/rpc` — the ABC serialized as `{"op", "args", "kwargs"}` →
    `{"result": ...}`. The op whitelist is **derived from the ABC's abstract
    methods** (minus the two file-transfer ops), so a new backend op is served
    the moment it's added — no per-op route to write, no drift. The REST
    routes remain the *browser's* contract; rpc is the *CLI's*. Local-only
    maintenance ops (`import_board`, `gc`, `delete_board`) are not exposed.
  - File transfer — the only ops where a client-side path is meaningless
    remotely: `add_attachment_file` posts multipart to the browser's upload
    route (which returns the created attachment under a transient
    `_attachment` key), and `download_attachment` streams store-relative blob
    urls from `GET /api/blob` (absolute/external urls are refused there — no
    SSRF — and fetched directly by the client instead).
- **Errors**: the server maps backend `SystemExit` to 4xx + `detail`; the
  http backend maps any non-2xx back to `SystemExit(detail)` — remote errors
  read exactly like native CLI errors, and `_resolve_*` keeps working since
  "not found" messages round-trip.
- **Selection/config**: `--backend http` / `TRELLO_BACKEND=http`. The server
  location is stable config like credentials: `trello configure-http <url>
  [<token>]` persists `server_url`/`server_token`; `TRELLO_SERVER` /
  `TRELLO_SERVER_TOKEN` / `--server` override per-invocation. Statelessness
  holds — no selection is persisted, only the data location.
- **Deployment** (see `deploy/`): systemd runs `serve --host 127.0.0.1
  --token <t> --allow-host <domain>` behind Caddy (TLS + reverse proxy).
  `--allow-host` extends the Host-header allow-list to the proxied public
  domain, keeping the DNS-rebinding guard strict for everything else. The
  token gates `/api/*` as before; the loopback bind means only the proxy can
  reach uvicorn.
- **Recursion guard, by convention**: the server must not itself run
  `--backend http` (a self-loop). Its systemd env pins `TRELLO_BACKEND=local`.

---

## Delivery phases

| Phase | What ships | User-visible? |
|------|-----------|---------------|
| **0 - Seam** | Extract `Backend` ABC; move Trello code to `backends/trello.py`; route through `get_backend()` | No (pure refactor, Trello still works) |
| **1 - Local core** | File store, boards/lists/cards CRUD + move/pos/archive/rename/desc/due, `local init`, `--backend` | `trello --backend local ...` = working file-backed kanban via existing CLI |
| **2 - Local parity** | labels, checklists, comments, attachments (blobs), activity/updates from the log, single-user `mine` | Local backend backs *every* CLI command |
| **3 - Web app** | FastAPI + JSON API + vanilla-JS drag-drop board + `trello serve` (works for both backends) | The browser kanban **(delivered)** |
| **4 - Niceties** | Live refresh (file-watch -> SSE) when Dropbox syncs a change; `trello export <board> --to local` to pull Trello boards into files | Quality-of-life **(delivered)** — export downloads uploaded-attachment blobs by default (`--no-attachments` to skip) and supports both `--to local` (pull) and `--to trello` (push a local board up as a brand-new board) |

The **export/import** bonus (Phase 4) falls out almost for free since both
backends share the entity shape.

### `export --to trello` (reverse import) — create-new-each-time

The reverse pushes the local store *up* to Trello. The asymmetry vs `--to local`:
Trello mints its own ids, so ids **cannot** be preserved and the idempotent
in-place refresh model doesn't apply. The chosen model is **create-new-each-time**:
each run creates a brand-new board (old→new id maps for labels/lists are built as
they're created; cards and their children — comments, checklists+items,
attachments — are re-created under the new ids). This keeps **statelessness** — no
`local→trello` id map is persisted anywhere. Necessary lossy bits: comments
re-post as the token user with a fresh timestamp (provenance folded into the body),
board members aren't mapped, and only open lists are pushed.

### `export --to trello --into <board>` (tracked-mapping re-sync) — DESIGN + RECOMMENDATION

> **Status: designed, NOT implemented — needs a product decision.** This section is
> the first deliverable of card `6a366ff2` (the re-sync follow-up to the shipped
> create-new-each-time model). It works through the full design — id-map storage,
> the diff/reconcile algorithm, and conflict handling — and ends with an explicit
> recommendation: **do not build it yet.** The reasoning is at the bottom. Nothing
> below has shipped; today's only `--to trello` mode is still create-new-each-time.
>
> **DECISION (2026-06-21, user): not building this.** Continuous / tracked re-sync
> into an existing Trello board is explicitly not wanted; one-off create-new-each-time
> `export --to trello` covers the need. The design below is retained as a record only,
> should the decision ever be revisited. Card `6a366ff2` is closed (Done) on this basis.

The deferred alternative to create-new-each-time is a **re-sync**: instead of a
fresh board every run, `export --to trello --into <board_id>` would push the local
store into an *existing* Trello board, updating in place. To find each local item's
counterpart across runs you must persist a `local→trello` id map — which
reintroduces cross-invocation state and a reconciliation engine. Here is exactly
what that would take.

#### (a) Id-map storage — a per-board, opt-in, local-only sidecar

The map lives **next to the source data**, one file per local board, written only
when `--into` is used:

```
<root>/<localBoardId>/sync/<trelloBoardId>.json
```

```jsonc
{
  "schemaVersion": 1,
  "localBoardId": "6a35…",          // source of truth (the file store board)
  "trelloBoardId": "abc123…",       // the --into target
  "lastSyncedAt": "2026-06-21T…Z",
  "tokenUserId": "5f…",             // whose token pushed last (provenance)
  "labels": { "<localLabelId>": "<trelloLabelId>", … },
  "lists":  { "<localListId>":  "<trelloListId>",  … },
  "cards":  {
    "<localCardId>": {
      "trelloId": "<trelloCardId>",
      "checklists": { "<localClId>": "<trelloClId>",
                      "items": { "<localItemId>": "<trelloItemId>" } },
      // attachments/comments deliberately NOT mapped — see reconcile notes
      "baseline": { "name":"…","desc":"…","due":"…","dueComplete":false,
                    "idList":"<localListId>","pos":1.0,"closed":false,
                    "labels":["<localLabelId>"],
                    "checklistsHash":"…" }   // last-pushed local content (3-way merge)
    }, …
  }
}
```

Keying the filename by `<trelloBoardId>` lets one local board track several Trello
boards (a personal copy + a shared copy) without collision. The sidecar is reused
across machines via the same Dropbox folder as the rest of the store.

**Why this is compatible with the Statelessness guideline (qualified).** The
guideline forbids *shared mutable selection state* — an "active board/backend" that
silently changes what a *different* invocation sees. The sync map is a different
category: it is **data tied to a specific source board**, like `local_root` or a
credential, not selection. It changes nothing about which board/backend any other
command resolves; it is read/written *only* on an explicit `--into` run; and absent
`--into` the tool behaves exactly as today. So it is opt-in, per-board, local-only
state — admissible under the letter of the guideline. **But** it is still
cross-invocation state with real failure modes (staleness, conflicted Dropbox
copies of the sidecar itself, a half-written map after a mid-push crash), which is
the spirit the guideline is trying to avoid. That tension is the crux of the
recommendation below.

#### (b) Diff-and-reconcile algorithm

Each entity class is reconciled by id via the map, in dependency order. All writes
go through the existing `Backend` ABC ops — no new transport.

1. **Gather both sides.** Local snapshot via the shared `_gather_board` helper;
   current Trello state via `dest.get_lists / get_labels / get_board_cards
   (visible+closed) / get_card / get_checklists / get_comments / get_attachments`.
2. **Labels.** For each local label: mapped → `update_label` if name/color drifted;
   unmapped → `create_label`, record id. Local labels whose mapped Trello label
   vanished → recreate. Trello labels with no local origin → leave (additive) or
   `delete_label` under a `--prune` flag.
3. **Lists.** Same shape with `create_list` / `update_list` (name) /
   `update_list(pos=…)` for reordering / `archive_list` for lists removed locally.
4. **Cards** (the bulk). For each local card:
   - **Unmapped** → `create_card` (+ children, exactly as `_push_card` does today),
     record the new id and the checklist/item sub-map.
   - **Mapped & present on Trello** → field-by-field `update_card` for
     name/desc/due/dueComplete/idList(move)/pos(reorder); add/remove `idLabels` via
     `add_label_to_card`/`remove_label_from_card` against the mapped label ids;
     `archive_card`/`unarchive_card` on `closed` drift.
   - **Mapped but gone on Trello** (deleted in the UI) → recreate and remap (or skip
     under a policy flag).
   - **Children**: checklists/items reconciled by sub-map (create/rename/delete,
     check/uncheck). **Comments and attachments stay append-only / create-each-time**
     — Trello can't preserve comment author/date anyway (today's provenance prefix),
     and re-diffing free-text comments is not worth a content hash; re-syncing them
     would either duplicate or require a comment-id map that Trello mutates. So
     comments are intentionally *not* reconciled (documented lossy bit, same spirit
     as create-new-each-time).
5. **Removed-upstream (local deletions).** Local card present in the map but absent
   from the current local snapshot → `archive_card` on Trello by default
   (`--prune` to hard-`delete` — but the ABC has no card-delete; Trello's is
   `DELETE /cards/{id}`, which would be a new backend op). Drop it from the map.
6. **Positions.** Reordering uses the same numeric `pos` push (`_pos_str`) the
   create path already uses, applied via `update_card(pos=…)` / `update_list(pos=…)`.
7. **Persist the map** atomically (temp + `os.replace`, like the rest of the store)
   only after the push succeeds, stamping `lastSyncedAt` and refreshing every
   `baseline`.

#### (c) Conflict handling — the genuinely hard part

Trello is independently editable between syncs. With only an id map you cannot tell
*who* changed a field, so a naive re-sync is **last-write-wins with extra steps** —
it silently clobbers Trello-side edits. Doing it *safely* needs a **three-way
merge** using the `baseline` (last-pushed local content) stored in the map:

| local vs baseline | trello vs baseline | action |
|---|---|---|
| unchanged | unchanged | nothing |
| changed | unchanged | push local (the intended case) |
| unchanged | changed | **keep Trello** (don't clobber a UI edit) |
| changed | changed (same value) | nothing |
| changed | changed (diff value) | **conflict** → policy |

Conflict **policy** options, smallest-surface first: (1) **`--on-conflict=skip`**
(default) — warn, leave Trello as-is, don't update the baseline so the next run
re-surfaces it; (2) **`--on-conflict=local`** — local wins (the blunt "I know what
I'm doing" mode, ≈ today's overwrite); (3) **`--on-conflict=trello`** — Trello
wins, pull the value back into local. Deletions are their own conflict axis (local
deleted vs Trello edited). All of this assumes the sidecar baseline survived; a lost
or conflicted-copy sidecar forces a cold "adopt" pass (match by name within a list,
ambiguous → bail).

#### Recommendation: **defer — do not implement yet** (needs a user decision)

1. **The hard 80% is a product decision, not an engineering one.** The id-map
   plumbing and the create/update/archive/reorder reconcile are mechanical. The
   *value* of the feature lives entirely in conflict handling, and the right policy
   (skip vs local-wins vs trello-wins, and whether to store a content baseline at
   all) depends on how the user actually intends to use it — as a one-way
   "publish my local board to Trello and keep it fresh" (baseline optional,
   local-wins acceptable) or a genuine two-way-aware sync (baseline mandatory, much
   bigger). Building the wrong half is worse than not building it.
2. **It cannot be verified end-to-end here.** Live Trello is off-limits (free
   workspace at the 10-board limit) and there is no committed test harness — the
   create-new-each-time model itself shipped "verified offline only". The reconcile
   *logic* could be unit-tested against a fake/local target, but the parts that
   actually bite (fresh-id minting, comment/checklist non-idempotency, rate limits,
   real UI drift) only show up against live Trello. Shipping an unverifiable
   *mutating* path that can silently clobber a user's real Trello board is the
   highest-risk change in this codebase.
3. **Statelessness cost is real even if admissible.** A persisted, Dropbox-synced,
   crash-sensitive sidecar is exactly the kind of cross-invocation state the project
   has worked to avoid; a stale/corrupt map mis-targets *live mutations*. Worth it
   only if the user genuinely needs in-place re-sync — which the shipped
   create-new-each-time model already substitutes for in the common "snapshot my
   local board to Trello" case.

**Net:** the design is ready to build behind a clean, opt-in `--into <board_id>`
flag (default behavior unchanged) the moment the user confirms (i) they want it and
(ii) the conflict policy. Until then it stays deferred — implementing now would mean
shipping an unverifiable, board-clobbering write path on a guessed policy. Tracked
by card `6a366ff2`, left open for that decision.

---

## Risks / open decisions

- **Dropbox conflicts** on multi-machine simultaneous edits -> mitigated by
  per-card files + atomic writes; documented as last-write-wins.
- **Web exposure**: local-only by default; remote is opt-in with a token. Don't
  want a kanban with personal data on `0.0.0.0` by accident.
- **Optional rename**: package/command is `trello` but it's now backend-agnostic.
  Keep `trello` for muscle memory (maybe add a neutral alias later) — cosmetic.
- **Field-contract discipline**: the local backend must populate every field
  `fmt.py` reads (even as empty) or commands `KeyError`. The ABC pins this.

---

## Starting point

**Phase 0** (the backend seam) — a no-risk refactor that unblocks everything and
leaves Trello behavior identical, verifiable by re-running the existing command
surface against real Trello boards.
