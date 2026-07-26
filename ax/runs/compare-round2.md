# AX comparison — patched-haiku → review-fixes

35 shared runs.

| metric | before | after | delta |
| --- | --- | --- | --- |
| passed | 35 | 35 | +0  |
| tool calls | 121 | 149 | +28 ⚠️ |
| tool errors | 7 | 13 | +6 ⚠️ |
| runs with an error | 3 | 8 | +5 ⚠️ |
| runs over budget | 3 | 12 | +9 ⚠️ |
| calls over budget | 5 | 22 | +17 ⚠️ |
| went around the CLI | 0 | 0 | +0  |

## Per case

| case | before | after | calls | errors |
| --- | --- | --- | --- | --- |
| `t1-activity` | ✅ | ✅ | 1 → 2 | 0 → 0 |
| `t1-boards` | ✅ | ✅ | 2 → 2 | 0 → 0 |
| `t1-card-detail` | ✅ | ✅ | 3 → 3 | 0 → 0 |
| `t1-cards-in-list` | ✅ | ✅ | 2 → 3 | 0 → 0 |
| `t1-checklist-read` | ✅ | ✅ | 6 → 7 | 3 → 3 |
| `t1-count` | ✅ | ✅ | 4 → 7 | 0 → 2 |
| `t1-json` | ✅ | ✅ | 1 → 2 | 0 → 1 |
| `t1-labels` | ✅ | ✅ | 1 → 4 | 0 → 1 |
| `t1-lists` | ✅ | ✅ | 2 → 6 | 0 → 3 |
| `t1-mine` | ✅ | ✅ | 2 → 2 | 0 → 0 |
| `t2-add-board` | ✅ | ✅ | 1 → 2 | 0 → 1 |
| `t2-add-card` | ✅ | ✅ | 2 → 2 | 0 → 0 |
| `t2-add-card-desc` | ✅ | ✅ | 2 → 3 | 0 → 0 |
| `t2-add-list` | ✅ | ✅ | 2 → 2 | 0 → 0 |
| `t2-archive-card` | ✅ | ✅ | 3 → 3 | 0 → 0 |
| `t2-card-top` | ✅ | ✅ | 5 → 4 | 0 → 0 |
| `t2-comment` | ✅ | ✅ | 4 → 3 | 1 → 0 |
| `t2-due` | ✅ | ✅ | 3 → 4 | 0 → 1 |
| `t2-label-set` | ✅ | ✅ | 4 → 5 | 0 → 0 |
| `t2-move-card` | ✅ | ✅ | 4 → 6 | 0 → 0 |
| `t2-rename-card` | ✅ | ✅ | 3 → 4 | 0 → 0 |
| `t2-rename-list` | ✅ | ✅ | 2 → 5 | 0 → 0 |
| `t2-unarchive` | ✅ | ✅ | 4 → 4 | 0 → 0 |
| `t3-archive-list` | ✅ | ✅ | 4 → 5 | 0 → 0 |
| `t3-attachment` | ✅ | ✅ | 1 → 3 | 0 → 1 |
| `t3-board-lifecycle` | ✅ | ✅ | 8 → 7 | 0 → 0 |
| `t3-bulk-move` | ✅ | ✅ | 4 → 7 | 0 → 0 |
| `t3-checklist` | ✅ | ✅ | 7 → 7 | 0 → 0 |
| `t3-grab` | ✅ | ✅ | 2 → 2 | 0 → 0 |
| `t3-move-across-boards` | ✅ | ✅ | 6 → 7 | 0 → 0 |
| `t3-new-label-apply` | ✅ | ✅ | 6 → 5 | 0 → 0 |
| `t3-reorder-relative` | ✅ | ✅ | 4 → 5 | 0 → 0 |
| `t3-report` | ✅ | ✅ | 6 → 2 | 3 → 0 |
| `t3-triage` | ✅ | ✅ | 7 → 7 | 0 → 0 |
| `t3-updates-since` | ✅ | ✅ | 3 → 7 | 0 → 0 |

**Runs that no longer hit any error:** ['t2-comment', 't3-report']

**Runs that now hit one:** ['t1-lists', 't1-labels', 't1-json', 't1-count', 't2-due', 't2-add-board', 't3-attachment']
