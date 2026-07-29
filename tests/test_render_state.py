"""Coverage for the web client's render-state and live-reload helpers.

PR #43 added the JS that stops a board re-render eating half-typed composer text
and stops a burst of live `change` events becoming a burst of full reloads. It is
the bulk of that fix and the part a user actually feels, so it gets the same
treatment as `linkify`/`renderMarkdown`: the REAL source is sliced out of
`app.js`, run under `node` against a DOM shim (`tests/domshim.js`), and asserted
on. Nothing is copy-pasted -- a change to the shipped code is a change to what is
tested here. Shared plumbing lives in `tests/jsrunner.py`.

Three slices, marked in app.js:

- `render-state`   -- captureBoardState / restoreBoardState
- `board-load`     -- boardReqSeq, lastBoardSig, loadBoard
- `live-debounce`  -- LIVE_DEBOUNCE_MS, scheduleLiveReload

Each runs with only the globals it needs, and its collaborators (`api`,
`setStatus`, `renderBoard`, `loadBoard`) supplied as recording spies -- which is
what makes loadBoard and scheduleLiveReload testable at all without dragging in
SortableJS and `fetch`. The debounce tests use the shim's virtual clock; nothing
here sleeps.

Auto-skips when `node` is not on PATH.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.jsrunner import APP_JS, app_js_source, run_node, slice_between

SHIM = (Path(__file__).resolve().parent / "domshim.js").read_text(encoding="utf-8")

SLICES = {
    "render-state": (
        "// >>> render-state (sliced by tests/test_render_state.py) >>>",
        "// <<< render-state <<<",
    ),
    "board-load": (
        "// >>> board-load (sliced by tests/test_render_state.py) >>>",
        "// <<< board-load <<<",
    ),
    "live-debounce": (
        "// >>> live-debounce (sliced by tests/test_render_state.py) >>>",
        "// <<< live-debounce <<<",
    ),
}


def _slice(name: str) -> str:
    start, end = SLICES[name]
    return slice_between(app_js_source(), start, end)


_OUT = "\nfunction out(v) { console.log(JSON.stringify(v)); }\n"


# ---------------------------------------------------------------- sanity

def test_slice_markers_are_present():
    """If a marker moves, every test below would silently test nothing."""
    src = app_js_source()
    for name, (start, end) in SLICES.items():
        assert src.count(start) == 1, name
        assert src.count(end) == 1, name
        assert src.index(start) < src.index(end), name


@pytest.mark.parametrize("name,expected", [
    ("render-state", ["function captureBoardState()", "function restoreBoardState("]),
    ("board-load", ["let boardReqSeq", "let lastBoardSig", "async function loadBoard("]),
    ("live-debounce", ["const LIVE_DEBOUNCE_MS", "function scheduleLiveReload()"]),
])
def test_each_slice_carries_what_it_should(name, expected):
    block = _slice(name)
    for token in expected:
        assert token in block, f"{name}: {token}"


def test_fixture_markup_matches_app_js():
    """The shim's makeBoard() is the one thing here that can drift from the real
    markup: rename a class in columnEl and every capture/restore test would pass
    against a board the app no longer builds. So every class name and dataset key
    the fixture uses has to be one app.js actually writes."""
    src = app_js_source()
    fixture = SHIM[SHIM.index("function makeBoard("):]
    classes = set(re.findall(r"className = '([^']+)'", fixture))
    assert classes, "no class names found in makeBoard -- the slice above is wrong"
    for name in sorted(classes):
        assert f"= '{name}'" in src, f"fixture class {name!r} is not written by app.js"
    for key in sorted(set(re.findall(r"dataset\.(\w+)", fixture))):
        assert f"dataset.{key}" in src, f"fixture dataset key {key!r} is unused in app.js"


def test_shim_refuses_a_selector_it_cannot_parse():
    """A slice that outgrows the shim must fail loudly rather than quietly
    matching nothing (which would read as 'the state was not captured')."""
    got = run_node(SHIM + _OUT + """
