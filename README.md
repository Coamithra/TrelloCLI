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

Commands are organized into noun groups (`card`, `list`, `label`, `checklist`, `comment`, `attachment`). Bare nouns default to `ls`, so `trello list` ≡ `trello list ls` and `trello card <list>` ≡ `trello card ls <list>`.

### Global options

```
--board <name_or_id>   Override active board for this command
                       (also: TRELLO_BOARD env var)
--json                 Emit raw JSON instead of formatted text (read commands)
```

### Global

```
trello configure <key> <token>     Save API credentials
trello boards                      List all boards
trello use <board>                 Set active board (name prefix or ID)
trello board                       Show active board info
trello board add <name> [desc]     Create a board (--no-default-lists, --use)
trello labels                      Show board labels
trello members                     Show board members
trello activity [n]                Show recent activity
```

### Card

```
trello card show <id> [--no-comments]  Show card detail (comments by default)
trello card ls <list> [--with-comment] Show cards in a list
trello card add <list> <name> [desc]   Create a card
trello card move <id> <list>           Move a card to a list
trello card archive <id>               Archive a card
trello card unarchive <id>             Restore an archived card
trello card rename <id> <name>         Rename a card
trello card desc <id> <text>           Update card description
trello card due <id> <date>            Set/clear due date (ISO, 1d/2w/1m/1y,
                                       'today', 'tomorrow', 'clear')
trello card pos <id> <pos>             Reorder card (top, bottom, number,
                                       'after <id>', 'before <id>')
trello card mine                       Show cards assigned to me
```

### List

```
trello list ls                     Show lists on active board
trello list add <name>             Create a new list
trello list archive <list>         Archive a list
trello list rename <list> <name>   Rename a list
```

### Label

```
trello label ls                          Show board labels
trello label add <name> <color>          Create a board label
trello label edit <label> [name] [color] Update a label
trello label delete <label>              Delete a board label
trello label set <card> <label>          Add a label to a card
trello label unset <card> <label>        Remove a label from a card
```

### Checklist

```
trello checklist ls <card>                       List checklists on a card
trello checklist add <card> <name>               Create a checklist
trello checklist delete <card> <checklist>       Delete a checklist
trello checklist rename <card> <checklist> <new> Rename a checklist
trello checklist item add <card> <cl> <text>     Add an item
trello checklist item delete <card> <cl> <item>  Delete an item
trello checklist item rename <card> <cl> <item> <text>  Rename an item
trello checklist item check <card> <cl> <item>   Mark item complete
trello checklist item uncheck <card> <cl> <item> Mark item incomplete
```

### Comment

```
trello comment ls <card>                  Show card comments
trello comment add <card> <text>          Add a comment
trello comment edit <card> <id> <text>    Edit a comment
trello comment delete <card> <id>         Delete a comment
```

### Attachment

```
trello attachment ls <card>                      List attachments (images flagged IMG)
trello attachment add <card> <file_or_url> [name] Attach a local file or a URL
trello attachment view <card> [attachment]       Download image(s) to local paths and
                                                 print them (defaults to all images)
trello attachment open <card> <attachment>       Open an attachment (image in your
                                                 viewer; URL link in browser)
trello attachment download <card> <attachment> [dest]  Save an attachment to disk
trello attachment rm <card> <attachment>         Remove an attachment
```

`card show` lists a card's attachments and flags images, so you'll notice when there's something to look at.

`attachment view` is the one to reach for when something (a person, or an agent) needs to actually *see* an image: it downloads each image to a local cache and prints the file paths, one per line, ready to open or read. Uploaded images are fetched through the authenticated Trello endpoint; URL attachments are fetched directly.

Names accept case-insensitive prefix matches; IDs accept short prefixes.

## Updating

```bash
pip install --upgrade git+https://github.com/CoamIthra/TrelloCLI.git
```
