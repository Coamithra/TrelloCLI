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
--board <name_or_id>   Board for this command, required (also: TRELLO_BOARD env var)
--backend <trello|local>  Data source (default: trello; also: TRELLO_BACKEND env var)
--local-root <path>    Local store folder (also: TRELLO_LOCAL_ROOT env var)
--json                 Emit raw JSON instead of formatted text (read commands)
```

There is no stored "active board" — pass `--board` (or set `TRELLO_BOARD`) on board-scoped
commands. The CLI keeps no shared session state so concurrent invocations never conflict.

### Global

```
trello configure <key> <token>     Save API credentials
trello boards                      List all boards
trello local init [path]           Set up the local file-backend root
trello board                       Show board info (needs --board)
trello board add <name> [desc]     Create a board (--no-default-lists)
trello labels                      Show board labels
trello members                     Show board members
trello activity [n]                Show recent activity
trello export [--to local] [--no-attachments]  Pull --board into the local file
                                   store (source backend = --backend, default
                                   trello; uploaded blobs downloaded by default)
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
trello list ls                     Show lists on the board
trello list add <name> [--top|--bottom|--pos <n>]  Create a new list
                                   (defaults to top, like `card add`)
trello list archive <list>         Archive a list
trello list rename <list> <name>   Rename a list
trello list pos <id> <pos>         Reorder list (top, bottom, number,
                                   'after <id>', 'before <id>')
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

## Local file backend

Besides Trello, the CLI can drive a self-hosted **file store** — JSON files on disk (e.g. in a
Dropbox-synced folder) — through the same commands and formatting. Select it per-invocation with
`--backend local` or `TRELLO_BACKEND=local`.

```bash
trello local init                         # root at ~/Dropbox/trello-cli (or: local init <path>)
trello --backend local board add "Home"   # prints the new board id
trello --backend local --board <id> card add "To Do" "Buy milk"
trello --backend local --board <id> card ls "To Do"
```

Layout: `<root>/<boardId>/{board.json, lists.json, labels.json, cards/<cardId>.json,
attachments/<cardId>/…, activity.log}`, with atomic writes (so a sync never sees a half-written
file), 24-hex ids, and float positions — identical in shape to Trello, so every command and `--json`
output works the same. **Every CLI command** now works on the local backend: boards, lists, cards,
labels, checklists, comments, attachments, members, and `card mine`, plus `activity` / `updates`
derived from the append-only `activity.log`.

Local specifics: labels, checklists, comments, and attachments live inline in the card JSON (a card
references labels by id; the full label is resolved from `labels.json`, so `label edit`/`delete`
reflect everywhere). Uploaded attachment blobs are copied under `attachments/<cardId>/`; URL
attachments just store the URL. Members are a single local user (your OS username), so `card mine`
returns every open card across your local boards.

### Pulling a Trello board into files

`trello --board <board> export` snapshots a board from the selected `--backend` (default Trello)
into the local file store — lists, cards (description, due, position, labels, closed state),
comments, checklists, and attachments — under `<local-root>/<boardId>/`. Source ids are
preserved, so re-running `export` is an idempotent refresh (cards deleted upstream are pruned from
the snapshot). Then browse it offline with `--backend local`, or render it in the web app:

```bash
trello --board "My Board" export                 # Trello -> local files (+ blobs)
trello --board "My Board" export --no-attachments  # metadata only, skip blob download
trello --backend local --board "My Board" list ls
trello --backend local serve                     # drag-drop kanban over the files
```

Uploaded-attachment **blobs are downloaded by default** into `<boardId>/attachments/<cardId>/`
and the stored URL is rewritten root-relative, so the snapshot is usable offline. Pass
`--no-attachments` to export metadata only (the blob then keeps its auth-required Trello URL).
Already-downloaded blobs are reused on re-export, and a per-blob download failure is non-fatal —
it warns and keeps the remote URL. URL attachments are already portable and exported as-is. Only
open lists are pulled (the API exposes open lists only).

## Web app

An optional local **web UI** — a drag-drop kanban that renders whichever backend you
select, served by FastAPI over the same `Backend` interface (no build step; vanilla JS +
SortableJS). Install the extra and launch:

```bash
pip install -e ".[web]"                 # or: pip install "trello-cli[web]"
trello --backend local serve            # or --backend trello; opens the browser
trello serve --port 8787 --host 127.0.0.1 --token <t> --no-browser
```

`serve` boots a local server and opens your browser. It binds **127.0.0.1 by default**
(local-only, no auth needed). A non-loopback `--host` opts into network exposure and is
**token-gated**: pass `--token <t>` or let `serve` mint one for you, and that token is then
required on every API request (the browser is opened on a `?token=…` URL automatically). Even
so, keep remote access behind a VPN or reverse proxy. In the UI:
pick a board from the dropdown, drag cards within/between columns and drag columns to
reorder (both write straight through the backend, using the same float-`pos` midpoint rule
as `card pos`), add a card from the composer at the bottom of a column, and click a card
for a read-only detail panel (description, due, labels, checklist, comments).

**Live refresh (local backend):** when serving a `--backend local` board, the page reloads itself
as the store changes on disk — a Dropbox sync from another machine, or another `--backend local`
CLI command — via a file-watch (`watchdog`) and a Server-Sent-Events stream. No polling, no manual
refresh; a reload that lands mid-drag is skipped (the next change re-syncs), so a drag is never
yanked out from under you. The Trello backend has no local files to watch, so its board doesn't
live-refresh.

### Remote access

On loopback `serve` runs with **no authentication** (it's local-only). The moment you bind a
non-loopback `--host`, the API is **token-gated**: supply `--token <t>` or `serve` auto-generates
one and prints it. The token must accompany every API request, either as `?token=<t>` (how the
browser is launched) or an `Authorization: Bearer <t>` header (handy for scripts/automation); the
static page shell loads without it but shows nothing until an API call succeeds. A bad or missing
token gets a `401`.

The token gates the port, but it is **not** TLS and **not** an identity system — so still front
remote access with something stronger:

- **Tailscale (recommended):** keep `serve` on `127.0.0.1` and reach it over your private tailnet —
  no port is published to the public internet.
- **Reverse proxy + auth:** run `serve` on loopback and front it with caddy/nginx terminating TLS
  and enforcing auth, proxying to `127.0.0.1:8787`.

Binding a non-loopback `--host` is safe-by-default (token required) but is best used *inside* one of
the above — the token alone publishes a read/write board, in cleartext, to anyone who has it.

## Updating

```bash
pip install --upgrade git+https://github.com/CoamIthra/TrelloCLI.git
```
