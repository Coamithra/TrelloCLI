# Web: render Markdown in card descriptions and comments (card 016dabdc)

## Context

The web detail panel renders card text with `linkify()` — bare `http(s)://` URLs become
`<a>`, everything else is a text node. Markdown syntax (`**bold**`, `- lists`, fences,
`[text](url)`) therefore shows as literal characters. Several cards on this board are
written in Markdown and read badly.

Presentation-only: descriptions and comments are stored and edited as raw Markdown text
either way, so no backend, dict-contract or API change. The editors stay raw textareas.

## Verified against the code (card claims re-checked)

- `linkify()` shipped (app.js ~L680-751); tested by `tests/test_linkify.py` under `node` +
  a ~20-line DOM shim. TRUE.
- Render sites: description read view (`openDetail`, `<pre class="detail-desc">` +
  `linkify`), comment body (`commentEl`), checklist item names. Card titles use
  `textContent` in both the board face and the detail `<h2>`. TRUE.
- `inlineEditable`'s read view already ignores clicks landing inside an `<a>`. TRUE.
- Vendoring precedent: `static/vendor/Sortable.min.js` (45 KB, UMD, banner comment),
  loaded by a `<script>` tag in `index.html`; `pyproject.toml` already ships
  `static/vendor/*` as package data. No build step.

## Design

### Parser + sanitizer choice

**markdown-it 14.3.0 (MIT, published 2026-07-02) vendored as `dist/markdown-it.min.js`
(~108 KB UMD, self-contained), used as a PARSER ONLY — plus a ~90-line whitelist
token→DOM walker we own. No sanitizer, no `innerHTML`, ever.**

Why not the obvious `markdown-it + DOMPurify + innerHTML` pairing:

1. **It cannot be tested here.** This repo's only JS test vehicle is `node` + a hand-written
   DOM shim (`tests/test_linkify.py`). DOMPurify needs a real DOM
   (`document.implementation.createHTMLDocument`); node has none, and pulling in `jsdom`
   means an npm install, which global policy keeps at arm's length. The XSS matrix would be
   unverifiable — exactly the property most worth locking down.
2. **It gives up the property the repo prizes.** `linkify()`'s safety is
   escape-by-construction: user text never enters an HTML parser. `md.parse()` returns a
   flat token stream (`tag`, `attrs`, `nesting`, inline `children`) — walking it with
   `document.createElement` keeps that property exactly, so markup in card text is a text
   node by construction rather than by a sanitizer getting its filter right.
3. It saves the second vendored file (DOMPurify 3.4.12, ~22 KB, MPL-2.0/Apache-2.0).

Options weighed and rejected:

| Option | Size | Verdict |
|---|---|---|
| markdown-it 14.3.0 + own DOM walker | ~108 KB | **chosen** — CommonMark-correct, walker testable under node |
| marked 18.0.7 + own DOM walker | ~40 KB | viable and 1/3 the size, but its token tree is nested and per-type, so the walker is bespoke per node type rather than one whitelist loop; less CommonMark-strict |
| markdown-it/marked + DOMPurify + innerHTML | +22 KB | rejected — untestable here (1), gives up escape-by-construction (2) |
| snarkdown 2.0.0 | ~1 KB | rejected — emits raw HTML strings with no escaping; *requires* a sanitizer, so it inherits (1) and (2) and adds poor fidelity |
| hand-rolled CommonMark subset | 0 KB | rejected — we'd own list/fence/emphasis edge cases forever; parser correctness is the one thing worth not owning |

markdown-it config: `{ html: false, linkify: false, breaks: true, typographer: false }`.
`html:false` means raw HTML in card text never becomes a token at all. `breaks:true` maps a
single newline to `<br>`, so plain non-Markdown descriptions keep looking exactly as they do
today under `white-space: pre-wrap`. `linkify:false` — see below.

### linkify: retained and LAYERED, not replaced (deviation from the card)

The card guessed linkify would be *replaced* by the parser's own autolinker. Recommending the
opposite: keep markdown-it's `linkify` **off** and run our `linkify()` on the walker's text
tokens.

- One URL-matching semantics in the app instead of two (markdown-it's bundled `linkify-it`
  also matches bare `www.`, emails and other schemes; ours is deliberately http/https-only).
- The documented `trimUrlTail` punctuation/bracket rules and their test file stay live
  instead of becoming dead code in the description path while still governing checklists.
