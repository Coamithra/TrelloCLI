"""Regression + XSS guard for the web client's `renderMarkdown()`.

Same trick as `tests/test_linkify.py` (read its docstring first): there is no JS
test runner in this repo, so the REAL source is sliced out of `app.js`, loaded
under `node` next to the REAL vendored `markdown-it.min.js`, and driven against
a small DOM shim. Nothing is copy-pasted -- a change to the shipped renderer is
a change to what is tested here.

This matters more than a formatting nicety. `renderMarkdown` is the code that
decides which elements and attributes user-authored card text may produce, and
it is deliberately built so that no sanitizer is needed: markdown-it is a parser
only, the walker calls `document.createElement` against a whitelist, and nothing
touches innerHTML. The property tests at the bottom (`test_only_whitelisted_*`,
`test_no_event_handler_attributes`, `test_every_href_is_http`) are what turn
that claim into something checkable, so they run over the whole hostile corpus
rather than one case at a time.

Auto-skips when `node` is not on PATH.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "trello_cli" / "web" / "static"
APP_JS = STATIC / "app.js"
VENDOR_MD = STATIC / "vendor" / "markdown-it.min.js"

# The slice under test runs from linkify's regex (renderMarkdown calls linkify
# on text tokens, so it must come along) through the end of the markdown block.
# Both markers are asserted below, so a rename fails loudly here instead of
# silently testing nothing.
_START = "const URL_RE ="
_END = "// <<< markdown-render <<<"

# A DOM just big enough for the walker: elements that record their tag,
# children, attributes and style, plus text nodes. `markdownit` is read off
# `window`, so the shim aliases it to the globalThis the vendored UMD bundle
# assigns itself to.
_SHIM = """
class TextNode { constructor(t) { this.kind = 'text'; this.text = t; } }
class El {
  constructor(tag) {
    this.kind = 'el'; this.tag = tag; this.children = [];
    this.attrs = {}; this.style = {};
  }
  set textContent(v) { this.children = v === '' ? [] : [new TextNode(v)]; this._text = v; }
  set className(v) { this.attrs.class = v; }
  get className() { return this.attrs.class; }
  get textContent() { return this._text; }
  set href(v) { this.attrs.href = v; }
  set target(v) { this.attrs.target = v; }
  set rel(v) { this.attrs.rel = v; }
  set title(v) { this.attrs.title = v; }
  get href() { return this.attrs.href; }
  setAttribute(k, v) { this.attrs[k] = v; }
  appendChild(c) { this.children.push(c); return c; }
}
class Frag extends El { constructor() { super('#fragment'); } }
const document = {
  createDocumentFragment: () => new Frag(),
  createElement: (tag) => new El(tag),
  createTextNode: (t) => new TextNode(t),
};
const window = globalThis;
"""

# Serialize the produced tree as nested [tag, attrs, style, children] / ['#text', s].
_DRIVER = """
function ser(n) {
  if (n.kind === 'text') return ['#text', n.text];
  return [n.tag, n.attrs, n.style, n.children.map(ser)];
}
console.log(JSON.stringify(INPUTS.map((src) => ser(renderMarkdown(src)))));
"""


def _extract_source() -> str:
    src = APP_JS.read_text(encoding="utf-8")
    start = src.index(_START)
    end = src.index(_END, start) + len(_END)
    return src[start:end]


def _run(inputs: list[str], *, with_parser: bool = True) -> list:
    """Render each input. `with_parser=False` omits the vendored bundle, which
    is exactly what a failed `<script>` load looks like to app.js -- the only
    way to reach renderMarkdown's degraded branch."""
    node = shutil.which("node")
    if node is None:  # pragma: no cover - environment-dependent
        pytest.skip("node not on PATH")
    script = (
        _SHIM
        + (VENDOR_MD.read_text(encoding="utf-8") if with_parser else "")
        + "\n"
        + _extract_source()
        + f"\nconst INPUTS = {json.dumps(inputs)};\n"
        + _DRIVER
    )
    # Via a temp FILE, not `node -e`: the vendored parser is ~125 KB and Windows
    # caps a command line at ~32 KB (test_linkify.py's slice is small enough to
    # inline, this is not).
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run.mjs"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run(
            [node, str(path)],
            capture_output=True, text=True, timeout=60,
        )
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def _one(src: str):
    return _run([src])[0]


