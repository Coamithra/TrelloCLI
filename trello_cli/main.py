"""Trello CLI - compact Trello interface that doesn't flood your context."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import webbrowser
from datetime import datetime, timedelta, timezone

# Force UTF-8 output on Windows (avoids cp1252 encoding errors with non-ASCII Trello data)
if sys.platform == "win32":
    for stream in ("stdout", "stderr"):
        s = getattr(sys, stream)
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")

from . import api, config
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
Usage: trello [--board <name_or_id>] [--backend <trello|local>] [--json] <command> [args]

Global options:
  --board <name_or_id>          Board for this command (required; no active board)
                                (also: TRELLO_BOARD env var)
  --backend <trello|local>      Data source for this command (default: trello)
                                (also: TRELLO_BACKEND env var)
  --local-root <path>           Local-backend store folder for this command
                                (also: TRELLO_LOCAL_ROOT env var; persist with
                                `local init <path>`)
  --json                        Emit raw JSON instead of formatted text
                                (read commands only)

Tip: bare nouns default to `ls` — e.g. `trello list` ≡ `trello list ls`,
     `trello card <list>` ≡ `trello card ls <list>`.

Global:
  configure <key> <token>       Save API credentials
  boards                        List all boards
  local init [path]             Set up the local file backend root
                                (default ~/Dropbox/trello-cli)
  board                         Show board info (needs --board)
  board add <name> [desc]       Create a new board (--no-default-lists)
  labels                        Show board labels
  members                       Show board members
  activity [n]                  Show recent activity
  updates <since> [type ...]    Show all updates/comments since a date
                                (ISO 2026-06-01, relative 6h/3d/2w/1m/1y,
                                'today', 'yesterday'; optional action-type
                                filter, e.g. commentCard updateCard)

Card:
  card show <card_id> [--no-comments]  Show card details (comments included by default)
  card ls <list> [--with-comment]      Show cards in a list (Activity column;
                                       --with-comment adds latest comment)
  card add <list> <name> [desc] Create a card at the top (--bottom to append)
  card move <card_id> <list>    Move a card to a list
  card archive <card_id>        Archive a card
  card unarchive <card_id>      Restore an archived card
  card rename <card_id> <name>  Rename a card
  card desc <card_id> <text>    Update card description
  card due <card_id> <date>     Set card due date (ISO 2026-05-01,
                                relative 1d/2w/1m/1y, 'tomorrow',
                                'today', or 'clear' to remove)
  card pos <card_id> <pos>      Reorder card. Pos: top, bottom, a number,
                                'after <other_card_id>', or
                                'before <other_card_id>'
  card mine                     Show cards assigned to me

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
  label add <name> <color>              Create a board label
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

Web:
  serve [--port 8787] [--host 127.0.0.1] [--no-browser]
                                Launch the drag-drop kanban web app for the
                                selected backend (pip install trello-cli[web]).
                                Binds 127.0.0.1 by default (local only). Live-
                                refreshes a local board as its files change.
"""


# ── Helpers ──────────────────────────────────────────────────────────


def _resolve_board_ref(ref: str) -> str:
    """Resolve a board name or ID to a board ID (for --board / TRELLO_BOARD)."""
    boards = api.get_boards()
    # Exact ID match
    for b in boards:
        if b["id"] == ref or short_id(b["id"]) == ref:
            return b["id"]
    # Name prefix match (case-insensitive)
    lower = ref.lower()
    matches = [b for b in boards if b["name"].lower().startswith(lower)]
    if len(matches) == 1:
        return matches[0]["id"]
    if len(matches) > 1:
        names = ", ".join(m["name"] for m in matches)
        raise SystemExit(f"Ambiguous board name '{ref}'. Matches: {names}")
    raise SystemExit(f"Board not found: {ref}")


def _require_board() -> str:
    override = config.get_board_override()
    if override:
        return _resolve_board_ref(override)
    raise SystemExit(
        "No board specified. Pass --board <name_or_id> or set TRELLO_BOARD."
    )


