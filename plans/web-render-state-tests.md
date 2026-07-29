# Node coverage for the web client's render-state and live-reload helpers

Card `4e1665f7` (Doing). Branch `test/web-render-state`, worktree `.trees/wt3`.

## Context

PR #43 (`fix/column-sort-refresh`, merged `fe40c99`) added the JS that stops a board
re-render eating the user's half-typed composer text and stops a burst of live `change`
events becoming a burst of full reloads. That JS is the bulk of the user-visible fix and
has no automated coverage. This card adds it, tests-only.

Three helper groups in `trello_cli/web/static/app.js`:

- `captureBoardState()` / `restoreBoardState()` (app.js:592-650) — per-column composer
  text + focus + caret, the open "Add another list" composer, board `scrollLeft`,
  per-column `scrollTop`.
- `loadBoard(id, {quiet})` (app.js:730-751) with `boardReqSeq` / `lastBoardSig` — the
  payload compare that skips a redraw of an unchanged board, and the stale-response drop.
- `scheduleLiveReload()` (app.js:1972-1983) with `LIVE_DEBOUNCE_MS` / `liveReloadTimer` —
  coalescing, and the mid-drag hand-off to `pendingReload`.

### Card claims that did not survive contact with master — PLEASE RULE

1. **There is no board-switch guard in `renderBoard`.** The card describes "the guard in
   renderBoard that drops the captured state when `data.board.id != renderedBoardId`".
   No `renderedBoardId` identifier exists anywhere in `app.js` (grepped). `renderBoard`
   captures and restores unconditionally. What actually protects a board switch is
   narrower: `restoreBoardState` looks each composer up by its list id and bails when the
   column is absent, so composers do not cross boards — but `scrollLeft` and the open
   add-list composer **do** carry from the old board to the new one.
   *Ask:* do I (a) write a test that asserts today's actual behaviour with a comment
   saying it is descriptive, (b) leave the board-switch case untested, or (c) is this a
   follow-up card to add the guard? I lean (a) — it is a regression guard either way.

2. **`lastBoardSig` is recorded BEFORE the render, not after a successful one.**
   app.js:744 sets `lastBoardSig = sig` and app.js:745 then calls `renderBoard(data)`.
   If `renderBoard` throws, the catch reports a load error while `lastBoardSig` already
   claims a board that was never drawn — the next quiet reload of the same payload then
   skips, leaving the stale board on screen. The card describes the intended rule
   ("recorded only after a successful render"); the code does not implement it.
   Per the card's own no-fixes rule I am **not** touching this. *Ask:* follow-up card?
   I will test the part that does hold — a superseded response records no signature.

Neither of these is a change I make in this card.

## Design

Same vehicle as `tests/test_linkify.py` and `tests/test_markdown.py`: slice the REAL
source out of `app.js`, run it under `node` against a DOM shim, assert on the result.
Nothing is copy-pasted, so a change to shipped code is a change to what is tested.

