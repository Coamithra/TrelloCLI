# `export --to local --fork` — mint a new board id

Card `c1597bcc` · branch `feat/export-fork`

## Context

`trello --board X export --to local` writes the snapshot under the **source board's
id**. That is deliberate: it makes a re-export an idempotent in-place refresh, which
is what you want for a *mirror*.

It is the wrong contract for the other thing people do with export: **splitting a
cloud board into two live boards** — one stays on Trello, one becomes local, and the
two diverge from that moment on. Today both live boards carry the same id and only
`--backend` tells them apart.

`--fork` adds that second mode. It is a **fork, not a mirror**: the new board is
permanently orphaned from its source (no future export tracks it; running `--fork`
twice yields two boards). Same create-new-each-time contract `export --to trello`
already documents.

## Design

### `local.py` — `import_board(..., board_id=None)`

Add a keyword-only `board_id` override; `bid = board_id or board["id"]`. Everything
downstream already flows from `bid` (`board.json`'s `"id"`, `_save_lists`,
`_save_labels`, `_to_store_card(bid, …)`'s `idBoard`, the prune, the blob-dir prune,
`_log`), so this is the only line that changes. Docstring gains the fork paragraph.

**Only the board id is reminted.** List / card / label / comment / checklist ids stay
as-is — see Out of scope.

### `main.py` — `cmd_export` / `_export_to_local`

- `--fork` joins `bool_flags` in `cmd_export`; `_export_to_trello` **rejects** it
  (Trello always mints its own ids, so `--to trello` is already create-new).
- `_export_to_local` mints the destination id **once, up front**:
  `dest_id = new_id() if fork else board["id"]`.
- The `--name only applies to export --to trello` guard relaxes under `--fork`.
  Rename works by overriding the snapshot's name (`board = {**board, "name": …}`)
  — `import_board` writes `board["name"]`. Still refused for a plain mirror, where
  a rename would just be undone by the next re-export.

**The wrinkle: the board id is a path component** (`<root>/<bid>/attachments/<cardId>/`).
Both attachment helpers run *before* `import_board` and both take a board id, so both
get `dest_id`, not `board["id"]`:

| call site | with the source id (wrong) | with `dest_id` (right) |
|---|---|---|
| `_export_attachment_blobs` | blobs land under the source board; the fork points at nothing | blobs land under the fork |
| `_preserve_local_attachment_urls` (`--no-attachments`) | seeds the fork's cards with urls into the *source board's* blob dir | finds no such board → no-op, which is correct for a fork |

- Output: fork prints that it created a **new** board and names the source id it is
  no longer tracking. JSON gains `forked` (bool) and `sourceId` — always present, so
  the shape stays stable (same rule as the zeroed `attachments` block).
- `--fork --no-attachments` warns on stderr: unlike a mirror, **nothing will ever
  re-export a fork**, so skipped blobs are skipped permanently.

## Verification

- `python -m pytest` green.
- New tests in `tests/test_local_store.py`:
  - `import_board(board_id=…)` writes under the new id, leaves the source board
    untouched, and stamps each card's `idBoard` with the new id.
  - **CLI-level fork with an uploaded attachment** (the wrinkle): monkeypatched
    `_gather_board` + `api.download_attachment`, then assert the blob is on disk
    under `<new_bid>/attachments/<cardId>/` and the stored url resolves.
  - `--fork` twice → two distinct boards, source board still present.
  - `--fork --no-attachments` does not cross-link to the source board's blobs.
  - `--name` refused without `--fork`, accepted with it; `--to trello --fork` refused.
- Functional smoke with the **worktree** interpreter (`.venv/Scripts/python.exe -m
  trello_cli`, never the bare `trello`), formatted + `--json`.

## Out of scope

- **Reminting list/card/label/comment/checklist ids.** Much larger diff (every
  cross-reference: `idList`, `idLabels`, checklist items, attachment ids) for a
  collision that only exists if a fork and a mirror of the same source live in the
  same store. Documented seams instead: `get_my_cards` walks every board, so that
  combination shows one card id twice; a fork's `card.shortUrl`/`shortLink` still
  deep-link to Trello cards it no longer tracks.
- Any `local→trello` id map / tracked re-sync (explicitly rejected, DESIGN.md).
- `--fork` for `--to trello` (already create-new-each-time by nature).
