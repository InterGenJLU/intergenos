# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""M4 (r26) capability-claim gate — regression pins.

The gate stops the small model fabricating a pkm subcommand ("run `pkm frobnicate
x`") by verifying every checkable `pkm <sub>` invocation in a draft against the
REAL parser surface (data/capability-surface.json) before delivery. Three
verdicts, all pinned here:

  * clean       — surface present, no fabricated invocation → deliver as-is.
  * violation   — surface present, the named subcommand is not real → regenerate
                  once grounded, else serve the honest capability fallback.
  * unavailable — the ground-truth surface is MISSING/unreadable (Ruling 2:
                  fail-LOUD, never a silent green) → serve the honest-under-
                  uncertainty fallback for a marker'd claim, else deliver the
                  draft, and WARN every turn.

Anti-lobotomy is pinned too: prose ("the pkm package manager") and an honest
correction ("there is no `pkm add` — use `pkm install`") must read CLEAN.

Router wiring (_screen_and_correct_capability) maps the three verdicts to the
capability_screen glass decision + the right fallback; the web path
(web_server._stream_llm_response) implements the SAME three-verdict handling
(web parity).
"""

from __future__ import annotations

import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path

import intergen.glass as glass
import intergen.safety as safety
from intergen.safety import (
    screen_capability_claim,
    capability_unverified_fallback,
    honest_capability_fallback,
)
from intergen.router import ConversationRouter


# ── glass capture (same pattern as test_glass) ──

def _glass_reset(tmp: str) -> None:
    os.environ["XDG_STATE_HOME"] = tmp
    os.environ.pop("INTERGEN_GLASS", None)
    glass._glass = None


def _glass_rows(tmp: str) -> list[dict]:
    p = Path(tmp) / "intergen" / "glass.jsonl"
    if not p.exists():
        return []
    with open(p) as f:
        return [json.loads(x) for x in f]


class ScreenCapabilityClaimSurfacePresent(unittest.TestCase):
    """Surface present (capability-surface.json ships) — the shipped gate."""

    def setUp(self) -> None:
        # Ensure the real surface is loaded (a prior test may have cleared/faked
        # it): reset the path to the shipped artifact and drop the lru_cache.
        safety._CAP_SURFACE_PATH = (
            Path(safety.__file__).with_name("data") / "capability-surface.json")
        safety._pkm_surface.cache_clear()
        # Guard: the shipped artifact must actually load, else these assertions
        # would silently test the degraded path.
        valid, _ = safety._pkm_surface()
        self.assertTrue(valid, "capability-surface.json must load for this suite")

    def test_real_install_is_clean(self) -> None:
        self.assertEqual(
            screen_capability_claim("run `pkm install firefox`"), ("clean", None))

    def test_alias_sync_is_clean(self) -> None:
        # `sync` is a real alias of `update` in the surface artifact.
        self.assertEqual(
            screen_capability_claim("just run `pkm sync`"), ("clean", None))

    def test_fabricated_subcommand_is_a_violation(self) -> None:
        self.assertEqual(
            screen_capability_claim("run `pkm frobnicate x`"),
            ("violation", "pkm frobnicate"))

    def test_negation_correction_is_clean(self) -> None:
        # The model DENYING an invalid subcommand is correct — never trip the gate.
        self.assertEqual(
            screen_capability_claim("there is no `pkm add` — use `pkm install`"),
            ("clean", None))

    def test_prose_mention_is_clean(self) -> None:
        # Prose, no invocation shape → nothing to verify.
        self.assertEqual(
            screen_capability_claim("the pkm package manager is great"),
            ("clean", None))


class ScreenCapabilityClaimSurfaceAbsent(unittest.TestCase):
    """Surface MISSING → 'unavailable' (Ruling 2 fail-loud), never a silent green."""

    def setUp(self) -> None:
        self._orig = safety._CAP_SURFACE_PATH
        safety._CAP_SURFACE_PATH = Path(tempfile.gettempdir()) / "no-such-cap-surface.json"
        safety._pkm_surface.cache_clear()

    def tearDown(self) -> None:
        safety._CAP_SURFACE_PATH = self._orig
        safety._pkm_surface.cache_clear()

    def test_invocation_is_unavailable_with_marker(self) -> None:
        self.assertEqual(
            screen_capability_claim("run `pkm install firefox`"),
            ("unavailable", "pkm install"))

    def test_no_invocation_is_unavailable_without_marker(self) -> None:
        self.assertEqual(
            screen_capability_claim("the weather is nice today"),
            ("unavailable", None))


class RouterCapabilityWiring(unittest.TestCase):
    """_screen_and_correct_capability maps each verdict to the right glass row
    + delivered text."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        _glass_reset(self.tmp)
        self.r = ConversationRouter.__new__(ConversationRouter)
        # Restore the shipped surface for the clean/violation cases.
        safety._CAP_SURFACE_PATH = (
            Path(safety.__file__).with_name("data") / "capability-surface.json")
        safety._pkm_surface.cache_clear()

    def _cap_rows(self) -> list[dict]:
        return [x for x in _glass_rows(self.tmp)
                if x.get("event") == "capability_screen"]

    def test_clean_delivers_draft_and_logs_clean(self) -> None:
        draft = "run `pkm install firefox`"
        with glass.turn(glass.new_turn_id(), "test"):
            out = self.r._screen_and_correct_capability(draft, [], source="dbus")
        self.assertEqual(out, draft)
        rows = self._cap_rows()
        self.assertEqual(rows[-1]["detail"]["verdict"], "clean")

    def test_violation_regen_fails_serves_honest_fallback(self) -> None:
        # Stub regeneration to fail so the honest fallback path is exercised.
        self.r._regenerate_with_capability_grounding = lambda messages, marker: None
        with glass.turn(glass.new_turn_id(), "test"):
            out = self.r._screen_and_correct_capability(
                "run `pkm frobnicate x`", [], source="dbus")
        self.assertEqual(out, honest_capability_fallback("pkm frobnicate"))
        rows = self._cap_rows()
        self.assertEqual(rows[-1]["detail"]["verdict"],
                         "violation_regen_failed_fallback")
        self.assertEqual(rows[-1]["detail"]["marker"], "pkm frobnicate")

    def test_violation_regen_succeeds_delivers_correction(self) -> None:
        self.r._regenerate_with_capability_grounding = (
            lambda messages, marker: "Use `pkm install firefox` instead.")
        with glass.turn(glass.new_turn_id(), "test"):
            out = self.r._screen_and_correct_capability(
                "run `pkm frobnicate x`", [], source="dbus")
        self.assertEqual(out, "Use `pkm install firefox` instead.")
        self.assertEqual(self._cap_rows()[-1]["detail"]["verdict"],
                         "violation_regenerated")


class RouterCapabilityWiringSurfaceAbsent(unittest.TestCase):
    """Router wiring for the degraded (surface missing) path."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        _glass_reset(self.tmp)
        self.r = ConversationRouter.__new__(ConversationRouter)
        self._orig = safety._CAP_SURFACE_PATH
        safety._CAP_SURFACE_PATH = Path(tempfile.gettempdir()) / "no-such-cap-surface.json"
        safety._pkm_surface.cache_clear()

    def tearDown(self) -> None:
        safety._CAP_SURFACE_PATH = self._orig
        safety._pkm_surface.cache_clear()

    def _cap_rows(self) -> list[dict]:
        return [x for x in _glass_rows(self.tmp)
                if x.get("event") == "capability_screen"]

    def test_unavailable_with_marker_serves_unverified_fallback(self) -> None:
        with glass.turn(glass.new_turn_id(), "test"):
            out = self.r._screen_and_correct_capability(
                "run `pkm install firefox`", [], source="dbus")
        self.assertEqual(out, capability_unverified_fallback("pkm install"))
        row = self._cap_rows()[-1]["detail"]
        self.assertEqual(row["verdict"], "unavailable_no_surface_fallback")
        self.assertEqual(row["marker"], "pkm install")

    def test_unavailable_without_marker_delivers_draft(self) -> None:
        draft = "the weather is nice today"
        with glass.turn(glass.new_turn_id(), "test"):
            out = self.r._screen_and_correct_capability(draft, [], source="dbus")
        self.assertEqual(out, draft)
        self.assertEqual(self._cap_rows()[-1]["detail"]["verdict"],
                         "unavailable_no_surface")


class WebPathParity(unittest.TestCase):
    """The web path implements the SAME three-verdict handling as the router —
    it must call the shared screen_capability_claim and map the same outcomes.
    Source-level parity guard: fails loudly if the web capability gate is removed
    or diverges from the shared verdict/fallback vocabulary."""

    def test_web_stream_uses_shared_gate_and_verdicts(self) -> None:
        from intergen import web_server
        src = inspect.getsource(web_server.WebServer._stream_llm_response)
        self.assertIn("screen_capability_claim", src)
        # the three verdicts + both degraded outcome strings + the fallbacks
        for token in ("clean", "unavailable",
                      "unavailable_no_surface_fallback", "unavailable_no_surface",
                      "capability_unverified_fallback",
                      "capability_screen"):
            self.assertIn(token, src, f"web path missing {token!r} handling")


if __name__ == "__main__":
    unittest.main(verbosity=2)
