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
trello boards [--archived|--all]   List boards (open by default; --archived =
                                   only archived, --all = both with a State column)
trello local init [path]           Set up the local file-backend root
trello local gc [--apply]          Clean stale local data (orphaned blobs,
                                   temp cache; --activity-keep <n>, --cache-days
                                   <n>). Dry run unless --apply
trello local rm <board> --yes      Delete a local board folder + blobs (no undo)
trello board                       Show board info (needs --board)
trello board add <name> [desc]     Create a board (--no-default-lists)
trello board rename <new name>     Rename the --board board
trello board archive               Archive the --board board (soft delete; restorable)
trello board restore               Restore (unarchive) the --board board
trello labels                      Show board labels
trello members                     Show board members
trello activity [n]                Show recent activity
trello export [--to local] [--no-attachments]  Pull --board into the local file
                                   store (source backend = --backend, default
                                   trello; uploaded blobs downloaded by default)
trello export --to trello [--name <name>] [--no-attachments]  Push a local --board
                                   up to Trello as a new board (source = --backend
                                   local; create-new-each-time)
```

### Workflow

```
trello grab [--from "To Do"] [--to "Doing"]  Atomically claim the top card of
                                   a list and move it to another, returning the
                                   card it got you (--json for the full dict;
                                   exit 1 if there's nothing to grab)
```

Made for "tell several agents to grab the top To Do ticket" without them racing
onto the **same** card. On the **local** backend it's truly atomic — the move
runs under the store lock, so concurrent grabbers each get a distinct card. On
the **Trello** backend (no atomic primitive) it fakes the
[CONTRIBUTING.md](CONTRIBUTING.md) claim handshake instead: grab, post a claim
comment, wait ~10-30s, and let the earliest claim win (retrying the next card on
a loss) — so a `trello grab` blocks for that wait. Cross-machine Dropbox
concurrency is out of scope (OS locks don't cross machines).

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

### Cleaning up local data

`attachment rm` deletes its own blob, but a few paths can leave stale files behind — a card pruned by
re-`export`, a `local rm`'d board, the append-only `activity.log`, or the `attachment view/open`
temp cache. `trello local gc` sweeps them up:

```bash
trello local gc                          # dry run — report orphaned blobs + old cache, delete nothing
trello local gc --apply                  # actually delete them
trello local gc --board <id> --apply     # scope the store sweep to one board
trello local gc --activity-keep 500 --apply   # also trim each log to its newest 500 lines
trello local gc --cache-days 0 --apply         # also clear the whole temp cache (default: older than 7 days)
```

It removes attachment blob dirs whose card no longer exists and blob files a live card no longer
references, prunes the temp download cache by age, and — only when you pass `--activity-keep` — trims
the activity log (it's an audit trail, so retention is opt-in). Everything is a **dry run unless
`--apply`**, printing exactly what would go.

To remove a whole local board (folder, cards, blobs, log — there's no undo):

```bash
trello local rm <board>          # dry run — show what would be deleted
trello local rm <board> --yes    # delete it
```

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

### Pushing a local board up to Trello

`trello --backend local --board <board> export --to trello` does the reverse: it pushes a board
from the local file store **up to Trello as a brand-new board**, re-creating lists, cards
(description, due, due-complete, position, labels, archived state), comments, checklists (with
item completion), and attachments (uploaded blobs are re-uploaded; URL attachments re-linked).

```bash
trello --backend local --board "My Board" export --to trello                 # local -> a new Trello board
trello --backend local --board "My Board" export --to trello --name "Copy"   # override the new board's name
trello --backend local --board "My Board" export --to trello --no-attachments  # skip uploading blobs
```

Because Trello mints its own ids, ids **cannot** be preserved, so this is **create-new-each-time**:
every run creates a fresh board (it never updates a previously-pushed one) — the command prints the
new board's id and URL. A few things change in the cloud copy by necessity:

- **Comments** are re-posted as *you* (the API token's user) with a current timestamp — Trello has
  no way to set a comment's author or date — so each is prefixed with an `_originally <author>,
  <date>_` line to preserve the original attribution in the body.
- **Board members** are not mapped (the new board has different membership), so card member
  assignments are dropped.
- Only **open lists** are pushed (same as `--to local`); a card whose list wasn't exported is
  skipped with a warning. Per-attachment failures warn and continue.

The source must be the local store, so run it with `--backend local`. (A tracked re-sync that
updates an existing board in place — `export --to trello --into <board>` — is **designed but not
yet built**: see the `--into` section in `DESIGN.md` for the id-map sidecar, the reconcile
algorithm, conflict handling, and why the build is deferred pending a conflict-policy decision.)

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
as `card pos`), add a card from the composer at the bottom of a column, and click a card to
open an **editable** detail panel: rename the card, edit the description, set labels and a due
date, manage attachments (see below), and add comments (checklists render read-only).

**Attachments:** the detail panel's **📎 Attach** button opens a popover to either upload a file
from your computer or paste a URL. Attachments list below with image thumbnails, a name link,
and size; a `📎 N` badge also shows on the card face. Uploaded files are served back through a
small token-gated endpoint (a local blob, or a Trello-hosted upload fetched with your token);
external URL attachments link straight to their source. Remove one with the × on its row. This
mirrors the CLI's `attachment add <file_or_url>` — anything attached here is visible to
`attachment ls` and vice-versa.

**Starred quick-swap boards:** click the ★ toggle (top-right, just before the board dropdown)
to **star** the current board — it gets a one-click button in the top bar and drops out of the
dropdown; the dropdown holds the rest. Click a button to jump straight to that board (the active
one is highlighted). Stars are a **per-browser preference** (stored in `localStorage`), so they
persist across reloads on that machine but don't sync to other browsers. When the bar fills up
the buttons squish (truncating with an ellipsis) rather than overflowing.

**Managing boards:** the **⚙** button (top-right) opens a **Manage boards** panel. Under
*Active boards*, click a board's name to rename it inline, or **Archive** it — a soft delete that
hides the board but keeps all its files (it drops off the board picker and quick-swap bar). Under
*Archived*, **Restore** brings an archived board back, or **Delete** permanently removes it (folder,
cards, and blobs — no undo, behind a confirm; the same wipe as `trello local rm`). These map to the
CLI's `board rename` / `board archive` / `board restore` and `boards --archived`, so changes made
either way are visible in both. (Permanent delete is local-backend only.)

**Managing columns:** an **"Add another list"** affordance sits after the last column, and
each column header has a `⋯` menu with **Delete list** (an archive — the column and its
cards are hidden, not destroyed) and a **Sort by** section (Manual / Newest / Oldest / Name) in
that same menu.

**Persisted per-column auto-sort (local backend — beats Trello):** picking a sort other than
Manual doesn't just re-order the column once — it is **saved on the list**, and every new card
added to that column is auto-placed into its sorted slot (alphabetical, or by newest/oldest
activity). The setting survives reloads. Manually dragging a card into an auto-sorted column
**clears that column's sort back to Manual** (so your hand-placement isn't immediately
overridden by the next add). This is a **local-backend feature**: Trello's API has no per-list
sort field, so on a `--backend trello` board the Sort by menu is a no-op.

**Live refresh:** when serving a `--backend local` board, the page reloads itself
as the store changes on disk — a Dropbox sync from another machine, or another `--backend local`
CLI command — via a file-watch (`watchdog`) and a Server-Sent-Events stream. No polling, no manual
refresh; a reload that lands mid-drag is skipped (the next change re-syncs), so a drag is never
yanked out from under you. The Trello backend has no local files to watch, so it instead polls the viewed board's latest
activity every few seconds and reloads when it advances, surfacing edits made from other CLIs or
the Trello web app.

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