def _resolve_list(board_id: str, name_or_id: str) -> str:
    """Resolve a list name (case-insensitive prefix) or ID prefix."""
    lists = api.get_lists(board_id)
    # Exact ID
    for lst in lists:
        if lst["id"] == name_or_id:
            return lst["id"]
    # ID prefix
    id_matches = [lst for lst in lists if lst["id"].startswith(name_or_id)]
    if len(id_matches) == 1:
        return id_matches[0]["id"]
    # Name prefix (case-insensitive)
    lower = name_or_id.lower()
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
    """Resolve a card ID prefix to a full card ID by searching the active board."""
    # If it looks like a full 24-char ID, use it directly
    if len(card_id_prefix) == 24:
        return card_id_prefix
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
    comments = api.get_comments(card_id, limit=50)
    matches = [c for c in comments if c["id"].startswith(comment_id_prefix)]
    if len(matches) == 1:
        return matches[0]["id"]
    if len(matches) > 1:
        ids = ", ".join(short_id(c["id"]) for c in matches[:5])
        raise SystemExit(f"Ambiguous comment ID prefix '{comment_id_prefix}'. Matches: {ids}")
    raise SystemExit(f"Comment not found with prefix: {comment_id_prefix}")


def _resolve_checklist(card_id: str, name_or_id: str) -> str:
    """Resolve a checklist name (case-insensitive prefix) or ID prefix."""
    checklists = api.get_checklists(card_id)
    # Exact ID
    for cl in checklists:
        if cl["id"] == name_or_id:
            return cl["id"]
    # ID prefix
    id_matches = [cl for cl in checklists if cl["id"].startswith(name_or_id)]
    if len(id_matches) == 1:
        return id_matches[0]["id"]
    # Name prefix (case-insensitive)
    lower = name_or_id.lower()
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
    # ID prefix
    id_matches = [it for it in items if it["id"].startswith(name_or_id)]
    if len(id_matches) == 1:
        return id_matches[0]["id"]
    # Name prefix (case-insensitive)
    lower = name_or_id.lower()
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
    labels = api.get_labels(board_id)
    # Exact ID
    for lb in labels:
        if lb["id"] == name_or_id:
            return lb["id"]
    # ID prefix
    id_matches = [lb for lb in labels if lb["id"].startswith(name_or_id)]
    if len(id_matches) == 1:
        return id_matches[0]["id"]
    # Name prefix (case-insensitive)
    lower = name_or_id.lower()
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
    atts = api.get_attachments(card_id)
    # Exact ID
    for a in atts:
        if a["id"] == name_or_id:
            return a
    # ID prefix
    id_matches = [a for a in atts if a["id"].startswith(name_or_id)]
    if len(id_matches) == 1:
        return id_matches[0]
    # Name prefix (case-insensitive)
    lower = name_or_id.lower()
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


def _dispatch(group: str, subcmds: dict, args: list[str]) -> None:
    """Dispatch a noun-group subcommand. If the first arg isn't a known
    verb and the group has an `ls` verb, treat all args as `ls <args>`."""
    if args and args[0] in subcmds:
        subcmds[args[0]](args[1:])
        return
    if "ls" in subcmds:
        subcmds["ls"](args)
        return
    verbs = ", ".join(subcmds)
    raise SystemExit(f"Usage: trello {group} <{verbs}> [args]")


def _parse_flags(
    args: list[str],
    bool_flags: tuple[str, ...] = (),
    value_flags: tuple[str, ...] = (),
) -> tuple[list[str], dict[str, str | bool]]:
    """Split `args` into (positionals, flags), rejecting unknown flags.

    `bool_flags` are valueless (presence → True). `value_flags` consume the
    following token as their value. Any other `--`-prefixed token raises
    SystemExit, so a mistyped flag is reported instead of being silently
    swallowed into a positional argument (e.g. a list/card name)."""
    positional: list[str] = []
    flags: dict[str, str | bool] = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a in bool_flags:
            flags[a] = True
        elif a in value_flags:
            if i + 1 >= len(args):
                raise SystemExit(f"{a} requires a value.")
            flags[a] = args[i + 1]
            i += 1
        elif a.startswith("--"):
            raise SystemExit(f"Unknown flag: {a}")
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


def cmd_boards(_args: list[str]) -> None:
    boards = api.get_boards()
    if _is_json():
        print_json(boards)
        return
    rows = [[short_id(b["id"]), b["name"], b.get("shortUrl", "")] for b in boards]
    print_table(["ID", "Name", "URL"], rows)


def _board_show(_args: list[str]) -> None:
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


def cmd_board(args: list[str]) -> None:
    if args and args[0] == "add":
        _board_add(args[1:])
        return
    if args and args[0] == "show":
        args = args[1:]
    _board_show(args)