def _walk(node):
    """Yield every node in the tree, depth first."""
    yield node
    if node[0] != "#text":
        for child in node[3]:
            yield from _walk(child)


def _tags(node) -> list[str]:
    return [n[0] for n in _walk(node) if n[0] not in ("#text", "#fragment")]


def _text(node) -> str:
    return "".join(n[1] for n in _walk(node) if n[0] == "#text")


def _anchors(node) -> list[dict]:
    return [n[1] for n in _walk(node) if n[0] == "a"]


def test_markers_still_present():
    """If this fails the slice finds nothing and every test below is vacuous."""
    src = APP_JS.read_text(encoding="utf-8")
    assert _START in src
    assert _END in src
    extracted = _extract_source()
    for name in ("function linkify", "function mdWalk", "function renderMarkdown", "MD_TAGS"):
        assert name in extracted, name
    assert VENDOR_MD.exists()


def test_vendored_parser_is_used_as_a_parser_only():
    """`md.render`/`renderInline` must never be called: their output is an HTML
    string, and using it would mean needing a sanitizer."""
    # Comments are stripped first -- the block's own prose explains why innerHTML
    # is avoided, and would otherwise trip this.
    code = "\n".join(re.sub(r"//.*", "", line) for line in _extract_source().splitlines())
    for banned in (".render(", ".renderInline(", "innerHTML", "insertAdjacentHTML"):
        assert banned not in code, banned


# ── fidelity ───────────────────────────────────────────────────────

@pytest.mark.parametrize("src,expect_tags,expect_text", [
    ("**bold**", ["p", "strong"], "bold"),
    ("*italic*", ["p", "em"], "italic"),
    ("~~struck~~", ["p", "s"], "struck"),
    ("`code`", ["p", "code"], "code"),
    ("# Heading", ["h1"], "Heading"),
    ("###### Six", ["h6"], "Six"),
    ("> quoted", ["blockquote", "p"], "quoted"),
    ("---", ["hr"], ""),
    ("- a\n- b", ["ul", "li", "p", "li", "p"], "ab"),
    ("1. a", ["ol", "li", "p"], "a"),
    ("```\nx = 1\n```", ["pre", "code"], "x = 1\n"),
    ("    indented", ["pre", "code"], "indented\n"),
])
def test_fidelity(src, expect_tags, expect_text):
    node = _one(src)
    assert _tags(node) == expect_tags
    assert _text(node) == expect_text


def test_plain_text_is_unchanged():
    node = _one("just some prose with no syntax at all")
    assert _tags(node) == ["p"]
    assert _text(node) == "just some prose with no syntax at all"


def test_single_newline_becomes_a_break():
    """`breaks: true` -- a plain description keeps reading the way it did under
    `white-space: pre-wrap`, rather than reflowing into one paragraph."""
    node = _one("line one\nline two")
    assert _tags(node) == ["p", "br"]
    assert _text(node) == "line oneline two"


def test_markdown_link():
    node = _one('see [the docs](https://example.com/d "tip") now')
    (a,) = _anchors(node)
    assert a["href"] == "https://example.com/d"
    assert a["title"] == "tip"
    assert a["target"] == "_blank"
    assert a["rel"] == "noopener noreferrer"
    assert _text(node) == "see the docs now"


def test_link_without_a_title_gets_an_empty_one():
    """An explicit empty title stops the read view's title="Click to edit"
    tooltip being inherited by the anchor (same rule as linkify)."""
    (a,) = _anchors(_one("[x](https://example.com/a)"))
    assert a["title"] == ""