- Checklist items keep plain `linkify()` (card's own suggestion) and now agree with
  descriptions on what a bare URL is.

Two suppressions in the walker: no linkify inside `code`/`pre` content, and none while inside
an open `<a>` (a Markdown link's own label), so anchors never nest.

### Render-path split

| Path | Before | After |
|---|---|---|
| Card description read view | `linkify` | `renderMarkdown` |
| Comment body | `linkify` | `renderMarkdown` |
| Checklist item names | `linkify` | unchanged (`linkify`) |
| Card title — board face | `textContent` | unchanged |
| Card title — detail `<h2>` | `textContent` | unchanged (explicitly out of scope) |
| Board/list names, manage panel | `textContent` | unchanged |

### Element + attribute policy (the walker's whitelist)

Allowed tags: `p br hr h1 h2 h3 h4 h5 h6 strong em s code pre blockquote ul ol li a
table thead tbody tr th td`. Anything else (including a token markdown-it would emit that
isn't on the list) renders its children as text rather than being dropped, so no content
disappears.

Attributes: **only** `href` and `title` on `<a>`, `start` on `<ol>`, and `align` (mapped to
`style.textAlign`, from table alignment) — nothing else is ever copied from a token, so no
`on*` handler can exist by construction. Fence info strings are dropped (no highlighting).

`<a>`: href must match `^https?://` after markdown-it's own normalization, else the anchor is
not created and the label renders as plain text. Always `target="_blank"`,
`rel="noopener noreferrer"`, and `title=''` unless the Markdown supplied one (the same
tooltip-inheritance fix `linkify` documents).

`![alt](url)` images are **not rendered** — the alt text plus, where the src is http(s), a
link to it. Keeps the whitelist minimal and stops a card description silently fetching
remote content (tracking pixel / referrer leak) from a drawer. Flagging as a product call.

### inlineEditable coexistence

Unchanged mechanism: the read view's click handler already returns early on
`e.target.closest('a')`, which covers both linkify anchors and Markdown links at any nesting
depth. The description read view changes from `<pre class="detail-desc">` to
`<div class="detail-desc markdown">` (a `<pre>`'s `white-space: pre` would wreck block
layout); the placeholder branch and the `.editable` class/`title` are untouched.

### File-by-file

1. `trello_cli/web/static/vendor/markdown-it.min.js` — **new**, extracted from the pinned
   npm tarball `https://registry.npmjs.org/markdown-it/-/markdown-it-14.3.0.tgz`
   (`package/dist/markdown-it.min.js`), never `npm install`. Banner comment prepended with
   name, version, source URL and MIT.
2. `trello_cli/web/static/vendor/README.md` — **new**: name / version / source URL /
   license / sha256 for markdown-it and (retroactively) Sortable, with the full MIT text.
3. `trello_cli/web/static/index.html` — one `<script src="/static/vendor/markdown-it.min.js">`
   before `app.js`.
4. `trello_cli/web/static/app.js` — new `renderMarkdown(text) → DocumentFragment` block
   directly after `linkify()` (same fragment-returning contract, so both call sites are a
   one-word change): the markdown-it instance, the tag/attr whitelist, the token walker,
   and the linkify-in-text-tokens rule. Call-site changes in `commentEl` and `openDetail`'s
   description `render`. Wrapped in markers so the test can slice it.
5. `trello_cli/web/static/style.css` — `.markdown` block styles (headings, lists, blockquote,
   `pre`/`code`, `hr`, table), `.detail-desc`/`.comment-body` lose `white-space: pre-wrap`
   in the rendered path, existing anchor rules extended to `.markdown a`.
6. `tests/test_markdown.py` — **new**, same pattern as `test_linkify.py`: node + an extended
   DOM shim, loading the real vendored `markdown-it.min.js` and the real sliced block from
   `app.js`, serializing the produced node tree to JSON.
7. `CLAUDE.md` — rewrite the `static/` bullet's "Markdown rendering is explicitly out of
   scope" sentence and record the parser-only/no-sanitizer decision and the whitelist rule
   ("adding a tag? add it to the whitelist").

## Verification

- `python -m pytest` (worktree venv) green, incl. the existing `test_linkify.py` untouched.
- `tests/test_markdown.py`, run under `node` (auto-skip without it):
  - **Fidelity:** bold/italic/inline code/fences/`- ` and `1. ` lists/headings/blockquote/
    `hr`/`[text](url)`/tables render the expected tags; plain prose with no syntax renders
    the same visible text as today, with single newlines as `<br>`.
  - **XSS matrix:** `<img src=x onerror=alert(1)>`, `<script>alert(1)</script>`,
    `<div onclick=…>` → literal text, zero elements; `[x](javascript:alert(1))`,
    `JaVaScRiPt:`, `java\tscript:`, `%6a%61vascript:`, `&#106;avascript:`,
    `data:text/html,…`, `vbscript:`, `file:`, relative `/foo`, `#anchor` → **no anchor**,
    label text preserved; autolink `<javascript:alert(1)>` → no anchor;
    `![x](javascript:…)` → no `img` and no anchor.
  - **Property tests over the whole corpus:** every element tag produced is in the
    whitelist; every `href` starts `http://`/`https://`; every anchor carries
    `target=_blank` + `rel=noopener noreferrer`; no attribute whose name starts with `on`
    is ever set.
  - **linkify interaction:** a bare URL inside a list item / blockquote links; a bare URL
    inside an inline code span and inside a fence stays text; a Markdown link's label
    containing a bare URL produces exactly one, non-nested anchor.
  - Marker assertions so a rename fails loudly instead of testing nothing (the
    `test_markers_still_present` pattern).
- Functional: serve from the worktree venv on loopback
  (`.venv/Scripts/python.exe -m trello_cli --backend local serve`) against a **scratch**
  board seeded with a Markdown-heavy card + comment, checked with `curl` for the static
  assets and, if a visual pass is needed, claude-in-chrome (real Chrome; never the in-app
  pane) — tabs closed after. Scratch board deleted with `local rm … --yes`.
- Before shipping: `git pull origin master` (branch `fix/column-sort-refresh` has unmerged
  `app.js` work in the sort-menu / composer / SSE regions), resolve per the runbook, re-run
  the gate.

## Out of scope

- Markdown in card titles (card says so), in list/board names, and in checklist items.
- WYSIWYG editing — both editors stay raw-text textareas.
- Syntax highlighting in fences, task-list checkboxes, footnotes, emoji shortcodes, or any
  markdown-it plugin.
- Inline images.
- Any backend, API, dict-contract or CLI change; the CLI keeps printing raw text.
