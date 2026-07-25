"""Turn a fanout into something you can act on.

Two artefacts: `index.md` — the scoreboard plus every failing transcript inlined,
which is the thing you hand to a model and ask "why did these fail?" — and
`corpus.md`, every transcript including the passes, for when you want to see what
the *successful* path looked like (an agent that passes in nine calls is still
telling you something about the tool).
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path


def _first_word_signature(cmd: str) -> str:
    """`trello --board X card ls "To Do"` -> `card ls`, for grouping."""
    toks = [t for t in re.split(r"\s+", cmd.strip()) if t]
    out = []
    skip_next = False
    for t in toks:
        if skip_next:
            skip_next = False
            continue
        if t in ("trello",):
            continue
        if t.startswith("--"):
            skip_next = t in ("--board", "--backend", "--local-root", "--server")
            continue
        out.append(t)
        if len(out) == 2:
            break
    return " ".join(out) or "(none)"


def _error_signature(msg: str) -> str:
    """Collapse a specific error into the class of error it belongs to."""
    m = msg.strip().lower()
    for pat, name in (
        (r"unknown flag", "unknown flag"),
        (r"unknown command", "unknown command"),
        (r"no board specified", "no board specified"),
        (r"ambiguous", "ambiguous name/prefix"),
        (r"not found|no such|no card|no list|no label", "not found"),
        (r"usage:", "bare usage dump"),
        (r"traceback", "traceback"),
        (r"command not found", "command not found"),
        (r"missing|required|expects|takes", "missing argument"),
    ):
        if re.search(pat, m):
            return name
    return (m.split("\n")[0][:60] or "(none)") if m else "(none)"


def summarize(results: list[dict]) -> dict:
    total = len(results)
    passed = [r for r in results if r["passed"]]
    by_tier: dict[int, list[dict]] = defaultdict(list)
    by_tag: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_tier[r["tier"]].append(r)
        for t in r["tags"]:
            by_tag[t].append(r)

    first_cmds = Counter(
        _first_word_signature(r["commands"][1] if len(r["commands"]) > 1 else r["commands"][0])
        for r in results if r["commands"]
    )
    errors = Counter(
        _error_signature(s)
        for r in results for s in ([r["first_error"]] if r["first_error"] else [])
    )
    return {
        "total": total,
        "passed": len(passed),
        "pass_rate": round(len(passed) / total, 3) if total else 0,
        "calls_total": sum(r["calls"] for r in results),
        "calls_budget": sum(r["budget"] for r in results),
        "over_budget_runs": sum(1 for r in results if r["over_budget"] > 0),
        "error_runs": sum(1 for r in results if r["errors"] > 0),
        "cost_usd": round(sum(r["cost_usd"] for r in results), 3),
        "wall_s": round(sum(r["wall_s"] for r in results), 1),
        "peeked": [r["case"] for r in results if r["peeked_at_source"]],
        "bypassed_cli": [r["case"] for r in results if r.get("bypassed_cli")],
        "by_tier": {
            t: {"n": len(rs), "passed": sum(1 for r in rs if r["passed"])}
            for t, rs in sorted(by_tier.items())
        },
        "by_tag": {
            t: {"n": len(rs), "passed": sum(1 for r in rs if r["passed"])}
            for t, rs in sorted(by_tag.items())
        },
        "first_commands": first_cmds.most_common(),
        "error_classes": errors.most_common(),
    }


def _table(results: list[dict]) -> list[str]:
    rows = [
        "| case | tier | verdict | calls/budget | errors | cost | why |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in sorted(results, key=lambda r: (r["tier"], r["case"])):
        verdict = "✅" if r["passed"] else "❌"
        why = (r["reason"] or "").replace("|", "/")[:70]
        rows.append(
            f"| `{r['case']}` | {r['tier']} | {verdict} | {r['calls']}/{r['budget']} "
            f"| {r['errors']} | ${r['cost_usd']:.3f} | {why} |"
        )
    return rows


def write(run_dir: Path, results: list[dict]) -> dict:
    s = summarize(results)
    lines = [
        f"# AX run — {run_dir.name}",
        "",
        f"**{s['passed']}/{s['total']} passed** ({s['pass_rate']:.0%}) · "
        f"{s['calls_total']} tool calls against a {s['calls_budget']} budget · "
        f"{s['error_runs']} runs hit at least one tool error · "
        f"${s['cost_usd']} · {s['wall_s'] / 60:.0f} min wall",
        "",
        "## By tier",
        "",
        "| tier | passed |",
        "| --- | --- |",
    ]
    for t, v in s["by_tier"].items():
        lines.append(f"| {t} | {v['passed']}/{v['n']} |")
    lines += ["", "## By tag", "", "| tag | passed |", "| --- | --- |"]
    for t, v in sorted(s["by_tag"].items(), key=lambda kv: kv[1]["passed"] / kv[1]["n"]):
        lines.append(f"| {t} | {v['passed']}/{v['n']} |")

    lines += ["", "## Error classes hit (first error per run)", ""]
    lines += [f"- {name} — {n}" for name, n in s["error_classes"]] or ["- none"]
    lines += ["", "## What each run reached for after orientation", ""]
    lines += [f"- `{cmd}` — {n}" for cmd, n in s["first_commands"]]
    if s["bypassed_cli"]:
        lines += [
            "",
            "> ⚠ **went around the CLI** and read the store files directly — the "
            f"tool had no command for what these needed: {s['bypassed_cli']}",
        ]
    if s["peeked"]:
        lines += ["", f"> ⚠ read the package source, so these no longer measure AX: {s['peeked']}"]

    lines += ["", "## Results", ""] + _table(results)

    failures = [r for r in results if not r["passed"]]
    lines += ["", f"## Failing transcripts ({len(failures)})", ""]
    for r in failures:
        t = _transcript(run_dir, r)
        if t:
            lines += [t, ""]
    (run_dir / "index.md").write_text("\n".join(lines) + "\n")

    corpus = [f"# AX corpus — {run_dir.name}", ""]
    for r in sorted(results, key=lambda r: (r["tier"], r["case"])):
        t = _transcript(run_dir, r)
        if t:
            corpus += [t, ""]
    (run_dir / "corpus.md").write_text("\n".join(corpus) + "\n")
    (run_dir / "summary.json").write_text(json.dumps(s, indent=2))
    return s


def _transcript(run_dir: Path, r: dict) -> str:
    name = r["case"] if not r.get("rep") else f"{r['case']}#{r['rep']}"
    p = run_dir / name / "transcript.md"
    return p.read_text() if p.exists() else ""


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="ax.report")
    ap.add_argument("run_dir")
    args = ap.parse_args(argv)
    run_dir = Path(args.run_dir)
    results = json.loads((run_dir / "results.json").read_text())
    write(run_dir, results)
    print(f"wrote {run_dir}/index.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
