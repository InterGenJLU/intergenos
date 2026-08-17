# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Deep-scan tier wiring — ScannerPolicy default_depth + ToolRegistry attach.

The chokepoint calls policy.scan(content, ctx) with no explicit depth, so the
configured posture must ride on the policy's default_depth: baseline escalates to
the deep tier only on a floor FLAG; deep always escalates. attach_deep_scanner is
the wiring-layer seam dbus_daemon uses once the LocalQwen tier is constructed.
"""

from __future__ import annotations

import unittest

from intergen.tool_registry import ToolRegistry
from intergen.interfaces.scanner import (
    Scanner,
    ScanContext,
    ScanDirection,
    ScanDisposition,
    ScanVerdict,
)
from intergen.scanner.policy import ScannerPolicy, ScanDepth


class _StubScanner(Scanner):
    def __init__(self, disposition, name="stub"):
        self._d = disposition
        self._name = name
        self.calls = 0

    @property
    def name(self):
        return self._name

    @property
    def is_local(self):
        return True

    def scan(self, content, ctx):
        self.calls += 1
        return ScanVerdict(disposition=self._d, reason=f"{self._name}-verdict", scanner=self._name)


def _ctx():
    return ScanContext("mcp:x/y", ScanDirection.INGRESS, "mcp_x_y")


class DefaultDepthTests(unittest.TestCase):
    def test_baseline_default_does_not_escalate_on_allow(self):
        floor = _StubScanner(ScanDisposition.ALLOW, "floor")
        deep = _StubScanner(ScanDisposition.BLOCK, "deep")
        policy = ScannerPolicy(local_rules=floor, deep_scanner=deep)  # default baseline
        verdict = policy.scan("x", _ctx())  # no explicit depth
        self.assertIs(verdict.disposition, ScanDisposition.ALLOW)
        self.assertEqual(deep.calls, 0, "deep tier ran at baseline on a clean floor")

    def test_deep_default_always_escalates(self):
        floor = _StubScanner(ScanDisposition.ALLOW, "floor")
        deep = _StubScanner(ScanDisposition.BLOCK, "deep")
        policy = ScannerPolicy(
            local_rules=floor, deep_scanner=deep, default_depth=ScanDepth.DEEP
        )
        verdict = policy.scan("x", _ctx())  # no explicit depth -> uses DEEP
        self.assertIs(verdict.disposition, ScanDisposition.BLOCK)
        self.assertEqual(deep.calls, 1)

    def test_baseline_escalates_on_floor_flag(self):
        floor = _StubScanner(ScanDisposition.FLAG, "floor")
        deep = _StubScanner(ScanDisposition.BLOCK, "deep")
        policy = ScannerPolicy(local_rules=floor, deep_scanner=deep)
        verdict = policy.scan("x", _ctx())
        self.assertIs(verdict.disposition, ScanDisposition.BLOCK)
        self.assertEqual(deep.calls, 1)

    def test_explicit_depth_overrides_default(self):
        floor = _StubScanner(ScanDisposition.ALLOW, "floor")
        deep = _StubScanner(ScanDisposition.BLOCK, "deep")
        policy = ScannerPolicy(local_rules=floor, deep_scanner=deep)
        verdict = policy.scan("x", _ctx(), depth=ScanDepth.DEEP)
        self.assertIs(verdict.disposition, ScanDisposition.BLOCK)


class AttachDeepScannerTests(unittest.TestCase):
    def test_attach_returns_false_without_policy(self):
        reg = ToolRegistry(scanner_policy=None)
        self.assertFalse(reg.attach_deep_scanner(_StubScanner(ScanDisposition.BLOCK)))

    def test_attach_returns_true_and_enables_escalation(self):
        floor = _StubScanner(ScanDisposition.FLAG, "floor")
        policy = ScannerPolicy(local_rules=floor)  # no deep tier yet
        reg = ToolRegistry(scanner_policy=policy)
        # Before attach: a floor FLAG has no deep tier -> stays FLAG.
        self.assertIs(policy.scan("x", _ctx()).disposition, ScanDisposition.FLAG)
        # After attach: the floor FLAG escalates to the deep tier's BLOCK.
        self.assertTrue(reg.attach_deep_scanner(_StubScanner(ScanDisposition.BLOCK, "deep")))
        self.assertIs(policy.scan("x", _ctx()).disposition, ScanDisposition.BLOCK)


if __name__ == "__main__":
    unittest.main()