const tries = ['.a > .b', '.a, .b', '.a:hover', '.a[x]'].map((sel) => {
  const root = makeBoard(['l1']);
  try { root.querySelectorAll(sel); return 'accepted'; }
  catch (e) { return 'threw'; }
});
out(tries);
""")
    assert got == ["threw"] * 4


# ------------------------------------------------- capture / restore

_STATE_HELPERS = """
let boardEl = null;

function snapshot(board) {
  const columns = {};
  board.querySelectorAll('.column').forEach((col) => {
    const id = col.dataset.listId;
    const input = composerIn(board, id);
    columns[id] = {
      value: input.value,
      focused: document.activeElement === input,
      start: input.selectionStart,
      end: input.selectionEnd,
      scrollTop: cardsIn(board, id).scrollTop,
    };
  });
  const form = board.querySelector('.add-list-form');
  const placeholder = board.querySelector('.add-list-placeholder');
  const addInput = board.querySelector('.add-list-input');
  return {
    columns,
    scrollLeft: board.scrollLeft,
    addList: {
      hidden: form.classList.contains('hidden'),
      placeholderHidden: placeholder.classList.contains('hidden'),
      value: addInput.value,
      focused: document.activeElement === addInput,
    },
  };
}

// Capture on one board, rebuild (as renderBoard does), restore onto the new one.
// `rebuild` gives the new board different lists; `preset` seeds the rebuilt DOM
// before the restore runs.
function roundTrip({ lists, setup, rebuild, preset }) {
  boardEl = makeBoard(lists);
  if (setup) setup(boardEl);
  const state = captureBoardState();
  boardEl = makeBoard(rebuild || lists);
  if (preset) preset(boardEl);
  restoreBoardState(state);
  return { state, after: snapshot(boardEl) };
}

function openAddList(board, value) {
  board.querySelector('.add-list-form').classList.remove('hidden');
  board.querySelector('.add-list-placeholder').classList.add('hidden');
  board.querySelector('.add-list-input').value = value;
}
"""


def _state(body: str):
    return run_node(SHIM + _slice("render-state") + _STATE_HELPERS + _OUT + body)


def test_composer_text_survives_a_rebuild():
    got = _state("""
out(roundTrip({ lists: ['l1', 'l2'], setup: (b) => {
  composerIn(b, 'l1').value = 'half typed card';
} }));
""")
    assert got["state"]["composers"]["l1"]["value"] == "half typed card"
    assert got["after"]["columns"]["l1"]["value"] == "half typed card"
    assert got["after"]["columns"]["l1"]["focused"] is False
    assert got["after"]["columns"]["l2"]["value"] == ""


def test_an_empty_unfocused_composer_is_not_captured():
    """Otherwise every render would carry a dictionary of empty strings around
    and re-focus nothing."""
    got = _state("out(roundTrip({ lists: ['l1', 'l2'] }));")
    assert got["state"]["composers"] == {}


def test_an_empty_focused_composer_is_captured_and_refocused():
    """Focus alone is worth preserving: you clicked '+ Add a card' and the board
    reloaded under you before you typed a character."""
    got = _state("""
out(roundTrip({ lists: ['l1', 'l2'], setup: (b) => { composerIn(b, 'l2').focus(); } }));
""")
    assert list(got["state"]["composers"]) == ["l2"]
    assert got["after"]["columns"]["l2"]["focused"] is True
    assert got["after"]["columns"]["l2"]["value"] == ""
    assert got["after"]["columns"]["l1"]["focused"] is False


@pytest.mark.parametrize("start,end", [(3, 3), (2, 7), (0, 0), (11, 11)])
def test_the_caret_comes_back_where_it_was(start, end):
    got = _state(f"""
