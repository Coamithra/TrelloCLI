"""Area 7 — the affordances the AX corpus asked for.

Each test here corresponds to a wrong turn that cold agents actually took
against the previous CLI surface (see `ax/FINDINGS.md`). They are ordinary unit
tests, but their job is to keep an *ergonomic* fix from silently regressing —
nothing else in the suite would notice if `card --help` went back to being
"Unknown flag: --help".
"""

from __future__ import annotations

import pytest

from trello_cli import config, main
from tests.conftest import use_local_cli


@pytest.fixture
def board(store_root):
    be = use_local_cli(store_root)
    b = be.create_board("Roadmap")
    config.set_board_override(b["id"])
    lists = {ls["name"]: ls["id"] for ls in be.get_lists(b["id"])}
    be.create_card(lists["To Do"], "Fix login bug")
    be.create_card(lists["Doing"], "Migrate database")
    gone = be.create_card(lists["Done"], "Drop legacy endpoint")
    be.archive_card(gone["id"])
    return be, b["id"], lists


# ── `card ls` without a list: the board, not an error ────────────────

def test_card_ls_without_list_shows_whole_board(board, capsys):
    main.cmd_card(["ls"])
    out = capsys.readouterr().out
    assert "Fix login bug" in out and "Migrate database" in out
    assert "List" in out.splitlines()[0]  # the extra column that says where


def test_card_ls_bare_noun_shows_whole_board(board, capsys):
    main.cmd_card([])
    assert "Migrate database" in capsys.readouterr().out


def test_card_ls_archived_finds_the_archived_card(board, capsys):
    main.cmd_card(["ls", "--archived"])
    out = capsys.readouterr().out
    assert "Drop legacy endpoint" in out
    assert "Fix login bug" not in out


def test_card_ls_archived_scoped_to_one_list(board, capsys):
    _be, _bid, lists = board
    main.cmd_card(["ls", "Done", "--archived"])
    assert "Drop legacy endpoint" in capsys.readouterr().out
    main.cmd_card(["ls", "To Do", "--archived"])
    assert "Drop legacy endpoint" not in capsys.readouterr().out


def test_card_ls_named_list_still_scopes(board, capsys):
    main.cmd_card(["ls", "Doing"])
    out = capsys.readouterr().out
    assert "Migrate database" in out and "Fix login bug" not in out


# ── ...but a board-wide read must not flood the caller's context ─────

@pytest.fixture
def big_board(store_root):
    be = use_local_cli(store_root)
    b = be.create_board("Big")
    config.set_board_override(b["id"])
    lists = {ls["name"]: ls["id"] for ls in be.get_lists(b["id"])}
    for i in range(60):
        be.create_card(lists["To Do"], f"Card {i:03d}")
    for i in range(5):
        be.create_card(lists["Doing"], f"Doing {i}")
    return be, b["id"]


def test_board_wide_ls_is_capped(big_board, capsys):
    main.cmd_card(["ls"])
    out = capsys.readouterr().out
    assert out.count("Card ") + out.count("Doing ") <= main._BOARD_LS_LIMIT + 2
    assert "Showing 50 of 65" in out


def test_cap_says_where_the_rest_are(big_board, capsys):
    main.cmd_card(["ls"])
    out = capsys.readouterr().out
    assert "To Do 60" in out and "Doing 5" in out       # per-column counts
    assert 'trello card ls "<list>"' in out             # how to narrow
    assert "--limit 0" in out                           # how to get everything


def test_limit_zero_shows_everything(big_board, capsys):
    main.cmd_card(["ls", "--limit", "0"])
    out = capsys.readouterr().out
    assert "Showing" not in out
    assert "Card 059" in out


def test_limit_is_honoured(big_board, capsys):
    main.cmd_card(["ls", "--limit", "3"])
    out = capsys.readouterr().out
    assert "Showing 3 of 65" in out


def test_json_stays_valid_and_warns_on_stderr(big_board, capsys):
    import json

    main.main.__globals__["_JSON_MODE"] = True
    try:
        main.cmd_card(["ls"])
        cap = capsys.readouterr()
    finally:
        main.main.__globals__["_JSON_MODE"] = False
    assert len(json.loads(cap.out)) == 50      # stdout is still parseable JSON
    assert "Showing 50 of 65" in cap.err       # the notice went to stderr


def test_named_list_is_not_capped_by_default(big_board, capsys):
    main.cmd_card(["ls", "To Do"])
    out = capsys.readouterr().out
    assert "Card 059" in out and "Showing" not in out


