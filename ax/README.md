# AX testing — can an agent that has never seen this repo drive the CLI?

Every caller of this CLI is an agent. The unit suite in `tests/` proves the code
is correct; it says nothing about whether a model can *find* the right command.
Those are different failure modes, and only one of them shows up in production:

```
$ trello board list
Unknown board command: list. Valid verbs: show, add, rename, archive, restore
```

The code is fine. The tool still cost that agent a turn, because `boards` and
`board` are different commands and nothing said so.

This harness measures that. It fans a corpus of plain-English tasks out across
cold agent runs, checks the *board* afterwards rather than the transcript, and
renders what happened into something small enough to read in one pass.

```
fanout ─► failure modes ─► backlog ─► patch ─► rerun ─► compare
```

## Running it

```bash
python -m ax.runner --cases all --model haiku --parallel 6
python -m ax.runner --cases t1 --model sonnet          # one tier
python -m ax.runner --cases tag:write --repeat 3       # variance on the mutators
python -m ax.report ax/runs/<run-id>                   # re-render without re-running
```

Needs the `claude` CLI on PATH and this package importable (`pip install -e .`).
A full 33-case Haiku run is a couple of dollars and about ten minutes.

## What a run actually is

Each case gets a **fresh seeded store** (`fixture.py`) and a cold agent with:

- a shell, and nothing else — `--tools Bash`
- a `PATH` containing one command, `trello`, wired to this checkout
- `TRELLO_BACKEND=local` + `TRELLO_LOCAL_ROOT` pointed at that store, the way a
  configured machine would have it
- **no** CLAUDE.md, project settings, hooks, MCP servers or skills
  (`--setting-sources "" --strict-mcp-config --disable-slash-commands`)
- **no** hint about which command to use. The task says "move the card", never
  `card move`

So the only things teaching it the tool are `trello --help` and whatever the CLI
says when it gets something wrong. That is the whole point:

> a tool's error messages are the highest-bandwidth in-context learning signal
> it has.

`TRELLO_BOARD` is deliberately *not* set — picking the right board out of two is
part of the task, because forgetting `--board` is the single most common thing an
agent does here.

## What gets measured

| signal | where it comes from |
| --- | --- |
| did it work | `verify()` reads the store back through `LocalBackend` — the board is the judge, not the agent's summary |
| did it say the right thing | `expect` / `forbid` substrings against the final answer (read-only cases) |
| did it do damage | read-only cases fail if the store digest changed at all |
| how expensive | tool calls against `budget`, the count a competent operator needs |
| what it hit on the way | error classes, first command reached for, retries |
| did it cheat | commands touching the package source are flagged; a run that read `local.py` isn't measuring AX any more |

`budget` is a *median health* number, not a pass/fail gate. Overrunning it is the
interesting middle: the tool was learnable, but only after a detour, and the
detour is the backlog item.

## The corpus

`cases.py`, three tiers:

- **tier 1 — reads.** If these aren't near-100% the tool is unusable by agents.
- **tier 2 — one mutation each.** The store is checked afterwards.
- **tier 3 — multi-step or discovery.** Things like "claim the top ticket without
  another agent taking the same one" (does it find `grab`?), bulk edits, and a
  couple of tasks the CLI has no direct verb for, to see what it improvises.

Cases are written the way a person would say it, with the CLI's vocabulary
deliberately avoided — "get it off the board without destroying it" instead of
"archive it". If a case only passes when the prompt uses the command's own noun,
that is a finding about the tool, not a badly written case.

## Reading the output

```
ax/runs/<run-id>/
  index.md          scoreboard, error classes, failing transcripts inlined
  corpus.md         every transcript, passes included
  summary.json      the numbers, for comparing runs
  results.json      per-run records
  <case-id>/
    transcript.md   the compact rendering (~50-100x smaller than the raw log)
    result.json     verdict + commands + cost
    trace.jsonl     raw stream-json (gitignored)
    store/          the board as the agent left it (gitignored)
```

`index.md` is built to be pasted into a model with "why did these fail?".

## Adding a case

```python
Case(
    id="t2-due",
    tier=2,
    prompt="The card 'Fix login bug' on the Roadmap board is due tomorrow — set that.",
    budget=4,
    verify=lambda s: None if (s.card("Fix login bug") or {}).get("due") else "no due date set",
    tags=["write", "card", "date"],
)
```

A verifier returns `None` to pass or a string explaining what it found — that
string lands in the report, so make it say the actual state, not "failed".

## Caveats

- Runs are hermetic against the **local** backend. Trello-backend AX (credentials,
  network errors, rate limits) is not covered.
- One run per case by default; models are stochastic. Use `--repeat` before
  concluding a single failure is a real regression.
- The sandbox is not a jail. An agent *can* go read the package source; the
  harness flags it rather than preventing it.