def test_ordered_list_start_is_preserved():
    node = _one("5. five\n6. six")
    ols = [n for n in _walk(node) if n[0] == "ol"]
    assert len(ols) == 1
    assert ols[0][1] == {"start": "5"}


def test_table_alignment_is_rederived_not_copied():
    node = _one("| a | b |\n|:--|--:|\n| 1 | 2 |")
    assert "table" in _tags(node)
    cells = [n for n in _walk(node) if n[0] in ("th", "td")]
    assert [c[2].get("textAlign") for c in cells] == ["left", "right"] * 2
    # The style STRING is never copied through onto the element.
    assert all("style" not in c[1] for c in cells)


def test_fence_info_string_is_dropped():
    """No highlighting, and no user-controlled text landing as a class."""
    node = _one("```javascript\nalert(1)\n```")
    codes = [n for n in _walk(node) if n[0] == "code"]
    assert len(codes) == 1
    assert codes[0][1] == {}
    assert codes[0][2] == {}


# ── linkify interaction ────────────────────────────────────────────

def test_bare_url_inside_markdown_still_links():
    node = _one("- see https://example.com/a for more")
    (a,) = _anchors(node)
    assert a["href"] == "https://example.com/a"
    assert "ul" in _tags(node)


def test_bare_url_keeps_linkifys_punctuation_rules():
    """The whole point of keeping linkify rather than markdown-it's own
    autolinker: trimUrlTail's rules stay in force on every path."""
    (a,) = _anchors(_one("go to https://example.com/a."))
    assert a["href"] == "https://example.com/a"


def test_no_link_inside_code():
    for src in ("`https://example.com/a`", "```\nhttps://example.com/a\n```"):
        node = _one(src)
        assert _anchors(node) == [], src
        assert "https://example.com/a" in _text(node)


def test_anchors_never_nest():
    """A Markdown link whose label is itself a bare URL must produce exactly one
    anchor -- linkify is suppressed inside an open <a>."""
    node = _one("[https://example.com/label](https://example.com/target)")
    anchors = _anchors(node)
    assert len(anchors) == 1
    assert anchors[0]["href"] == "https://example.com/target"
    assert _text(node) == "https://example.com/label"


def test_linked_image_does_not_nest_anchors():
    """`[![alt](img)](target)` is already inside an anchor, so the image's own
    link is suppressed -- createElement builds the tree directly and there is no
    HTML parser to un-nest a nested <a> afterwards."""
    node = _one("[![alt](https://cdn.example.com/p.png)](https://target.example.com/)")
    anchors = _anchors(node)
    assert len(anchors) == 1
    assert anchors[0]["href"] == "https://target.example.com/"
    assert _text(node) == "alt"


def test_refused_link_label_is_ordinary_prose():
    """A link whose href fails the gate keeps its label as text -- and that text
    is treated like any other prose, so a URL inside it still linkifies (to
    itself, never to the refused href)."""
    node = _one("[https://label.example.com/x](/relative/path)")
    anchors = _anchors(node)
    assert [a["href"] for a in anchors] == ["https://label.example.com/x"]
    assert _text(node) == "https://label.example.com/x"


def test_www_and_mailto_are_not_linked():
    """One URL semantics app-wide: http(s) only, as linkify has always done."""
    assert _anchors(_one("www.example.com and mailto:me@example.com")) == []


# ── XSS matrix ─────────────────────────────────────────────────────

