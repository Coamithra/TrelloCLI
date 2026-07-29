"""Trello CLI - compact Trello interface that doesn't flood your context."""

from __future__ import annotations

import difflib
import os
import re
import subprocess
import sys
import tempfile
import time
import webbrowser
from datetime import datetime, timedelta, timezone

# Force UTF-8 output on Windows (avoids cp1252 encoding errors with non-ASCII Trello data)
if sys.platform == "win32":
    for stream in ("stdout", "stderr"):
        s = getattr(sys, stream)
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")

from . import api, config, magnet
from .fmt import (
    due_str,
    is_image,
    label_str,
    print_card_detail,
    print_json,
    print_table,
    short_id,
    size_str,
    truncate,
)

_JSON_MODE = False


def _is_json() -> bool:
    return _JSON_MODE

USAGE = """\
Usage: trello [--board <name_or_id>] [--backend <trello|local|http>] [--json] <command> [args]

Global options:
  --board <name_or_id>          Board for this command (required; no active board)
                                (also: TRELLO_BOARD env var)
  --backend <trello|local|http> Data source for this command (default: trello;
                                http = a hosted trellno server, see configure-http)
                                (also: TRELLO_BACKEND env var)
  --local-root <path>           Local-backend store folder for this command
                                (also: TRELLO_LOCAL_ROOT env var; inspect with
                                `local root`, persist with
                                `local init <path> --set-default`)
  --server <url>                Trellno server URL for --backend http
                                (also: TRELLO_SERVER env var; token via
                                TRELLO_SERVER_TOKEN; persist with configure-http)
  --json                        Emit raw JSON instead of formatted text
                                (read commands; some mutators also echo JSON)

Tip: bare nouns default to `ls` — e.g. `trello list` ≡ `trello list ls`,
     `trello card <list>` ≡ `trello card ls <list>`.

Global:
  configure <key> <token>       Save API credentials
  configure-http <url> [<tok>]  Save a hosted trellno server (its `serve --token`
                                value) for --backend http; omit the token to
                                keep a previously saved one
  boards [<query>]              List boards (open by default; --archived shows
        [--archived|--all]      only archived, --all shows both with state).
                                A query filters by name substring or ID prefix:
                                `trello boards roadmap`
  local init [path]             Create a local file-backend store folder.
       [--set-default]          Changes NO global setting unless --set-default,
                                which persists it as this machine's default for
                                EVERY --backend local invocation (other running
                                sessions included). For a scratch store, skip the
                                flag and pass --local-root <path> per command
  local root                    Show which local store is in use and who chose it
                                (flag / env / config / default) — read-only
  local gc [--apply]            Clean stale local data: orphaned attachment
                                blobs + temp download cache (--cache-days <n>,
                                default 7; --activity-keep <n> trims the log).
                                Dry run unless --apply. Scope with --board
  local rm <board> --yes        Delete a local board folder + blobs (no undo;
                                dry run unless --yes)
  board                         Show board info (needs --board)
  board add <name> [desc]       Create a new board (--no-default-lists)
  board rename <new name>       Rename the --board board
  board archive                 Archive the --board board (soft delete; restorable)
  board restore                 Restore (unarchive) the --board board
  board link [--as uri|cmd]     Print the board's magnet link (see `card link`)
             [--short]
  open <magnet>                 Show whatever a magnet link addresses. The
                                magnet carries its own board and backend, so no
                                flags are needed — and it works anywhere a card
                                id does: `trello card comment <magnet> "done"`
  labels                        Show board labels
  members                       Show board members
  activity [n]                  Show recent activity
  updates <since> [type ...]    Show all updates/comments since a date
                                (ISO 2026-06-01, relative 6h/3d/2w/1m/1y,
                                'today', 'yesterday'; optional action-type
                                filter, e.g. commentCard updateCard)

Find:
  search <query>                Find cards by text on the --board board (`find`
        [--list <list>]         works too). Searches names, descriptions,
        [--all] [--partial]     comments and checklists. Terms are AND-ed;
        [--substring]           `-word` excludes. --list scopes to one column,
                                --all includes archived cards.
                                MATCHING: whole words by default; --partial also
                                matches word prefixes (scroll -> scrollbar);
                                --substring matches mid-word (crollba ->
                                scrollbar) and is LOCAL-BACKEND ONLY, because
                                Trello's search is a word index.
                                Trello's operators (due:, label:, is:, has:,
                                sort:, name:, description:, comment:, checklist:)
                                work on both backends; created:/member:/board:
                                are Trello-only and are literal text locally.

Workflow:
  grab [--from "To Do"]         Atomically claim the top card of a list and move
       [--to "Doing"]           it to another, returning the card it got you
                                (--json for the full dict). Use it when several
                                agents grab the top ticket at once: no two get
                                the same card. Exit 1 if there's nothing to grab.
                                Local: truly atomic (store lock). Trello: faked
                                with the claim-comment handshake (a ~10-30s wait),
                                and prints a `Claim:` id — the claim comment it
                                leaves on the card, so you can tell it from a
                                rival's.

Card:
  card show <card_id> [--no-comments]  Show card details (comments included by default)
  card ls [<list>] [--with-comment]    Show cards. With a list: that column.
          [--archived] [--limit <n>]   Without one: every card on the board,
                                       with a List column, capped at 50 to keep
                                       big boards from flooding your context
                                       (the footer says what was left out and
                                       which column to ask for; --limit 0 shows
                                       all). --archived shows archived cards
                                       instead of open ones (that's how you
                                       find a card to unarchive).
                                       Looking for a card by keyword rather than
                                       listing a column? Use `search <query>`.
  card add <list> <name> [desc] Create a card at the top (--bottom to append)
  card move <card_id> <list>    Move a card to a list (to claim the *top* card of
                                a list when other agents are racing you, use
                                `grab` instead — it can't hand out duplicates)
  card archive <card_id>        Archive a card
  card unarchive <card_id>      Restore an archived card (find it with
                                `card ls --archived`)
  card rename <card_id> <name>  Rename a card
  card desc <card_id> <text>    Update card description
  card due <card_id> <date>     Set card due date (ISO 2026-05-01,
                                relative 1d/2w/1m/1y, 'tomorrow',
                                'today', or 'clear' to remove)
  card pos <card_id> <pos>      Reorder card. Pos: top, bottom, a number,
                                'after <other_card_id>', or
                                'before <other_card_id>'
  card mine                     Show cards assigned to me
  card link <card_id>           Print the card's magnet link: one token that
       [--as uri|cmd]           carries board + backend + card id, so handing a
       [--short]                card to another agent is one string instead of
                                three flags. --as cmd prints a ready-to-run
                                command line instead; --short abbreviates the
                                ids. `card show` prints it as a `Link:` line.

List:
  list ls                       Show lists on the board
  list add <name> [--top|--bottom|--pos <n>]  Create a new list
                                (defaults to top, like `card add`)
  list archive <list>           Archive a list
  list rename <list> <new_name> Rename a list
  list pos <list_id> <pos>      Reorder list. Pos: top, bottom, a number,
                                'after <other_list_id>', or
                                'before <other_list_id>'

Label:
  label ls                              Show board labels
  label add <name> [color]              Create a board label (a single argument
                                        is taken as the name; color optional)
  label edit <label> [name] [color]     Update a label
  label delete <label>                  Delete a board label
  label set <card_id> <label>           Add a label to a card
  label unset <card_id> <label>         Remove a label from a card

Checklist:
  checklist ls <card_id>                              List checklists on a card
  checklist add <card_id> <name>                      Create a checklist
  checklist delete <card_id> <checklist>              Delete a checklist
  checklist rename <card_id> <checklist> <name>       Rename a checklist
  checklist item add <card_id> <checklist> <text>     Add an item
  checklist item delete <card_id> <checklist> <item>  Delete an item
  checklist item rename <card_id> <cl> <item> <text>  Rename an item
  checklist item check <card_id> <checklist> <item>   Mark item complete
  checklist item uncheck <card_id> <checklist> <item> Mark item incomplete

Comment:
  comment add <card_id> <text>              Add a comment
  comment ls <card_id>                      Show card comments
  comment edit <card_id> <comment_id> <text> Edit a comment
  comment delete <card_id> <comment_id>      Delete a comment

Attachment:
  attachment ls <card_id>                       List attachments (images flagged IMG)
  attachment add <card_id> <file_or_url> [name] Attach a local file or a URL
  attachment view <card_id> [attachment]        Download image(s) to local paths and
                                                print them (defaults to all images;
                                                ready to open/read)
  attachment open <card_id> <attachment>        Open an attachment (image in your
                                                viewer; URL link in browser)
  attachment download <card_id> <attachment> [dest]  Save an attachment to disk
  attachment rm <card_id> <attachment>          Remove an attachment

Data:
  export [--to local]           Pull the --board board (from --backend, default
         [--no-attachments]     trello) into the local file store, preserving ids.
                                Uploaded attachment blobs are downloaded by default
                                (--no-attachments skips). Browse it with --backend
                                local, or `serve` it.
  export --to trello            Push a local --board up to Trello as a brand-new
         [--name <name>]        board (source must be --backend local). Re-creates
         [--no-attachments]     lists/labels/cards/comments/checklists/attachments
                                under fresh ids; --name overrides the board name.
                                Create-new-each-time: re-running makes another board.

Web:
  serve [--port 8787] [--host 127.0.0.1] [--token <t>] [--no-browser]
        [--allow-host <h1,h2>]  Launch the drag-drop kanban web app for the
                                selected backend (pip install trello-cli[web]).
                                Binds 127.0.0.1 by default (local only). Live-
                                refreshes a local board as its files change.
                                Hosted (see deploy/): bind loopback behind a
                                reverse proxy with --token and --allow-host
                                <public domain>; point other machines at it
                                with configure-http + --backend http.
"""


# ── Helpers ──────────────────────────────────────────────────────────


def _reject_flag_value(ref: str, what: str) -> None:
    """Refuse a `--flag` where a value belongs, with a message that says why.

    Commands that take free text can't use `_parse_flags` (the text may itself
    start with `--`), so an invented flag used to sail through as a positional
    and surface as nonsense three layers down: `comment add --card "Migrate
    database"` reported "Card not found with prefix: --card"."""
    if ref.startswith("--"):
        hint = _FLAG_HINTS.get(ref)
        raise SystemExit(
            f"Expected a {what}, got the flag {ref}. "
            f"This CLI takes values positionally."
            + (f"\n{hint}" if hint else "")
        )


def _free_text(args: list[str], what: str) -> str:
    """Join `args` into a free-text value, refusing an invented `--flag` in it.

    The same hole as `_reject_flag_value`, one step further along: a command
    whose value is *text* can't run its arguments through `_parse_flags`, so
    nothing stopped a made-up flag from becoming part of the text. That is how

        trello label add --card "Add dark mode" --label "feature"

    created a label named `--card Add dark mode --label feature` and exited 0 —
    a wrong write reported as a success, which is the one outcome an agent
    caller can't see. Text meant to be one value should arrive as one quoted
    argument, so a bare `--token` here is a flag; a literal one is still
    reachable after a `--` separator."""
    if args and args[0] == "--":  # everything after is literal
        return " ".join(args[1:])
    for a in args:
        if a.startswith("--"):
            hint = _FLAG_HINTS.get(a)
            raise SystemExit(
                f"Expected {what}, got the flag {a}. This CLI takes values "
                f"positionally — quote the text as one argument."
                + (f"\n{hint}" if hint else "")
                + "\nIf the text really does start with dashes, put it after "
                  "a bare `--`."
            )
    return " ".join(args)


def _resolve_board_ref(ref: str) -> str:
    """Resolve a board name or ID to a board ID (for --board / TRELLO_BOARD).

    Includes archived boards so an archived board can still be addressed (e.g. to
    `board restore` or `board show` it) — `get_boards()` alone hides them.

    A board magnet resolves to the board it names. `main()` normally converts a
    magnet in argv to a plain id before we get here, so this branch is what
    makes a magnet work in the one place it can't reach: `TRELLO_BOARD` (which
    then supplies the board only — the backend still comes from the env/flag)."""
    if magnet.is_magnet(ref):
        mag = magnet.parse(ref)
        if mag["type"] != "board":
            raise SystemExit(
                f"That is a {mag['type']} magnet, and a board is what's wanted here. "
                f"A {mag['type']} magnet already carries its board, so pass it "
                f"where the {mag['type']} goes and drop --board entirely."
            )
        return mag["board"]
    _reject_flag_value(ref, "board name or ID")
    boards = api.get_boards(include_closed=True)
    # Exact ID match
    for b in boards:
        if b["id"] == ref or short_id(b["id"]) == ref:
            return b["id"]
    lower = ref.lower()
    # Exact name match (case-insensitive) — wins over a mere prefix
    exact = [b for b in boards if b["name"].lower() == lower]
    if len(exact) == 1:
        return exact[0]["id"]
    if len(exact) > 1:
        names = ", ".join(m["name"] for m in exact)
        raise SystemExit(f"Ambiguous board name '{ref}'. Matches: {names}")
    # Name prefix match (case-insensitive)
    matches = [b for b in boards if b["name"].lower().startswith(lower)]
    if len(matches) == 1:
        return matches[0]["id"]
    if len(matches) > 1:
        names = ", ".join(m["name"] for m in matches)
        raise SystemExit(f"Ambiguous board name '{ref}'. Matches: {names}")
    raise SystemExit(f"Board not found: {ref}" + _local_root_diagnosis(boards))


def _local_root_diagnosis(boards: list[dict] | None = None) -> str:
    """The tail of a local-backend "not found" error: which store was searched,
    who chose it, and what is actually in it. Empty on other backends.

    A board that "vanished" is nearly always a retargeted `local_root` (see the
    `local init --set-default` warning) — and the bare message gave a cold agent
    no way to discover that, since the store path appears nowhere in the CLI's
    normal output."""
    if config.get_backend_name() != "local":
        return ""
    lines = [
        f"\nSearched local store: {config.get_local_root()}",
        f"  (local_root from {config.local_root_source()})",
    ]
    if boards is not None:
        if boards:
            names = ", ".join(b.get("name", "?") for b in boards[:5])
            more = ", …" if len(boards) > 5 else ""
            lines.append(f"  That store holds {len(boards)} board(s): {names}{more}")
        else:
            lines.append("  That store holds no boards at all.")
    lines.append(
        "Wrong store? Run `trello local root` to see where that path came from; "
        "override\nper-command with --local-root <path> or TRELLO_LOCAL_ROOT."
    )
    return "\n".join(lines)


def _require_board() -> str:
    override = config.get_board_override()
    if override:
        return _resolve_board_ref(override)
    raise SystemExit(
        "No board specified. Pass --board <name_or_id> or set TRELLO_BOARD."
    )


def _resolve_list(board_id: str, name_or_id: str) -> str:
    """Resolve a list name (case-insensitive prefix) or ID prefix."""
    _reject_flag_value(name_or_id, "list name or ID")
    lists = api.get_lists(board_id)
    # Exact ID
    for lst in lists:
        if lst["id"] == name_or_id:
            return lst["id"]
    lower = name_or_id.lower()
    # Exact name (case-insensitive) — beats both id-prefix and name-prefix
    exact = [lst for lst in lists if lst["name"].lower() == lower]
    if len(exact) == 1:
        return exact[0]["id"]
    if len(exact) > 1:
        names = ", ".join(m["name"] for m in exact)
        raise SystemExit(f"Ambiguous list name '{name_or_id}'. Matches: {names}")
    # ID prefix
    id_matches = [lst for lst in lists if lst["id"].startswith(name_or_id)]
    if len(id_matches) == 1:
        return id_matches[0]["id"]
    # Name prefix (case-insensitive)
    name_matches = [lst for lst in lists if lst["name"].lower().startswith(lower)]
    if len(name_matches) == 1:
        return name_matches[0]["id"]
    if len(name_matches) > 1:
        names = ", ".join(m["name"] for m in name_matches)
        raise SystemExit(f"Ambiguous list name '{name_or_id}'. Matches: {names}")
    if len(id_matches) > 1:
        ids = ", ".join(short_id(m["id"]) for m in id_matches)
        raise SystemExit(f"Ambiguous list ID prefix '{name_or_id}'. Matches: {ids}")
    raise SystemExit(f"List not found: {name_or_id}")


