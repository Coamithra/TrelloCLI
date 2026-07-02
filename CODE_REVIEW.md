# TrelloCLI — Code Review

**Date:** 2026-07-02
**Scope:** Full application — CLI (`main.py`, `fmt.py`), the backend seam (`api.py`, `backends/`), the local file store (`store.py`, `local.py`), the Trello REST client (`trello.py`), config (`config.py`), and the web app (`web/server.py`, `web/live.py`, `web/static/app.js`).
**Method:** Four parallel per-subsystem reviews, then direct verification of the highest-severity findings against the source. Every finding cites a file and line and quotes the offending code.

## Implementation status (2026-07-02)

Everything below was implemented on this branch by four per-subsystem passes (matching the file groups above), then adversarially re-reviewed and covered by a new 70-test pytest suite (`tests/`, run with `python -m pytest`; also added as the `dev` extra). Line numbers in the findings refer to the pre-fix code. Deliberate deviations, decided during implementation:

- **C7 multi-word names:** every command taking a single trailing name/text joins its positionals — except `card add`, whose optional positional `description` makes joining ambiguous; multi-word card names stay quoted (documented in its usage). Unifying would need a breaking `--desc` flag, beyond this pass's small-local-fix bar.
- **C6 export blob sequencing** ("blobs written outside the lock"): unchanged. Fixing it needs a lock-scoped import hook on the local backend; the exposure window (concurrent `local gc --apply` deleting a just-downloaded blob before `import_board` commits) is narrow and self-heals on re-export.
- **L2 gc/delete_board lock narrowing:** unchanged. Narrowing the dry-run phase would mean unwrapping those ops from `_MUTATORS`' single-source-of-truth locking; keeping the invariant was judged worth the (minor, pre-existing) lock hold during a sweep.
- **T2 cap exhaustion:** `grab` now distinguishes a genuinely empty source list (`None` → "Nothing to grab") from claim-contention exhaustion (clean error suggesting retry).
- **X3 fail-safe:** an unparseable *own* claim date now counts as a loss (previously a win). In the no-competitor corner case this can leave the card in the dest list unowned, but treating it as a win risks two winners; losing is the safer failure. Like the rest of the handshake, unverified against live Trello.
- **W4 status fidelity:** with the Trello backend now translating all upstream HTTP errors to `SystemExit` (T1), the web `_ok` maps them by message: not-found → 404, rate limit → 429, upstream credential failure → 502, other validation → 400.

## Overall assessment

This is well-above-average code for its size and ambition. The architecture is genuinely clean: the `Backend` ABC / `api` facade / concrete-backend seam is signature-exact (all ~40 operations line up), `fmt.py` is backend-agnostic as designed, and the concurrency story in the local store (single re-entrant `StoreLock`, the `_MUTATORS` single-source-of-truth wrapping, atomic temp-file + `os.replace` writes, the transient-vs-persisted `rebalanced` discipline) is carefully thought through and honestly commented. The web frontend is disciplined about DOM construction — no XSS sinks in ~1,500 lines of vanilla JS despite abundant `innerHTML` temptation.

The defects cluster in a few recurring themes rather than in the architecture:

1. **Trust boundaries.** The store is an explicitly Dropbox-synced, multi-machine folder fronted by an optionally network-exposed web server, yet attachment URLs and imported card JSON are treated as trusted input (path traversal), and the loopback web bind has no Host validation (DNS rebinding).
2. **Failure-mode blast radius.** A single corrupt/half-synced card file aborts whole-board and cross-board operations; the `grab` claim-handshake's `try/except` conflates "failed before claiming" with "failed after winning," so one transient error can undo a competitor's win.
3. **Cross-backend contract drift.** The Trello backend's field-narrowed reads silently omit fields the local backend supplies (`idBoard`, `dueComplete`), and `local.update_card` returns an un-enriched store dict — so "either backend, no per-backend code" is violated in places that aren't exercised by tests (there are none).
4. **Silent-success ergonomics.** Typo'd verbs, stray positional arguments, and prefix-name collisions degrade to successful no-ops or wrong-target mutations instead of clean errors.

None of these require design changes; every major has a small, local fix. There is **no automated test suite**, which is the single highest-leverage gap — most findings below would be caught by modest unit coverage of the resolvers, the pos math, and a cross-backend dict-shape conformance test.

