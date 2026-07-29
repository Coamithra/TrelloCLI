"""Area — magnet links: build/parse round-trips, malformed tokens, conflicts.

`trello_cli.magnet` is deliberately dependency-free, so most of this file is
plain unit testing. The conflict matrix at the bottom drives `main._apply_magnet`
directly, because the precedence rule (env loses silently, a disagreeing flag is
a hard error) is the part a future change is most likely to get subtly wrong.
"""

from __future__ import annotations

import pytest

from trello_cli import config, magnet
from trello_cli.main import _apply_magnet

BOARD = "a9a56930df5f690a050c713a"
CARD = "94ab1031363168cc2d66b463"
SERVER = "https://trellno.example.com"


# ── slugify ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("Magnet links", "magnet-links"),
    ("Magnet links: one token (café) #42", "magnet-links-one-token-cafe-42"),
    ("  spaced   out  ", "spaced-out"),
    ("---leading and trailing---", "leading-and-trailing"),
    ("ALL CAPS", "all-caps"),
    ("naïve résumé", "naive-resume"),
    ("", ""),
    (None, ""),
    ("!!!", ""),          # folds to nothing -> caller omits the '#'
    ("日本語", ""),        # ditto: no ascii survives
])
def test_slugify(name, expected):
    assert magnet.slugify(name) == expected


def test_slugify_truncates_without_a_trailing_dash():
    long = "a word " * 20
    slug = magnet.slugify(long)
    assert len(slug) <= magnet.SLUG_MAX
    assert not slug.endswith("-")


def test_slug_is_shell_and_url_safe():
    slug = magnet.slugify("Rm -rf / & echo $HOME; ls #now")
    assert all(c.isalnum() or c == "-" for c in slug)


# ── build ─────────────────────────────────────────────────────────────

def test_build_card_full():
    assert magnet.build_card(CARD, BOARD, "local") == (
        f"trello://card/local/{BOARD}/{CARD}"
    )


def test_build_card_with_slug():
    link = magnet.build_card(CARD, BOARD, "local", name="Magnet links")
    assert link == f"trello://card/local/{BOARD}/{CARD}#magnet-links"


def test_build_card_short():
    link = magnet.build_card(CARD, BOARD, "local", short=True)
    assert link == f"trello://card/local/{BOARD[:8]}/{CARD[:8]}"


def test_build_board():
    assert magnet.build_board(BOARD, "trello") == f"trello://board/trello/{BOARD}"


def test_build_http_percent_encodes_the_server():
    link = magnet.build_card(CARD, BOARD, "http", server=SERVER)
    assert "://" not in link[len(magnet.SCHEME):]  # only the scheme's own
    assert magnet.parse(link)["server"] == SERVER


def test_build_http_without_a_server_is_an_error():
    with pytest.raises(SystemExit) as e:
        magnet.build_card(CARD, BOARD, "http")
    assert "configure-http" in str(e.value)


def test_build_rejects_an_unknown_backend():
    with pytest.raises(SystemExit):
        magnet.build_card(CARD, BOARD, "sqlite")


def test_backend_is_always_emitted_even_when_default():
    # Self-contained beats short: a magnet with no backend segment would
    # resolve against whatever the *receiver* happens to default to.
    assert "/trello/" in magnet.build_card(CARD, BOARD, "trello")


def test_the_server_token_is_never_in_the_link():
    # These strings get pasted into agent prompts and PR bodies.
    link = magnet.build_card(CARD, BOARD, "http", server=SERVER)
    assert "token" not in link.lower()


# ── round-trip ────────────────────────────────────────────────────────

@pytest.mark.parametrize("backend", ["trello", "local"])
@pytest.mark.parametrize("short", [False, True])
def test_card_round_trip(backend, short):
    link = magnet.build_card(CARD, BOARD, backend, name="Some card", short=short)
    got = magnet.parse(link)
    assert got["type"] == "card"
    assert got["backend"] == backend
    assert got["server"] is None
    assert got["slug"] == "some-card"
    expected_card, expected_board = (CARD, BOARD) if not short else (CARD[:8], BOARD[:8])
    assert (got["id"], got["board"]) == (expected_card, expected_board)


@pytest.mark.parametrize("backend", ["trello", "local"])
def test_board_round_trip(backend):
    link = magnet.build_board(BOARD, backend, name="Scratch")
    got = magnet.parse(link)
    assert got["type"] == "board"
    # A board magnet addresses its own board, so `id` and `board` agree.
    assert got["id"] == got["board"] == BOARD