def _resolve_card(card_id_prefix: str, include_closed: bool = False) -> str:
    """Resolve a card ID prefix to a full card ID by searching the active board.

    A card magnet is accepted anywhere a card ID is: `main()` has already seeded
    the board and backend from it, so all that's left here is to unwrap the id
    and fall through to the ordinary resolution — which is the point, because a
    stale magnet then still gets the good "Card not found on this board" rather
    than a blind cross-board write."""
    if magnet.is_magnet(card_id_prefix):
        mag = magnet.parse(card_id_prefix)
        if mag["type"] != "card":
            raise SystemExit(
                f"That is a {mag['type']} magnet, and a card is what's wanted here. "
                f"Get a card's magnet with: trello card link <card_id>"
            )
        card_id_prefix = mag["id"]
    _reject_flag_value(card_id_prefix, "card ID")
    # A full 24-char ID: validate it belongs to the current board (when one is
    # set) instead of trusting it blindly — a foreign/deleted id then gets a clean
    # "Card not found" rather than a cross-board mutation or a raw 404 traceback.
    # With no board context we can't check, so trust it (the backend errors if bad).
    if len(card_id_prefix) == 24:
        if not config.get_board_override():
            return card_id_prefix
        board_id = _require_board()
        if any(c["id"] == card_id_prefix for c in api.get_board_cards(board_id)):
            return card_id_prefix
        if include_closed and any(
            c["id"] == card_id_prefix
            for c in api.get_board_cards(board_id, card_filter="closed")
        ):
            return card_id_prefix
        raise SystemExit(f"Card not found on this board: {card_id_prefix}")
    board_id = _require_board()
    cards = api.get_board_cards(board_id)
    matches = [c for c in cards if c["id"].startswith(card_id_prefix)]
    if len(matches) == 1:
        return matches[0]["id"]
    if len(matches) > 1:
        names = ", ".join(f"{short_id(c['id'])}={c['name']}" for c in matches[:5])
        raise SystemExit(f"Ambiguous card ID prefix '{card_id_prefix}'. Matches: {names}")
    # Fall back to closed cards if requested
    if include_closed:
        closed = api.get_board_cards(board_id, card_filter="closed")
        matches = [c for c in closed if c["id"].startswith(card_id_prefix)]
        if len(matches) == 1:
            return matches[0]["id"]
        if len(matches) > 1:
            names = ", ".join(f"{short_id(c['id'])}={c['name']}" for c in matches[:5])
            raise SystemExit(f"Ambiguous card ID prefix '{card_id_prefix}'. Matches: {names}")
    raise SystemExit(f"Card not found with prefix: {card_id_prefix}")


def _resolve_comment(card_id: str, comment_id_prefix: str) -> str:
    """Resolve a comment (action) ID prefix to a full action ID."""
    if len(comment_id_prefix) == 24:
        return comment_id_prefix
    limit = 1000
    comments = api.get_comments(card_id, limit=limit)
    matches = [c for c in comments if c["id"].startswith(comment_id_prefix)]
    if len(matches) == 1:
        return matches[0]["id"]
    if len(matches) > 1:
        ids = ", ".join(short_id(c["id"]) for c in matches[:5])
        raise SystemExit(f"Ambiguous comment ID prefix '{comment_id_prefix}'. Matches: {ids}")
    if len(comments) >= limit:
        print(f"  warning: searched only the newest {limit} comments; older ones "
              "were not checked", file=sys.stderr)
    raise SystemExit(f"Comment not found with prefix: {comment_id_prefix}")


def _resolve_checklist(card_id: str, name_or_id: str) -> str:
    """Resolve a checklist name (case-insensitive prefix) or ID prefix."""
    _reject_flag_value(name_or_id, "checklist name or ID")
    checklists = api.get_checklists(card_id)
    # Exact ID
    for cl in checklists:
        if cl["id"] == name_or_id:
            return cl["id"]
    lower = name_or_id.lower()
    # Exact name (case-insensitive)
    exact = [cl for cl in checklists if cl["name"].lower() == lower]
    if len(exact) == 1:
        return exact[0]["id"]
    if len(exact) > 1:
        names = ", ".join(m["name"] for m in exact)
        raise SystemExit(f"Ambiguous checklist '{name_or_id}'. Matches: {names}")
    # ID prefix
    id_matches = [cl for cl in checklists if cl["id"].startswith(name_or_id)]
    if len(id_matches) == 1:
        return id_matches[0]["id"]
    # Name prefix (case-insensitive)
    name_matches = [cl for cl in checklists if cl["name"].lower().startswith(lower)]
    if len(name_matches) == 1:
        return name_matches[0]["id"]
    if len(name_matches) > 1:
        names = ", ".join(m["name"] for m in name_matches)
        raise SystemExit(f"Ambiguous checklist '{name_or_id}'. Matches: {names}")
    if len(id_matches) > 1:
        ids = ", ".join(short_id(m["id"]) for m in id_matches)
        raise SystemExit(f"Ambiguous checklist ID prefix '{name_or_id}'. Matches: {ids}")
    raise SystemExit(f"Checklist not found: {name_or_id}")


def _resolve_checkitem(card_id: str, checklist_id: str, name_or_id: str) -> str:
    """Resolve a check item name (case-insensitive prefix) or ID prefix."""
    checklists = api.get_checklists(card_id)
    items = []
    for cl in checklists:
        if cl["id"] == checklist_id:
            items = cl.get("checkItems", [])
            break
    # Exact ID
    for it in items:
        if it["id"] == name_or_id:
            return it["id"]
    lower = name_or_id.lower()
    # Exact name (case-insensitive)
    exact = [it for it in items if it["name"].lower() == lower]
    if len(exact) == 1:
        return exact[0]["id"]
    if len(exact) > 1:
        names = ", ".join(m["name"] for m in exact)
        raise SystemExit(f"Ambiguous item '{name_or_id}'. Matches: {names}")
    # ID prefix
    id_matches = [it for it in items if it["id"].startswith(name_or_id)]
    if len(id_matches) == 1:
        return id_matches[0]["id"]
    # Name prefix (case-insensitive)
    name_matches = [it for it in items if it["name"].lower().startswith(lower)]
    if len(name_matches) == 1:
        return name_matches[0]["id"]
    if len(name_matches) > 1:
        names = ", ".join(m["name"] for m in name_matches)
        raise SystemExit(f"Ambiguous item '{name_or_id}'. Matches: {names}")
    if len(id_matches) > 1:
        ids = ", ".join(short_id(m["id"]) for m in id_matches)
        raise SystemExit(f"Ambiguous item ID prefix '{name_or_id}'. Matches: {ids}")
    raise SystemExit(f"Check item not found: {name_or_id}")


TRELLO_COLORS = {
    "yellow", "purple", "blue", "red", "green", "orange",
    "black", "sky", "pink", "lime",
}


def _resolve_label(board_id: str, name_or_id: str) -> str:
    """Resolve a label name (case-insensitive prefix) or ID prefix."""
    _reject_flag_value(name_or_id, "label name or ID")
    labels = api.get_labels(board_id)
    # Exact ID
    for lb in labels:
        if lb["id"] == name_or_id:
            return lb["id"]
    lower = name_or_id.lower()
    # Exact name (case-insensitive)
    exact = [lb for lb in labels if lb.get("name") and lb["name"].lower() == lower]
    if len(exact) == 1:
        return exact[0]["id"]
    if len(exact) > 1:
        names = ", ".join(m.get("name", m["id"][:8]) for m in exact)
        raise SystemExit(f"Ambiguous label '{name_or_id}'. Matches: {names}")
    # ID prefix
    id_matches = [lb for lb in labels if lb["id"].startswith(name_or_id)]
    if len(id_matches) == 1:
        return id_matches[0]["id"]
    # Name prefix (case-insensitive)
    name_matches = [lb for lb in labels if (lb.get("name") or "").lower().startswith(lower) and lb.get("name")]
    if len(name_matches) == 1:
        return name_matches[0]["id"]
    if len(name_matches) > 1:
        names = ", ".join(m.get("name", m["id"][:8]) for m in name_matches)
        raise SystemExit(f"Ambiguous label '{name_or_id}'. Matches: {names}")
    if len(id_matches) > 1:
        ids = ", ".join(short_id(m["id"]) for m in id_matches)
        raise SystemExit(f"Ambiguous label ID prefix '{name_or_id}'. Matches: {ids}")
    raise SystemExit(f"Label not found: {name_or_id}")


def _resolve_attachment(card_id: str, name_or_id: str) -> dict:
    """Resolve an attachment by ID, ID prefix, or case-insensitive name prefix.
    Returns the full attachment dict (callers need its url/isUpload/mimeType)."""
    _reject_flag_value(name_or_id, "attachment name or ID")
    atts = api.get_attachments(card_id)
    # Exact ID
    for a in atts:
        if a["id"] == name_or_id:
            return a
    lower = name_or_id.lower()
    # Exact name (case-insensitive)
    exact = [a for a in atts if (a.get("name") or "").lower() == lower and a.get("name")]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        names = ", ".join(a.get("name") or short_id(a["id"]) for a in exact)
        raise SystemExit(f"Ambiguous attachment '{name_or_id}'. Matches: {names}")
    # ID prefix
    id_matches = [a for a in atts if a["id"].startswith(name_or_id)]
    if len(id_matches) == 1:
        return id_matches[0]
    # Name prefix (case-insensitive)
    name_matches = [a for a in atts if (a.get("name") or "").lower().startswith(lower)]
    if len(name_matches) == 1:
        return name_matches[0]
    if len(name_matches) > 1:
        names = ", ".join(a.get("name") or short_id(a["id"]) for a in name_matches)
        raise SystemExit(f"Ambiguous attachment '{name_or_id}'. Matches: {names}")
    if len(id_matches) > 1:
        ids = ", ".join(short_id(a["id"]) for a in id_matches)
        raise SystemExit(f"Ambiguous attachment ID prefix '{name_or_id}'. Matches: {ids}")
    raise SystemExit(f"Attachment not found: {name_or_id}")


_HELP_FLAGS = {"-h", "--help"}
# The bare word `help` only reads as a help request where a *verb* belongs.
# Scanning every position for it made `card add "To Do" help` (a card named
# "help") print the usage and exit 0 — a write silently turned into a no-op that
# still looked like a success, which is the one thing an agent caller can't see.
_HELP_WORDS = _HELP_FLAGS | {"help"}


def _wants_help(args: list[str], subcmds: dict) -> bool:
    """True when `args` asks for help, rather than carrying one of these as text.

    Either the first argument is a help word, or the last is a help *flag* and
    the first is a real verb (`card ls --help`, `comment add <id> --help`)."""
    if not args:
        return False
    if args[0] in _HELP_WORDS:
        return True
    return args[-1] in _HELP_FLAGS and args[0] in subcmds


# Wrong-but-reasonable verbs, and where the thing they wanted actually lives.
# Every entry here was an actual first guess made by a cold agent (see ax/):
# the noun groups don't nest, so `card comment` and `list cards` read as
# perfectly sensible commands right up until they aren't.
_VERB_HINTS = {
    ("card", "comment"): "trello comment add <card_id> <text>",
    ("card", "comments"): "trello card show <card_id>",
    ("card", "label"): "trello label set <card_id> <label>",
    ("card", "checklist"): "trello checklist add <card_id> <name>",
    ("card", "attachment"): "trello attachment add <card_id> <url>",
    ("card", "attach"): "trello attachment add <card_id> <url>",
    ("card", "create"): "trello card add <list> <name>",
    ("card", "delete"): "trello card archive <card_id>",
    ("card", "assign"): "trello card mine (this CLI is single-user)",
    ("card", "search"): 'trello search <query>  (top-level, not a card verb)',
    ("card", "find"): 'trello search <query>  (top-level, not a card verb)',
    ("card", "url"): "trello card link <card_id>  (a magnet link; local cards "
                     "have no Trello URL)",
    ("card", "magnet"): "trello card link <card_id>",
    ("card", "open"): "trello open <magnet>  (top-level; for attachments, "
                      "trello attachment open <card_id> <attachment>)",
    ("board", "url"): "trello board link",
    ("board", "list"): "trello boards",
    ("board", "ls"): "trello boards",
    ("board", "cards"): "trello card ls",
    ("board", "delete"): "trello board archive (or `local rm <board> --yes`)",
    ("list", "cards"): "trello card ls <list>",
    ("list", "card"): "trello card ls <list>",
    ("list", "delete"): "trello list archive <list>",
    ("label", "delete"): "trello label delete <label>",
    ("comment", "list"): "trello comment ls <card_id>",
}

# Words that are a verb *somewhere* in this CLI. When one shows up where a group
# expects a verb, the caller meant it as a verb — so say so, rather than letting
# the bare-noun `ls` fallback swallow it and fail three layers down with
# "List not found: comment Migrate database Blocked on design review."
_VERB_WORDS = {
    "add", "archive", "assign", "attach", "attachment", "attachments", "board",
    "boards", "card", "cards", "check", "checklist", "checklists", "comment",
    "comments", "create", "del", "delete", "desc", "describe", "download",
    "due", "edit", "item", "items", "label", "labels", "link", "list", "lists",
    "ls", "magnet", "url",
    "find", "mine", "move", "new", "open", "pos", "position", "remove",
    "rename", "reorder", "restore", "rm", "search", "set", "show", "uncheck",
    "unarchive", "unset", "update",
}

# What else a caller in this group probably needs to know about.
_SEE_ALSO = {
    "card": (
        "`grab --from <list> --to <list>` claims the top card of a list atomically\n"
        "          (use it instead of `card ls` + `card move` when other agents are\n"
        "          working the same board); comments, labels, checklists and\n"
        "          attachments are their own noun groups and all take a <card_id>.\n"
        "          `card link <card_id>` prints one token carrying board + backend\n"
        "          + card id, accepted anywhere a <card_id> is."
    ),
    "list": "`card ls <list>` shows the cards in a column.",
    "board": "`--board <name_or_id>` picks the board for every other command.",
    "label": "`label set <card_id> <label>` puts a board label on a card.",
    "comment": "`card show <card_id>` already prints a card's comments.",
}


def _usage_section(group: str) -> str:
    """The USAGE lines describing one noun group (plus its plural, so
    `board --help` also turns up `boards`)."""
    head = group.split()[0]
    wanted = {head, head + "s"}
    out: list[str] = []
    keep = False
    for line in USAGE.splitlines():
        stripped = line.strip()
        if not stripped:
            keep = False
            continue
        if line.startswith("  ") and not line.startswith("   "):
            keep = stripped.split()[0] in wanted
        if keep:
            out.append(line)
    return "\n".join(out)


def _print_group_help(group: str) -> None:
    """`trello card --help` — the section of USAGE that group owns.

    Agents reach for `<noun> --help` before they reach for the top-level help;
    answering that with "Unknown flag: --help" burns a turn and teaches nothing."""
    body = _usage_section(group)
    if not body:
        print(USAGE)
        return
    print(f"Usage: trello [--board <name_or_id>] [--json] {group} <verb> [args]")
    print()
    print(body)
    see = _SEE_ALSO.get(group.split()[0])
    if see:
        print(f"\nSee also: {see}")


def _unknown_verb(group: str, subcmds: dict, verb: str, ls_takes_args: bool) -> None:
    verbs = ", ".join(subcmds)
    lines = [f"Unknown {group} command: {verb}. Valid verbs: {verbs}"]
    hint = _VERB_HINTS.get((group, verb.lower()))
    if hint is None and verb.lower() in ("list", "ls") and "ls" in subcmds:
        hint = f"trello {group} ls"
    if hint:
        lines.append(f"Did you mean: {hint}")
    if ls_takes_args:
        lines.append(f'If {verb!r} is a name, not a verb: trello {group} ls "{verb}"')
    lines.append(f"Full help: trello {group} --help")
    raise SystemExit("\n".join(lines))


def _dispatch(group: str, subcmds: dict, args: list[str],
              ls_takes_args: bool = False) -> None:
    """Dispatch a noun-group subcommand.

    A bare noun (no args) falls back to `ls`. When args are present but the first
    isn't a known verb, only fall back to `ls` if `ls` actually *consumes* those
    args (e.g. `card ls <list>`); otherwise the args would be silently ignored —
    a typo'd verb like `list renmae ...` — so error instead of a false success.
    A first arg that is a verb *elsewhere* in the CLI never falls through either,
    however much `ls` would accept it."""
    if _wants_help(args, subcmds):
        _print_group_help(group)
        return
    if args and args[0] in subcmds:
        subcmds[args[0]](args[1:])
        return
    if args and args[0].lower() in _VERB_WORDS:
        _unknown_verb(group, subcmds, args[0], ls_takes_args)
    if "ls" in subcmds and (not args or ls_takes_args):
        subcmds["ls"](args)
        return
    if args:
        _unknown_verb(group, subcmds, args[0], ls_takes_args)
    raise SystemExit(f"Usage: trello {group} <{', '.join(subcmds)}> [args]")


