"""Area 6 — `trello search` and the `boards <query>` filter.

Two layers:
  * LocalBackend.search_cards — the matcher itself (field coverage, granularity,
    AND/negation, operators, sort).
  * the CLI — flag plumbing, backend-specific hints, and TrelloBackend's refusal
    to pretend it can do substring matching.

The matching semantics asserted here are the ones probed against the live Trello
API on 2026-07-26 (the probe table lives in DESIGN.md): whole word by default, word-prefix under
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


# ── cross-board search ────────────────────────────────────────────────


@pytest.fixture
def two_boards(searchable):
    """`searchable`'s "Roadmap" plus a second board that shares a word with it,
    so a hit can only be attributed by which board it came from."""
    be = searchable["backend"]
    other = be.create_board("Infra")
    lists = {l["name"]: l["id"] for l in be.get_lists(other["id"])}
    card = be.create_card(lists["To Do"], "Rotate the scrollbar certificate")
    return {**searchable, "other_bid": other["id"], "other_card": card["id"]}


def test_no_board_searches_every_board(two_boards):
    """`board_id=None` is the whole feature: one query, every board."""
    be = two_boards["backend"]
    assert _ids(be.search_cards(None, "scrollbar")) == {
        two_boards["name"], two_boards["other_card"]}
    # ...and scoping still excludes the other board.
    assert _ids(be.search_cards(two_boards["bid"], "scrollbar")) == {
        two_boards["name"]}


def test_cross_board_hits_carry_their_board(two_boards):
    """`idBoard` is how a caller attributes a cross-board hit, so every result
    must have one — it is the only thing distinguishing the two boards' cards."""
    be = two_boards["backend"]
    got = {c["id"]: c["idBoard"] for c in be.search_cards(None, "scrollbar")}
    assert got[two_boards["name"]] == two_boards["bid"]
    assert got[two_boards["other_card"]] == two_boards["other_bid"]


def test_cross_board_order_is_stable(two_boards):
    """Board order is `store.board_ids()` (sorted ids), then board order within
    each — deterministic, so two identical runs agree."""
    be = two_boards["backend"]
    first = [c["id"] for c in be.search_cards(None, "scrollbar")]
    assert first == [c["id"] for c in be.search_cards(None, "scrollbar")]
    by_board = sorted([two_boards["bid"], two_boards["other_bid"]])
    assert [c["idBoard"] for c in be.search_cards(None, "scrollbar")] == by_board


def test_cross_board_sort_orders_the_whole_result(two_boards):
    """A `sort:` key orders every board's hits together, not each board's
    separately — otherwise the boards, not the key, would decide the order."""
    be = two_boards["backend"]
    be.update_card(two_boards["name"], due="2027-01-01T00:00:00.000Z")
    be.update_card(two_boards["other_card"], due="2026-01-01T00:00:00.000Z")
    got = [c["id"] for c in be.search_cards(None, "scrollbar sort:due")]
    assert got == [two_boards["other_card"], two_boards["name"]]


def test_cross_board_skips_archived_boards_unless_all(two_boards):
    """--all means "include the hidden things" uniformly: archived cards, and
    searching every board, archived boards too."""
    be = two_boards["backend"]
    be.update_board(two_boards["other_bid"], closed=True)
    assert _ids(be.search_cards(None, "scrollbar")) == {two_boards["name"]}
    assert _ids(be.search_cards(None, "scrollbar", include_closed=True)) == {
        two_boards["name"], two_boards["other_card"]}


def test_board_operator_filters_by_board_name(two_boards):
    """`board:` is only interesting cross-board, which is why it could not be
    implemented until now."""
    be = two_boards["backend"]
    assert _ids(be.search_cards(None, "scrollbar board:infra")) == {
        two_boards["other_card"]}
    # prefix, like `list:` — and negation drops that board instead
    assert _ids(be.search_cards(None, "scrollbar board:road")) == {
        two_boards["name"]}
    assert _ids(be.search_cards(None, "scrollbar -board:infra")) == {
        two_boards["name"]}


def test_board_operator_is_no_longer_trello_only(two_boards):
    """It used to be literal text (matching nothing); now it filters. The
    _TRELLO_ONLY_OPS agreement test covers the other half of this move."""
    from trello_cli.backends import local as local_mod
    assert "board" not in local_mod._TRELLO_ONLY_OPS
    assert "board" not in main._TRELLO_ONLY_OPS
    q = local_mod._parse_query_terms("board:infra")
    assert q.filters == [("board", "infra", False)] and q.terms == []


