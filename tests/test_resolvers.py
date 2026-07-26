"""Area 2 — CLI resolvers, date parsing, filename safety, dispatch."""

from __future__ import annotations

import json

import pytest

from trello_cli import main
from tests.conftest import use_local_cli


@pytest.fixture
def cli(store_root):
    """A local store wired into the api facade, with one board addressable via
    the --board override. Returns (backend, board_id)."""
    be = use_local_cli(store_root)
    b = be.create_board("Board", default_lists=False)
    from trello_cli import config
    config.set_board_override(b["id"])
    return be, b["id"]


# ── _resolve_list exact-match tier (C2 fix) ──────────────────────────

def test_exact_name_beats_prefix(cli):
    be, bid = cli
    be.create_list(bid, "To Do")
    be.create_list(bid, "To Do (blocked)")
    todo = next(l for l in be.get_lists(bid) if l["name"] == "To Do")["id"]
    assert main._resolve_list(bid, "To Do") == todo


def test_exact_name_beats_id_prefix_shadowing(cli):
    be, bid = cli
    first = be.create_list(bid, "Primary")
    # Name a second list exactly after the first list's id prefix.
    shadow_name = first["id"][:4]
    shadow = be.create_list(bid, shadow_name)
    # Exact name must win over the id-prefix match against `first`.
    assert main._resolve_list(bid, shadow_name) == shadow["id"]


def test_ambiguous_exact_names_error(cli):
    be, bid = cli
    be.create_list(bid, "Dup")
    be.create_list(bid, "Dup")
    with pytest.raises(SystemExit):
        main._resolve_list(bid, "Dup")


def test_id_prefix_still_resolves(cli):
    be, bid = cli
    lst = be.create_list(bid, "Solo")
    assert main._resolve_list(bid, lst["id"][:8]) == lst["id"]
    assert main._resolve_list(bid, lst["id"]) == lst["id"]


def test_unknown_list_raises(cli):
    be, bid = cli
    be.create_list(bid, "Solo")
    with pytest.raises(SystemExit):
        main._resolve_list(bid, "nope-nothing-matches")


# ── _parse_due ────────────────────────────────────────────────────────

def test_parse_due_date_only_defaults_9am():
    out = main._parse_due("2026-05-01")
    assert out.startswith("2026-05-01T09:00")
    assert out.endswith("+00:00")


def test_parse_due_explicit_time_preserved():
    out = main._parse_due("2026-05-01T15:30")
    # The C1 fix: an explicit time-of-day is NOT clobbered to 09:00.
    assert out.startswith("2026-05-01T15:30")


def test_parse_due_aware_input_untouched():
    out = main._parse_due("2026-05-01T15:30:00+05:00")
    assert "+05:00" in out
    assert "15:30" in out


def test_parse_due_clear():
    assert main._parse_due("clear") is None


def test_parse_due_bad_raises():
    with pytest.raises(SystemExit):
        main._parse_due("not-a-date")


# ── _parse_since ──────────────────────────────────────────────────────

def test_parse_since_today_midnight():
    out = main._parse_since("today")
    assert "T00:00:00" in out


def test_parse_since_relative_lookback():
    # 3d is a valid look-back and returns an ISO timestamp in the past.
    out = main._parse_since("3d")
    assert out.endswith("+00:00")


def test_parse_since_iso_naive_gets_utc():
    out = main._parse_since("2026-06-01")
    assert out.startswith("2026-06-01T00:00:00+00:00")


# ── _safe_filename traversal cases ────────────────────────────────────

def test_safe_filename_strips_directories():
    assert main._safe_filename("../../etc/passwd", "fb") == "passwd"
    assert main._safe_filename("reports/q3.pdf", "fb") == "q3.pdf"
    assert main._safe_filename("a\\b\\c.txt", "fb") == "c.txt"


def test_safe_filename_leading_dots_and_fallback():
    assert main._safe_filename(".bashrc", "fb") == "bashrc"
    assert main._safe_filename("...", "fb") == "fb"
    assert main._safe_filename("", "fb") == "fb"


# ── cmd_grab: the Claim line ──────────────────────────────────────────

def _grab_board(cli):
    be, bid = cli
    be.create_list(bid, "To Do")
    be.create_list(bid, "Doing")
    lists = {l["name"]: l["id"] for l in be.get_lists(bid)}
    be.create_card(lists["To Do"], "Fix login bug")
    return be, bid


def test_grab_prints_claim_id_when_the_backend_supplies_one(cli, capsys, monkeypatch):
    """The Trello backend's claim comment is never retracted, so the caller has
    to be told which id is its own — that only helps if `grab` prints it."""
    _grab_board(cli)
    real = main.api.grab_top_card

    def _claimed(src, dst):
        got = real(src, dst)
        assert got is not None
        return {**got, "claimId": "3f9a1c2d"}

    monkeypatch.setattr(main.api, "grab_top_card", _claimed)

    main.cmd_grab([])

    out = capsys.readouterr().out
    assert "Grabbed: Fix login bug" in out
    assert "Claim: 3f9a1c2d" in out


def test_grab_json_carries_the_claim_id(cli, capsys, monkeypatch):
    """`--json` prints the card dict as-is, so the transient key rides along —
    an agent parsing JSON must not have to fall back to the formatted output."""
    _grab_board(cli)
    real = main.api.grab_top_card

    def _claimed(src, dst):
        got = real(src, dst)
        assert got is not None
        return {**got, "claimId": "3f9a1c2d"}

    monkeypatch.setattr(main.api, "grab_top_card", _claimed)
    monkeypatch.setattr(main, "_JSON_MODE", True)

    main.cmd_grab([])

    assert json.loads(capsys.readouterr().out)["claimId"] == "3f9a1c2d"


def test_grab_omits_claim_line_without_a_claim(cli, capsys):
    """Local grabs claim under the store lock and post no comment: there is no
    claim id, so no line about one."""
    _grab_board(cli)
    main.cmd_grab([])
    assert "Claim:" not in capsys.readouterr().out


# ── _dispatch ─────────────────────────────────────────────────────────

def test_dispatch_bare_noun_falls_back_to_ls():
    calls = []
    subcmds = {"ls": lambda a: calls.append(("ls", a))}
    main._dispatch("list", subcmds, [])
    assert calls == [("ls", [])]


def test_dispatch_unknown_verb_errors():
    subcmds = {"ls": lambda a: None, "add": lambda a: None}
    with pytest.raises(SystemExit) as ei:
        main._dispatch("list", subcmds, ["renmae", "x"])
    assert "Unknown list command" in str(ei.value)


def test_dispatch_ls_takes_args_consumes_positional():
    calls = []
    subcmds = {"ls": lambda a: calls.append(a)}
    main._dispatch("card", subcmds, ["Some List"], ls_takes_args=True)
    assert calls == [["Some List"]]


def test_dispatch_known_verb_dispatched():
    calls = []
    subcmds = {"ls": lambda a: None, "add": lambda a: calls.append(a)}
    main._dispatch("list", subcmds, ["add", "New"])
    assert calls == [["New"]]
