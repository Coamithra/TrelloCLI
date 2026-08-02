---
name: grab
description: Atomically claim a Trello card (the top backlog card by default) and implement it end to end using the contributing runbook — worktree, research, design, implement, verify, PR, card paperwork.
argument-hint: (nothing = top backlog card) | "top <List> card" | a specific card name/id | extra scope notes
---

# Grab a card and ship it

Use the `trello` CLI's `grab` command to claim the top backlog card (a *specific* card:
`card move` it instead), tell the user in one line which card you got, and implement it
end to end per the contributing runbook. Two rules the runbook doesn't state: work the
card `grab` hands you, even when it isn't the one you saw on top; and if the card turns
out wrong or blocked, comment why on it and return it to the backlog — never silently
drop it.
