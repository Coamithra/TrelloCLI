# Clickable URLs in the web card detail panel

Card: `4a7bb180` — "Web: make URLs clickable in the card detail panel"
Branch: `feat/clickable-urls`

## Context

In the web kanban (`trello serve`), a card's description and comments render as
inert plain text. If you paste `https://github.com/foo/bar` into a description,
you can look at it but not click it — you have to select it and copy it by hand.
Descriptions on this project's own board are full of links (spec URLs, PR links,
the Trello board link in `CLAUDE.md`), so this is a daily papercut.

Both render sites deliberately use `textContent`, which is what makes them
XSS-safe today:

- `app.js:1265` — the `<pre class="detail-desc">` read view of the description
- `app.js:1166` — `.comment-body`
- `app.js:1292` — checklist item names (same shape; read-only)

The fix has to keep that safety property. Switching to `innerHTML` with an
escaping pass would work but puts a hand-rolled escaper on the critical path for
every piece of user text in the panel; one missed case is a stored XSS on a
board that syncs across machines via Dropbox.

## Design

### `linkify(text) -> DocumentFragment` (new helper in `app.js`)

Walk the string with a global regex, appending alternating **text nodes** and
`<a>` **elements** to a fragment. Non-URL text never goes through an HTML
parser, so it stays escaped by construction — the same safety property
`textContent` gives, without the hand-rolled escaper.

```
const URL_RE = /\bhttps?:\/\/[^\s<>"']+/gi;
```

Only `http://` and `https://`. No `javascript:`/`data:` (they can't match), no
bare `www.`, no `mailto:` — an href built from this regex is always an absolute
http(s) URL, so there is no scheme-smuggling surface.

Anchors get `target="_blank"` + `rel="noopener noreferrer"` (a card link should
not replace the board you're looking at, and `noopener` keeps the opened page
off `window.opener`).

**Trailing-punctuation trim.** `[^\s]+` greedily eats sentence punctuation, so
`see https://x.com/a).` would link `https://x.com/a).`. After matching, walk
back over trailing `.,;:!?'"` unconditionally, and over `)`/`]`/`}` only when
the match holds more closers than openers — so the Wikipedia-style
`https://en.wikipedia.org/wiki/Foo_(bar)` keeps its paren while
`(see https://x.com/a)` does not. The trimmed tail is emitted as ordinary text.

### Call sites

- `commentEl()` — `body.textContent = …` → `body.appendChild(linkify(…))`
- description `render()` — same, but only for the real description; the
  "Add a more detailed description…" placeholder stays `textContent`
- checklist item names — same one-line swap. Not named on the card, but it's the
  same user-authored text in the same panel; leaving it inert would be an
  arbitrary hole.

Card **titles** are deliberately excluded: the board face is a drag handle, and a
link inside it would fight SortableJS for the mousedown.

### `inlineEditable` click guard

The description read view is click-to-edit — `view.addEventListener('click',
showEditor)`. Without a guard, clicking a link drops the box into edit mode and
the navigation is lost. Add an early return in the handler when the click
originated inside an `<a>`:

```
view.addEventListener('click', (e) => { if (e.target.closest('a')) return; showEditor(); });
```

Shared with the single-line title editor, which contains no anchors, so it is a
no-op there.

### Styling (`style.css`)

One rule for links inside the two/three text surfaces, using the existing
`--accent` var (the app is single-theme dark; no light-mode variant to carry):

```
.detail-desc a, .comment-body a, .checklist a { color: var(--accent); text-decoration: underline; overflow-wrap: anywhere; }
```

`.detail-desc` / `.comment-body` already set `word-break: break-word`, so a long
URL will not blow out the drawer width.

## Verification

No JS test harness exists in this repo (`tests/` is Python-only, and
`test_web_api.py` only asserts the static shell is reachable), so this is a
functional browser check plus the unit suite for the no-regression side.

1. `python -m pytest` stays green (nothing server-side changes, but the gate is the gate).
2. Serve an **isolated** store — `--local-root <scratchpad>/store` — so no
   scratch data touches the real Dropbox store at all. Seed one card with a
   description and comments covering:
   - a bare URL on its own line
   - a URL mid-sentence followed by `.` / `,` / `)`
   - a Wikipedia-style URL with a legitimate `(…)` in the path
   - two URLs on one line
   - a non-URL that must stay text (`javascript:alert(1)`, `not-a-url`, `www.foo.com`)
   - an HTML-injection probe (`<img src=x onerror=alert(1)>`) that must render
     as literal text, proving the escape-by-construction property
3. In the browser: links render blue/underlined, open in a new tab, clicking a
   link does **not** open the description editor, clicking the surrounding text
   still does, the injection probe shows as text with no console error.
4. Check the comment surface and a checklist item the same way.

Browser work goes through `claude-in-chrome` — per `~/.claude/CLAUDE.md` the
in-app Browser pane crashes Claude Desktop on this machine.

## Out of scope

- Full Markdown rendering (Trello's real behaviour). That's a much bigger
  surface (bold/lists/code fences + a sanitizer) and a separate card if wanted.
- Bare `www.`, `mailto:`, and other schemes.
- Linkifying card titles / board faces.
- The CLI's own output — terminals linkify on their own.
