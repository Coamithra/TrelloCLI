---
name: review
description: Spawn a fresh agent to review the current branch diff against main with no prior context, catching logic errors, missed edge cases, convention violations, and naming issues that the working session has gone blind to. Then fix every finding before proceeding.
argument-hint: (nothing = pinned reviewer model) | "with <model>" to override it
---

# Branch Diff Review

A fresh agent reviews `git diff <base>...HEAD` with no prior context. That blank slate
catches what the working session has stopped seeing. You then fix every finding.

## Step 1 — Spawn the reviewer

Find the base branch (`main`/`master`). If you're ON it, or the diff is empty, say so and
stop — don't invent a comparison.

Spawn `subagent_type: "general-purpose"` in the foreground with an **explicitly pinned
`model`**: the cheapest tier that reviews well — today that's `"opus"`. Never silently
upgrade past it, however important the diff seems — only the user asking (`/review with
haiku`) changes it.

**Hand it the raw diff and nothing else.** Do not summarise your understanding of the
changes for it. Starting cold is the entire value of this skill.

Prompt template:

```
Review the diff for branch `<branch>` against `<base>`:

  git diff <base>...HEAD

For larger changes also open the changed files at HEAD — diff hunks hide context.

You have NO prior context on this work. That is the point: flag anything that looks
off to a fresh reader. Beyond the usual (logic errors, edge cases, dead code,
security, resource handling), specifically:

- Convention violations — skim neighbouring files in the same module to learn the
  LOCAL style first, rather than applying general defaults.
- Naming that is misleading, vague, or stale.
- Comments that lie, restate the obvious, or rot ("used by X", task references).
- Tests that don't actually assert the behaviour they claim, or miss a new branch.
- Backwards-incompatible API changes that aren't called out.

Numbered list. Per finding: <file>:<line> — one-line summary, 2-4 sentences on the
problem and a concrete fix, then Severity: blocker | should-fix | nit. No preamble,
no "overall this looks good" filler. If the diff is clean, say so in one line.
```

## Step 2 — Triage, then fix

Sort every finding into three buckets and **show the user the triage before editing**:

- **Fix now** — including nits, when the fix is local and obvious.
- **Follow-up** — real feedback you're not actioning: too large (cross-cutting refactor,
  needs a direction call from the user), too cosmetic, or out of scope for this branch.
  Goes to Step 4.
- **Moot** — the reviewer was wrong or missed context. Say why; don't silently skip, and
  don't file these as follow-ups.

Then apply the fixes. If one turns out larger than expected mid-edit, revert it and demote
it to a follow-up — don't half-finish.

## Step 3 — Report

Findings by severity, fixes applied, follow-ups with a one-line reason each, moot findings
with reasoning, and what you ran to verify. **Do not commit unless the user asks.**

## Step 4 — Capture follow-ups on the board

Only if the project has a Trello board AND the Follow-up bucket is non-empty. Find the
board id in `CLAUDE.md`/`README.md`/`CONTRIBUTING.md`; if there's none, or no `trello` on
PATH, skip silently (mention the latter in Step 3). Never dump cards into a default board.

The bar is "is this real feedback?", not "is it important?" — the cosmetic and
low-priority items are the whole point of the card.

```bash
trello --board <id> card add "<list>" "Review follow-ups: <branch>" "<description>"
trello --board <id> checklist add <card_id> "Reviewer feedback"
trello --board <id> checklist item add <card_id> "Reviewer feedback" "<file>:<line> — <summary>"
```

Target the leftmost non-Done list (`Backlog`/`To Do`/`Inbox`); ask if none is obvious. The
description needs branch, base, HEAD SHA and a one-line "what this branch did" so the
items make sense cold. Pass real newlines, not `\n`. Name the card in your Step 3 summary.

## Notes

- Split across two agents by directory only if the diff is huge (>2000 lines).
- This is self-review during active work, not a replacement for human PR review.