def cmd_labels(_args: list[str]) -> None:
    board_id = _require_board()
    labels = api.get_labels(board_id)
    if _is_json():
        print_json(labels)
        return
    rows = [[short_id(lb["id"]), lb.get("name", ""), lb.get("color", "")] for lb in labels]
    print_table(["ID", "Name", "Color"], rows)


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
    limit = int(args[0]) if args else 10
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
    card = api.get_card(_resolve_card(positional[0]))
    comments = [] if flags.get("--no-comments") else api.get_comments(card["id"], limit=20)
    if _is_json():
        print_json({**card, "comments": comments})
        return
    print_card_detail(card, comments)


def _card_ls(args: list[str]) -> None:
    positional, flags = _parse_flags(args, bool_flags=("--with-comment",))
    if not positional:
        raise SystemExit("Usage: trello card ls <list_name_or_id> [--with-comment]")
    with_comment = bool(flags.get("--with-comment"))
    board_id = _require_board()
    list_id = _resolve_list(board_id, " ".join(positional))
    cards = api.get_cards_in_list(list_id, with_latest_comment=with_comment)
    if _is_json():
        print_json(cards)
        return
    rows = []
    for c in cards:
        rows.append([
            short_id(c["id"]),
            (c.get("dateLastActivity") or "")[:10],
            truncate(c["name"], 50),
            label_str(c.get("labels", [])),
            due_str(c.get("due")),
        ])
    print_table(["ID", "Activity", "Name", "Labels", "Due"], rows)
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
            "Usage: trello card add <list_name_or_id> <card_name> [description] [--bottom]"
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
    new_name = " ".join(args[1:])
    api.update_card(card_id, name=new_name)
    print(f"Renamed card {short_id(card_id)} to: {new_name}")


def _card_desc(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello card desc <card_id> <description>")
    card_id = _resolve_card(args[0])
    desc = " ".join(args[1:])
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
    due = _parse_due(" ".join(args[1:]))
    api.update_card(card_id, due=due if due is not None else "")
    if due is None:
        print(f"Cleared due date on {short_id(card_id)}.")
    else:
        print(f"Set due date on {short_id(card_id)} to {due[:10]}.")


def _card_pos(args: list[str]) -> None:
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
        idx = next((i for i, c in enumerate(others) if c["id"] == other_id), None)
        if idx is None:
            raise SystemExit("Reference card not found in list.")
        ref_pos = others[idx]["pos"]
        if keyword == "after":
            new_pos = (ref_pos + others[idx + 1]["pos"]) / 2 \
                if idx + 1 < len(others) else "bottom"
        else:
            new_pos = (others[idx - 1]["pos"] + ref_pos) / 2 \
                if idx > 0 else "top"
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
    rows = []
    for c in cards:
        rows.append([
            short_id(c["id"]),
            (c.get("dateLastActivity") or "")[:10],
            truncate(c["name"], 50),
            label_str(c.get("labels", [])),
            due_str(c.get("due")),
        ])
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
    }, args)


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
    pos = chosen[0] if chosen else None
    board_id = _require_board()
    name = " ".join(positional)
    lst = api.create_list(board_id, name, pos=pos)
    print(f"Created list: {lst['name']} ({lst['id'][:8]})")


def _list_pos(args: list[str]) -> None:
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
        idx = next((i for i, lst in enumerate(others) if lst["id"] == other_id), None)
        if idx is None:
            raise SystemExit("Reference list not found on board.")
        ref_pos = others[idx]["pos"]
        if keyword == "after":
            new_pos = (ref_pos + others[idx + 1]["pos"]) / 2 \
                if idx + 1 < len(others) else "bottom"
        else:
            new_pos = (others[idx - 1]["pos"] + ref_pos) / 2 \
                if idx > 0 else "top"
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
    new_name = " ".join(args[1:])
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
        raise SystemExit("Usage: trello label add <name> <color>\n"
                         f"Colors: {', '.join(sorted(TRELLO_COLORS))}")
    board_id = _require_board()
    # Last arg may be a color
    color = None
    if len(args) >= 2 and args[-1].lower() in TRELLO_COLORS:
        color = args[-1].lower()
        name = " ".join(args[:-1])
    else:
        name = " ".join(args)
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
        fields["name"] = " ".join(rest)
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
    name = " ".join(args[1:])
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
    new_name = " ".join(args[2:])
    api.rename_checklist(cl_id, new_name)
    print(f"Renamed checklist {short_id(cl_id)} to: {new_name}")