def test_unknown_operator_degrades_to_text(searchable):
    """An operator we don't implement must not raise — it becomes a literal
    term, so a query is never rejected for using one."""
    be, bid = searchable["backend"], searchable["bid"]
    assert be.search_cards(bid, "bogus:thing") == []


def test_sort_edited_is_most_recent_first(searchable):
    """Trello's `sort:edited` is most-recently-edited FIRST. `-zzzz` is a
    match-everything trick: nothing contains "zzzz", so negating it keeps every
    card and leaves sorting as the only thing under test."""
    be, bid = searchable["backend"], searchable["bid"]
    be.update_card(searchable["checklist"], name="Release chores now")
    got = [c["id"] for c in be.search_cards(bid, "-zzzz sort:edited")]
    assert got[0] == searchable["checklist"]


def test_sort_due_is_soonest_first(searchable):
    """`sort:due` runs the other way — soonest deadline first — so direction is
    per-key, not global. Cards with no due date sort last either way."""
    be, bid = searchable["backend"], searchable["bid"]
    be.update_card(searchable["name"], due="2030-01-01T00:00:00.000Z")
    be.update_card(searchable["desc"], due="2026-01-01T00:00:00.000Z")
    got = [c["id"] for c in be.search_cards(bid, "-zzzz sort:due")]
    assert got[:2] == [searchable["desc"], searchable["name"]]
    assert searchable["comment"] in got[2:]  # undated, pushed to the end


def test_negated_sort_is_ignored(searchable):
    """`-sort:due` is meaningless; it must not apply as though un-negated."""
    be, bid = searchable["backend"], searchable["bid"]
    be.update_card(searchable["name"], due="2030-01-01T00:00:00.000Z")
    default = [c["id"] for c in be.search_cards(bid, "-zzzz")]
    assert [c["id"] for c in be.search_cards(bid, "-zzzz -sort:due")] == default


# ── operators that only Trello can answer ─────────────────────────────

@pytest.mark.parametrize("query", ["created:week", "member:bob"])
def test_trello_only_operators_are_literal_text_not_dropped(searchable, query):
    """The failure mode this guards: DROPPING the operator would silently WIDEN
    the result set, handing back cards the caller asked to exclude. As literal
    text the query narrows to nothing instead — and the CLI hints why."""
    be, bid = searchable["backend"], searchable["bid"]
    assert be.search_cards(bid, f"{query} scrollbar") == []


def test_unsupported_operators_are_recorded(searchable):
    from trello_cli.backends import local as local_mod
    q = local_mod._parse_query_terms("created:week scrollbar")
    assert q.unsupported == ["created:week"]
    assert [t.text for t in q.terms] == ["created:week", "scrollbar"]


# ── quoting ───────────────────────────────────────────────────────────

def test_quoted_operator_value(searchable):
    """`list:"To Do"` has to survive as one token — the default columns have a
    space in them, so this is the commonest possible scoped search."""
    be, bid = searchable["backend"], searchable["bid"]
    got = _ids(be.search_cards(bid, 'list:"To Do"'))
    assert got == {searchable["name"], searchable["desc"]}


def test_quoted_phrase_term(searchable):
    be, bid = searchable["backend"], searchable["bid"]
    assert _ids(be.search_cards(bid, '"session cookie"')) == {searchable["desc"]}
    assert be.search_cards(bid, '"cookie session"') == []


def test_unbalanced_quote_does_not_raise(searchable):
    """A stray quote is user text, not a crash."""
    be, bid = searchable["backend"], searchable["bid"]
    assert _ids(be.search_cards(bid, 'scrollbar"')) == {searchable["name"]}


# ── punctuated terms ──────────────────────────────────────────────────

def test_punctuated_term_matches(backend):
    """The term is tokenised too, not just the haystack — otherwise no term
    containing punctuation could ever match under word/partial."""
    b = backend.create_board("B")
    lists = backend.get_lists(b["id"])
    card = backend.create_card(lists[0]["id"], "Web app (FastAPI + drag-drop)",
                               desc="see trello_cli/__main__.py")
    bid = b["id"]
    assert _ids(backend.search_cards(bid, "drag-drop")) == {card["id"]}
    assert _ids(backend.search_cards(bid, "trello_cli/__main__.py")) == {card["id"]}
    # adjacency still matters: the sub-words must appear in order
    assert backend.search_cards(bid, "drop-drag") == []


