# local init: stop hijacking the machine (card 81c0e2e6)

## Context

`trello local init <path>` persists `local_root` into `~/.trello-cli.json`. That is the
one persisted piece of local-backend *selection* in a CLI whose stated design guideline is
statelessness — and it is the command an agent naturally reaches for when it wants a
throwaway store. Hit for real twice on 2026-07-26: a subagent ran `local init <scratch>`,
and every other `--backend local` invocation on the machine — including concurrent sessions
— started answering `Board not found: 6a353ffc`. Nothing was lost; the CLI just looked like
it had eaten the board, and the error gave no hint that the root had moved.

## Design

Ship **(a) + (d)**.

### (a) `local init` no longer persists by default

`trello_cli/main.py :: _local_init`

- `_parse_flags(args, bool_flags=("--set-default",))`.
- Default (no flag): create the folder, persist **nothing**, print how to use it
  per-invocation:
  ```
  Local store ready: <path>
  Nothing was persisted — this changed no global setting.
  Use it per-command:
    trello --backend local --local-root <path> <command>
    TRELLO_LOCAL_ROOT=<path> trello --backend local <command>
  Make it this machine's default (affects EVERY --backend local invocation on this
  machine, including sessions already running):
    trello local init <path> --set-default
  ```
- `--set-default`: persist as today, but loudly and reversibly (the useful half of the
  card's option (b)) — print the previous value and how to put it back:
  ```
  Default local root: <old>  ->  <new>   (persisted in ~/.trello-cli.json)
  This affects EVERY `--backend local` invocation on this machine, including sessions
  already running. Undo with: trello local init <old> --set-default
  ```
  When old == new, say "unchanged" and skip the undo line.
- Bare `local init` (no path) keeps resolving to `config.get_local_root()` as today.

**Onboarding cost, honestly.** Setup stays one command — it just grows a flag:
`trello local init --set-default`. README's local-backend walkthrough and the USAGE line
change to show that form, so anyone following the docs is unaffected; the behaviour change
only bites the improvised-scratch-store path, which is the bug. The named alternative
(**a′**: persist silently only when the config has *no* `local_root` yet, refuse to
silently change an existing one) has zero onboarding cost but makes the command's
behaviour depend on invisible state, and still hijacks a machine that was running on the
default root. Recommending plain (a) for predictability — overseer can overrule.

### (d) self-diagnosing "Board not found"

`trello_cli/config.py` — new `local_root_source() -> str` returning the provenance of
`get_local_root()`'s answer (`--local-root` flag / `TRELLO_LOCAL_ROOT` / `config
<CONFIG_PATH>` / `default`), mirroring the resolution order in one place so the two can't
drift.

`trello_cli/main.py :: _resolve_board_ref` — when the miss happens on the **local**
backend, replace the bare `Board not found: <ref>` with:

```
Board not found: 6a353ffc
Searched local store: C:\...\scratch\store   (local_root from config C:\Users\...\.trello-cli.json)
That store holds 1 board(s): rev
Wrong store? Override per-command with --local-root <path> or TRELLO_LOCAL_ROOT.
```

(board list is already in hand from `api.get_boards`; truncate the names.) Non-local
backends keep today's one-liner. `_resolve_local_board`'s "Board not found in local store"
gets the same root + provenance tail.

`trello_cli/backends/local.py :: _load_board` — append the store root it searched
(`self.store.root`); no provenance there, since a `LocalBackend` can be constructed with an
explicit root (export, `local gc`). Note: this string also reaches an http client, i.e. the
server's filesystem path becomes visible to a token-holding remote client — judged
acceptable (single-user, token-gated), flagging it rather than hiding it.

### `trello local root` (approved — include)

A read-only verb printing the effective root, its provenance, and the persisted config
value — the one thing an already-hijacked agent has no way to discover today. The (d)
error text points at it. Reuses `local_root_source()`; gets a USAGE line so `local --help`
advertises it, and an AX-affordance lock on the error wording + the pointer.

### Docs

- `USAGE` in `main.py`: the `local init` block gains `[--set-default]` and a one-line "does
  not change any global default unless --set-default" (this *is* `local --help`, via
  `_usage_section`).
- `README.md`: the two `local init` mentions (~line 191, ~line 323) switch to
  `local init <path> --set-default` for onboarding and note the per-command form for
  scratch stores.
- `CLAUDE.md` — Conventions/Statelessness: one line that `local_root` persistence is now
  opt-in via `local init --set-default`.

## Verification

- `python -m pytest` (worktree venv) green. New tests:
  - `local init <tmp>` creates the dir and leaves `config.CONFIG_PATH` **absent/unchanged**
    (the card's explicit ask).
  - `local init <tmp> --set-default` writes `local_root`, and its output names the old
    value.
  - init output contains both `--local-root` and `TRELLO_LOCAL_ROOT` (AX).
  - `local_root_source()` returns the right label for flag / env / config / default.
  - `Board not found` on the local backend contains the searched root and the provenance
    label; on a non-local backend it does not.
  - `tests/test_ax_affordances.py`: lock the `local --help` text mentioning `--set-default`
    and the self-diagnosing error, per the file's purpose.
- Functional, via `.trees/wt4/.venv/Scripts/python.exe -m trello_cli` **only** (never the
  global `trello`, never a real `local init`): drive a scratch store created by hand under
  the scratchpad with `--local-root`, and check the not-found error in both formatted and
  `--json` modes. `local init` itself is exercised **only** under pytest with
  `config.CONFIG_PATH` monkeypatched (conftest is already hermetic) — the whole point of
  this card is that running it for real retargets the machine.
- `/review` after commit; fix every finding.

## Out of scope

- Options (b)/(c) as standalone landings — the loud/reversible output and the doc fixes are
  folded into (a) rather than shipped instead of it.
- Any change to `--board`/`--backend` selection, or to how `server_url`/credentials persist.
- Migrating or validating an existing `local_root` (e.g. warning that the configured root
  does not exist) — separate concern; note as a follow-up card if the overseer wants it.
- The AX corpus run (`ax/runner`) — per CLAUDE.md this is a periodic sweep, not a per-card
  gate; `tests/test_ax_affordances.py` is the guard here.
