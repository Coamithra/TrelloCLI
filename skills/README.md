# Agent skills

Three [Claude Code skills](https://docs.anthropic.com/en/docs/claude-code) that, together
with a board managed by this CLI and a copy of [`CONTRIBUTING.example.md`](../CONTRIBUTING.example.md),
make up a complete card → worktree → PR workflow for AI coding agents — solo or as a
supervised parallel fleet.

| Skill | What it does |
|---|---|
| [`grab`](grab/SKILL.md) | Atomically claim one card and implement it end to end via the runbook |
| [`review`](review/SKILL.md) | Spawn a fresh zero-context agent to review the branch diff, then fix every finding |
| [`farm`](farm/SKILL.md) | Farm a whole backlog out to parallel per-card agents while acting as overseer — plan checkpoints, mid-flight rulings, batch review |

## Install

Copy each folder into your user-level skills directory:

```
~/.claude/skills/grab/SKILL.md
~/.claude/skills/review/SKILL.md
~/.claude/skills/farm/SKILL.md
~/.claude/skills/farm/rulings.md
```

Then wire up the environment the skills assume — they deliberately do not carry it
themselves:

1. **The generic runbook.** Put an adapted copy of `CONTRIBUTING.example.md` at
   `~/.claude/CONTRIBUTING.md`.
2. **Your global `~/.claude/CLAUDE.md`.** The skills (`farm` especially) don't re-explain
   the contributing flow or where project specifics live — that's standing knowledge
   every session should have, so it belongs in your global memory, not repeated per
   skill. Add a block like:

   > **Contributing workflow:** projects with a GitHub repo and a Trello board follow a
   > shared card → worktree → PR workflow; the generic runbook is
   > `~/.claude/CONTRIBUTING.md`. Before picking up a task, read the project's local
   > `CLAUDE.md` for its board id, backend (remote `trello` vs `--backend local`), list
   > names, default branch, worktree layout + per-worktree bootstrap, and verification
   > gate. Where they differ, the project file wins. No board or repo → the runbook
   > doesn't apply.

3. **Each project's `CLAUDE.md`.** Name its board id, backend, columns, default branch,
   and verification gate.

`/grab` works a single card; `/farm` runs the fleet.

## A note on style

These are living documents grown out of real runs, deliberately kept short: they encode
the surprises (things that actually bit), not the procedures a capable model derives
itself. **If a rule is one the model would have followed anyway, it doesn't earn its
lines** — every kept sentence should change behaviour. The scar tissue of many live farming runs
is folded directly into `farm`'s numbered steps. Resist adding scaffolding these files don't have —
that's a design decision, not an omission.

The same test applies to *facts about your environment*: anything every session already
knows from your global `CLAUDE.md` (like the contributing-workflow block above) is not
repeated in a skill. A skill states the decisions unique to its job; the environment
states itself once.

`farm` strains hardest against this, since an overseer needs more in context than an
implementer. Its answer is progressive disclosure: `SKILL.md` is the orchestration spine,
and the taxonomy for ruling on cards that arrive mid-run lives in
[`farm/rulings.md`](farm/rulings.md), loaded at a batch boundary. Prefer that split to
letting the spine grow.

Model names are pinned as *rules with a current answer* ("the cheapest tier that reviews
well; today that's Opus"), because a bare name goes stale silently and expensively. When
the lineup shifts, update the name — don't drop the pin.

The origin story is on
[the author's devblog](https://haraldmaassen.com/devblog).
