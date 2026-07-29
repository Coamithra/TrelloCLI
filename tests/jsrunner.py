"""Shared plumbing for the tests that run the REAL `app.js` under `node`.

`trello_cli/web/static/` is vanilla JS with no build step and no test runner
(deliberately), so the web client's testable logic is covered by slicing the
shipped source out of `app.js`, loading it under `node` against a DOM shim, and
asserting on what it produces. Nothing is copy-pasted, so a change to the
shipped code is a change to what is tested. Three files do this:
`test_linkify.py`, `test_markdown.py`, `test_render_state.py`.

**Plumbing only, and deliberately so.** This module holds the parts all three
genuinely share -- finding `node`, slicing between markers, running a script and
parsing its output. The DOM shims stay in the individual test files: linkify's
records an element's text, markdown's records attributes and style, and
render-state's needs selectors, dataset, focus and fake timers. Each is the
minimum its own slice touches, which is what makes them readable next to the
assertions they serve. A future "consolidate the shims" pass should know that
keeping them apart was the decision, not an oversight.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

STATIC = Path(__file__).resolve().parent.parent / "trello_cli" / "web" / "static"
APP_JS = STATIC / "app.js"


def node_or_skip() -> str:
    """Path to `node`, or skip -- never block the suite on a machine without it."""
    node = shutil.which("node")
    if node is None:  # pragma: no cover - environment-dependent
        pytest.skip("node not on PATH")
    return node


def slice_between(text: str, start: str, end: str, *, keep_end: bool = True) -> str:
    """The block of `text` from `start` through `end`.

    Both markers are required: a rename upstream raises here rather than
    silently yielding a slice that tests nothing.
    """
    if start not in text:
        raise AssertionError(f"start marker not found: {start!r}")
    begin = text.index(start)
    if end not in text[begin:]:
        raise AssertionError(f"end marker not found after start: {end!r}")
    stop = text.index(end, begin)
    return text[begin:stop + len(end)] if keep_end else text[begin:stop]


def run_node(script: str, *, timeout: int = 60) -> Any:
    """Run `script` as an ES module and parse the JSON it prints.

    Via a temp FILE rather than `node -e`: the scripts embed a whole DOM shim
    (and, for markdown, the ~125 KB vendored parser), while Windows caps a
    command line at ~32 KB.

    Both ends are pinned to UTF-8. node emits it regardless of platform, so
    letting Python decode the pipe with the locale codec (cp1252 on Windows)
    mojibakes any non-ASCII the sliced source carries -- app.js is full of
    `Loading...`-style ellipses and arrows.
    """
    node = node_or_skip()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run.mjs"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run(
            [node, str(path)],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        )
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def app_js_source() -> str:
    return APP_JS.read_text(encoding="utf-8")
