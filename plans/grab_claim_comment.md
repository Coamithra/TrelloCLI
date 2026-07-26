# grab (remote Trello): make the winner's lingering claim comment recognizable

Card `a786472fc4467527769b91ce`. Branch `fix/grab-claim-comment`, worktree `.trees/wt2`.

## Context

`TrelloBackend.grab_top_card` posts `I am doing this now — claim <hex8>` on the card it is
trying to claim, waits 10-30s, then adjudicates. The **loss** path deletes its comment
(`trello.py:329`); the **win** path returns the card and drops `mine` on the floor
(`trello.py:318-324`). So every successful remote grab leaves a permanent claim comment on
the card.

A later agent running `card show <id>` sees an opaque hex id it cannot tie to itself and
concludes someone else holds the card. Reported live 2026-07-26.

The winner cannot simply delete the comment: a competitor still inside its own randomized
10-30s sleep re-reads comments in `_won_claim`; with our claim gone it sees no earlier claim,
believes it won, and both agents own the card. A safe deletion has to outlive any competitor
that could still adjudicate against us — ~90s of blocking on every successful grab.

**User's chosen shape** (latest card comment, supersedes the post-window-delete option):
`grab` returns the claim id to its caller and prints it, so a caller that later reads the
card recognizes the claim as its own. Paired with option (a): make the comment itself
self-explanatory to a cold reader. Zero added wall clock, no double-grab window — the
comment stays put, so `_won_claim` keeps adjudicating correctly for anyone mid-window.

Local backend is unaffected (grabs under the store lock, posts no comment).

## Design

### 1. `trello_cli/backends/trello.py` — surface the claim id

- New `_claim_text(claim_id) -> str` builds the comment body in one place:

  ```
  I am doing this now — claim <id>

  (Automated bookkeeping posted by `trello grab`; the grab prints this id back to whoever
  ran it, so if your grab output says `Claim: <id>`, this claim is yours. The real claim is
  the card sitting in the in-progress list. This comment only settles ties between
  simultaneous grabbers, and one older than 60s settles nothing at all.)
  ```

  The window figure is interpolated from `_GRAB_CLAIM_WINDOW` so it can't drift.
  **The prefix stays byte-identical up to the id** — `_parse_claim` matches the marker then
  takes the FIRST whitespace token, so trailing text parses fine and rivals running the old
  or new build interoperate in both directions.

- Win path returns `{**card, "claimId": claim_id}` (a copy, mirroring `local.py`'s
  `{**out, "rebalanced": True}`), on both the `get_card` branch and the fallback.

### 2. Transient-key contract

`claimId` is **absent** on the local backend — there is no claim there, and an honest
absence beats a uniform `None` that reads like "a claim exists but is unknown". This is the
existing transient-key convention already documented at the top of `base.py` (`rebalanced`);
extend that docstring and `grab_top_card`'s docstring to name `claimId`. The http backend
forwards whatever the server's backend produced — JSON round-trips the extra key, no change.

### 3. `trello_cli/main.py` — print it

`cmd_grab` gains, only when the key is present:

```
  Claim: 3f9a1c2d (this card's claim comment is yours)
```

`--json` already prints the whole dict, so the key rides along for free. Help text for
`grab` gets one clause about the claim line.

### 4. Docs

- `CLAUDE.md`: trello.py bullet (winner returns `claimId`, comment is self-describing),
  `cmd_grab` bullet (the `Claim:` line).
- `~/.claude/CONTRIBUTING.md`: one sentence under the atomic-`grab` section — a claim comment
  on a card is `grab` bookkeeping, not a human's hold; the id printed by your own grab is how
  you tell whether it is yours. (The marker string itself is **no longer** in CONTRIBUTING —
  hand-run claims were dropped in favour of `grab` — so there is no literal to keep in sync.)

## Tests (`tests/test_trello_backend.py`)

- `_parse_claim` extracts the id from the new multi-line body (locks the byte-identical
  prefix + trailing-text constraint).
- `_won_claim` still ranks a rival that posted the verbose body, and still ignores the
  marker quoted mid-prose.
- `grab_top_card` win path returns `claimId` equal to the id actually posted in the comment
  (fake `get_cards_in_list`/`move_card`/`add_comment`/`get_comments`/`get_card`, wait range
  monkeypatched to 0).
- Loss-then-win across two cards still returns the right card, with `claimId` present.
- `LocalBackend.grab_top_card` result has **no** `claimId` (`tests/test_local_store.py`).
- `cmd_grab` prints the `Claim:` line when the backend supplies one and omits it otherwise.

## Verification

- `python -m pytest` green (worktree venv).
- Functional: local-backend `grab` on a scratch board, formatted + `--json`, confirming no
  `Claim:` line and no `claimId` key. The remote path cannot be exercised (no creds; the
  handshake has never been verified against live Trello — pre-existing, stated in CLAUDE.md),
  so its proof is unit-level.
- **No AX corpus run.** User's call this session: the corpus rerun is an occasional
  ergonomics sweep, not a per-card gate — one extra output line does not warrant fanning the
  whole corpus across billed cold `claude -p` runs. CLAUDE.md's verification-gate bullet is
  amended to say so, and `tests/test_ax_affordances.py` remains the per-change guard.

## Out of scope

- Post-window retraction / editing the comment after the fact (option (b) and its cheaper
  edit variant) — superseded by the user's choice.
- Verifying the handshake against live Trello.
- Making the claim id attributable to a machine/user (option (c)).
