---
name: farm
description: Farm a project's Trello backlog out to parallel per-card background agents while acting as overseer and voice of reason — each agent must stop and get its plan critically reviewed before writing code; the overseer rules on mid-flight questions and reviews the merged result. Use whenever the user wants multiple backlog cards/tickets worked at once — "make a dent in the backlog", "farm this out", "batch the backlog", "spin up agents on the board", "work through the backlog with agents" — or wants you to supervise/oversee parallel card agents. For implementing a SINGLE card end to end, use /grab instead.
argument-hint: (nothing = triage whole backlog) | batch size | "only the <topic> cards" | model to use for agents
---

# Farm the backlog — parallel card agents with an overseer

You are the **overseer**, not an implementer. Spawn one background agent per card;
spend your own effort on triage, plan review, mid-flight rulings, and reviewing the
merged result. The core contract: **every agent stops after research+design and gets
its plan critically reviewed against the card before writing code** — that checkpoint
is where agent drift (shortcuts, rabbit holes, headscratchy designs) is cheapest to
catch. Approval is the default outcome, but earn it: check the plan against what the
card *actually asks*, and rule decisively on every question the agent raises. You're
the only one who sees every plan — a quick glance across them catches collisions no
per-card review can.

Resolve project specifics exactly as `/grab` step 1 does (project `CLAUDE.md` +
`CONTRIBUTING.md` + your shared generic runbook at `~/.claude/CONTRIBUTING.md` — start
from this repo's `CONTRIBUTING.example.md`; project file wins; no board/repo → stop). Honor the invocation arguments: a batch size, a topic filter ("only the net
cards"), or an agent model are constraints, not suggestions. Batch to your review
bandwidth — a handful of agents whose plans you can review promptly — never "spawn
the whole backlog". Cards the user must handle themselves (human hand/eye, taking
over the screen, deploys and other outward-facing actions) are not farmed out — list
them loudly.

Two authority clarifications the runbook needs from you: (1) the generic runbook's
Phase 3 says "align with the user before writing code" — in a farm, YOUR approval
satisfies that for in-scope work; only user-owned decisions (scope, product feel,
outward-facing actions) still go up. Say so in the spawn prompt. (2) When a stop
reveals a card's premise was wrong, the correction goes on the card as a comment —
an audit trail, not a rewrite of the card's text.

## Learnings from live runs (the non-obvious part — read these)

These are things that actually bit or surprised a real farming session; everything
else about orchestration you can work out yourself.

- **Cards drift between filing and pickup — verify their facts before spawning, and
  check OPEN PRs, not just the board.** Real cases from one session: a card's
  "verified" leftover directory had already been deleted; a doc a card wanted
  recovered from git history had never been committed at all; a card's entire premise
  (files replaced on main) was actually sitting in someone's open unmerged PR, which
  also duplicated the card's planned `.gitignore` change. Cheaply verify what you can
  during triage, put corrections in the agent's prompt, and tell agents to re-verify
  the card's remaining claims rather than plan on top of them.
- **A card's claims about its own verification oracle can be wrong.** One card
  asserted its refactor class "should be fully provable by the hash oracle"; a pilot
  showed the oracle only covers a subset. The pattern that worked: agents run a small
  pilot before a big sweep, STOP on a surprise verdict instead of iterating past it,
  and the work gets restructured around the discovered rule — with the card's record
  corrected in place so the wrong premise dies there.