# Flags agents invent because every other CLI has them. Naming the positional
# form costs one line and saves a turn.
_FLAG_HINTS = {
    "--list": ('The list is positional: trello card ls "To Do"   '
               "(`search` does take --list <list>)"),
    "--card": "The card is positional: trello card show <card_id>",
    "--name": "The name is positional, e.g. trello card add <list> <name>",
    "--all": "For archived cards use --archived; for archived boards, boards --all",
    "--assigned-to": "This CLI is single-user: trello card mine",
    "--assignee": "This CLI is single-user: trello card mine",
    "--filter": "Filter by column instead: trello card ls <list>",
    "--limit": "`card ls` takes --limit <n>; elsewhere counts are positional, "
               "e.g. trello activity 20",
    "--magnet": "The magnet is positional: trello open <magnet>",
    "--link": "The magnet is positional: trello open <magnet>",
}


def _parse_flags(
    args: list[str],
    bool_flags: tuple[str, ...] = (),
    value_flags: tuple[str, ...] = (),
) -> tuple[list[str], dict[str, str | bool]]:
    """Split `args` into (positionals, flags), rejecting unknown flags.

    `bool_flags` are valueless (presence → True). `value_flags` consume the
    following token as their value. Any other `--`-prefixed token raises
    SystemExit, so a mistyped flag is reported instead of being silently
    swallowed into a positional argument (e.g. a list/card name). A bare `--`
    ends flag parsing, the same escape hatch `_free_text` offers, so a value
    that really does start with dashes stays reachable."""
    positional: list[str] = []
    flags: dict[str, str | bool] = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            positional.extend(args[i + 1:])
            break
        if a in bool_flags:
            flags[a] = True
        elif a in value_flags:
            if i + 1 >= len(args):
                raise SystemExit(f"{a} requires a value.")
            flags[a] = args[i + 1]
            i += 1
        elif a.startswith("--"):
            hint = _FLAG_HINTS.get(a)
            raise SystemExit(f"Unknown flag: {a}" + (f"\n{hint}" if hint else ""))
        else:
            positional.append(a)
        i += 1
    return positional, flags


# ── Global commands ──────────────────────────────────────────────────