Severity key: **CRITICAL** = data loss / security with a realistic trigger; **MAJOR** = wrong behavior or security on plausible input; **MINOR** = narrow edge case, quality, or docs.

---

## Cross-cutting / highest priority

### X1. MAJOR — Path traversal via attachment `url` (arbitrary file read + delete through the web server)
`backends/local.py:901-906` (`_blob_path`), consumed by `web/server.py` `/raw` (read) and `local.py:967-974` (`delete_attachment`).
```python
def _blob_path(self, url: str) -> Path:
    p = Path(url)
    return p if p.is_absolute() else self.store.root / url
```
No check that the resolved path stays under `store.root`; relative `..` and absolute paths are honored by design. `import_board` persists attachment metadata verbatim (`local.py:370`). Since the store is Dropbox-shared, a card file with `{"attachments":[{"id":"a","isUpload":true,"url":"../../../home/user/.ssh/id_rsa"}]}` makes `GET /api/cards/{id}/attachments/a/raw` stream the private key to the browser, and `delete_attachment` calls `blob.unlink()` / `blob.parent.rmdir()` on the target. This also contradicts `server.py`'s comment that "the server never fetches an arbitrary URL" (an `isUpload:true` attachment with a remote URL — legitimately produced by `export --to local --no-attachments` — makes the server fetch it).
**Fix:** enforce `dest.resolve().is_relative_to(store.root.resolve())` and pin uploaded blobs to the card's own `attachments/<cardId>/` directory.

### X2. MAJOR — One corrupt/half-synced card file aborts all card reads on the board (and comment ops across *all* boards)
`backends/store.py:96-104` (`read_json` fail-fast) + `local.py` `LocalStore.cards()` / `_locate_comment` / `_locate_checklist`.
```python
except json.JSONDecodeError as e:
    raise SystemExit(f"Corrupt store file {path}: {e}")
```
`store.cards()` runs `read_json` over every `cards/*.json`, so one zero-byte or truncated file (Dropbox mid-sync; note `json.loads("")` raises, so *empty* counts as corrupt) breaks `get_board_cards`, `get_cards_in_list`, `create_card`/`update_card` (via `_list_positions`), and `get_my_cards`. Worse, the comment/checklist locators scan every card of every board, so a corrupt card on board A breaks `comment edit` on board B. The `gc` comment at `local.py:1010-1012` is also wrong: `read_json` raises before the `if not card` guard runs, so `local gc --apply` dies mid-sweep after already deleting some dirs.
**Fix:** skip-and-warn on per-card decode errors in `cards()` and the locators (mirror the tolerant `read_activity`), keep the hard fail for `board.json` / `lists.json`.

### X3. MAJOR — `grab` claim-handshake rollback can undo a competitor's legitimate win
`backends/trello.py:190-206`.
```python
self.move_card(card_id, dest_list_id)          # fast grab
try:
    mine = self.add_comment(...)
    ...
    if self._won_claim(card_id, claim_id, my_date):
        return self.get_card(card_id)          # win-path read inside try
    self.delete_comment(mine["id"])            # loss cleanup inside try
except Exception:
    self.move_card(card_id, source_list_id)    # blanket rollback
    raise
```
The single `try` conflates three phases. (a) If agent A **loses** and its `delete_comment` hits a transient error, the `except` yanks the card back to the source list — but agent B has legitimately won it and is now working it; agent C can then grab it too → two agents on one card. (b) If A **wins** and the win-path `get_card` blips, the rollback moves the won card back *and* leaves A's claim comment un-retracted, so A's own retry loses to its dead claim and the card is unclaimable for the ~60s window. (c) `except Exception` doesn't catch `KeyboardInterrupt` during the 10-30s `time.sleep`, so Ctrl-C strands the card in the dest list with a live claim — contradicting the code's own "don't strand the card" promise.
**Fix:** make rollback conditional on *not* having passed adjudication; delete `mine` on the win-path rollback; use `finally`/`BaseException` for the interrupt case. (CLAUDE.md already notes this handshake is "not verified against live Trello.")