# Everything a hostile card description might carry. Used case by case below and
# as a corpus for the property tests.
HOSTILE = [
    "<img src=x onerror=alert(1)>",
    "<script>alert(1)</script>",
    "<div onclick=\"alert(1)\">click</div>",
    "<a href='javascript:alert(1)'>x</a>",
    "<iframe src='https://evil.example.com'></iframe>",
    "<svg/onload=alert(1)>",
    "[x](javascript:alert(1))",
    "[x](JaVaScRiPt:alert(1))",
    "[x](  javascript:alert(1))",
    "[x](java\tscript:alert(1))",
    "[x](%6a%61vascript:alert(1))",
    "[x](&#106;avascript:alert(1))",
    "[x](vbscript:msgbox(1))",
    "[x](data:text/html,<script>alert(1)</script>)",
    "[x](file:///etc/passwd)",
    "[x](/relative/path)",
    "[x](#anchor)",
    "[x](//evil.example.com)",
    "![alt](javascript:alert(1))",
    "![alt](https://evil.example.com/pixel.gif)",
    "<https://ok.example.com/a>",
    "> <script>alert(1)</script>",
    "`<script>alert(1)</script>`",
    "```\n<script>alert(1)</script>\n```",
    "| <script>alert(1)</script> |\n|---|\n| a |",
    "**<script>alert(1)</script>**",
]


@pytest.mark.parametrize("src", [
    "<img src=x onerror=alert(1)>",
    "<script>alert(1)</script>",
    "<div onclick=\"alert(1)\">click</div>",
    "<iframe src='https://evil.example.com'></iframe>",
    "<svg/onload=alert(1)>",
])
def test_raw_html_stays_literal_text(src):
    """`html: false` means raw HTML is never even tokenised -- it arrives as a
    text token and lands in a text node."""
    node = _one(src)
    # Only the wrapping paragraph, plus (for the iframe case) the anchor linkify
    # makes of the bare URL sitting inside that literal text -- never the tag the
    # source spells out.
    assert set(_tags(node)) <= {"p", "a"}, _tags(node)
    assert _text(node) == src


@pytest.mark.parametrize("src,label", [
    ("[x](javascript:alert(1))", "x"),
    ("[x](JaVaScRiPt:alert(1))", "x"),
    ("[x](  javascript:alert(1))", "x"),
    ("[x](java\tscript:alert(1))", "x"),
    ("[x](%6a%61vascript:alert(1))", "x"),
    ("[x](&#106;avascript:alert(1))", "x"),
    ("[x](vbscript:msgbox(1))", "x"),
    ("[x](data:text/html,<b>hi</b>)", "x"),
    ("[x](file:///etc/passwd)", "x"),
    ("[x](/relative/path)", "x"),
    ("[x](#anchor)", "x"),
    ("[x](//evil.example.com)", "x"),
])
def test_dangerous_or_relative_hrefs_produce_no_anchor(src, label):
    """The href gate runs AFTER markdown-it's own normalisation, so it sees the
    final string. A rejected link keeps its label as plain text."""
    node = _one(src)
    assert _anchors(node) == [], src
    assert label in _text(node)


def test_autolink_of_a_dangerous_scheme_produces_no_anchor():
    assert _anchors(_one("<javascript:alert(1)>")) == []


def test_autolink_of_an_http_url_works():
    (a,) = _anchors(_one("<https://ok.example.com/a>"))
    assert a["href"] == "https://ok.example.com/a"


def test_images_are_never_img_elements():
    """Deliberate: a description must not make every viewer's browser fetch a
    remote URL. The alt text is shown, linked to the source."""
    node = _one("![a picture](https://cdn.example.com/p.png)")
    assert "img" not in _tags(node)
    (a,) = _anchors(node)
    assert a["href"] == "https://cdn.example.com/p.png"
    assert _text(node) == "a picture"


def test_image_with_a_dangerous_src_produces_no_link():
    node = _one("![alt](javascript:alert(1))")
    assert _anchors(node) == []
    assert "img" not in _tags(node)
    assert "alt" in _text(node)


# ── properties over the whole hostile corpus ───────────────────────

# Mirrors MD_TAGS in app.js. Asserted against the source below so the two cannot
# drift apart silently.
WHITELIST = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "s", "code", "pre", "blockquote",
    "ul", "ol", "li", "a", "table", "thead", "tbody", "tr", "th", "td",
}