def _checklist_item_add(args: list[str]) -> None:
    if len(args) < 3:
        raise SystemExit("Usage: trello checklist item add <card_id> <checklist> <text>")
    card_id = _resolve_card(args[0])
    cl_id = _resolve_checklist(card_id, args[1])
    name = " ".join(args[2:])
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
    new_name = " ".join(args[3:])
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
    }, args)


# ── Comment subcommands ─────────────────────────────────────────────


def _comment_add(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello comment add <card_id> <text>")
    card_id = _resolve_card(args[0])
    api.add_comment(card_id, " ".join(args[1:]))
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
    api.update_comment(comment_id, " ".join(args[2:]))
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
    }, args)


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
    name = " ".join(args[2:]) if len(args) > 2 else None
    if source.startswith(("http://", "https://")):
        a = api.add_attachment_url(card_id, source, name=name)
    else:
        if not os.path.isfile(source):
            raise SystemExit(f"File not found: {source}")
        a = api.add_attachment_file(card_id, source, name=name)
    print(f"Attached {a.get('name') or source} ({short_id(a['id'])}) to {short_id(card_id)}.")


def _attachment_dest(att: dict, dest: str | None) -> str:
    """Resolve the destination path for download/open of an attachment."""
    filename = att.get("name") or os.path.basename(att.get("url", "")) or att["id"]
    if dest is None:
        tmp = os.path.join(tempfile.gettempdir(), "trello-cli")
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
    }, args)


# ── Local-backend setup ─────────────────────────────────────────────


def _local_init(args: list[str]) -> None:
    positional, _ = _parse_flags(args)
    root = positional[0] if positional else config.get_local_root()
    os.makedirs(root, exist_ok=True)
    config.set_local_root(root)
    print(f"Local backend initialized at {root}")
    print("Use it with:  trello --backend local <command>"
          "   (or set TRELLO_BACKEND=local)")


def cmd_local(args: list[str]) -> None:
    _dispatch("local", {"init": _local_init}, args)


# ── Export (pull a board into the local file store) ─────────────────


