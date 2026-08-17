# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen Sentinel scanner engine — vendor-neutral content scanners.

Concrete implementations of the `intergen.interfaces.scanner.Scanner` ABC:

    LocalRulesScanner  — always-on deterministic floor.
    ScannerPolicy      — composes the floor with an optional deeper scanner.
    LocalQwenScanner   — local llama.cpp Qwen classifier, on-demand keep-alive.
    CloudScanner       — opt-in, wraps a vendor-neutral cloud adapter.
"""

from __future__ import annotations

from intergen.scanner.cloud_scanner import CloudScanner
from intergen.scanner.local_rules import LocalRulesScanner
from intergen.scanner.local_qwen import LocalQwenScanner
from intergen.scanner.policy import ScannerPolicy, ScanDepth

__all__ = [
    "LocalRulesScanner",
    "LocalQwenScanner",
    "CloudScanner",
    "ScannerPolicy",
    "ScanDepth",
]
