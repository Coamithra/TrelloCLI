---
name: review
description: Spawn a fresh agent to review the current branch diff against main with no prior context, catching logic errors, missed edge cases, convention violations, and naming issues that the working session has gone blind to. Then fix every finding before proceeding.
argument-hint: (no arguments)
---

# Branch Diff Review

A fresh agent reviews `git diff main...<current-branch>` with no prior context. That blank-slate perspective catches things the working session has stopped seeing — logic errors, missed edge cases, convention violations, awkward naming. You then fix every finding before continuing.

## Step 1: Identify the Branch and Base

Determine the current branch and the repo base branch (`main` or `master`; default to `main`
if unclear), and confirm the diff is non-empty.

If the branch *is* the base branch (e.g. you're on `main`), tell the user there is nothing to review and stop. Do not invent a comparison.

If the diff is empty, tell the user and stop.

## Step 2: Spawn the Reviewer Agent

Use the `Agent` tool with `subagent_type: "general-purpose"` and `model: "opus"`. Always pass `model: "opus"` explicitly — omitting it makes the reviewer inherit the session model, and if the session runs on a pricier model (e.g. Fable) that's wasted money. Never silently upgrade the reviewer past Opus, no matter how important the diff seems. Only use a different model if the user explicitly asked for one when invoking this skill (e.g. "/review with haiku"). The agent must start cold — do **not** summarise your understanding of the changes for it. Hand it the raw diff and let it form its own opinion. That's the entire value of this skill.

Prompt template:

```
Review the diff for branch `<branch>` against `<base>`. Run:

  git diff <base>...HEAD

Read the full diff. For larger changes, also open the changed files at HEAD to see the surrounding context — diff hunks alone hide a lot.

You have NO prior context on this work. That is the point: flag anything that
looks off to a fresh reader. Specifically look for:

- Logic errors and off-by-ones.
- Missed edge cases (empty inputs, None, unicode, concurrency, error paths,
  partial failures).
- Convention violations vs. the rest of the codebase (skim neighbouring files
  in the same module to learn the local style — naming, error handling,
  logging, type hints, test layout).
- Naming issues — misleading, vague, inconsistent, or stale identifiers.
- Dead code, leftover debug prints, commented-out blocks, TODOs that should be
  resolved.
- Comments that lie, restate the obvious, or rot easily ("used by X", task
  references).
- Tests: missing coverage for new branches, tests that don't actually assert
  the behaviour they claim, brittle assertions.
- Public API surface: backwards-incompatible changes that aren't called out,
  signatures that leak internals.
- Security and resource handling: injection vectors, unclosed handles,
  unbounded allocations, secrets in logs.

Report findings as a numbered list. For each:

  N. <file>:<line> — <one-line summary>
     <2-4 sentences explaining the problem and a concrete fix>
     Severity: blocker | should-fix | nit

Be direct. No preamble, no "overall the code looks good" filler. If you find
nothing in a category, skip it. If the diff is genuinely clean, say so in one
line.
```

Run the agent in the foreground — you need its findings before you can act.

## Step 3: Triage the Findings

Read every finding. For each, decide:

- **Fix now**: blocker, should-fix, and nit findings whose fix is local and obvious. Default to fixing nits too — the user explicitly asked for "even minor ones".
- **Follow-up**: legitimate feedback you're not actioning right now. Includes findings whose fix is a major undertaking (cross-cutting refactor, design change, requires user input on direction), and findings that are real but too cosmetic / out of scope / too low priority for this branch. These will become Trello checklist items in Step 6.
- **Disagree (moot)**: the reviewer was wrong, missed context, or the point doesn't apply. Be explicit with reasoning — don't silently skip. These do **not** become follow-ups; the point is moot, not deferred.

Show the user the triage as a short list before you start editing, so they can intervene if something looks wrong.

## Step 4: Apply Fixes

Make the edits. After each batch, re-run the project's check commands (lint, type-check, tests) to confirm nothing regressed.

If a fix turns out to be larger than expected mid-edit, stop, revert that fix, and demote it to a follow-up. Don't half-finish.

## Step 5: Report Back

End with a short summary:

- Findings: N total (B blockers, S should-fix, K nits).
- Fixed: list of fixes applied (file:line — one line each).
- Follow-ups: list of deferred items with a one-line reason each.
- Disagreed: any findings dismissed as moot, with one-line reasoning.
- Verification: which checks you ran and the result.

Do not commit unless the user asks.

## Step 6: Capture Follow-ups in Trello

If — and only if — the project has an associated Trello board AND there is at least one item in the **Follow-up** bucket from Step 3, create a Trello card so the deferred feedback isn't lost.

**Detect the board.** Look for a Trello reference in the project: a board id, board URL, or `trello use <id>` instruction in `CLAUDE.md`, `README.md`, `CONTRIBUTING.md`, or similar root-level docs. If nothing's there, skip this step silently — don't dump cards into a random default board. If `trello` isn't on PATH, also skip (and mention it in the summary).

**What goes on the card.** Only the Follow-up bucket. Do NOT include:
- Anything you actually fixed in Step 4.
- Anything in the Disagreed (moot) bucket — the reviewer was wrong, there's nothing to track.

DO include cosmetic, out-of-scope, and "too low priority for this branch" items — that's the whole point of the card. The bar is "is this real, legitimate feedback?", not "is it important?".

**Create it.** Pick a target list with `trello --board <id> list ls` — prefer something like `Backlog`, `To Do`, `Inbox`, or the leftmost non-Done list. If no obvious target exists, ask the user.

```bash
# Card name: short, references the branch.
trello --board <id> card add "<list>" "Review follow-ups: <branch>" "<description>"

# Capture the new card id from the output, then attach a checklist.
trello --board <id> checklist add <card_id> "Reviewer feedback"
trello --board <id> checklist item add <card_id> "Reviewer feedback" "<file>:<line> — <one-line summary>"
# ...one item per follow-up.
```

Description should give a future reader enough to pick it up cold: branch name, base, the commit SHA at HEAD, and a one-line "what this branch did" so the checklist items have context. Pass real newlines, not `\n` escape sequences (the CLI wants real newlines).

Mention the card (name + URL or id) in the Step 5 summary so the user can see where the follow-ups landed.

## Notes

- The reviewer agent runs cold by design. Do not pre-digest the changes for it.
- One agent is usually enough. Spawn a second only if the diff is huge (say >2000 lines) and you want to split it by directory — in that case, give each agent a disjoint file list.
- This skill is for self-review during active work. It is not a replacement for heavier multi-agent review or human PR review.
