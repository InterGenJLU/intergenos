# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Branded GTK4 consent dialog — security-property tests.

Covers the swap that replaces the zenity render on the two consent surfaces with
the branded binary (`docs/research/branding/consent-dialog/`), exercising the
invariants WC's red-team conditioned the approval on:

  * visual integrity (§B / inv 8)      — bidi / zero-width / non-printing badged
  * scrubbed child env (§C / inv 10)    — injection family dropped, PATH fixed
  * transport (§A)                       — spec on stdin, never argv
  * pre-render watchdog + bounded wait   — fail-fast vs patient (§F / inv 11)
  * fail-closed exit-code mapping (§F)   — only affirmative codes allow/send
  * never-truncate the egress payload (§E)
  * stdin-parser robustness (§5.3)       — malformed/oversize → fail-closed
  * secret-highlight is an AID (§5.5)
  * zenity KEPT as the fallback (regression guard against the rogue removal)
  * path-logging preserved (§7 / inv 7)

Runs on any host; the watchdog tests use a stand-in subprocess (no display).
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from intergen import consent_dialog
from intergen import consent_dialog_proto as proto
from intergen import consent_modal, review_modal
from intergen.consent_dialog_sanitize import (
    detect_secret_ranges,
    sanitize_for_display,
)

_REPO = Path(__file__).resolve().parents[2]


def _gi_available() -> bool:
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk  # noqa: F401
        return True
    except Exception:
        return False


# ── §B / invariant 8 — visual integrity ──────────────────────────────────────
class SanitizeTests(unittest.TestCase):
    def test_bidi_override_is_badged(self):
        # RLO (U+202E) is the Trojan-Source reorder character.
        out = sanitize_for_display("admin‮gpj.exe")
        self.assertNotIn("‮", out)
        self.assertIn("U+202E", out)

    def test_isolates_and_marks_badged(self):
        for cp in (0x2066, 0x2069, 0x200F):
            out = sanitize_for_display(f"x{chr(cp)}y")
            self.assertNotIn(chr(cp), out)
            self.assertIn(f"U+{cp:04X}", out)

    def test_zero_width_and_bom_badged(self):
        for cp in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF):
            out = sanitize_for_display(f"a{chr(cp)}b")
            self.assertNotIn(chr(cp), out)

    def test_control_chars_badged(self):
        out = sanitize_for_display("a\x00\x07b")
        self.assertNotIn("\x00", out)
        self.assertIn("U+0000", out)

    def test_normal_text_newline_tab_preserved(self):
        s = "hello world\nsecond\tline / pkm install htop"
        self.assertEqual(sanitize_for_display(s), s)

    def test_ordinary_spaces_not_badged(self):
        self.assertEqual(sanitize_for_display("a b  c"), "a b  c")


# ── §5.5 — secret highlight is an AID ─────────────────────────────────────────
class SecretDetectorTests(unittest.TestCase):
    def test_finds_url_creds_and_assignment(self):
        shown = "url=postgres://app:S3cr3tPw@192.0.2.4/db\npassword: hunter2"
        spans = detect_secret_ranges(shown)
        found = {shown[a:b] for a, b in spans}
        self.assertIn("S3cr3tPw", found)
        self.assertIn("hunter2", found)

    def test_finds_key_prefixes(self):
        shown = "token AKIAIOSFODNN7EXAMPLE and ghp_abcdefgh12345678"
        found = {shown[a:b] for a, b in detect_secret_ranges(shown)}
        self.assertTrue(any("AKIA" in f for f in found))
        self.assertTrue(any("ghp_" in f for f in found))

    def test_ranges_within_bounds_and_safe_on_empty(self):
        self.assertEqual(detect_secret_ranges(""), [])
        shown = "nothing secret here at all"
        for a, b in detect_secret_ranges(shown):
            self.assertTrue(0 <= a < b <= len(shown))


