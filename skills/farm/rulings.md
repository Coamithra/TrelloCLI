# Ruling on what a batch produced

Companion to [`SKILL.md`](SKILL.md), which stays the orchestration spine. Read this at a
batch boundary, before the first ruling of the batch — it is the taxonomy for cards and
proposals that arrive *during* a run, which is where an overseer's judgement is most
load-bearing and least derivable from first principles.

## Discovery is a board diff, not trust in reports

Snapshot the backlog's card ids at triage; after EVERY agent completion — not at batch
end — re-list the backlog and diff against the known set, and rule on anything new
immediately. Agents file cards their reports undersell or omit (and the
propose-don't-file contract only helps for agents spawned after it existed), so the
report is a lead, never the inventory. Ruling per-completion matters because the
bounce-back bin is only cheap while the filing agent is still warm and resumable; a card
discovered at batch end has already lost that option's main value.

## Every agent-proposed follow-up gets an explicit ACCEPTANCE ruling — and "file a card" is not the default outcome

The failure mode, seen live: an agent fixes 14 silently-rejecting debug flags, then files
"the REST of the flag families still reject silently" — a finish-my-job card. It had the
pattern, the helper, the probe rig and the full context loaded; the remaining families
were mechanical repetition, and carding them converts an hour of warm-context work into a
cold research phase for some future agent.

Rule each proposal into one of four bins, **in this order** — the decline bin comes first
because the other three quietly assume the work is worth doing by someone.

### 1. DECLINE — "if it ain't broke"

*What actually breaks if we never do this?* Agents systematically over-file here — they
surface a symptom faithfully, then propose fixing it without ever asking whether it has a
victim. The tells: it is transient (a burst at startup or on a reset that settles and
never returns), it is cosmetic (a counter, a log line, an internal name no player and no
future reader is misled by), it is theoretical (an edge nothing reachable produces), or
the proposal's own evidence says "no adverse effect" in passing and then carries on
regardless. Decline those out loud and record why ON the card — a written decline is what
stops the next agent that trips over the same symptom re-filing it.

**The one thing worth rescuing from a decline, and it is easy to miss:** "nothing is
broken" is not "nothing is lost". Check whether the benign symptom shares a CHANNEL with a
real fault — one counter, one log line, one error path serving both — because declining
then trains everyone to ignore the alarm that would have caught the real thing. When that
is the case the right card is usually far SMALLER than the one proposed: split the channel
so the benign case names itself, and drop the investigation half entirely. (Live example:
a duplicate-spawn counter fired on ordinary respawns AND on a protocol mismatch. The filed
card said "reproduce the burst and explain it"; the useful card was "split the counter by
cause" — an hour, headless, and it made the repro unnecessary because the counter now
states which case it is.)

**Taste calls are the user's, not yours.** If the decline turns on how something should
FEEL or read rather than on whether it works — wording, how generous a game rule is,
whether a stall is annoying — put the options to them in a sentence and let them rule.
Don't quietly bin it, and don't quietly build it.

### 2. BOUNCE IT BACK — "you just do it, same branch or a fresh one"

For a mechanical continuation of the work just done: same pattern, same files, same
verification rig. An agent's "larger blast radius" hesitation is usually deference, not a
real boundary.

### 3. NOT CARD-WORTHY

Too insignificant to justify a future session's claim-research-worktree overhead. Do it
yourself in the root checkout, fold it into a sibling's scope, or drop it and say so.

### 4. ACCEPT

Genuinely separate work: a different kind of task (a defect discovered vs. the sweep that
found it), a different code area, gated on something, or big enough to deserve its own
plan review. Legitimacy test: would you have chartered this card on its own merits during
triage, or does it only exist because an agent stopped at its card's literal edge?

## Groom the backlog the batch just grew

Card agents reliably file follow-up cards (a good instinct — don't suppress it), but they
file them from a single card's viewpoint. You have the cross-batch view and the stronger
model, so give the new arrivals a once-over: merge cards that one session could do and
verify in one go (same code area, or a handful of small independent fixes); close or trim
cards already covered by in-flight or existing backlogged work; and sanity-check each
card's premise while the context is still fresh — a correction now is a one-line edit, the
same correction next month is a wasted research phase.
