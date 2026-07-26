# AX findings — round 1 (Haiku 4.5, 35 cases)

The first pass of `fanout → failure modes → backlog → patch → rerun`. Baseline
run: `runs/baseline-haiku`. Patched run: `runs/patched-haiku`. Comparison:
`runs/compare-round1.md`.

## The headline is not the pass rate

The baseline scored **34/35**. That number is nearly worthless on its own — a
capable model will brute-force its way to the right board state eventually. What
it cost to get there is the actual measurement, and it is what moved:

| | baseline | patched | |
| --- | --- | --- | --- |
| passed | 34 | 35 | +1 |
| tool calls (budget: 146) | **212** | **121** | **−43%** |
| tool errors | 23 | 7 | −70% |
| runs hitting at least one error | 16 / 35 | 3 / 35 | −13 |
| runs over budget | 24 / 35 | 3 / 35 | −21 |
| calls over budget | 71 | 5 | −93% |
| runs that gave up and read the store files | 1 | 0 | −1 |

Same corpus, same model, same prompts — only the CLI's surface changed. The 91
calls that disappeared were pure tax: turns real callers were paying to
rediscover, one error at a time, things the tool could simply have told them.

Fifteen runs went from "hit an error" to "clean": `t1-card-detail`, `t1-labels`,
`t1-json`, `t1-mine`, `t2-move-card`, `t2-rename-card`, `t2-due`,
`t2-archive-card`, `t2-add-board`, `t3-grab`, `t3-new-label-apply`,
`t3-checklist`, `t3-attachment`, `t3-reorder-relative`, `t3-move-across-boards`.
`t1-json` alone went from 6 calls and 3 errors to 1 call and none.

---

## F1 — `card ls` with no list was an error (10/35 runs)

The single most common wrong turn, by a wide margin:

```
$ trello --board Roadmap card ls --json
Usage: trello card ls <list_name_or_id> [--with-comment]
```

Ten separate runs asked for the board's cards without naming a column, because
"what's on this board" is the first question anyone has. There was no command
for it at all: `card ls` needed a list, and nothing else listed cards. The
recovery was always the same three-call detour — `list ls`, then `card ls` per
column, then reassemble.

**Fixed:** `card ls` with no list lists every card on the board with a `List`
column. `list ls` + N× `card ls` collapses to one call.

**Guard rail:** a board-wide read is the one listing whose size the caller
doesn't choose, so it caps at 50 rows and the footer says what it left out and
where the rest are:

```
Showing 50 of 212 cards (To Do 88 · Doing 12 · Done 112).
Narrow with: trello card ls "<list>"   ·   all of them: --limit 0
```

The cap turns truncation into navigation instead of a silent lie. In `--json`
the array stays valid and the notice goes to stderr, so `| jq` is unaffected.
The per-list view keeps no default cap — naming a column *is* choosing the size —
but honours an explicit `--limit`.

## F2 — nothing could list archived cards, so an agent read the JSON off disk

`t2-unarchive` ("a card was archived by mistake, put it back") took **19 calls**
and zero tool errors. `card unarchive <card_id>` is right there in `--help`; what
was missing was any way to *learn the id*. The agent tried `activity`, tried
every list, tried `export`, tried the trello backend — then gave up on the CLI:

```
15. ls -la .../store/19f86fde.../
17. grep -l "Drop legacy endpoint" .../cards/*.json
18. cat .../cards/99b2dfdebe4c577e422c8c32.json
19. trello card unarchive 99b2dfdebe4c577e422c8c32
```

It passed the verifier by going around the tool. On any deployment where the
store isn't on the same box — the http backend, someone else's server — that
run is an outright failure instead. `t3-updates-since` hit the same wall and
silently reported nothing.

**Fixed:** `card ls --archived` (board-wide or per-list). `card unarchive`'s help
line now points at it.

## F3 — `<noun> --help` was an error

```
$ trello card --help
Unknown flag: --help
```

Agents reach for `<noun> --help` before top-level `--help`; it was hit in
several runs and taught nothing. The top-level help is ~8.5KB, so the
alternative is also expensive: every orientation call pays for all ten groups.

