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

Commands:
  configure <key> <token>   Save API credentials
  boards                    List all boards
  use <board_name_or_id>    Set active board
  board                     Show active board info
  lists                     Show lists on active board
  cards <list_name_or_id>   Show cards in a list
  card <card_id>            Show card details
  add <list> <name> [desc]  Create a card
  move <card_id> <list>     Move a card to a list
  rename card <id> <name>   Rename a card
  rename list <name> <new>  Rename a list
  archive <card_id>         Archive a card
  archive-list <list>       Archive a list
  comment <card_id> <text>  Add a comment
  comments <card_id>        Show card comments
  my-cards                  Show cards assigned to me
  labels                    Show board labels
  members                   Show board members
  add-list <name>           Create a new list on the active board
  activity [n]              Show recent activity
"""


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


def cmd_lists(_args: list[str]) -> None:
    board_id = _require_board()
    lists = api.get_lists(board_id)
    rows = [[lst["id"], lst["name"]] for lst in lists]
    print_table(["ID", "Name"], rows)


def cmd_cards(args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: trello cards <list_name_or_id>")
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


def cmd_card(args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: trello card <card_id>")
    card = api.get_card(_resolve_card(args[0]))
    print_card_detail(card)


def cmd_add(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello add <list_name_or_id> <card_name> [description]")
    board_id = _require_board()
    list_id = _resolve_list(board_id, args[0])
    name = args[1]
    desc = " ".join(args[2:]) if len(args) > 2 else None
    card = api.create_card(list_id, name, desc=desc)
    print(f"Created: {card['name']} ({short_id(card['id'])})")


def cmd_move(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello move <card_id> <list_name_or_id>")
    board_id = _require_board()
    card_id = _resolve_card(args[0])
    list_id = _resolve_list(board_id, " ".join(args[1:]))
    api.move_card(card_id, list_id)
    print(f"Moved {short_id(card_id)} to list.")


def cmd_archive(args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: trello archive <card_id>")
    card_id = _resolve_card(args[0])
    api.archive_card(card_id)
    print(f"Archived {short_id(card_id)}.")


def cmd_archive_list(args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: trello archive-list <list_name_or_id>")
    board_id = _require_board()
    list_id = _resolve_list(board_id, " ".join(args))
    api.archive_list(list_id)
    print("Archived list.")


def cmd_rename(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello rename <card|list> <id_or_name> <new_name>")
    target = args[0].lower()
    if target == "card":
        if len(args) < 3:
            raise SystemExit("Usage: trello rename card <card_id> <new_name>")
        card_id = _resolve_card(args[1])
        new_name = " ".join(args[2:])
        api.update_card(card_id, name=new_name)
        print(f"Renamed card {short_id(card_id)} to: {new_name}")
    elif target == "list":
        if len(args) < 3:
            raise SystemExit("Usage: trello rename list <list_name_or_id> <new_name>")
        board_id = _require_board()
        list_id = _resolve_list(board_id, args[1])
        new_name = " ".join(args[2:])
        api.rename_list(list_id, new_name)
        print(f"Renamed list to: {new_name}")
    else:
        raise SystemExit("Usage: trello rename <card|list> <id_or_name> <new_name>")


def cmd_add_list(args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: trello add-list <name>")
    board_id = _require_board()
    name = " ".join(args)
    lst = api.create_list(board_id, name)
    print(f"Created list: {lst['name']} ({lst['id'][:8]})")


def cmd_comment(args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: trello comment <card_id> <text>")
    card_id = _resolve_card(args[0])
    api.add_comment(card_id, " ".join(args[1:]))
    print("Comment added.")


def cmd_comments(args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: trello comments <card_id>")
    comments = api.get_comments(_resolve_card(args[0]))
    if not comments:
        print("  No comments.")
        return
    for c in comments:
        data = c.get("data", {})
        who = c.get("memberCreator", {}).get("username", "?")
        date = c.get("date", "")[:10]
        text = data.get("text", "")
        lines = text.splitlines()
        print(f"  {date}  @{who}: {lines[0] if lines else ''}")
        if len(lines) > 1:
            pad = " " * (len(date) + len(who) + 6)
            for line in lines[1:]:
                print(f"  {pad}{line}")


def cmd_my_cards(_args: list[str]) -> None:
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
        # Build a concise one-liner depending on action type
        card_name = data.get("card", {}).get("name", "")
        list_name = data.get("list", {}).get("name", "")
        detail = ""
        if card_name:
            detail = truncate(card_name, 40)
        if list_name and not card_name:
            detail = list_name
        print(f"  {date}  @{who:<12}  {atype:<24}  {detail}")


COMMANDS = {
    "configure": cmd_configure,
    "boards": cmd_boards,
    "use": cmd_use,
    "board": cmd_board,
    "lists": cmd_lists,
    "cards": cmd_cards,
    "card": cmd_card,
    "add": cmd_add,
    "move": cmd_move,
    "archive": cmd_archive,
    "rename": cmd_rename,
    "comment": cmd_comment,
    "comments": cmd_comments,
    "my-cards": cmd_my_cards,
    "labels": cmd_labels,
    "members": cmd_members,
    "add-list": cmd_add_list,
    "archive-list": cmd_archive_list,
    "activity": cmd_activity,
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
