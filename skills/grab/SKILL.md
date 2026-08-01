---
name: grab
description: Atomically claim a Trello card (the top backlog card by default) and implement it end to end using the contributing runbook — worktree, research, design, implement, verify, PR, card paperwork.
argument-hint: (nothing = top backlog card) | "top <List> card" | a specific card name/id | extra scope notes
---

# Grab a card and ship it

Claim one card **atomically**, then work it through the full runbook. The whole point is
that the claim happens *first* — before reading, planning, or pulling — so parallel agents
never collide on the same ticket.

## Step 1 — Resolve the project's board specifics

Read the project's local `CLAUDE.md` **and** its `CONTRIBUTING.md` (if present) for:
board id, backend (remote `trello` vs `--backend local`), the column/list ids, default
branch (`main` vs `master`), worktree layout + per-worktree bootstrap, dev-server ports,
and the verification gate.

If the project has **no board or no repo**, the runbook doesn't apply — say so and stop.

Read `~/.claude/CONTRIBUTING.md` (the generic runbook — start from this repo's
`CONTRIBUTING.example.md`) too, plus the project's own
`CONTRIBUTING.md` — where they differ, **the project file wins**.

## Step 2 — Work out which card `$ARGUMENTS` means

| Invocation | Meaning |
|---|---|
| `/grab` (no args) | Top card of the project's backlog / `To Do` list |
| `/grab top Later card`, `/grab from Icebox` | Same, but pop from that source list instead |
| `/grab the spider boss card`, `/grab <card_id>` | That **specific** card — do NOT use `grab` (it only pops the top card); find it with `card ls <listId>` and `card move <card_id> <in-progress-list>` by hand |
| Extra words about scope (`/grab top card, just plan it`) | Honour them — they modify how far you take the card, not which one |

One card per invocation. If the request is ambiguous between a list name and a card name,
list the candidates and ask.

## Step 3 — Claim it, first, before anything else

```
trello [--backend local] --board <id> grab --from <backlog-list> --to <in-progress-list>
```

- `--from` / `--to` are **required** on boards whose columns aren't the CLI's `To Do` /
  `Doing` defaults. Pass list **ids** when the project documents ids.
- `grab` pops the top card of the source list, moves it, and prints what you got.
  **Exit 1 = the source list is empty** — report that and stop; don't retry or invent work.
- **Expect a different card than the one you saw on top.** Another agent may have claimed it
  in the gap; `grab` atomically handed you the next one. That race is why `grab` exists.
  Work the card you actually got — don't investigate where the other one "went".
- On the local backend the grab is truly atomic (store lock); no claim handshake needed.

Then tell the user, in one line, which card you got, before diving in.

## Step 4 — Run the runbook end to end

Follow `~/.claude/CONTRIBUTING.md` (as amended by the project's `CONTRIBUTING.md`) from
Phase 1 through Phase 7 — don't improvise a shorter path:

1. **Phase 1** — tracker doc (`plans/tracker_<branch>.md` or the project's equivalent) with
   every runbook step as checkboxes; pull the default branch; read the card + its comments +
   any linked plan; create the worktree + branch (project's slot layout and prefixes) and
   run its per-worktree bootstrap.
2. **Phase 2** — research: read the cited code, trace the call chain, scope the blast radius.
3. **Phase 3** — design: write/update the plan doc, **present it and get the user's approval
   before writing code**, then post the short TLDR comment on the card.
4. **Phase 4** — implement per the plan and the project's conventions; update the project's
   `CLAUDE.md` if the change adds a convention, flag, or gotcha.
5. **Phase 5** — the project's verification gate. Prove it with the project's own tooling;
   don't hand the user a manual test plan in place of verifying.
6. **Phase 6** — commit, `/review` the branch diff and fix every finding, pull + resolve
   conflicts by the runbook's rules, re-verify, PR + self-merge (solo-repo
   convention — adapt if your repo gates merges), clean up worktree/branch/plan/tracker, move the card to Done, comment the
   summary (real newlines), open follow-up cards, write the user's closing overview.
7. **Phase 7** — stop dev servers and close verification browser tabs.

Keep the tracker doc checked off as you go — it's the recovery point if context is lost.

## Guardrails

- Claim first. If you've already read half the board before claiming, you've done it wrong.
- Never `grab` twice in one invocation to "get a better card" — you'd orphan the first one
  in the in-progress list.
- If you decide mid-flight that the card is wrong/blocked, don't silently drop it: comment
  the reason on the card and move it back to the backlog list before stopping.
