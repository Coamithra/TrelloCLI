---
name: grab
description: Atomically claim a Trello card (the top backlog card by default) and implement it end to end using the contributing runbook — worktree, research, design, implement, verify, PR, card paperwork.
argument-hint: (nothing = top backlog card) | "top <List> card" | a specific card name/id | extra scope notes
---

# Grab a card and ship it

Claim one card **atomically**, then work it through the full runbook. The point is that
the claim happens *first* — before reading, planning, or pulling — so parallel agents
never collide on the same ticket.

## Step 1 — Resolve the project's specifics

Read the project's `CLAUDE.md` and `CONTRIBUTING.md` for: board id, backend (remote
`trello` vs `--backend local`), list ids, default branch, worktree layout + per-worktree
bootstrap, dev-server ports, and the verification gate. Then read the generic runbook at
`~/.claude/CONTRIBUTING.md` (start from this repo's `CONTRIBUTING.example.md`) — **where
they differ, the project file wins**.

No board or no repo → the runbook doesn't apply. Say so and stop.

## Step 2 — Work out which card

| Invocation | Meaning |
|---|---|
| `/grab` | Top card of the backlog / `To Do` list |
| `/grab top Later card`, `/grab from Icebox` | Same, popped from that list instead |
| `/grab the spider boss card`, `/grab <card_id>` | That **specific** card — `grab` only pops the top one, so find it with `card ls` and `card move` it by hand |
| `/grab top card, just plan it` | Scope words modify how far you take the card, not which one |

One card per invocation. Ambiguous between a list name and a card name → list the
candidates and ask.

## Step 3 — Claim it, first, before anything else

```
trello [--backend local] --board <id> grab --from <backlog-list> --to <in-progress-list>
```

- `--from`/`--to` are **required** unless the board uses the CLI's `To Do`/`Doing`
  defaults. Pass list **ids** where the project documents ids.
- **Exit 1 = source list empty** — report that and stop; don't retry or invent work.
- **Expect a different card than the one you saw on top.** Another agent may have claimed
  it in the gap, and `grab` atomically handed you the next one — that race is why `grab`
  exists. Work the card you got; don't investigate where the other one "went".
- On the local backend the grab is truly atomic (store lock); no claim handshake needed.

Tell the user in one line which card you got, before diving in.

## Step 4 — Run the runbook end to end

Follow `~/.claude/CONTRIBUTING.md` Phase 1→7 as amended by the project — don't improvise a
shorter path. Three things it's tempting to shortcut:

- **Keep the tracker doc** (`plans/tracker_<branch>.md` or the project's equivalent)
  checked off as you go — it's the recovery point if context is lost.
- **Present the design and get approval before writing code** (Phase 3), then post the
  TLDR comment on the card.
- **Prove the verification gate with the project's own tooling** (Phase 5) — don't hand
  the user a manual test plan in place of verifying. Then `/review` the branch diff and
  fix every finding before the PR.

## Guardrails

- Claim first. If you've read half the board before claiming, you've done it wrong.
- Never `grab` twice in one invocation to get a better card — you'd orphan the first.
- Deciding mid-flight that the card is wrong or blocked? Comment the reason on the card
  and move it back to the backlog before stopping — never silently drop it.
