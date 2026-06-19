# Plan: Phase 1 — Local file backend (core)

Card: **Phase 1: Local file backend - core (boards/lists/cards)** — `6a354391`

## Context

Phase 0 inserted a `Backend` ABC (`backends/base.py`) between the CLI and the data
source; `api.py` is now a facade forwarding to `get_backend()`. Phase 1 adds the
**second backend**: a local file store, so `trello --backend local ...` is a working
file-backed kanban driven by the *existing* CLI and formatters — no command/`fmt.py`
changes.

**Done when:** `trello --backend local ...` creates/moves/edits boards, lists, and
cards on disk and they render via the existing CLI (formatted + `--json`), with the
Trello backend completely unchanged.

## The contract (what `LocalBackend` must return)

Every backend returns Trello-shaped dicts. The keys the formatters/commands actually
read (the contract Phase 1 must satisfy):

- **boards**: `get_boards` → `[{id,name,shortUrl,closed}]`; `get_board` → `{id,name,shortUrl,desc}`; `create_board` → `{id,name,shortUrl,...}`
- **lists**: `get_lists` → `[{id,name,pos}]` (open only, sorted by `pos`); create/rename/archive/update return a list dict
- **cards**: `get_board_cards(filter=visible|closed)` → `[{id,name,shortUrl,labels,due,idList,idMembers,shortId,dateLastActivity}]`; `get_cards_in_list` → same + `pos` (sorted by `pos`); `get_card` → `{id,name,desc,shortUrl,labels,due,dueComplete,idList,idMembers,shortId,dateLastActivity,checklists:[],attachments:[]}`; create/move/archive/unarchive/update return a card dict
- **pos is a float** so the `card pos` / `list pos` midpoint math (`(a+b)/2`, `"top"`, `"bottom"`) works unchanged
- **ids are 24-hex** so `short_id` (first 8) and the `len==24` resolver short-circuit behave identically

## Files

### `backends/store.py` (new) — primitives, Trello-agnostic
- `new_id()` → `secrets.token_hex(12)` (24 hex chars)
- `now_iso()` → `datetime.now(timezone.utc).isoformat()` (for `dateLastActivity`)
- `atomic_write_json(path, obj)` → write temp in same dir + `os.replace` (atomic on Windows); `read_json(path, default)`
- `resolve_pos(existing: list[float], pos)` → float. `"top"` → `min/2` (or `STEP` if empty); `"bottom"` → `max+STEP` (or `STEP`); a number → `float(pos)`. `STEP = 65536.0`
- `append_activity(board_dir, entry)` → append one JSON line to `activity.log` (JSONL). Called on every mutation; the reader (`activity`/`updates`) is Phase 2, but the log accumulates now.
- A small `LocalStore` class holding `root` with path helpers (`board_dir`, `cards_dir`, card/board/lists file paths).

### `backends/local.py` (new) — `LocalBackend(Backend)`
- **Fully implemented (core):** all board, list, and card ops above + `get_comments` → `[]` (so `card show` renders).
- **Layout:** `<root>/<boardId>/{board.json, lists.json, cards/<cardId>.json, activity.log}`
- `create_board(default_lists=True)` seeds lists **To Do / Doing / Done** (local equivalent of Trello's defaults; matches this project's board).
- `update_card` accepts `name/desc/due/pos/idList/closed`; `move_card`/`archive_card`/`unarchive_card` are thin wrappers over it. `pos` resolves against the card's current list.
- **Stubbed (Phase 2)** — raise a clean `SystemExit("The local backend doesn't support '<op>' yet (Phase 2).")`, no traceback: labels, members, checklists, attachments, `get_my_cards` (single-user `mine`), `add/update/delete_comment`, `get_activity`, `get_actions_since`. The class stays concrete (all abstract methods present) so it instantiates.

### `backends/__init__.py` — selection
```python
@lru_cache(maxsize=None)
def get_backend() -> Backend:
    name = config.get_backend_name()        # --backend > config "backend" > "trello"
    if name == "local":
        from .local import LocalBackend
        return LocalBackend(config.get_local_root())
    if name == "trello":
        return TrelloBackend()
    raise SystemExit(f"Unknown backend: {name!r} (use 'trello' or 'local').")
```

### `config.py` — stateless backend selection + local_root (DECIDED: statelessness)
- **Remove the legacy active board entirely** (`get_active_board`/`set_active_board`/`get_active_board_name`) — it's shared mutable session state that breaks concurrent multi-agent use.
- `set_backend_override(name)` / `get_backend_name()` → `--backend` flag override → `TRELLO_BACKEND` env → `"trello"`. **No persistent "default backend" config key** (that would be conflict-prone shared state). Mirrors the existing `--board`/`TRELLO_BOARD` pattern.
- `get_local_root()` → `TRELLO_LOCAL_ROOT` env → config `"local_root"` → default `~/Dropbox/trello-cli` (expanduser). `set_local_root(path)`. local_root is stable machine *config* (like credentials), not session state — safe to persist.

### `main.py` — `--backend` flag + `local init`; remove `use`/`--use`
- Parse `--backend <name>` in `main()` next to `--json`/`--board`; `config.set_backend_override(name)` before dispatch.
- `_require_board()`: drop the active-board fallback — require `--board`/`TRELLO_BOARD`, else a clear error.
- **Remove `cmd_use` + the `use` command** and the `--use` activation flag on `board add`.
- New top-level `local` command: `local init [path]` → create the root dir, save `local_root`, print how to select it (`--backend local` / `TRELLO_BACKEND=local`). Does **not** flip a persistent default (statelessness).
- Update `USAGE` (drop `use`/active-board; add `--backend`, `local init`).

### Docs
- `CLAUDE.md`: add a **Statelessness** design guideline; document `--backend`/`TRELLO_BACKEND`, the local backend, `local init`; drop active-board mentions.
- `README.md`: same command-surface additions; remove `use`/`--use`.
- `DESIGN.md`: update the "Backend selection" section — stateless `--backend`/env, no active board.

## Decisions (resolved with user)

- **A. `local init` does NOT flip a persistent default.** Select the backend per-invocation
  with `--backend local` or `TRELLO_BACKEND=local`. Statelessness > demo convenience.
- **B. Active board is removed, not made per-backend.** It's legacy shared state that breaks
  concurrent agents; everything uses `--board`/`TRELLO_BOARD` already.

## Tests (Phase 5 — all against a temp local root, zero Trello writes)
Run from the worktree venv with `--backend local`, pointing `local_root` at a throwaway dir:
1. `local init <tmp>` → root created, config updated
2. `board add "Demo"` → `<tmp>/<id>/board.json` + To Do/Doing/Done in `lists.json` (use `--board <id>` for subsequent board-scoped commands — no active board)
3. `list ls`, `list add`, `list rename`, `list pos`, `list archive`
4. `card add`, `card ls`, `card show` (+ `--no-comments`), `card move`, `card rename`, `card desc`, `card due`, `card pos` (top/bottom/number/after/before), `card archive`/`unarchive`
5. Re-run each with `--json`; confirm raw-JSON shape
6. Confirm a Phase-2 op (e.g. `card mine`, `label ls`) errors cleanly (no traceback)
7. **Regression:** run a couple of read commands with `--backend trello` against the real board — output identical to master

## Out of scope (Phase 2+)
Labels, checklists, comments (add/edit/delete), attachments, members, `card mine`,
`activity`/`updates` reading from the log, the web app. The store *writes* `activity.log`
now; reading it is Phase 2.
