"""Area 6 — `trello search` and the `boards <query>` filter.

Two layers:
  * LocalBackend.search_cards — the matcher itself (field coverage, granularity,
    AND/negation, operators, sort).
  * the CLI — flag plumbing, backend-specific hints, and TrelloBackend's refusal
    to pretend it can do substring matching.

The matching semantics asserted here are the ones probed against the live Trello
API on 2026-07-26 (see plans/search.md): whole word by default, word-prefix under
`partial`, AND across terms, `-term` negates. `substring` is the local store's
own extension — Trello's index cannot do it, which is why the refusal test below
matters as much as the positive ones.
"""

from __future__ import annotations

import httpx
import pytest

from trello_cli import api, config, main
from trello_cli.backends.trello import TrelloBackend

from tests.conftest import use_local_cli


@pytest.fixture
def searchable(backend):
    """A board whose cards each hide their distinguishing word in a DIFFERENT
    field, so a test can prove which fields are actually searched."""
    b = backend.create_board("Roadmap")
    bid = b["id"]
    lists = {l["name"]: l["id"] for l in backend.get_lists(bid)}

    in_name = backend.create_card(lists["To Do"], "Fix the scrollbar flicker")
    in_desc = backend.create_card(lists["To Do"], "Login bug",
                                  desc="Session cookie is dropped on Safari 17.")
    in_comment = backend.create_card(lists["Doing"], "Rebalance work")
    backend.add_comment(in_comment["id"], "LocalBackend respread the siblings.")
    in_checklist = backend.create_card(lists["Done"], "Release chores")
    cl = backend.create_checklist(in_checklist["id"], "Steps")
    backend.add_checkitem(cl["id"], "Publish the changelog")

    return {
        "backend": backend, "bid": bid, "lists": lists,
        "name": in_name["id"], "desc": in_desc["id"],
        "comment": in_comment["id"], "checklist": in_checklist["id"],
    }


def _ids(cards):
    return {c["id"] for c in cards}


# ── field coverage ────────────────────────────────────────────────────

@pytest.mark.parametrize("query,key", [
    ("scrollbar", "name"),
    ("cookie", "desc"),
    ("respread", "comment"),
    ("changelog", "checklist"),
])
def test_searches_every_field(searchable, query, key):
    """Name, description, comments and checklist items are all in scope — the
    coverage Trello's own index has."""
    be, bid = searchable["backend"], searchable["bid"]
    assert _ids(be.search_cards(bid, query)) == {searchable[key]}


def test_desc_hit_reports_match_context(searchable):
    """A desc/comment hit is an unexplained row without the matching line, so the
    backend hands one back for the CLI's Matches: block."""
    be, bid = searchable["backend"], searchable["bid"]
    (card,) = be.search_cards(bid, "cookie")
    assert card["_match"]["field"] == "desc"
    assert "Safari 17" in card["_match"]["line"]


def test_name_hit_has_no_match_context(searchable):
    """A name hit is already visible in the table — no context line needed."""
    be, bid = searchable["backend"], searchable["bid"]
    (card,) = be.search_cards(bid, "scrollbar")
    assert "_match" not in card


def test_match_key_is_transient(searchable):
    """`_match` is set after the read and must never reach the stored card."""
    be, bid = searchable["backend"], searchable["bid"]
    be.search_cards(bid, "cookie")
    assert "_match" not in be.get_card(searchable["desc"])


# ── match granularity ─────────────────────────────────────────────────

def test_whole_word_is_the_default(searchable):
    """`scroll` must NOT find `scrollbar` — Trello's plain search doesn't."""
    be, bid = searchable["backend"], searchable["bid"]
    assert be.search_cards(bid, "scroll") == []


def test_partial_matches_word_prefix(searchable):
    """Trello's own `partial=true` behaviour: scroll -> scrollbar."""
    be, bid = searchable["backend"], searchable["bid"]
    assert _ids(be.search_cards(bid, "scroll", partial=True)) == {searchable["name"]}


def test_partial_does_not_match_mid_word(searchable):
    """A prefix index is still not a substring index."""
    be, bid = searchable["backend"], searchable["bid"]
    assert be.search_cards(bid, "crollba", partial=True) == []


