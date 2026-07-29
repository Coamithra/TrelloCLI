"""`local init` must not retarget the machine, and a wrong root must say so.

Card 81c0e2e6: `local init <scratch>` persisted `local_root` into the config,
which silently pointed every other `--backend local` invocation on the machine —
including sessions already running — at the scratch store, whose only symptom was
a bare `Board not found: <id>` for a board that had not moved.

These tests are hermetic via the autouse `_hermetic` fixture: `config.CONFIG_PATH`
points at a throwaway path, so nothing here can touch a real ~/.trello-cli.json.
"""

from __future__ import annotations

import json
import os

import pytest

from trello_cli import config, main
from tests.conftest import use_local_cli


# ── `local init` does not persist without --set-default ──────────────

def test_init_creates_the_folder_but_writes_no_config(tmp_path):
    root = tmp_path / "scratch"
    main.cmd_local(["init", str(root)])
    assert root.is_dir()
    assert not config.CONFIG_PATH.exists(), "local init must not touch the config"
    assert config.get_stored_local_root() is None


def test_init_leaves_an_existing_local_root_alone(tmp_path):
    config.set_local_root(str(tmp_path / "real"))
    main.cmd_local(["init", str(tmp_path / "scratch")])
    assert config.get_stored_local_root() == str(tmp_path / "real")


def test_init_output_shows_both_per_invocation_forms(tmp_path, capsys):
    root = tmp_path / "scratch"
    main.cmd_local(["init", str(root)])
    out = capsys.readouterr().out
    assert "--local-root" in out
    assert "TRELLO_LOCAL_ROOT" in out
    assert "--set-default" in out
    assert "Nothing was persisted" in out


# ── `local init --set-default` persists, loudly ──────────────────────

def test_set_default_persists_the_root(tmp_path, capsys):
    root = tmp_path / "store"
    main.cmd_local(["init", str(root), "--set-default"])
    assert root.is_dir()
    assert config.get_stored_local_root() == str(root)
    assert json.loads(config.CONFIG_PATH.read_text())["local_root"] == str(root)


def test_set_default_names_the_previous_value_and_the_undo(tmp_path, capsys):
    old = tmp_path / "old"
    config.set_local_root(str(old))
    capsys.readouterr()
    main.cmd_local(["init", str(tmp_path / "new"), "--set-default"])
    out = capsys.readouterr().out
    assert str(old) in out, "must show what it is replacing"
    assert "EVERY" in out, "must say the change is machine-wide"
    assert f"trello local init {old} --set-default" in out, "must give the undo"


def test_set_default_to_the_same_path_says_unchanged(tmp_path, capsys):
    root = tmp_path / "store"
    main.cmd_local(["init", str(root), "--set-default"])
    capsys.readouterr()
    main.cmd_local(["init", str(root), "--set-default"])
    assert "unchanged" in capsys.readouterr().out


def test_init_rejects_a_second_positional(tmp_path):
    with pytest.raises(SystemExit):
        main.cmd_local(["init", str(tmp_path / "a"), str(tmp_path / "b")])


# ── provenance ───────────────────────────────────────────────────────

def test_source_reports_the_flag(tmp_path):
    config.set_local_root_override(str(tmp_path))
    assert config.local_root_source() == "--local-root flag"


def test_source_reports_the_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("TRELLO_LOCAL_ROOT", str(tmp_path))
    assert config.local_root_source() == "TRELLO_LOCAL_ROOT env var"


def test_source_reports_the_config(tmp_path):
    config.set_local_root(str(tmp_path))
    assert config.local_root_source() == f"config {config.CONFIG_PATH}"


def test_source_reports_the_default():
    assert config.local_root_source() == "built-in default"
    assert config.get_local_root() == str(config.DEFAULT_LOCAL_ROOT)


def test_precedence_flag_beats_env_beats_config(tmp_path, monkeypatch):
    config.set_local_root(str(tmp_path / "cfg"))
    assert config.local_root_source() == f"config {config.CONFIG_PATH}"
    monkeypatch.setenv("TRELLO_LOCAL_ROOT", str(tmp_path / "env"))
    assert config.local_root_source() == "TRELLO_LOCAL_ROOT env var"
    config.set_local_root_override(str(tmp_path / "flag"))
    assert config.local_root_source() == "--local-root flag"
    assert config.get_local_root() == str(tmp_path / "flag")


# ── `local root` ─────────────────────────────────────────────────────

def test_local_root_shows_path_and_provenance(tmp_path, capsys):
    config.set_local_root(str(tmp_path / "store"))
    capsys.readouterr()
    main.cmd_local(["root"])
    out = capsys.readouterr().out
    assert str(tmp_path / "store") in out
    assert str(config.CONFIG_PATH) in out
    assert "NO — nothing there" in out  # the folder was never created


def test_local_root_json(tmp_path, capsys, monkeypatch):
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("TRELLO_LOCAL_ROOT", str(root))
    monkeypatch.setattr(main, "_JSON_MODE", True)
    main.cmd_local(["root"])
    data = json.loads(capsys.readouterr().out)
    assert data["root"] == str(root)
    assert data["source"] == "TRELLO_LOCAL_ROOT env var"
    assert data["stored"] is None
    assert data["exists"] is True


