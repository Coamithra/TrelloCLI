# Contributing: Tackling a Trello Card

Step-by-step workflow for picking up and completing any card from the TrelloCLI project board (board id `6a353ffc`), now managed on the **local backend**: run every `trello` board-management command with `--backend local`. The board lives in the local file store (the configured `local_root`); it was exported with IDs preserved from the original [Trello board](https://trello.com/b/oa692YwN), which is now a snapshot source only. Lists are **To Do → Doing → Done**.

TrelloCLI is a small **Python 3.12 CLI** (package `trello_cli/`) that wraps the Trello REST API over `httpx` and prints compact, context-friendly output. There is no build step, no dev server, and no browser — the way you verify a change is by **running the actual CLI against a real Trello board** and reading the output. The roadmap (a local file backend + a web kanban) lives in [`DESIGN.md`](DESIGN.md); the To Do cards are the phases from that plan.

> **Meta-footgun: the `trello` CLI you use to manage this board *is this codebase*.** If it's installed editable (`pip install -e .`), the `trello` on your PATH runs the **root checkout's** source, not the worktree's. So your in-progress changes generally won't break your board-management commands — but if you ever `pip install -e .` from inside a worktree, the global `trello` starts running half-finished code. Keep the editable install pointed at the root checkout, and verify your changes by invoking the worktree's interpreter explicitly (`.venv/Scripts/python.exe -m trello_cli ...` or `.venv/Scripts/trello ...`) rather than the bare `trello`.

---

## Quick ship (no card / small change)

Not every change is a Trello card. For a quick fix or doc tweak that doesn't warrant the full runbook below, the default ship flow is **PR + auto self-merge**:

```
git checkout -b <prefix>/<short-name>     # off main
git add <files> && git commit -m "..."     # only the files you touched
git push -u origin <branch>
gh pr create --fill                        # PR record + URL, no clicking
gh pr merge --merge                        # self-merge (see note); use --merge, not --squash
git checkout master && git pull origin master  # fast-forward local master to the merge
```

**No approval needed.** `master` is an unprotected branch on this solo private repo, so GitHub disabling the "Approve" button on your *own* PR is irrelevant — a required review only applies under a branch-protection rule, and this repo has none. Don't stop to ask the user to approve or open the PR by hand. (If the user says "just merge / direct", skip the PR entirely and fast-forward `master`.) The full card runbook (Phase 6) uses this same merge step inside the worktree flow.

---

## Before You Start: Create a Tracker Doc

**This is mandatory.** Before doing anything else, create a file `plans/tracker_<branch>.md` (create the `plans/` directory if it doesn't exist yet) with every step from this runbook as a checkbox list. Example:

```markdown
# Tracker: feat/backend-seam

## Phase 1: Pick Up the Card
- [ ] Claim the top card with `trello grab` (atomic; the two-phase handshake is the fallback), before anything else
- [ ] Pull latest master
- [ ] Read the card (description, comments, DESIGN.md)
- [ ] Create worktree and branch

## Phase 2: Research
- [ ] Read the referenced code
- [ ] Trace the call chain
...
```

Check off each step as you complete it. This is your source of truth for progress — if you get interrupted or context is lost, the tracker tells you exactly where you left off. Delete the tracker file after the card is shipped.

---

## Worktree Quick Reference

All work happens in an isolated **git worktree** under `.trees/` (gitignored). This lets multiple agents work on different cards simultaneously without interfering with each other. The root checkout stays on `master` — never switch it to a feature branch.

| Command | What it does |
|---------|-------------|
| `git worktree add .trees/wt<k> -b <branch> master` | Create a worktree in slot `wt<k>` + branch from master |
| `git worktree list` | Show all active worktrees |
| `git worktree remove .trees/<name>` | Remove a worktree (clean up) |
| `git worktree prune` | Clean up stale worktree references |

**Key rules:**
- Each worktree gets its own branch; a branch can only be checked out in one worktree at a time
- Gitignored files do NOT exist in a fresh worktree — most importantly `.venv/`. Set up a venv in the worktree before running anything: `python -m venv .venv` then `.venv/Scripts/python.exe -m pip install -e .`
- All worktree directories live under `.trees/` (gitignored at repo root)
- Windows note: if `git worktree remove` fails with "Permission denied", `cd` out of the worktree first (the Bash tool's own cwd can't be inside the dir you're deleting), kill any `python.exe` still running from that worktree's `.venv`, then retry. A freshly synced `.venv` being scanned by Defender/Search indexer can also hold a transient lock — retry after a few seconds
- **Slot naming (mandatory):** worktree directories use fixed slot names `wt1`..`wt8`, NOT branch names. Pick the lowest slot not shown in `git worktree list`. If `git worktree add` fails because the directory already exists, another agent grabbed that slot in the same instant — take the next one. Branch names stay fully descriptive; the slot is only the folder

### Running the CLI from a worktree

There is no server or port to manage. Set up an editable install in the worktree's venv and invoke it through that venv's interpreter so you're exercising the worktree's code, not the global `trello`:

```
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .
$env:PYTHONIOENCODING='utf-8'; .venv/Scripts/python.exe -m trello_cli --backend local --board <id> card ls "To Do"
```

(`trello` is the same as `python -m trello_cli` once installed; from a worktree, prefer the explicit `.venv/Scripts/python.exe -m trello_cli` form so there's no ambiguity about which copy of the code is running.)

**One caution specific to this project:**
- **Mutating commands hit the shared project board for real.** (The board is on the local backend now, so these write to the local file store, which syncs to every machine sharing the Dropbox folder.) `card add/move/archive`, `comment add/delete`, `label set`, etc. take effect immediately and are visible to anyone on the board — there's no dry-run. When you need to verify a *write* path, prefer a throwaway scratch board (`trello --backend local board add "scratch-<branch>"`) or a scratch card you create and clean up, rather than mutating real cards on the TrelloCLI board. Read commands (`ls`, `show`, `boards`, `labels`) are side-effect-free and safe to run freely.

---

## Phase 1: Pick Up the Card

> **Fastest path: the atomic `grab` command.** When you are told to "pick up the top card/ticket", you do not need to run the two-phase handshake below by hand. `trello grab` does the whole claim in one command:
>
> ```
> trello --backend local --board 6a353ffc grab --from "To Do" --to "Doing"
> ```
>
> It pops the top card of To Do, moves it to Doing, and prints the card it got you (it exits 1 when To Do is empty). Fire it from several agents at once and no two will get the same card. On this board the local backend makes the grab **truly atomic** -- it pops the card under the store lock, with no claim-comment race at all. The manual two-phase handshake below is exactly what `grab` does for the remote backend, so reach for it only when you are claiming a card by hand.

> **Claim the card FIRST — and confirm the claim before you trust it.** When several agents are launched in tandem and each is told to "pick up the top card of To Do", they all read the board, go off and do some work, and only *then* move the card — so they all grab the *same* card. Moving a card to Doing is a fast claim, but "read the board" and "move the card" can't be truly atomic, so two agents can *both* land on the same card within the same second. The fix is a two-phase claim: move it to Doing immediately (the fast grab), then post a claim comment and wait — the **earliest claim comment wins**, deterministically, because comments carry server timestamps. Do the move *before* reading the card, pulling master, or any other step; keep the read→move gap to those two back-to-back commands with **nothing in between**. If the board shows the top card is already in Doing (another agent beat you to it), claim the next To Do card down instead.

1. **Claim the top card with the two-phase handshake (do this first, nothing before it)** — Run these in order, with nothing else interleaved:
    1. **Mint a claim ID** once for this session — a short unique token (e.g. `python -c "import secrets; print(secrets.token_hex(4))"`). Reuse the same ID for every claim attempt this session.
    2. **Grab it** — View the To Do list (`trello --backend local --board 6a353ffc card ls "To Do"`), then *immediately* `trello --backend local --board 6a353ffc card move <card_id> Doing` for the top card. (If the top card is already in Doing, target the next one down instead.)
    3. **Post the claim comment** — `trello --backend local --board 6a353ffc comment add <card_id> "I am doing this now — claim <claim_id>"`. This exact phrase is the lock marker other agents scan for.
    4. **Wait 10-30s**, randomly pick a waiting length between these values, then **re-read the card's comments with their timestamps** — `trello --backend local --board 6a353ffc --json comment ls <card_id>`. Use `--json`: the formatted `comment ls` prints only the day, but the JSON `date` field is a millisecond-precision ISO timestamp, which is what a 10s tie-break needs.
    5. **Resolve ties — earliest claim comment wins.** Look at every comment containing "I am doing this now". If any such comment from a *different* agent (different claim ID) has a `date` **earlier than yours**, you lost the race: that agent owns the card. Back off — `trello --backend local --board 6a353ffc comment delete <card_id> <your_comment_id>` to remove your own claim comment (note it takes **both** the card id and the comment id, the `id` field from the JSON above), and **leave the card in Doing** (don't yank it from the winner). Then:
        - If you were told to work a **specific** card, stop here — end the session; the card is taken.
        - If the request was **generic** ("top card of To Do"), go back to (ii) and claim the **next** To Do card down, repeating the whole handshake.
    6. **You hold the lock** when your claim comment is the earliest (or the only) "I am doing this now". Only now read the card and proceed.
2. **Pull latest master** — `git pull origin master` so you start from the newest code
3. **Read the card** — Now that it's claimed, read the card description and the relevant section of [`DESIGN.md`](DESIGN.md). DESIGN.md is the long-form source of truth; the card is a pointer to a phase
4. **Create worktree and branch** — Branch off `master` with a descriptive prefix:
    - Bugs: `fix/<short-name>` (e.g. `fix/pos-midpoint-rounding`)
    - Features: `feat/<short-name>` (e.g. `feat/backend-seam`)
    - Refactoring: `refactor/<short-name>`
    - Docs / plans only: `docs/<short-name>`
    ```
    git worktree add .trees/wt<k> -b <branch> master   # lowest free slot; see Worktree Quick Reference
    cd .trees/wt<k>
    python -m venv .venv
    .venv/Scripts/python.exe -m pip install -e .
    git push -u origin <branch>
    ```
5. **All subsequent work happens inside `.trees/wt<k>/`**

## Phase 2: Research

Dig into the problem before proposing solutions. Use `/research` for topics that need external context (e.g. a Trello REST endpoint's exact params/response shape, an `httpx` behaviour, FastAPI/SortableJS specifics for the web-app phases).

6. **Read the referenced code** — The card and `DESIGN.md` cite specific files. Read them — descriptions can drift. The architecture map in [`CLAUDE.md`](CLAUDE.md) is the fastest orientation
7. **Trace the call chain** — The layers (all documented in `CLAUDE.md`):
    - `trello_cli/config.py` — credentials + the local-backend root (`local_root`), stored in `~/.trello-cli.json`; board/backend selection is per-invocation only (`--board`/`--backend`/env), never persisted
    - `trello_cli/api.py` — thin `httpx` client over the Trello REST API; requests only the fields each command needs
    - `trello_cli/fmt.py` — compact table/detail formatting + helpers (`short_id`, `truncate`, `due_str`, `label_str`, `is_image`, `size_str`, `print_json`). **Backend-agnostic** — it formats plain Trello-shaped dicts, which is what makes the planned local backend cheap
    - `trello_cli/main.py` — CLI entry point: noun-group dispatch (`card`, `list`, `label`, `checklist`, `comment`, `attachment`), the `_resolve_*` prefix resolvers, `_require_board()`, and `--json` / `--board` handling
8. **Identify the blast radius** — Does it touch the **dict shape** every command and `fmt.py` depend on? Trello-shaped keys (`id`, `name`, `idList`, `pos`, `labels`, `checkItems`, `state`, …) are a contract — a backend or command that drops a field will `KeyError` downstream. Does it touch **resolver semantics** (ID / ID-prefix / case-insensitive name-prefix, `SystemExit` on miss/ambiguity)? **Board scope** (`_require_board()`, `--board`, `TRELLO_BOARD`)? **`pos` math** (float midpoints for `card pos` / `list pos`)? **Output mode** (`--json` → raw `print_json`, else formatted)? Read-only formatting changes have a small blast radius; anything touching the API client or the dict contract is wide
9. **Research unknowns** — Use `/research` for anything needing external knowledge: a specific Trello REST endpoint's params/response, `httpx` quirks, FastAPI routing/`StaticFiles`, SortableJS drag events
10. **Summarize findings** — Brief writeup of what you learned: root cause (bugs), design options (features), or risk areas (refactors). Becomes input to the design phase

## Phase 3: Design

11. **Draft the approach** — Either update `DESIGN.md` or write a short plan under `plans/<file>.md`. Include:
    - **Context**: what the card is about and why it matters
    - **Design**: file-by-file changes; any new dict fields or backend methods; new command surfaces or flags; new config keys
    - **Tests**: which commands you'll run to verify, against what (a real board, a scratch board, or a throwaway card), and the expected output
    - **Out of scope**: what you're explicitly *not* doing
12. **Check for reusable patterns** — Look for existing utilities and conventions before inventing new ones: the `_resolve_*` resolvers, the `_dispatch(group, subcmds, args)` router, the `fmt.py` helpers, the field-narrowing in `api.py` (request only the fields a command needs), the `_is_json()` branch. Match the existing style rather than adding a parallel one
13. **Align with the user** — Present the plan, get approval before writing code

## Phase 4: Implement

14. **Make the changes** — Edit files per the approved plan. Follow project conventions:
    - **Python 3.12.** Use `python` (NOT `python3` — it hits the Windows Store alias); always set `PYTHONIOENCODING=utf-8` (Windows defaults to cp1252 and crashes on UTF-8 in JSON/output). The inline-prefix form is shell-specific: PowerShell → `$env:PYTHONIOENCODING='utf-8'; python ...`; bash → `PYTHONIOENCODING=utf-8 python ...`
    - **Keep the dict contract intact** — every command and `fmt.py` reads Trello-shaped dicts. If you add a backend or a code path, populate every field the formatters read (even as empty) or downstream commands `KeyError`
    - **Request only the fields you need** — `api.py` narrows each request's `fields=` to keep responses (and context) small. Preserve that discipline; don't fetch whole objects when a command uses three keys
    - **Resolvers stay strict** — accept ID / ID-prefix / case-insensitive name-prefix, and `SystemExit` on miss or ambiguity. Don't silently pick the first match
    - **Respect output mode** — read commands must branch on `_is_json()` and emit raw JSON via `print_json` under `--json`; don't print human tables in JSON mode
    - **Secrets:** credentials live in `~/.trello-cli.json` (and the `TRELLO_KEY` / `TRELLO_TOKEN` env vars) — never hardcode or commit a key/token. This is a public repo
    - **Comments**: default to none; only add when the *why* is non-obvious. Don't narrate what the code does
15. **Document new conventions** — Update [`CLAUDE.md`](CLAUDE.md) (the architecture map + conventions) and the [`README.md`](README.md) command list if the change adds a command/flag, a config key, a new module, or changes a documented contract. `CLAUDE.md` is the pickup guide and source of truth — keep it current

## Phase 5: Verify

There is no typecheck/build gate — this is a small CLI. **Verification is functional: run the affected commands and read the output.**

16. **Exercise every command path you touched** — against a real board for read paths, and a **scratch board or throwaway card for write paths** (see the caution in "Running the CLI from a worktree"). Run from the worktree's interpreter so you're testing the worktree's code:
    ```
    $env:PYTHONIOENCODING='utf-8'
    .venv/Scripts/python.exe -m trello_cli --backend local --board <id> <command you changed>
    ```
    For a refactor that must not change behavior (e.g. the Phase 0 backend seam), the bar is **identical output before and after** — run the same commands on `master` and on your branch and diff the results. Read commands are free to run; clean up anything a write path created.
17. **Check both output modes** — if you touched a read command, run it with and without `--json` and confirm both render correctly (formatted table vs. raw `print_json`)
18. **Spot-check the diff** — Read through once more for typos, dict keys that don't exist, resolvers that lost their strictness, requests that over-fetch fields, a JSON-mode path that prints a human table, and dead-code residue
19. **Flag what needs manual testing** — Leave a note for the user of anything you couldn't fully verify (e.g. "needs a board with >1 member to confirm `card mine`", "web drag-drop reorder needs a manual browser check")

## Phase 6: Review & Ship

20. **Commit** — Descriptive message in the project's existing style (imperative, single-line subject; body explains *why* not *what*). Reference the card if useful. Push to the feature branch
21. **Peer review** — Run `/review` (spawns a fresh agent against the branch diff vs `master` with no prior context). It catches logic errors, missed edge cases, convention violations, naming issues we've gone blind to. Fix every finding before proceeding — even minor ones — unless the fix is a major undertaking (in which case track it as a follow-up card)
22. **Pull master into the branch** — `git pull origin master` to pick up anything that landed while you were working. Resolve conflicts using the rules below

### Merge Conflict Rules

22.1. **Default to master's version.** If a conflict is in code you didn't intentionally change, accept master's side. Someone else fixed a bug or added a feature — don't silently revert their work
22.2. **Assume incoming changes are important.** Treat every conflict as "master has a critical fix" until you've read the diff and confirmed otherwise. Be very careful about overwriting new code with your version
22.3. **Only keep your side for lines you specifically wrote.** If you changed a function and master also changed it, read both versions carefully. Merge surgically — keep their fixes, layer your change on top
22.4. **If the merge is messy, restart from master.** When conflicts are widespread or hard to reason about, it's safer to take master wholesale and reimplement your changes on top. A clean re-apply is better than a botched merge
22.5. **Re-read the final result.** After resolving, read through every conflicted file in full. Make sure the merged code actually makes sense — don't just trust the conflict markers

23. **Re-verify after the merge** — re-run the commands from Phase 5 to make sure the merge didn't break anything
24. **Return to the root checkout** — `cd` back to the project root (where `master` is checked out). Remaining steps run from here
25. **Open a PR and self-merge** — `gh pr create --fill` then `gh pr merge --merge` (real merge commit, not `--squash`, so the branch's commits stay reachable and step 26's `git branch -d` still works), then `git pull origin master` to fast-forward the root checkout. **No approval needed** — `master` is unprotected on this solo repo, so GitHub disabling "Approve" on your own PR is irrelevant; a required review only applies under a branch-protection rule, of which there is none. The PR is a record/URL with no extra ceremony — don't wait on a human to approve. (Direct `git merge <branch> && git push` is the fallback if `gh` is unavailable.)
26. **Clean up the worktree and branch** — kill any process still running from the worktree FIRST (it holds the worktree directory lock)
    ```
    git worktree remove .trees/wt<k>
    git worktree prune
    git branch -d <branch>
    git push origin --delete <branch>
    ```
27. **Delete the plan + tracker files** — If the card had a `plans/<file>.md` behind it, delete it now (`git rm plans/<file>.md && git rm plans/tracker_<branch>.md && git commit -m "Remove <name> plan; <feature/fix> is implemented" && git push`). The plans directory is for *open* work only; the tracker doc is per-card scratch. (Updates to `DESIGN.md` stay — that's the living roadmap.)
28. **Move card to Done** — `trello --backend local --board 6a353ffc card move <card_id> Done`
29. **Comment on the card** — `trello --backend local --board 6a353ffc comment add <card_id> "<summary>"`. Include: what changed, which files, what it fixes/adds, the commit hash(es), and what needs manual testing. Use real newlines in the text, not `\n` escapes. Leaves a paper trail for future debugging
30. **Create follow-up cards** — If review, implementation, or testing surfaced issues that are out of scope for this card (pre-existing bugs, minor improvements, edge cases deferred as too risky to bundle), create new Trello cards (`trello --backend local --board 6a353ffc card add "To Do" "<title>" "<desc>"`). Reference the original card so there's a trail. Don't let follow-up work disappear into commit messages — if it's worth noting, it's worth tracking
31. **Write an overview of the changes made** — As the final step, post a concise overview to the user summarizing the work: what changed (the user-facing behavior delta, not a file list), which files were touched, anything that still needs manual testing or follow-up, and the commit hash(es) and merged branch. This is the closing handoff — it's how the user picks the session up cold and knows the card is actually shipped

## Phase 7: Clean up

Stop any processes (a `trello serve` web server, etc.) you've started, and remove any scratch boards/cards you created to verify write paths :)

---

## Quick Reference: Card Categories

| Category | Key concerns |
|----------|-------------|
| **CLI commands / dispatch** | `main.py` — `_dispatch(group, subcmds, args)` routes verbs; bare nouns fall back to `ls`. New verbs go in the group's subcmd table. Read commands branch on `_is_json()` |
| **Resolvers** | Every domain has a `_resolve_*` helper: accepts ID, ID-prefix, or case-insensitive name-prefix; `SystemExit` on miss/ambiguity. Keep them strict — never silently pick the first match |
| **API client** | `api.py` — thin `httpx` over Trello REST. Narrow `fields=` to only what the command needs (keeps responses + context small). Secrets come from `~/.trello-cli.json` / env, never hardcoded |
| **Formatting** | `fmt.py` — backend-agnostic; formats Trello-shaped dicts. Touching the dict shape is a wide blast radius (every command reads these keys). `print_json` is the `--json` path |
| **Board scope / config** | `config.py` + `_require_board()`. Honors `--board <name_or_id>` and `TRELLO_BOARD`. Credentials + `local_root` persist in `~/.trello-cli.json`; board/backend selection is per-invocation only (no active board) |
| **Backends (planned, see DESIGN.md)** | The `Backend` ABC is the CLI's ~40 ops. Both `TrelloBackend` and the future `LocalBackend` must return the **same dict shape** or `fmt.py` / commands `KeyError`. Local backend: 24-hex IDs + float `pos` so resolvers and `pos` math behave identically |
| **Web app (planned, see DESIGN.md)** | FastAPI over the same `Backend`; vanilla JS + SortableJS, no build step. Binds `127.0.0.1` by default — remote exposure is an opt-in with a token, never the default. Web deps go in an optional `[web]` extra so the core CLI stays httpx-only |
| **Refactoring** | High blast radius if it touches the dict contract, the resolvers, or board scoping. Verify by diffing command output before/after against a real board — behavior must be identical |
