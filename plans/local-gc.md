# Plan: `trello local gc` — local-backend stale-data cleanup

Card: 6a35d10e — "Local backend: stale-data cleanup (orphaned attachment blobs, board/card lifecycle)"

## Context

The local file backend (`backends/local.py` + `store.py`) lays out
`<root>/<boardId>/{board.json, lists.json, labels.json, cards/<id>.json,
attachments/<cardId>/…, activity.log}`. Uploaded attachment blobs are copied
under `attachments/<cardId>/`; `attachment rm` deletes its own blob inline. The
card flags four ways stale data accumulates. Findings, per source:

1. **Orphaned attachment blob dirs.** `attachment rm` cleans up inline, and there
   is no `card delete` — so the **one current automatic orphan vector is
   `import_board`** (the `export` target): it prunes card *JSON* files for cards
   deleted upstream (`local.py:260-264`) but leaves their `attachments/<cardId>/`
   dirs behind. Also, `delete_attachment` swallows `OSError` (`local.py:716`), so
   a blob can survive its metadata. Both leave files no card references.
2. **No board-delete path.** Boards can be created/imported but never removed —
   the folder + blobs live forever. There is no `board delete`/`board archive`
   at all (only `card`/`list` archive).
3. **`activity.log` grows unbounded.** Append-only JSONL, never trimmed
   (`store.py:147-151`).
4. **Temp download cache.** `attachment view/open/download` (no dest) writes to
   `<tempdir>/trello-cli/` (`main.py:1276`) and nothing ever prunes it. Shared by
   both backends.

## Design decision: explicit `gc` command, dry-run by default

A deliberately-invoked **`trello local gc`** sweep, **not** cleanup hooks on the
mutation hot paths. Rationale tied to the project's own guidelines:

- **Concurrency / Dropbox last-write-wins.** The store is used by many agents
  concurrently (there is a *separate* open card about race-hardening the write
  paths). Auto-deleting blob dirs inside `update_card`/`delete_attachment` would
  race other invocations reading the same card and is exactly the kind of
  surprise destruction to avoid. An explicit, user-run sweep that **defaults to a
  dry run** (reports, deletes only with `--apply`) is safe and predictable.
- **Audit trail.** `activity.log` is a feature (`activity`/`updates` read it);
  silently truncating it would destroy data. Retention is **opt-in** via a flag.

### Command surface

```
trello local gc [--apply] [--activity-keep N] [--cache-days N]
```

- Operates on the configured local root (`config.get_local_root()`), like
  `local init` / `export` — instantiates `LocalBackend` directly, independent of
  `--backend` selection. Honors `--board` to scope the store sweep to one board
  (temp-cache prune is global regardless).
- **Dry run by default**: prints exactly what it *would* remove + reclaimed
  bytes. `--apply` performs the deletions.
- Branches on `_is_json()` — emits the report dict raw under `--json`.

### What it cleans

1. **Orphaned attachment blob dirs** — `attachments/<cardId>/` with no
   `cards/<cardId>.json`. Remove the whole dir.
2. **Orphaned blob files** — within a *live* card's attachment dir, files not
   referenced by any `isUpload` attachment's `url` in that card's JSON.
3. **Empty dirs** — emptied `attachments/<cardId>/` and an empty `attachments/`.
4. **`activity.log` retention** — only when `--activity-keep N` is given: keep the
   newest N log lines per board (atomic temp+replace rewrite). Default: untouched.
5. **Temp download cache** — prune `<tempdir>/trello-cli/` entries older than
   `--cache-days N` (default **7**; `0` = clear all). Regenerable cache, low risk.

### Code changes (file by file)

- **`backends/store.py`** — add `LocalStore.attachments_root(board_id)` (the
  `<board>/attachments` parent) + small dir-walk helpers if needed; add
  `rewrite_activity_tail(board_id, keep)` (atomic last-N rewrite of raw lines).
- **`backends/local.py`**
  - `import_board`: when pruning a deleted card's JSON, also remove its
    `attachments/<cardId>/` dir (closes the one current automatic orphan vector).
  - Add **`gc(self, board_id=None, apply=False, activity_keep=None) -> dict`** —
    local-only (not on the ABC, mirrors `import_board`). Walks the store, returns
    a report `{orphan_dirs, orphan_files, empty_dirs, bytes, activity_trimmed,
    applied}`. Does the store deletions only when `apply=True`.
- **`main.py`**
  - Extract `_temp_cache_dir()` so `_attachment_dest` and gc agree on the path.
  - `_local_gc(args)`: parse `--apply` (bool), `--activity-keep`/`--cache-days`
    (value); call `LocalBackend(get_local_root()).gc(...)`, prune the temp cache,
    merge + print the report (or `print_json`). Register `"gc"` in `cmd_local`'s
    dispatch table; add a `local gc` line to `USAGE`.
- **`CLAUDE.md`** (store.py + local.py bullets, local-group convention) and
  **`README.md`** (Local backend section: add `local gc`, document retention).

### Board deletion (in scope, per approval)

A separate confirmed command — **not** a gc side effect, so gc never removes a
board the user intentionally kept:

```
trello local rm <board> --yes
```

- Resolves `<board>` against the local store (full/short id or name prefix,
  including closed boards). Without `--yes`: prints the board + card/attachment
  counts + reclaimable bytes and deletes nothing. With `--yes`: `shutil.rmtree`
  the whole `<root>/<boardId>/` folder. Branches on `_is_json()`.
- `LocalBackend.delete_board(board_id, apply=False) -> dict` (local-only).

### Out of scope

- **Race-hardening the write paths** — the separate open card (6a35de7b).
- gc/rm do not touch Trello (the remote backend has no local files).

## Tests (Phase 5, against a scratch `--local-root`, no Trello writes)

1. `local init` a temp root; `board add`; `card add`; `attachment add <file>`.
2. Manufacture orphans: delete a `cards/<id>.json` by hand (simulating a prune)
   leaving its `attachments/<id>/`; drop a stray file into a live card's att dir.
3. `local gc` (dry run) → lists both orphans, deletes nothing (verify files still
   present). `local gc --json` → report dict.
4. `local gc --apply` → orphans gone, live card's real blob untouched.
5. `local gc --activity-keep 2 --apply` → activity.log trimmed to 2 lines;
   `activity` still reads.
6. Temp cache: create old/new files under `<tempdir>/trello-cli`; `--cache-days`
   prunes only the old ones.
7. Re-run export prune path: confirm a pruned card's attachment dir is removed.