def test_punctuated_term_under_partial(backend):
    b = backend.create_board("B")
    lists = backend.get_lists(b["id"])
    card = backend.create_card(lists[0]["id"], "Web app (FastAPI + drag-drop)")
    assert _ids(backend.search_cards(b["id"], "drag-dro", partial=True)) == {card["id"]}


# ── date operators ────────────────────────────────────────────────────

def _iso(days_from_now):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(days=days_from_now)).isoformat()


def test_due_windows_look_forward(searchable):
    """`due:week` is now..now+7d, so it excludes an already-overdue card —
    `due:overdue` is the separate value for those."""
    be, bid = searchable["backend"], searchable["bid"]
    be.update_card(searchable["name"], due=_iso(3))
    be.update_card(searchable["desc"], due=_iso(-3))
    assert _ids(be.search_cards(bid, "due:week")) == {searchable["name"]}
    assert _ids(be.search_cards(bid, "due:overdue")) == {searchable["desc"]}
    assert _ids(be.search_cards(bid, "due:day")) == set()


def test_due_complete_and_incomplete(searchable):
    be, bid = searchable["backend"], searchable["bid"]
    be.update_card(searchable["name"], due=_iso(3))
    be.update_card(searchable["desc"], due=_iso(3), dueComplete=True)
    assert _ids(be.search_cards(bid, "due:complete")) == {searchable["desc"]}
    assert _ids(be.search_cards(bid, "due:incomplete")) == {searchable["name"]}
    # an overdue-but-completed card is not overdue
    be.update_card(searchable["desc"], due=_iso(-3))
    assert searchable["desc"] not in _ids(be.search_cards(bid, "due:overdue"))


def test_undated_card_matches_no_due_filter(searchable):
    be, bid = searchable["backend"], searchable["bid"]
    for value in ("day", "week", "month", "overdue", "complete", "incomplete"):
        assert searchable["comment"] not in _ids(be.search_cards(bid, f"due:{value}"))


def test_malformed_dates_do_not_raise(searchable):
    """A hand-edited store shouldn't crash a search."""
    be, bid = searchable["backend"], searchable["bid"]
    board_id, card = be._load_card(searchable["name"])
    card["due"] = "not-a-date"
    card["dateLastActivity"] = "also-not-a-date"
    be._save_card(board_id, card)
    assert be.search_cards(bid, "due:week") == []
    assert searchable["name"] not in _ids(be.search_cards(bid, "edited:day"))


def test_edited_window(searchable):
    """Everything was just created, so it's all within a day."""
    be, bid = searchable["backend"], searchable["bid"]
    assert len(be.search_cards(bid, "edited:day")) == 4
    assert be.search_cards(bid, "edited:bogus") == []


def test_unknown_filter_value_matches_nothing(searchable):
    be, bid = searchable["backend"], searchable["bid"]
    assert be.search_cards(bid, "is:sideways") == []
    assert be.search_cards(bid, "has:cover") == []


# ── negated filters ───────────────────────────────────────────────────

def test_negated_filters(searchable):
    be, bid = searchable["backend"], searchable["bid"]
    got = _ids(be.search_cards(bid, "-list:doing"))
    assert searchable["comment"] not in got and searchable["name"] in got
    label = be.create_label(bid, "urgent", "red")
    be.add_label_to_card(searchable["name"], label["id"])
    assert searchable["name"] not in _ids(be.search_cards(bid, "-label:urgent"))


def test_is_open_does_not_resurrect_cards_in_archived_columns(searchable):
    """A card in an archived column is invisible on Trello (the whole column is
    gone), so `is:open` must not surface it — only --all reaches it."""
    be, bid, lists = searchable["backend"], searchable["bid"], searchable["lists"]
    be.update_list(lists["Doing"], closed=True)
    assert be.search_cards(bid, "respread") == []
    assert be.search_cards(bid, "respread is:open") == []
    assert _ids(be.search_cards(bid, "respread", include_closed=True)) == \
        {searchable["comment"]}


