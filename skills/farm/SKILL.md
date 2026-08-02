---
name: farm
description: Farm a project's Trello backlog out to parallel per-card background agents while acting as overseer and voice of reason — each agent must stop and get its plan critically reviewed before writing code; the overseer rules on mid-flight questions and reviews the merged result. Use whenever the user wants multiple backlog cards/tickets worked at once — "make a dent in the backlog", "farm this out", "batch the backlog", "spin up agents on the board", "work through the backlog with agents" — or wants you to supervise/oversee parallel card agents. For implementing a SINGLE card end to end, use /grab instead.
argument-hint: (nothing = triage whole backlog) | batch size | "only the <topic> cards" | model to use for agents
---

# Farm the backlog — parallel card agents with an overseer

The design: the most capable model in the run — you — orchestrates parallel agents
working the backlog. They implement; you check their work, supply the big-picture context
they lack, and take the complicated problems they hit. You are the **overseer, not an
implementer**: one background agent per card, and **every agent stops after
research+design and gets its plan reviewed before writing code**.

## 1 — Triage

- Verify each candidate card's facts — against OPEN PRs too, not just the board. Cards
  drift between filing and pickup; corrections go in the spawn prompt, and agents
  re-verify the rest.
- A constraint you believe applies (backwards compatibility, a frozen interface) is
  verified with the user before any spawn prompt states it — a false one taxes every
  design in the batch.
- A card ONLY the user can do (a deploy, a feel check, their screen) isn't farmed —
  tag it `[HUMAN REQUIRED]` for future runs and surface it. Partly theirs? Farm it; the
  user's part joins the end-of-run checklist.
- Snapshot the backlog's card ids — the completion-time board diff needs this baseline.
- Scan the board for cards worth combining into one spawn — same surface area, or a
  clutch of small fixes. Each agent costs a worktree, a plan review, and a PR; a card
  should be worth that overhead.
- Size the batch to your own review bandwidth, never "the whole backlog". Pre-assign
  each card a worktree slot and branch name — simultaneous agents racing "pick a free
  slot" is a known failure — and serialize broad sweeps over the same tree into
  separate batches.

## 2 — Spawn

Pin the fleet's model explicitly on every spawn: the cheapest tier that implements well
— today that's `model: "opus"`. (A user-named model wins.)

Agents inherit none of your context, so each prompt carries: repo path, card id, the
runbook reading list, the slot + branch, your stale-fact corrections and cross-card
warnings, and the project's verification doctrine. Include these five rules every time:

- Never rewrite a card's text — when the work reveals the spec is wrong, the correction
  goes in a comment.
- Narrate any git discard BEFORE doing it; never touch dirty state you didn't create.
- A git failure in the ROOT checkout is probably a sibling shipping this second — wait
  and retry, never force.
- If the card isn't where triage saw it, another session took it — stop and report.
- Design as if you own any shared resource; if your design touches one, say so at the
  checkpoint — the overseer sequences the edits, that's what it's for.

And the checkpoint contract:

> CHECKPOINT — MANDATORY: research + design only, then END YOUR TURN with your plan
> (context, file-by-file changes, verification, out-of-scope). No implementation and no
> card comment until the overseer approves. At ANY point, on any question or surprise,
> end your turn and ask — never guess, never expand scope silently. After approval:
> implement, verify, run /review and fix findings, ship per the runbook, do the card
> paperwork, report with commit hashes. Follow-up work you discover is PROPOSED, not
> filed — the overseer rules, and often the ruling is "extend your scope and do it now".

## 3 — Review plans

Check the plan against what the card *actually* asks. Your approval satisfies the
runbook's "align with the user before writing code" for in-scope work — say so in the
approval; only scope, product feel, and outward-facing calls go up to the user. Broad
sweeps prove the change on a small sample before running wide; a surprising verification
result on the pilot means STOP and escalate — the card's own claim about how its work
can be verified may be wrong.

Run the overthink check on every plan: **is this the design the agent would build with no
batch constraints?** Have it name any constraint that shaped the design; the tell of a
bent one is an estimator, inference, or tunable threshold standing where the
authoritative side already knows the value. Test named constraints against what you
verified with the user at triage; a new one you're unsure of goes to the user, and the
answer joins your running list. When a constraint of yours shaped the design, the fix is
to lift or sequence it — not to admire the workaround.

## 4 — While agents run

- Rule on every question a stopped agent raises; the agent stays paused while you do.
- Tell agents in the spawn prompt that beyond the checkpoint, you're available as an
  **advisor** on hard calls mid-implementation — Anthropic's advisor strategy: the
  executor escalates decisions it can't reasonably solve to a stronger model. Matters
  most when the fleet runs a cheaper tier than you.
- Escalate user-owned calls via the ask-a-question tool. Don't rule on the user's behalf.
- After EVERY agent completion, re-list the backlog and diff against the triage
  snapshot — a report is a lead, never the inventory; agents file cards their reports
  undersell or omit. Rule on anything new per [`filed-cards.md`](filed-cards.md) while
  the filer is still warm and resumable — "file a card" is not the default outcome.
- Agents run `/review` themselves; if it fails, they say so in their report and you
  cover it.
- A card agent hitting a real tooling gap gets a separate agent to build the
  capability — don't let it bodge around the gap.

## 5 — Batch boundary

In order, before the next batch:

1. **Review the merged diff yourself** — you have cross-card context the per-card
   reviews lacked. Spot-check anything other agents consume (the runbook especially) as
   soon as it lands.
2. **Run the batch smoke** — one batch-level headless smoke beats per-card browser
   passes.
3. **Trim the batch's CLAUDE.md changes** — agents bloat docs with their change's story:
   what they didn't end up doing, how it used to work, function-level detail. Keep the
   durable rule or gotcha, cut the narrative.
4. **Groom the backlog the batch grew** — per [`filed-cards.md`](filed-cards.md).
5. **Sweep the in-progress list for orphans** — a dead or stalled agent leaves its card
   claimed, and the board can't tell that from healthy work. Match every card to a live
   agent; for the rest, read the branch first, then finish or return the card. Never
   re-spawn onto an orphan's half-built worktree blind.
6. **Grow the user checklist** — anything needing the user's hands accumulates into ONE
   checklist, delivered at end of run and organized so a single pass covers it — never a
   trickle of per-card test requests.
7. **PAUSE via the ask-a-question tool: "continue or compact?"** — it doubles as the
   batch-done notification. Only the USER can /compact; you have no tool for it. If they
   pick compact, hand them a ready-to-type `/compact` naming what to keep (queue,
   rulings, user checklist, anything not yet durable) and what to drop (shipped cards'
   play-by-play, plan texts, tool output). Wait for the answer — a batch launched
   pre-compact re-bloats the window.

A long run WILL get its context summarized, so by each batch's end everything a
post-summarization overseer needs — batch state, rulings given, deferred cards, the user
checklist — lives in card comments, PRs and filed cards, never only in conversation.
