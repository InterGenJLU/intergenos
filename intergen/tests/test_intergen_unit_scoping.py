# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Panic-remediation wave (2026-07-08) — the intergen.service session-class
scoping guard + the bounded-restart invariant.

Regression protection for the greeter-DynamicUser restart loop that ran the
user-enabled intergen.service unbounded for a machine's whole uptime (11,506
cycles / 59h observed 2026-07-08; the on-CPU task in that boot's kernel-fault
post-mortem). Two closures, both authored into the packaged unit heredoc in
packages/ai/intergen/build.sh:

  LEG 1 — a session-class guard. `ConditionUser=!@system` alone was the defect:
  a greeter DynamicUser (uid ~60584) is NOT system-range, so it PASSED and the
  unit started in every greeter manager. The fix LAYERS a second guard —
  `ExecCondition=... getent -s files passwd $(id -u)` — which skips (does NOT
  fail, so schedules no restart) any manager whose uid is not a real account in
  the LOCAL passwd files backend. `-s files` bypasses nss-systemd, which would
  otherwise resolve a DynamicUser while its scope is live and defeat the check.

  LEG 2 — bounded restarts. The interval MUST exceed StartLimitBurst*RestartSec
  so a genuine crash-loop accumulates its burst inside the window and trips into
  a loud failed state. The systemd default 10s is shorter than 5*5=25s, so the
  burst never accumulated and the limit never fired. Shipped: 30s / 5 / 5s.

RED/GREEN: revert either guard (drop the ExecCondition line, or set
StartLimitIntervalSec below Burst*RestartSec) and the matching test fails; with
the shipped heredoc they pass.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path


def _repo_root() -> Path:
    # intergen/tests/<this> -> intergen/tests -> intergen -> repo root
    return Path(__file__).resolve().parents[2]


def _extract_service_heredoc() -> str | None:
    """Return the body of the `<< 'SERVICE' ... SERVICE` heredoc that authors
    /usr/lib/systemd/user/intergen.service in the package build.sh, or None if
    the packaging tree isn't present (e.g. tests run from an installed copy)."""
    build_sh = _repo_root() / "packages" / "ai" / "intergen" / "build.sh"
    if not build_sh.is_file():
        return None
    text = build_sh.read_text(encoding="utf-8")
    m = re.search(r"<<\s*'SERVICE'\n(.*?)\nSERVICE\b", text, re.DOTALL)
    return m.group(1) if m else None


def _unit_kv(body: str, key: str) -> list[str]:
    """All values of a systemd key= line in the unit body (comments stripped)."""
    out = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        if k.strip() == key:
            out.append(v.strip())
    return out


class TestIntergenUnitScoping(unittest.TestCase):
    def setUp(self):
        self.body = _extract_service_heredoc()
        if self.body is None:
            self.skipTest("packaging tree (packages/ai/intergen/build.sh) not present")

    # ---- LEG 1: the layered session-class guard ---------------------------
    def test_conditionuser_system_range_guard_present(self):
        self.assertIn(
            "!@system", _unit_kv(self.body, "ConditionUser"),
            "ConditionUser=!@system (system-range guard, excludes user@0 root-su) is missing",
        )

    def test_execcondition_files_backend_guard_present(self):
        execconds = _unit_kv(self.body, "ExecCondition")
        self.assertTrue(execconds, "ExecCondition session-class guard is missing entirely")
        joined = " ".join(execconds)
        # files backend, on the running uid — the two properties that make the
        # guard robust against a live DynamicUser scope.
        self.assertIn("getent", joined)
        self.assertRegex(
            joined, r"-s\s+files",
            "guard must use `getent -s files` — the default backend resolves a "
            "live DynamicUser via nss-systemd and would let the greeter through",
        )
        self.assertRegex(
            joined, r"passwd\s+.*id -u",
            "guard must check the RUNNING uid ($(id -u)) against passwd",
        )

    # ---- LEG 2: the bounded-restart invariant -----------------------------
    def test_restart_bound_invariant(self):
        def _one_int(key, default=None):
            vals = _unit_kv(self.body, key)
            if not vals:
                if default is not None:
                    return default
                self.fail(f"{key} not set in the unit")
            # strip a trailing unit suffix like 's'
            return int(re.match(r"(\d+)", vals[-1]).group(1))

        interval = _one_int("StartLimitIntervalSec")
        burst = _one_int("StartLimitBurst")
        restart_sec = _one_int("RestartSec")
        self.assertEqual(_unit_kv(self.body, "Restart"), ["on-failure"])
        # THE load-bearing invariant: the burst must be able to accumulate
        # inside the window, or the limit never fires (the default-10s bug).
        self.assertGreaterEqual(
            interval, burst * restart_sec,
            f"StartLimitIntervalSec={interval}s < StartLimitBurst({burst})*"
            f"RestartSec({restart_sec})={burst*restart_sec}s: a crash-loop's "
            f"burst can never accumulate in-window, so the restart bound never "
            f"trips (this is exactly the systemd-default-10s greeter-loop bug)",
        )

    # ---- functional: the guard predicate actually distinguishes membership -
    @unittest.skipUnless(shutil.which("getent"), "getent not available")
    def test_guard_predicate_distinguishes_files_passwd_membership(self):
        # Root (uid 0) is always in the local passwd files backend -> proceed.
        rc_present = subprocess.run(
            ["getent", "-s", "files", "passwd", "0"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode
        self.assertEqual(rc_present, 0, "root must resolve in files-passwd (guard would wrongly skip)")
        # A uid never provisioned in /etc/passwd -> skip (the DynamicUser class).
        rc_absent = subprocess.run(
            ["getent", "-s", "files", "passwd", "2000000000"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode
        self.assertNotEqual(rc_absent, 0, "a non-provisioned uid must NOT resolve in files-passwd")


if __name__ == "__main__":
    unittest.main()