### X4. MAJOR — Cross-backend dict-shape drift breaks web features and the `--json` contract
Multiple sites:
- **`get_card` omits `idBoard` on the Trello backend** (`trello.py:144`), so the web label popover requests `/api/boards/undefined/labels` (`app.js:778,861`) — the entire label feature fails on the Trello backend. It works on local only because local card JSON stores `idBoard`.
- **`local.update_card` returns the raw store dict** (`local.py:685`), keeping store-only `idLabels` + inline `comments` and lacking the resolved `labels` key that `_enrich_card` produces. So `trello --backend local grab --json | jq .labels` → `null`, while `--backend trello` returns a populated array; the web `PATCH /api/cards/{id}` response leaks internal keys too. **Fix:** `return self._enrich_card(board_id, card)` (re-attaching the transient `rebalanced` flag after enrichment).
- **`get_cards_in_list` / `get_my_cards` omit `dueComplete`** on the Trello backend (`trello.py:134,153`), so completed due dates render as pending, and list-view dicts differ from the local backend's.

### X5. MAJOR — `local.update_card(idList=...)` never validates the destination list
`backends/local.py:659-664`.
```python
if "idList" in fields:
    card["idList"] = fields["idList"]
    existing = self._list_positions(board_id, card["idList"], exclude=card_id)
```
No check that `idList` names a real, open list on the card's board (`_list_positions` returns `[]` for an unknown id, so the write succeeds). The web `PATCH /api/cards/{id}` whitelists `idList` and passes it through raw. A stale/foreign/archived list id → the card saves with an `idList` that maps to no column: it renders nowhere yet still appears in `get_board_cards` and `card mine`, silently, with no error.
**Fix:** verify the target list exists and is open in `_load_lists(board_id)`; `SystemExit` otherwise.

---

## CLI — `main.py`, `fmt.py`

### C1. MAJOR — `_parse_due` overwrites an explicitly-given time-of-day with 09:00
`main.py:708-709`.
```python
if dt.tzinfo is None:
    dt = dt.replace(hour=9, tzinfo=timezone.utc)
```
Meant to default *date-only* input to 9am, but it fires for any naive datetime. `trello card due abc123 2026-05-01T15:30` → stored as `2026-05-01T09:30+00:00` (minutes kept, hour clobbered), and the success echo hides it. `_parse_since` (line 748) handles the same case correctly with `replace(tzinfo=...)` only — proving intent.

### C2. MAJOR — Resolvers lack an exact-name tier, so an exactly-typed name is unaddressable when it prefixes a sibling
`main.py:189-197` (`_resolve_board_ref`) and the same pattern in `_resolve_list` (221), `_resolve_checklist` (285), `_resolve_checkitem` (314), `_resolve_label` (345), `_resolve_attachment` (371).
```python
matches = [b for b in boards if b["name"].lower().startswith(lower)]
...
if len(matches) > 1:
    raise SystemExit(f"Ambiguous board name '{ref}'. ...")
```
With lists `To Do` and `To Do (blocked)`, the exact name `"To Do"` matches both → the flagship `grab --from "To Do"` workflow wedges the moment anyone adds a "To Do Later" column. Also the ID-prefix tier runs before the name tier, so a list literally named `5` is shadowed by any sibling whose id starts with `5`.
**Fix:** add an exact-match tier (case-insensitive) before the prefix tier in each resolver.

### C3. MAJOR — `board archive` / `board restore` discard trailing arguments and mutate the `--board` board
`main.py:506-522`.
```python
if verb == "archive":
    _board_set_closed(True)   # ignores args[1:]
    return
```
With `TRELLO_BOARD=ProjectBoard`, `trello board archive Scratch` archives **ProjectBoard** and prints `Archived board: ProjectBoard`. A wrong-target mutation on well-formed-looking input. (Contrast `_local_rm`, which demands exactly one positional.)

### C4. MAJOR — `ls` fallback turns typo'd verbs into successful no-ops (exit 0)
`main.py:385-395`, affecting `list` and `label` (whose `ls` ignores its args).
```python
if args and args[0] in subcmds:
    subcmds[args[0]](args[1:]); return
if "ls" in subcmds:
    subcmds["ls"](args)       # args silently ignored by _list_ls / _label_ls
```
`trello list renmae "To Do" "Backlog"` → falls to `list ls` → prints the table and exits 0. An agent checking the exit code concludes the rename succeeded. Same for `trello label delte urgent`. (`cmd_board` at 506 has the same class of bug — unknown verbs degrade to `board show`, exit 0.)