def test_substring_matches_mid_word(searchable):
    """The local store's extension — the thing Trello physically cannot do."""
    be, bid = searchable["backend"], searchable["bid"]
    assert _ids(be.search_cards(bid, "crollba", substring=True)) == {searchable["name"]}


def test_granularity_operators_are_per_term(searchable):
    """`substring:` / `partial:` / `word:` set granularity for ONE term, so a
    query can mix a strict term with a loose one."""
    be, bid = searchable["backend"], searchable["bid"]
    assert _ids(be.search_cards(bid, "substring:crollba")) == {searchable["name"]}
    # word: forces strictness back on even when the query default is loose.
    assert be.search_cards(bid, "word:crollba", substring=True) == []


def test_word_split_crosses_punctuation(backend):
    """Trello matches `drop` inside `drag-drop`; splitting on non-alphanumerics
    is what reproduces that."""
    b = backend.create_board("B")
    lists = backend.get_lists(b["id"])
    card = backend.create_card(lists[0]["id"], "Web app (FastAPI + drag-drop)")
    assert _ids(backend.search_cards(b["id"], "drop")) == {card["id"]}


def test_matching_is_case_insensitive(searchable):
    be, bid = searchable["backend"], searchable["bid"]
    assert _ids(be.search_cards(bid, "SCROLLBAR")) == {searchable["name"]}


# ── multi-term AND / negation ─────────────────────────────────────────

def test_terms_are_anded(searchable):
    """`scrollbar bananas` finds nothing — as probed, terms AND rather than OR."""
    be, bid = searchable["backend"], searchable["bid"]
    assert _ids(be.search_cards(bid, "fix scrollbar")) == {searchable["name"]}
    assert be.search_cards(bid, "scrollbar bananas") == []


def test_negation_excludes(searchable):
    """`-term` is Trello's documented negation, and the one operator that
    behaved correctly when probed."""
    be, bid = searchable["backend"], searchable["bid"]
    assert _ids(be.search_cards(bid, "the -scrollbar")) != set()
    assert searchable["name"] not in _ids(be.search_cards(bid, "the -scrollbar"))


def test_negation_only_query(searchable):
    """A query of nothing but exclusions still returns the complement."""
    be, bid = searchable["backend"], searchable["bid"]
    got = _ids(be.search_cards(bid, "-scrollbar"))
    assert searchable["name"] not in got
    assert searchable["desc"] in got


# ── operators ─────────────────────────────────────────────────────────

def test_field_scoped_operators(searchable):
    """`description:` hits only the description; `name:` only the name."""
    be, bid = searchable["backend"], searchable["bid"]
    assert _ids(be.search_cards(bid, "description:cookie")) == {searchable["desc"]}
    assert be.search_cards(bid, "name:cookie") == []
    assert _ids(be.search_cards(bid, "comment:respread")) == {searchable["comment"]}
    assert _ids(be.search_cards(bid, "checklist:changelog")) == {searchable["checklist"]}


def test_list_operator_scopes_to_a_column(searchable):
    be, bid = searchable["backend"], searchable["bid"]
    assert _ids(be.search_cards(bid, "list:doing")) == {searchable["comment"]}


def test_is_operator_reaches_archived_without_all(searchable):
    """An explicit `is:archived` decides visibility on its own, so it works
    without also passing --all."""
    be, bid = searchable["backend"], searchable["bid"]
    be.archive_card(searchable["desc"])
    assert be.search_cards(bid, "cookie") == []
    assert _ids(be.search_cards(bid, "cookie is:archived")) == {searchable["desc"]}


def test_has_operator(searchable):
    be, bid = searchable["backend"], searchable["bid"]
    got = _ids(be.search_cards(bid, "has:description"))
    assert searchable["desc"] in got and searchable["name"] not in got


def test_label_operator(searchable):
    be, bid = searchable["backend"], searchable["bid"]
    label = be.create_label(bid, "urgent", "red")
    be.add_label_to_card(searchable["name"], label["id"])
    assert _ids(be.search_cards(bid, "label:urgent")) == {searchable["name"]}


def test_unknown_operator_degrades_to_text(searchable):
    """An operator we don't implement must not raise — it becomes a literal
    term, so a query is never rejected for using one."""
    be, bid = searchable["backend"], searchable["bid"]
    assert be.search_cards(bid, "bogus:thing") == []


