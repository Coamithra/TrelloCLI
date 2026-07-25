# AX run — baseline-haiku

**35/35 passed** (100%) · 212 tool calls against a 146 budget · 16 runs hit at least one tool error · $0.834 · 14 min wall

## By tier

| tier | passed |
| --- | --- |
| 1 | 10/10 |
| 2 | 13/13 |
| 3 | 12/12 |

## By tag

| tag | passed |
| --- | --- |
| aggregate | 2/2 |
| ambiguity | 1/1 |
| attachment | 1/1 |
| board | 2/2 |
| board-scope | 1/1 |
| card | 7/7 |
| checklist | 2/2 |
| comment | 1/1 |
| cross-board | 1/1 |
| date | 2/2 |
| discovery | 5/5 |
| hard | 2/2 |
| json | 2/2 |
| label | 2/2 |
| list | 3/3 |
| multi | 5/5 |
| pos | 2/2 |
| read | 12/12 |
| resolver | 4/4 |
| semantic | 3/3 |
| workflow | 1/1 |
| write | 23/23 |

## Error classes hit (first error per run)

- bare usage dump — 7
- unknown command — 2
- unknown label command: list. valid verbs: ls, add, edit, del — 1
- unknown list command: cards. valid verbs: ls, add, archive,  — 1
- unknown list command: --assigned-to. valid verbs: ls, add, a — 1
- not found — 1
- no board specified — 1
- unknown board command: list. valid verbs: show, add, rename, — 1
- invalid position: 'after 8c5d782e'. use top, bottom, a numbe — 1

## What each run reached for after orientation

- `boards` — 15
- `card ls` — 7
- `list ls` — 4
- `label ls` — 1
- `activity 5` — 1
- `(none)` — 1
- `card add` — 1
- `card comment` — 1
- `git log` — 1
- `board add` — 1
- `boards |` — 1
- `board show` — 1

> ⚠ ran outside the public surface (read the source): ['t2-unarchive']

## Results

| case | tier | verdict | calls/budget | errors | cost | why |
| --- | --- | --- | --- | --- | --- | --- |
| `t1-activity` | 1 | ✅ | 2/3 | 0 | $0.015 |  |
| `t1-boards` | 1 | ✅ | 2/2 | 0 | $0.013 |  |
| `t1-card-detail` | 1 | ✅ | 6/4 | 1 | $0.022 |  |
| `t1-cards-in-list` | 1 | ✅ | 2/3 | 0 | $0.014 |  |
| `t1-checklist-read` | 1 | ✅ | 6/4 | 0 | $0.024 |  |
| `t1-count` | 1 | ✅ | 6/4 | 0 | $0.024 |  |
| `t1-json` | 1 | ✅ | 6/3 | 3 | $0.026 |  |
| `t1-labels` | 1 | ✅ | 2/3 | 1 | $0.009 |  |
| `t1-lists` | 1 | ✅ | 2/3 | 0 | $0.014 |  |
| `t1-mine` | 1 | ✅ | 3/3 | 1 | $0.017 |  |
| `t2-add-board` | 2 | ✅ | 2/2 | 1 | $0.013 |  |
| `t2-add-card` | 2 | ✅ | 2/3 | 0 | $0.014 |  |
| `t2-add-card-desc` | 2 | ✅ | 5/3 | 0 | $0.021 |  |
| `t2-add-list` | 2 | ✅ | 8/3 | 0 | $0.026 |  |
| `t2-archive-card` | 2 | ✅ | 5/4 | 1 | $0.021 |  |
| `t2-card-top` | 2 | ✅ | 5/4 | 0 | $0.021 |  |
| `t2-comment` | 2 | ✅ | 9/4 | 3 | $0.028 |  |
| `t2-due` | 2 | ✅ | 5/4 | 1 | $0.022 |  |
| `t2-label-set` | 2 | ✅ | 5/4 | 0 | $0.022 |  |
| `t2-move-card` | 2 | ✅ | 5/4 | 1 | $0.021 |  |
| `t2-rename-card` | 2 | ✅ | 6/4 | 1 | $0.024 |  |
| `t2-rename-list` | 2 | ✅ | 5/4 | 0 | $0.021 |  |
| `t2-unarchive` | 2 | ✅ | 19/5 | 0 | $0.065 |  |
| `t3-archive-list` | 3 | ✅ | 5/4 | 0 | $0.020 |  |
| `t3-attachment` | 3 | ✅ | 8/5 | 1 | $0.032 |  |
| `t3-board-lifecycle` | 3 | ✅ | 6/6 | 0 | $0.022 |  |
| `t3-bulk-move` | 3 | ✅ | 7/5 | 0 | $0.024 |  |
| `t3-checklist` | 3 | ✅ | 10/7 | 1 | $0.032 |  |
| `t3-grab` | 3 | ✅ | 9/3 | 4 | $0.024 |  |
| `t3-move-across-boards` | 3 | ✅ | 9/6 | 1 | $0.032 |  |
| `t3-new-label-apply` | 3 | ✅ | 10/6 | 1 | $0.032 |  |
| `t3-reorder-relative` | 3 | ✅ | 7/6 | 1 | $0.028 |  |
| `t3-report` | 3 | ✅ | 6/6 | 0 | $0.026 |  |
| `t3-triage` | 3 | ✅ | 8/8 | 0 | $0.032 |  |
| `t3-updates-since` | 3 | ✅ | 9/4 | 0 | $0.034 |  |

## Failing transcripts (0)