# ── §C / invariant 10 — scrubbed child env ───────────────────────────────────
class ScrubbedEnvTests(unittest.TestCase):
    def test_injection_family_dropped_display_kept_path_fixed(self):
        poison = {
            "LD_PRELOAD": "/evil.so", "LD_LIBRARY_PATH": "/evil",
            "GTK_MODULES": "x", "GTK_PATH": "/x", "GIO_MODULE_DIR": "/x",
            "GIO_EXTRA_MODULES": "/x", "GTK_IM_MODULE": "x",
            "GSETTINGS_BACKEND": "x", "PYTHONPATH": "/x",
            "DISPLAY": ":0", "HOME": "/home/u",
        }
        with mock.patch.dict(os.environ, poison, clear=False):
            env = consent_dialog._scrubbed_env()
        for bad in ("LD_PRELOAD", "LD_LIBRARY_PATH", "GTK_MODULES", "GTK_PATH",
                    "GIO_MODULE_DIR", "GIO_EXTRA_MODULES", "GTK_IM_MODULE",
                    "GSETTINGS_BACKEND", "PYTHONPATH"):
            self.assertNotIn(bad, env)
        self.assertEqual(env.get("DISPLAY"), ":0")
        self.assertEqual(env.get("HOME"), "/home/u")
        self.assertEqual(env["PATH"], proto.SAFE_PATH)


# ── pre-render watchdog + bounded wait (§F / invariant 11), via a stand-in ────
class WatchdogTests(unittest.TestCase):
    """`_run_dialog` against a stand-in subprocess (no display). The marker is
    embedded by value so the stand-in needs no imports."""

    def _cmd(self, body: str) -> list[str]:
        return [sys.executable, "-c", body]

    _M = "M=%r" % proto.RENDERED_MARKER  # embeds the exact marker literal

    def test_rendered_then_exit_code_is_returned(self):
        body = ("import sys; sys.stdin.read(); %s; "
                "sys.stdout.write(M+chr(10)); sys.stdout.flush(); "
                "sys.exit(10)" % self._M)
        self.assertEqual(
            consent_dialog._run_dialog({"mode": "consent"}, cmd=self._cmd(body)), 10)

    def test_exit_without_render_returns_none(self):
        body = "import sys; sys.stdin.read(); sys.exit(70)"
        self.assertIsNone(
            consent_dialog._run_dialog({"mode": "consent"}, cmd=self._cmd(body)))

    def test_hang_after_render_hits_deadline_and_denies(self):
        body = ("import sys,time; sys.stdin.read(); %s; "
                "sys.stdout.write(M+chr(10)); sys.stdout.flush(); "
                "time.sleep(30)" % self._M)
        with mock.patch.object(proto, "POST_RENDER_DEADLINE_SECONDS", 1.0):
            code = consent_dialog._run_dialog({"mode": "consent"}, cmd=self._cmd(body))
        self.assertEqual(code, proto.EXIT_DENY)

    def test_never_renders_hangs_fails_fast_to_fallback(self):
        body = "import sys,time; sys.stdin.read(); time.sleep(30)"
        with mock.patch.object(proto, "PRE_RENDER_TIMEOUT_SECONDS", 1.0):
            code = consent_dialog._run_dialog({"mode": "consent"}, cmd=self._cmd(body))
        self.assertIsNone(code)

    def test_spec_delivered_on_stdin_not_argv(self):
        # Transport (§A): the stand-in proves the spec is readable on stdin. argv
        # carries only the literal command — no spec bytes.
        body = ("import sys,json; data=sys.stdin.read(); "
                "spec=json.loads(data); %s; "
                "sys.stdout.write(M+chr(10)); sys.stdout.flush(); "
                "sys.exit(10 if spec.get('payload')=='SENTINEL' else 1)" % self._M)
        code = consent_dialog._run_dialog(
            {"mode": "consent", "payload": "SENTINEL"}, cmd=self._cmd(body))
        self.assertEqual(code, 10)  # the child only sees SENTINEL via stdin


# ── fail-closed exit-code mapping (§F) ────────────────────────────────────────
class _Prov:
    value = "user_direct"


class _Dec:
    effective_provenance = _Prov()
    reason = "needs review"
    needs_pkexec = False


class _Call:
    name = "shell.run"
    arguments = {"cmd": "systemctl restart x"}