def test_sort_operator_orders_results(searchable):
    """`sort:edited` reorders; the default keeps board order (list, then pos)."""
    be, bid = searchable["backend"], searchable["bid"]
    be.update_card(searchable["checklist"], name="Release chores now")
    got = [c["id"] for c in be.search_cards(bid, "-zzzz sort:edited")]
    assert got[-1] == searchable["checklist"]


# ── archived / scoping ────────────────────────────────────────────────

def test_archived_hidden_by_default(searchable):
    be, bid = searchable["backend"], searchable["bid"]
    be.archive_card(searchable["desc"])
    assert be.search_cards(bid, "cookie") == []
    assert _ids(be.search_cards(bid, "cookie", include_closed=True)) == {searchable["desc"]}


def test_list_id_scoping(searchable):
    be, bid, lists = searchable["backend"], searchable["bid"], searchable["lists"]
    assert be.search_cards(bid, "respread", list_id=lists["Doing"])
    assert be.search_cards(bid, "respread", list_id=lists["To Do"]) == []


def test_empty_query_returns_nothing(searchable):
    be, bid = searchable["backend"], searchable["bid"]
    assert be.search_cards(bid, "   ") == []


# ── TrelloBackend: forwards, and refuses what it can't do ─────────────

def _trello_backend(handler):
    """A TrelloBackend wired to a MockTransport — same shape test_conformance
    uses, so no network is ever touched."""
    be = TrelloBackend()
    be._auth = ("key", "token")
    be._client = httpx.Client(base_url="https://api.trello.com/1",
                              transport=httpx.MockTransport(handler))
    return be


@pytest.mark.parametrize("kwargs,query", [
    ({"substring": True}, "crollba"),
    ({}, "substring:crollba"),
    ({}, "partial:scroll"),
])
def test_trello_refuses_substring(kwargs, query):
    """Silently degrading to a word match would return plausible results for a
    query that meant something else — refuse instead, both for the flag and for
    the per-term operators."""
    be = _trello_backend(lambda r: httpx.Response(200, json={}))
    with pytest.raises(SystemExit) as e:
        be.search_cards("b" * 24, query, **kwargs)
    assert "--backend local" in str(e.value)


def test_trello_passes_query_through_verbatim():
    """Trello's own operators must reach Trello untouched — this backend
    deliberately doesn't reimplement them."""
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"cards": []})

    be = _trello_backend(handler)
    be.search_cards("b" * 24, "due:week -done label:bug", partial=True)
    assert "due%3Aweek" in seen["url"] and "label%3Abug" in seen["url"]
    assert "partial=true" in seen["url"]


def test_trello_post_filters_closed_and_list():
    """`is:archived` misbehaved when probed, so visibility/list scoping are
    applied client-side rather than trusted to the operators."""
    cards = [
        {"id": "a" * 24, "idList": "l1", "closed": False, "name": "open here"},
        {"id": "b" * 24, "idList": "l1", "closed": True, "name": "closed here"},
        {"id": "c" * 24, "idList": "l2", "closed": False, "name": "other list"},
    ]
    be = _trello_backend(lambda r: httpx.Response(200, json={"cards": cards}))
    assert _ids(be.search_cards("b" * 24, "here")) == {"a" * 24, "c" * 24}
    assert _ids(be.search_cards("b" * 24, "here", include_closed=True)) == {
        "a" * 24, "b" * 24, "c" * 24}
    assert _ids(be.search_cards("b" * 24, "here", list_id="l1")) == {"a" * 24}


# ── CLI ───────────────────────────────────────────────────────────────

def test_cli_search_renders_table_and_context(searchable, store_root, capsys):
    be = use_local_cli(store_root)
    del be
    config.set_board_override(searchable["bid"])
    main.cmd_search(["cookie"])
    out = capsys.readouterr().out
    assert "Login bug" in out
    assert "Matches:" in out and "Safari 17" in out


def test_cli_no_match_hints_at_substring_on_local(searchable, store_root, capsys):
    """The discoverability affordance: a local no-match advertises --substring,
    which is the one thing this backend can do and Trello can't."""
    be = use_local_cli(store_root)
    del be
    config.set_board_override(searchable["bid"])
    main.cmd_search(["crollba"])
    out = capsys.readouterr().out
    assert "No cards matching" in out and "--substring" in out