- **Pre-assign worktrees and branch names in the spawn prompts** (per the project's
  worktree layout — fixed slots where the project uses them, branch-named dirs where
  it doesn't). Simultaneously launched agents racing "pick the lowest free slot" is a
  known failure mode; naming them costs nothing. Serialize broad sweeps over the same
  tree into different batches; for small known overlaps (two cards adding `.gitignore`
  lines) just warn both agents the conflict is additive.
- **Parallel agents shipping simultaneously can contend on the ROOT checkout** (the
  runbook's Phase 6 has each agent pull/fast-forward it and clean up). Tell agents: a
  git failure there (index.lock, non-fast-forward) is probably a sibling shipping at
  the same moment — wait a moment and retry, never force. And when claiming, verify
  the card is still where triage saw it; if it moved, another session took it — stop
  and report, don't move it anyway.
- **`/review` runs fine from inside a subagent** — have agents run it themselves.
  Give them the fallback anyway (say so in the final report, overseer covers it) so a
  failure doesn't stall the card.
- **Agents will sometimes do silently-destructive git things** (one dropped a stash
  unannounced and tripped the harness's security flag). Put the rule in the spawn
  prompt from the first batch: narrate any discard BEFORE doing it, never touch
  stashes or dirty state you didn't create — and correct violations immediately even
  when nothing was lost.
- **One batch-level smoke beats per-card browser passes.** Where the project has a
  headless route, per-card browser checks are mostly waste for pure-code cards: run
  the project's cheapest gate once on the merged combination at batch end. The user
  will push back on browser passes that headless tooling could cover — default to
  headless, and when a card agent hits a genuine tooling gap, spawn a separate agent
  to build the capability rather than letting the card agent bodge around it.
- **Escalate to the user only what is genuinely theirs, but do escalate those — via
  the ask-a-question tool, not prose.** The real examples: merging a PARKED PR that
  explicitly asks for a human check (a listen-through), and timing an outward-facing
  deploy. A question embedded in a status report gets missed (happened live; the user
  had to ask for a repeat); the question tool pops the session up in the desktop app
  and makes the options explicit. An agent blocked on such a call stays paused while
  you ask — don't rule on the user's behalf, and don't let the agent pick "whatever
  unblocks me".
- **Every card and proposal that arrives DURING the run gets an explicit ruling from
  you — and "file a card" is not the default outcome.** Card agents reliably propose
  follow-ups (a good instinct — don't suppress it) and sometimes file them anyway,
  always from one card's viewpoint; you have the cross-batch view and the stronger
  model. **Read [`rulings.md`](rulings.md) (next to this file) before the first ruling
  of a batch** — it holds the four bins (decline / bounce back / not card-worthy /
  accept), the board-diff discovery rule, and the backlog groom, kept out of here so
  this file stays the orchestration spine.
- **Claim-first means a dead agent leaves an ORPHANED card — reconcile the in-progress
  list at every batch boundary.** Claiming happens before any work (that's what stops
  collisions), so an agent that dies, stalls, or ends its turn without shipping leaves
  a card parked in the in-progress column with nobody working it — and nothing in the
  board's own state distinguishes that from healthy in-flight work. At each boundary,
  list that column and match every card to a live agent you spawned. For each with no
  live agent, look at its branch/PR before touching the card: shipped but paperwork
  missed → finish the paperwork and move it to Done; partial work → comment what
  exists and where (branch, worktree slot, how far the runbook got), then move it back
  to the backlog so it can be re-claimed cleanly; nothing at all → move it back and
  free the slot. Never re-spawn onto an orphan without doing that first — an agent
  inheriting a half-finished worktree it didn't build is worse than a cold restart.
- **After every batch, review and TRIM the batch's CLAUDE.md changes.** Card agents
  reliably bloat the docs with their own change's story: what they did NOT end up doing,
  how it USED to work, and noodly function-level detail — all meaningless to a fresh
  agent reading the doc cold. Keep the durable rule/constraint/gotcha, cut the
  narrative, and move genuinely deep detail into subdocuments (or the card's paper
  trail, which is where history belongs). Use judgement: a documented negative result
  that stops the next agent re-deriving it earns its lines; a diary of the diff doesn't.
- **User verification time is scarce — batch it into one session.** Anything that
  genuinely needs the user's hands or eyes (feel checks, listen-throughs, the cards
  you didn't farm out) accumulates into ONE consolidated checklist delivered at the
  end of the run, organized so a single playthrough covers it all — not a trickle of
  small per-card test requests.

## Spawn prompt — what each agent needs

**Pin the fleet's model EXPLICITLY on every spawn, never omit it.** An omitted model
INHERITS the overseer session's model, so a whole batch of card agents silently bills at
whatever tier the overseer happens to run on — and when that is a top tier, a fleet burns
through its token limits doing implementation work a cheaper tier handles fine. Pick the
cheapest tier that implements well and name it; today that is `model: "opus"`. The
overseer stays on the session model; the fleet is pinned. (A user-named model overrides,
as with any argument.)
Self-contained (agents inherit none of your context): repo path + card id + the
runbook reading list; the claim command (a pre-assigned card is `card move`d, never
`grab`bed); the assigned slot + branch; the project's verification doctrine restated
as direct instructions (including anything banned outright — doc rules agents merely
*read* get under-weighted); your stale-fact corrections and cross-card warnings; and
the process contract:

> CHECKPOINT — MANDATORY: do research + design only, then END YOUR TURN with your
> plan (context, file-by-file changes, verification, out-of-scope). No implementation
> and no card comment until the overseer approves. At ANY point, on any question or
> surprise — a failed claim, an oracle verdict you didn't predict, a collision with
> someone else's work — end your turn and ask the overseer; never guess, never expand
> scope silently. After approval: implement, verify, run /review and fix findings,
> ship per the runbook, do the card paperwork, report with commit hashes. Follow-up
> work you discover is PROPOSED, not filed: put it in your plan or final report as a
> proposal and the overseer rules — often the ruling is "extend your scope and do it
> now" (you have the context loaded) or "too small to card", so don't create the
> card yourself unless the overseer accepts it as genuinely separate work.

A long farm run WILL eventually get its context summarized (the harness does this
automatically; only the USER can run /compact — you have no tool for it). Design for
it: by the end of each batch, everything a post-summarization overseer needs — batch
state, rulings given, deferred cards, the accumulating user checklist — must live in
durable surfaces (card comments, PR descriptions, filed follow-ups), never only in
conversation memory. Then, at every batch boundary (batch review + smoke + doc trim +
backlog groom + orphan sweep done, next batch NOT yet launched), PAUSE via the ask-a-question tool — "continue or
compact?" — which also pops the session up in the desktop app, so it doubles as the
batch-done notification. If the user picks compact, reply with a ready-to-type
invocation that names what to keep, e.g.:

> /compact Keep: the backlog triage and queue (which cards are next, slot plan),
> every ruling/process-correction made this run, the user-verification checklist so
> far, and anything flagged as not-yet-durable. Drop: shipped cards' play-by-play,
> plan texts, and tool output — those live in the PRs and card comments.

Wait for the user's answer (compact done, or "keep going") before spawning the next
batch — the pause is the point; a batch launched pre-compact re-bloats the window.

After the batch merges: review the combined diff yourself (you have cross-card
context the per-card reviews lacked — spot-check anything other agents consume, like
the runbook, as soon as it lands rather than at batch end), run the batch smoke, roll
the next batch, and report to the user outcome-first with what-needs-you unmissable.
