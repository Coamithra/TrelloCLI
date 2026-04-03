"""Trello CLI - compact Trello interface that doesn't flood your context."""

from __future__ import annotations

import sys

from . import api, config
from .fmt import (
    due_str,
    label_str,
    print_card_detail,
    print_table,
    short_id,
    truncate,
)

USAGE = """\
Usage: trello <command> [args]

Global:
  configure <key> <token>       Save API credentials
  boards                        List all boards
  use <board_name_or_id>        Set active board
  board                         Show active board info
  labels                        Show board labels
  members                       Show board members
  activity [n]                  Show recent activity

Card:
  card show <card_id>           Show card details
  card ls <list>                Show cards in a list
  card add <list> <name> [desc] Create a card
  card move <card_id> <list>    Move a card to a list
  card archive <card_id>        Archive a card
  card rename <card_id> <name>  Rename a card
  card desc <card_id> <text>    Update card description
  card mine                     Show cards assigned to me

List:
  list ls                       Show lists on active board
  list add <name>               Create a new list
  list archive <list>           Archive a list
  list rename <list> <new_name> Rename a list

Comment:
  comment add <card_id> <text>              Add a comment
  comment ls <card_id>                      Show card comments
  comment edit <card_id> <comment_id> <text> Edit a comment
  comment delete <card_id> <comment_id>      Delete a comment
"""


# ── Helpers ──────────────────────────────────────────────────────────


def _require_board() -> str:
    board_id = config.get_active_board()
    if not board_id:
        raise SystemExit("No active board. Run: trello use <board_name_or_id>")
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


def _resolve_card(card_id_prefix: str) -> str:
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
    rows = [[short_id(b["id"]), b["name"], b.get("shortUrl", "")] for b in boards]
    print_table(["ID", "Name", "URL"], rows)


def cmd_use(args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: trello use <board_name_or_id>")
    query = " ".join(args).lower()
    boards = api.get_boards()

    # Try exact ID match
    for b in boards:
        if b["id"] == query or short_id(b["id"]) == query:
            config.set_active_board(b["id"], b["name"])
            print(f"Active board: {b['name']} ({short_id(b['id'])})")
            return

    # Try name prefix match
    matches = [b for b in boards if b["name"].lower().startswith(query)]
    if len(matches) == 1:
        b = matches[0]
        config.set_active_board(b["id"], b["name"])
        print(f"Active board: {b['name']} ({short_id(b['id'])})")
        return
    if len(matches) > 1:
        names = ", ".join(m["name"] for m in matches)
        raise SystemExit(f"Ambiguous board name. Matches: {names}")
    raise SystemExit(f"Board not found: {query}")


def cmd_board(_args: list[str]) -> None:
    board_id = _require_board()
    b = api.get_board(board_id)
    print(f"  Board: {b['name']}")
    print(f"  ID:    {b['id']}")
    print(f"  URL:   {b.get('shortUrl', '')}")
    desc = b.get("desc", "").strip()
    if desc:
        print(f"  Desc:  {truncate(desc, 80)}")


def cmd_labels(_args: list[str]) -> None:
    board_id = _require_board()
    labels = api.get_labels(board_id)
    rows = [[short_id(lb["id"]), lb.get("name", ""), lb.get("color", "")] for lb in labels]
    print_table(["ID", "Name", "Color"], rows)


def cmd_members(_args: list[str]) -> None:
    board_id = _require_board()
    members = api.get_members(board_id)
    rows = [[short_id(m["id"]), m.get("fullName", ""), f"@{m.get('username', '')}"] for m in members]
    print_table(["ID", "Name", "Username"], rows)


def cmd_activity(args: list[str]) -> None:
    board_id = _require_board()
    limit = int(args[0]) if args else 10
    actions = api.get_activity(board_id, limit)
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
        raise SystemExit("Usage: trello card show <card_id>")
    card = api.get_card(_resolve_card(args[0]))
    print_card_detail(card)


def _card_ls(args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: trello card ls <list_name_or_id>")
    board_id = _require_board()
    list_id = _resolve_list(board_id, " ".join(args))
    cards = api.get_cards_in_list(list_id)
    rows = []
    for c in cards:
        rows.append([
            short_id(c["id"]),
            truncate(c["name"], 50),
            label_str(c.get("labels", [])),
            due_str(c.get("due")),
        ])
    print_table(["ID", "Name", "Labels", "Due"], rows)


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


def _card_mine(_args: list[str]) -> None:
    cards = api.get_my_cards()
    rows = []
    for c in cards:
        rows.append([
            short_id(c["id"]),
            truncate(c["name"], 50),
            label_str(c.get("labels", [])),
            due_str(c.get("due")),
        ])
    print_table(["ID", "Name", "Labels", "Due"], rows)


def cmd_card(args: list[str]) -> None:
    _dispatch("card", {
        "show": _card_show,
        "ls": _card_ls,
        "add": _card_add,
        "move": _card_move,
        "archive": _card_archive,
        "rename": _card_rename,
        "desc": _card_desc,
        "mine": _card_mine,
    }, args)


# ── List subcommands ────────────────────────────────────────────────


def _list_ls(_args: list[str]) -> None:
    board_id = _require_board()
    lists = api.get_lists(board_id)
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
    "comment": cmd_comment,
}


def main() -> None:
    args = sys.argv[1:]
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