def test_local_root_is_read_only(tmp_path):
    with pytest.raises(SystemExit):
        main.cmd_local(["root", str(tmp_path)])


# ── the self-diagnosing "Board not found" ────────────────────────────

def test_board_not_found_names_the_store_and_its_source(store_root):
    be = use_local_cli(store_root)
    be.create_board("Roadmap")
    with pytest.raises(SystemExit) as e:
        main._resolve_board_ref("6a353ffc")
    msg = str(e.value)
    assert "Board not found: 6a353ffc" in msg
    assert store_root in msg
    assert "--local-root flag" in msg          # provenance
    assert "1 board(s): Roadmap" in msg        # what IS there
    assert "trello local root" in msg          # the recovery command


def test_board_not_found_reports_an_empty_store(store_root):
    use_local_cli(store_root)
    os.makedirs(store_root, exist_ok=True)
    with pytest.raises(SystemExit) as e:
        main._resolve_board_ref("6a353ffc")
    assert "holds no boards at all" in str(e.value)


def test_diagnosis_is_local_backend_only():
    config.set_backend_override("trello")
    assert main._local_root_diagnosis([]) == ""


def test_local_store_resolver_also_diagnoses(store_root):
    """`local gc` / `local rm` resolve against the store directly."""
    from trello_cli.backends.local import LocalBackend

    be = LocalBackend(store_root)
    be.create_board("Roadmap")
    with pytest.raises(SystemExit) as e:
        main._resolve_local_board(be, "6a353ffc")
    msg = str(e.value)
    assert store_root in msg
    assert "trello local root" in msg


def test_backend_load_board_names_the_store(store_root):
    from trello_cli.backends.local import LocalBackend

    be = LocalBackend(store_root)
    with pytest.raises(SystemExit) as e:
        be.get_board("6a353ffc61a1ba7c32c0ff72")
    assert store_root in str(e.value)


def test_local_store_resolver_lists_what_the_store_holds(store_root):
    """Same tail as the --board path — one text, not two that drift."""
    from trello_cli.backends.local import LocalBackend

    be = LocalBackend(store_root)
    be.create_board("Roadmap")
    with pytest.raises(SystemExit) as e:
        main._resolve_local_board(be, "6a353ffc")
    assert "1 board(s): Roadmap" in str(e.value)


def test_diagnosis_survives_a_nameless_board():
    """A board.json with a null name must not turn the error into a TypeError."""
    config.set_backend_override("local")
    assert "?" in main._local_root_diagnosis([{"name": None}])


# ── path handling ────────────────────────────────────────────────────

def test_set_default_stores_an_absolute_path(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main.cmd_local(["init", "scratch", "--set-default"])
    stored = config.get_stored_local_root()
    assert os.path.isabs(stored)
    assert stored == str(tmp_path / "scratch")


def test_printed_commands_quote_a_path_with_spaces(tmp_path, capsys):
    root = tmp_path / "my store"
    main.cmd_local(["init", str(root)])
    out = capsys.readouterr().out
    assert f'--local-root "{root}"' in out
    assert f'TRELLO_LOCAL_ROOT="{root}"' in out
    assert f'trello local init "{root}" --set-default' in out


def test_undo_line_quotes_the_previous_path(tmp_path, capsys):
    old = tmp_path / "old store"
    config.set_local_root(str(old))
    capsys.readouterr()
    main.cmd_local(["init", str(tmp_path / "new"), "--set-default"])
    assert f'trello local init "{old}" --set-default' in capsys.readouterr().out


def test_set_default_from_unset_explains_how_to_get_back(tmp_path, capsys):
    """The first --set-default has no earlier path to re-init to, so the only
    route back is the config key — say so rather than leaving a one-way door."""
    main.cmd_local(["init", str(tmp_path / "store"), "--set-default"])
    out = capsys.readouterr().out
    assert "local_root" in out
    assert str(config.CONFIG_PATH) in out


def test_init_rejects_an_empty_path(tmp_path):
    with pytest.raises(SystemExit) as e:
        main.cmd_local(["init", "   "])
    assert "Empty path" in str(e.value)


def test_init_on_a_file_path_is_a_clean_error(tmp_path):
    clash = tmp_path / "afile"
    clash.write_text("not a directory")
    with pytest.raises(SystemExit) as e:
        main.cmd_local(["init", str(clash / "store")])
    assert "Cannot create local store" in str(e.value)


# ── `boards` on an empty local store says where it looked ────────────

def test_boards_on_an_empty_local_store_names_the_store(store_root, capsys):
    use_local_cli(store_root)
    os.makedirs(store_root, exist_ok=True)
    main.cmd_boards([])
    out = capsys.readouterr().out
    assert store_root in out
    assert "trello local root" in out
    assert "ID" not in out, "a bare table header is the symptom, not the answer"


def test_boards_still_lists_a_populated_store(store_root, capsys):
    be = use_local_cli(store_root)
    be.create_board("Roadmap")
    main.cmd_boards([])
    out = capsys.readouterr().out
    assert "Roadmap" in out
    assert "Searched local store" not in out