def test_match_line_is_capped(backend):
    """One pathological description can't dump a screenful per result."""
    from trello_cli.backends.local import _MATCH_LINE_MAX
    b = backend.create_board("B")
    lists = backend.get_lists(b["id"])
    backend.create_card(lists[0]["id"], "Long",
                        desc="padding " * 200 + "needle")
    (card,) = backend.search_cards(b["id"], "needle")
    assert len(card["_match"]["line"]) <= _MATCH_LINE_MAX


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
    use_local_cli(store_root)
    config.set_board_override(searchable["bid"])
    main.cmd_search(["cookie"])
    out = capsys.readouterr().out
    assert "Login bug" in out
    assert "Matches:" in out and "Safari 17" in out


def test_cli_no_match_hints_at_substring_on_local(searchable, store_root, capsys):
    """The discoverability affordance: a local no-match advertises --substring,
    which is the one thing this backend can do and Trello can't."""
    use_local_cli(store_root)
    config.set_board_override(searchable["bid"])
    main.cmd_search(["crollba"])
    out = capsys.readouterr().out
    assert "No cards matching" in out and "--substring" in out


def test_cli_hints_trello_only_operators_on_local(searchable, store_root, capsys):
    """Gated on the query actually using one, per the CLI's hint convention."""
    use_local_cli(store_root)
    config.set_board_override(searchable["bid"])
    main.cmd_search(["created:week", "scrollbar"])
    out = capsys.readouterr().out
    assert "Trello-backend operators" in out
    capsys.readouterr()
    main.cmd_search(["scrollbar"])
    assert "Trello-backend operators" not in capsys.readouterr().out


def test_cli_flags_reach_the_backend(searchable, store_root, capsys):
    """--partial / --substring / --list / --all are plumbed, not just accepted."""
    use_local_cli(store_root)
    config.set_board_override(searchable["bid"])

    main.cmd_search(["scroll"])
    assert "No cards matching" in capsys.readouterr().out
    main.cmd_search(["scroll", "--partial"])
    assert "scrollbar flicker" in capsys.readouterr().out
    main.cmd_search(["crollba", "--substring"])
    assert "scrollbar flicker" in capsys.readouterr().out

    main.cmd_search(["respread", "--list", "Doing"])
    assert "Rebalance work" in capsys.readouterr().out
    main.cmd_search(["respread", "--list", "To Do"])
    assert "No cards matching" in capsys.readouterr().out

    searchable["backend"].archive_card(searchable["desc"])
    main.cmd_search(["cookie"])
    assert "No cards matching" in capsys.readouterr().out
    main.cmd_search(["cookie", "--all"])
    assert "Login bug" in capsys.readouterr().out


def test_cli_search_help_only_when_alone(searchable, store_root, capsys):
    """`search help` is a search for the word "help", not a help request."""
    use_local_cli(store_root)
    config.set_board_override(searchable["bid"])
    main.cmd_search(["--help"])
    assert "MATCHING" in capsys.readouterr().out
    main.cmd_search(["help"])
    out = capsys.readouterr().out
    assert "No cards matching" in out and "MATCHING" not in out


def test_cli_json_emits_cards_and_sends_hints_to_stderr(
        searchable, store_root, capsys, monkeypatch):
    """stdout stays a clean JSON array so `| jq` works; notes go to stderr."""
    import json
    use_local_cli(store_root)
    config.set_board_override(searchable["bid"])
    monkeypatch.setattr(main, "_JSON_MODE", True)
    main.cmd_search(["created:week", "crollba"])
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []
    assert "Trello-backend operators" in captured.err
    assert captured.err.count("--substring") >= 1

    main.cmd_search(["cookie"])
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert [c["name"] for c in data] == ["Login bug"]
    assert data[0]["_match"]["field"] == "desc"


def test_cli_card_search_points_at_the_top_level_command(searchable, store_root):
    """`trello card search cookie` used to die with "List not found: search"."""
    use_local_cli(store_root)
    config.set_board_override(searchable["bid"])
    with pytest.raises(SystemExit) as e:
        main.cmd_card(["search", "cookie"])
    assert "trello search <query>" in str(e.value)


def test_cli_requires_a_query(searchable, store_root):
    use_local_cli(store_root)
    config.set_board_override(searchable["bid"])
    with pytest.raises(SystemExit) as e:
        main.cmd_search([])
    assert "Usage:" in str(e.value)


