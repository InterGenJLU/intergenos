# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Run the shipped web page, whole, under node (see web_page_harness.js).

``run_page(steps, constants)`` hands the page a list of steps and returns what
it did: the frames it sent, every close of its socket, the banners it showed,
the lines in its message pane, and anything it logged. A case asserts on that,
so what is exercised is the page that ships, not a description of it.

The harness needs node. Cases using it skip with that reason where node is not
installed, as the other cases that run page code under node do.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

HARNESS = Path(__file__).with_name("web_page_harness.js")
APP_JS = Path(__file__).resolve().parents[1] / "web" / "app.js"
NODE = shutil.which("node")
NEEDS_NODE = "node is required to run the shipped page"


def run_page(steps: list, constants: dict | None = None,
             timeout: float = 60.0) -> dict:
    """The page's record of a run. A harness failure is raised, never passed:
    a page that could not be driven has shown nothing about its behaviour."""
    with tempfile.TemporaryDirectory(prefix="web-page-") as tmp:
        plan = Path(tmp) / "plan.json"
        plan.write_text(json.dumps({"constants": constants or {},
                                    "steps": steps}), encoding="utf-8")
        run = subprocess.run([NODE, str(HARNESS), str(APP_JS), str(plan)],
                             capture_output=True, text=True, timeout=timeout)
    if run.returncode != 0:
        raise AssertionError(
            f"the page harness failed (exit {run.returncode}): {run.stderr}")
    record = json.loads(run.stdout)
    if record["logged"]:
        raise AssertionError(
            "the page logged while it was driven, which is how it reports an "
            f"exception thrown inside a frame handler: {record['logged']}")
    return record


def connected() -> list:
    """The steps that bring the page to a connected socket."""
    return [{"open": True},
            {"server": {"type": "connected", "client_id": "page-case",
                        "system_status": {}}}]


def sent_types(record: dict) -> list:
    return [frame.get("type") for frame in record["sent"]]


def pane_texts(pane: list) -> list:
    return [line["text"] for line in pane]