@pytest.mark.parametrize("kind", ["card", "board"])
def test_http_round_trip(kind):
    link = (magnet.build_card(CARD, BOARD, "http", server=SERVER) if kind == "card"
            else magnet.build_board(BOARD, "http", server=SERVER))
    got = magnet.parse(link)
    assert got["backend"] == "http"
    assert got["server"] == SERVER
    assert got["board"] == BOARD


@pytest.mark.parametrize("server", [
    "https://trellno.example.com",
    "http://192.168.1.9:8787",
    "https://example.com/trello",          # a path survives the encoding
    "https://example.com:8443/a/b",
])
def test_server_urls_survive_encoding(server):
    link = magnet.build_board(BOARD, "http", server=server)
    assert magnet.parse(link)["server"] == server


# ── lenient parsing ───────────────────────────────────────────────────

@pytest.mark.parametrize("length", [4, 6, 8, 12, 24])
def test_hand_written_id_lengths_parse(length):
    link = f"trello://card/local/{BOARD[:length]}/{CARD[:length]}"
    got = magnet.parse(link)
    assert got["board"] == BOARD[:length]
    assert got["id"] == CARD[:length]


def test_uppercase_ids_are_normalized():
    got = magnet.parse(f"trello://card/local/{BOARD.upper()}/{CARD.upper()}")
    assert (got["board"], got["id"]) == (BOARD, CARD)


# ── the slug is ignored on parse ──────────────────────────────────────

@pytest.mark.parametrize("suffix", [
    "",                       # absent
    "#magnet-links",          # accurate
    "#the-old-name",          # stale, after a rename
    "#a/b/c",                 # slashes: still all slug, the split came first
    "#",                      # empty
    "#Wildly Different!!",    # never generated, still ignored
])
def test_slug_never_affects_resolution(suffix):
    got = magnet.parse(f"trello://card/local/{BOARD}/{CARD}{suffix}")
    assert (got["type"], got["board"], got["id"]) == ("card", BOARD, CARD)


# ── malformed tokens ──────────────────────────────────────────────────

@pytest.mark.parametrize("token,expect", [
    (f"https://card/local/{BOARD}/{CARD}", "does not start with"),
    (f"card/local/{BOARD}/{CARD}", "does not start with"),
    ("trello://", "nothing after the scheme"),
    (f"trello://cart/local/{BOARD}/{CARD}", "unknown entity type"),
    (f"trello://list/local/{BOARD}/{CARD}", "unknown entity type"),
    ("trello://card", "missing the <backend> segment"),
    (f"trello://card/sqlite/{BOARD}/{CARD}", "unknown backend"),
    (f"trello://card/local/{BOARD}", "needs <boardId>/<cardId>"),
    ("trello://board/local", "needs <boardId>"),
    (f"trello://card/local/{BOARD}/{CARD}/extra", "trailing segments"),
    (f"trello://card/local//{CARD}", "empty path segment"),
    (f"trello://card/local/{BOARD}/{CARD}/", "empty path segment"),
    (f"trello://card/local/{BOARD}/not-hex-at-all", "is not an id"),
    (f"trello://card/local/{BOARD}/ab", "is not an id"),          # too short
    (f"trello://card/http/{BOARD}/{CARD}", "not an http(s) URL"),  # server missing
    ("trello://board/http/ftp%3A%2F%2Fx.example", "not an http(s) URL"),
])
def test_malformed_tokens_fail_loudly(token, expect):
    with pytest.raises(SystemExit) as e:
        magnet.parse(token)
    assert expect in str(e.value)


def test_a_malformed_token_shows_the_grammar():
    # A magnet is machine-generated, so an unparseable one means a version
    # mismatch or a hand-edit — both worth surfacing with the shape expected.
    with pytest.raises(SystemExit) as e:
        magnet.parse("trello://card/local/zzzz/zzzz")
    msg = str(e.value)
    assert "trello://card/<backend>/<boardId>/<cardId>" in msg
    assert "trello card link" in msg


def test_a_hash_inside_a_segment_is_not_a_slug():
    # Splitting the slug off first means a stray '#' leaves the segment count
    # wrong, which is an error — not a token that quietly loses its card id.
    with pytest.raises(SystemExit):
        magnet.parse(f"trello://card/local/{BOARD}#x/{CARD}")


