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
  half-written file.
- **Activity log**: every mutating op appends a JSONL line -> gives
  `activity` / `updates` a real local equivalent, plus a free audit trail
  (diff-friendly if the folder is also a git repo).
- **Attachments** are simpler locally than on Trello — `attachment view/open/
  download` just resolve a local path, no auth fetch.
- **Members / `card mine`**: single-user model — `get_members` returns one local
  user (default = OS username); `mine` returns cards tagged to it. This is where
  "parity with the CLI, not Trello" lets us stub lightly.

Conflict model: last-write-wins with per-card granularity. Genuine simultaneous
two-machine edits produce a Dropbox "conflicted copy" the user resolves manually.
Documented limitation — not a real-time collab tool.

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

## Delivery phases

| Phase | What ships | User-visible? |
|------|-----------|---------------|
| **0 - Seam** | Extract `Backend` ABC; move Trello code to `backends/trello.py`; route through `get_backend()` | No (pure refactor, Trello still works) |
| **1 - Local core** | File store, boards/lists/cards CRUD + move/pos/archive/rename/desc/due, `local init`, `--backend` | `trello --backend local ...` = working file-backed kanban via existing CLI |
| **2 - Local parity** | labels, checklists, comments, attachments (blobs), activity/updates from the log, single-user `mine` | Local backend backs *every* CLI command |
| **3 - Web app** | FastAPI + JSON API + vanilla-JS drag-drop board + `trello serve` (works for both backends) | The browser kanban **(delivered)** |
| **4 - Niceties** | Live refresh (file-watch -> SSE) when Dropbox syncs a change; `trello export <board> --to local` to pull Trello boards into files | Quality-of-life |

The **export/import** bonus (Phase 4) falls out almost for free since both
backends share the entity shape.

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
