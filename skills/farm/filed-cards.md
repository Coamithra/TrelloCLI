# Guide for triaging new cards

Companion to [`SKILL.md`](SKILL.md). Read before your first ruling of a run.

## Every proposal gets an explicit ruling — and "file a card" is not the default

The failure mode is the finish-my-job card: an agent cards the mechanical remainder of
its own work, converting warm-context work (pattern, rig and context all loaded) into a
cold research phase for some future agent.

Four bins, asked in this order.

**1. Decline — "if it ain't broke."** Asked first because the other three assume the
work is worth doing: *what actually breaks if we never do this?* Agents over-file
symptoms that have no victim — don't rabbithole. Record the decline ON the card, or the
next agent re-files it. Two catches: a benign symptom sharing a channel (one counter,
log line or error path) with a real fault earns a smaller card — split the channel so
the benign case names itself, skip the investigation; and a decline that turns on taste
rather than function is the user's call — put the options to them, don't quietly bin or
build.

**2. Bounce it back** — "you just do it, same branch or a fresh one" — for a mechanical
continuation of the work just done: same pattern, same files, same verification rig. An
agent's "larger blast radius" hesitation is usually deference, not a real boundary.

**3. Not card-worthy** — too small to justify a future session's claim-research-worktree
overhead. Do it in the root checkout, fold it into a sibling's scope, or drop it and say so.

**4. Accept** — genuinely separate work: a different kind of task (a defect discovered vs.
the sweep that found it), a different code area, gated on something, or big enough to
deserve its own plan review. Test: would you have chartered this card on its own merits
during triage, or does it exist only because an agent stopped at its card's literal edge?
An accepted card only the user can do is tagged `[HUMAN REQUIRED]` and joins the
end-of-run checklist — same rule as triage.

## Then check it against the board

The filer had one card's viewpoint; you have the whole board's. Look for similar cards:
can this one be combined with another into something one session does and verifies in one
go (each spawn must be worth its worktree, plan review and PR)? Does in-flight work
already cover it — close or trim. And sanity-check its premise while the context is
fresh: a correction now is a one-line edit, the same correction next month is a wasted
research phase.
