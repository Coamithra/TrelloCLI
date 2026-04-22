"""Trello CLI - compact Trello interface that doesn't flood your context."""

from __future__ import annotations

import re
import sys
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
    label_str,
    print_card_detail,
    print_json,
    print_table,
    short_id,
    truncate,
)

_JSON_MODE = False


def _is_json() -> bool:
    return _JSON_MODE

USAGE = """\
Usage: trello [--board <name_or_id>] [--json] <command> [args]

Global options:
  --board <name_or_id>          Override active board for this command
                                (also: TRELLO_BOARD env var)
  --json                        Emit raw JSON instead of formatted text
                                (read commands only)

Global:
  configure <key> <token>       Save API credentials
  boards                        List all boards
  use <board_name_or_id>        Set active board (default)
  board                         Show active board info
  labels                        Show board labels
  members                       Show board members
  activity [n]                  Show recent activity

Card:
  card show <card_id> [--no-comments]  Show card details (comments included by default)
  card ls <list> [--with-comment]      Show cards in a list (Activity column;
                                       --with-comment adds latest comment)
  card add <list> <name> [desc] Create a card
  card move <card_id> <list>    Move a card to a list
  card archive <card_id>        Archive a card
  card unarchive <card_id>      Restore an archived card
  card rename <card_id> <name>  Rename a card
  card desc <card_id> <text>    Update card description
  card due <card_id> <date>     Set card due date (ISO 2026-05-01,
                                relative 1d/2w/1m/1y, 'tomorrow',
                                'today', or 'clear' to remove)
  card mine                     Show cards assigned to me

List:
  list ls                       Show lists on active board
  list add <name>               Create a new list
  list archive <list>           Archive a list
  list rename <list> <new_name> Rename a list

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
    board_id = config.get_active_board()
    if not board_id:
        raise SystemExit(
            "No active board. Run: trello use <board>, "
            "or pass --board <name>, or set TRELLO_BOARD."
        )
    return board_id


def _resolve_list(board_id: str, name_or_id: str) -> str:
    """Resolve a list name (case-insensitive prefix match) or ID."""
    lists = api.get_lists(board_id)
    # Try exact ID match first
    for lst in lists:
        if lst["id"] == name_or_id:
            return lst["id"]
    # Try case-insensitive prefix match on name
    lower = name_or_id.lower()
    matches = [lst for lst in lists if lst["name"].lower().startswith(lower)]
    if len(matches) == 1:
        return matches[0]["id"]
    if len(matches) > 1:
        names = ", ".join(m["name"] for m in matches)
        raise SystemExit(f"Ambiguous list name '{name_or_id}'. Matches: {names}")
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


def _dispatch(group: str, subcmds: dict, args: list[str]) -> None:
    """Dispatch a noun-group subcommand."""
    if not args or args[0] not in subcmds:
        verbs = ", ".join(subcmds)
        raise SystemExit(f"Usage: trello {group} <{verbs}> [args]")
    subcmds[args[0]](args[1:])


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


def cmd_use(args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: trello use <board_name_or_id>")
    ref = " ".join(args)
    board_id = _resolve_board_ref(ref)
    # Fetch the board name for display/storage
    b = api.get_board(board_id)
    config.set_active_board(board_id, b["name"])
    print(f"Active board: {b['name']} ({short_id(board_id)})")


def cmd_board(_args: list[str]) -> None:
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
    if not args:
        raise SystemExit("Usage: trello card show <card_id> [--no-comments]")
    no_comments = "--no-comments" in args
    card_args = [a for a in args if not a.startswith("--")]
    card = api.get_card(_resolve_card(card_args[0]))
    comments = [] if no_comments else api.get_comments(card["id"], limit=20)
    if _is_json():
        print_json({**card, "comments": comments})
        return
    print_card_detail(card, comments)


def _card_ls(args: list[str]) -> None:
    with_comment = "--with-comment" in args
    positional = [a for a in args if not a.startswith("--")]
    if not positional:
        raise SystemExit("Usage: trello card ls <list_name_or_id> [--with-comment]")
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
    if len(args) < 2:
        raise SystemExit("Usage: trello card add <list_name_or_id> <card_name> [description]")
    board_id = _require_board()
    list_id = _resolve_list(board_id, args[0])
    name = args[1]
    desc = " ".join(args[2:]) if len(args) > 2 else None
    card = api.create_card(list_id, name, desc=desc)
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
    if not args:
        raise SystemExit("Usage: trello list add <name>")
    board_id = _require_board()
    name = " ".join(args)
    lst = api.create_list(board_id, name)
    print(f"Created list: {lst['name']} ({lst['id'][:8]})")


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


# ── Command dispatch ────────────────────────────────────────────────

COMMANDS = {
    "configure": cmd_configure,
    "boards": cmd_boards,
    "use": cmd_use,
    "board": cmd_board,
    "labels": cmd_labels,
    "members": cmd_members,
    "activity": cmd_activity,
    "card": cmd_card,
    "list": cmd_list,
    "label": cmd_label,
    "checklist": cmd_checklist,
    "comment": cmd_comment,
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
