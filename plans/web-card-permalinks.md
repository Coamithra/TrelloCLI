# Web UI: card permalinks (deep-link + copy control)

Card `c230af2b` — board `6a353ffc`, branch `feat/web-card-permalinks`.

## Context

The CLI has magnet links (`trello_cli/magnet.py`): `trello card link`, the `Link:` line in
`card show`, and a magnet accepted anywhere a card ref goes. The web app has none of it —
`grep -rn 'trello://' trello_cli/web/` is empty — and worse, it has no card deep-link *at
all*: the URL only ever carries `?board=` and `?token=`, so opening a card leaves the URL
untouched and there is nothing to share, bookmark, or reload onto.

## Design

### Server — `trello_cli/web/server.py`

`GET /api/cards/{card_id}` gains a transient **`_magnet`** key (string, or `null`)
alongside the `comments` it already adds. Same precedent as the multipart upload route's
`_attachment`: a browser-only REST route, so no backend/dict contract moves (the http
backend gets `get_card` over `/api/rpc`, not this route).

New module-level helper:

```python
def _card_magnet(card: dict) -> str | None
```

— `magnet.build_card(card["id"], card["idBoard"], config.get_backend_name(),
name=card["name"], server=config.get_server_url() if backend == "http" else None)`.
Returns `None` (never raises) when the card carries no 24-hex `idBoard`, when the backend
is `http` with no configured server URL, or on any `SystemExit` from the builder — a card
whose magnet can't be built still opens, it just has no magnet row.

**Why server-side and not in JS:** the grammar has exactly one authority (`magnet.py`).
Building it client-side would mean a second implementation of `slugify` (NFKD folding) and
the percent-encoding rules, in a language that has to stay byte-compatible with the Python
one forever. The server also already knows the backend name and server URL; the browser
doesn't.

**The token is never in it** — `magnet.py` refuses to put the server token in a magnet, and
the page-link builder below strips `?token=` for the same reason.

### Client — `trello_cli/web/static/app.js`

New marked slice `// >>> card-url >>>` (sliced by a new test), holding:

- `setCardInUrl(cardId | null)` — `replaceState` the `?card=` param (deleted when null),
  preserving everything else (notably `?token=`). No history entry, matching
  `setBoardInUrl`.

The `?card=` param is for **this browser** — F5, a bookmark, a live reload — not for
sharing. The thing you hand to another agent is the magnet, and only the magnet is
offered for copying (see below).

Wiring:

| where | what |
| --- | --- |
| `openDetail(cardId, {fromUrl})` | on success `setCardInUrl(card.id)` |
| `openDetail` failure with `fromUrl` | silent: `closeDetail()`, no error status — a stale/foreign `?card=` degrades to just the board |
| `openDetail` when `card.idBoard !== currentBoardId` and `fromUrl` | same silent drop |
| `closeDetail()` | `setCardInUrl(null)` |
| `openManageBoards()` | `setCardInUrl(null)` — it takes over the same drawer |
| `selectBoard()` | `if (openCard) closeDetail()` — the drawer would otherwise show a card from the board you just left, and the URL would claim board B + card from board A. Gated on `openCard` so the manage panel (which sets `openCard = null`) survives `reloadBoardsNav`'s board switch |
| `init()` | `await loadBoard(...)`, then restore `?card=` via `openDetail(id, {fromUrl: true})` |

**Board scope of a restore:** board comes from `?board=` as today; the card opens only if
it is on that board. A `?card=` with no/invalid `?board=` is dropped rather than used to
*infer* the board — every link this UI emits carries both, so inferring would only serve
hand-edited URLs, at the cost of an extra fetch before the first render.

New **`🔗 Link`** button in the detail toolbar (next to Labels / Due date / Attach), opening
a popover (`openPopoverAt`, as labels/due do) holding the card's magnet in a readonly
`<input>` (pre-selected) + a Copy button. When `_magnet` is `null` the popover says why
instead. **No page-URL row**: the browser URL is only reachable by someone who can already
hit this server *and* has a token, so it is not the thing you hand to an agent — the magnet
is, and it resolves from a bare `trello` command with no flags. (A `local` magnet still
needs the recipient to reach the same store; an `http` one carries the server URL.)

Copy uses `navigator.clipboard.writeText`, falling back to selecting the input and telling
the user to press Ctrl+C (the API needs a secure context: fine on `localhost` and on the
https deploy, absent on a plain-http LAN bind).

## Tests / Verification

- **New** `tests/test_card_url.py` — slices `card-url` out of the real `app.js` and runs it
  under `node` (same vehicle as `test_render_state.py`, auto-skips without node): the param
  is set, unrelated params (`board`, `token`) are preserved, `setCardInUrl(null)` removes
  only `card`, no history entry is pushed, and the marker-presence guard.
- **`tests/test_web_api.py`** — `GET /api/cards/{id}` carries a parseable `_magnet` naming
  the right board + card; `magnet.parse` round-trips it.
- `python -m pytest` green.
- Functional: `trello serve` on the local backend — open a card, copy the magnet, reload the
  page (card reopens), close it (param goes), paste the magnet into
  `trello card show <magnet>` and get the same card, hand-edit `?card=` to junk (board
  loads, no error), switch boards with a card open (drawer closes, URL coherent).

## Out of scope

- **Copying the page URL.** Scoped out by the user: the magnet is what gets handed to
  agents, and a `?board=…&card=…&token=…` URL is only useful to someone already on this
  server.
- **Accepting a pasted magnet in the UI** to navigate to a card — needs a UI entry point and
  a backend-match rule the browser can't act on (it can't switch backends the way the CLI
  can). Follow-up card.
- The `--as cmd` form in the popover; board magnets / a board-level copy control.
- Any change to `magnet.py`'s grammar, or to the CLI.