**Fixed:** `trello card --help` (and `card ls --help`, and `trello help card`)
prints just that group's section, with a *See also* line. The `card` section now
names `grab` and points comments/labels/checklists/attachments at their own noun
groups.

## F4 — the bare-noun fallback turned a wrong verb into a nonsense error

```
$ trello card comment "Migrate database" --board Roadmap "Blocked on design review."
List not found: comment Migrate database Blocked on design review.
```

The noun groups don't nest, so `card comment` is a reasonable guess — but the
`ls` fallback swallowed the verb and blamed a list that was never mentioned.
Same shape for `list cards` and `board list` (the plural `boards` is what lists
boards, and nothing said so).

**Fixed:** a first argument that is a verb *somewhere* in the CLI never falls
through to `ls`. It gets the valid verbs, a *Did you mean* pointing at the real
command, a `trello <group> --help` pointer, and — since a list could legitimately
be called "Archive" — the explicit `trello card ls "Archive"` escape hatch.

## F5 — the help text documented a form the parser rejected

Help says `'after <other_card_id>'`, quotes included. Quote it exactly as
documented and:

```
$ trello card pos b20d3d70 "after 8c5d782e"
Invalid position: 'after 8c5d782e'. Use top, bottom, a number, 'after <id>', ...
```

The error even repeats the syntax the caller just used. **Fixed:** both
`card pos` and `list pos` accept the relative form as one token or two.

## F6 — invented flags and positional boards got no hint

`--list`, `--all`, `--assigned-to` all produced a bare `Unknown flag: X`, and
`trello board show Roadmap` answered "No board specified" — to a caller who had
just specified the board, only positionally.

**Fixed:** a hint table names the positional form (`The list is positional:
trello card ls "To Do"`), `board show <name>` says where `--board` goes, and an
unknown top-level command gets `difflib` suggestions (`attach` → `attachment`).

## F7 — `grab` was invisible (fixed, and the case now checks the mechanism)

`t3-grab` states the race condition explicitly: *several agents are working this
board, claim the top ticket so no two get the same one*. `grab` exists for
exactly that. The agent never found it — it never ran `trello --help` at all,
went straight to guessing, and solved the task with `card ls` + `card move`:
the precise race `grab` prevents. The board looked correct afterwards, so the
verifier passed it.

Two things came out of that:

1. The case now asserts on the **mechanism** (`expect_cmd=r"\bgrab\b"`), not just
   the outcome. Scored under that rule the baseline is 34/35, and this is the
   one real failure in the corpus.
2. `card move`'s help line and the `card --help` *See also* now point at `grab`.

That was enough: in the patched run `t3-grab` found `grab` and finished in **2
calls with no errors** (baseline: 9 calls, 4 errors, wrong mechanism). It is
still an indirect fix — it only helps an agent that reads help before guessing —
so keep the mechanism assertion in place to catch it silently reverting.

---

---

## What survived — the round-2 backlog

Only three runs still hit an error, and two of them now recover in a single turn
because the error names the right command (`board list` → *Did you mean: trello
boards*; `board show <id>` → *the board is a global flag*). Those are working as
intended: a first guess that costs one turn and teaches the tool.

The third was a genuinely bad message. `comment add` takes free text, so it
can't run its arguments through `_parse_flags` — an invented flag sailed
through as a positional and came back as:

```
$ trello comment add --board Roadmap --card "Migrate database" --text "..."
Card not found with prefix: --card
```

**Fixed after the round-1 rerun** (so it is not in the comparison above, and is
covered by unit test rather than a fresh fanout): every resolver now rejects a
`--flag` where a value belongs and says values are positional here.

## What the fixes themselves broke — found by code review, not by the corpus

Three of the round-1 patches shipped a false success of their own. None of them
is reachable by any case in the corpus, which is the point worth recording: the
fanout finds what agents *do*, and a reviewer finds what the fix *now permits*.
They need each other.

