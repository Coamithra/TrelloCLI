"""Coverage for the web client's `?card=` deep-link param.

`setCardInUrl` is what makes F5 reopen the card you had open. It is four lines,
but every one of them is a thing that has gone wrong in this file before: losing
`?token=` logs the page out, losing `?board=` snaps it back to the first board,
and `pushState` instead of `replaceState` turns every card click into a
back-button stop. So it gets the same treatment as the other web-client logic --
the REAL source sliced out of `app.js` and run under `node` (see
`tests/jsrunner.py`), nothing copy-pasted.

The shim here is `location` + `history` and nothing else: that is the entire
surface the slice touches. Auto-skips when `node` is not on PATH.
"""

from __future__ import annotations

from tests.jsrunner import app_js_source, run_node, slice_between

START = "// >>> card-url (sliced by tests/test_card_url.py) >>>"
END = "// <<< card-url <<<"

# `URL` and `URLSearchParams` are node globals, so the shim only has to supply
# the two browser objects the slice reaches for -- and record what it did to
# them. `replaceState` swaps the href WITHOUT appending to `pushed`, which is
# what lets a test tell the two apart.
SHIM = """
let location = { href: '' };
const history = {
  pushed: [],
  replaced: [],
  replaceState(state, title, url) {
    this.replaced.push(String(url));
    location.href = String(url);
  },
  pushState(state, title, url) {
    this.pushed.push(String(url));
    location.href = String(url);
  },
};
function out(v) { console.log(JSON.stringify(v)); }
function report() {
  const url = new URL(location.href);
  const params = {};
  url.searchParams.forEach((v, k) => { params[k] = v; });
  return {
    href: location.href,
    params,
    replaced: history.replaced.length,
    pushed: history.pushed.length,
  };
}
"""


def _slice() -> str:
    return slice_between(app_js_source(), START, END)


def _run(body: str):
    return run_node(SHIM + _slice() + body)


def test_slice_markers_are_present():
    """If a marker moves, every test below would silently test nothing."""
    src = app_js_source()
    assert src.count(START) == 1
    assert src.count(END) == 1
    assert src.index(START) < src.index(END)
    assert "function setCardInUrl(" in _slice()


def test_the_card_id_lands_in_the_url():
    got = _run("""
location.href = 'http://localhost:8787/?board=b1';
setCardInUrl('c0ffee');
out(report());
""")
    assert got["params"]["card"] == "c0ffee"


def test_the_board_and_token_params_survive():
    """Dropping ?token= logs the page out of its own API; dropping ?board= snaps
    it back to the first board on the next reload."""
    got = _run("""
location.href = 'http://localhost:8787/?board=b1&token=s3cret';
setCardInUrl('c1');
out(report());
""")
    assert got["params"] == {"board": "b1", "token": "s3cret", "card": "c1"}


def test_reopening_a_different_card_replaces_the_param():
    got = _run("""
location.href = 'http://localhost:8787/?board=b1';
setCardInUrl('c1');
setCardInUrl('c2');
out(report());
""")
    assert got["params"]["card"] == "c2"
    assert got["href"].count("card=") == 1


def test_closing_the_card_removes_only_the_card_param():
    got = _run("""
location.href = 'http://localhost:8787/?board=b1&token=s3cret&card=c1';
setCardInUrl(null);
out(report());
""")
    assert got["params"] == {"board": "b1", "token": "s3cret"}
    assert "card" not in got["href"]


def test_clearing_a_url_that_never_had_a_card_is_a_no_op_on_the_params():
    """closeDetail() runs on every drawer close, including the manage-boards
    panel, which never set one."""
    got = _run("""
location.href = 'http://localhost:8787/?board=b1';
setCardInUrl(null);
out(report());
""")
    assert got["params"] == {"board": "b1"}


def test_no_history_entry_is_pushed():
    """The drawer is not a page. A pushState per card click would make Back walk
    back through every card you glanced at instead of leaving the app."""
    got = _run("""
location.href = 'http://localhost:8787/?board=b1';
setCardInUrl('c1');
setCardInUrl('c2');
setCardInUrl(null);
out(report());
""")
    assert got["pushed"] == 0
    assert got["replaced"] == 3


def test_an_empty_id_clears_rather_than_writing_an_empty_param():
    """`?card=` with no value would survive a reload and resolve to nothing --
    an openDetail('') call whose 404 the restore path then has to swallow."""
    got = _run("""
location.href = 'http://localhost:8787/?board=b1&card=c1';
setCardInUrl('');
out(report());
""")
    assert got["params"] == {"board": "b1"}
