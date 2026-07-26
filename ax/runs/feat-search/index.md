# AX run — feat-search

**36/37 passed** (97%) · 155 tool calls against a 151 budget · 26 runs hit at least one tool error · $1.012 · 11 min wall

## By tier

| tier | passed |
| --- | --- |
| 1 | 11/12 |
| 2 | 13/13 |
| 3 | 12/12 |

## By tag

| tag | passed |
| --- | --- |
| aggregate | 1/2 |
| read | 13/14 |
| ambiguity | 1/1 |
| attachment | 1/1 |
| board | 2/2 |
| board-scope | 1/1 |
| card | 7/7 |
| checklist | 2/2 |
| comment | 1/1 |
| cross-board | 1/1 |
| date | 2/2 |
| discovery | 7/7 |
| hard | 2/2 |
| json | 2/2 |
| label | 2/2 |
| list | 3/3 |
| multi | 5/5 |
| pos | 2/2 |
| resolver | 4/4 |
| search | 2/2 |
| semantic | 3/3 |
| workflow | 1/1 |
| write | 23/23 |

## Error classes hit (first error per run)

- unknown command — 6
- bare usage dump — 4
- unknown board command: list. valid verbs: show, add, rename, — 2
- id list activity name labels due -------- ----- ---------- - — 2
- missing argument — 2
- 2026-07-26 @coami updatecard drop legacy endpoint 2026-07-26 — 1
- not found — 1
- no board specified — 1
- id name ------------------------ ----- c2bddbcebe02e6f584fa2 — 1
- unknown flag — 1
- unknown card command: create. valid verbs: show, ls, add, mo — 1
- created list: blocked (9eba3b54) — 1
- board: roadmap id: c062060ed3977d8a1910cb5c url: desc: produ — 1
- the board is a global flag, not an argument: trello --board  — 1
- unknown list command: d8f39051ec43292b796f7d72. valid verbs: — 1

## What each run reached for after orientation

- `boards` — 7
- `! search` — 5
- `boards roadmap` — 2
- `! list` — 2
- `card add` — 2
- `search "Refactor` — 2
- `search "Add` — 2
- `card ls` — 2
- `! activity` — 1
- `labels` — 1
- `(none)` — 1
- `board add` — 1
- `search "Set` — 1
- `help comment` — 1
- `!` — 1
- `list rename` — 1
- `! board` — 1
- `card show` — 1
- `boards |` — 1
- `label add` — 1
- `! updates` — 1

## Results