- **A help word used as a value stopped being a value.** The group dispatcher
  scanned every argument for `-h`/`--help`/`help`, so `card add "To Do" help`
  printed the usage and exited **0** — a write silently doing nothing, which is
  precisely the failure an agent caller cannot detect. Same for a comment whose
  text is `help`. Now `help` is only a help request where a *verb* belongs, and
  a help *flag* is one at the end of a real verb's arguments (`card ls --help`,
  `card ls "To Do" --help` and `card help` all still work).
- **`board show <name>` was only guarded when no board was selected.** With
  `--board`/`TRELLO_BOARD` set, the positional was dropped and a *different*
  board reported, exit 0. F6's message now fires whenever a name is given.
- **`--limit -5` meant "no limit".** The validator was
  `lstrip("-").isdigit()`, so a negative passed and then read downstream as the
  unlimited case that `0` is documented for.

### F8 — an invented flag became the *data* (found by replaying the corpus)

The round-2 fix stopped a `--flag` reaching a resolver. It did nothing for the
sink no resolver ever sees: a **name, description or comment body**, which is
free text joined straight out of `args`. Replaying the corpus turned up a
command that had been sitting in a passing run the whole time:

```
$ trello label add --board Roadmap --card "Add dark mode" --label "feature"
Created label: --card Add dark mode --label feature (09564aed)   # exit 0
```

`t2-label-set` passed — the agent went on to find `label set` — so neither the
verifier nor the scoreboard ever saw it. It is the same defect as the three
above and the worst-shaped one: a wrong write, reported as a success, leaving
junk on the board.

**Fixed:** `_free_text` guards every free-text sink (label add/edit, card
rename/desc/due, list/board/checklist rename, checklist + item add, comment
add/edit, attachment name). Text meant as one value should arrive as one quoted
argument, so a bare `--token` there is a flag. A value that really does start
with dashes stays reachable after a bare `--`, which `_parse_flags` now honours
too, so the escape hatch is the same everywhere.

### The rerun that says these fixes made it worse (they didn't)

`runs/review-fixes` is the corpus against the fixed CLI, and
`runs/compare-round2.md` is ugly: 35/35 still passes, but 121 → 149 calls and
7 → 13 errors. **That is variance, and it is worth keeping as the worked example
of why a single run per case cannot be read as a regression.** The new errors are
all first guesses the model happened not to make last time — `board get`,
`board labels`, `create-board`, `card update`, `attach`, `get board … --format
json` — and every one of them recovers in one turn on the message it gets back.

The way to settle it without paying for `--repeat 3` is to replay: run all 149
commands from the new run against both CLI versions over the *same* seeded
store, and diff exit code, stdout and stderr. All 149 are byte-identical apart
from freshly minted random ids. No command in the run even reaches a line this
patch changed, so the delta cannot be caused by it.

That replay is cheaper and stronger than a repeat when the question is "did my
diff do this?" — repeats measure the model, a replay measures the tool. It is
also how F8 above was found and then verified: replaying all 482 commands from
all three runs against the CLI before and after that patch, exactly one changed
behaviour — the `label add --card …` line, which now exits 1 instead of
creating junk.

## Not fixed, deliberately

- **The 8.5KB usage dump.** Seven runs hit an error whose response was the full
  top-level help. It is expensive, but it is also what let them recover in one
  turn — and per-group help (F3) now gives them a cheaper door. Revisit when
  there's evidence the dump itself is the problem.
- **`grab` discovery.** See F7.
- **Trello-backend AX.** The corpus is hermetic against the local backend, so
  credential errors, rate limits and network failures are unmeasured. Those
  error messages are the ones a real caller hits first on a fresh machine.

## Method notes

- One run per case; models are stochastic and a single failure is not yet a
  regression. `--repeat 3` before believing any one flip.
- Provider-side 429/529s produce runs with no tool calls and no signal. The
  runner now retries them rather than scoring them.
- Cases assert on the store, not the agent's summary, and it matters: the
  baseline `t3-grab` run signed off with "the grab command atomically claimed
  this card" having never run `grab`.
- The patched run was executed in two batches — the provider was returning 529s
  and a first attempt was killed partway. The runner merges a re-run subset over
  an existing run for exactly this reason; every case in the comparison is a
  complete run against the patched CLI.
