# TrelloCLI

Compact Trello CLI tool — wraps the Trello REST API with concise output formatting.

## Management

- Trello board with tasks: 6a353ffc
- When implementing tickets from Trello, use CONTRIBUTING.md for workflow

## Architecture

- `trello_cli/config.py` — credentials, active board, and per-invocation board override stored in `~/.trello-cli.json`
- `trello_cli/api.py` — thin **facade**; each operation forwards to the active backend via `get_backend()`. `main.py`'s `api.<op>(...)` call sites stay unchanged
- `trello_cli/backends/` — pluggable data sources behind a common interface:
  - `base.py` — the `Backend` ABC: the ~40 operations the CLI needs, all returning Trello-shaped dicts
  - `trello.py` — `TrelloBackend`, the httpx client over the Trello REST API (requests only the fields each command needs)
  - `__init__.py` — `get_backend()` factory (cached singleton). Trello-only for now; backend *selection* + a local file store arrive in later phases (see DESIGN.md)
- `trello_cli/fmt.py` — compact table/detail formatting and small helpers (`short_id`, `truncate`, `due_str`, `label_str`, `is_image`, `size_str`, `print_json`); backend-agnostic (formats plain dicts)
- `trello_cli/main.py` — CLI entry point, noun-group dispatch (`card`, `list`, `label`, `checklist`, `comment`, `attachment`), name/ID prefix resolution

## Conventions

- **Noun-group dispatch** — `_dispatch(group, subcmds, args)` routes verbs within a group. Bare nouns (or nouns followed by a non-verb) fall back to `ls` if the group has one, so `trello list` ≡ `trello list ls`.
- **Resolvers** — every domain has a `_resolve_*` helper that accepts an ID, an ID prefix, or a case-insensitive name prefix, and raises `SystemExit` on miss/ambiguity.
- **Board scope** — `_require_board()` returns the active board ID, honoring `--board <name>` (parsed in `main()`) and the `TRELLO_BOARD` env var as overrides.
- **Output mode** — `--json` is stripped in `main()` and toggles `_JSON_MODE`; read commands branch on `_is_json()` to emit raw JSON via `print_json` instead of formatted tables.
- **Backend seam** — commands never touch a concrete backend or transport. They call `api.<op>(...)`, which forwards to `get_backend()`. Every backend returns the same Trello-shaped dicts (the keys `fmt.py` reads), so adding a backend means implementing the `Backend` ABC — no command or formatter changes.

## Install

```bash
pip install -e .          # local dev (editable)
pip install git+<url>     # from GitHub on another machine
```