class MappingTests(unittest.TestCase):
    def test_review_codes_map_fail_closed(self):
        cases = {
            proto.EXIT_REVIEW_ALLOW_ONCE: "allow_once",
            proto.EXIT_REVIEW_ALLOW_CONVERSATION: "allow_conversation",
            proto.EXIT_DENY: "deny",
            139: "deny",   # SIGSEGV-ish crash code → deny
            0: "deny",     # a clean exit(0) is NOT consent → deny
            proto.EXIT_RENDER_FAILED: "deny",  # if seen post-render
        }
        for code, expected in cases.items():
            with mock.patch.object(consent_dialog, "_run_dialog", return_value=code):
                self.assertEqual(
                    consent_dialog.run_review_dialog(_Call(), _Dec()), expected)

    def test_review_render_failed_returns_none_for_fallback(self):
        with mock.patch.object(consent_dialog, "_run_dialog", return_value=None):
            self.assertIsNone(consent_dialog.run_review_dialog(_Call(), _Dec()))

    def test_consent_only_send_is_true(self):
        for code, expected in {proto.EXIT_CONSENT_SEND: True, proto.EXIT_DENY: False,
                               0: False, 137: False}.items():
            with mock.patch.object(consent_dialog, "_run_dialog", return_value=code):
                self.assertEqual(
                    consent_dialog.run_consent_dialog("x", "anthropic"), expected)

    def test_consent_render_failed_returns_none(self):
        with mock.patch.object(consent_dialog, "_run_dialog", return_value=None):
            self.assertIsNone(consent_dialog.run_consent_dialog("x", "anthropic"))

    def test_consent_oversize_denies_without_spawning(self):
        spawned = []
        with mock.patch.object(consent_dialog, "_run_dialog",
                               side_effect=lambda *a, **k: spawned.append(1)):
            result = consent_dialog.run_consent_dialog(
                "X" * (proto.MAX_PAYLOAD_BYTES + 1), "anthropic")
        self.assertFalse(result)            # §E — fail-closed, never truncate
        self.assertEqual(spawned, [])       # and never even spawned the binary


