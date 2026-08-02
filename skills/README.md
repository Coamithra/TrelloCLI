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

Then put a copy of `CONTRIBUTING.example.md` where the skills expect the generic runbook
(`~/.claude/CONTRIBUTING.md`), adapt its specifics, and give each project a `CLAUDE.md`
that names its board id, backend, columns, default branch, and verification gate.
`/grab` works a single card; `/farm` runs the fleet.

## A note on style

These are living documents grown out of real runs, deliberately kept short: they encode
the surprises (things that actually bit), not the procedures a capable model derives
itself. **If a rule is one the model would have followed anyway, it doesn't earn its
lines** — every kept sentence should change behaviour. `farm`'s "Learnings from live runs"
is the scar tissue of many sessions. Resist adding scaffolding these files don't have —
that's a design decision, not an omission.

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
