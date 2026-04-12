# Trello CLI

A compact Trello CLI that doesn't flood your context. Designed for use inside Claude Code via `! trello <command>`.

## Install on a new machine

```bash
pip install git+https://github.com/CoamIthra/TrelloCLI.git
```

Or with pipx (isolated from other Python projects):

```bash
pipx install git+https://github.com/CoamIthra/TrelloCLI.git
```

Then configure your credentials:

```bash
trello configure <api_key> <token>
```

Get your API key and token from https://trello.com/power-ups/admin.

## Usage

```
trello boards                    List all boards
trello use <board>               Set active board (name prefix or ID)
trello board                     Show active board info
trello lists                     Show lists on active board
trello cards <list>              Show cards in a list (name prefix or ID)
trello card <id>                 Show card details (ID prefix works)
trello add <list> <name> [desc]  Create a card
trello move <card_id> <list>     Move a card to a list
trello archive <card_id>         Archive a card
trello comment <card_id> <text>  Add a comment
trello comments <card_id>        Show card comments
trello my-cards                  Show cards assigned to me
trello labels                    Show board labels
trello label add <name> <color>  Create a board label
trello label edit <label> [name] [color]  Update a label
trello label delete <label>      Delete a board label
trello label set <card> <label>  Add a label to a card
trello label unset <card> <label> Remove a label from a card
trello members                   Show board members
trello activity [n]              Show recent activity
```

## Updating

```bash
pip install --upgrade git+https://github.com/CoamIthra/TrelloCLI.git
```