def test_whitelist_matches_the_source():
    block = _extract_source()
    start = block.index("const MD_TAGS")
    body = block[block.index("[", start) + 1:block.index("]", start)]
    assert set(re.findall(r"'([^']+)'", body)) == WHITELIST


CORPUS = HOSTILE + [
    "# H\n\n**b** *i* `c` [t](https://e.example.com) ![a](https://e.example.com/i.png)",
    "- a\n- b\n\n1. x\n\n> q\n\n```js\ncode\n```\n\n| a |\n|---|\n| 1 |",
    "https://bare.example.com/a. and text",
    "",
]


def test_only_whitelisted_tags_are_ever_produced():
    for src, node in zip(CORPUS, _run(CORPUS)):
        extra = set(_tags(node)) - WHITELIST
        assert not extra, f"{src!r} produced {extra}"


def test_no_event_handler_attributes():
    for src, node in zip(CORPUS, _run(CORPUS)):
        for n in _walk(node):
            if n[0] == "#text":
                continue
            bad = [k for k in n[1] if k.lower().startswith("on")]
            assert not bad, f"{src!r} set {bad} on <{n[0]}>"


def test_every_href_is_an_absolute_http_url():
    for src, node in zip(CORPUS, _run(CORPUS)):
        for a in _anchors(node):
            assert a["href"].startswith(("http://", "https://")), f"{src!r}: {a['href']}"
            assert a["target"] == "_blank"
            assert a["rel"] == "noopener noreferrer"


def test_no_user_text_disappears():
    """A tag off the whitelist renders its children into the parent rather than
    being dropped, so content is never silently lost."""
    for src in ("<script>alert(1)</script>", "<div onclick=x>visible</div>", "plain words"):
        assert _text(_one(src)), src


def test_empty_input_is_an_empty_fragment():
    node = _one("")
    assert node[0] == "#fragment"
    assert node[3] == []


# ── degraded path (vendored parser missing) ────────────────────────

def test_fallback_to_linkify_when_the_parser_is_absent():
    """A failed `<script>` load must not blank the panel: the text still shows,
    bare URLs still link, and Markdown syntax simply stays literal."""
    src = "**bold** stays literal, see https://example.com/a."
    (node,) = _run([src], with_parser=False)
    assert _text(node) == src
    assert [a["href"] for a in _anchors(node)] == ["https://example.com/a"]


def test_fallback_preserves_whitespace():
    """Both hosts dropped `white-space: pre-wrap` for the Markdown path, so the
    fallback carries a class that puts it back -- without it a whole
    description reflows onto one line."""
    (node,) = _run(["line one\n\nline two"], with_parser=False)
    assert _tags(node) == ["span"]
    assert node[3][0][1] == {"class": "md-fallback"}
    assert "\n\n" in _text(node)


def test_fallback_marker_class_exists_in_the_stylesheet():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ".md-fallback" in css
    assert re.search(r"\.md-fallback\s*\{[^}]*white-space:\s*pre-wrap", css)


# ── vendored artifact provenance ───────────────────────────────────

# sha256 of the upstream `package/dist/markdown-it.min.js` from
# https://registry.npmjs.org/markdown-it/-/markdown-it-14.3.0.tgz, with the
# trailing sourceMappingURL comment removed (see static/vendor/README.md).
# Compared after normalising line endings, since git may rewrite them.
VENDOR_MD_SHA256 = "9fc19d0c0ea39204f6e1d8b1f2bb3b431c21245c38570ec1b204dd79df08e2cd"


def test_vendored_parser_is_the_pinned_artifact():
    """A 125 KB opaque bundle is exactly the thing that should not change
    without someone noticing. If this fails, the file was swapped, re-minified
    or upgraded -- update the hash here AND the provenance in
    static/vendor/README.md in the same change."""
    raw = VENDOR_MD.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(raw).hexdigest() == VENDOR_MD_SHA256
    readme = (STATIC / "vendor" / "README.md").read_text(encoding="utf-8")
    assert "markdown-it 14.3.0" in readme
