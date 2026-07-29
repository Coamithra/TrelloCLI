"""Magnet links — one self-contained token that addresses a card or a board.

Handing a card to another agent otherwise means handing over three separate
things (backend, board, card id); drop or garble any one and the receiver gets
"Board not found" / "Card not found" with nothing in the id to recover from.
Local cards make it worse: `shortUrl` is a Trello-only field, so `card show`
prints an empty `URL:` line and there is literally nothing to paste. A magnet
carries all of it, resolvable by any agent on any machine with no shared state
and no central index.

Grammar (path segments, no query string)::

    trello://card/<backend>/<boardId>/<cardId>[#<slug>]
    trello://board/<backend>/<boardId>[#<slug>]
    trello://card/http/<urlencoded-server>/<boardId>/<cardId>[#<slug>]
    trello://board/http/<urlencoded-server>/<boardId>[#<slug>]

Why path segments and not `?board=…&backend=…`: a magnet's whole job is to
survive a copy-paste into a shell command, and `&` backgrounds an unquoted
command in bash while `?` is a glob character. A token silently truncated at
`&` is exactly the plausible-looking-wrong-result this repo refuses everywhere
else. Path segments carry no shell-special characters at all. The one field
that cannot be a clean segment is the http server URL (it contains `://`), so
it is percent-encoded — `%` is not shell-special either.

The trailing `#<slug>` is emitted from the entity's name and **ignored on
parse**: 48 characters of hex say nothing about *which* card this is, and a
magnet's second job (after resolving) is being readable in a PR body, a prompt
or a chat message. Because it is never consulted, a renamed card just carries a
stale slug and still resolves. `#` opens a comment in bash/zsh only at the
start of a word, so an unquoted `trello://...#slug` stays one argument.

Ids are emitted in full (24 hex) by default and parsed leniently (4-24 hex), so
a hand-written or `--short` token works too; parsed ids are handed to the
existing `_resolve_*` resolvers, so prefix behaviour is whatever those already
do rather than a second, divergent notion of "id prefix".

This module imports nothing from `main`/`api`/`config`, so it stays unit-
testable in isolation.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote, unquote

SCHEME = "trello://"
ENTITY_TYPES = ("card", "board")
BACKENDS = ("trello", "local", "http")

# Long enough to say something, short enough not to dominate the token.
SLUG_MAX = 40

# Lenient on length (a hand-written or --short token is 8) but strict on the
# alphabet, so a garbled paste is an error rather than a doomed lookup.
_ID_RE = re.compile(r"^[0-9a-f]{4,24}$")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")

GRAMMAR = """\
  trello://card/<backend>/<boardId>/<cardId>[#<slug>]
  trello://board/<backend>/<boardId>[#<slug>]
  trello://card/http/<urlencoded-server>/<boardId>/<cardId>[#<slug>]
  trello://board/http/<urlencoded-server>/<boardId>[#<slug>]
