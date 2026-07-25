"""The seeded store every AX run starts from.

One fixture, built fresh per run, so a case that mutates can be verified by
reading the store afterwards and no two runs can see each other's writes. The
content is deliberately full of the things that trip agents up: a name that is
only unique past the fourth word, two boards so `--board` can't be guessed, a
card already carrying a label/comment/checklist, and an archived card.
"""

from __future__ import annotations

from pathlib import Path

from trello_cli.backends.local import LocalBackend

# The board every case works on unless it says otherwise.
BOARD = "Roadmap"
# A second board exists purely so that "the board" is ambiguous — an agent that
# forgets --board must be told so by the CLI, not rescued by there being one.
OTHER_BOARD = "Scratch"

LABELS = [("bug", "red"), ("feature", "green"), ("chore", "yellow")]

# list -> cards, top of list first (as `card add` builds them bottom-up below).
CARDS: dict[str, list[tuple[str, str]]] = {
    "To Do": [
        ("Fix login bug", "Session cookie is dropped on Safari 17."),
        ("Add dark mode", ""),
        # "Write ..." is a deliberately ambiguous name prefix.
        ("Write API docs", ""),
        ("Write onboarding guide", ""),
        ("Refactor exporter", ""),
    ],
    "Doing": [("Migrate database", "Postgres 14 -> 16.")],
    "Done": [("Set up CI", "")],
}


def build(root: str | Path) -> dict:
    """Seed a fresh store under `root`; return a small map of ids for verifiers."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    be = LocalBackend(str(root))

    board = be.create_board(BOARD, "Product roadmap")
    be.create_board(OTHER_BOARD, "Throwaway")

    labels = {}
    for name, color in LABELS:
        labels[name] = be.create_label(board["id"], name, color)["id"]

    lists = {ls["name"]: ls["id"] for ls in be.get_lists(board["id"])}
    cards: dict[str, str] = {}
    for list_name, entries in CARDS.items():
        for name, desc in reversed(entries):  # create bottom-up: add puts on top
            card = be.create_card(lists[list_name], name, desc or None)
            cards[name] = card["id"]

    # "Fix login bug" is the well-furnished card: label + comment + checklist.
    be.add_label_to_card(cards["Fix login bug"], labels["bug"])
    be.add_comment(cards["Fix login bug"], "Only reproduces with a stale cookie.")
    cl = be.create_checklist(cards["Fix login bug"], "Repro steps")
    for item in ("Log in on Safari", "Wait 24h", "Refresh"):
        be.add_checkitem(cl["id"], item)

    # An archived card, so "what's on the board" and "restore that card" differ.
    gone = be.create_card(lists["Done"], "Drop legacy endpoint")
    be.archive_card(gone["id"])
    cards["Drop legacy endpoint"] = gone["id"]

    return {"board": board["id"], "lists": lists, "cards": cards, "labels": labels}


class Store:
    """Read-side helper the case verifiers are written against."""

    def __init__(self, root: str | Path):
        self.be = LocalBackend(str(root))

    # -- boards ---------------------------------------------------------
    def boards(self, include_closed: bool = True) -> list[dict]:
        return self.be.get_boards(include_closed=include_closed)

    def board(self, name: str = BOARD) -> dict | None:
        for b in self.boards():
            if b["name"].lower() == name.lower():
                return b
        return None

    def board_id(self, name: str = BOARD) -> str:
        b = self.board(name)
        if not b:
            raise AssertionError(f"board {name!r} is gone")
        return b["id"]

    # -- lists ----------------------------------------------------------
    def lists(self, board: str = BOARD) -> list[dict]:
        return self.be.get_lists(self.board_id(board))

    def list_names(self, board: str = BOARD) -> list[str]:
        return [ls["name"] for ls in self.lists(board) if not ls.get("closed")]

    def list_id(self, name: str, board: str = BOARD) -> str | None:
        for ls in self.lists(board):
            if ls["name"].lower() == name.lower():
                return ls["id"]
        return None

    # -- cards ----------------------------------------------------------
    def cards(self, board: str = BOARD, card_filter: str = "visible") -> list[dict]:
        return self.be.get_board_cards(self.board_id(board), card_filter=card_filter)

    def card(self, name: str, board: str = BOARD, card_filter: str = "all") -> dict | None:
        for c in self.cards(board, card_filter=card_filter):
            if c["name"].lower() == name.lower():
                return c
        return None

    def cards_in(self, list_name: str, board: str = BOARD) -> list[dict]:
        lid = self.list_id(list_name, board)
        if lid is None:
            return []
        return [
            c for c in self.cards(board)
            if c.get("idList") == lid and not c.get("closed")
        ]

    def names_in(self, list_name: str, board: str = BOARD) -> list[str]:
        return [c["name"] for c in self.cards_in(list_name, board)]

    def list_of(self, card_name: str, board: str = BOARD) -> str | None:
        """Name of the list a card currently sits in."""
        c = self.card(card_name, board)
        if not c:
            return None
        for ls in self.lists(board):
            if ls["id"] == c.get("idList"):
                return ls["name"]
        return None

    # -- misc -----------------------------------------------------------
    def labels(self, board: str = BOARD) -> list[dict]:
        return self.be.get_labels(self.board_id(board))

    def label_names_on(self, card_name: str, board: str = BOARD) -> list[str]:
        c = self.card(card_name, board)
        return [lb.get("name", "") for lb in (c or {}).get("labels", [])]

    def comments(self, card_name: str, board: str = BOARD) -> list[str]:
        c = self.card(card_name, board)
        if not c:
            return []
        return [a.get("data", {}).get("text", "") for a in self.be.get_comments(c["id"], limit=50)]

    def checklists(self, card_name: str, board: str = BOARD) -> list[dict]:
        c = self.card(card_name, board)
        return self.be.get_checklists(c["id"]) if c else []

    def attachments(self, card_name: str, board: str = BOARD) -> list[dict]:
        c = self.card(card_name, board)
        return self.be.get_attachments(c["id"]) if c else []
