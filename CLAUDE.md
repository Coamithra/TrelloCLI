# TrelloCLI

Compact Trello CLI tool — wraps the Trello REST API with concise output formatting.

## Architecture

- `trello_cli/config.py` — credentials + active board state stored in `~/.trello-cli.json`
- `trello_cli/api.py` — thin httpx client over Trello REST API, requests only needed fields
- `trello_cli/fmt.py` — compact table/detail formatting
- `trello_cli/main.py` — CLI entry point, command dispatch, name/ID prefix resolution

## Install

```bash
pip install -e .          # local dev (editable)
pip install git+<url>     # from GitHub on another machine
```