# ── stdin-parser robustness via the real binary (§5.3) ───────────────────────
@unittest.skipUnless(_gi_available(), "GTK4/gi not available on this host")
class BinaryFailClosedTests(unittest.TestCase):
    def _exit_code_for(self, stdin_bytes: bytes) -> int:
        import subprocess
        p = subprocess.run(
            [sys.executable, "-m", "intergen.consent_dialog_gtk"],
            input=stdin_bytes, cwd=str(_REPO),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return p.returncode

    def test_malformed_json_render_failed(self):
        self.assertEqual(self._exit_code_for(b"not json at all"),
                         proto.EXIT_RENDER_FAILED)

    def test_unknown_mode_render_failed(self):
        self.assertEqual(self._exit_code_for(b'{"mode":"bogus"}'),
                         proto.EXIT_RENDER_FAILED)

    def test_oversize_payload_denies(self):
        import json
        big = json.dumps({"mode": "consent",
                          "payload": "x" * (proto.MAX_PAYLOAD_BYTES + 8)})
        self.assertEqual(self._exit_code_for(big.encode()), proto.EXIT_DENY)


# ── inertness regression guard (source-level, portable) ──────────────────────
class InertSourceTests(unittest.TestCase):
    def test_binary_renders_inert_no_markup_path(self):
        src = (_REPO / "intergen" / "consent_dialog_gtk.py").read_text()
        self.assertIn("set_use_markup(False)", src)
        self.assertIn("buf.set_text(shown)", src)
        self.assertNotIn(".insert_markup(", src)   # no markup-insertion CALL
        self.assertNotIn("set_use_markup(True)", src)


# ── wiring: branded primary, zenity fallback, path-logging, no argv leak ──────
class WiringTests(unittest.TestCase):
    def test_zenity_paths_still_present(self):
        # zenity is KEPT as the fallback — guard against a future rogue removal.
        self.assertTrue(callable(review_modal._prompt_review_zenity))
        self.assertTrue(callable(consent_modal._prompt_consent_zenity))

    def test_review_argv_leak_closed_in_source(self):
        # The pre-existing leak: review_modal passed the body on argv (--text=).
        # The branded primary moves it to stdin; the zenity fallback's --text=
        # remains only on the fallback path. Assert the daemon helper writes the
        # spec to the child's stdin (never argv).
        helper = (_REPO / "intergen" / "consent_dialog.py").read_text()
        self.assertIn("proc.stdin.write(data)", helper)

    def test_prompt_review_branded_primary_logs_and_skips_zenity(self):
        with mock.patch.object(review_modal, "_session_active", return_value=True), \
             mock.patch.object(review_modal.consent_dialog, "run_review_dialog",
                               return_value="allow_once") as gtk, \
             mock.patch.object(review_modal, "_prompt_review_zenity") as zen, \
             self.assertLogs("intergen.review_modal", level="INFO") as logs:
            result = review_modal.prompt_review(_Call(), _Dec())
        self.assertEqual(result, "allow_once")
        gtk.assert_called_once()
        zen.assert_not_called()
        self.assertTrue(any("branded GTK" in m for m in logs.output))

    def test_prompt_review_branded_deny_not_reprompted(self):
        # A rendered DENY is final — must NOT fall through to a zenity re-prompt.
        with mock.patch.object(review_modal, "_session_active", return_value=True), \
             mock.patch.object(review_modal.consent_dialog, "run_review_dialog",
                               return_value="deny"), \
             mock.patch.object(review_modal, "_prompt_review_zenity") as zen:
            self.assertEqual(review_modal.prompt_review(_Call(), _Dec()), "deny")
        zen.assert_not_called()

    def test_prompt_review_falls_back_when_branded_unavailable(self):
        with mock.patch.object(review_modal, "_session_active", return_value=True), \
             mock.patch.object(review_modal.consent_dialog, "run_review_dialog",
                               return_value=None), \
             mock.patch.object(review_modal, "_prompt_review_zenity",
                               return_value="deny") as zen:
            self.assertEqual(review_modal.prompt_review(_Call(), _Dec()), "deny")
        zen.assert_called_once()

    def test_prompt_send_consent_branded_primary_logs(self):
        with mock.patch.object(consent_modal, "_session_active", return_value=True), \
             mock.patch.object(consent_modal.consent_dialog, "run_consent_dialog",
                               return_value=True) as gtk, \
             mock.patch.object(consent_modal, "_prompt_consent_zenity") as zen, \
             self.assertLogs("intergen.consent_modal", level="INFO") as logs:
            self.assertTrue(consent_modal.prompt_send_consent("hi", "anthropic"))
        gtk.assert_called_once()
        zen.assert_not_called()
        self.assertTrue(any("branded GTK" in m for m in logs.output))

    def test_prompt_send_consent_falls_back_when_branded_unavailable(self):
        with mock.patch.object(consent_modal, "_session_active", return_value=True), \
             mock.patch.object(consent_modal.consent_dialog, "run_consent_dialog",
                               return_value=None), \
             mock.patch.object(consent_modal, "_prompt_consent_zenity",
                               return_value=True) as zen:
            self.assertTrue(consent_modal.prompt_send_consent("hi", "anthropic"))
        zen.assert_called_once()


# ── punch-list hardening (WC adversarial pass #2) ─────────────────────────────
class ProvenanceBadgeTests(unittest.TestCase):
    def test_badge_class_keyed_to_real_trust(self):
        from intergen.consent_dialog_sanitize import provenance_badge_class as pbc
        self.assertEqual(pbc("user_direct"), "igc-badge-direct")     # green
        self.assertEqual(pbc("user_implied"), "igc-badge-warn")      # amber
        self.assertEqual(pbc("ingress_derived"), "igc-badge-danger")  # red

    def test_unknown_provenance_is_danger_failsafe(self):
        from intergen.consent_dialog_sanitize import provenance_badge_class as pbc
        self.assertEqual(pbc("something_new"), "igc-badge-danger")
        self.assertEqual(pbc(None), "igc-badge-danger")


class SanitizeRangesTests(unittest.TestCase):
    def test_ranges_slice_exactly_the_badges(self):
        from intergen.consent_dialog_sanitize import sanitize_with_ranges
        shown, ranges = sanitize_with_ranges("a‮b​c")
        self.assertEqual(len(ranges), 2)
        for a, b in ranges:                       # each range is a real 〈U+XXXX〉 badge
            self.assertTrue(shown[a:b].startswith("〈U+") and shown[a:b].endswith("〉"))

    def test_no_badges_means_no_ranges(self):
        from intergen.consent_dialog_sanitize import sanitize_with_ranges
        shown, ranges = sanitize_with_ranges("plain text\nhere")
        self.assertEqual(shown, "plain text\nhere")
        self.assertEqual(ranges, [])

    def test_wrapper_returns_only_shown(self):
        from intergen.consent_dialog_sanitize import (
            sanitize_for_display, sanitize_with_ranges)
        s = "x‮y"
        self.assertEqual(sanitize_for_display(s), sanitize_with_ranges(s)[0])


class HardeningSourceTests(unittest.TestCase):
    def _src(self, name: str) -> str:
        return (_REPO / "intergen" / name).read_text()

    def test_no_new_privs_runs_before_gtk_import_in_binary(self):
        # Fix 1: set in the fresh child before GTK, NOT via the daemon's fork-unsafe
        # preexec_fn.
        src = self._src("consent_dialog_gtk.py")
        self.assertIn("PR_SET_NO_NEW_PRIVS", src)
        call_at = src.find("_set_no_new_privs()")
        gi_at = src.find("\nimport gi")
        self.assertTrue(0 < call_at < gi_at, "no-new-privs must precede import gi")

    def test_daemon_no_longer_uses_preexec_fn(self):
        # the call-form argument must be gone (the word may remain in a comment)
        self.assertNotIn("preexec_fn=", self._src("consent_dialog.py"))

    def test_provenance_badge_is_not_hardcoded_green(self):
        src = self._src("consent_dialog_gtk.py")
        self.assertIn("provenance_badge_class(prov)", src)
        self.assertNotIn('["igc-badge", "igc-badge-direct"]', src)

    def test_initial_focus_rests_on_safe_widget(self):
        src = self._src("consent_dialog_gtk.py")
        self.assertIn("_safe_widget", src)
        self.assertIn("grab_focus()", src)

    def test_payload_badges_get_a_distinct_tag(self):
        src = self._src("consent_dialog_gtk.py")
        self.assertIn("sanitize_with_ranges", src)
        self.assertIn('create_tag("badge"', src)


# ── review risk-breakdown callout + session wording (operator design pass) ────
class RiskCopyTests(unittest.TestCase):
    def test_severity_keyed_to_provenance(self):
        from intergen.consent_dialog_sanitize import review_risk_copy as rrc
        self.assertEqual(rrc("user_direct", "read_only")[0], "ok")
        self.assertEqual(rrc("user_implied", "read_only")[0], "warn")
        self.assertEqual(rrc("ingress_derived", "read_only")[0], "danger")

    def test_unknown_and_empty_provenance_are_danger(self):
        from intergen.consent_dialog_sanitize import review_risk_copy as rrc
        self.assertEqual(rrc("something_new", "read_only")[0], "danger")
        self.assertEqual(rrc("", "read_only")[0], "danger")
        self.assertEqual(rrc(None, "read_only")[0], "danger")

    def test_copy_is_plain_language(self):
        # headline + detail must be present and read as words, not a raw token
        from intergen.consent_dialog_sanitize import review_risk_copy as rrc
        for prov in ("user_direct", "user_implied", "ingress_derived", "xyz"):
            _sev, head, detail = rrc(prov, "privileged_state_changing")
            self.assertTrue(head and detail)
            self.assertNotIn("_", head)  # no snake_case classification leaking through


class ReviewUxSourceTests(unittest.TestCase):
    def _src(self, name: str) -> str:
        return (_REPO / "intergen" / name).read_text()

    def test_review_renders_plain_language_risk_callout(self):
        src = self._src("consent_dialog_gtk.py")
        self.assertIn("review_risk_copy(", src)
        # the call must carry the computed class, not provenance alone
        self.assertIn("proto.K_RISK_TIER", src)

    def test_missing_provenance_defaults_to_unknown_not_green(self):
        src = self._src("consent_dialog_gtk.py")
        self.assertIn('or "unknown"', src)
        self.assertNotIn('or "user_direct"', src)

    def test_broad_grant_de_emphasized_on_risky_provenance(self):
        src = self._src("consent_dialog_gtk.py")
        self.assertIn("igc-allowconv-muted", src)
        self.assertIn('severity == "ok"', src)

    def test_session_wording_consistent_across_acceptance_modals(self):
        gtk = self._src("consent_dialog_gtk.py")
        self.assertIn('"Allow this session"', gtk)
        self.assertNotIn('"Allow this conversation"', gtk)
        web = (_REPO / "intergen" / "web" / "app.js").read_text()
        self.assertIn(">Allow this session<", web)
        self.assertNotIn(">Allow this conversation<", web)


if __name__ == "__main__":
    unittest.main()
