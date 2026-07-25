"""Render a run's stream-json log into a transcript a human (or an Opus) can read.

A single 30-turn run is ~150KB of JSONL, most of it message plumbing. The signal
an AX review actually needs is small and always the same shape: what did it try
first, what did the tool say back, what did it try next, what did it end up with.
This squeezes one to the other — roughly 50-100x smaller, so a whole 30-run
corpus fits in one context window and can be read in one pass.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_CMD = 400
MAX_OUT = 220
MAX_ERR = 320


def _clip(text: str, limit: int) -> str:
    """Collapse to one line and cut to `limit`, saying how much was dropped.

    Tool output is read here for its shape, not its content — a truncated table
    still tells you the command worked, and the dropped-chars count tells you
    how much of the agent's context it ate.
    """
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return f"{flat[: limit - 20].rstrip()} …[+{len(flat) - limit + 20} chars]"


def _blocks(msg: dict) -> list[dict]:
    content = (msg or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content or []


@dataclass
class Step:
    n: int
    tool: str
    cmd: str
    out: str = ""
    is_error: bool = False

    @property
    def failed(self) -> bool:
        # The CLI raises SystemExit for every user-facing error, so a non-zero
        # exit — which Claude Code reports as an error tool_result — is exactly
        # the set of "the tool told the agent no" moments we want to count.
        return self.is_error


@dataclass
class Trace:
    steps: list[Step] = field(default_factory=list)
    final: str = ""
    notes: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0
    model: str = ""
    error: str = ""
    # Set when the run died on the provider's side (overload, rate limit) rather
    # than on anything the tool did — those runs carry no AX signal at all.
    api_error: str = ""
    # Provider retries Claude Code absorbed internally. A run can burn its whole
    # timeout in here and never reach a result record, so this is the only way to
    # tell "the tool confused it" from "the API was down".
    api_retries: int = 0

    @property
    def calls(self) -> int:
        return len(self.steps)

    @property
    def errors(self) -> int:
        return sum(1 for s in self.steps if s.failed)

    @property
    def first_error(self) -> str:
        for s in self.steps:
            if s.failed:
                return s.out
        return ""

    @property
    def commands(self) -> list[str]:
        return [s.cmd for s in self.steps]


def parse(path: str | Path) -> Trace:
    """Read a stream-json trace file into a Trace."""
    trace = Trace()
    pending: dict[str, Step] = {}
    for line in Path(path).read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = rec.get("type")
        if kind == "system" and rec.get("subtype") == "init":
            trace.model = rec.get("model", "")
        elif kind == "system" and rec.get("subtype") == "api_retry":
            trace.api_retries += 1
        elif kind == "assistant":
            for b in _blocks(rec.get("message", {})):
                if b.get("type") == "tool_use":
                    inp = b.get("input") or {}
                    cmd = inp.get("command") or inp.get("file_path") or json.dumps(inp)[:MAX_CMD]
                    step = Step(len(trace.steps) + 1, b.get("name", "?"), _clip(cmd, MAX_CMD))
                    trace.steps.append(step)
                    pending[b.get("id", "")] = step
        elif kind == "user":
            for b in _blocks(rec.get("message", {})):
                if b.get("type") == "tool_result":
                    step = pending.pop(b.get("tool_use_id", ""), None)
                    if step is None:
                        continue
                    content = b.get("content")
                    if isinstance(content, list):
                        content = "\n".join(
                            c.get("text", "") for c in content if isinstance(c, dict)
                        )
                    step.is_error = bool(b.get("is_error"))
                    raw = str(content or "")
                    if step.is_error:
                        # Drop Claude Code's own "Exit code 1" preamble so the
                        # error signature is the CLI's own words.
                        raw = re.sub(r"^Exit code \d+\s*", "", raw)
                    step.out = _clip(raw, MAX_ERR if step.is_error else MAX_OUT)
        elif kind == "result":
            trace.final = _clip(str(rec.get("result") or ""), 600)
            trace.cost_usd = float(rec.get("total_cost_usd") or 0)
            trace.duration_ms = int(rec.get("duration_ms") or 0)
            trace.num_turns = int(rec.get("num_turns") or 0)
            if rec.get("is_error"):
                trace.error = rec.get("subtype") or "error"
            if rec.get("api_error_status") or rec.get("terminal_reason") == "api_error":
                trace.api_error = str(rec.get("api_error_status") or "api_error")
    return trace


def to_markdown(trace: Trace, meta: dict) -> str:
    """One run -> one compact markdown section."""
    verdict = "PASS" if meta.get("passed") else "FAIL"
    head = [
        f"## {meta.get('case')} — {verdict}",
        "",
        f"`{meta.get('model', trace.model)}` · calls {trace.calls}/{meta.get('budget', '?')}"
        f" · errors {trace.errors} · ${trace.cost_usd:.3f} · {trace.duration_ms / 1000:.0f}s",
        "",
        f"**task** {meta.get('prompt', '')}",
    ]
    if meta.get("reason"):
        head += ["", f"**why it failed** {meta['reason']}"]
    head += ["", "**trace**", ""]

    body = []
    for s in trace.steps:
        mark = "✗" if s.failed else "·"
        body.append(f"{s.n}. {mark} `{s.cmd}`")
        if s.out:
            body.append(f"     → {s.out}")
    if not body:
        body = ["_(no tool calls)_"]

    tail = ["", f"**answer** {trace.final or '(none)'}"]
    if trace.error:
        tail.append(f"**run error** {trace.error}")
    return "\n".join(head + body + tail) + "\n"