| case | tier | verdict | calls/budget | errors | cost | why |
| --- | --- | --- | --- | --- | --- | --- |
| `t1-activity` | 1 | ✅ | 2/3 | 1 | $0.024 |  |
| `t1-boards` | 1 | ✅ | 2/2 | 0 | $0.016 |  |
| `t1-card-detail` | 1 | ✅ | 6/4 | 3 | $0.026 |  |
| `t1-cards-in-list` | 1 | ✅ | 3/3 | 0 | $0.025 |  |
| `t1-checklist-read` | 1 | ✅ | 3/4 | 2 | $0.026 |  |
| `t1-count` | 1 | ❌ | 9/4 | 3 | $0.036 | answer missing ['5'] |
| `t1-find-board` | 1 | ✅ | 1/2 | 0 | $0.015 |  |
| `t1-find-card` | 1 | ✅ | 2/3 | 2 | $0.017 |  |
| `t1-json` | 1 | ✅ | 4/3 | 1 | $0.034 |  |
| `t1-labels` | 1 | ✅ | 2/3 | 1 | $0.018 |  |
| `t1-lists` | 1 | ✅ | 2/3 | 1 | $0.022 |  |
| `t1-mine` | 1 | ✅ | 4/3 | 1 | $0.028 |  |
| `t2-add-board` | 2 | ✅ | 2/2 | 1 | $0.023 |  |
| `t2-add-card` | 2 | ✅ | 3/3 | 2 | $0.028 |  |
| `t2-add-card-desc` | 2 | ✅ | 4/3 | 2 | $0.024 |  |
| `t2-add-list` | 2 | ✅ | 3/3 | 2 | $0.028 |  |
| `t2-archive-card` | 2 | ✅ | 3/4 | 0 | $0.025 |  |
| `t2-card-top` | 2 | ✅ | 4/4 | 1 | $0.030 |  |
| `t2-comment` | 2 | ✅ | 5/4 | 2 | $0.025 |  |
| `t2-due` | 2 | ✅ | 4/4 | 3 | $0.032 |  |
| `t2-label-set` | 2 | ✅ | 4/4 | 0 | $0.028 |  |
| `t2-move-card` | 2 | ✅ | 4/4 | 3 | $0.028 |  |
| `t2-rename-card` | 2 | ✅ | 3/4 | 1 | $0.026 |  |
| `t2-rename-list` | 2 | ✅ | 2/4 | 0 | $0.023 |  |
| `t2-unarchive` | 2 | ✅ | 3/5 | 3 | $0.025 |  |
| `t3-archive-list` | 3 | ✅ | 5/4 | 0 | $0.030 |  |
| `t3-attachment` | 3 | ✅ | 3/5 | 3 | $0.026 |  |
| `t3-board-lifecycle` | 3 | ✅ | 6/6 | 1 | $0.031 |  |
| `t3-bulk-move` | 3 | ✅ | 7/5 | 4 | $0.029 |  |
| `t3-checklist` | 3 | ✅ | 11/7 | 5 | $0.041 |  |
| `t3-grab` | 3 | ✅ | 9/3 | 2 | $0.037 |  |
| `t3-move-across-boards` | 3 | ✅ | 5/6 | 0 | $0.031 |  |
| `t3-new-label-apply` | 3 | ✅ | 6/6 | 0 | $0.033 |  |
| `t3-reorder-relative` | 3 | ✅ | 4/6 | 0 | $0.029 |  |
| `t3-report` | 3 | ✅ | 3/6 | 0 | $0.030 |  |
| `t3-triage` | 3 | ✅ | 10/8 | 2 | $0.039 |  |
| `t3-updates-since` | 3 | ✅ | 2/4 | 2 | $0.025 |  |

## Failing transcripts (1)

## t1-count — FAIL

`haiku` · calls 9/4 · errors 3 · $0.036 · 22s

**task** Use the `trello` command. How many cards are in each column of the Roadmap board? Give me the counts.

**why it failed** answer missing ['5']

**trace**

1. ✗ `trello board list`
     → Unknown board command: list. Valid verbs: show, add, rename, archive, restore Did you mean: trello boards Full help: trello board --help
2. · `trello boards`
     → ID Name URL -------- ------- --- 0b6656ec Roadmap a53b2724 Scratch
3. ✗ `trello board show 0b6656ec`
     → The board is a global flag, not an argument: trello --board "0b6656ec" board show
4. · `trello --board 0b6656ec board show`
     → Board: Roadmap ID: 0b6656ecbc2ed8aa83a7d772 URL: Desc: Product roadmap
5. ✗ `trello --board 0b6656ec lists`
     → Unknown command: lists Did you mean: list Usage: trello [--board <name_or_id>] [--backend <trello|local|http>] [--json] <command> [args] Global options: --board <name_or_id> Board for this command (required; no active board) (also: TRELLO_BOARD env var) --backend <trello|local|http> Data source for …[+6703 chars]
6. · `trello --board 0b6656ec list ls`
     → ID Name ------------------------ ----- 0ed136fd726cb5a706630476 To Do c1d8715e4ef80d751c012eaa Doing b6767caa7f33b97633cf8e72 Done
7. · `trello --board 0b6656ec card ls "To Do" --json | grep -c '"id"'`
     → 10
8. · `trello --board 0b6656ec card ls "Doing" --json | grep -c '"id"'`
     → 1
9. · `trello --board 0b6656ec card ls "Done" --json | grep -c '"id"'`
     → 1

**answer** Here are the card counts for each column on the Roadmap board: | Column | Count | |--------|-------| | To Do | 10 | | Doing | 1 | | Done | 1 |