<backend> is one of: trello, local, http.
Get one with: trello card link <card_id>"""


def is_magnet(token: object) -> bool:
    """True for anything that *claims* to be a magnet.

    Deliberately just the scheme check: any `trello://` token is a magnet
    attempt, so a malformed one reaches `parse` and fails loudly instead of
    being skipped and re-read as a card-name prefix."""
    return isinstance(token, str) and token.startswith(SCHEME)


def slugify(name: str | None) -> str:
    """A short, shell-safe, lowercase-alphanumeric slug for a card/board name.

    Accented and non-Latin characters are folded away rather than escaped — the
    slug is decoration, and a percent-encoded one would cost more than it
    tells. A name that folds to nothing yields "", and the caller then omits
    the `#` entirely."""
    if not name:
        return ""
    folded = unicodedata.normalize("NFKD", name)
    folded = folded.encode("ascii", "ignore").decode("ascii")
    slug = _NON_SLUG_RE.sub("-", folded.lower()).strip("-")
    return slug[:SLUG_MAX].rstrip("-")


def _fail(token: str, problem: str) -> None:
    raise SystemExit(
        f"Not a valid magnet link: {problem}\n"
        f"  got: {token}\n"
        f"Expected one of:\n{GRAMMAR}"
    )


def _prefix_segments(backend: str, server: str | None) -> list[str]:
    """The `<backend>[/<urlencoded-server>]` segments shared by both builders."""
    backend = (backend or "trello").lower()
    if backend not in BACKENDS:
        raise SystemExit(
            f"Cannot build a magnet for backend {backend!r} "
            f"(known: {', '.join(BACKENDS)})."
        )
    if backend != "http":
        return [backend]
    if not server:
        raise SystemExit(
            "Cannot build an http magnet without a server URL. "
            "Set one with `trello configure-http <url>` or pass --server <url>."
        )
    # The URL only — never the server *token*. These strings get pasted into
    # agent prompts and PR bodies, and a credential does not belong in one.
    return [backend, quote(server, safe="")]


def _build(kind: str, ids: list[str], backend: str, *,
           name: str | None, server: str | None, short: bool) -> str:
    segments = [kind, *_prefix_segments(backend, server)]
    segments += [i[:8] if short else i for i in ids]
    token = SCHEME + "/".join(segments)
    slug = slugify(name)
    return f"{token}#{slug}" if slug else token


def build_card(card_id: str, board_id: str, backend: str, *,
               name: str | None = None, server: str | None = None,
               short: bool = False) -> str:
    """A magnet addressing one card."""
    return _build("card", [board_id, card_id], backend,
                  name=name, server=server, short=short)


def build_board(board_id: str, backend: str, *,
                name: str | None = None, server: str | None = None,
                short: bool = False) -> str:
    """A magnet addressing one board."""
    return _build("board", [board_id], backend,
                  name=name, server=server, short=short)


def parse(token: str) -> dict:
    """Parse a magnet into `{type, id, board, backend, server, slug}`.

    `id` is the thing the token addresses — the card id for a card magnet, the
    board id for a board magnet — and `board` is always the board it lives on.

    Anything unparseable raises `SystemExit` naming the problem and showing the
    grammar. This is the deliberate opposite of `search`'s "unknown operator
    degrades to literal text": a magnet is machine-generated, so a token that
    does not parse means a version mismatch or a hand-edit, and both are worth
    surfacing rather than turning into a silent "card not found"."""
    if not is_magnet(token):
        _fail(str(token), f"it does not start with {SCHEME!r}")

    # Split the slug off FIRST, so a `#` anywhere else stays part of a segment
    # and trips the id/count checks below instead of being read as a slug.
    body, _, slug = token[len(SCHEME):].partition("#")
    if not body:
        _fail(token, "there is nothing after the scheme")
    segments = body.split("/")
    if any(not s for s in segments):
        _fail(token, "it has an empty path segment (a doubled or trailing '/')")

    kind = segments[0].lower()
    if kind not in ENTITY_TYPES:
        _fail(token, f"unknown entity type {segments[0]!r} "
                     f"(known: {', '.join(ENTITY_TYPES)})")
    if len(segments) < 2:
        _fail(token, "it is missing the <backend> segment")
    backend = segments[1].lower()
    if backend not in BACKENDS:
        _fail(token, f"unknown backend {segments[1]!r} "
                     f"(known: {', '.join(BACKENDS)})")

    rest = segments[2:]
    server = None
    if backend == "http":
        if not rest:
            _fail(token, "an http magnet needs a percent-encoded server URL segment")
        server = unquote(rest.pop(0))
        if not server.startswith(("http://", "https://")):
            _fail(token, f"the server segment decodes to {server!r}, "
                         f"which is not an http(s) URL")

    wanted = 2 if kind == "card" else 1
    if len(rest) < wanted:
        missing = "<boardId>/<cardId>" if kind == "card" else "<boardId>"
        _fail(token, f"a {kind} magnet needs {missing}")
    if len(rest) > wanted:
        _fail(token, f"trailing segments after the id: {'/'.join(rest[wanted:])!r}")

    ids = [s.lower() for s in rest]
    for raw, ident in zip(rest, ids):
        if not _ID_RE.match(ident):
            _fail(token, f"{raw!r} is not an id (4-24 hex characters)")

    return {
        "type": kind,
        "id": ids[-1],
        "board": ids[0],
        "backend": backend,
        "server": server,
        "slug": slug,
    }