def test_cli_hints_trello_only_operators_on_local(searchable, store_root, capsys):
    """Gated on the query actually using one, per the CLI's hint convention."""
    be = use_local_cli(store_root)
    del be
    config.set_board_override(searchable["bid"])
    main.cmd_search(["created:week", "scrollbar"])
    out = capsys.readouterr().out
    assert "Trello-backend operators" in out
    capsys.readouterr()
    main.cmd_search(["scrollbar"])
    assert "Trello-backend operators" not in capsys.readouterr().out


def test_cli_requires_a_query(searchable, store_root):
    be = use_local_cli(store_root)
    del be
    config.set_board_override(searchable["bid"])
    with pytest.raises(SystemExit) as e:
        main.cmd_search([])
    assert "Usage:" in str(e.value)


def test_cli_rejects_invented_flags(searchable, store_root):
    """The invented-flag guard: `--card X` must not become a search term."""
    be = use_local_cli(store_root)
    del be
    config.set_board_override(searchable["bid"])
    with pytest.raises(SystemExit) as e:
        main.cmd_search(["--card", "scrollbar"])
    assert "Unknown flag" in str(e.value)


def test_cli_double_dash_escapes_a_dashed_query(searchable, store_root, capsys):
    """A query that really starts with dashes stays reachable."""
    be = use_local_cli(store_root)
    del be
    config.set_board_override(searchable["bid"])
    main.cmd_search(["--", "-scrollbar"])
    out = capsys.readouterr().out
    assert "Login bug" in out and "scrollbar flicker" not in out


def test_hint_operator_list_matches_the_backend():
    """main.py's hint vocabulary and local.py's operator table must not drift —
    the hint claims these are unsupported locally, so the backend had better
    agree."""
    from trello_cli.backends import local as local_mod
    q = local_mod._parse_query_terms(
        " ".join(f"{op}:x" for op in main._TRELLO_ONLY_OPS))
    assert len(q.unsupported) == len(main._TRELLO_ONLY_OPS)
    assert q.terms == []


# ── boards <query> ────────────────────────────────────────────────────

def test_boards_query_filters_by_name_and_id(backend, store_root, capsys):
    be = use_local_cli(store_root)
    roadmap = be.create_board("Roadmap")
    be.create_board("Scratch")
    config.set_backend_override("local")

    main.cmd_boards(["roadmap"])
    out = capsys.readouterr().out
    assert "Roadmap" in out and "Scratch" not in out

    # substring, not just prefix — you rarely know how a board name starts
    main.cmd_boards(["oadma"])
    assert "Roadmap" in capsys.readouterr().out

    main.cmd_boards([roadmap["id"][:6]])
    assert "Roadmap" in capsys.readouterr().out


def test_boards_query_no_match_message(backend, store_root, capsys):
    be = use_local_cli(store_root)
    be.create_board("Roadmap")
    main.cmd_boards(["nonesuch"])
    out = capsys.readouterr().out
    assert "No open boards matching" in out and "--all" in out


def test_boards_query_composes_with_archived(backend, store_root, capsys):
    be = use_local_cli(store_root)
    old = be.create_board("Old Roadmap")
    be.create_board("Live Roadmap")
    be.update_board(old["id"], closed=True)

    main.cmd_boards(["roadmap"])
    out = capsys.readouterr().out
    assert "Live Roadmap" in out and "Old Roadmap" not in out

    main.cmd_boards(["roadmap", "--archived"])
    out = capsys.readouterr().out
    assert "Old Roadmap" in out and "Live Roadmap" not in out


def test_boards_query_json_is_filtered(backend, store_root, capsys, monkeypatch):
    be = use_local_cli(store_root)
    be.create_board("Roadmap")
    be.create_board("Scratch")
    monkeypatch.setattr(main, "_JSON_MODE", True)
    main.cmd_boards(["roadmap"])
    import json
    data = json.loads(capsys.readouterr().out)
    assert [b["name"] for b in data] == ["Roadmap"]


def test_search_reaches_the_facade(searchable, store_root):
    """`api.search_cards` forwards to the selected backend — the seam commands
    actually go through."""
    use_local_cli(store_root)
    got = api.search_cards(searchable["bid"], "cookie")
    assert _ids(got) == {searchable["desc"]}