@pytest.mark.parametrize("token", [
    "trello://",
    "trello://nonsense",
    f"trello://card/local/{BOARD}",
])
def test_is_magnet_claims_even_broken_tokens(token):
    # Anything with the scheme is a magnet *attempt*, so it reaches parse() and
    # fails there rather than being re-read as a card-name prefix.
    assert magnet.is_magnet(token)


@pytest.mark.parametrize("token", ["", "94ab1031", "To Do", None, 7])
def test_is_magnet_rejects_non_magnets(token):
    assert not magnet.is_magnet(token)


# ── conflict matrix (main._apply_magnet) ──────────────────────────────

def _card_magnet(backend="local", server=None):
    return magnet.parse(magnet.build_card(CARD, BOARD, backend, server=server))


def test_magnet_seeds_board_and_backend():
    _apply_magnet(_card_magnet(), {})
    assert config.get_board_override() == BOARD
    assert config.get_backend_name() == "local"


def test_magnet_seeds_the_server_too():
    _apply_magnet(_card_magnet("http", SERVER), {})
    assert config.get_server_url() == SERVER


@pytest.mark.parametrize("var,value", [
    ("TRELLO_BOARD", "deadbeefdeadbeefdeadbeef"),
    ("TRELLO_BACKEND", "trello"),
])
def test_env_loses_silently(monkeypatch, var, value):
    # Env is an ambient default; the magnet is specific. An agent with
    # TRELLO_BOARD exported has to be able to paste a magnet and have it work.
    monkeypatch.setenv(var, value)
    _apply_magnet(_card_magnet(), {})
    assert config.get_board_override() == BOARD
    assert config.get_backend_name() == "local"


def test_disagreeing_backend_flag_is_an_error():
    with pytest.raises(SystemExit) as e:
        _apply_magnet(_card_magnet(), {"--backend": "trello"})
    assert "disagrees" in str(e.value)


def test_agreeing_backend_flag_is_fine():
    _apply_magnet(_card_magnet(), {"--backend": "local"})
    assert config.get_backend_name() == "local"


def test_disagreeing_board_flag_is_an_error():
    with pytest.raises(SystemExit) as e:
        _apply_magnet(_card_magnet(), {"--board": "deadbeefdeadbeefdeadbeef"})
    assert "disagrees" in str(e.value)


@pytest.mark.parametrize("flag", [BOARD, BOARD[:8], BOARD[:4], BOARD.upper()])
def test_agreeing_board_flag_is_fine(flag):
    _apply_magnet(_card_magnet(), {"--board": flag})
    assert config.get_board_override() == BOARD


def test_short_magnet_agrees_with_a_full_board_flag():
    mag = magnet.parse(magnet.build_card(CARD, BOARD, "local", short=True))
    _apply_magnet(mag, {"--board": BOARD})
    assert config.get_board_override() == BOARD[:8]


def test_a_board_name_alongside_a_magnet_is_refused():
    # We can't compare a name to an id without resolving it, and a resolve
    # could pick a third board — so it never agrees, and the message says so.
    with pytest.raises(SystemExit) as e:
        _apply_magnet(_card_magnet(), {"--board": "Scratch"})
    assert "name" in str(e.value)


def test_a_board_magnet_as_the_board_flag_is_agreement():
    _apply_magnet(_card_magnet(), {"--board": magnet.build_board(BOARD, "local")})
    assert config.get_board_override() == BOARD


def test_a_card_magnet_as_the_board_flag_is_refused():
    # The same token in TRELLO_BOARD is refused by _resolve_board_ref; the two
    # channels must not disagree.
    link = magnet.build_card(CARD, BOARD, "local")
    with pytest.raises(SystemExit) as e:
        _apply_magnet(magnet.parse(link), {"--board": link})
    assert "card magnet" in str(e.value)


def test_disagreeing_server_flag_is_an_error():
    with pytest.raises(SystemExit) as e:
        _apply_magnet(_card_magnet("http", SERVER),
                      {"--server": "https://elsewhere.example.com"})
    assert "disagrees" in str(e.value)


def test_a_trailing_slash_is_not_a_server_disagreement():
    _apply_magnet(_card_magnet("http", SERVER), {"--server": SERVER + "/"})
    assert config.get_server_url() == SERVER
