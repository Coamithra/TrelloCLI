"""Compact output formatting for Trello CLI."""

from __future__ import annotations

import json


def print_json(data: object) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def short_id(full_id: str) -> str:
    """Show first 8 chars of a Trello ID."""
    return full_id[:8]


def label_str(labels: list[dict]) -> str:
    """Format labels as compact colored tags."""
    if not labels:
        return ""
    parts = []
    for lb in labels:
        name = lb.get("name") or lb.get("color", "?")
        parts.append(f"[{name}]")
    return " ".join(parts)


def due_str(due: str | None, due_complete: bool = False) -> str:
    if not due:
        return ""
    date = due[:10]
    return f"({date} {'done' if due_complete else 'due'})"


def truncate(text: str, length: int = 60) -> str:
    if len(text) <= length:
        return text
    return text[: length - 1] + "\u2026"


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    """Print a compact aligned table."""
    if not rows:
        print("  (empty)")
        return

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))

    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        # Pad row to match header count
        padded = row + [""] * (len(headers) - len(row))
        print(fmt.format(*padded))


def print_card_detail(card: dict, comments: list[dict] | None = None) -> None:
    """Print a single card's full details compactly."""
    print(f"  Card:    {card['name']}")
    print(f"  ID:      {card['id']}")
    print(f"  URL:     {card.get('shortUrl', '')}")

    labels = card.get("labels", [])
    if labels:
        print(f"  Labels:  {label_str(labels)}")

    due = card.get("due")
    if due:
        print(f"  Due:     {due_str(due, card.get('dueComplete', False))}")

    desc = card.get("desc", "").strip()
    if desc:
        lines = desc.splitlines()
        print(f"  Desc:    {lines[0]}")
        for line in lines[1:]:
            print(f"           {line}")

    checklists = card.get("checklists", [])
    for cl in checklists:
        items = cl.get("checkItems", [])
        done = sum(1 for it in items if it.get("state") == "complete")
        print(f"  Checklist: {cl['name']} ({done}/{len(items)})")
        for it in items:
            mark = "x" if it.get("state") == "complete" else " "
            print(f"    [{mark}] {it['name']}")

    if comments:
        print(f"  Comments ({len(comments)}):")
        for c in comments:
            who = c.get("memberCreator", {}).get("username", "?")
            date = c.get("date", "")[:10]
            text = c.get("data", {}).get("text", "")
            lines = text.splitlines()
            print(f"    {date}  @{who}: {lines[0] if lines else ''}")
            if len(lines) > 1:
                pad = " " * (len(date) + len(who) + 8)
                for line in lines[1:]:
                    print(f"    {pad}{line}")
