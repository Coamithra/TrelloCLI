# AX findings — round 1 (Haiku 4.5, 35 cases)

The first pass of `fanout → failure modes → backlog → patch → rerun`. Baseline
run: `runs/baseline-haiku`. Patched run: `runs/patched-haiku`. Comparison:
`runs/compare-round1.md`.

## The headline is not the pass rate

The baseline scored **35/35**. That number is nearly worthless on its own — a
capable model will brute-force its way to the right board state eventually. What
it cost to get there is the actual measurement:

| | baseline |
| --- | --- |
| tool calls | **212** against a 146-call budget (+45%) |
| runs that hit at least one tool error | **16 / 35** |
| runs that gave up on the CLI and read the store files directly | **1** |
| runs that reached the intended command | **34 / 35** (`grab` was never found) |

Every one of those errors is a turn a real caller paid for, on a task the tool
supports. That is the tax AX testing measures and unit tests cannot see.

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

## F7 — `grab` is invisible, and that one is not fixed

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
2. `card move` and the `card --help` *See also* now point at `grab`. That is a
   partial fix at best: it only helps an agent that already opened the card
   help. An agent that guesses its way to `card move` on the first try still
   never learns `grab` exists. Left open.

---

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
- Cases assert on the store, not the agent's summary — several runs described
  work they had not done, and the store caught it.
