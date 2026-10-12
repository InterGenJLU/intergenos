# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Exercise the distiller through its command-line input and output files."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/post-burn-distill.py"
STAMP = "20260827T120000Z"


@pytest.fixture
def distill(tmp_path):
    trace = tmp_path / "trace"
    host = tmp_path / "host"
    trace.mkdir()
    host.mkdir()

    def run(records=(), *, traces=None, logs=None, cap=250000):
        inputs = traces if traces is not None else {
            f"build-pkg-example-lib-{STAMP}-run.jsonl": records,
        }
        for name, rows in inputs.items():
            (trace / name).write_text("".join(json.dumps(r) + "\n" for r in rows))
        for name, text in (logs or {}).items():
            (host / name).write_text(text)
        output = tmp_path / "result"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--since", "20260827T0000",
             "--until", "20260827T2359", "--trace-root", str(trace),
             "--host-logs", str(host), "--out", str(output),
             "--bundle-cap", str(cap)], capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return output, result.stdout

    return run


def class_text(output, name):
    path = output / "out" / f"class-{name}.txt"
    return path.read_text() if path.exists() else ""


def stream(text, **fields):
    return {"type": "subprocess_output", "text": text, **fields}


def capture(text, **fields):
    return {"type": "pkg_capture", "pkg": "example-lib", "tier": "extra",
            "output": text, "ts": "same-time", "output_bytes": len(text),
            "rc": 0, **fields}


def test_package_stream_joins_chunks_and_keeps_repeated_lines(distill):
    output, _ = distill([
        stream("warning: li"), stream("brary diagnostic\n"),
        stream("warning: library diagnostic\n"),
        {"type": "subprocess_end", "rc": 0},
    ])
    text = class_text(output, "warnings")
    assert "count=2 pkgs=1 runs[green=2]" in text
    assert "example-lib|green] warning: library diagnostic" in text


def test_package_subprocess_end_reads_both_streams(distill):
    output, _ = distill([{"type": "subprocess_end", "rc": 1,
                          "stdout": "using bundled library\n",
                          "stderr": "error: configuration stopped\n"}])
    assert "example-lib|halted] using bundled library" in class_text(output, "fallback")
    assert "example-lib|halted] error:" in class_text(output, "error")


def test_identical_stream_capture_and_host_log_count_once(distill):
    text = "warning: first diagnostic\nwarning: second diagnostic\n"
    output, _ = distill([
        stream("warning: first diagnostic\n", pkg="example-lib"),
        stream("warning: second diagnostic\n", pkg="example-lib"),
        {"type": "subprocess_end", "rc": 0, "stdout": text},
        capture(text),
    ], logs={"example-lib-20260827-120000.log": text})
    warnings = class_text(output, "warnings")
    assert warnings.count("count=1 pkgs=1") == 2
    assert "count=2" not in warnings


def test_distinct_capture_bytes_with_equal_metadata_survive(distill):
    output, _ = distill([capture("warning: alpha\n"), capture("warning: bravo\n")])
    warnings = class_text(output, "warnings")
    assert "warning: alpha" in warnings
    assert "warning: bravo" in warnings


def test_tier_capture_hash_reads_beyond_the_first_two_hundred_bytes(distill):
    prefix = "plain build output\n" * 20
    output, _ = distill(traces={f"build-tier-extra-{STAMP}-run.jsonl": [
        {"type": "subprocess_end", "stdout": prefix + "warning: alpha\n", "rc": 0},
        {"type": "subprocess_end", "stdout": prefix + "warning: bravo\n", "rc": 0},
    ]})
    warnings = class_text(output, "warnings")
    assert "warning: alpha" in warnings
    assert "warning: bravo" in warnings


def test_nested_orchestrator_stream_is_not_a_package_source(distill):
    output, _ = distill(traces={f"build-orchestrator-{STAMP}-run.jsonl": [
        {"type": "subprocess_end", "stdout": "error: nested parent\n", "rc": 1},
        stream("error: nested parent\n", pkg="example-lib"),
    ]})
    assert not class_text(output, "error")


def test_unfinished_package_stream_is_retained_with_unknown_outcome(distill):
    output, _ = distill([stream("warning: unfinished capture\n")])
    assert "runs[unknown=1]" in class_text(output, "warnings")


def test_package_trace_outside_window_is_not_scanned(distill):
    output, _ = distill(traces={"build-pkg-example-lib-20260828T120000Z-run.jsonl": [
        stream("error: outside the requested window\n"),
    ]})
    assert not class_text(output, "error")


def test_capture_embedded_in_host_log_counts_once(distill):
    output, _ = distill([
        stream("warning: repeated diagnostic\n"),
        stream("warning: repeated diagnostic\n"),
        {"type": "subprocess_end", "rc": 0},
    ], logs={"example-lib-20260827-120000.log":
             "build starting\nwarning: repeated diagnostic\n"
             "warning: repeated diagnostic\ncompleted successfully\n"})
    assert "count=2 pkgs=1" in class_text(output, "warnings")
    assert "count=4" not in class_text(output, "warnings")


def test_equal_lines_in_different_packages_remain_distinct(distill):
    output, _ = distill([capture("warning: shared wording\n", pkg="alpha"),
                          capture("warning: shared wording\n", pkg="bravo")])
    assert "count=2 pkgs=2" in class_text(output, "warnings")