out(roundTrip({{ lists: ['l1'], setup: (b) => {{
  const input = composerIn(b, 'l1');
  input.value = 'hello world';
  input.focus();
  input.setSelectionRange({start}, {end});
}} }}));
""")
    col = got["after"]["columns"]["l1"]
    assert (col["start"], col["end"]) == (start, end)
    assert col["focused"] is True


def test_the_caret_is_only_restored_on_the_focused_composer():
    """An unfocused input's caret is meaningless -- and focusing it to set one
    would steal focus from wherever the user actually is."""
    got = _state("""
out(roundTrip({ lists: ['l1'], setup: (b) => {
  const input = composerIn(b, 'l1');
  input.value = 'hello world';
  input.setSelectionRange(4, 4);
} }));
""")
    assert got["after"]["columns"]["l1"]["value"] == "hello world"
    assert got["after"]["columns"]["l1"]["focused"] is False
    assert got["after"]["columns"]["l1"]["start"] == 0


def test_each_column_keeps_its_own_text():
    got = _state("""
out(roundTrip({ lists: ['l1', 'l2', 'l3'], setup: (b) => {
  composerIn(b, 'l1').value = 'one';
  composerIn(b, 'l3').value = 'three';
  composerIn(b, 'l3').focus();
} }));
""")
    cols = got["after"]["columns"]
    assert cols["l1"]["value"] == "one"
    assert cols["l2"]["value"] == ""
    assert cols["l3"]["value"] == "three"
    assert [cols[k]["focused"] for k in ("l1", "l2", "l3")] == [False, False, True]


def test_a_column_that_vanished_is_dropped_without_taking_the_rest_down():
    """The list was archived (by another agent's CLI write, say) between the
    capture and the rebuild. Its text has nowhere to go; the others still must
    land, so the lookup bails per column rather than throwing."""
    got = _state("""
out(roundTrip({
  lists: ['l1', 'l2'],
  setup: (b) => {
    composerIn(b, 'l1').value = 'survives';
    composerIn(b, 'l2').value = 'its column is about to go away';
  },
  rebuild: ['l1'],
}));
""")
    assert sorted(got["state"]["composers"]) == ["l1", "l2"]
    assert got["after"]["columns"] == {
        "l1": {"value": "survives", "focused": False, "start": 0, "end": 0, "scrollTop": 0},
    }


def test_scroll_position_is_restored():
    got = _state("""
out(roundTrip({ lists: ['l1', 'l2'], setup: (b) => {
  b.scrollLeft = 420;
  cardsIn(b, 'l1').scrollTop = 137;
} }));
""")
    assert got["state"]["scrollLeft"] == 420
    assert got["state"]["scrollTops"] == {"l1": 137}
    assert got["after"]["scrollLeft"] == 420
    assert got["after"]["columns"]["l1"]["scrollTop"] == 137
    assert got["after"]["columns"]["l2"]["scrollTop"] == 0


def test_a_zero_scrolltop_is_not_captured():
    """An unscrolled column carries no state, so a rebuild that scrolled it for
    its own reasons is left alone rather than yanked back to the top."""
    got = _state("""
out(roundTrip({
  lists: ['l1'],
  setup: (b) => { cardsIn(b, 'l1').scrollTop = 0; },
  preset: (b) => { cardsIn(b, 'l1').scrollTop = 99; },
}));
""")
    assert got["state"]["scrollTops"] == {}
    assert got["after"]["columns"]["l1"]["scrollTop"] == 99


def test_an_open_add_list_composer_is_restored_open():
    got = _state("""
out(roundTrip({ lists: ['l1'], setup: (b) => {
  openAddList(b, 'Backlog');
  b.querySelector('.add-list-input').focus();
} }));
""")
    assert got["state"]["addList"] == {"value": "Backlog", "focused": True}
    assert got["after"]["addList"] == {
        "hidden": False, "placeholderHidden": True, "value": "Backlog", "focused": True,
    }


def test_an_open_but_unfocused_add_list_composer_keeps_its_text_without_stealing_focus():
    got = _state("""
out(roundTrip({ lists: ['l1'], setup: (b) => { openAddList(b, 'Backlog'); } }));
""")
    assert got["after"]["addList"]["value"] == "Backlog"
    assert got["after"]["addList"]["hidden"] is False
    assert got["after"]["addList"]["focused"] is False


def test_a_closed_add_list_composer_stays_closed():
    got = _state("out(roundTrip({ lists: ['l1'] }));")
    assert got["state"]["addList"] is None
    assert got["after"]["addList"] == {
        "hidden": True, "placeholderHidden": False, "value": "", "focused": False,
    }


def test_a_caret_restore_that_throws_does_not_abort_the_rest_of_the_restore():
    """setSelectionRange throws on an input whose type does not support it. The
    guard around it exists so a browser quirk cannot take the whole render down
    -- which would leave the board rendered but unscrolled and half-restored."""
    got = _state("""
out(roundTrip({
  lists: ['l1', 'l2'],
  setup: (b) => {
    const first = composerIn(b, 'l1');
    first.value = 'focused and broken';
    first.focus();
    composerIn(b, 'l2').value = 'restored after the throw';
    b.scrollLeft = 300;
  },
  preset: (b) => {
    composerIn(b, 'l1').setSelectionRange = () => { throw new Error('unsupported'); };
  },
}));
""")
    assert got["after"]["columns"]["l1"]["value"] == "focused and broken"
    assert got["after"]["columns"]["l1"]["focused"] is True
    assert got["after"]["columns"]["l2"]["value"] == "restored after the throw"
    assert got["after"]["scrollLeft"] == 300


def test_composer_text_does_not_cross_a_board_switch():
    """renderBoard captures and restores unconditionally, so a board switch runs
    a capture of the OLD board against the NEW one. What bounds it is the
    per-list-id lookup in restoreBoardState: board B has none of board A's list
    ids, so every composer is dropped.

    NOTE: `scrollLeft` and the add-list composer are keyed on nothing, so they DO
    carry across. Asserted here descriptively -- this documents today's behaviour,
    it is not a statement that it is desirable (see the follow-up card).
    """
    got = _state("""
out(roundTrip({
  lists: ['a1', 'a2'],
  setup: (b) => {
    composerIn(b, 'a1').value = 'typed on board A';
    composerIn(b, 'a1').focus();
    b.scrollLeft = 500;
    openAddList(b, 'a new list for board A');
  },
  rebuild: ['b1', 'b2'],
}));
""")
    assert [c["value"] for c in got["after"]["columns"].values()] == ["", ""]
    assert not any(c["focused"] for c in got["after"]["columns"].values())
    # Documented current behaviour, not an endorsement:
    assert got["after"]["scrollLeft"] == 500
    assert got["after"]["addList"]["value"] == "a new list for board A"


# ------------------------------------------------------------ loadBoard

_LOAD_HELPERS = """
let statuses = [];
let rendered = [];
let apiCalls = [];
let apiImpl = null;

function setStatus(msg, isError) { statuses.push([String(msg), !!isError]); }
function renderBoard(data) { rendered.push(data.board.id); }
async function api(path) { apiCalls.push(path); return apiImpl(path); }

function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function payload(id, extra) {
  return { board: { id, name: 'Board ' + id }, lists: [], cards: extra || [] };
}

function report() { return { statuses, rendered, apiCalls, sig: lastBoardSig }; }
"""


def _load(body: str):
    return run_node(SHIM + _slice("board-load") + _LOAD_HELPERS + _OUT + body)


def test_a_user_initiated_load_narrates_itself():
    got = _load("""
apiImpl = () => payload('B1');
await loadBoard('B1');
out(report());
""")
    assert got["rendered"] == ["B1"]
    assert got["statuses"] == [["Loading…", False], ["Board B1", False]]
    assert got["apiCalls"] == ["/api/boards/B1"]


def test_a_quiet_load_renders_without_any_status_churn():
    """Nobody asked for this refresh, so the top-right should not flash
    'Loading...' -> board name every time a live change lands."""
    got = _load("""
apiImpl = () => payload('B1');
await loadBoard('B1', { quiet: true });
out(report());
""")
    assert got["rendered"] == ["B1"]
    assert got["statuses"] == []


def test_a_quiet_reload_of_an_unchanged_board_is_skipped():
    """The whole point of the signature: every write the user makes reaches the
    store, comes back as a live change, and a sync client replays it again a
    moment later. Comparing the payload keeps that a single redraw without ever
    second-guessing whose change it was."""
    got = _load("""
apiImpl = () => payload('B1');
await loadBoard('B1', { quiet: true });
await loadBoard('B1', { quiet: true });
await loadBoard('B1', { quiet: true });
out(report());
""")
    assert got["rendered"] == ["B1"]
    assert got["apiCalls"] == ["/api/boards/B1"] * 3, "the fetch still happens; only the redraw is skipped"


def test_a_quiet_reload_whose_payload_changed_redraws():
    got = _load("""
let n = 0;
apiImpl = () => payload('B1', [{ id: 'c' + (n++) }]);
await loadBoard('B1', { quiet: true });
await loadBoard('B1', { quiet: true });
out(report());
""")
    assert got["rendered"] == ["B1", "B1"]


def test_a_reload_the_user_asked_for_is_never_skipped():
    """Identical payload, but the signature compare is gated on `quiet` -- a
    non-quiet reload is an explicit request and always redraws."""
    got = _load("""
apiImpl = () => payload('B1');
await loadBoard('B1');
await loadBoard('B1');
out(report());
""")
    assert got["rendered"] == ["B1", "B1"]


def test_the_signature_is_per_payload_not_per_board():
    got = _load("""
apiImpl = (path) => payload(path.split('/').pop());
await loadBoard('A', { quiet: true });
await loadBoard('B', { quiet: true });
await loadBoard('B', { quiet: true });
out(report());
""")
    assert got["rendered"] == ["A", "B"]


def test_a_superseded_response_renders_nothing_and_records_nothing():
    """A slow response for the board you navigated away from must not render
    over the newer one, nor leave its signature behind -- that would make the
    next quiet reload of the board actually on screen skip."""
    got = _load("""
const first = deferred();
const second = deferred();
const queue = [first.promise, second.promise];
apiImpl = () => queue.shift();

const slow = loadBoard('A');
const quick = loadBoard('B');
second.resolve(payload('B'));
await quick;
const beforeLate = { rendered: [...rendered], sig: lastBoardSig };
first.resolve(payload('A'));
await slow;
out({ ...report(), beforeLate });
""")
    assert got["rendered"] == ["B"]
    assert got["sig"] == got["beforeLate"]["sig"]
    assert json.loads(got["sig"])["board"]["id"] == "B"
    assert got["statuses"][-1] == ["Board B", False]
    assert ["Board A", False] not in got["statuses"]


def test_a_failed_load_reports_the_error_even_when_quiet():
    """Errors are never quiet -- a refresh nobody asked for still has to say so
    when it breaks, or the board silently goes stale."""
    got = _load("""
apiImpl = () => Promise.reject(new Error('boom'));
await loadBoard('B1', { quiet: true });
out(report());
""")
    assert got["rendered"] == []
    assert got["statuses"] == [["Load failed: boom", True]]
    assert got["sig"] is None


def test_a_failed_load_leaves_the_next_quiet_reload_free_to_redraw():
    got = _load("""
let fail = true;
apiImpl = () => (fail ? Promise.reject(new Error('boom')) : payload('B1'));
await loadBoard('B1', { quiet: true });
fail = false;
await loadBoard('B1', { quiet: true });
out(report());
""")
    assert got["rendered"] == ["B1"]


# -------------------------------------------------- scheduleLiveReload

_LIVE_HELPERS = """
let liveReloadTimer = null;
let currentBoardId = 'B1';
let liveDragging = false;
let pendingReload = false;
let calls = [];

function loadBoard(id, opts) { calls.push([id, opts || null]); }

function report() {
  return { calls, pendingReload, timerIsNull: liveReloadTimer === null,
           pendingDelays: clock.pending() };
}
"""


def _live(body: str):
    return run_node(SHIM + _slice("live-debounce") + _LIVE_HELPERS + _OUT + body)


def test_the_reload_waits_for_the_debounce_window():
    got = _live("""
scheduleLiveReload();
clock.advance(LIVE_DEBOUNCE_MS - 1);
const early = [...calls];
clock.advance(1);
out({ ...report(), early });
""")
    assert got["early"] == []
    assert got["calls"] == [["B1", {"quiet": True}]]


def test_the_scheduled_delay_is_the_declared_constant():
    got = _live("""
scheduleLiveReload();
out({ ...report(), constant: LIVE_DEBOUNCE_MS });
""")
    assert got["constant"] == 350
    assert got["pendingDelays"] == [350]


def test_a_burst_of_changes_collapses_to_one_reload():
    """One store write is rarely one `change`: re-sorting a column rewrites every
    card file in it, and a sync client replays those writes again. Each used to
    be a full board reload."""
    got = _live("""
scheduleLiveReload();
clock.advance(100);
scheduleLiveReload();
clock.advance(100);
scheduleLiveReload();
const armed = clock.pending();
clock.advance(LIVE_DEBOUNCE_MS - 1);
const early = [...calls];
clock.advance(1);
out({ ...report(), armed, early });
""")
    assert got["armed"] == [350], "each call replaces the previous timer rather than adding one"
    assert got["early"] == [], "the window runs from the LAST change, not the first"
    assert got["calls"] == [["B1", {"quiet": True}]]


def test_the_timer_handle_is_cleared_before_the_body_runs():
    got = _live("""
scheduleLiveReload();
clock.advance(LIVE_DEBOUNCE_MS);
out(report());
""")
    assert got["timerIsNull"] is True
    assert got["pendingDelays"] == []


def test_nothing_reloads_when_no_board_is_selected():
    got = _live("""
scheduleLiveReload();
currentBoardId = null;
clock.advance(LIVE_DEBOUNCE_MS);
out(report());
""")
    assert got["calls"] == []
    assert got["pendingReload"] is False


def test_a_reload_landing_mid_drag_is_handed_to_pending_reload():
    """Reloading now would yank the card out from under the pointer; dropping it
    would hide another agent's edit until some unrelated later reload. onEnd
    consumes pendingReload."""
    got = _live("""
scheduleLiveReload();
liveDragging = true;
clock.advance(LIVE_DEBOUNCE_MS);
out(report());
""")
    assert got["calls"] == []
    assert got["pendingReload"] is True


def test_the_drag_check_happens_when_the_timer_fires_not_when_it_is_scheduled():
    """A drag that started and finished inside the debounce window must not
    defer the reload -- there is nothing left to yank."""
    got = _live("""
liveDragging = true;
scheduleLiveReload();
clock.advance(LIVE_DEBOUNCE_MS - 1);
liveDragging = false;
clock.advance(1);
out(report());
""")
    assert got["calls"] == [["B1", {"quiet": True}]]
    assert got["pendingReload"] is False


def test_a_reload_scheduled_before_a_drag_starts_is_still_deferred():
    """The mirror of the case above: the drag starts during the window and is
    still going when the timer fires."""
    got = _live("""
scheduleLiveReload();
clock.advance(200);
liveDragging = true;
clock.advance(LIVE_DEBOUNCE_MS - 200);
out(report());
""")
    assert got["calls"] == []
    assert got["pendingReload"] is True


def test_app_js_is_the_only_source_of_truth():
    """Belt and braces: none of the three slices may be empty, and the file they
    come from must be the shipped one."""
    assert APP_JS.name == "app.js"
    for name in SLICES:
        assert len(_slice(name).splitlines()) > 5, name
