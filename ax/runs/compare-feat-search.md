# AX comparison — review-fixes → feat-search

35 shared runs.

| metric | before | after | delta |
| --- | --- | --- | --- |
| passed | 35 | 34 | -1 ⚠️ |
| tool calls | 149 | 152 | +3 ⚠️ |
| tool errors | 13 | 52 | +39 ⚠️ |
| runs with an error | 8 | 25 | +17 ⚠️ |
| runs over budget | 12 | 11 | -1 ✅ |
| calls over budget | 22 | 26 | +4 ⚠️ |
| went around the CLI | 0 | 0 | +0  |

## Per case

| case | before | after | calls | errors |
| --- | --- | --- | --- | --- |
| `t1-activity` | ✅ | ✅ | 2 → 2 | 0 → 1 |
| `t1-boards` | ✅ | ✅ | 2 → 2 | 0 → 0 |
| `t1-card-detail` | ✅ | ✅ | 3 → 6 | 0 → 3 |
| `t1-cards-in-list` | ✅ | ✅ | 3 → 3 | 0 → 0 |
| `t1-checklist-read` | ✅ | ✅ | 7 → 3 | 3 → 2 |
| `t1-count` | ✅ | ❌ 🔴 regressed | 7 → 9 | 2 → 3 |
| `t1-json` | ✅ | ✅ | 2 → 4 | 1 → 1 |
| `t1-labels` | ✅ | ✅ | 4 → 2 | 1 → 1 |
| `t1-lists` | ✅ | ✅ | 6 → 2 | 3 → 1 |
| `t1-mine` | ✅ | ✅ | 2 → 4 | 0 → 1 |
| `t2-add-board` | ✅ | ✅ | 2 → 2 | 1 → 1 |
| `t2-add-card` | ✅ | ✅ | 2 → 3 | 0 → 2 |
| `t2-add-card-desc` | ✅ | ✅ | 3 → 4 | 0 → 2 |
| `t2-add-list` | ✅ | ✅ | 2 → 3 | 0 → 2 |
| `t2-archive-card` | ✅ | ✅ | 3 → 3 | 0 → 0 |
| `t2-card-top` | ✅ | ✅ | 4 → 4 | 0 → 1 |
| `t2-comment` | ✅ | ✅ | 3 → 5 | 0 → 2 |
| `t2-due` | ✅ | ✅ | 4 → 4 | 1 → 3 |
| `t2-label-set` | ✅ | ✅ | 5 → 4 | 0 → 0 |
| `t2-move-card` | ✅ | ✅ | 6 → 4 | 0 → 3 |
| `t2-rename-card` | ✅ | ✅ | 4 → 3 | 0 → 1 |
| `t2-rename-list` | ✅ | ✅ | 5 → 2 | 0 → 0 |
| `t2-unarchive` | ✅ | ✅ | 4 → 3 | 0 → 3 |
| `t3-archive-list` | ✅ | ✅ | 5 → 5 | 0 → 0 |
| `t3-attachment` | ✅ | ✅ | 3 → 3 | 1 → 3 |
| `t3-board-lifecycle` | ✅ | ✅ | 7 → 6 | 0 → 1 |
| `t3-bulk-move` | ✅ | ✅ | 7 → 7 | 0 → 4 |
| `t3-checklist` | ✅ | ✅ | 7 → 11 | 0 → 5 |
| `t3-grab` | ✅ | ✅ | 2 → 9 | 0 → 2 |
| `t3-move-across-boards` | ✅ | ✅ | 7 → 5 | 0 → 0 |
| `t3-new-label-apply` | ✅ | ✅ | 5 → 6 | 0 → 0 |
| `t3-reorder-relative` | ✅ | ✅ | 5 → 4 | 0 → 0 |
| `t3-report` | ✅ | ✅ | 2 → 3 | 0 → 0 |
| `t3-triage` | ✅ | ✅ | 7 → 10 | 0 → 2 |
| `t3-updates-since` | ✅ | ✅ | 7 → 2 | 0 → 2 |

**Runs that no longer hit any error:** none

**Runs that now hit one:** ['t1-activity', 't1-card-detail', 't1-mine', 't2-add-card', 't2-add-card-desc', 't2-add-list', 't2-card-top', 't2-comment', 't2-move-card', 't2-rename-card', 't2-unarchive', 't3-board-lifecycle', 't3-bulk-move', 't3-checklist', 't3-grab', 't3-triage', 't3-updates-since']
