# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The packaged intergen.service syscall re-allow set.

The unit takes @system-service and then denies @privileged and @resources. Both
denials remove syscalls the inference backend genuinely needs, and a denied
syscall here is not an error return — the kernel SIGSYS-kills the process, so
the service crash-loops until its start limit trips.

Four have been measured, each on real hardware, each added only after a
crash-loop was traced to it:

  sched_setaffinity   the GGML threadpool pins workers by P/E-core topology
  sched_setscheduler  the GPU/Vulkan backend's thread setup
  setpriority         the same backend's thread setup
  mbind               NUMA page placement during ROCm/HIP memory-pool bring-up
                      (added 2026-08-06, AMD serving)

WHAT THESE TESTS PROTECT is the shape of the fix, not just its presence. The
tempting repair for each new crash is to stop denying the group — drop
`~@resources` and every one of these problems disappears at once. That would
hand a user-session AI daemon the whole resource-control surface to fix a NUMA
call. So the tests pin three things together: the group denials are still there,
every re-allow is an individually named syscall rather than a group, and each
named syscall is one the denials actually remove — a re-allow for something that
was never denied is dead text that will mislead the next reader.

Membership is checked against the systemd on this machine via
`systemd-analyze syscall-filter`, and skipped when that is unavailable rather
than asserted from a hard-coded list that would rot.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

_EXPECTED_REALLOWS = {
    "sched_setaffinity",
    "sched_setscheduler",
    "setpriority",
    "mbind",
}

_DENIED_GROUPS = ("~@privileged", "~@resources")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _extract_service_heredoc() -> str | None:
    build_sh = _repo_root() / "packages" / "ai" / "intergen" / "build.sh"
    if not build_sh.is_file():
        return None
    text = build_sh.read_text(encoding="utf-8")
    m = re.search(r"<<\s*'SERVICE'\n(.*?)\nSERVICE\b", text, re.DOTALL)
    return m.group(1) if m else None


def _filter_values(body: str) -> list[str]:
    """Every SystemCallFilter= value, in order, comments stripped."""
    out = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("#") or "=" not in s:
            continue
        key, _, value = s.partition("=")
        if key.strip() == "SystemCallFilter":
            out.append(value.strip())
    return out


def _group_members(group: str) -> set[str] | None:
    """The syscalls in a systemd filter group, or None when unavailable."""
    if not shutil.which("systemd-analyze"):
        return None
    try:
        r = subprocess.run(["systemd-analyze", "syscall-filter", group],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    members = set()
    for line in r.stdout.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("@"):
            continue
        members.add(s)
    return members or None


class UnitPresentTest(unittest.TestCase):
    def test_the_heredoc_is_locatable(self):
        self.assertIsNotNone(
            _extract_service_heredoc(),
            "the packaged intergen.service heredoc could not be found in "
            "packages/ai/intergen/build.sh — every test below would pass "
            "vacuously")


class ReAllowSetTest(unittest.TestCase):
    def setUp(self):
        body = _extract_service_heredoc()
        if body is None:
            self.skipTest("packaging tree not present")
        self.body = body
        self.values = _filter_values(body)

    def test_the_baseline_allowlist_is_still_the_umbrella(self):
        self.assertIn("@system-service", self.values)

    def test_both_group_denials_are_still_in_place(self):
        for denial in _DENIED_GROUPS:
            self.assertIn(
                denial, self.values,
                f"{denial} was removed. Dropping a group denial makes every "
                f"crash-loop in this class disappear, which is exactly why it "
                f"must not be how they are fixed.")

    def test_mbind_is_re_allowed(self):
        self.assertIn(
            "mbind", self.values,
            "mbind (237) is not re-allowed; ROCm/HIP memory-pool bring-up "
            "calls it and @resources denies it, so the backend is "
            "SIGSYS-killed during initialisation")

    def test_the_re_allow_set_is_exactly_the_measured_four(self):
        """A closed set, so an unexplained addition has to be deliberate.

        Every entry here cost a crash-loop to find. Growing the set silently is
        how a targeted exception list turns back into a wide grant.
        """
        named = {v for v in self.values
                 if not v.startswith("@") and not v.startswith("~")}
        self.assertEqual(
            named, _EXPECTED_REALLOWS,
            "the named syscall re-allow set changed; if that is intended, "
            "update _EXPECTED_REALLOWS and say in the recipe comment what was "
            "measured")

    def test_no_re_allow_is_a_group(self):
        """The whole discipline in one assertion.

        Anything after the denials that starts with @ re-opens a class rather
        than a call.
        """
        denials_seen = False
        for value in self.values:
            if value.startswith("~"):
                denials_seen = True
                continue
            if denials_seen:
                self.assertFalse(
                    value.startswith("@"),
                    f"a group ({value}) is re-allowed after the denials — "
                    f"re-allows must be individually named syscalls")

    def test_the_capability_set_is_still_empty(self):
        """A user-session daemon needs no Linux capabilities.

        Pinned alongside the syscall set because it is the other thing that
        would silently make these crashes go away for the wrong reason.
        """
        for key in ("CapabilityBoundingSet", "AmbientCapabilities"):
            values = [ln.split("=", 1)[1].strip()
                      for ln in self.body.splitlines()
                      if ln.strip().startswith(f"{key}=")]
            self.assertTrue(values, f"{key} is no longer set at all")
            for v in values:
                self.assertEqual(v, "", f"{key} is no longer empty: {v!r}")


class ReAllowsAreLoadBearingTest(unittest.TestCase):
    """Each named syscall must be one the denials actually remove.

    A re-allow for a syscall that was never denied does nothing except tell the
    next reader something untrue about why it is there.
    """

    def setUp(self):
        body = _extract_service_heredoc()
        if body is None:
            self.skipTest("packaging tree not present")
        self.values = _filter_values(body)
        self.denied = set()
        for group in ("@privileged", "@resources"):
            members = _group_members(group)
            if members is None:
                self.skipTest(
                    "systemd-analyze syscall-filter is unavailable, so group "
                    "membership cannot be measured on this machine")
            self.denied |= members

    def test_every_named_re_allow_is_denied_by_a_group_above_it(self):
        named = [v for v in self.values
                 if not v.startswith("@") and not v.startswith("~")]
        self.assertTrue(named, "no named re-allows found")
        for syscall in named:
            with self.subTest(syscall=syscall):
                self.assertIn(
                    syscall, self.denied,
                    f"{syscall} is re-allowed but neither @privileged nor "
                    f"@resources denies it — the line has no effect and its "
                    f"comment claims otherwise")

    def test_mbind_is_denied_by_resources_specifically(self):
        """Pin the reason, not just the outcome.

        The recipe comment says @resources is what takes mbind away. If that
        ever stops being true the comment is wrong, and a wrong reason in a
        security comment is worse than no comment.
        """
        members = _group_members("@resources")
        if members is None:
            self.skipTest("systemd-analyze syscall-filter is unavailable")
        self.assertIn("mbind", members,
                      "@resources no longer contains mbind — the recipe "
                      "comment's stated reason is out of date")


if __name__ == "__main__":
    unittest.main()
