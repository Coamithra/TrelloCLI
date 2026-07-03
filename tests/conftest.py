"""Shared fixtures for the TrelloCLI test suite.

Every test is hermetic: no real `~/.trello-cli.json` is ever read or written
(config.CONFIG_PATH is redirected to a throwaway path that does not exist), no
network is touched, and no `~/Dropbox` store is used. Backends run against a
per-test `tmp_path` file store.
"""

from __future__ import annotations

import pytest

from trello_cli import config
from trello_cli.backends import get_backend
from trello_cli.backends.local import LocalBackend


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    """Neutralize the on-disk config and all ambient selection state.

    - Point config.CONFIG_PATH at a non-existent file so `_load()` returns {}
      and nothing is ever read from / written to the developer's real config.
    - Drop any TRELLO_* env vars the host shell might carry.
    - Reset the per-invocation override module globals.
    - Clear the cached `get_backend()` singleton before and after each test so a
      backend/board selection never leaks between tests.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(config, "CONFIG_PATH", fake_home / ".trello-cli.json")
    monkeypatch.setattr(config, "DEFAULT_LOCAL_ROOT", fake_home / "Dropbox" / "trello-cli")
    monkeypatch.setenv("HOME", str(fake_home))
    for var in ("TRELLO_API_KEY", "TRELLO_TOKEN", "TRELLO_BOARD",
                "TRELLO_BACKEND", "TRELLO_LOCAL_ROOT",
                "TRELLO_SERVER", "TRELLO_SERVER_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    config.set_board_override.__globals__["_board_override"] = None
    config._board_override = None
    config._backend_override = None
    config._local_root_override = None
    config._server_override = None

    get_backend.cache_clear()
    yield
    get_backend.cache_clear()
    config._board_override = None
    config._backend_override = None
    config._local_root_override = None
    config._server_override = None


@pytest.fixture
def store_root(tmp_path):
    """A fresh file-store root path (a str, as LocalBackend expects)."""
    return str(tmp_path / "store")


@pytest.fixture
def backend(store_root):
    """A LocalBackend on an empty tmp store."""
    return LocalBackend(store_root)


@pytest.fixture
def board(backend):
    """A LocalBackend with one board (default To Do / Doing / Done lists).

    Returns (backend, board_id, lists) where lists is the open-list list.
    """
    b = backend.create_board("Test Board")
    bid = b["id"]
    lists = backend.get_lists(bid)
    return backend, bid, lists


def use_local_cli(root):
    """Point the `api` facade / `main` resolvers at a local store rooted at
    `root`, returning a LocalBackend on the same root. For CLI-level tests that
    exercise `main._resolve_*` / `api.*` through `get_backend()`."""
    config.set_backend_override("local")
    config.set_local_root_override(root)
    get_backend.cache_clear()
    return LocalBackend(root)