def cmd_configure(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello configure <api_key> <token>")
    config.save_credentials(args[0], args[1])
    print("Credentials saved.")


def cmd_configure_http(args: list[str]) -> None:
    if not args or len(args) > 2 or args[0].startswith("-"):
        raise SystemExit("Usage: trello configure-http <server_url> [<token>]")
    url = args[0].rstrip("/")
    if not url.lower().startswith(("http://", "https://")):
        raise SystemExit(
            f"Server URL must start with http:// or https:// (got {url!r})."
        )
    token = args[1] if len(args) > 1 else None
    config.save_http_server(url, token)
    print(f"Trellno server saved: {url}"
          + ("" if token else "  (existing token kept)"))
    print("Use it with:  trello --backend http <command>"
          "  (or TRELLO_BACKEND=http)")


def cmd_boards(args: list[str]) -> None:
    positional, flags = _parse_flags(args, bool_flags=("--archived", "--all"))
    archived_only = bool(flags.get("--archived"))
    include_closed = archived_only or bool(flags.get("--all"))
    boards = api.get_boards(include_closed=include_closed)
    if archived_only:
        boards = [b for b in boards if b.get("closed")]
    # Optional positional filter: `boards trellocli`. Substring on the name (not
    # the prefix-only matching `--board` does — when you're *looking* for a board
    # you rarely know how its name starts) or a prefix of the id. Filtering is
    # client-side on every backend, so unlike card search there's no remote index
    # to mirror and no cross-backend divergence to create.
    query = " ".join(positional).strip().lower()
    if query:
        boards = [
            b for b in boards
            if query in (b.get("name") or "").lower()
            or (b.get("id") or "").lower().startswith(query)
        ]
    if _is_json():
        print_json(boards)
        return
    if not boards and query:
        scope = ("archived boards" if archived_only
                 else "boards" if include_closed else "open boards")
        print(f'No {scope} matching "{query}".')
        if not include_closed:
            print("  Archived boards are hidden by default: boards --all")
        return
    if include_closed:
        rows = [[short_id(b["id"]), b["name"],
                 "archived" if b.get("closed") else "", b.get("shortUrl", "")]
                for b in boards]
        print_table(["ID", "Name", "State", "URL"], rows)
    else:
        rows = [[short_id(b["id"]), b["name"], b.get("shortUrl", "")] for b in boards]
        print_table(["ID", "Name", "URL"], rows)


def _board_show(_args: list[str]) -> None:
    # `trello board show Roadmap` is the obvious guess, and the board is *not* a
    # positional anywhere in this CLI — so say where it goes instead of letting
    # _require_board() answer a question the caller already tried to answer.
    # This fires whenever a name was given, override or not: with one set, the
    # positional used to be dropped silently and a *different* board reported.
    if _args:
        raise SystemExit(
            f"The board is a global flag, not an argument: "
            f"trello --board {_args[0]!r} board show".replace("'", '"')
        )
    board_id = _require_board()
    b = api.get_board(board_id)
    if _is_json():
        print_json(b)
        return
    print(f"  Board: {b['name']}")
    print(f"  ID:    {b['id']}")
    print(f"  URL:   {b.get('shortUrl', '')}")
    desc = b.get("desc", "").strip()
    if desc:
        print(f"  Desc:  {truncate(desc, 80)}")


def _board_add(args: list[str]) -> None:
    positional, flags = _parse_flags(args, bool_flags=("--no-default-lists",))
    if not positional:
        raise SystemExit(
            "Usage: trello board add <name> [description] [--no-default-lists]"
        )
    name = positional[0]
    desc = " ".join(positional[1:]) if len(positional) > 1 else None
    b = api.create_board(name, desc=desc, default_lists=not flags.get("--no-default-lists"))
    print(f"Created board: {b['name']} ({short_id(b['id'])})  {b.get('shortUrl', '')}")


def _board_rename(args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: trello --board <board> board rename <new name>")
    board_id = _require_board()
    b = api.update_board(board_id, name=_free_text(args, "a new board name"))
    if _is_json():
        print_json(b)
        return
    print(f"Renamed board to: {b['name']} ({short_id(b['id'])})")


def _board_set_closed(closed: bool) -> None:
    board_id = _require_board()
    b = api.update_board(board_id, closed=closed)
    if _is_json():
        print_json(b)
        return
    verb = "Archived" if closed else "Restored"
    print(f"{verb} board: {b['name']} ({short_id(b['id'])})")


_BOARD_VERBS = {"show": None, "add": None, "rename": None,
                "archive": None, "restore": None, "link": None}


def cmd_board(args: list[str]) -> None:
    verb = args[0] if args else ""
    if _wants_help(args, _BOARD_VERBS):
        _print_group_help("board")
        return
    if verb == "add":
        _board_add(args[1:])
        return
    if verb == "rename":
        _board_rename(args[1:])
        return
    if verb == "link":
        _board_link(args[1:])
        return
    if verb in ("archive", "restore", "unarchive"):
        # These verbs act on the --board board and take NO positionals — a stray
        # one (`board archive Scratch`) would otherwise be silently dropped and
        # the *--board* board mutated. Reject it so the wrong board isn't touched.
        extra = args[1:]
        if extra:
            raise SystemExit(
                f"trello board {verb} takes no arguments (got {extra[0]!r}). "
                f"It acts on the --board board; pass --board <board> to choose it."
            )
        _board_set_closed(verb == "archive")
        return
    if verb in ("", "show"):
        _board_show(args[1:] if verb == "show" else args)
        return
    # `board list` is the single commonest wrong guess here — the plural command
    # `boards` is what lists them, and nothing said so.
    _unknown_verb("board", _BOARD_VERBS, verb, ls_takes_args=False)


def cmd_members(_args: list[str]) -> None:
    board_id = _require_board()
    members = api.get_members(board_id)
    if _is_json():
        print_json(members)
        return
    rows = [[short_id(m["id"]), m.get("fullName", ""), f"@{m.get('username', '')}"] for m in members]
    print_table(["ID", "Name", "Username"], rows)


def cmd_activity(args: list[str]) -> None:
    board_id = _require_board()
    if args:
        try:
            limit = int(args[0])
        except ValueError:
            raise SystemExit(
                f"Usage: trello activity [n]  (n must be a number, got {args[0]!r})"
            )
    else:
        limit = 10
    actions = api.get_activity(board_id, limit)
    if _is_json():
        print_json(actions)
        return
    for a in actions:
        date = a.get("date", "")[:10]
        who = a.get("memberCreator", {}).get("username", "?")
        atype = a.get("type", "?")
        data = a.get("data", {})
        card_name = data.get("card", {}).get("name", "")
        list_name = data.get("list", {}).get("name", "")
        detail = ""
        if card_name:
            detail = truncate(card_name, 40)
        if list_name and not card_name:
            detail = list_name
        print(f"  {date}  @{who:<12}  {atype:<24}  {detail}")


# ── Card subcommands ────────────────────────────────────────────────


def _card_show(args: list[str]) -> None:
    positional, flags = _parse_flags(args, bool_flags=("--no-comments",))
    if not positional:
        raise SystemExit("Usage: trello card show <card_id> [--no-comments]")
    card = api.get_card(_resolve_card(positional[0], include_closed=True))
    comments = [] if flags.get("--no-comments") else api.get_comments(card["id"], limit=20)
    if _is_json():
        print_json({**card, "comments": comments})
        return
    print_card_detail(card, comments, link=_card_magnet(card))


def _card_magnet(card: dict) -> str | None:
    """The card's magnet for the `Link:` line, or None if we can't build one.

    Never fatal: a card whose dict carries no board id still shows, it just
    shows without the link."""
    board_id = card.get("idBoard") or config.get_board_override()
    if not board_id or len(board_id) != 24:
        return None
    backend = config.get_backend_name()
    server = _link_server(backend)
    if backend == "http" and not server:
        return None
    return magnet.build_card(card["id"], board_id, backend,
                             name=card.get("name"), server=server)


def _link_flags(args: list[str], usage: str) -> tuple[list[str], str, bool]:
    """Shared `--as uri|cmd` / `--short` parsing for `card link` / `board link`."""
    positional, flags = _parse_flags(
        args, bool_flags=("--short",), value_flags=("--as",)
    )
    mode = flags.get("--as", "uri")
    if mode not in ("uri", "cmd"):
        raise SystemExit(
            f"--as takes 'uri' (a trello:// magnet, the default) or 'cmd' "
            f"(a ready-to-run command line), got {mode!r}.\n{usage}"
        )
    return positional, str(mode), bool(flags.get("--short"))


def _link_cmd(board_id: str, card_id: str | None, backend: str, short: bool) -> str:
    """The `--as cmd` form: a command line that reaches the same entity.

    The card raises this as a rival to the URI, and for "paste this into your
    shell" it genuinely is the better form — agents run commands, not URIs. The
    URI stays the default because only it round-trips back into structured
    form."""
    bid = short_id(board_id) if short else board_id
    parts = ["trello", "--backend", backend]
    if backend == "http":
        server = config.get_server_url()
        if server:
            parts += ["--server", server]
    parts += ["--board", bid]
    if card_id:
        parts += ["card", "show", short_id(card_id) if short else card_id]
    else:
        parts += ["board"]
    return " ".join(parts)


def _link_server(backend: str) -> str | None:
    """The server URL a magnet needs to be portable off this machine (http only)."""
    return config.get_server_url() if backend == "http" else None


_CARD_LINK_USAGE = "Usage: trello card link <card_id> [--as uri|cmd] [--short]"


def _card_link(args: list[str]) -> None:
    positional, mode, short = _link_flags(args, _CARD_LINK_USAGE)
    if not positional:
        raise SystemExit(_CARD_LINK_USAGE)
    card = api.get_card(_resolve_card(positional[0], include_closed=True))
    # Prefer the card's own board over --board: it lets `card link <full_id>`
    # work with no board flag, and it can't disagree with the card.
    board_id = card.get("idBoard") or _require_board()
    backend = config.get_backend_name()
    if mode == "cmd":
        link = _link_cmd(board_id, card["id"], backend, short)
    else:
        link = magnet.build_card(card["id"], board_id, backend,
                                 name=card.get("name"),
                                 server=_link_server(backend), short=short)
    if _is_json():
        print_json({
            "link": link, "as": mode, "type": "card", "id": card["id"],
            "board": board_id, "backend": backend,
            "server": _link_server(backend),
            "slug": magnet.slugify(card.get("name")),
        })
        return
    # One bare line, so `$(trello card link X)` is directly usable.
    print(link)


_BOARD_LINK_USAGE = "Usage: trello --board <board> board link [--as uri|cmd] [--short]"


def _board_link(args: list[str]) -> None:
    positional, mode, short = _link_flags(args, _BOARD_LINK_USAGE)
    if positional:
        # Same trap as `board archive Scratch`: the board is a global flag, so a
        # positional here would be silently dropped and the *--board* board
        # linked instead.
        raise SystemExit(
            f"The board is a global flag, not an argument: "
            f"trello --board {positional[0]!r} board link".replace("'", '"')
        )
    board_id = _require_board()
    b = api.get_board(board_id)
    backend = config.get_backend_name()
    if mode == "cmd":
        link = _link_cmd(board_id, None, backend, short)
    else:
        link = magnet.build_board(board_id, backend, name=b.get("name"),
                                  server=_link_server(backend), short=short)
    if _is_json():
        print_json({
            "link": link, "as": mode, "type": "board", "id": board_id,
            "board": board_id, "backend": backend,
            "server": _link_server(backend),
            "slug": magnet.slugify(b.get("name")),
        })
        return
    print(link)


def cmd_open(args: list[str]) -> None:
    """`trello open <magnet>` — the "I was handed a token, now what" command."""
    if not args or args[0] in _HELP_WORDS:
        print("Usage: trello open <magnet>")
        print()
        print("Show whatever a magnet link addresses (a card's detail, or a")
        print("board's info). The magnet carries its own board and backend, so")
        print("no --board / --backend flags are needed.")
        print()
        print("Get one with: trello card link <card_id>  /  trello board link")
        return
    token = args[0]
    if not magnet.is_magnet(token):
        raise SystemExit(
            f"`open` takes a magnet link (starting {magnet.SCHEME!r}), "
            f"got {token!r}.\n"
            f"Get one with: trello card link <card_id>\n"
            f"To show a card by id instead: trello card show {token}"
        )
    if len(args) > 1:
        raise SystemExit(f"trello open takes one magnet (got {args[1]!r} as well).")
    mag = magnet.parse(token)
    # main() has already seeded board/backend/server from the token, so both
    # branches are the ordinary show path.
    if mag["type"] == "card":
        _card_show([token])
    else:
        _board_show([])


def _card_row(c: dict) -> list[str]:
    """One table row for a card (shared by `card ls` and `card mine`)."""
    return [
        short_id(c["id"]),
        (c.get("dateLastActivity") or "")[:10],
        truncate(c["name"], 50),
        label_str(c.get("labels", [])),
        due_str(c.get("due"), c.get("dueComplete", False)),
    ]


# How many cards a board-wide `card ls` prints before it stops and tells you how
# to narrow. The whole point of this CLI is not flooding a caller's context, and
# a board-wide listing is the one read whose size nobody controls — a 400-card
# board would otherwise cost more context than the task that asked for it. The
# per-list view is deliberately NOT capped by default: its size is something the
# caller already chose by naming the column.
_BOARD_LS_LIMIT = 50


def _card_ls_board(board_id: str, archived: bool, limit: int) -> None:
    """Every card on the board in one table, with the list each one is in.

    `card ls` used to require a list, but "show me the cards on this board" is
    the first thing anyone asks of a board — asking it without naming a column
    was the single most common wrong turn in the AX corpus (see ax/). One table
    with a List column stays greppable and costs less context than a table per
    column."""
    lists = api.get_lists(board_id)
    order = {lst["id"]: i for i, lst in enumerate(lists)}
    names = {lst["id"]: lst["name"] for lst in lists}
    cards = api.get_board_cards(
        board_id, card_filter="closed" if archived else "visible"
    )
    cards.sort(key=lambda c: (order.get(c.get("idList"), len(order)), c.get("pos", 0)))
    total = len(cards)
    shown = cards if limit <= 0 else cards[:limit]

    # Per-column counts, so a truncated listing still says where the rest are —
    # it turns "there's more" into "here is which column to ask for next".
    counts = []
    for lst in lists:
        n = sum(1 for c in cards if c.get("idList") == lst["id"])
        if n:
            counts.append(f"{lst['name']} {n}")
    note = (
        f"Showing {len(shown)} of {total} cards ({' · '.join(counts)}). "
        f'Narrow with: trello card ls "<list>"   ·   all of them: --limit 0'
    ) if len(shown) < total else ""

    if _is_json():
        print_json(shown)
        # stdout stays a clean JSON array, so the truncation notice goes to
        # stderr — visible to a human or an agent reading combined output,
        # invisible to `| jq`.
        if note:
            print(note, file=sys.stderr)
        return
    if not cards:
        print("No archived cards." if archived else "No cards on this board.")
        return
    rows = [
        [
            short_id(c["id"]),
            truncate(names.get(c.get("idList"), "?"), 18),
            (c.get("dateLastActivity") or "")[:10],
            truncate(c["name"], 50),
            label_str(c.get("labels", [])),
            due_str(c.get("due"), c.get("dueComplete", False)),
        ]
        for c in shown
    ]
    print_table(["ID", "List", "Activity", "Name", "Labels", "Due"], rows)
    if note:
        print(f"\n  {note}")


def _card_ls(args: list[str]) -> None:
    positional, flags = _parse_flags(
        args, bool_flags=("--with-comment", "--archived"), value_flags=("--limit",)
    )
    with_comment = bool(flags.get("--with-comment"))
    archived = bool(flags.get("--archived"))
    if with_comment and archived:
        raise SystemExit("--with-comment cannot be combined with --archived.")
    raw_limit = flags.get("--limit")
    # `.isdigit()` and not `lstrip("-").isdigit()`: a negative used to pass here
    # and then read as "no limit" downstream, which is what 0 is documented for.
    if raw_limit is not None and not str(raw_limit).isdigit():
        raise SystemExit(
            f"--limit takes a number of 0 or more (0 for no limit), got {raw_limit!r}."
        )
    board_id = _require_board()
    if not positional:
        limit = _BOARD_LS_LIMIT if raw_limit is None else int(raw_limit)
        _card_ls_board(board_id, archived, limit)
        return
    list_id = _resolve_list(board_id, " ".join(positional))
    if archived:
        cards = [
            c for c in api.get_board_cards(board_id, card_filter="closed")
            if c.get("idList") == list_id
        ]
        cards.sort(key=lambda c: c.get("pos", 0))
    else:
        cards = api.get_cards_in_list(list_id, with_latest_comment=with_comment)
    # A named column is a size the caller already chose, so this view has no
    # default cap — but an explicit --limit still has to be honoured, not
    # silently accepted and ignored.
    total = len(cards)
    if raw_limit is not None and int(raw_limit) > 0:
        cards = cards[: int(raw_limit)]
    note = f"Showing {len(cards)} of {total} cards. Raise or drop with --limit <n>." \
        if len(cards) < total else ""
    if _is_json():
        print_json(cards)
        if note:
            print(note, file=sys.stderr)
        return
    rows = [_card_row(c) for c in cards]
    print_table(["ID", "Activity", "Name", "Labels", "Due"], rows)
    if note:
        print(f"\n  {note}")
    if with_comment:
        print()
        print("  Latest comments:")
        for c in cards:
            actions = c.get("actions") or []
            if not actions:
                continue
            a = actions[0]
            text = (a.get("data", {}).get("text") or "").splitlines()
            first = text[0] if text else ""
            who = a.get("memberCreator", {}).get("username", "?")
            date = (a.get("date") or "")[:10]
            print(f"    {short_id(c['id'])}  {date} @{who}: {truncate(first, 70)}")


def _card_add(args: list[str]) -> None:
    positional, flags = _parse_flags(args, bool_flags=("--bottom",))
    if len(positional) < 2:
        raise SystemExit(
            "Usage: trello card add <list_name_or_id> <card_name> [description] [--bottom]\n"
            "  Quote a multi-word card name; any words after it become the description."
        )
    pos = "bottom" if flags.get("--bottom") else "top"
    board_id = _require_board()
    list_id = _resolve_list(board_id, positional[0])
    name = positional[1]
    desc = " ".join(positional[2:]) if len(positional) > 2 else None
    card = api.create_card(list_id, name, desc=desc, pos=pos)
    print(f"Created: {card['name']} ({short_id(card['id'])})")


def _card_move(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello card move <card_id> <list_name_or_id>")
    board_id = _require_board()
    card_id = _resolve_card(args[0])
    list_id = _resolve_list(board_id, " ".join(args[1:]))
    api.move_card(card_id, list_id)
    print(f"Moved {short_id(card_id)} to list.")


def _card_archive(args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: trello card archive <card_id>")
    card_id = _resolve_card(args[0])
    api.archive_card(card_id)
    print(f"Archived {short_id(card_id)}.")


def _card_unarchive(args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: trello card unarchive <card_id>")
    card_id = _resolve_card(args[0], include_closed=True)
    api.unarchive_card(card_id)
    print(f"Unarchived {short_id(card_id)}.")


def _card_rename(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello card rename <card_id> <new_name>")
    card_id = _resolve_card(args[0])
    new_name = _free_text(args[1:], "a new card name")
    api.update_card(card_id, name=new_name)
    print(f"Renamed card {short_id(card_id)} to: {new_name}")


def _card_desc(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello card desc <card_id> <description>")
    card_id = _resolve_card(args[0])
    desc = _free_text(args[1:], "a description")
    api.update_card(card_id, desc=desc)
    print(f"Updated description for {short_id(card_id)}.")


def _parse_due(raw: str) -> str | None:
    """Parse a due-date argument into an ISO string (or None to clear)."""
    s = raw.strip().lower()
    if s in ("clear", "none", "remove", "off"):
        return None
    now = datetime.now(timezone.utc)
    today = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if s == "today":
        return today.isoformat()
    if s == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    m = re.fullmatch(r"(\d+)\s*(d|day|days|w|week|weeks|m|mo|mon|month|months|y|year|years)", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("d"):
            delta = timedelta(days=n)
        elif unit.startswith("w"):
            delta = timedelta(weeks=n)
        elif unit.startswith("y"):
            delta = timedelta(days=365 * n)
        else:
            delta = timedelta(days=30 * n)
        return (today + delta).isoformat()
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        raise SystemExit(
            f"Could not parse date: {raw!r}. "
            "Use ISO (2026-05-01), relative (1d/2w/1m/1y), 'today', 'tomorrow', or 'clear'."
        )
    if dt.tzinfo is None:
        # Only default to 9am for a date-only input; an explicit time-of-day is
        # preserved (mirrors _parse_since). ISO date-only has no "T"/":".
        if "T" in raw or ":" in raw:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.replace(hour=9, tzinfo=timezone.utc)
    return dt.isoformat()


def _parse_since(raw: str) -> str:
    """Parse a 'since' argument into an ISO string (a point in the past).

    Accepts ISO dates (2026-06-01), relative look-backs (6h, 3d, 2w, 1m, 1y),
    and the words 'today' / 'yesterday'. Unlike `_parse_due`, relative values
    count *backwards* from now."""
    s = raw.strip().lower()
    now = datetime.now(timezone.utc)
    if s == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    if s == "yesterday":
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return (midnight - timedelta(days=1)).isoformat()
    m = re.fullmatch(r"(\d+)\s*(h|hour|hours|d|day|days|w|week|weeks|m|mo|mon|month|months|y|year|years)", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("h"):
            delta = timedelta(hours=n)
        elif unit.startswith("d"):
            delta = timedelta(days=n)
        elif unit.startswith("w"):
            delta = timedelta(weeks=n)
        elif unit.startswith("y"):
            delta = timedelta(days=365 * n)
        else:
            delta = timedelta(days=30 * n)
        return (now - delta).isoformat()
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        raise SystemExit(
            f"Could not parse date: {raw!r}. "
            "Use ISO (2026-06-01), relative (6h/3d/2w/1m/1y), 'today', or 'yesterday'."
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _print_action(a: dict) -> None:
    """One compact line per board action, with comment text inlined."""
    ts = (a.get("date") or "")[:16].replace("T", " ")
    who = a.get("memberCreator", {}).get("username", "?")
    atype = a.get("type", "?")
    data = a.get("data", {})
    card_name = (data.get("card") or {}).get("name", "")
    if atype == "commentCard":
        first = ((data.get("text") or "").splitlines() or [""])[0]
        detail = f"{truncate(card_name, 28)}: {truncate(first, 60)}"
    elif card_name:
        detail = truncate(card_name, 50)
    else:
        detail = (data.get("list") or {}).get("name", "")
    print(f"  {ts}  @{who:<12}  {atype:<18}  {detail}")


def cmd_updates(args: list[str]) -> None:
    positional, _ = _parse_flags(args)
    if not positional:
        raise SystemExit(
            "Usage: trello updates <since> [action_type ...]\n"
            "  Since: ISO date (2026-06-01), relative (6h, 3d, 2w, 1m, 1y),\n"
            "         'today', or 'yesterday'.\n"
            "  Optionally filter by Trello action types, e.g. commentCard updateCard."
        )
    board_id = _require_board()
    since = _parse_since(positional[0])
    action_types = ",".join(positional[1:]) if len(positional) > 1 else None
    actions = api.get_actions_since(board_id, since, action_types=action_types)
    if _is_json():
        print_json(actions)
        return
    if not actions:
        print(f"  No activity since {since[:16].replace('T', ' ')}.")
        return
    print(f"  {len(actions)} update(s) since {since[:16].replace('T', ' ')}:")
    for a in actions:
        _print_action(a)


def _card_due(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit(
            "Usage: trello card due <card_id> <date>\n"
            "  Date: ISO (2026-05-01), relative (1d, 2w, 1m, 1y),\n"
            "        'today', 'tomorrow', or 'clear' to remove."
        )
    card_id = _resolve_card(args[0])
    due = _parse_due(_free_text(args[1:], "a due date"))
    api.update_card(card_id, due=due if due is not None else "")
    if due is None:
        print(f"Cleared due date on {short_id(card_id)}.")
    else:
        print(f"Set due date on {short_id(card_id)} to {due[:10]}.")


def _split_relative_pos(args: list[str]) -> list[str]:
    """Accept `pos <id> "after <other>"` as well as `pos <id> after <other>`.

    The help text quotes the relative form — `'after <other_card_id>'` — so a
    caller quoting it exactly, as agents reliably do, used to get
    "Invalid position: 'after 8c5d782e'" for following the documentation."""
    if len(args) >= 2:
        parts = args[1].split()
        if len(parts) == 2 and parts[0].lower() in ("after", "before"):
            return [args[0], parts[0].lower(), parts[1], *args[2:]]
    return args


def _relative_pos(others: list[dict], other_id: str, keyword: str):
    """Compute the new `pos` placing an item before/after `other_id` within the
    sorted `others` list (which excludes the item being moved). Returns a float
    midpoint, or the string 'top'/'bottom' at the ends, or None if `other_id`
    isn't present. Shared by `card pos` and `list pos`."""
    idx = next((i for i, o in enumerate(others) if o["id"] == other_id), None)
    if idx is None:
        return None
    ref_pos = others[idx]["pos"]
    if keyword == "after":
        return (ref_pos + others[idx + 1]["pos"]) / 2 if idx + 1 < len(others) else "bottom"
    return (others[idx - 1]["pos"] + ref_pos) / 2 if idx > 0 else "top"


def _card_pos(args: list[str]) -> None:
    args = _split_relative_pos(args)
    if len(args) < 2:
        raise SystemExit(
            "Usage: trello card pos <card_id> <position>\n"
            "  Position: top, bottom, a number,\n"
            "            'after <other_card_id>', or 'before <other_card_id>'"
        )
    card_id = _resolve_card(args[0])
    keyword = args[1].lower()

    if keyword in ("top", "bottom"):
        api.update_card(card_id, pos=keyword)
        print(f"Moved {short_id(card_id)} to {keyword}.")
        return

    if keyword in ("after", "before"):
        if len(args) < 3:
            raise SystemExit(
                f"Usage: trello card pos <card_id> {keyword} <other_card_id>"
            )
        other_id = _resolve_card(args[2])
        if other_id == card_id:
            raise SystemExit("Cannot position a card relative to itself.")
        card = api.get_card(card_id)
        other = api.get_card(other_id)
        if card["idList"] != other["idList"]:
            raise SystemExit(
                "Cards are not in the same list. "
                "Use 'card move' first, then 'card pos'."
            )
        cards = api.get_cards_in_list(card["idList"])
        cards.sort(key=lambda c: c.get("pos", 0))
        others = [c for c in cards if c["id"] != card_id]
        new_pos = _relative_pos(others, other_id, keyword)
        if new_pos is None:
            raise SystemExit("Reference card not found in list.")
        api.update_card(card_id, pos=new_pos)
        print(f"Moved {short_id(card_id)} {keyword} {short_id(other_id)}.")
        return

    try:
        numeric = float(args[1])
    except ValueError:
        raise SystemExit(
            f"Invalid position: {args[1]!r}. "
            "Use top, bottom, a number, 'after <id>', or 'before <id>'."
        )
    api.update_card(card_id, pos=numeric)
    print(f"Set position of {short_id(card_id)} to {numeric}.")


def _card_mine(_args: list[str]) -> None:
    cards = api.get_my_cards()
    if _is_json():
        print_json(cards)
        return
    rows = [_card_row(c) for c in cards]
    print_table(["ID", "Activity", "Name", "Labels", "Due"], rows)


def cmd_card(args: list[str]) -> None:
    _dispatch("card", {
        "show": _card_show,
        "ls": _card_ls,
        "add": _card_add,
        "move": _card_move,
        "archive": _card_archive,
        "unarchive": _card_unarchive,
        "rename": _card_rename,
        "desc": _card_desc,
        "due": _card_due,
        "pos": _card_pos,
        "mine": _card_mine,
        "link": _card_link,
    }, args, ls_takes_args=True)


# ── List subcommands ────────────────────────────────────────────────


def _list_ls(_args: list[str]) -> None:
    board_id = _require_board()
    lists = api.get_lists(board_id)
    if _is_json():
        print_json(lists)
        return
    rows = [[lst["id"], lst["name"]] for lst in lists]
    print_table(["ID", "Name"], rows)


def _list_add(args: list[str]) -> None:
    usage = (
        "Usage: trello list add <name> [--top | --bottom | --pos <n>]\n"
        "  Position defaults to top (leftmost), matching `card add`."
    )
    positional, flags = _parse_flags(
        args, bool_flags=("--top", "--bottom"), value_flags=("--pos",)
    )
    if not positional:
        raise SystemExit(usage)
    chosen = [p for p in ("top", "bottom") if flags.get(f"--{p}")]
    pos_val = flags.get("--pos")
    if isinstance(pos_val, str):
        chosen.append(pos_val)
    if len(chosen) > 1:
        raise SystemExit("Use only one of --top, --bottom, or --pos.")
    # Pass an explicit "top" default so both backends agree (the Trello backend
    # would otherwise append at the bottom; the local store defaults to top).
    pos = chosen[0] if chosen else "top"
    board_id = _require_board()
    name = " ".join(positional)
    lst = api.create_list(board_id, name, pos=pos)
    print(f"Created list: {lst['name']} ({lst['id'][:8]})")


def _list_pos(args: list[str]) -> None:
    args = _split_relative_pos(args)
    if len(args) < 2:
        raise SystemExit(
            "Usage: trello list pos <list_id> <position>\n"
            "  Position: top, bottom, a number,\n"
            "            'after <other_list_id>', or 'before <other_list_id>'"
        )
    board_id = _require_board()
    list_id = _resolve_list(board_id, args[0])
    keyword = args[1].lower()

    if keyword in ("top", "bottom"):
        api.update_list(list_id, pos=keyword)
        print(f"Moved {short_id(list_id)} to {keyword}.")
        return

    if keyword in ("after", "before"):
        if len(args) < 3:
            raise SystemExit(
                f"Usage: trello list pos <list_id> {keyword} <other_list_id>"
            )
        other_id = _resolve_list(board_id, args[2])
        if other_id == list_id:
            raise SystemExit("Cannot position a list relative to itself.")
        lists = api.get_lists(board_id)
        lists.sort(key=lambda lst: lst.get("pos", 0))
        others = [lst for lst in lists if lst["id"] != list_id]
        new_pos = _relative_pos(others, other_id, keyword)
        if new_pos is None:
            raise SystemExit("Reference list not found on board.")
        api.update_list(list_id, pos=new_pos)
        print(f"Moved {short_id(list_id)} {keyword} {short_id(other_id)}.")
        return

    try:
        numeric = float(args[1])
    except ValueError:
        raise SystemExit(
            f"Invalid position: {args[1]!r}. "
            "Use top, bottom, a number, 'after <id>', or 'before <id>'."
        )
    api.update_list(list_id, pos=numeric)
    print(f"Set position of {short_id(list_id)} to {numeric}.")


def _list_archive(args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: trello list archive <list_name_or_id>")
    board_id = _require_board()
    list_id = _resolve_list(board_id, " ".join(args))
    api.archive_list(list_id)
    print("Archived list.")


def _list_rename(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello list rename <list_name_or_id> <new_name>")
    board_id = _require_board()
    list_id = _resolve_list(board_id, args[0])
    new_name = _free_text(args[1:], "a new list name")
    api.rename_list(list_id, new_name)
    print(f"Renamed list to: {new_name}")


def cmd_list(args: list[str]) -> None:
    _dispatch("list", {
        "ls": _list_ls,
        "add": _list_add,
        "archive": _list_archive,
        "rename": _list_rename,
        "pos": _list_pos,
    }, args)


# ── Label subcommands ──────────────────────────────────────────────


def _label_ls(_args: list[str]) -> None:
    board_id = _require_board()
    labels = api.get_labels(board_id)
    if _is_json():
        print_json(labels)
        return
    rows = [[short_id(lb["id"]), lb.get("name", ""), lb.get("color", "")] for lb in labels]
    print_table(["ID", "Name", "Color"], rows)


def _label_add(args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: trello label add <name> [color]\n"
                         "  A single argument is the (colorless) label name; a\n"
                         "  trailing known color word sets the color.\n"
                         f"  Colors: {', '.join(sorted(TRELLO_COLORS))}")
    board_id = _require_board()
    # Last arg may be a color
    color = None
    if len(args) >= 2 and args[-1].lower() in TRELLO_COLORS:
        color = args[-1].lower()
        name = _free_text(args[:-1], "a label name")
    else:
        name = _free_text(args, "a label name")
    lb = api.create_label(board_id, name, color)
    print(f"Created label: {lb.get('name', '')} ({short_id(lb['id'])})"
          f"{' [' + lb.get('color', '') + ']' if lb.get('color') else ''}")


def _label_edit(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello label edit <label> [name] [color]\n"
                         f"Colors: {', '.join(sorted(TRELLO_COLORS))}")
    board_id = _require_board()
    label_id = _resolve_label(board_id, args[0])
    fields: dict[str, str] = {}
    rest = args[1:]
    # If last arg is a color, treat it as color; rest is name
    if rest[-1].lower() in TRELLO_COLORS:
        fields["color"] = rest[-1].lower()
        rest = rest[:-1]
    if rest:
        fields["name"] = _free_text(rest, "a label name")
    if not fields:
        raise SystemExit("Nothing to update. Provide a new name and/or color.")
    api.update_label(label_id, **fields)
    print(f"Updated label {short_id(label_id)}.")


def _label_delete(args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: trello label delete <label>")
    board_id = _require_board()
    label_id = _resolve_label(board_id, args[0])
    api.delete_label(label_id)
    print(f"Deleted label {short_id(label_id)}.")


def _label_set(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello label set <card_id> <label>")
    board_id = _require_board()
    card_id = _resolve_card(args[0])
    label_id = _resolve_label(board_id, " ".join(args[1:]))
    api.add_label_to_card(card_id, label_id)
    print(f"Added label to card {short_id(card_id)}.")


def _label_unset(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello label unset <card_id> <label>")
    board_id = _require_board()
    card_id = _resolve_card(args[0])
    label_id = _resolve_label(board_id, " ".join(args[1:]))
    api.remove_label_from_card(card_id, label_id)
    print(f"Removed label from card {short_id(card_id)}.")


def cmd_label(args: list[str]) -> None:
    _dispatch("label", {
        "ls": _label_ls,
        "add": _label_add,
        "edit": _label_edit,
        "delete": _label_delete,
        "set": _label_set,
        "unset": _label_unset,
    }, args)


# ── Checklist subcommands ──────────────────────────────────────────


def _checklist_ls(args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: trello checklist ls <card_id>")
    card_id = _resolve_card(args[0])
    checklists = api.get_checklists(card_id)
    if _is_json():
        print_json(checklists)
        return
    if not checklists:
        print("  No checklists.")
        return
    for cl in checklists:
        items = cl.get("checkItems", [])
        done = sum(1 for it in items if it.get("state") == "complete")
        print(f"  {short_id(cl['id'])}  {cl['name']} ({done}/{len(items)})")
        for it in items:
            mark = "x" if it.get("state") == "complete" else " "
            print(f"    [{mark}] {short_id(it['id'])}  {it['name']}")


def _checklist_add(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello checklist add <card_id> <name>")
    card_id = _resolve_card(args[0])
    name = _free_text(args[1:], "a checklist name")
    cl = api.create_checklist(card_id, name)
    print(f"Created checklist: {cl['name']} ({short_id(cl['id'])})")


def _checklist_delete(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello checklist delete <card_id> <checklist>")
    card_id = _resolve_card(args[0])
    cl_id = _resolve_checklist(card_id, args[1])
    api.delete_checklist(cl_id)
    print(f"Deleted checklist {short_id(cl_id)}.")


def _checklist_rename(args: list[str]) -> None:
    if len(args) < 3:
        raise SystemExit("Usage: trello checklist rename <card_id> <checklist> <name>")
    card_id = _resolve_card(args[0])
    cl_id = _resolve_checklist(card_id, args[1])
    new_name = _free_text(args[2:], "a new checklist name")
    api.rename_checklist(cl_id, new_name)
    print(f"Renamed checklist {short_id(cl_id)} to: {new_name}")


def _checklist_item_add(args: list[str]) -> None:
    if len(args) < 3:
        raise SystemExit("Usage: trello checklist item add <card_id> <checklist> <text>")
    card_id = _resolve_card(args[0])
    cl_id = _resolve_checklist(card_id, args[1])
    name = _free_text(args[2:], "item text")
    it = api.add_checkitem(cl_id, name)
    print(f"Added item: {it['name']} ({short_id(it['id'])})")


def _checklist_item_delete(args: list[str]) -> None:
    if len(args) < 3:
        raise SystemExit("Usage: trello checklist item delete <card_id> <checklist> <item>")
    card_id = _resolve_card(args[0])
    cl_id = _resolve_checklist(card_id, args[1])
    item_id = _resolve_checkitem(card_id, cl_id, args[2])
    api.delete_checkitem(cl_id, item_id)
    print(f"Deleted item {short_id(item_id)}.")


def _checklist_item_rename(args: list[str]) -> None:
    if len(args) < 4:
        raise SystemExit("Usage: trello checklist item rename <card_id> <checklist> <item> <text>")
    card_id = _resolve_card(args[0])
    cl_id = _resolve_checklist(card_id, args[1])
    item_id = _resolve_checkitem(card_id, cl_id, args[2])
    new_name = _free_text(args[3:], "new item text")
    api.update_checkitem(card_id, item_id, name=new_name)
    print(f"Renamed item {short_id(item_id)} to: {new_name}")


def _checklist_item_check(args: list[str]) -> None:
    if len(args) < 3:
        raise SystemExit("Usage: trello checklist item check <card_id> <checklist> <item>")
    card_id = _resolve_card(args[0])
    cl_id = _resolve_checklist(card_id, args[1])
    item_id = _resolve_checkitem(card_id, cl_id, args[2])
    api.update_checkitem(card_id, item_id, state="complete")
    print(f"Checked {short_id(item_id)}.")


def _checklist_item_uncheck(args: list[str]) -> None:
    if len(args) < 3:
        raise SystemExit("Usage: trello checklist item uncheck <card_id> <checklist> <item>")
    card_id = _resolve_card(args[0])
    cl_id = _resolve_checklist(card_id, args[1])
    item_id = _resolve_checkitem(card_id, cl_id, args[2])
    api.update_checkitem(card_id, item_id, state="incomplete")
    print(f"Unchecked {short_id(item_id)}.")


def _checklist_item(args: list[str]) -> None:
    _dispatch("checklist item", {
        "add": _checklist_item_add,
        "delete": _checklist_item_delete,
        "rename": _checklist_item_rename,
        "check": _checklist_item_check,
        "uncheck": _checklist_item_uncheck,
    }, args)


def cmd_checklist(args: list[str]) -> None:
    _dispatch("checklist", {
        "ls": _checklist_ls,
        "add": _checklist_add,
        "delete": _checklist_delete,
        "rename": _checklist_rename,
        "item": _checklist_item,
    }, args, ls_takes_args=True)


# ── Comment subcommands ─────────────────────────────────────────────


def _comment_add(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello comment add <card_id> <text>")
    card_id = _resolve_card(args[0])
    api.add_comment(card_id, _free_text(args[1:], "comment text"))
    print("Comment added.")


def _comment_ls(args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: trello comment ls <card_id>")
    comments = api.get_comments(_resolve_card(args[0]))
    if _is_json():
        print_json(comments)
        return
    if not comments:
        print("  No comments.")
        return
    for c in comments:
        data = c.get("data", {})
        who = c.get("memberCreator", {}).get("username", "?")
        date = c.get("date", "")[:10]
        cid = short_id(c["id"])
        text = data.get("text", "")
        lines = text.splitlines()
        print(f"  {cid}  {date}  @{who}: {lines[0] if lines else ''}")
        if len(lines) > 1:
            pad = " " * (len(cid) + len(date) + len(who) + 8)
            for line in lines[1:]:
                print(f"  {pad}{line}")


def _comment_edit(args: list[str]) -> None:
    if len(args) < 3:
        raise SystemExit("Usage: trello comment edit <card_id> <comment_id> <new_text>")
    card_id = _resolve_card(args[0])
    comment_id = _resolve_comment(card_id, args[1])
    api.update_comment(comment_id, _free_text(args[2:], "comment text"))
    print(f"Comment {short_id(comment_id)} updated.")


def _comment_delete(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello comment delete <card_id> <comment_id>")
    card_id = _resolve_card(args[0])
    comment_id = _resolve_comment(card_id, args[1])
    api.delete_comment(comment_id)
    print(f"Comment {short_id(comment_id)} deleted.")


def cmd_comment(args: list[str]) -> None:
    _dispatch("comment", {
        "add": _comment_add,
        "ls": _comment_ls,
        "edit": _comment_edit,
        "delete": _comment_delete,
    }, args, ls_takes_args=True)


# ── Attachment subcommands ──────────────────────────────────────────


def _open_local(path: str) -> None:
    """Open a local file with the OS default application."""
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    else:
        subprocess.run(["xdg-open", path], check=False)


def _attachment_ls(args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: trello attachment ls <card_id>")
    card_id = _resolve_card(args[0])
    atts = api.get_attachments(card_id)
    if _is_json():
        print_json(atts)
        return
    if not atts:
        print("  No attachments.")
        return
    rows = []
    for a in atts:
        rows.append([
            "IMG" if is_image(a) else "",
            short_id(a["id"]),
            truncate(a.get("name") or a.get("url") or "(unnamed)", 50),
            size_str(a.get("bytes")),
        ])
    print_table(["Kind", "ID", "Name", "Size"], rows)


def _attachment_add(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit(
            "Usage: trello attachment add <card_id> <file_path_or_url> [name]"
        )
    card_id = _resolve_card(args[0])
    source = args[1]
    name = _free_text(args[2:], "an attachment name") if len(args) > 2 else None
    if source.startswith(("http://", "https://")):
        a = api.add_attachment_url(card_id, source, name=name)
    else:
        if not os.path.isfile(source):
            raise SystemExit(f"File not found: {source}")
        a = api.add_attachment_file(card_id, source, name=name)
    print(f"Attached {a.get('name') or source} ({short_id(a['id'])}) to {short_id(card_id)}.")


def _temp_cache_dir() -> str:
    """Shared cache for `attachment view/open/download` with no explicit dest.
    Regenerable scratch — `local gc` prunes it by age."""
    return os.path.join(tempfile.gettempdir(), "trello-cli")


def _safe_filename(name: str, fallback: str) -> str:
    """Sanitize a filename for safe path-joining: strip any directory components
    (so `reports/q3.pdf` → `q3.pdf`), replace unsafe chars, and drop leading dots
    so `..` can't escape. Same guard `_export_attachment_blobs` applies."""
    name = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().lstrip(".")
    return name or fallback


def _attachment_dest(att: dict, dest: str | None) -> str:
    """Resolve the destination path for download/open of an attachment."""
    raw = att.get("name") or os.path.basename(att.get("url", "")) or att["id"]
    filename = _safe_filename(raw, att["id"])
    if dest is None:
        tmp = _temp_cache_dir()
        os.makedirs(tmp, exist_ok=True)
        return os.path.join(tmp, f"{short_id(att['id'])}-{filename}")
    if os.path.isdir(dest):
        return os.path.join(dest, filename)
    return dest


def _attachment_download(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello attachment download <card_id> <attachment> [dest]")
    card_id = _resolve_card(args[0])
    att = _resolve_attachment(card_id, args[1])
    url = att.get("url")
    if not url:
        raise SystemExit("Attachment has no downloadable URL.")
    dest = _attachment_dest(att, args[2] if len(args) > 2 else None)
    api.download_attachment(url, dest, authed=att.get("isUpload", False))
    print(f"Downloaded to {dest}")


def _attachment_view(args: list[str]) -> None:
    """Download image(s) to a local cache and print the path(s), one per line.
    Defaults to every image on the card; pass an attachment to narrow it. The
    printed paths are what an agent (or `card show` reader) opens/reads."""
    if not args:
        raise SystemExit("Usage: trello attachment view <card_id> [attachment]")
    card_id = _resolve_card(args[0])
    if len(args) > 1:
        atts = [_resolve_attachment(card_id, args[1])]
    else:
        atts = [a for a in api.get_attachments(card_id) if is_image(a)]
        if not atts:
            print("  No image attachments.")
            return
    for a in atts:
        url = a.get("url")
        if not url:
            continue
        dest = _attachment_dest(a, None)
        api.download_attachment(url, dest, authed=a.get("isUpload", False))
        print(dest)


def _attachment_open(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello attachment open <card_id> <attachment>")
    card_id = _resolve_card(args[0])
    att = _resolve_attachment(card_id, args[1])
    url = att.get("url")
    if not url:
        raise SystemExit("Attachment has no URL to open.")
    # URL attachments (external links) open straight in the browser; uploaded
    # files need the OAuth header to fetch, so download to a temp file first.
    if not att.get("isUpload", False):
        webbrowser.open(url)
        print(f"Opened {att.get('name') or url} in browser.")
        return
    dest = _attachment_dest(att, None)
    api.download_attachment(url, dest, authed=True)
    _open_local(dest)
    print(f"Opened {att.get('name') or short_id(att['id'])} ({dest})")


def _attachment_rm(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello attachment rm <card_id> <attachment>")
    card_id = _resolve_card(args[0])
    att = _resolve_attachment(card_id, args[1])
    api.delete_attachment(card_id, att["id"])
    print(f"Removed attachment {short_id(att['id'])} from {short_id(card_id)}.")


def cmd_attachment(args: list[str]) -> None:
    _dispatch("attachment", {
        "ls": _attachment_ls,
        "add": _attachment_add,
        "view": _attachment_view,
        "open": _attachment_open,
        "download": _attachment_download,
        "rm": _attachment_rm,
    }, args, ls_takes_args=True)


# ── Local-backend setup ─────────────────────────────────────────────


def _local_init(args: list[str]) -> None:
    """Create a local-store folder. Persists nothing unless --set-default.

    `local init <scratch>` used to write `local_root` into ~/.trello-cli.json,
    which silently retargeted EVERY `--backend local` invocation on the machine —
    including other sessions already running, which then reported "Board not
    found" for a board that had not moved. Persisting a machine-wide default is
    now the deliberate act it always was, and the default behaviour just makes
    the folder and says how to address it per-invocation."""
    positional, flags = _parse_flags(args, bool_flags=("--set-default",))
    if len(positional) > 1:
        raise SystemExit("Usage: trello local init [path] [--set-default]")
    root = positional[0] if positional else config.get_local_root()
    root = os.path.abspath(os.path.expanduser(root))
    os.makedirs(root, exist_ok=True)

    if not flags.get("--set-default"):
        print(f"Local store ready: {root}")
        print("Nothing was persisted — no global setting changed.")
        print("\nUse it per-command:")
        print(f"  trello --backend local --local-root {root} <command>")
        print(f"  TRELLO_LOCAL_ROOT={root} trello --backend local <command>")
        print("\nMake it this machine's default (affects EVERY --backend local "
              "invocation\non this machine, including sessions already running):")
        print(f"  trello local init {root} --set-default")
        return

    previous = config.get_stored_local_root()
    config.set_local_root(root)
    if previous == root:
        print(f"Default local root unchanged: {root}  "
              f"(persisted in {config.CONFIG_PATH})")
    else:
        shown = previous if previous else "(unset — was the built-in default)"
        print(f"Default local root: {shown}  ->  {root}")
        print(f"Persisted in {config.CONFIG_PATH}.")
        print("This affects EVERY `--backend local` invocation on this machine, "
              "including\nsessions already running.")
        if previous:
            print(f"\nUndo with:  trello local init {previous} --set-default")
    print("\nUse it with:  trello --backend local <command>"
          "   (or set TRELLO_BACKEND=local)")


def _local_root(args: list[str]) -> None:
    """Read-only: which store the local backend would use, and who chose it.

    The recovery affordance for an agent whose root was retargeted out from under
    it — without this there is no way to discover the effective root at all."""
    if args:
        raise SystemExit("Usage: trello local root   (read-only; no arguments)")
    root = config.get_local_root()
    stored = config.get_stored_local_root()
    if _is_json():
        print_json({
            "root": root,
            "source": config.local_root_source(),
            "stored": stored,
            "config_path": str(config.CONFIG_PATH),
            "exists": os.path.isdir(root),
        })
        return
    print(f"Local store root: {root}")
    print(f"  from:     {config.local_root_source()}")
    print(f"  exists:   {'yes' if os.path.isdir(root) else 'NO — nothing there'}")
    print(f"  persisted in {config.CONFIG_PATH}: {stored or '(none)'}")
    print("\nOverride for one command with --local-root <path> or "
          "TRELLO_LOCAL_ROOT.\nChange the machine-wide default with "
          "`trello local init <path> --set-default`.")


def _opt_int(value: str | bool | None, flag: str) -> int | None:
    """Parse an optional non-negative int flag value (None if absent)."""
    if value is None:
        return None
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise SystemExit(f"{flag} requires an integer, got {value!r}")
    if n < 0:
        raise SystemExit(f"{flag} must be >= 0")
    return n


def _resolve_local_board(backend, ref: str) -> str:
    """Resolve a board ref (full/short id, or case-insensitive name prefix)
    against the *local* store, closed boards included. Mirrors
    `_resolve_board_ref` but targets the file store directly (the `local`
    commands operate on the store regardless of --backend selection)."""
    # Every board on disk — get_board reads board.json without the closed filter
    # get_boards applies, so an archived board is still reachable here (you may
    # well want to `local rm` one).
    boards = []
    for bid in backend.store.board_ids():
        if bid == ref or short_id(bid) == ref:
            return bid
        boards.append(backend.get_board(bid))
    lower = ref.lower()
    matches = [b for b in boards if (b.get("name") or "").lower().startswith(lower)]
    if len(matches) == 1:
        return matches[0]["id"]
    if len(matches) > 1:
        names = ", ".join(m["name"] for m in matches)
        raise SystemExit(f"Ambiguous board name '{ref}'. Matches: {names}")
    raise SystemExit(
        f"Board not found in local store: {ref}"
        f"\nSearched local store: {backend.store.root}"
        f"  (local_root from {config.local_root_source()})"
        "\nWrong store? Run `trello local root`; override per-command with "
        "--local-root <path>."
    )


def _prune_temp_cache(days: int, apply: bool) -> dict:
    """Prune the attachment temp cache of files older than `days` (0 = all).
    Reports {files, bytes}; deletes only when `apply`."""
    tmp = _temp_cache_dir()
    files: list[str] = []
    freed = 0
    if not os.path.isdir(tmp):
        return {"files": files, "bytes": freed}
    cutoff = time.time() - days * 86400
    for name in sorted(os.listdir(tmp)):
        path = os.path.join(tmp, name)
        if not os.path.isfile(path):
            continue
        if days == 0 or os.path.getmtime(path) < cutoff:
            freed += os.path.getsize(path)
            files.append(path)
            if apply:
                try:
                    os.remove(path)
                except OSError:
                    pass
    return {"files": files, "bytes": freed}


def _local_gc(args: list[str]) -> None:
    positional, flags = _parse_flags(
        args,
        bool_flags=("--apply",),
        value_flags=("--activity-keep", "--cache-days"),
    )
    if positional:
        raise SystemExit(
            "Usage: trello [--board <board>] local gc "
            "[--apply] [--activity-keep <n>] [--cache-days <n>]"
        )
    apply = bool(flags.get("--apply"))
    activity_keep = _opt_int(flags.get("--activity-keep"), "--activity-keep")
    cache_days = _opt_int(flags.get("--cache-days"), "--cache-days")
    if cache_days is None:
        cache_days = 7

    from .backends.local import LocalBackend

    backend = LocalBackend(config.get_local_root())
    override = config.get_board_override()
    board_id = _resolve_local_board(backend, override) if override else None
    report = backend.gc(board_id=board_id, apply=apply, activity_keep=activity_keep)
    cache = _prune_temp_cache(cache_days, apply)
    report["cache_files"] = len(cache["files"])
    report["cache_bytes"] = cache["bytes"]
    if _is_json():
        print_json(report)
        return
    if board_id:
        print(f"(scope: board {short_id(board_id)} — temp cache is global)")
    _print_gc_report(report, apply)


def _print_gc_report(report: dict, apply: bool) -> None:
    removed = (len(report["orphan_dirs"]) + len(report["orphan_files"])
               + report["cache_files"] + report["activity_trimmed"])
    if not removed:
        print("Nothing to clean.")
        return
    print("Removed:" if apply else "Would remove:")
    print(f"  {len(report['orphan_dirs'])} orphaned attachment dir(s)")
    print(f"  {len(report['orphan_files'])} orphaned blob file(s)")
    print(f"  {report['cache_files']} temp-cache file(s)")
    if report["activity_trimmed"]:
        print(f"  {report['activity_trimmed']} activity-log line(s) trimmed")
    print(f"  {size_str(report['bytes'] + report['cache_bytes']) or '0B'} reclaimed")
    if not apply:
        print("\nDry run — re-run with --apply to delete.")


def _local_rm(args: list[str]) -> None:
    positional, flags = _parse_flags(args, bool_flags=("--yes",))
    if len(positional) != 1:
        raise SystemExit("Usage: trello local rm <board> --yes")

    from .backends.local import LocalBackend

    backend = LocalBackend(config.get_local_root())
    board_id = _resolve_local_board(backend, positional[0])
    apply = bool(flags.get("--yes"))
    report = backend.delete_board(board_id, apply=apply)
    if _is_json():
        print_json(report)
        return
    verb = "Deleted" if apply else "Would delete"
    print(
        f"{verb} board '{report['name']}' ({short_id(report['id'])}): "
        f"{report['cards']} card(s), {report['attachments']} attachment(s), "
        f"{size_str(report['bytes'])}"
    )
    if not apply:
        print("\nNot deleted — re-run with --yes to confirm.")


def cmd_local(args: list[str]) -> None:
    _dispatch("local", {
        "init": _local_init,
        "root": _local_root,
        "gc": _local_gc,
        "rm": _local_rm,
    }, args)


# ── Export (pull a board into the local file store) ─────────────────


def _export_attachment_blobs(backend, board_id: str, cards: list[dict], *,
                             fetch_relative: bool = False) -> dict:
    """Pull uploaded attachment blobs from the source backend into the target local
    store so the exported copy is usable offline.

    For every attachment with `isUpload` and an http(s) `url`, the blob is fetched
    via the *source* backend (`api.download_attachment`, authed — Trello uploads
    need the OAuth header) into `<root>/<boardId>/attachments/<cardId>/` and its
    `url` is rewritten root-relative (matching `add_attachment_file`), so the
    stored card points at the local file. URL attachments (`isUpload` False) are
    already portable and left untouched. Best-effort: a per-blob failure warns,
    drops any partial file, and keeps the remote url so metadata still exports.
    Trello blobs are immutable by id, so any blob already on disk for that id is
    reused (skipped) on re-export — even if the attachment was renamed upstream,
    which avoids a needless re-download. Mutates `cards` in place; returns
    per-blob counts.

    `fetch_relative` also pulls attachments whose url is already store-relative,
    which a `--fork` needs: those paths are rooted at the SOURCE board's id (an
    http source serves them straight out of its own store), so leaving them alone
    would point the fork's cards into a different board's blob dir — or nothing at
    all. A mirror re-export keeps skipping them: there the source id *is* the
    destination id, so the blob is already exactly where it belongs."""
    counts = {"downloaded": 0, "skipped": 0, "failed": 0}
    root = backend.store.root
    for card in cards:
        for att in card.get("attachments", []):
            url = att.get("url")
            if not att.get("isUpload") or not url:
                continue
            remote = str(url).lower().startswith(("http://", "https://"))
            if not remote and not fetch_relative:
                continue  # already a local path (e.g. re-export of a local source)
            dest_dir = backend.store.attachments_dir(board_id, card["id"])
            # Reuse any complete blob already downloaded for this id (the filename
            # may differ if it was renamed upstream). The ".part" temp below has no
            # dash after the id, so it never matches this id-prefix glob.
            cached = next(
                (p for p in sorted(dest_dir.glob(f"{att['id']}-*"))
                 if p.is_file() and p.stat().st_size > 0),
                None,
            ) if dest_dir.exists() else None
            if cached is not None:
                att["url"] = cached.relative_to(root).as_posix()
                counts["skipped"] += 1
                continue
            name = _safe_filename(att.get("name") or url, att["id"])  # path-safety guard
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{att['id']}-{name}"
            tmp = dest_dir / f"{att['id']}.part"  # stream here, then os.replace (atomic; no truncated cache)
            try:
                api.download_attachment(url, str(tmp), authed=True)
                os.replace(tmp, dest)
                att["url"] = dest.relative_to(root).as_posix()
                counts["downloaded"] += 1
            except Exception as e:
                # A store-relative url that failed to copy is NOT "kept remote":
                # it still names the source board's store, which is the very
                # cross-link fetch_relative exists to break. Say which it is.
                kept = ("keeping remote url" if remote else
                        "url still points into the source board's store")
                print(f"  warning: could not download attachment {short_id(att['id'])} "
                      f"({name}): {e} — {kept}", file=sys.stderr)
                try:
                    if tmp.is_file():
                        tmp.unlink()
                except OSError:
                    pass
                counts["failed"] += 1
    return counts


def _preserve_local_attachment_urls(backend, board_id: str, cards: list[dict]) -> None:
    """On a `--no-attachments` re-export, keep attachment urls that a previous
    export already localized (a store-relative path) instead of overwriting them
    with the source backend's remote (auth-gated) url — which would orphan the
    on-disk blob. Mutates `cards` in place. No-op on a first export (board absent).
    """
    try:
        existing = backend.get_board_cards(board_id, card_filter="all")
    except SystemExit:
        return  # board not in the store yet — nothing to preserve
    stored: dict[str, str] = {}
    for c in existing:
        for a in c.get("attachments", []):
            url = a.get("url") or ""
            if url and not str(url).lower().startswith(("http://", "https://")):
                stored[a["id"]] = url
    if not stored:
        return
    for card in cards:
        for att in card.get("attachments", []):
            local = stored.get(att.get("id"))
            if local:
                att["url"] = local


def _gather_board(board_id: str) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """Read a full snapshot of a board from the active backend: board meta, open
    lists, labels, and every card (visible + closed) merged with its detail and
    full comment thread. Shared by both export directions.

    Every card, visible + closed. The filtered listings drop the closed flag, so
    stamp it. board-cards carries `pos` (get_card omits it); get_card supplies
    desc plus checklists/attachments inline (both backends do); get_comments adds
    the comment thread."""
    board = api.get_board(board_id)
    lists = api.get_lists(board_id)
    labels = api.get_labels(board_id)
    summaries: list[dict] = []
    for card_filter, closed in (("visible", False), ("closed", True)):
        for c in api.get_board_cards(board_id, card_filter=card_filter):
            summaries.append({**c, "closed": closed})
    cards = []
    for c in summaries:
        merged = {**api.get_card(c["id"]), **c}  # board-cards wins (pos, closed)
        comments = api.get_comments(c["id"], limit=1000)
        if len(comments) >= 1000:
            print(f"  warning: card {short_id(c['id'])} has >= 1000 comments; only "
                  "the newest 1000 were exported", file=sys.stderr)
        merged["comments"] = comments
        cards.append(merged)
    return board, lists, labels, cards


def _card_label_ids(card: dict) -> list[str]:
    """A card's label ids regardless of source shape: `idLabels` if present
    (Trello-shaped), else the ids of the resolved `labels` dicts (local-shaped)."""
    ids = card.get("idLabels")
    if ids is None:
        ids = [lb["id"] for lb in card.get("labels", []) if lb.get("id")]
    return list(ids)


def _pos_str(pos) -> str | None:
    """Stringify a numeric `pos` for the Trello API (which accepts a number to
    place an item exactly); non-numeric/absent → None (Trello defaults to bottom)."""
    return str(pos) if isinstance(pos, (int, float)) and not isinstance(pos, bool) else None


def _comment_provenance(cm: dict) -> str:
    """A short Markdown prefix preserving a re-posted comment's original author and
    date — Trello posts comments as the token user with a fresh timestamp, so the
    only way to keep the who/when is to fold it into the body."""
    mc = cm.get("memberCreator") or {}
    who = mc.get("fullName") or mc.get("username") or cm.get("idMemberCreator") or "unknown"
    when = str(cm.get("date") or "")[:10]  # YYYY-MM-DD
    tag = f"_originally {who}" + (f", {when}" if when else "") + "_"
    return tag + "\n\n"


def _push_attachment(dest, source_root: str, card_id: str, att: dict,
                     with_attachments: bool, counts: dict) -> None:
    """Re-create one attachment on the new Trello card. Uploaded local blobs are
    re-uploaded (`add_attachment_file`); external URL attachments are re-linked
    (`add_attachment_url`). Best-effort: a per-attachment failure warns and bumps
    `failed` rather than aborting the export."""
    url = att.get("url")
    if not url:
        return
    name = att.get("name")
    is_upload = bool(att.get("isUpload"))
    is_remote = str(url).lower().startswith(("http://", "https://"))
    try:
        if is_upload and not is_remote:
            # Local blob (root-relative or absolute path) → upload the file itself.
            if not with_attachments:
                counts["skipped"] += 1
                return
            path = url if os.path.isabs(url) else os.path.join(source_root, url)
            if not os.path.isfile(path):
                print(f"  warning: attachment blob missing on disk: {url} — skipping",
                      file=sys.stderr)
                counts["failed"] += 1
                return
            dest.add_attachment_file(card_id, path, name=name)
            counts["uploaded"] += 1
        else:
            # External URL attachment, or an uploaded blob we only have a remote
            # (auth-gated) url for (source exported --no-attachments) → re-link it.
            dest.add_attachment_url(card_id, url, name=name)
            counts["linked"] += 1
    except Exception as e:
        print(f"  warning: could not push attachment {short_id(att.get('id', ''))} "
              f"({name or url}): {e}", file=sys.stderr)
        counts["failed"] += 1


def _push_card(dest, source_root: str, card: dict, new_list: str,
               label_map: dict, counts: dict, with_attachments: bool) -> None:
    """Create one card and its children — dueComplete, comments, checklists+items,
    attachments — on the new Trello board, then archive it if it was archived.
    Bumps `counts` in place. Raises on a Trello error so the caller can
    warn-and-continue rather than aborting the whole push."""
    new_label_ids = [label_map[i] for i in _card_label_ids(card) if i in label_map]
    created = dest.create_card(
        new_list, card.get("name", ""),
        desc=card.get("desc") or None,
        due=card.get("due") or None,
        labels=new_label_ids or None,
        pos=_pos_str(card.get("pos")),
    )
    new_card_id = created["id"]
    counts["cards"] += 1
    # Trello rejects dueComplete without a due date; the local store doesn't enforce
    # that invariant, so only set it when there's actually a due date to complete.
    if card.get("due") and card.get("dueComplete"):
        dest.update_card(new_card_id, dueComplete="true")
    # Comments, oldest first, each prefixed with its original author/date.
    for cm in sorted(card.get("comments", []), key=lambda x: x.get("date", "")):
        text = (cm.get("data") or {}).get("text") or ""
        if not text:
            continue
        dest.add_comment(new_card_id, _comment_provenance(cm) + text)
        counts["comments"] += 1
    # Checklists and their items, preserving order and completion state.
    for cl in sorted(card.get("checklists", []), key=lambda x: x.get("pos", 0)):
        new_cl = dest.create_checklist(new_card_id, cl.get("name", ""))
        counts["checklists"] += 1
        for it in sorted(cl.get("checkItems", []), key=lambda x: x.get("pos", 0)):
            new_item = dest.add_checkitem(new_cl["id"], it.get("name", ""))
            if it.get("state") == "complete":
                dest.update_checkitem(new_card_id, new_item["id"], state="complete")
    for att in card.get("attachments", []):
        _push_attachment(dest, source_root, new_card_id, att, with_attachments,
                         counts["attachments"])
    # Archive last: the card (and its children) must exist before it's closed.
    if card.get("closed"):
        dest.archive_card(new_card_id)


def _push_board_to_trello(dest, source_root: str, board: dict, lists: list[dict],
                          labels: list[dict], cards: list[dict], name: str,
                          with_attachments: bool) -> dict:
    """Create a brand-new Trello board from a local snapshot and return counts.

    Trello mints its own ids, so we build old→new maps for labels and lists as we
    create them, then re-create each card and its children (comments,
    checklists+items, attachments) under the new ids. Create-new-each-time:
    non-idempotent by design — re-running makes another board (see DESIGN/README)."""
    new_board = dest.create_board(name, desc=board.get("desc") or None,
                                  default_lists=False)
    new_board_id = new_board["id"]
    # Surface the new board's id/url before the card loop: this is
    # create-new-each-time, so a mid-push failure (rate-limit, network) can't be
    # resumed — printing it up front tells the user which half-built board to delete.
    print(f"  creating Trello board {short_id(new_board_id)} "
          f"{new_board.get('shortUrl', '')}".rstrip(), file=sys.stderr)

    label_map: dict[str, str] = {}
    for lb in labels:
        color = lb.get("color") or None
        try:
            created = dest.create_label(new_board_id, lb.get("name", ""), color)
        except Exception as e:
            # A locally-invented color Trello rejects shouldn't abort the export;
            # retry colorless so the label (and its card assignments) still land.
            if color is None:
                raise
            print(f"  warning: label color {color!r} rejected ({e}) — creating "
                  f"'{lb.get('name', '')}' without a color", file=sys.stderr)
            created = dest.create_label(new_board_id, lb.get("name", ""), None)
        label_map[lb["id"]] = created["id"]

    list_map: dict[str, str] = {}
    for l in sorted(lists, key=lambda x: x.get("pos", 0)):
        created = dest.create_list(new_board_id, l.get("name", ""),
                                   pos=_pos_str(l.get("pos")))
        list_map[l["id"]] = created["id"]

    counts = {
        "lists": len(list_map), "labels": len(label_map),
        "cards": 0, "comments": 0, "checklists": 0,
        "attachments": {"uploaded": 0, "linked": 0, "failed": 0, "skipped": 0},
    }

    for card in sorted(cards, key=lambda c: c.get("pos", 0)):
        new_list = list_map.get(card.get("idList") or "")
        if new_list is None:
            # Card in a closed/unexported list (only open lists are pushed, mirroring
            # --to local). Skip it rather than orphan it.
            print(f"  warning: skipping card {short_id(card['id'])} "
                  f"({truncate(card.get('name', ''), 40)}) — its list was not exported",
                  file=sys.stderr)
            continue
        # Best-effort, like attachments: a single bad card warns and continues
        # rather than aborting the push and orphaning the rest of the board.
        try:
            _push_card(dest, source_root, card, new_list, label_map, counts,
                       with_attachments)
        except Exception as e:
            print(f"  warning: could not push card {short_id(card['id'])} "
                  f"({truncate(card.get('name', ''), 40)}): {e}", file=sys.stderr)

    return {"id": new_board_id, "name": name,
            "shortUrl": new_board.get("shortUrl", ""), **counts}


_EXPORT_USAGE = (
    "Usage: trello --board <board> export [--to local|trello] "
    "[--fork] [--name <name>] [--no-attachments]\n"
    "  --to local  (default): pull the board into the local file store "
    "(source = --backend, default trello). Ids are preserved, so re-running it "
    "refreshes the same board in place.\n"
    "  --to trello: push a local board up to Trello as a new board "
    "(source must be --backend local).\n"
    "  --fork (--to local only): give the copy NEW ids, so it becomes a separate "
    "board instead of the source's mirror. Permanent: no later export tracks a "
    "forked board, and forking twice makes two boards. Pair with --name."
)


def cmd_export(args: list[str]) -> None:
    # Every flag here is irreversible in one direction or another, and this usage
    # text is the only place they are written down — answering the first thing
    # anyone types with "Unknown flag: --help" hides exactly that. (`search` is
    # special-cased for the same reason; here there is no query to confuse it
    # with, so `help` is taken too.)
    if len(args) == 1 and args[0] in ("--help", "-h", "help"):
        print(_EXPORT_USAGE)
        return
    positional, flags = _parse_flags(
        args, bool_flags=("--no-attachments", "--fork"),
        value_flags=("--to", "--name"),
    )
    if positional:
        raise SystemExit(_EXPORT_USAGE)
    target = str(flags.get("--to") or "local").lower()
    if target == "local":
        _export_to_local(flags)
    elif target == "trello":
        _export_to_trello(flags)
    else:
        raise SystemExit(
            f"Unsupported export target: {target!r}. Use '--to local' (pull a board "
            "into the file store) or '--to trello' (push a local board up to Trello)."
        )


def _fork_snapshot(lists: list[dict], labels: list[dict], cards: list[dict],
                   new_id) -> tuple[list[dict], list[dict], list[dict]]:
    """Re-id a gathered snapshot so a fork shares no entity id with its source.

    The board id is not the only one that has to change. `LocalBackend` finds an
    entity by scanning EVERY board and taking the first hit (`_locate_card`,
    `_locate_list`, `_locate_comment`, `_locate_checklist`), which is sound only
    because ids are unique store-wide — Trello's are, and `new_id()` is random. A
    fork that kept its source's ids would break that invariant, and a store
    holding both a fork and a mirror of one source would then route every
    id-addressed write (`card rename`, `comment add`, `checklist item check`, …)
    to whichever board id sorts first, silently ignoring `--board`.

    So everything is reminted and the cross-references are rewritten to match:
    `idList` and `idLabels` through the list/label maps, `idCard`/`idChecklist`
    down the checklist tree. Ids that do not name store entities (members,
    Trello's `shortLink`/`shortUrl`) are left alone. Returns new dicts — the
    caller's snapshot is not mutated."""
    list_map = {l["id"]: new_id() for l in lists}
    label_map = {lb["id"]: new_id() for lb in labels}
    out_lists = [{**l, "id": list_map[l["id"]]} for l in lists]
    out_labels = [{**lb, "id": label_map[lb["id"]]} for lb in labels]

    out_cards = []
    for card in cards:
        cid = new_id()
        # A card in a list that wasn't exported keeps its old idList and is
        # dropped by import_board's list-scoped reads, exactly as for a mirror.
        new_card = {**card, "id": cid,
                    "idList": list_map.get(card.get("idList", ""), card.get("idList", "")),
                    # Drop the resolved `labels` dicts: they carry the source's
                    # label ids, and `_to_store_card` prefers `idLabels` anyway.
                    "idLabels": [label_map[i] for i in _card_label_ids(card)
                                 if i in label_map]}
        new_card.pop("labels", None)
        new_card["comments"] = [{**cm, "id": new_id()}
                                for cm in card.get("comments") or []]
        new_card["attachments"] = [{**a, "id": new_id()}
                                   for a in card.get("attachments") or []]
        checklists = []
        for cl in card.get("checklists") or []:
            clid = new_id()
            checklists.append({
                **cl, "id": clid, "idCard": cid,
                "checkItems": [{**it, "id": new_id(), "idChecklist": clid}
                               for it in cl.get("checkItems") or []],
            })
        new_card["checklists"] = checklists
        out_cards.append(new_card)
    return out_lists, out_labels, out_cards


def _export_name(flags: dict) -> str:
    """The `--name` override for either export direction, guarded.

    `_parse_flags` takes the token after `--name` verbatim, so `--name --fork`
    would name a board `--fork` — the same "invented flags never become data" hole
    `_free_text` closes for positional text. It can't be `_free_text` itself here:
    that guard's escape hatch is a bare `--` *earlier in argv*, which `--name`
    (exactly one token, consumed by `_parse_flags`) can never reach, so it would
    advise a fix that does not work. Empty when unset."""
    raw = str(flags.get("--name") or "")
    if raw.startswith("--"):
        raise SystemExit(
            f"Expected a board name after --name, got the flag {raw}. This CLI "
            "takes values positionally — quote the text as one argument.\n"
            "A name that really does start with dashes can't be passed here; "
            "export it, then `trello --backend local --board <id> board rename`."
        )
    return raw


def _export_to_local(flags: dict) -> None:
    fork = bool(flags.get("--fork"))
    # Validate the flags before touching the network: a bad --name should say so,
    # not surface as whatever the first real step happens to complain about.
    name = _export_name(flags)
    if "--name" in flags and not fork:  # presence, so --name "" is refused too
        # A rename on a mirror would just be overwritten by the next re-export
        # (which re-imports the source's name); on a fork it sticks, and it is the
        # obvious pairing — two boards with one name is the confusion fork fixes.
        raise SystemExit(
            "--name applies to export --to trello, or to export --to local --fork. "
            "A plain --to local export mirrors the source board, so its name comes "
            "from the source and a rename would not survive the next export."
        )
    if config.get_backend_name() == "local":
        # Source and target would be the same store (same local_root) — the prune
        # step would then delete from the very files it just read. Export is a pull
        # from a remote backend; run it with --backend trello (the default).
        raise SystemExit(
            "export --to local pulls a board *into* the local store, so the source "
            "must be a remote backend. Run it with --backend trello (the default), "
            "not local."
        )
    board_id = _require_board()
    board, lists, labels, cards = _gather_board(board_id)

    from .backends.local import LocalBackend
    from .backends.store import new_id

    backend = LocalBackend(config.get_local_root())
    # Mint the destination id ONCE, up front. The board id is a path component
    # (<root>/<bid>/attachments/<cardId>/), so both attachment helpers below run
    # against the *destination* board, not the source: minting it later (inside
    # import_board) would strand every blob under the source id, and passing the
    # source id to _preserve_local_attachment_urls would seed a fork's cards with
    # urls pointing into the source board's blob dir.
    dest_id = new_id() if fork else board["id"]
    if fork:
        # Before the attachment step: blob dirs are keyed by CARD id and blob
        # filenames by ATTACHMENT id, both of which change here.
        lists, labels, cards = _fork_snapshot(lists, labels, cards, new_id)
    if name:
        board = {**board, "name": name}
    if flags.get("--no-attachments"):
        blobs = None
        if fork and any(a.get("isUpload")
                        for c in cards for a in c.get("attachments") or []):
            # A fresh id means there is no prior copy to preserve urls from — and
            # crucially nothing to preserve them from the *source* board either.
            print("  note: --fork with --no-attachments is permanent — no later "
                  "export tracks a forked board, so these blobs are never "
                  "downloaded and the fork's attachment urls keep pointing at "
                  "the source.", file=sys.stderr)
        else:
            # Don't downgrade already-localized attachment urls back to the source's
            # (auth-gated) remote urls, which would orphan the on-disk blobs.
            _preserve_local_attachment_urls(backend, dest_id, cards)
    else:
        # Download blobs before import so import_board persists the rewritten
        # (local) urls.
        blobs = _export_attachment_blobs(backend, dest_id, cards,
                                         fetch_relative=fork)
    result = backend.import_board(board, lists, labels, cards, board_id=dest_id)
    # Stable JSON shape: always present, zeroed when --no-attachments skipped it.
    result["attachments"] = blobs or {"downloaded": 0, "skipped": 0, "failed": 0}
    result["forked"] = fork
    result["sourceId"] = board_id
    if _is_json():
        print_json(result)
        return
    verb = "Forked" if fork else "Exported"
    print(
        f"{verb} '{result['name']}' ({short_id(result['id'])}) to {config.get_local_root()}\n"
        f"  {result['lists']} lists, {result['cards']} cards, "
        f"{result['labels']} labels, {result['comments']} comments\n"
        f"Browse it:  trello --backend local --board {short_id(result['id'])} list ls"
    )
    if fork:
        print(
            f"  new board id — not a mirror of {short_id(board_id)}: no later export "
            "tracks this copy, and forking again makes another board."
        )
    if blobs and (blobs["downloaded"] or blobs["skipped"] or blobs["failed"]):
        parts = [f"{blobs['downloaded']} downloaded"]
        if blobs["skipped"]:
            parts.append(f"{blobs['skipped']} cached")
        if blobs["failed"]:
            parts.append(f"{blobs['failed']} failed (kept remote url)")
        print(f"  attachment blobs: {', '.join(parts)}")


def _export_to_trello(flags: dict) -> None:
    if flags.get("--fork"):
        # Not a silent no-op: Trello mints its own ids, so this direction is
        # *already* create-new-each-time — --fork would read as if the default
        # were an in-place update.
        raise SystemExit(
            "--fork applies to export --to local. export --to trello already "
            "creates a brand-new board on every run (Trello mints its own ids, "
            "so they can never be preserved)."
        )
    name_override = _export_name(flags)  # validated before any network work
    if config.get_backend_name() != "local":
        # The reverse pushes the *local* store up to Trello; the source must be the
        # file store, so the active backend has to be local.
        raise SystemExit(
            "export --to trello pushes the *local* store up to Trello, so the source "
            "must be the local backend. Run it with --backend local."
        )
    board_id = _require_board()
    board, lists, labels, cards = _gather_board(board_id)
    name = name_override or str(board.get("name") or "Exported board")
    with_attachments = not flags.get("--no-attachments")
    source_root = config.get_local_root()

    from .backends.trello import TrelloBackend

    dest = TrelloBackend()
    result = _push_board_to_trello(
        dest, source_root, board, lists, labels, cards, name, with_attachments,
    )
    if _is_json():
        print_json(result)
        return
    att = result["attachments"]
    print(
        f"Pushed '{result['name']}' up to Trello as new board {short_id(result['id'])}\n"
        f"  {result['lists']} lists, {result['cards']} cards, {result['labels']} labels, "
        f"{result['comments']} comments, {result['checklists']} checklists\n"
        f"  {result['shortUrl']}".rstrip()
    )
    if att["uploaded"] or att["linked"] or att["failed"] or att["skipped"]:
        parts = []
        if att["uploaded"]:
            parts.append(f"{att['uploaded']} uploaded")
        if att["linked"]:
            parts.append(f"{att['linked']} linked")
        if att["skipped"]:
            parts.append(f"{att['skipped']} skipped (--no-attachments)")
        if att["failed"]:
            parts.append(f"{att['failed']} failed")
        print(f"  attachments: {', '.join(parts)}")


# ── Web server ──────────────────────────────────────────────────────


def cmd_serve(args: list[str]) -> None:
    positional, flags = _parse_flags(
        args, bool_flags=("--no-browser",),
        value_flags=("--port", "--host", "--token", "--allow-host"),
    )
    if positional:
        raise SystemExit(
            "Usage: trello serve [--port <n>] [--host <addr>] [--token <t>] "
            "[--no-browser] [--allow-host <h1,h2>]"
        )
    try:
        from .web.server import serve
    except ModuleNotFoundError:
        raise SystemExit(
            "The web app needs extra dependencies. Install them with:\n"
            "    pip install trello-cli[web]"
        )
    port_raw = flags.get("--port")
    try:
        port = int(port_raw) if port_raw is not None else 8787
    except (TypeError, ValueError):
        raise SystemExit(f"Invalid --port: {port_raw!r}")
    host = flags.get("--host") or "127.0.0.1"
    token_raw = flags.get("--token")
    token = str(token_raw) if token_raw else None
    # Extra Host-header names to accept (comma-separated) — the public
    # domain(s) a reverse proxy forwards when this binds loopback behind it.
    allow_raw = flags.get("--allow-host")
    allow_hosts = tuple(
        h.strip() for h in str(allow_raw).split(",") if h.strip()
    ) if allow_raw else ()
    serve(host=str(host), port=port, token=token,
          open_browser=not flags.get("--no-browser"),
          allow_hosts=allow_hosts)


# ── Workflow commands ───────────────────────────────────────────────

def _grab_resolve_list(board_id: str, name: str, defaulted: bool) -> str:
    """Resolve a grab list, hinting at --from/--to when a *defaulted* name (the
    board has no "To Do"/"Doing") is what failed."""
    try:
        return _resolve_list(board_id, name)
    except SystemExit as e:
        if defaulted:
            raise SystemExit(f"{e} (couldn't resolve the default '{name}' list; "
                             "pass --from/--to to name your lists)")
        raise


def cmd_grab(args: list[str]) -> None:
    positional, flags = _parse_flags(args, value_flags=("--from", "--to"))
    if positional:
        raise SystemExit("Usage: trello grab [--from <list>] [--to <list>]")
    from_flag, to_flag = flags.get("--from"), flags.get("--to")
    src_name = str(from_flag or "To Do")
    dst_name = str(to_flag or "Doing")
    board_id = _require_board()
    src_id = _grab_resolve_list(board_id, src_name, defaulted=from_flag is None)
    dst_id = _grab_resolve_list(board_id, dst_name, defaulted=to_flag is None)
    if src_id == dst_id:
        raise SystemExit("--from and --to resolve to the same list.")
    card = api.grab_top_card(src_id, dst_id)
    if _is_json():
        print_json(card)
        if card is None:
            sys.exit(1)
        return
    if card is None:
        print(f"Nothing to grab in '{src_name}'.")
        sys.exit(1)
    names = {l["id"]: l["name"] for l in api.get_lists(board_id)}
    print(f"Grabbed: {card['name']}")
    print(f"  ID:    {short_id(card['id'])} ({card['id']})")
    print(f"  Moved: {names.get(src_id, src_name)} -> {names.get(dst_id, dst_name)}")
    # Only the Trello backend claims by commenting, and that comment is never
    # retracted on a win — print the id so the caller can later tell the claim
    # on the card is its own rather than a rival's (see base.py's `claimId`).
    claim = card.get("claimId")
    if claim:
        print(f"  Claim: {claim} (the claim comment with this id on the card is yours)")


# ── Find ────────────────────────────────────────────────────────────

# Operators Trello's index implements but the local store can't (see the operator
# table in backends/local.py, which is the authority — tests assert these agree).
# Detected only to HINT: the query still runs, they're just literal text locally.
_TRELLO_ONLY_OPS = ("created", "member", "board")
_TRELLO_ONLY_OP_RE = re.compile(
    r"(?:^|\s)-?(?:" + "|".join(_TRELLO_ONLY_OPS) + r"):", re.IGNORECASE)


def _search_hints(query: str, backend: str, found: int, substring: bool) -> list[str]:
    """Backend-specific hints for a search, gated on what the query actually used.

    The two backends genuinely differ (Trello has operators and fuzzy matching;
    only the local store can match mid-word), and the CLI surface is the only
    documentation an agent caller ever reads — so say so at the moment it
    matters, not only in --help."""
    # `http` is deliberately absent from both gates: its semantics are the
    # SERVER's backend's, which this side can't see — a server on a local store
    # honours --substring, one fronting Trello doesn't. Guessing would mean
    # telling half of those users something false.
    hints: list[str] = []
    if backend == "local" and _TRELLO_ONLY_OP_RE.search(query):
        hints.append(
            "Note: " + "/".join(f"{o}:" for o in _TRELLO_ONLY_OPS)
            + " are Trello-backend operators; on the local backend they're "
              "matched as literal text (so they narrow to nothing rather than "
              "being ignored)."
        )
    if found or substring:
        return hints
    if backend == "trello":
        hints.append(
            "Trello matches whole words (--partial for word-prefix). Mid-word "
            "matching needs the local backend."
        )
    elif backend == "local":
        hints.append(
            "Whole-word match. Try --partial for word-prefixes, or --substring "
            "for mid-word matches (e.g. 'crollba' finding 'scrollbar')."
        )
    return hints


def cmd_search(args: list[str]) -> None:
    # `search --help` is the first thing anyone tries on a command they just
    # discovered; letting _parse_flags answer it with "Unknown flag: --help"
    # burns a turn and teaches nothing. Only the DASHED forms, and only alone:
    # "help" is an ordinary word, so `trello search help` searches for it
    # (`trello help search` is the undashed way to ask).
    if len(args) == 1 and args[0] in ("--help", "-h"):
        # Not a noun group, so it takes a query rather than a verb —
        # _print_group_help's "<verb>" header would be a lie.
        print("Usage: trello [--board <name_or_id>] [--json] search <query> [flags]")
        print()
        print(_usage_section("search"))
        return
    positional, flags = _parse_flags(
        args,
        bool_flags=("--all", "--partial", "--substring"),
        value_flags=("--list",),
    )
    if not positional:
        raise SystemExit(
            "Usage: trello --board <board> search <query> [--list <list>] "
            "[--all] [--partial] [--substring]\n"
            "Searches card names, descriptions, comments and checklists.\n"
            'Example: trello --board Roadmap search "safari cookie"'
        )
    query = " ".join(positional)
    board_id = _require_board()
    list_ref = flags.get("--list")
    list_id = _resolve_list(board_id, str(list_ref)) if list_ref else None
    substring = bool(flags.get("--substring"))
    cards = api.search_cards(
        board_id, query,
        list_id=list_id,
        include_closed=bool(flags.get("--all")),
        partial=bool(flags.get("--partial")),
        substring=substring,
    )
    backend = config.get_backend_name()
    hints = _search_hints(query, backend, len(cards), substring)

    if _is_json():
        print_json(cards)
        # stdout stays a clean JSON array — notes go to stderr, same split
        # `card ls` uses for its truncation notice.
        for hint in hints:
            print(hint, file=sys.stderr)
        return

    if not cards:
        print(f'No cards matching "{query}".')
        for hint in hints:
            print(f"  {hint}")
        return

    names = {l["id"]: l["name"] for l in api.get_lists(board_id)}
    rows = [
        [
            short_id(c["id"]),
            truncate(names.get(c.get("idList"), "?"), 18),
            (c.get("dateLastActivity") or "")[:10],
            truncate(c["name"], 50),
            label_str(c.get("labels", [])),
            due_str(c.get("due"), c.get("dueComplete", False)),
        ]
        for c in cards
    ]
    print_table(["ID", "List", "Activity", "Name", "Labels", "Due"], rows)

    # A hit in a description/comment/checklist is an unexplained row without the
    # line that matched — the table only shows the name.
    context = [c for c in cards if (c.get("_match") or {}).get("line")]
    if context:
        print()
        print("  Matches:")
        for c in context:
            m = c["_match"]
            print(f"    {short_id(c['id'])}  ({m['field']}) "
                  f"{truncate(m['line'], 70)}")
    for hint in hints:
        print(f"\n  {hint}")


# ── Command dispatch ────────────────────────────────────────────────

COMMANDS = {
    "configure": cmd_configure,
    "configure-http": cmd_configure_http,
    "boards": cmd_boards,
    "search": cmd_search,
    "find": cmd_search,  # the other obvious guess; both reach the same command
    "local": cmd_local,
    "export": cmd_export,
    "serve": cmd_serve,
    "grab": cmd_grab,
    "open": cmd_open,
    "board": cmd_board,
    "labels": _label_ls,  # top-level `labels` == `label ls`
    "members": cmd_members,
    "activity": cmd_activity,
    "updates": cmd_updates,
    "card": cmd_card,
    "list": cmd_list,
    "label": cmd_label,
    "checklist": cmd_checklist,
    "comment": cmd_comment,
    "attachment": cmd_attachment,
}


_HEXY_RE = re.compile(r"^[0-9a-f]{4,24}$")


def _board_flag_agrees(flag_value: str, magnet_board: str) -> bool:
    """True when a `--board` value and a magnet's board are the same board.

    Either may be the abbreviated form (`--short` magnets and the 8-char ids
    every table prints), so agreement is prefix-either-way. A *name* can't be
    compared without resolving it, so it never agrees — the caller gets the
    "drop the flag" error instead of a resolve that might pick a third board."""
    ref = flag_value.lower()
    if not _HEXY_RE.match(ref):
        return False
    return magnet_board.startswith(ref) or ref.startswith(magnet_board)


def _apply_magnet(mag: dict, explicit: dict[str, str]) -> None:
    """Seed board/backend/server from a magnet, refusing a flag that disagrees.

    Precedence is split by how deliberate the setting is:

    - **Env** (TRELLO_BOARD / TRELLO_BACKEND / TRELLO_SERVER): the magnet
      silently wins. Env is an ambient default and the magnet is specific; an
      agent with TRELLO_BOARD exported has to be able to paste a magnet and
      have it work, and erroring would break the feature in exactly the setup
      it is most needed in.
    - **An explicit flag that disagrees: hard error**, naming both values.
      Double-specifying is a real agent habit (a copied `--backend local
      --board …` prefix in front of a pasted magnet), and resolving a card id
      against the wrong backend is the plausible-looking-wrong-answer this CLI
      refuses everywhere else. Agreement is fine and stays silent.

    A flag whose value IS the magnet (`--board trello://board/…`) is agreement
    by construction, not a conflict."""
    backend_flag = explicit.get("--backend")
    if backend_flag and backend_flag.lower() != mag["backend"]:
        raise SystemExit(
            f"--backend {backend_flag} disagrees with the magnet link, which "
            f"says backend {mag['backend']}.\n"
            f"The magnet already carries its backend — drop the flag."
        )

    board_flag = explicit.get("--board")
    if board_flag and magnet.is_magnet(board_flag):
        # A *board* magnet here is agreement by construction. A card magnet is
        # not: it does carry a board, but putting it behind --board means the
        # caller thinks that's where a card goes, and the same token in
        # TRELLO_BOARD is refused (`_resolve_board_ref`) — so refuse it here
        # too rather than have the two channels disagree.
        kind = magnet.parse(board_flag)["type"]
        if kind != "board":
            raise SystemExit(
                f"--board was given a {kind} magnet. A {kind} magnet already "
                f"carries its board, so pass it where the {kind} goes and drop "
                f"--board entirely:\n"
                f"  trello card show {board_flag}"
            )
    elif (board_flag
            and not _board_flag_agrees(board_flag, mag["board"])):
        named = "" if _HEXY_RE.match(board_flag.lower()) else (
            " (a board *name* can't be checked against the magnet's id without "
            "resolving it, so it is refused either way)"
        )
        raise SystemExit(
            f"--board {board_flag} disagrees with the magnet link, which says "
            f"board {mag['board']}{named}.\n"
            f"The magnet already carries its board — drop the flag."
        )

    server_flag = explicit.get("--server")
    if (server_flag and mag["server"]
            and server_flag.rstrip("/") != mag["server"].rstrip("/")):
        raise SystemExit(
            f"--server {server_flag} disagrees with the magnet link, which "
            f"says {mag['server']}.\n"
            f"The magnet already carries its server — drop the flag."
        )

    config.set_board_override(mag["board"])
    config.set_backend_override(mag["backend"])
    if mag["server"]:
        config.set_server_override(mag["server"])


_GLOBAL_VALUE_FLAGS = ("--board", "--backend", "--server", "--local-root")


def _find_magnet(args: list[str]) -> dict | None:
    """The magnet in *reference position*, parsed — or None if there isn't one.

    Scans the raw arguments *before* the flags are stripped, so a magnet used
    as a flag value (`--board trello://board/…`) is seen too. Parsing here also
    means a malformed magnet fails immediately, with the grammar, rather than
    surfacing as a mystery "Card not found" further down.

    It stops at the first token that is neither a flag, a flag's value, nor a
    command/verb word — i.e. the first *argument*, which is where a ref goes.
    Scanning the whole of argv instead would let a magnet quoted in free text
    hijack the invocation: `comment add <id> "done, see trello://card/http/…"`
    would have been posted against the backend and board named in the comment
    body. Free text is always a later positional than the ref, so stopping at
    the first argument is what separates the two."""
    i = 0
    while i < len(args):
        a = args[i]
        if magnet.is_magnet(a):
            return magnet.parse(a)
        if a in _GLOBAL_VALUE_FLAGS:
            if i + 1 < len(args) and magnet.is_magnet(args[i + 1]):
                return magnet.parse(args[i + 1])
            i += 2  # skip the flag's value: it is not a ref position
            continue
        if a.startswith("-") or a in COMMANDS or a.lower() in _VERB_WORDS:
            i += 1
            continue
        return None  # the first argument, and it is not a magnet
    return None


def main() -> None:
    global _JSON_MODE
    args = sys.argv[1:]

    if "--json" in args:
        _JSON_MODE = True
        args = [a for a in args if a != "--json"]

    # A magnet link supplies board + backend (+ server), so it has to be read
    # before the flags it replaces are applied. The token itself stays in argv
    # and flows on to the command, where `_resolve_card` unwraps it.
    mag = _find_magnet(args)
    explicit: dict[str, str] = {}

    # Extract --board flag before dispatch. Each of these consumes the *next*
    # token as its value; refuse a following flag (starts with "-") so a dropped
    # value (`trello --board --backend local ...`) is a clean error, not a board
    # override literally named "--backend".
    if "--board" in args:
        idx = args.index("--board")
        if idx + 1 >= len(args) or args[idx + 1].startswith("-"):
            raise SystemExit("--board requires a board name or ID.")
        explicit["--board"] = args[idx + 1]
        config.set_board_override(args[idx + 1])
        args = args[:idx] + args[idx + 2:]

    # Extract --backend flag before dispatch (selects the data source)
    if "--backend" in args:
        idx = args.index("--backend")
        if idx + 1 >= len(args) or args[idx + 1].startswith("-"):
            raise SystemExit("--backend requires a name (trello, local or http).")
        explicit["--backend"] = args[idx + 1]
        config.set_backend_override(args[idx + 1])
        args = args[:idx] + args[idx + 2:]

    # Extract --server flag before dispatch (hosted trellno URL, http backend)
    if "--server" in args:
        idx = args.index("--server")
        if idx + 1 >= len(args) or args[idx + 1].startswith("-"):
            raise SystemExit("--server requires a URL.")
        explicit["--server"] = args[idx + 1]
        config.set_server_override(args[idx + 1])
        args = args[:idx] + args[idx + 2:]

    # Extract --local-root flag before dispatch (local file-store folder)
    if "--local-root" in args:
        idx = args.index("--local-root")
        if idx + 1 >= len(args) or args[idx + 1].startswith("-"):
            raise SystemExit("--local-root requires a path.")
        config.set_local_root_override(args[idx + 1])
        args = args[:idx] + args[idx + 2:]

    if mag:
        _apply_magnet(mag, explicit)

    if not args or args[0] in ("-h", "--help", "help"):
        # `trello help card` / `trello --help card` — the one group, not all ten.
        if len(args) > 1 and args[1] in COMMANDS:
            _print_group_help(args[1])
            return
        print(USAGE)
        return

    cmd_name = args[0]
    cmd_func = COMMANDS.get(cmd_name)
    if not cmd_func:
        print(f"Unknown command: {cmd_name}")
        near = difflib.get_close_matches(cmd_name, COMMANDS, n=3, cutoff=0.6)
        if near:
            print("Did you mean: " + ", ".join(near))
        print(USAGE)
        sys.exit(1)

    try:
        cmd_func(args[1:])
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:  # noqa: BLE001 — backstop for raw transport tracebacks
        # Translate Trello-backend HTTP/transport errors into a clean one-liner
        # (the local backend already raises SystemExit). httpx is only present
        # when the trello backend's deps are installed, so import it guardedly;
        # anything else re-raises with its real traceback. SystemExit is a
        # BaseException, so clean CLI errors pass straight through untouched.
        try:
            import httpx
        except ImportError:
            raise e from None
        if isinstance(e, httpx.HTTPStatusError):
            raise SystemExit(
                f"Trello API error: HTTP {e.response.status_code} for {e.request.url}"
            )
        if isinstance(e, httpx.TransportError):
            raise SystemExit(f"Network error talking to Trello: {e}")
        raise


if __name__ == "__main__":
    main()