### C5. MAJOR — `_attachment_dest` builds the download path from an unsanitized attachment name
`main.py:1350-1359`.
```python
filename = att.get("name") or os.path.basename(att.get("url", "")) or att["id"]
return os.path.join(tmp, f"{short_id(att['id'])}-{filename}")
```
`_export_attachment_blobs` sanitizes the same input (`re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)`, explicitly "the path-safety guard"), but the view/open path doesn't. An attachment named `reports/q3.pdf` → `open(dest, "wb")` on a nonexistent subdir → `FileNotFoundError` traceback; a name with `..` writes outside the cache dir.

### C6. MINOR — miscellaneous CLI correctness
- **`cmd_activity` crashes on non-numeric arg** (`main.py:547`, `int(args[0])` → raw `ValueError`).
- **`card ls`/`card mine` show completed due dates as pending** (`main.py:600,880` — `dueComplete` available on local but not passed to `due_str`).
- **Naive global-flag surgery** (`main.py:2064-2090`): `--json` stripped everywhere; `--board`/`--backend`/`--local-root` consume the next token even if it's another flag → `trello --board --backend local boards` sets board override to `"--backend"`.
- **`_resolve_card` passes any 24-char token through unvalidated** (`main.py:236`) → cross-board mutation or raw `httpx` 404 traceback instead of a clean "Card not found."
- **Archived cards unaddressable by prefix** anywhere except `card unarchive` (`main.py:574` etc. don't pass `include_closed=True`).
- **`export --to local --no-attachments` re-run downgrades stored URLs** from local blobs back to auth-gated remote URLs, orphaning the blobs (`main.py:1897`).
- **`_export_attachment_blobs` writes blobs before `import_board`, outside the lock** (`main.py:1597`) — a concurrent `local gc --apply` can delete them.
- **`grab --json` on local emits a store-shaped card** (same root cause as X4).
- **`list add` "defaults to top" only on local** (`main.py:914` + `trello.py:103`).
- **`_resolve_comment` searches only the 50 newest comments** (`main.py:263`); **export caps comments at 1000** silently (`main.py:1675`).

### C7. MINOR — CLI quality
- `cmd_labels` (525) and `_label_ls` (1021) are byte-for-byte duplicates; `_card_pos` (810) and `_list_pos` (937) duplicate the whole midpoint algorithm; `_card_ls`/`_card_mine` duplicate row building.
- Help-text drift: `USAGE` says `--json` is "read commands only" but many mutators honor it (inconsistently); `serve` usage omits `--token`; `label add` usage marks color required though it's optional, and `trello label add red` creates a colorless label named "red."
- Inconsistent multi-word-name handling: `card ls`/`card move`/`list archive`/`label set` join positionals, but `card add` and `list rename` take only the first token.
- `fmt.label_str` (fmt.py:23) can render `[None]` for a label with `name:""` and `color:null`.
- No top-level exception handler around dispatch (`main.py:2103`) → raw `httpx` tracebacks on 401/429/timeout.

---

## Trello backend + config — `trello.py`, `config.py`

### T1. MAJOR — Every HTTP error surfaces as a raw `httpx.HTTPStatusError` traceback
`trello.py:53,58,63,68` (bare `r.raise_for_status()`), no top-level handler in `main.py`. `trello show <deleted-card>` → multi-frame traceback, whereas the same command on `--backend local` prints a clean one-line `SystemExit`. Also makes the web server return 500 instead of 404 for Trello-backend not-founds (its `_ok` only translates `SystemExit`). Same for 401 (revoked token) and 429.

### T2. MINOR — Trello backend correctness/quality
- **`shortId` requested in four card reads but it's not a real Trello field** (`trello.py:127,134,144,153`; the real field is `idShort`) and nothing consumes it — dead weight, or a 400 if Trello ever validates field names.
- **No retry/backoff for 429 or transient errors** (`trello.py:51-68`), in a tool built for many concurrent agents; a rate-limit burst during `grab` lands in the flawed rollback paths (X3).
- **`get_auth()` re-reads and re-parses the config file on every HTTP request** (`trello.py:47` → `config.py:57`), and each request opens a fresh TLS connection (no shared `httpx.Client`).
- **`_won_claim` assumes victory when its own claim date is unparseable** (`trello.py:216`) → two racers both "win"; and an aware-vs-naive date comparison at 231/233 would raise `TypeError`. A non-claim comment merely containing the marker phrase forces a spurious loss (`trello.py:226`).
- **`remove_label_from_card` and `delete_attachment` reinline `_delete` verbatim** (`trello.py:286,376`) — a future transport fix (e.g. retries) would miss them.
- **Cap exhaustion returns `None`, indistinguishable from an empty list** (`trello.py:208`) → "Nothing to grab" on a full but contended board; contradictory comments on `_GRAB_MAX_ATTEMPTS` (line 30 vs 181).
- Stale docstrings claiming Trello-only / "planned local file store" (`trello.py:5`, `base.py:6`).

### T3. MINOR — Config handling
- **Credentials written world-readable and non-atomically** (`config.py:53-54`): `write_text(json.dumps(...))` with default umask, no `chmod 600`, no temp+`os.replace` (which `store.py` does carefully). On a shared machine any user reads `~/.trello-cli.json`.
- **Corrupt config → raw `json.JSONDecodeError`** from nearly every command (`config.py:47-49`, no handling).

---

## Local store — `store.py`, `local.py`

*(X1, X2, X4, X5 above are the majors here.)*

### L1. MINOR — Local store correctness
- **Many mutators skip `_log` and the `dateLastActivity` bump** (`update_comment`, `delete_comment`, `update_label`, `delete_label`, `remove_label_from_card`, all checklist ops, `delete_attachment`), so `activity`/`updates` is a partial record and "newest"-sorted lists don't reorder — diverging from the Trello backend, which logs all of these.
- **Inconsistent `pos` access**: direct `c["pos"]` in `_list_positions`/`_rebalance_cards`/`_rebalance_lists_inplace` (`local.py:133,148,464`) vs defensive `.get("pos",0)` in adjacent sort keys → `KeyError` traceback on a hand-edited card.
- **`unarchive_card` keeps a stale `pos`** (`local.py:645`) that can exactly equal a sibling's after an interim rebalance → two cards share a `pos`, order arbitrary.
- **Dropbox conflicted-copy card files become phantom duplicates** (`store.py:294` globs `*.json`; a `... (conflicted copy).json` carries the same `id`) — never converge, `gc` doesn't sweep them. A stem≠`card["id"]` guard would help.
- **`_parse_iso` can't parse `Z`-suffixed timestamps on Python 3.10** (`local.py:794`; `fromisoformat` gained `Z` in 3.11, but pyproject allows `>=3.10`) → `updates --since ...Z` silently returns the whole log.
- **`setdefault("memberCreator", user)`** (`local.py:806,828`) attributes legacy/foreign log entries to whoever runs the command — actively wrong on a shared store.
- **Archived list's open cards still returned by `get_board_cards("visible")` / `get_my_cards`** (unlike Trello) → after web "Delete list," cards are invisible in every column yet counted in `card mine`.
- **`import_board` resets every list's local `sort` to `manual`** on a Trello re-pull (`local.py:407`) — silent loss of local-only state.

### L2. MINOR — Local store quality
- `_enrich_card` re-reads `labels.json` once *per card* (`local.py:260`) → N reads per N-card board; load once and pass the map down.
- `update_list` and `get_boards` re-read files their callers just loaded.
- `gc`/`delete_board(apply=False)` take the store lock for the whole multi-board sweep (`local.py:1039`) → other processes hit the 15s lock timeout during routine `gc`; `_dir_size` (74) has no error handling for files removed mid-scan.
- `atomic_write_text` leaks its `.tmp` file on a mid-write crash (`store.py:107`); `gc` never sweeps `*.tmp` strays.
- `_unsupported` is dead code (`local.py:92`); the "insert at top" `pos` expression is duplicated (`store.py:70` vs `local.py:227`); `resolve_pos` silently treats a typo'd keyword as "bottom" (`store.py:74`).

**Verified non-issues:** `_MUTATORS` is complete (every writer wrapped); all 44 ABC methods implemented; `grab_top_card` is genuinely atomic under the lock; the `rebalanced` transient is never persisted; `StoreLock`'s timeout path releases cleanly.

---

## Web app — `server.py`, `live.py`, `app.js`

*(X4's `idBoard` bug is the top web finding.)*

### W1. MAJOR — Tokenless loopback API is vulnerable to DNS rebinding
`server.py:91-110,398-401`. The token middleware is only installed when a token exists, and `serve()` only mints one for non-loopback binds; neither uvicorn nor the app validates the `Host` header. A page the user visits while `trello serve` runs can rebind its hostname to `127.0.0.1` and issue same-origin requests — including `DELETE /api/boards/{id}?confirm=true` (permanent purge). **Fix:** add Host allow-listing (`TrustedHostMiddleware`) even on loopback.

### W2. MAJOR — Token sent as `?token=` on every fetch, leaking into logs/history/shared links
`app.js:27-32,136,194` + `server.py:429` (`log_level="info"`). `api()` appends `?token=` to all XHRs, and `attachmentHref` embeds it in `<a href>`/`<img src>`, even though `fetch` can set `Authorization: Bearer` (which the server accepts). Every call writes the secret to the access log; "Copy link address" on an attachment shares a URL carrying the full read/write token. **Fix:** use the Bearer header for `fetch`; reserve `?token=` for navigation/`EventSource`/attachment hrefs only.

### W3. MAJOR — Stale-response race in board switching (and card opening)
`app.js:115-122,620-629`. `selectBoard` fires `loadBoard` without sequencing, and `renderBoard` never checks the response still matches `currentBoardId`. Click board A then quickly B; A's slower response lands last and renders A's columns while the URL, nav, and SSE all say B — the user edits A believing it's B. `openDetail` (1127) has the same race for rapid card clicks. **Fix:** guard renders with a request token / current-id check.

### W4. MINOR — Web correctness
- **`_ok` maps backend validation errors to 404, not 400** (`server.py:76`) — `PATCH /api/lists/{id}` with `{"sort":"bogus"}` returns 404 for a list that exists.
- **`_guard` silently drops non-whitelisted fields** (`server.py:58`) instead of rejecting — a `{"name","closed"}` card PATCH applies the rename, discards `closed`, returns 200.
- **Zero-open-boards bricks the UI** (`app.js:1527`): the early `return` happens before listeners (incl. the ⚙ manage-boards button) are wired, so archived boards can't be restored from the web.
- **SSE `change` events mid-drag are dropped, not deferred** (`app.js:1517`) → other agents' edits stay invisible until an unrelated later mutation.
- **Failed drag PATCH leaves the DOM in the dropped position with no rollback** (`app.js:505,474`) — the lie persists (no SSE will correct a failed write).
- **`/raw` temp file leaks on client disconnect mid-download** (`server.py:257`; `BackgroundTask` unlink is skipped if send raises) and re-copies the whole blob per view.
- **`live.start_watching` can't recover a dead watcher** (`live.py:56`) — if the store root is removed/recreated, live refresh silently dies but keeps returning `True`.
- **`EventSource` has no `onerror`** (`app.js:1513`) — a restarted server (new token) → silent infinite 401 reconnect loop, board frozen, no hint.
- **Blocking disk I/O on the event loop per SSE tick** (`server.py:339` → `config.get_local_root()` re-reads the config file every second).
- **`Escape` in an inline editor/comment composer closes the whole drawer** (`app.js:746`; no `stopPropagation`) and loses the draft.
- **Popover positioned before async content loads** (`app.js:655` — labels popover measured at "Loading…" size, then overflows).

### W5. MINOR — Web quality
`post()`/`patch()` helpers exist but two call sites reinline the fetch boilerplate (`app.js:355,598`); `withParam` duplicates `withToken`'s query-append logic.

**Verified non-issues:** no XSS sink found (all dynamic strings via `textContent`/`createElement`); `/raw` refuses non-uploads so the web surface can't mint a traversal URL (though the store-side `_blob_path` still can — see X1); token comparison is bytes-safe; httpx drops `Authorization` on cross-origin redirects so the S3 upload redirect doesn't leak credentials.

---

## Recommended priority order

1. **X1** (path traversal) and **W1/W2** (web auth) — security, realistic triggers.
2. **X2** (corrupt-file blast radius) and **X3** (`grab` rollback) — data-integrity on the tool's core workflow.
3. **X4/X5** (cross-backend shape drift, unvalidated move) and **C1/C2/C3/C4/C5** (CLI wrong-behavior / silent-success) — user-visible correctness.
4. **T1** (error translation) — quality-of-life across the whole Trello backend.
5. Add a **test suite** — resolver exact-match, pos/rebalance math, and a cross-backend dict-shape conformance test would catch most of section X and the CLI majors.

The remaining MINOR items are edge cases, docs drift, and deduplication — worth a cleanup pass but not urgent.