def test_cli_no_board_searches_every_board(two_boards, store_root, capsys):
    """With no --board this used to be `_require_board`'s error. Nothing that
    worked before changes — an error became a feature."""
    use_local_cli(store_root)
    main.cmd_search(["scrollbar"])
    out = capsys.readouterr().out
    assert "scrollbar flicker" in out and "scrollbar certificate" in out


def test_cli_all_boards_beats_an_ambient_board(two_boards, store_root, capsys):
    """TRELLO_BOARD is an ambient default, so --all-boards has to be able to
    reach cross-board from a session that exports one."""
    use_local_cli(store_root)
    config.set_board_override(two_boards["bid"])
    main.cmd_search(["scrollbar"])
    assert "scrollbar certificate" not in capsys.readouterr().out
    main.cmd_search(["scrollbar", "--all-boards"])
    assert "scrollbar certificate" in capsys.readouterr().out


def test_cli_board_column_only_appears_cross_board(two_boards, store_root, capsys):
    """A bare list name is ambiguous across boards, so cross-board output names
    the board — and a scoped search renders exactly as it always did."""
    use_local_cli(store_root)
    main.cmd_search(["scrollbar"])
    out = capsys.readouterr().out
    assert "Board" in out.splitlines()[0]
    assert "Infra" in out and "Roadmap" in out

    config.set_board_override(two_boards["bid"])
    main.cmd_search(["scrollbar"])
    header = capsys.readouterr().out.splitlines()[0]
    assert "Board" not in header and header.split() == [
        "ID", "List", "Activity", "Name", "Labels", "Due"]


def test_cli_json_is_unchanged_cross_board(two_boards, store_root, capsys,
                                           monkeypatch):
    """No synthetic key for attribution — `idBoard` was always on the card."""
    import json
    use_local_cli(store_root)
    monkeypatch.setattr(main, "_JSON_MODE", True)
    main.cmd_search(["scrollbar"])
    data = json.loads(capsys.readouterr().out)
    assert {c["idBoard"] for c in data} == {two_boards["bid"],
                                            two_boards["other_bid"]}


def test_cli_list_flag_needs_a_board(two_boards, store_root):
    """--list means nothing without one board to resolve the column against;
    guessing a board would be worse than the error."""
    use_local_cli(store_root)
    with pytest.raises(SystemExit) as e:
        main.cmd_search(["scrollbar", "--list", "To Do"])
    msg = str(e.value)
    assert "--board" in msg and "list:" in msg


def test_cli_hints_the_board_it_searched_on_a_miss(searchable, store_root, capsys):
    """A miss on one board can't be told from "it isn't anywhere" in the output,
    so say which board was looked at — and don't say it when nothing was
    scoped away."""
    use_local_cli(store_root)
    config.set_board_override(searchable["bid"])
    main.cmd_search(["bananas"])
    out = capsys.readouterr().out
    assert 'Searched only the board "Roadmap"' in out and "--all-boards" in out

    main.cmd_search(["bananas", "--all-boards"])
    assert "Searched only the board" not in capsys.readouterr().out


def test_cli_rejects_invented_flags(searchable, store_root):
    """The invented-flag guard: `--card X` must not become a search term."""
    use_local_cli(store_root)
    config.set_board_override(searchable["bid"])
    with pytest.raises(SystemExit) as e:
        main.cmd_search(["--card", "scrollbar"])
    assert "Unknown flag" in str(e.value)


def test_cli_double_dash_escapes_a_dashed_query(searchable, store_root, capsys):
    """A query that really starts with dashes stays reachable."""
    use_local_cli(store_root)
    config.set_board_override(searchable["bid"])
    main.cmd_search(["--", "-scrollbar"])
    out = capsys.readouterr().out
    assert "Login bug" in out and "scrollbar flicker" not in out


def test_hint_operator_list_matches_the_backend():
    """main.py's hint vocabulary and local.py's operator table must not drift —
    the hint claims these are unsupported locally, so the backend had better
    agree."""
    from trello_cli.backends import local as local_mod
    assert set(main._TRELLO_ONLY_OPS) == set(local_mod._TRELLO_ONLY_OPS)
    q = local_mod._parse_query_terms(
        " ".join(f"{op}:x" for op in main._TRELLO_ONLY_OPS))
    assert len(q.unsupported) == len(main._TRELLO_ONLY_OPS)
    # ...and each is ALSO kept as a literal term, so the query narrows rather
    # than silently widening.
    assert len(q.terms) == len(main._TRELLO_ONLY_OPS)


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
