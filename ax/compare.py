"""Compare two runs — did the patch actually move anything?

    python -m ax.compare ax/runs/baseline-haiku ax/runs/patched-haiku

The loop is only worth running if you close it, and "pass rate went up" is
usually the least interesting part of the answer: what you want to see is the
tool calls and the tool errors going down on the same corpus, because that is
the tax every real caller was paying.

Verdicts are recomputed under the *current* case rules wherever that is possible
from stored data (the `expect_cmd` mechanism check needs only the command list),
so a rule added after a baseline run still scores both runs the same way.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .cases import BY_ID


def _rescore(r: dict) -> dict:
    """Re-apply the rules that can be checked from a stored record."""
    case = BY_ID.get(r["case"])
    r = dict(r)
    if case and case.expect_cmd and r["passed"]:
        if not any(re.search(case.expect_cmd, c) for c in r["commands"]):
            r["passed"] = False
            r["reason"] = f"never used the intended command (/{case.expect_cmd}/)"
            r["rescored"] = True
    return r


def load(run_dir: Path) -> dict[str, dict]:
    rows = json.loads((run_dir / "results.json").read_text())
    return {r["case"]: _rescore(r) for r in rows}


def compare(before_dir: Path, after_dir: Path) -> str:
    before, after = load(before_dir), load(after_dir)
    shared = [c for c in before if c in after]

    def agg(runs: dict, key: str) -> int:
        return sum(runs[c][key] for c in shared)

    lines = [
        f"# AX comparison — {before_dir.name} → {after_dir.name}",
        "",
        f"{len(shared)} shared cases.",
        "",
        "| metric | before | after | delta |",
        "| --- | --- | --- | --- |",
    ]
    b_pass = sum(1 for c in shared if before[c]["passed"])
    a_pass = sum(1 for c in shared if after[c]["passed"])
    for label, b, a, better_is_low in (
        ("passed", b_pass, a_pass, False),
        ("tool calls", agg(before, "calls"), agg(after, "calls"), True),
        ("tool errors", agg(before, "errors"), agg(after, "errors"), True),
        ("runs with an error",
         sum(1 for c in shared if before[c]["errors"]),
         sum(1 for c in shared if after[c]["errors"]), True),
        ("runs over budget",
         sum(1 for c in shared if before[c]["over_budget"]),
         sum(1 for c in shared if after[c]["over_budget"]), True),
        ("calls over budget",
         agg(before, "over_budget"), agg(after, "over_budget"), True),
        ("went around the CLI",
         sum(1 for c in shared if before[c].get("bypassed_cli")),
         sum(1 for c in shared if after[c].get("bypassed_cli")), True),
    ):
        delta = a - b
        arrow = "" if delta == 0 else ("✅" if (delta < 0) == better_is_low else "⚠️")
        lines.append(f"| {label} | {b} | {a} | {delta:+d} {arrow} |")

    lines += ["", "## Per case", "",
              "| case | before | after | calls | errors |",
              "| --- | --- | --- | --- | --- |"]
    for c in sorted(shared, key=lambda c: (BY_ID[c].tier if c in BY_ID else 9, c)):
        b, a = before[c], after[c]
        flip = ""
        if b["passed"] != a["passed"]:
            flip = " 🟢 fixed" if a["passed"] else " 🔴 regressed"
        lines.append(
            f"| `{c}` | {'✅' if b['passed'] else '❌'} | {'✅' if a['passed'] else '❌'}{flip} "
            f"| {b['calls']} → {a['calls']} | {b['errors']} → {a['errors']} |"
        )

    gone = [c for c in shared if before[c]["first_error"] and not after[c]["first_error"]]
    new = [c for c in shared if after[c]["first_error"] and not before[c]["first_error"]]
    lines += ["", f"**Runs that no longer hit any error:** {gone or 'none'}",
              "", f"**Runs that now hit one:** {new or 'none'}"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="ax.compare")
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--out", default=None, help="also write the report here")
    args = ap.parse_args(argv)
    text = compare(Path(args.before), Path(args.after))
    print(text)
    if args.out:
        Path(args.out).write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
