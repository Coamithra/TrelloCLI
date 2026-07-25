"""Fan a case corpus out across cold agent runs and collect the evidence.

Every run gets its own seeded store, its own working directory, and nothing
else: no CLAUDE.md, no project settings, no MCP servers, no skills, no hint
about which command to reach for. The only things teaching it the tool are
`trello --help` and whatever the CLI says when it gets something wrong — which
is precisely the surface we are trying to measure.

    python -m ax.runner --cases all --model haiku --parallel 6

Writes runs/<run-id>/ with, per case, the raw stream-json trace, a rendered
transcript, and a verdict; then an index.md + results.json over the lot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from . import fixture, render, report
from .cases import Case, select

REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "ax" / "runs"

# Neutral: it tells the agent it is headless, not how the CLI works.
SYSTEM_NUDGE = (
    "You are running non-interactively with no human to answer questions. "
    "Make reasonable choices, finish the task, then state the outcome plainly."
)

# Commands that mean the agent stopped using the public surface and went looking
# at the implementation — that is a valid strategy for a human, but it means the
# run no longer measures the tool's agent experience.
PEEK_MARKERS = ("trello_cli", str(REPO), "site-packages", "pip show", "pip download")

# Provider-side failures (429/529) carry no AX signal — retry rather than score.
API_RETRIES = 3
API_BACKOFF_S = 20


def _claude() -> str:
    exe = shutil.which("claude")
    if not exe:
        raise SystemExit("ax: the `claude` CLI is not on PATH — the harness drives it headlessly.")
    return exe


def _python() -> str:
    venv = REPO / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def _make_shim(dirpath: Path) -> Path:
    """A PATH containing exactly one thing: `trello`.

    Deliberately not the venv's bin dir — that would hand the agent a `python`
    pointed straight at the package source.
    """
    dirpath.mkdir(parents=True, exist_ok=True)
    shim = dirpath / "trello"
    shim.write_text(f'#!/bin/sh\nexec "{_python()}" -m trello_cli "$@"\n')
    shim.chmod(0o755)
    return dirpath


def _digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def _verify(
    case: Case, store_root: Path, answer: str, changed: bool, commands: list[str]
) -> tuple[bool, str]:
    if case.readonly and changed:
        return False, "read-only task, but the agent modified the board"
    if case.expect_cmd and not any(re.search(case.expect_cmd, c) for c in commands):
        return False, f"never used the intended command (/{case.expect_cmd}/)"
    if case.verify:
        try:
            reason = case.verify(fixture.Store(store_root))
        except Exception as exc:  # a verifier blowing up is a failure, not a crash
            return False, f"verifier error: {exc}"
        if reason:
            return False, reason
    low = answer.lower()
    missing = [w for w in case.expect if w.lower() not in low]
    if missing:
        return False, f"answer missing {missing}"
    said = [w for w in case.forbid if w.lower() in low]
    if said:
        return False, f"answer contains forbidden {said}"
    return True, ""


def run_case(
    case: Case, run_dir: Path, model: str, timeout: int, budget_usd: float, rep: int = 0
) -> dict:
    case_dir = run_dir / (case.id if rep == 0 else f"{case.id}#{rep}")
    store = case_dir / "store"
    work = case_dir / "work"
    shutil.rmtree(case_dir, ignore_errors=True)
    work.mkdir(parents=True)
    fixture.build(store)
    before = _digest(store)

    claude = _claude()
    env = {
        **os.environ,
        # The agent's whole world: our shim, claude's own runtime, coreutils.
        "PATH": f"{run_dir / 'bin'}:{Path(claude).parent}:/usr/local/bin:/usr/bin:/bin",
        "TRELLO_BACKEND": "local",
        "TRELLO_LOCAL_ROOT": str(store),
    }
    env.pop("TRELLO_BOARD", None)

    cmd = [
        claude, "-p", case.prompt,
        "--model", model,
        "--output-format", "stream-json", "--verbose",
        "--tools", "Bash",          # a shell and nothing else — same as a real caller
        "--allowed-tools", "Bash",  # pre-approved, so nothing blocks on a prompt
        "--strict-mcp-config",          # no MCP servers from the host config
        "--setting-sources", "",        # no user/project settings, no hooks
        "--disable-slash-commands",     # no skills
        "--no-session-persistence",
        "--append-system-prompt", SYSTEM_NUDGE,
        "--max-budget-usd", str(budget_usd),
    ]

    started = time.time()
    trace_path = case_dir / "trace.jsonl"
    status = "ok"
    # A provider-side 429/529 produces a run with no tool calls and no signal.
    # Scoring that as a tool failure would poison the comparison, so re-run it —
    # a fanout of dozens of concurrent agents will hit these.
    for attempt in range(API_RETRIES + 1):
        with open(trace_path, "w") as out:
            try:
                proc = subprocess.run(
                    cmd, cwd=work, env=env, stdout=out,
                    stderr=subprocess.PIPE, timeout=timeout, text=True,
                )
                status = "ok" if proc.returncode == 0 else f"exit {proc.returncode}"
                if proc.returncode != 0:
                    (case_dir / "stderr.txt").write_text(proc.stderr or "")
            except subprocess.TimeoutExpired:
                status = "timeout"
        trace = render.parse(trace_path)
        if status == "timeout" and trace.api_retries:
            # It didn't run out of ideas, it ran out of API. Claude Code's own
            # backoff can eat the whole timeout without ever emitting a result.
            trace.api_error = f"timeout after {trace.api_retries} provider retries"
        if not trace.api_error or attempt == API_RETRIES:
            break
        print(f"  retry {case.id}: API {trace.api_error}", flush=True)
        time.sleep(API_BACKOFF_S * (2 ** attempt))
        shutil.rmtree(store, ignore_errors=True)
        fixture.build(store)
        before = _digest(store)
    wall = time.time() - started

    if trace.api_error:
        status = f"api {trace.api_error}"
    changed = _digest(store) != before
    passed, reason = _verify(case, store, trace.final, changed, trace.commands)
    if status != "ok" and not passed:
        reason = reason or status

    # Two different kinds of "left the public surface", and they mean opposite
    # things. Reading the *store* is the tool failing the agent — it had no
    # command for what it needed and went around the CLI. Reading the *source*
    # is the agent failing the experiment — whatever it learned there, a real
    # caller wouldn't have.
    bypassed = [c for c in trace.commands if str(store) in c]
    peeked = [
        c for c in trace.commands
        if str(store) not in c and any(m in c for m in PEEK_MARKERS)
    ]
    result = {
        "case": case.id,
        "rep": rep,
        "tier": case.tier,
        "tags": case.tags,
        "prompt": case.prompt,
        "model": model,
        "passed": passed,
        "reason": reason,
        "status": status,
        "budget": case.budget,
        "calls": trace.calls,
        "over_budget": max(0, trace.calls - case.budget),
        "errors": trace.errors,
        "first_error": trace.first_error,
        "commands": trace.commands,
        "answer": trace.final,
        "cost_usd": round(trace.cost_usd, 4),
        "wall_s": round(wall, 1),
        "changed_store": changed,
        "api_error": trace.api_error,
        "bypassed_cli": bypassed,
        "peeked_at_source": peeked,
    }
    (case_dir / "result.json").write_text(json.dumps(result, indent=2))
    (case_dir / "transcript.md").write_text(render.to_markdown(trace, result))
    mark = "PASS" if passed else "FAIL"
    print(
        f"  {mark:4} {case.id:24} calls {trace.calls:>2}/{case.budget:<2} "
        f"err {trace.errors:>2}  ${trace.cost_usd:.3f}  {reason[:60]}",
        flush=True,
    )
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ax.runner")
    ap.add_argument("--cases", default="all", help="all | t1 | tag:write | case-id,case-id")
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--parallel", type=int, default=6)
    ap.add_argument("--repeat", type=int, default=1, help="runs per case (variance)")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--budget-usd", type=float, default=0.60, help="spend cap per run")
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args(argv)

    cases = select(args.cases)
    run_id = args.run_id or f"{time.strftime('%Y%m%d-%H%M%S')}-{args.model}"
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _make_shim(run_dir / "bin")

    jobs = [(c, r) for c in cases for r in range(args.repeat)]
    print(f"ax: {len(cases)} cases x{args.repeat} on {args.model} -> {run_dir}", flush=True)

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = [
            pool.submit(run_case, c, run_dir, args.model, args.timeout, args.budget_usd, rep)
            for c, rep in jobs
        ]
        for f in futures:
            results.append(f.result())

    # Re-running a subset into an existing run merges over it, so a handful of
    # cases lost to a provider outage can be replayed without redoing the corpus.
    merged: dict[tuple[str, int], dict] = {}
    old = run_dir / "results.json"
    if old.exists():
        for r in json.loads(old.read_text()):
            merged[(r["case"], r.get("rep", 0))] = r
    else:
        # No index yet — a previous invocation was killed before it wrote one.
        # Each case wrote its own result as it finished, so nothing is lost.
        for path in sorted(run_dir.glob("*/result.json")):
            r = json.loads(path.read_text())
            merged.setdefault((r["case"], r.get("rep", 0)), r)
    for r in results:
        merged[(r["case"], r.get("rep", 0))] = r
    results = list(merged.values())

    old.write_text(json.dumps(results, indent=2))
    report.write(run_dir, results)
    passed = sum(1 for r in results if r["passed"])
    print(f"\nax: {passed}/{len(results)} passed — {run_dir}/index.md")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
