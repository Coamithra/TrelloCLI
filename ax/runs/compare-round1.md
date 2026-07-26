# AX comparison — baseline-haiku → patched-haiku

35 shared runs.

| metric | before | after | delta |
| --- | --- | --- | --- |
| passed | 34 | 35 | +1 ✅ |
| tool calls | 212 | 121 | -91 ✅ |
| tool errors | 23 | 7 | -16 ✅ |
| runs with an error | 16 | 3 | -13 ✅ |
| runs over budget | 24 | 3 | -21 ✅ |
| calls over budget | 71 | 5 | -66 ✅ |
| went around the CLI | 1 | 0 | -1 ✅ |

## Per case

| case | before | after | calls | errors |
| --- | --- | --- | --- | --- |
| `t1-activity` | ✅ | ✅ | 2 → 1 | 0 → 0 |
| `t1-boards` | ✅ | ✅ | 2 → 2 | 0 → 0 |
| `t1-card-detail` | ✅ | ✅ | 6 → 3 | 1 → 0 |
| `t1-cards-in-list` | ✅ | ✅ | 2 → 2 | 0 → 0 |
| `t1-checklist-read` | ✅ | ✅ | 6 → 6 | 0 → 3 |
| `t1-count` | ✅ | ✅ | 6 → 4 | 0 → 0 |
| `t1-json` | ✅ | ✅ | 6 → 1 | 3 → 0 |
| `t1-labels` | ✅ | ✅ | 2 → 1 | 1 → 0 |
| `t1-lists` | ✅ | ✅ | 2 → 2 | 0 → 0 |
| `t1-mine` | ✅ | ✅ | 3 → 2 | 1 → 0 |
| `t2-add-board` | ✅ | ✅ | 2 → 1 | 1 → 0 |
| `t2-add-card` | ✅ | ✅ | 2 → 2 | 0 → 0 |
| `t2-add-card-desc` | ✅ | ✅ | 5 → 2 | 0 → 0 |
| `t2-add-list` | ✅ | ✅ | 8 → 2 | 0 → 0 |
| `t2-archive-card` | ✅ | ✅ | 5 → 3 | 1 → 0 |
| `t2-card-top` | ✅ | ✅ | 5 → 5 | 0 → 0 |
| `t2-comment` | ✅ | ✅ | 9 → 4 | 3 → 1 |
| `t2-due` | ✅ | ✅ | 5 → 3 | 1 → 0 |
| `t2-label-set` | ✅ | ✅ | 5 → 4 | 0 → 0 |
| `t2-move-card` | ✅ | ✅ | 5 → 4 | 1 → 0 |
| `t2-rename-card` | ✅ | ✅ | 6 → 3 | 1 → 0 |
| `t2-rename-list` | ✅ | ✅ | 5 → 2 | 0 → 0 |
| `t2-unarchive` | ✅ | ✅ | 19 → 4 | 0 → 0 |
| `t3-archive-list` | ✅ | ✅ | 5 → 4 | 0 → 0 |
| `t3-attachment` | ✅ | ✅ | 8 → 1 | 1 → 0 |
| `t3-board-lifecycle` | ✅ | ✅ | 6 → 8 | 0 → 0 |
| `t3-bulk-move` | ✅ | ✅ | 7 → 4 | 0 → 0 |
| `t3-checklist` | ✅ | ✅ | 10 → 7 | 1 → 0 |
| `t3-grab` | ❌ | ✅ 🟢 fixed | 9 → 2 | 4 → 0 |
| `t3-move-across-boards` | ✅ | ✅ | 9 → 6 | 1 → 0 |
| `t3-new-label-apply` | ✅ | ✅ | 10 → 6 | 1 → 0 |
| `t3-reorder-relative` | ✅ | ✅ | 7 → 4 | 1 → 0 |
| `t3-report` | ✅ | ✅ | 6 → 6 | 0 → 3 |
| `t3-triage` | ✅ | ✅ | 8 → 7 | 0 → 0 |
| `t3-updates-since` | ✅ | ✅ | 9 → 3 | 0 → 0 |

**Runs that no longer hit any error:** ['t1-card-detail', 't1-labels', 't1-json', 't1-mine', 't2-move-card', 't2-rename-card', 't2-due', 't2-archive-card', 't2-add-board', 't3-grab', 't3-new-label-apply', 't3-checklist', 't3-attachment', 't3-reorder-relative', 't3-move-across-boards']

**Runs that now hit one:** ['t1-checklist-read', 't3-report']
