# TrelloCLI

Compact Trello CLI tool — wraps the Trello REST API with concise output formatting.

## Architecture

- `trello_cli/config.py` — credentials, active board, and per-invocation board override stored in `~/.trello-cli.json`
- `trello_cli/api.py` — thin httpx client over Trello REST API, requests only the fields each command needs
- `trello_cli/fmt.py` — compact table/detail formatting and small helpers (`short_id`, `truncate`, `due_str`, `label_str`, `is_image`, `size_str`, `print_json`)
- `trello_cli/main.py` — CLI entry point, noun-group dispatch (`card`, `list`, `label`, `checklist`, `comment`, `attachment`), name/ID prefix resolution

## Conventions

- **Noun-group dispatch** — `_dispatch(group, subcmds, args)` routes verbs within a group. Bare nouns (or nouns followed by a non-verb) fall back to `ls` if the group has one, so `trello list` ≡ `trello list ls`.
- **Resolvers** — every domain has a `_resolve_*` helper that accepts an ID, an ID prefix, or a case-insensitive name prefix, and raises `SystemExit` on miss/ambiguity.
- **Board scope** — `_require_board()` returns the active board ID, honoring `--board <name>` (parsed in `main()`) and the `TRELLO_BOARD` env var as overrides.
- **Output mode** — `--json` is stripped in `main()` and toggles `_JSON_MODE`; read commands branch on `_is_json()` to emit raw JSON via `print_json` instead of formatted tables.

## Install

```bash
pip install -e .          # local dev (editable)
pip install git+<url>     # from GitHub on another machine
```
