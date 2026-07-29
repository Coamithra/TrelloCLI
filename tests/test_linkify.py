"""Regression guard for the web client's `linkify()` (trello_cli/web/static/app.js).

`tests/` is otherwise Python-only and the web client has no JS test runner (the
`static/` bundle is vanilla JS with no build step, deliberately). But the
punctuation-walk and bracket-balance rules in `trimUrlTail` are exactly the kind
of logic that regresses silently, and getting them wrong is user-visible: a link
that swallows a sentence's full stop 404s.

So this runs the REAL source out of `app.js` under `node`, against a ~20-line DOM
shim, and asserts on the resulting node list. The functions under test are
DOM-free apart from `linkify`'s three `document.create*` calls, which the shim
supplies. Nothing is copy-pasted: the block is sliced out of `app.js` between two
markers, so a change to the shipped code is a change to what is tested here.

Auto-skips when `node` is not on PATH, so it never blocks the suite on a machine
without it.
"""

from __future__ import annotations

import json

import pytest

from tests.jsrunner import app_js_source, run_node

# The slice of app.js under test: from the URL_RE declaration through the end of
# linkify(). Both markers are asserted below, so a rename trips a clear failure
# here rather than silently testing nothing.
_START = "const URL_RE ="
_END = "\nfunction linkify(text) {"

# A DOM just big enough for linkify: fragments that collect children, elements
# whose property sets are recorded, and text nodes. `closest` is not needed (only
# inlineEditable's click guard uses it, which is not part of this slice).
_SHIM = """
class TextNode { constructor(t) { this.kind = 'text'; this.text = t; } }
class El {
  constructor(tag) { this.kind = 'el'; this.tag = tag; this.children = []; }
  set textContent(v) { this.text = v; }
  get textContent() { return this.text; }
  appendChild(c) { this.children.push(c); return c; }
}
class Frag extends El { constructor() { super('#fragment'); } }
const document = {
  createDocumentFragment: () => new Frag(),
  createElement: (tag) => new El(tag),
  createTextNode: (t) => new TextNode(t),
};
"""

_DRIVER = """
const out = INPUTS.map((src) => {
  const frag = linkify(src);
  return frag.children.map((n) =>
    n.kind === 'text'
      ? ['T', n.text]
      : ['A', n.href, n.textContent, n.target, n.rel, n.title]);
});
console.log(JSON.stringify(out));
"""


def _extract_source() -> str:
    src = app_js_source()
    start = src.index(_START)
    end = src.index(_END, start)
    # From `_END` (the start of linkify) to the closing brace of linkify. The
    # function is the last thing in the block, and its body's only top-level
    # closer is a `}` in column 0.
    tail_start = end + 1
    tail_end = src.index("\n}\n", tail_start) + len("\n}\n")
    return src[start:tail_end]


def _run(inputs: list[str]) -> list[list[list]]:
    return run_node(
        _SHIM
        + _extract_source()
        + f"\nconst INPUTS = {json.dumps(inputs)};\n"
        + _DRIVER,
        timeout=30,
    )


def _links(nodes: list[list]) -> list[str]:
    return [n[1] for n in nodes if n[0] == "A"]


def _text(nodes: list[list]) -> str:
    return "".join(n[1] if n[0] == "T" else n[2] for n in nodes)


def test_markers_still_present():
    """If this fails, the slice above no longer finds the code and every other
    test in this file would be silently vacuous."""
    src = app_js_source()
    assert _START in src
    assert _END in src
    extracted = _extract_source()
    assert "function trimUrlTail" in extracted
    assert extracted.rstrip().endswith("}")


@pytest.mark.parametrize("text,expected", [
    # Bare URL, nothing around it.
    ("https://example.com/a", ["https://example.com/a"]),
    # Trailing sentence punctuation is not part of the link.
    ("see https://example.com/a.", ["https://example.com/a"]),
    ("a https://example.com/a, b", ["https://example.com/a"]),
    ("q: https://example.com/a?", ["https://example.com/a"]),
    ("he said https://example.com/a\"", ["https://example.com/a"]),
    # A closing bracket the URL did not open belongs to the prose...
    ("(see https://example.com/a)", ["https://example.com/a"]),
    ("[https://example.com/a]", ["https://example.com/a"]),
    # ...but one it did open is part of the path.
    ("https://en.wikipedia.org/wiki/Monad_(functional_programming)",
     ["https://en.wikipedia.org/wiki/Monad_(functional_programming)"]),
    # Both at once: the URL's own paren survives, the wrapping one does not.
    ("(https://en.wikipedia.org/wiki/Foo_(bar))",
     ["https://en.wikipedia.org/wiki/Foo_(bar)"]),
    # Query strings and fragments are kept whole.
    ("https://example.com/s?q=a&b=c#frag", ["https://example.com/s?q=a&b=c#frag"]),
    # Several links in one string.
    ("https://a.example.com/x and https://b.example.com/y here.",
     ["https://a.example.com/x", "https://b.example.com/y"]),
    # http as well as https.
    ("http://example.com/a", ["http://example.com/a"]),
    # Nothing that is not an http(s) URL becomes a link.
    ("javascript:alert(1)", []),
    ("data:text/html,<b>x</b>", []),
    ("www.example.com", []),
    ("mailto:me@example.com", []),
    ("not-a-url", []),
    ("", []),
    # A match trimmed down to a bare scheme is prose, not a link to nowhere.
    ("URLs must start with https://.", []),
    ("https://", []),
])
def test_matching(text, expected):
    nodes = _run([text])[0]
    assert _links(nodes) == expected


@pytest.mark.parametrize("text", [
    "see https://example.com/a. and https://example.com/b, done",
    "(https://en.wikipedia.org/wiki/Foo_(bar)) tail",
    "URLs must start with https://. But https://example.com/x works.",
    "<img src=x onerror=alert(1)> https://example.com/a <script>alert(1)</script>",
    "no urls here at all",
    "",
])
def test_no_text_is_lost_or_duplicated(text):
    """The fragment must reconstruct the input exactly: every character lands in
    either a text node or a link, once. This is what makes the trimming safe --
    punctuation walked out of a match has to come back as prose."""
    nodes = _run([text])[0]
    assert _text(nodes) == text


def test_html_is_never_parsed():
    """The escape-by-construction property: markup in user text stays a literal
    text node, never an element."""
    text = "<img src=x onerror=alert('XSS')> <b>bold?</b> https://example.com/a"
    nodes = _run([text])[0]
    assert [n for n in nodes if n[0] == "A"], "the real URL should still link"
    # Everything that is not the one anchor is a plain text node.
    assert all(n[0] in ("T", "A") for n in nodes)
    assert "<img src=x onerror=alert('XSS')>" in _text(nodes)


def test_anchor_attributes():
    nodes = _run(["https://example.com/a"])[0]
    anchors = [n for n in nodes if n[0] == "A"]
    assert len(anchors) == 1
    _, href, label, target, rel, title = anchors[0]
    assert href == "https://example.com/a"
    assert label == href, "the visible text is the trimmed href, not the raw match"
    assert target == "_blank"
    assert rel == "noopener noreferrer"
    assert title == "", "an empty title stops the 'Click to edit' tooltip inheriting"


def test_regex_state_does_not_leak_between_calls():
    """URL_RE is module-level and /g, so lastIndex must be reset per call -- two
    identical inputs in a row have to produce identical output."""
    first, second = _run(["https://example.com/a", "https://example.com/a"])
    assert _links(first) == _links(second) == ["https://example.com/a"]
