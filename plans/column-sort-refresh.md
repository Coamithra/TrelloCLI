# Column sort: single refresh, honour sort on move, split created vs updated

Card `bb7e16d7` (Doing). Branch `fix/column-sort-refresh`, worktree `.trees/wt2`.

## Context

Three complaints about the per-list auto-sort feature, all real:

1. **Re-sorting a column causes several full board reloads.** The menu click does
   `PATCH {sort}` then an explicit `loadBoard()`; the server meanwhile rewrites every
   card file in the column, so the watchdog counter moves and the 1s-poll SSE fires one
   or more *extra* `change` events, each another `loadBoard()`. `renderBoard()` wipes
   `boardEl.innerHTML` and rebuilds, so an in-progress "+ Add a card" composer loses its
   text and its focus (and the board loses its scroll position).
2. **A card moved into a sorted column ignores the sort.** `update_card` with `idList`
   unconditionally does `resolve_pos(existing, "bottom")` (`local.py:1140`); only
   `create_card` consults `_auto_place_pos`. Same hole in `unarchive_card`. So
   "newest first" does not float a card moved into Done to the top.
3. **`newest`/`oldest` are `dateLastActivity`, not creation.** Tracked as a known gap in
   `DESIGN.md:504`; never built, because the store records no creation time.

## Design

### `local.py` — creation time

- `_new_card` stamps `"dateCreated": now_iso()`; `_card_shape` (the import path) carries a
  source `dateCreated` through.
- `_card_created(card)` is the read-time accessor, best-effort backfill for cards written
  before the field existed:
  1. stored `dateCreated`;
  2. else, if the card carries Trello provenance (`shortLink`/`shortUrl` non-empty — local
     cards never set them), decode `int(id[:8], 16)` as unix seconds, accepted only if it
     lands in a sane window (2011 → now+1d). This is the discriminator the search design
     lacked: a Trello id encodes its creation time, a local `new_id()` is random, and
     `shortLink` is what tells them apart;
  3. else `dateLastActivity`.
  No migration write — the fallback runs on read.

### `local.py` — sort modes

`LIST_SORTS` becomes `manual`, `created-newest`, `created-oldest`, `activity-newest`,
`activity-oldest`, `name`. The old `newest`/`oldest` values stay accepted forever as
aliases of the `activity-*` pair (`_SORT_ALIASES`), normalized on read (`get_lists`) and on
write (`update_list` persists the canonical value) so existing stores keep working.
`_sort_key(sort)` returns `(keyfunc, reverse)` instead of the caller deriving `reverse`
from `sort == "newest"`.

### `local.py` — honour the sort on move / unarchive

In `update_card`, auto-place when: the card ends up open, its (destination) list has a
non-manual sort, the caller passed **no explicit `pos`**, and the call touched placement
(`idList` given, or the card is being unarchived). `dateLastActivity` is stamped *before*
the placement so an `activity-*` sort sees the fresh value. An explicit `pos` still wins —
the web drag sends `{idList, pos}` and must land where the user dropped it (it clears the
column's sort straight after). `unarchive_card` stops forcing `pos="bottom"` when the
destination is sorted.

### `web/static/app.js`

- `renderBoard` preserves, across the rebuild: each column's composer text +
  focus/selection, the add-list composer's open state and text, the board's horizontal
  scroll and each column's vertical scroll.
- SSE `change` events are **debounced** (`LIVE_DEBOUNCE_MS`) so a burst of file writes
  collapses into one reload, and the reload runs *quiet* (no "Loading…" status churn).
- Sort menu lists the six modes with the created/updated split spelled out.

### Out of scope

- ~~`sort:created` / `created:` in `trello search`~~ — was out of scope here on DESIGN.md's
  "absent beats inconsistent" reasoning. SHIPPED since, on card db0babb3: the backfill grew
  an activity.log step that dates the cards the reasoning was about, so the operators answer
  from a recorded creation time. See DESIGN.md.
- A CLI verb for setting a list sort (still web-only).
- Suppressing the redundant SSE reload that follows the client's own mutation — the
  debounce collapses the burst and nothing is lost across a render, which is what actually
  hurt.

## Verification

- `python -m pytest` green, plus new tests: move/unarchive auto-place into a sorted list,
  explicit `pos` still wins, `dateCreated` stamped on create, alias normalization,
  created-vs-activity ordering, id-backfill for a Trello-shaped card.
- Functional: `trello serve` on a scratch board — re-sort a column while typing in the
  composer (text + focus survive, one refresh), move a card into a "newest (updated)"
  column via CLI and see it on top.
