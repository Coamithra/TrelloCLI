# AX run — review-fixes

**35/35 passed** (100%) · 149 tool calls against a 146 budget · 8 runs hit at least one tool error · $0.689 · 12 min wall

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

- unknown command — 3
- unknown board command: list. valid verbs: show, add, rename, — 2
- unknown board command: get. valid verbs: show, add, rename,  — 1
- unknown board command: labels. valid verbs: show, add, renam — 1
- unknown card command: update. valid verbs: show, ls, add, mo — 1

## What each run reached for after orientation

- `boards` — 12
- `card ls` — 10
- `card add` — 2
- `(none)` — 2
- `activity 5` — 1
- `board show` — 1
- `board` — 1
- `card mine` — 1
- `board add` — 1
- `list add` — 1
- `card due` — 1
- `grab "To` — 1
- `label add` — 1

## Results

| case | tier | verdict | calls/budget | errors | cost | why |
| --- | --- | --- | --- | --- | --- | --- |
| `t1-activity` | 1 | ✅ | 2/3 | 0 | $0.015 |  |
| `t1-boards` | 1 | ✅ | 2/2 | 0 | $0.014 |  |
| `t1-card-detail` | 1 | ✅ | 3/4 | 0 | $0.016 |  |
| `t1-cards-in-list` | 1 | ✅ | 3/3 | 0 | $0.016 |  |
| `t1-checklist-read` | 1 | ✅ | 7/4 | 3 | $0.025 |  |
| `t1-count` | 1 | ✅ | 7/4 | 2 | $0.018 |  |
| `t1-json` | 1 | ✅ | 2/3 | 1 | $0.018 |  |
| `t1-labels` | 1 | ✅ | 4/3 | 1 | $0.018 |  |
| `t1-lists` | 1 | ✅ | 6/3 | 3 | $0.022 |  |
| `t1-mine` | 1 | ✅ | 2/3 | 0 | $0.015 |  |
| `t2-add-board` | 2 | ✅ | 2/2 | 1 | $0.015 |  |
| `t2-add-card` | 2 | ✅ | 2/3 | 0 | $0.014 |  |
| `t2-add-card-desc` | 2 | ✅ | 3/3 | 0 | $0.018 |  |
| `t2-add-list` | 2 | ✅ | 2/3 | 0 | $0.015 |  |
| `t2-archive-card` | 2 | ✅ | 3/4 | 0 | $0.018 |  |
| `t2-card-top` | 2 | ✅ | 4/4 | 0 | $0.020 |  |
| `t2-comment` | 2 | ✅ | 3/4 | 0 | $0.019 |  |
| `t2-due` | 2 | ✅ | 4/4 | 1 | $0.016 |  |
| `t2-label-set` | 2 | ✅ | 5/4 | 0 | $0.023 |  |
| `t2-move-card` | 2 | ✅ | 6/4 | 0 | $0.024 |  |
| `t2-rename-card` | 2 | ✅ | 4/4 | 0 | $0.020 |  |
| `t2-rename-list` | 2 | ✅ | 5/4 | 0 | $0.021 |  |
| `t2-unarchive` | 2 | ✅ | 4/5 | 0 | $0.018 |  |
| `t3-archive-list` | 3 | ✅ | 5/4 | 0 | $0.020 |  |
| `t3-attachment` | 3 | ✅ | 3/5 | 1 | $0.019 |  |
| `t3-board-lifecycle` | 3 | ✅ | 7/6 | 0 | $0.026 |  |
| `t3-bulk-move` | 3 | ✅ | 7/5 | 0 | $0.025 |  |
| `t3-checklist` | 3 | ✅ | 7/7 | 0 | $0.028 |  |
| `t3-grab` | 3 | ✅ | 2/3 | 0 | $0.016 |  |
| `t3-move-across-boards` | 3 | ✅ | 7/6 | 0 | $0.028 |  |
| `t3-new-label-apply` | 3 | ✅ | 5/6 | 0 | $0.023 |  |
| `t3-reorder-relative` | 3 | ✅ | 5/6 | 0 | $0.021 |  |
| `t3-report` | 3 | ✅ | 2/6 | 0 | $0.014 |  |
| `t3-triage` | 3 | ✅ | 7/8 | 0 | $0.022 |  |
| `t3-updates-since` | 3 | ✅ | 7/4 | 0 | $0.027 |  |

## Failing transcripts (0)

