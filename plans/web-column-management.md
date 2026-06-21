# Plan: Web column management (add/delete columns + persisted per-column sort)

Two related web-UI features shipped on one branch (`feat/web-column-management`):

- **Card 6a373389** — Web UI: add and delete columns (lists).
- **Card 6a37338b** — Web UI: per-column sort (newest/oldest/name) with PERSISTED auto-sort.

## Context

The web kanban (`trello_cli/web/`) can already drag cards and reorder columns, add
cards, and read card detail — but it cannot create or remove a column, and there is
no sorting affordance at all. These two cards add a Trello-style "Add another list"
control + a per-column actions menu (delete) and a per-column sort menu whose chosen
sort is *persisted* and re-applied to every new card (the feature that beats real
Trello, which only one-shot sorts).

## Card 1: Add & delete columns

### Backend / API (`server.py`)
The local + trello backends already implement `create_list`, `archive_list`,
`rename_list`, `update_list`. Only the HTTP surface is missing.

- **Create:** new `POST /api/boards/{board_id}/lists` (board-scoped, mirrors the
  list-scoped `POST /api/lists/{list_id}/cards`). Body `{name}`; rejects empty name
  with 400. New list lands at the bottom (`pos="bottom"`) so it appears to the right,
  matching Trello's "Add another list".
- **Delete (archive):** widen `_LIST_PATCH_FIELDS` from `{"pos"}` to
  `{"pos", "closed"}` and let the existing `PATCH /api/lists/{id}` carry
  `{closed: true}`. This matches how the app already archives nothing-but-mirrors the
  card model (cards have no DELETE endpoint either — archive is the delete). No new
  endpoint needed; the whitelist widening is the entire change. `archive_list`/
  `update_list(closed=True)` already exist on both backends.

### Frontend (`app.js` / `style.css` / `index.html`)
- **Add-list affordance:** a trailing `.add-list` element after the last column in
  `renderBoard`. Click → inline input; Enter/blur creates via `POST
  /api/boards/{id}/lists`, then reload the board.
- **Column actions menu:** a `⋯` button in `.column-header`; clicking opens a small
  menu with **Delete list**. Confirm, then `PATCH /api/lists/{id}` `{closed:true}`
  and reload. (Stops drag propagation so the menu button doesn't start a column drag.)

## Card 2: Persisted per-column sort

### Where the local backend stores it — decision (a)
Add an **optional `sort` field** to each list dict in `lists.json`:
`"sort": "manual" | "newest" | "oldest" | "name"`. Default `"manual"`.

- Populated everywhere a list is produced: `create_list`, `create_board`'s default
  lists, `import_board`, and **defaulted on read** in `get_lists` (`l.setdefault("sort","manual")`)
  so pre-existing stores (lists.json with no `sort` key) never KeyError and always
  surface a value to the UI. Trello-shaped contract stays intact: `sort` is additive
  and optional; `fmt.py` and CLI list commands ignore it (they only read `id`/`name`/`pos`).
- `update_list` accepts a `sort` field (validated against the allowed set; bad value
  → SystemExit so the web 400/404 path stays clean).

### Trello backend behavior — decision (b)
Trello's REST API has **no native per-list sort field**. So per-column persisted sort
is a **local-backend-only feature**. The trello `update_list` forwards `**fields` to
the Trello API, which silently drops unknown fields, so a `sort` PATCH against a Trello
board is a **no-op** (not an error) — and `get_lists` on Trello never returns a `sort`
key, so the UI shows the default ("manual") and apply-on-create does nothing. Documented
as local-only in CLAUDE.md / README.md. No attempt to emulate it via Trello's
client-side board prefs (out of scope, and they aren't a server-side per-list sort).

### Apply-on-create — the auto-sort
In the local `create_card`, after resolving the board/list, look up the list's `sort`:
- `manual` → current behavior (honour the requested `pos`).
- `newest`/`oldest`/`name` → ignore the requested `pos` and compute the correct insertion
  `pos` so the new card lands in its sorted slot among the list's existing open cards:
  - `name` → alphabetical (case-insensitive) by `name`.
  - `newest` → most-recent `dateLastActivity` first (new card is newest → top).
  - `oldest` → oldest first (new card is newest → bottom).
  The web `POST /api/lists/{id}/cards` already sends `pos="bottom"`; the backend overrides
  it per the saved sort. CLI `card add` is likewise auto-placed (consistent — the sort is a
  property of the list, not the UI).

  Insertion is computed by finding the neighbours the card sorts between and taking the
  float midpoint (same `pos` model as everywhere else), then the usual rebalance guard runs.

### Manual drag vs saved sort — decision (c)
**A manual drag/drop of a card CLEARS the destination list's saved sort** (resets it to
`manual`). Rationale: if the user hand-places a card, an auto-sort would immediately fight
them by re-placing the next card elsewhere; the least-surprising behavior is "you took
manual control of this column, so it's manual now." Implemented in the web layer: the card
move/reorder PATCH path (`onEnd`) issues a `PATCH /api/lists/{destList}` `{sort:"manual"}`
when the destination list had a non-manual sort, then the existing reload refreshes the
menu state. (Doing it web-side keeps the backend `update_card` free of cross-entity list
mutation and keeps CLI `card pos`/`card move` from silently clearing a sort — the sort is a
web-interaction concept; a CLI move is explicit positioning the user already controls.)

Re-selecting a sort from the menu re-sorts the whole column immediately (one-shot reorder of
existing cards) AND persists the setting so future adds stay sorted.

### Frontend (`app.js`)
- A sort control in `.column-header` (a `<select>` or a menu) with options
  Manual / Newest / Oldest / Name, initialised from `list.sort`.
- On change: `PATCH /api/lists/{id}` `{sort}`, then **immediately re-sort existing cards**
  (compute each card's new `pos` client-side bottom-to-top, or simpler: reload the board
  after a backend re-sort). Chosen approach: backend `update_list(sort=…)` only persists the
  setting; the one-shot reorder of *existing* cards is done by the web client issuing card
  pos PATCHes, OR — simpler and consistent with rebalance reloads — the client computes the
  sorted order and PATCHes each card's pos, then reloads. To keep it simple and robust, the
  one-shot reorder is performed client-side by sorting the current DOM cards and PATCHing new
  positions, then reload. (If this proves heavy, fall back to a backend "resort list" — but
  that adds a method; client-side keeps server surface minimal.)

## Tests (Phase 5)
Scratch board on local backend; own server on :8802.
- `POST /api/boards/{id}/lists` creates a list (appears, has `sort:"manual"`).
- `PATCH /api/lists/{id}` `{closed:true}` archives it (gone from `get_lists`).
- `PATCH /api/lists/{id}` `{sort:"name"}` persists; re-fetch board shows `sort:"name"`.
- Add a card via `POST /api/lists/{id}/cards` → it lands in alphabetical slot, not bottom.
- Re-fetch board (reload) → sort still `name`, ordering holds (persistence).
- CLI `list ls` / `card add` / `card ls` still work against the local store.

## Out of scope
- Renaming a column from the web UI (backend supports it; not requested by either card).
- Trello-backend sort emulation (documented no-op).
- A backend "resort existing cards" op (kept client-side).