def _export_attachment_blobs(backend, board_id: str, cards: list[dict]) -> dict:
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
    per-blob counts."""
    counts = {"downloaded": 0, "skipped": 0, "failed": 0}
    root = backend.store.root
    for card in cards:
        for att in card.get("attachments", []):
            url = att.get("url")
            if not att.get("isUpload") or not url:
                continue
            if not str(url).lower().startswith(("http://", "https://")):
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
            name = os.path.basename(str(att.get("name") or url).strip())
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name) or att["id"]  # the regex is the path-safety guard
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{att['id']}-{name}"
            tmp = dest_dir / f"{att['id']}.part"  # stream here, then os.replace (atomic; no truncated cache)
            try:
                api.download_attachment(url, str(tmp), authed=True)
                os.replace(tmp, dest)
                att["url"] = dest.relative_to(root).as_posix()
                counts["downloaded"] += 1
            except Exception as e:
                print(f"  warning: could not download attachment {short_id(att['id'])} "
                      f"({name}): {e} — keeping remote url", file=sys.stderr)
                try:
                    if tmp.is_file():
                        tmp.unlink()
                except OSError:
                    pass
                counts["failed"] += 1
    return counts


def cmd_export(args: list[str]) -> None:
    positional, flags = _parse_flags(
        args, bool_flags=("--no-attachments",), value_flags=("--to",),
    )
    if positional:
        raise SystemExit(
            "Usage: trello --board <board> export [--to local] [--no-attachments]\n"
            "  Pulls the board (from --backend, default trello) into the local file store.\n"
            "  Uploaded attachment blobs are downloaded by default; --no-attachments skips them."
        )
    target = str(flags.get("--to") or "local").lower()
    if target != "local":
        raise SystemExit(
            f"Unsupported export target: {target!r}. Only '--to local' is supported "
            "(export pulls a board into the local file store)."
        )
    if config.get_backend_name() == "local":
        # Source and target would be the same store (same local_root) — the prune
        # step would then delete from the very files it just read. Export is a pull
        # from a remote backend; run it with --backend trello (the default).
        raise SystemExit(
            "export pulls a board *into* the local store, so the source must be a "
            "remote backend. Run it with --backend trello (the default), not local."
        )
    board_id = _require_board()
    board = api.get_board(board_id)
    lists = api.get_lists(board_id)
    labels = api.get_labels(board_id)

    # Every card, visible + closed. The filtered listings drop the closed flag, so
    # stamp it. board-cards carries `pos` (get_card omits it); get_card supplies
    # desc plus checklists/attachments inline — export depends on get_card
    # returning those (both backends do); get_comments adds the comment thread.
    summaries: list[dict] = []
    for card_filter, closed in (("visible", False), ("closed", True)):
        for c in api.get_board_cards(board_id, card_filter=card_filter):
            summaries.append({**c, "closed": closed})
    cards = []
    for c in summaries:
        merged = {**api.get_card(c["id"]), **c}  # board-cards wins (pos, closed)
        merged["comments"] = api.get_comments(c["id"], limit=1000)
        cards.append(merged)

    from .backends.local import LocalBackend

    backend = LocalBackend(config.get_local_root())
    # Download blobs before import so import_board persists the rewritten (local) urls.
    blobs = None if flags.get("--no-attachments") else _export_attachment_blobs(
        backend, board["id"], cards,
    )
    result = backend.import_board(board, lists, labels, cards)
    # Stable JSON shape: always present, zeroed when --no-attachments skipped it.
    result["attachments"] = blobs or {"downloaded": 0, "skipped": 0, "failed": 0}
    if _is_json():
        print_json(result)
        return
    print(
        f"Exported '{result['name']}' ({short_id(result['id'])}) to {config.get_local_root()}\n"
        f"  {result['lists']} lists, {result['cards']} cards, "
        f"{result['labels']} labels, {result['comments']} comments\n"
        f"Browse it:  trello --backend local --board {short_id(result['id'])} list ls"
    )
    if blobs and (blobs["downloaded"] or blobs["skipped"] or blobs["failed"]):
        parts = [f"{blobs['downloaded']} downloaded"]
        if blobs["skipped"]:
            parts.append(f"{blobs['skipped']} cached")
        if blobs["failed"]:
            parts.append(f"{blobs['failed']} failed (kept remote url)")
        print(f"  attachment blobs: {', '.join(parts)}")


# ── Web server ──────────────────────────────────────────────────────


def cmd_serve(args: list[str]) -> None:
    positional, flags = _parse_flags(
        args, bool_flags=("--no-browser",),
        value_flags=("--port", "--host", "--token"),
    )
    if positional:
        raise SystemExit(
            "Usage: trello serve [--port <n>] [--host <addr>] [--token <t>] "
            "[--no-browser]"
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
    serve(host=str(host), port=port, token=token,
          open_browser=not flags.get("--no-browser"))


# ── Command dispatch ────────────────────────────────────────────────

COMMANDS = {
    "configure": cmd_configure,
    "boards": cmd_boards,
    "local": cmd_local,
    "export": cmd_export,
    "serve": cmd_serve,
    "board": cmd_board,
    "labels": cmd_labels,
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


def main() -> None:
    global _JSON_MODE
    args = sys.argv[1:]

    if "--json" in args:
        _JSON_MODE = True
        args = [a for a in args if a != "--json"]

    # Extract --board flag before dispatch
    if "--board" in args:
        idx = args.index("--board")
        if idx + 1 >= len(args):
            raise SystemExit("--board requires a board name or ID.")
        config.set_board_override(args[idx + 1])
        args = args[:idx] + args[idx + 2:]

    # Extract --backend flag before dispatch (selects the data source)
    if "--backend" in args:
        idx = args.index("--backend")
        if idx + 1 >= len(args):
            raise SystemExit("--backend requires a name (trello or local).")
        config.set_backend_override(args[idx + 1])
        args = args[:idx] + args[idx + 2:]

    # Extract --local-root flag before dispatch (local file-store folder)
    if "--local-root" in args:
        idx = args.index("--local-root")
        if idx + 1 >= len(args):
            raise SystemExit("--local-root requires a path.")
        config.set_local_root_override(args[idx + 1])
        args = args[:idx] + args[idx + 2:]

    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return

    cmd_name = args[0]
    cmd_func = COMMANDS.get(cmd_name)
    if not cmd_func:
        print(f"Unknown command: {cmd_name}")
        print(USAGE)
        sys.exit(1)

    cmd_func(args[1:])


if __name__ == "__main__":
    main()
