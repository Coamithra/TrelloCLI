---
name: farm
description: Farm a project's Trello backlog out to parallel per-card background agents while acting as overseer and voice of reason — each agent must stop and get its plan critically reviewed before writing code; the overseer rules on mid-flight questions and reviews the merged result. Use whenever the user wants multiple backlog cards/tickets worked at once — "make a dent in the backlog", "farm this out", "batch the backlog", "spin up agents on the board", "work through the backlog with agents" — or wants you to supervise/oversee parallel card agents. For implementing a SINGLE card end to end, use /grab instead.
argument-hint: (nothing = triage whole backlog) | batch size | "only the <topic> cards" | model to use for agents
---

# Farm the backlog — parallel card agents with an overseer

The design: the most capable model in the run — you — orchestrates parallel agents
working the backlog. They implement; you check their work, supply the big-picture context
they lack, and take the complicated problems they hit. You are the **overseer, not an
implementer**: one background agent per card.

- **Every agent stops after research+design and gets its plan reviewed before writing
  code.** Approval is the default outcome; what you add is checking the plan
  against what the card *actually* asks.
- **Your approval satisfies the runbook's "align with the user before writing code"**
  for in-scope work — say so in the spawn prompt. Only scope, product feel, and
  outward-facing calls still go up to the user.
- **Batch to your own review bandwidth**, never "spawn the whole backlog".
- **A card ONLY the user can do** (a deploy, a feel check, their screen) isn't farmed —
  surface it at triage instead of silently skipping it. Partly theirs? Farm it; the
  user's part joins the end-of-run checklist.
- **Agents never rewrite a card's text** — when the work reveals the spec is wrong, the
  correction goes in a comment.

## Learnings from live runs (the non-obvious part)

- **Verify a card's facts at triage — including against OPEN PRs, not just the board.**
  Cards drift between filing and pickup; put corrections in the spawn prompt and have
  agents re-verify the rest.
- **Have agents pilot a sweep small before running it wide, and STOP on a surprise
  verdict** — a card's claim about how its own work can be verified may itself be wrong.
- **Pre-assign worktree slot and branch name in every spawn prompt.** Simultaneous
  agents racing "pick the lowest free slot" is a known failure. Serialize broad sweeps
  over the same tree into separate batches.
- **A git failure in the ROOT checkout is probably a sibling shipping this second** —
  tell agents to wait and retry, never force. Likewise: if a card moved since triage,
  another session took it — stop and report, don't move it back.
- **`/review` runs fine from inside a subagent** — have agents run it themselves; if it
  fails, they say so in their report and you cover it.
- **Spawn prompts must ban silently-destructive git**: narrate any discard BEFORE doing
  it, never touch dirty state you didn't create.
- **One batch-level smoke beats per-card browser passes.** Default to headless; when a
  card agent hits a real tooling gap, spawn an agent to build the capability rather than
  letting it bodge around the gap.
- **Escalate to the user via the ask-a-question tool, never prose** — a question buried
  in a status report gets missed. The blocked agent stays paused meanwhile; don't rule
  on the user's behalf.
- **Rule on every card and proposal that arrives mid-run — "file a card" is not the
  default outcome.** Read [`rulings.md`](rulings.md) before the first ruling of a batch.
- **Sweep the in-progress list for orphans at each batch boundary** — a dead or stalled
  agent leaves its card claimed, and the board can't tell that from healthy work. Match
  every card to a live agent; for the rest, read the branch first, then finish or return
  the card. Never re-spawn onto an orphan's half-built worktree blind.
- **Trim the batch's CLAUDE.md changes.** Agents bloat docs with their change's story:
  what they didn't end up doing, how it used to work, function-level detail. Keep the
  durable rule or gotcha, cut the narrative.
- **Batch user-verification into ONE checklist** at the end of the run, organized so a
  single pass covers it — not a trickle of per-card test requests.

## Spawn prompt

**Pin the fleet's model explicitly on every spawn** — omitted, agents inherit *your*
tier. Name the cheapest tier that implements well; today that's `model: "opus"`. (A
user-named model wins.)

Agents inherit none of your context, so each prompt carries: repo path, card id, the
runbook reading list, the claim command (a pre-assigned card is `card move`d, never
`grab`bed), the assigned slot + branch, the project's verification doctrine restated as
direct instructions — **including anything banned outright**, since rules agents merely
*read* get under-weighted — your stale-fact corrections, cross-card warnings, and:

> CHECKPOINT — MANDATORY: research + design only, then END YOUR TURN with your plan
> (context, file-by-file changes, verification, out-of-scope). No implementation and no
> card comment until the overseer approves. At ANY point, on any question or surprise,
> end your turn and ask — never guess, never expand scope silently. After approval:
> implement, verify, run /review and fix findings, ship per the runbook, do the card
> paperwork, report with commit hashes. Follow-up work you discover is PROPOSED, not
> filed — the overseer rules, and often the ruling is "extend your scope and do it now".

## Batch boundaries

A long run WILL get its context summarized, and **only the USER can /compact — you have
no tool for it**. So by each batch's end, everything a post-summarization overseer needs
(batch state, rulings given, deferred cards, the user checklist) must live in card
comments, PRs and filed cards, never only in conversation.

Then, with batch review + smoke + doc trim + backlog groom + orphan sweep done and the
next batch NOT yet launched, PAUSE via the ask-a-question tool — "continue or compact?" —
which doubles as the batch-done notification. If they pick compact, hand them a
ready-to-type `/compact` naming what to keep (queue, rulings, user checklist, anything not
yet durable) and what to drop (shipped cards' play-by-play, plan texts, tool output). Wait
for their answer before spawning — a batch launched pre-compact re-bloats the window.

Review the merged diff yourself before rolling on; you have cross-card context the
per-card reviews lacked. Spot-check anything other agents consume (the runbook especially)
as soon as it lands, not at batch end.
