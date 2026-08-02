---
name: grab
description: Atomically claim a Trello card (the top backlog card by default) and implement it end to end using the contributing runbook — worktree, research, design, implement, verify, PR, card paperwork.
argument-hint: (nothing = top backlog card) | "top <List> card" | a specific card name/id | extra scope notes
---

# Grab a card and ship it

Claim the card FIRST — before reading, planning, or pulling — so parallel agents never
collide: `trello grab` pops the top backlog card atomically; a *specific* card is `card
move`d by hand instead. Tell the user in one line which card you got, then follow the
contributing runbook end to end — the project's CLAUDE.md/CONTRIBUTING.md amend
`~/.claude/CONTRIBUTING.md`, and where they differ the project wins. No board or no repo
→ the runbook doesn't apply; say so and stop.

- One card per invocation, never a second grab for a better card — you'd orphan the
  first. Scope words ("just plan it") modify how far you take it, not which card.
- Expect a different card than the one you saw on top — another agent claiming in the
  gap is the race grab exists for. Work the one you got.
- Wrong or blocked mid-flight? Comment why on the card and return it to the backlog —
  never silently drop it.
- Don't shortcut the runbook: design approval before code, prove the verification gate
  with the project's own tooling, and `/review` the diff before the PR.
