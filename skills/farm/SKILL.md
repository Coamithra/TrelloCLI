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
- **After every batch, groom the backlog the batch just grew.** Card agents reliably
  file follow-up cards (a good instinct — don't suppress it), but they file them from
  a single card's viewpoint. You have the cross-batch view and the stronger model, so
  give the new arrivals a once-over: merge cards that one session could do and verify
  in one go (same code area, or a handful of small independent fixes); close or trim
  cards already covered by in-flight or existing backlogged work; and sanity-check
  each card's premise while the context is still fresh — a correction now is a one-line
  edit, the same correction next month is a wasted research phase.
- **Every agent-proposed follow-up gets an explicit ACCEPTANCE ruling from the
  overseer — and "file a card" is not the default outcome.** The failure mode (seen
  live): an agent fixes 14 silently-rejecting debug flags, then files "the REST of
  the flag families still reject silently" — a finish-my-job card. It had the
  pattern, the helper, the probe rig and the full context loaded; the remaining
  families were mechanical repetition, and carding them converts an hour of warm-
  context work into a cold research phase for some future agent. Rule each proposal
  into one of three bins: **(a) bounce it back** — "you just do it, same branch or a
  fresh one" — when it's a mechanical continuation of the work just done (same
  pattern, same files, same verification rig); an agent's "larger blast radius"
  hesitation is usually deference, not a real boundary. **(b) not card-worthy** — too
  insignificant to justify a future session's claim-research-worktree overhead; do it
  yourself in the root checkout, fold it into a sibling's scope, or drop it and say
  so. **(c) accept** — genuinely separate work: a different kind of task (a defect
  discovered vs. the sweep that found it), a different code area, gated on something,
  or big enough to deserve its own plan review. Legitimacy test: would you have
  chartered this card on its own merits during triage, or does it only exist because
  an agent stopped at its card's literal edge?
  **(d) DECLINE — "if it ain't broke".** Ask this BEFORE the other three bins, because
  all three quietly assume the work is worth doing by someone: *what actually breaks if
  we never do this?* Agents systematically over-file here — they surface a symptom
  faithfully, then propose fixing it without ever asking whether it has a victim. The
  tells: it is transient (a burst at startup or on a reset that settles and never
  returns), it is cosmetic (a counter, a log line, an internal name no player and no
  future reader is misled by), it is theoretical (an edge nothing reachable produces),
  or the proposal's own evidence says "no adverse effect" in passing and then carries on
  regardless. Decline those out loud and record why ON the card — a written decline is
  what stops the next agent that trips over the same symptom re-filing it.
  **The one thing worth rescuing from a decline, and it is easy to miss:** "nothing is
  broken" is not "nothing is lost". Check whether the benign symptom shares a CHANNEL
  with a real fault — one counter, one log line, one error path serving both — because
  declining then trains everyone to ignore the alarm that would have caught the real
  thing. When that is the case the right card is usually far SMALLER than the one
  proposed: split the channel so the benign case names itself, and drop the
  investigation half entirely. (Live example: a duplicate-spawn counter fired on
  ordinary respawns AND on a protocol mismatch. The filed card said "reproduce the burst
  and explain it"; the useful card was "split the counter by cause" — an hour, headless,
  and it made the repro unnecessary because the counter now states which case it is.)
  **Taste calls are the user's, not yours.** If the decline turns on how something should
  FEEL or read rather than on whether it works — wording, how generous a game rule is,
  whether a stall is annoying — put the options to them in a sentence and let them rule.
  Don't quietly bin it, and don't quietly build it.
  **Discovery is a board diff, not trust in reports.** Snapshot the backlog's card
  ids at triage; after EVERY agent completion — not at batch end — re-list the
  backlog and diff against the known set, and rule on anything new immediately.
  Agents file cards their reports undersell or omit (and the propose-don't-file
  contract only helps for agents spawned after it existed), so the report is a lead,
  never the inventory. Ruling per-completion matters because the bounce-back bin is
  only cheap while the filing agent is still warm and resumable; a card discovered
  at batch end has already lost that option's main value.
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

Card agents run on **Opus** — pass `model: "opus"` EXPLICITLY on every spawn, never
omit it: an omitted model INHERITS the overseer session's model, and if that session is
Fable, a batch of card agents burns through Fable token limits doing implementation
work Opus handles fine. The overseer stays on the session model; the fleet is Opus.
(A user-named model overrides, as with any argument.)
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
backlog groom done, next batch NOT yet launched), PAUSE via the ask-a-question tool — "continue or
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
