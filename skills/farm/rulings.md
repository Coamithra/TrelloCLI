# Ruling on what a batch produced

Companion to [`SKILL.md`](SKILL.md). Read at a batch boundary, before the first ruling —
this is the part of overseeing least derivable from first principles.

## Discovery is a board diff, not trust in reports

Snapshot the backlog's card ids at triage; after EVERY agent completion — not at batch end
— re-list and diff against the known set. Agents file cards their reports undersell or
omit, so a report is a lead, never the inventory. Per-completion matters because the
bounce-back ruling is only cheap while the filing agent is still warm and resumable.

## Every proposal gets an explicit ruling — and "file a card" is not the default

The failure mode, seen live: an agent fixes 14 silently-rejecting debug flags, then files
"the REST of the flag families still reject silently" — a finish-my-job card. It had the
pattern, the probe rig and the full context loaded; carding the rest converts an hour of
warm-context work into a cold research phase for some future agent.

Four bins, asked in this order.

**1. Decline — "if it ain't broke."** First, because the other three quietly assume the
work is worth doing by someone: *what actually breaks if we never do this?* Agents
systematically over-file here, surfacing a symptom faithfully and proposing a fix without
ever asking whether it has a victim. The tells: it's transient (a burst at startup that
settles and never returns), cosmetic (a counter, a log line, an internal name nobody is
misled by), theoretical (an edge nothing reachable produces), or its own evidence says "no
adverse effect" in passing and carries on regardless. Record the decline ON the card —
that's what stops the next agent tripping over the same symptom and re-filing it.

*The catch worth rescuing, and it's easy to miss:* "nothing is broken" is not "nothing is
lost". If the benign symptom shares a CHANNEL with a real fault — one counter, one log
line, one error path serving both — declining trains everyone to ignore the alarm that
would have caught the real thing. The right card is then far SMALLER than the one
proposed: split the channel so the benign case names itself, and drop the investigation
half. (Live: a duplicate-spawn counter fired on ordinary respawns AND on a protocol
mismatch. Filed card — "reproduce the burst and explain it". Useful card — "split the
counter by cause", which made the repro unnecessary.)

*Taste calls are the user's, not yours.* If the decline turns on how something should FEEL
or read rather than on whether it works, put the options to them in a sentence. Don't
quietly bin it, and don't quietly build it.

**2. Bounce it back** — "you just do it, same branch or a fresh one" — for a mechanical
continuation of the work just done: same pattern, same files, same verification rig. An
agent's "larger blast radius" hesitation is usually deference, not a real boundary.

**3. Not card-worthy** — too small to justify a future session's claim-research-worktree
overhead. Do it in the root checkout, fold it into a sibling's scope, or drop it and say so.

**4. Accept** — genuinely separate work: a different kind of task (a defect discovered vs.
the sweep that found it), a different code area, gated on something, or big enough to
deserve its own plan review. Test: would you have chartered this card on its own merits
during triage, or does it exist only because an agent stopped at its card's literal edge?

## Groom the backlog the batch grew

Agents file follow-ups from one card's viewpoint; you have the cross-batch view. Merge
cards one session could do and verify in one go, close or trim ones already covered by
in-flight work, and sanity-check each premise while the context is fresh — a correction
now is a one-line edit, the same correction next month is a wasted research phase.