def test_named_list_honours_an_explicit_limit(big_board, capsys):
    main.cmd_card(["ls", "To Do", "--limit", "2"])
    assert "Showing 2 of 60" in capsys.readouterr().out


def test_limit_rejects_nonsense(big_board):
    with pytest.raises(SystemExit) as e:
        main.cmd_card(["ls", "--limit", "lots"])
    assert "number" in str(e.value)


def test_with_comment_and_archived_are_refused_together(board):
    with pytest.raises(SystemExit) as e:
        main.cmd_card(["ls", "--with-comment", "--archived"])
    assert "cannot be combined" in str(e.value)


# ── per-group help ───────────────────────────────────────────────────

@pytest.mark.parametrize("group,cmd", [
    ("card", main.cmd_card),
    ("list", main.cmd_list),
    ("label", main.cmd_label),
    ("comment", main.cmd_comment),
    ("checklist", main.cmd_checklist),
    ("attachment", main.cmd_attachment),
    ("board", main.cmd_board),
])
def test_group_help_answers_instead_of_erroring(group, cmd, capsys):
    cmd(["--help"])
    out = capsys.readouterr().out
    assert group in out
    assert "Unknown" not in out


def test_group_help_reaches_the_verbs_of_that_group_only(capsys):
    main.cmd_list(["--help"])
    out = capsys.readouterr().out
    assert "list add" in out
    # No *command line* from another group (a cross-reference inside a
    # description is fine — `list add` explicitly mentions `card add`).
    described = [
        ln.strip().split()[0] for ln in out.splitlines()
        if ln.startswith("  ") and not ln.startswith("   ") and ln.strip()
    ]
    assert set(described) == {"list"}


def test_card_help_points_at_grab(capsys):
    main.cmd_card(["--help"])
    assert "grab" in capsys.readouterr().out


def test_help_flag_after_a_verb_also_helps(capsys):
    main.cmd_card(["ls", "--help"])
    assert "card ls" in capsys.readouterr().out


# ── unknown verbs say where the thing actually lives ─────────────────

def test_card_comment_points_at_the_comment_group(board):
    with pytest.raises(SystemExit) as e:
        main.cmd_card(["comment", "abc123", "hello"])
    msg = str(e.value)
    assert "trello comment add" in msg
    # ...and must NOT have fallen through to `card ls` and blamed a list.
    assert "List not found" not in msg


def test_board_list_points_at_boards(board):
    with pytest.raises(SystemExit) as e:
        main.cmd_board(["list"])
    assert "trello boards" in str(e.value)


def test_list_cards_points_at_card_ls(board):
    with pytest.raises(SystemExit) as e:
        main.cmd_list(["cards"])
    assert "trello card ls" in str(e.value)


def test_unknown_verb_still_allows_a_list_named_like_one(board):
    """The escape hatch the verb-guard needs: an explicit `ls` always wins."""
    with pytest.raises(SystemExit) as e:
        main.cmd_card(["archive"])  # no card id — reads as the verb it is
    assert "Usage: trello card archive" in str(e.value)


def test_list_name_that_is_not_a_verb_still_falls_through(board, capsys):
    main.cmd_card(["Doing"])
    assert "Migrate database" in capsys.readouterr().out


# ── flags and positions ──────────────────────────────────────────────

def test_unknown_flag_names_the_positional_form(board):
    with pytest.raises(SystemExit) as e:
        main.cmd_card(["ls", "--list", "To Do"])
    assert "positional" in str(e.value)


def test_quoted_relative_position_is_accepted(board, capsys):
    be, bid, lists = board
    a = be.create_card(lists["To Do"], "A")
    b = be.create_card(lists["To Do"], "B")
    # `card pos <id> "after <id>"` — exactly how the help text quotes it.
    main.cmd_card(["pos", b["id"], f"after {a['id']}"])
    order = [c["name"] for c in sorted(
        be.get_cards_in_list(lists["To Do"]), key=lambda c: c["pos"])]
    assert order.index("B") == order.index("A") + 1


def test_split_relative_pos_leaves_other_forms_alone():
    assert main._split_relative_pos(["id", "top"]) == ["id", "top"]
    assert main._split_relative_pos(["id", "after", "x"]) == ["id", "after", "x"]
    assert main._split_relative_pos(["id", "12.5"]) == ["id", "12.5"]


def test_board_as_a_positional_says_where_it_goes(board):
    config.set_board_override(None)
    with pytest.raises(SystemExit) as e:
        main.cmd_board(["show", "Roadmap"])
    assert "--board" in str(e.value)
