"""The case corpus.

Each case is a task written the way a human would hand it to an agent — no
flags, no command names, nothing that leaks the CLI's shape. What we measure:

  * did it get there            (`verify` reads the store; `expect` reads the answer)
  * how expensive was getting there  (`budget` = a healthy median tool-call count)
  * what did it try on the way   (the rendered transcript)

`budget` is the number of shell calls a competent operator who already knows the
tool would need, plus one for orientation (`trello --help`). Overrunning it is
not a failure — it is the interesting middle ground where the tool is learnable
but not obvious, and it is where the backlog items come from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .fixture import Store

Verifier = Callable[[Store], "str | None"]  # None = pass, str = why it failed


@dataclass
class Case:
    id: str
    tier: int
    prompt: str
    budget: int
    verify: Verifier | None = None
    # Substrings the final answer must contain (case-insensitive).
    expect: list[str] = field(default_factory=list)
    # Substrings the final answer must NOT contain (a wrong answer stated
    # confidently is worse than a refusal).
    forbid: list[str] = field(default_factory=list)
    # A regex at least one issued command must match. For tasks where the right
    # outcome reached the wrong way is still a failure — "claim the top ticket
    # so no one else can" is not solved by `card ls` + `card move`, however
    # right the board looks afterwards.
    expect_cmd: str | None = None
    # Read-only cases additionally fail if the store changed at all.
    readonly: bool = False
    tags: list[str] = field(default_factory=list)


def _has(items: list[str], want: str) -> bool:
    return any(want.lower() in i.lower() for i in items)


CASES: list[Case] = [
    # ---------------------------------------------------------------- tier 1
    # Reads. If these are not near-100% the tool is unusable by agents at all.
    Case(
        id="t1-boards",
        tier=1,
        prompt="Which Trello boards exist? Use the `trello` command. List their names.",
        budget=2,
        expect=["Roadmap", "Scratch"],
        readonly=True,
        tags=["read", "discovery"],
    ),
    Case(
        id="t1-lists",
        tier=1,
        prompt="Use the `trello` command to tell me the names of the columns on the Roadmap board, in order.",
        budget=3,
        expect=["To Do", "Doing", "Done"],
        readonly=True,
        tags=["read", "board-scope"],
    ),
    Case(
        id="t1-cards-in-list",
        tier=1,
        prompt="Use the `trello` command. What is in the To Do column of the Roadmap board?",
        budget=3,
        expect=["Fix login bug", "Add dark mode", "Refactor exporter"],
        readonly=True,
        tags=["read"],
    ),
    Case(
        id="t1-find-card",
        tier=1,
        prompt=(
            "Use the `trello` command. Somewhere on the Roadmap board there's a card "
            "about a problem with cookies in Safari. Which card is it?"
        ),
        # The give-away words live ONLY in the description ("Session cookie is
        # dropped on Safari 17."), never in the title — so listing columns and
        # reading names can't answer it. Filing this card is what the search
        # feature exists for; before it, an agent had to open cards one by one.
        budget=3,
        expect=["Fix login bug"],
        forbid=["Add dark mode", "Migrate database"],
        readonly=True,
        tags=["read", "search", "discovery"],
    ),
    Case(
        id="t1-find-board",
        tier=1,
        prompt=(
            "Use the `trello` command. Is there a board whose name mentions 'scratch'? "
            "Give me its name and id."
        ),
        budget=2,
        expect=["Scratch"],
        readonly=True,
        tags=["read", "search", "discovery"],
    ),
    Case(
        id="t1-card-detail",
        tier=1,
        prompt=(
            "Use the `trello` command. On the Roadmap board there is a card called "
            "'Fix login bug'. What does its description say, and what did the last "
            "comment on it say?"
        ),
        budget=4,
        expect=["safari", "stale cookie"],
        readonly=True,
        tags=["read", "resolver"],
    ),
    Case(
        id="t1-labels",
        tier=1,
        prompt="Use the `trello` command to list the labels defined on the Roadmap board, with their colours.",
        budget=3,
        expect=["bug", "feature", "chore"],
        readonly=True,
        tags=["read"],
    ),
    Case(
        id="t1-json",
        tier=1,
        prompt=(
            "Use the `trello` command to get the cards in the Roadmap board's To Do "
            "column as machine-readable JSON, and tell me the id of the card named "
            "'Add dark mode'."
        ),
        budget=3,
        expect=[],
        readonly=True,
        tags=["read", "json"],
    ),
    Case(
        id="t1-activity",
        tier=1,
        prompt="Use the `trello` command to show me the 5 most recent things that happened on the Roadmap board.",
        budget=3,
        readonly=True,
        tags=["read"],
    ),
    Case(
        id="t1-mine",
        tier=1,
        prompt="Use the `trello` command. Which cards on the Roadmap board are assigned to me?",
        budget=3,
        readonly=True,
        tags=["read"],
    ),
    Case(
        id="t1-checklist-read",
        tier=1,
        prompt=(
            "Use the `trello` command. The Roadmap board has a card 'Fix login bug'. "
            "Does it have a checklist, and if so what are the items?"
        ),
        budget=4,
        expect=["log in on safari", "refresh"],
        readonly=True,
        tags=["read", "checklist"],
    ),
    Case(
        id="t1-count",
        tier=1,
        prompt=(
            "Use the `trello` command. How many cards are in each column of the "
            "Roadmap board? Give me the counts."
        ),
        budget=4,
        expect=["5", "1"],
        readonly=True,
        tags=["read", "aggregate"],
    ),
    # ---------------------------------------------------------------- tier 2
    # One mutation each. The store is the judge.
    Case(
        id="t2-add-card",
        tier=2,
        prompt=(
            "Use the `trello` command to add a card called 'Write release notes' to "
            "the To Do column of the Roadmap board."
        ),
        budget=3,
        verify=lambda s: None if _has(s.names_in("To Do"), "Write release notes")
        else f"card not in To Do (To Do = {s.names_in('To Do')})",
        tags=["write", "card"],
    ),
    Case(
        id="t2-add-card-desc",
        tier=2,
        prompt=(
            "Use the `trello` command. Add a card 'Ship v2' to the To Do column of "
            "the Roadmap board, with the description 'Cut the release branch first.'"
        ),
        budget=3,
        verify=lambda s: (
            "card missing" if not s.card("Ship v2")
            else None if "release branch" in (s.card("Ship v2") or {}).get("desc", "").lower()
            else f"desc not set (got {(s.card('Ship v2') or {}).get('desc')!r})"
        ),
        tags=["write", "card"],
    ),
    Case(
        id="t2-move-card",
        tier=2,
        prompt=(
            "Use the `trello` command. On the Roadmap board, I've started work on "
            "'Add dark mode' — move it into the Doing column."
        ),
        budget=4,
        verify=lambda s: None if s.list_of("Add dark mode") == "Doing"
        else f"card is in {s.list_of('Add dark mode')!r}",
        tags=["write", "card", "resolver"],
    ),
    Case(
        id="t2-rename-card",
        tier=2,
        prompt=(
            "Use the `trello` command. On the Roadmap board, rename the card "
            "'Refactor exporter' to 'Refactor the exporter module'."
        ),
        budget=4,
        verify=lambda s: None if s.card("Refactor the exporter module")
        else "renamed card not found",
        tags=["write", "card", "resolver"],
    ),
    Case(
        id="t2-due",
        tier=2,
        prompt=(
            "Use the `trello` command. The card 'Fix login bug' on the Roadmap board "
            "is due tomorrow — set that."
        ),
        budget=4,
        verify=lambda s: None if (s.card("Fix login bug") or {}).get("due")
        else "no due date set",
        tags=["write", "card", "date"],
    ),
    Case(
        id="t2-archive-card",
        tier=2,
        prompt=(
            "Use the `trello` command. 'Set up CI' on the Roadmap board is finished "
            "and I don't want to see it any more — get it off the board without "
            "destroying it."
        ),
        budget=4,
        verify=lambda s: None if (s.card("Set up CI") or {}).get("closed")
        else "card is still open",
        tags=["write", "card", "semantic"],
    ),
    Case(
        id="t2-label-set",
        tier=2,
        prompt=(
            "Use the `trello` command. Tag the card 'Add dark mode' on the Roadmap "
            "board with the existing 'feature' label."
        ),
        budget=4,
        verify=lambda s: None if _has(s.label_names_on("Add dark mode"), "feature")
        else f"labels are {s.label_names_on('Add dark mode')}",
        tags=["write", "label"],
    ),
    Case(
        id="t2-comment",
        tier=2,
        prompt=(
            "Use the `trello` command to leave the comment 'Blocked on design "
            "review.' on the card 'Migrate database' on the Roadmap board."
        ),
        budget=4,
        verify=lambda s: None if _has(s.comments("Migrate database"), "Blocked on design review")
        else f"comments are {s.comments('Migrate database')}",
        tags=["write", "comment"],
    ),
    Case(
        id="t2-add-list",
        tier=2,
        prompt="Use the `trello` command to add a new column called 'Blocked' to the Roadmap board.",
        budget=3,
        verify=lambda s: None if _has(s.list_names(), "Blocked")
        else f"lists are {s.list_names()}",
        tags=["write", "list"],
    ),
    Case(
        id="t2-add-board",
        tier=2,
        prompt="Use the `trello` command to create a new board called 'Q3 Planning'.",
        budget=2,
        verify=lambda s: None if s.board("Q3 Planning") else "board not created",
        tags=["write", "board"],
    ),
    Case(
        id="t2-rename-list",
        tier=2,
        prompt="Use the `trello` command. On the Roadmap board, rename the 'Doing' column to 'In Progress'.",
        budget=4,
        verify=lambda s: None if _has(s.list_names(), "In Progress")
        else f"lists are {s.list_names()}",
        tags=["write", "list"],
    ),
    Case(
        id="t2-card-top",
        tier=2,
        prompt=(
            "Use the `trello` command. 'Refactor exporter' is at the bottom of the "
            "To Do column on the Roadmap board — make it the first card in that column."
        ),
        budget=4,
        verify=lambda s: None if s.names_in("To Do")[:1] == ["Refactor exporter"]
        else f"To Do order is {s.names_in('To Do')}",
        tags=["write", "pos"],
    ),
    Case(
        id="t2-unarchive",
        tier=2,
        prompt=(
            "Use the `trello` command. A card called 'Drop legacy endpoint' was "
            "archived on the Roadmap board by mistake. Put it back on the board."
        ),
        budget=5,
        verify=lambda s: None if not (s.card("Drop legacy endpoint") or {"closed": True}).get("closed")
        else "card is still archived",
        tags=["write", "card", "discovery"],
    ),
    # ---------------------------------------------------------------- tier 3
    # Multi-step, or requiring a verb the agent has to notice exists.
    Case(
        id="t3-grab",
        tier=3,
        prompt=(
            "Use the `trello` command. Several agents are working this board at "
            "once, so claim the top ticket of the Roadmap board's To Do column for "
            "yourself — move it into Doing without any risk of another agent "
            "claiming the same one. Tell me which card you got."
        ),
        budget=3,
        verify=lambda s: None if s.list_of("Fix login bug") == "Doing"
        else f"top To Do card is in {s.list_of('Fix login bug')!r}",
        # `card ls` + `card move` gets the same board state and is exactly the
        # race the task rules out, so the mechanism is the test.
        expect_cmd=r"\bgrab\b",
        tags=["write", "workflow", "discovery"],
    ),
    Case(
        id="t3-new-label-apply",
        tier=3,
        prompt=(
            "Use the `trello` command. Create a red label called 'urgent' on the "
            "Roadmap board and put it on both 'Fix login bug' and 'Migrate database'."
        ),
        budget=6,
        verify=lambda s: (
            "label not created" if not _has([l["name"] for l in s.labels()], "urgent")
            else None if _has(s.label_names_on("Fix login bug"), "urgent")
            and _has(s.label_names_on("Migrate database"), "urgent")
            else f"applied to: login={s.label_names_on('Fix login bug')} "
                 f"migrate={s.label_names_on('Migrate database')}"
        ),
        tags=["write", "label", "multi"],
    ),
    Case(
        id="t3-checklist",
        tier=3,
        prompt=(
            "Use the `trello` command. Add a checklist called 'Rollout' to the card "
            "'Migrate database' on the Roadmap board, with the items 'Take backup', "
            "'Run migration' and 'Verify'."
        ),
        budget=7,
        verify=lambda s: (
            "checklist not created"
            if not _has([c["name"] for c in s.checklists("Migrate database")], "Rollout")
            else None
            if len([
                i for cl in s.checklists("Migrate database")
                if cl["name"].lower() == "rollout"
                for i in cl.get("checkItems", [])
            ]) == 3
            else "wrong number of items: " + str([
                i.get("name") for cl in s.checklists("Migrate database")
                for i in cl.get("checkItems", [])
            ])
        ),
        tags=["write", "checklist", "multi"],
    ),
    Case(
        id="t3-bulk-move",
        tier=3,
        prompt=(
            "Use the `trello` command. We're resetting the sprint on the Roadmap "
            "board: move every card that is currently in Doing back to To Do."
        ),
        budget=5,
        verify=lambda s: None if not s.names_in("Doing") and _has(s.names_in("To Do"), "Migrate database")
        else f"Doing={s.names_in('Doing')} ToDo={s.names_in('To Do')}",
        tags=["write", "multi", "semantic"],
    ),
    Case(
        id="t3-triage",
        tier=3,
        prompt=(
            "Use the `trello` command. On the Roadmap board, every card in To Do "
            "whose name starts with 'Write' should get the 'chore' label and be due "
            "in one week."
        ),
        budget=8,
        verify=lambda s: (
            None
            if all(
                _has(s.label_names_on(n), "chore") and (s.card(n) or {}).get("due")
                for n in ("Write API docs", "Write onboarding guide")
            )
            else "api=%s/%s guide=%s/%s" % (
                s.label_names_on("Write API docs"), (s.card("Write API docs") or {}).get("due"),
                s.label_names_on("Write onboarding guide"), (s.card("Write onboarding guide") or {}).get("due"),
            )
        ),
        tags=["write", "multi", "resolver", "ambiguity"],
    ),
    Case(
        id="t3-archive-list",
        tier=3,
        prompt=(
            "Use the `trello` command. The Done column on the Roadmap board is "
            "clutter — take the whole column off the board."
        ),
        budget=4,
        verify=lambda s: None if not _has(s.list_names(), "Done")
        else f"lists are {s.list_names()}",
        tags=["write", "list", "semantic"],
    ),
    Case(
        id="t3-board-lifecycle",
        tier=3,
        prompt=(
            "Use the `trello` command. The 'Scratch' board is not needed right now "
            "but might be later: archive it, show me that it no longer appears in "
            "the normal board list, then restore it."
        ),
        budget=6,
        verify=lambda s: None if s.board("Scratch") and not s.board("Scratch").get("closed")
        else "Scratch is not back in an open state",
        tags=["write", "board", "multi", "discovery"],
    ),
    Case(
        id="t3-attachment",
        tier=3,
        prompt=(
            "Use the `trello` command. Attach the link "
            "https://example.com/rfc-42 to the card 'Migrate database' on the "
            "Roadmap board, named 'RFC 42'."
        ),
        budget=5,
        verify=lambda s: None if _has(
            [a.get("url", "") for a in s.attachments("Migrate database")], "rfc-42"
        ) else f"attachments are {[a.get('url') for a in s.attachments('Migrate database')]}",
        tags=["write", "attachment"],
    ),
    Case(
        id="t3-reorder-relative",
        tier=3,
        prompt=(
            "Use the `trello` command. In the To Do column of the Roadmap board, "
            "put 'Refactor exporter' directly after 'Fix login bug'."
        ),
        budget=6,
        verify=lambda s: (
            None if s.names_in("To Do")[:2] == ["Fix login bug", "Refactor exporter"]
            else f"To Do order is {s.names_in('To Do')}"
        ),
        tags=["write", "pos", "hard"],
    ),
    Case(
        id="t3-move-across-boards",
        tier=3,
        prompt=(
            "Use the `trello` command. Copy the card 'Add dark mode' from the "
            "Roadmap board over to the Scratch board's To Do column, keeping the "
            "same name, and leave the original where it is."
        ),
        budget=6,
        verify=lambda s: (
            None if _has(s.names_in("To Do", board="Scratch"), "Add dark mode")
            and _has(s.names_in("To Do"), "Add dark mode")
            else f"scratch={s.names_in('To Do', board='Scratch')} roadmap={s.names_in('To Do')}"
        ),
        tags=["write", "cross-board", "hard"],
    ),
    Case(
        id="t3-report",
        tier=3,
        prompt=(
            "Use the `trello` command. Give me a one-line status for the Roadmap "
            "board: how many cards are open, how many are labelled 'bug', and what "
            "the single card in Doing is."
        ),
        budget=6,
        expect=["migrate database"],
        readonly=True,
        tags=["read", "aggregate", "json"],
    ),
    Case(
        id="t3-updates-since",
        tier=3,
        prompt=(
            "Use the `trello` command. What has changed on the Roadmap board in the "
            "last day? Include comments."
        ),
        budget=4,
        readonly=True,
        tags=["read", "date", "discovery"],
    ),
]

BY_ID = {c.id: c for c in CASES}


def select(spec: str | None) -> list[Case]:
    """Pick cases by id, tier (`t1`), tag (`tag:write`) or `all`."""
    if not spec or spec == "all":
        return list(CASES)
    picked: list[Case] = []
    for token in spec.split(","):
        token = token.strip()
        if token in BY_ID:
            picked.append(BY_ID[token])
        elif token.startswith("tag:"):
            picked += [c for c in CASES if token[4:] in c.tags]
        elif token.startswith("t") and token[1:].isdigit():
            picked += [c for c in CASES if c.tier == int(token[1:])]
        else:
            raise SystemExit(f"Unknown case selector: {token!r}")
    seen, out = set(), []
    for c in picked:
        if c.id not in seen:
            seen.add(c.id)
            out.append(c)
    return out