`tests/test_markdown.py` (PR #46) already grew the second copy of the node-runner
plumbing. This card would be the third, so the plumbing gets factored out — but only the
plumbing.

### `tests/jsrunner.py` — new, shared

The genuinely duplicated part of the two existing files:

- `APP_JS`, `STATIC` paths.
- `node_or_skip()` — `shutil.which('node')` or `pytest.skip`.
- `slice_between(text, start, end)` — marker slice with both markers asserted.
- `run_node(script)` — write a temp `.mjs`, run it, assert rc == 0 with stderr in the
  message, `json.loads(stdout)`.

`test_linkify.py` and `test_markdown.py` switch their `_run` bodies to call it; **their
shims, drivers, slices, docstrings and every assertion stay exactly as they are**, so the
diff there is a dozen lines of plumbing and the 89 currently-green JS tests keep testing
what they test. (`test_linkify` moves from `node -e` to a temp file, which also retires
its 32 KB-command-line caveat.) Say the word and I skip this refactor and keep the third
copy instead — but three copies is the outcome I read the card as wanting avoided.

Not shared: the DOM shims themselves. linkify's records `text` on an element,
markdown's records `attrs`/`style`, and this card's is a different animal again
(selectors, dataset, focus). Each is deliberately the minimum its file needs, and merging
them means every one of them tests through machinery it does not use.

### `tests/domshim.js` — new

A real `.js` file rather than a Python string, because it is ~150 lines and wants to be
readable and editable. Loaded with `read_text` and prepended to the script, the same way
`test_markdown.py` already loads the vendored parser off disk.

Provides exactly what the three slices touch:

- `El`: `tagName`, `className`/`classList` (`add`/`remove`/`contains`), `dataset`,
  `children`/`parentNode`, `append`/`appendChild`, `value`, `placeholder`,
  `textContent`, `scrollTop`, `scrollLeft`, `selectionStart`/`selectionEnd`,
  `setSelectionRange`, `focus()` (sets `document.activeElement`), no-op
  `addEventListener`, and an `innerHTML` setter that only accepts `''` (it clears
  children — the one use in `renderBoard`, and refusing anything else keeps the shim from
  quietly pretending to parse HTML).
- A small selector engine: descendant combinators over compound selectors of tag,
  `.class` and `[attr="value"]` — enough for `.composer-input`, `.cards`,
  `.column[data-list-id="x"] .composer-input`, `.add-list-form`. Anything it cannot parse
  throws, so an unsupported selector fails loudly instead of silently matching nothing.
  Backs `querySelector`/`querySelectorAll` on any element and `closest()` walking
  `parentNode`.
- `document` with `createElement` / `activeElement`.
- **Fake timers**: `setTimeout`/`clearTimeout` over a queue, plus `clock.advance(ms)` and
  `clock.pending()`. No real sleeps anywhere, so the debounce tests are deterministic and
  instant.
- `makeBoard([{id, name}, …])` — builds a board element in the shape `columnEl` /
  `addListEl` produce (`.column[data-list-id]` > `.cards[data-list-id]` + `.composer` >
  `.composer-input`, and the `.add-list` > `.add-list-placeholder` + `.add-list-form.hidden`
  > `.add-list-input` tail).

That fixture is the one thing here that can drift from production markup, so a Python
test asserts every class name and dataset key it uses is present in `columnEl` /
`addListEl` in `app.js`. Cheap, and it catches a rename that would otherwise make the
whole file vacuously pass.

### `app.js` — three pairs of marker comments, nothing else

Mirroring `// >>> markdown-render (sliced by tests/test_markdown.py) >>>`:

| slice | from | to |
|---|---|---|
| `render-state` | before `captureBoardState` | after `restoreBoardState` |
| `board-load` | before `let boardReqSeq = 0;` | after `loadBoard` |
| `live-debounce` | before `const LIVE_DEBOUNCE_MS` | after `scheduleLiveReload` |

Comment-only — **zero behaviour change**, which is the card's hard constraint. The
alternative is zero-touch slicing off neighbouring identifiers (what `test_linkify.py`
does), but for `board-load` the nearest anchor below is a decorative `// ── detail
drawer ──` banner, and a slice anchored on ASCII art is the kind of thing that silently
starts testing nothing. Markers also tell the next person editing these functions that a
test slices them. Say so if you would rather I not touch `app.js` at all and I will use
identifier anchors.

### `tests/test_render_state.py` — new, the actual card

Each slice runs with only the globals it needs declared in the harness (`boardEl`,
`currentBoardId`, `liveDragging`, `pendingReload`), and its collaborators
(`api`, `setStatus`, `renderBoard`, `loadBoard`) supplied as recording spies. That is
what makes `loadBoard` and `scheduleLiveReload` testable at all without dragging in
SortableJS and `fetch`.

**A. slice sanity** — markers present; each slice contains the functions it should be
carrying; fixture markup matches `app.js` (above). Without these the rest is vacuous.

**B. capture/restore round-trip** (the card's stated priority — most mechanical, protects
the actual user-visible fix):

1. composer text survives a rebuild; unfocused and non-empty is captured.
2. an empty **unfocused** composer is not captured; an empty **focused** one is, and
   comes back focused (the `!input.value && activeElement !== input` rule).
3. caret position restored; a non-collapsed selection (`start != end`) restored.
4. several columns each keep their own text, independently.
5. a column that vanished in the rebuild (archived list) is dropped silently — no throw,
   and the surviving columns still restore.
6. `scrollLeft` restored; per-column `scrollTop` restored; a zero `scrollTop` is not
   captured and does not clobber the rebuilt value.
7. add-list composer open → restored open, with its value, placeholder hidden, focused;
   closed → stays closed, nothing restored.
8. a `setSelectionRange` that throws is swallowed and the rest of the restore still runs
   (that `try/catch` exists precisely so a browser quirk cannot take the render down).
9. board switch: capture on board A's DOM, restore into board B's — composers dropped.
   Scope per your ruling on question 1 above.

**C. `loadBoard`**:

1. non-quiet: `Loading…` then the board name; renders.
2. quiet: renders on first load, and sets **no** status at all.
3. quiet + identical payload → render skipped.
4. quiet + changed payload → renders.
5. **non-quiet + identical payload → renders anyway** — a reload the user asked for is
   never skipped.
6. a superseded response (second `loadBoard` started first) neither renders, nor sets
   status, nor records a signature.
7. error path: rejection sets the error status even under `quiet`, and leaves
   `lastBoardSig` alone so the next quiet reload still redraws.
8. the signature is per-payload, not per-board: A, then B, then a quiet reload of B with
   B's payload, skips.

**D. `scheduleLiveReload`** (fake clock throughout):

1. nothing fires before `LIVE_DEBOUNCE_MS`; exactly one `loadBoard(currentBoardId,
   {quiet: true})` at it.
2. a burst of calls inside the window collapses to one reload, timed from the last call.
3. no `currentBoardId` at fire time → no reload.
4. `liveDragging` at fire time → no reload, `pendingReload === true`.
5. `liveDragging` at **schedule** time but clear at fire time → reload happens (the
   deferral is decided when the timer fires, not when it is scheduled).
6. `liveReloadTimer` is nulled before the body runs.
7. the delay handed to `setTimeout` is exactly `LIVE_DEBOUNCE_MS`.

## Verification

- `.venv/Scripts/python.exe -m pytest` in `.trees/wt3` — full suite green, and the JS
  files **run** rather than skip (`node` v24.13.1 is on PATH here; `-rs` must show no
  skips for the three JS files).
- Prove the new slices are not vacuous: temporarily break each marker and confirm the
  sanity test fails loudly, then revert. Same for the shim's unsupported-selector throw.
- `git diff master -- trello_cli/` must be **comment-only** — that is the card's hard
  constraint, and it is checkable by eye in one screen.
- The two migrated JS test files must be unchanged below their `_run`: `git diff` shows
  plumbing only.
- No CLI behaviour is touched, so no board/scratch-board verification applies.

## Out of scope

- Any fix to `app.js`. The two findings above are reported, not repaired.
- `initDragging` / the drag `onEnd` that consumes `pendingReload` — needs SortableJS and
  `fetch` shimmed; the `liveDragging → pendingReload` rule itself is covered at the timer
  site.
- `initLive`'s own mid-drag deferral and its "drop the reload queued for the board we are
  leaving" — needs an `EventSource` + `location` shim. Same rule, second site; propose as
  a follow-up card rather than doubling this one's shim.
- A JS test runner, npm dependencies, jsdom. The repo's `static/` bundle is deliberately
  build-step-free and the node-plus-shim vehicle is the established answer.
- `renderBoard` itself beyond its capture/restore bracketing (it pulls in `columnEl`,
  `cardEl`, `addListEl`, `initDragging`).
