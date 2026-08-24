# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""AI-5 (BLEND, decided 2026-05-30) — write_file sensitive-path guard.

Two composed layers over ONE shared policy (_classify_path), so the gate-side
tier and the root-side enforcement cannot diverge:

  - Gate-side: exact PROTECTED_PATHS and the danger-equivalent
    BLOCKED_PREFIXES (/etc/sudoers.d/, /boot/) classify BLOCKED even as root —
    closing the exact-7 narrowness (/etc/sudoers blocked but /etc/sudoers.d/evil
    was not; /boot/vmlinuz blocked but the bootloader config under /boot/ was
    not). SENSITIVE_PREFIXES stay CONFIRM (legit AI-assisted config-edit targets).

  - Root-side euid backstop: an unprivileged (euid != 0) write to any
    SENSITIVE_PREFIXES path is refused — a genuine sensitive write must reach
    execute() in root context via the reviewed privileged dispatch path (human
    modal + dispatch token).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen.tools.write_file import (
    WriteFileTool, _classify_path, _is_sensitive_prefix,
)
from intergen.interfaces.types import SafetyTier


class ClassificationTests(unittest.TestCase):
    def test_exact_protected_blocked(self):
        for p in ("/etc/shadow", "/etc/sudoers", "/boot/vmlinuz"):
            self.assertEqual(_classify_path(Path(p)), SafetyTier.BLOCKED, p)

    def test_danger_equivalent_prefixes_blocked(self):
        # AI-5 blend (gate-side half): danger-equivalent prefixes BLOCKED even though
        # not in the exact-7 — closes the /etc/sudoers.d + /boot/ narrowness.
        for p in ("/etc/sudoers.d/evil", "/boot/grub/grub.cfg", "/boot"):
            self.assertEqual(_classify_path(Path(p)), SafetyTier.BLOCKED, p)

    def test_sensitive_is_confirm_not_blocked(self):
        # Sensitive system paths that are NOT on the signed never-list stay CONFIRM
        # (legit AI-assisted edit targets); the root-side euid backstop, not a hard
        # block, protects them. (/etc/ssh/sshd_config is NO LONGER here: it is an
        # exact entry in the manifest's identity_auth_privilege never-list, so once
        # the signed manifest verifies — the PI-D fix — it is correctly BLOCKED, not
        # CONFIRM.)
        #
        # /usr/lib/systemd/system/x.service is NO LONGER here either, and for the
        # same reason: the signed manifest's system_binaries category carries the
        # prefix rule /usr/lib/, which covers it, so it is BLOCKED wherever that
        # manifest verifies. The claim in this comment that both remaining paths
        # were CONFIRM "in every environment" was true only where the manifest
        # could not be loaded — a build host — and false on every installed
        # system, which is where it matters. Measured 2026-08-24 on the R001.1
        # image: policy.is_protected() returns a system_binaries /usr/lib/ prefix
        # match for it and None for the .desktop path below.
        for p in ("/usr/share/applications/x.desktop",):
            self.assertEqual(_classify_path(Path(p)), SafetyTier.CONFIRM, p)

    def test_unit_file_is_blocked_by_the_signed_manifest(self):
        # The other half of the correction above, asserted rather than assumed:
        # where the signed manifest loads, its /usr/lib/ prefix blocks a unit
        # file. Where it cannot load, the defense-in-depth floor decides and this
        # path is CONFIRM — so the assertion is made against what the policy
        # itself says, not against a fixed tier.
        import intergen.tools.write_file as wf
        policy = wf._manifest_policy()
        path = "/usr/lib/systemd/system/x.service"
        if policy is None or policy.is_protected(path) is None:
            self.assertEqual(_classify_path(Path(path)), SafetyTier.CONFIRM, path)
        else:
            self.assertEqual(_classify_path(Path(path)), SafetyTier.BLOCKED, path)

    def test_ordinary_path_confirm(self):
        for p in ("/home/me/notes.txt", "/tmp/x"):
            self.assertEqual(_classify_path(Path(p)), SafetyTier.CONFIRM, p)

    def test_sensitive_prefix_detection(self):
        for p in ("/etc/ssh/sshd_config", "/usr/lib/systemd/system/x.service",
                  "/usr/lib/modules/z", "/usr/share/x"):
            self.assertTrue(_is_sensitive_prefix(Path(p)), p)
        for p in ("/home/me/notes.txt", "/tmp/x", "/var/tmp/y"):
            self.assertFalse(_is_sensitive_prefix(Path(p)), p)

    def test_classify_safety_delegates(self):
        tool = WriteFileTool()
        self.assertEqual(
            tool.classify_safety({"path": "/etc/shadow", "content": "x"}),
            SafetyTier.BLOCKED,
        )
        self.assertEqual(
            tool.classify_safety({"path": "/etc/sudoers.d/x", "content": "x"}),
            SafetyTier.BLOCKED,
        )
        # A sensitive-but-not-never-listed path delegates to CONFIRM (env-independent:
        # this path is not on the signed never-list, so it is CONFIRM whether or not
        # the manifest is loaded). sshd_config is intentionally NOT used here — it is
        # a never-list exact entry and is BLOCKED once the manifest verifies, and
        # neither is a unit file under /usr/lib/, which the manifest's
        # system_binaries prefix rule covers; both were measured 2026-08-24.
        self.assertEqual(
            tool.classify_safety({"path": "/usr/share/applications/x.desktop", "content": "x"}),
            SafetyTier.CONFIRM,
        )
        self.assertEqual(
            tool.classify_safety({"path": "", "content": "x"}),
            SafetyTier.BLOCKED,
        )


class AIImmutableConfigTests(unittest.TestCase):
    """Decision #5 / manifest system_ai — InterGen's own config + state is
    AI-immutable: the AI write path is BLOCKED, mirroring the signed manifest."""

    def test_user_config_override_is_blocked(self):
        # The window the review flagged: the user-override config supersedes the
        # system one and was previously only CONFIRM — now BLOCKED.
        p = Path(os.path.expanduser("~/.config/intergen/config.yml"))
        self.assertEqual(_classify_path(p), SafetyTier.BLOCKED)

    def test_ai_state_dirs_blocked(self):
        for p in ("/var/lib/intergen/memory.db", "/var/log/intergen/tool-dispatch.jsonl",
                  os.path.expanduser("~/.config/intergen/dispatch-key")):
            self.assertEqual(_classify_path(Path(p)), SafetyTier.BLOCKED, p)

    def test_system_intergen_stays_confirm_per_manifest(self):
        # /etc/intergen/ is NOT in the signed manifest's system_ai (it relies on
        # the /etc/ sensitive-prefix + root-side euid backstop) — must stay CONFIRM,
        # not over-blocked.
        self.assertEqual(
            _classify_path(Path("/etc/intergen/config.yml")), SafetyTier.CONFIRM
        )

    def test_other_user_config_not_overblocked(self):
        # Only intergen's own config is immutable — a sibling app's config is not.
        p = Path(os.path.expanduser("~/.config/other-app/settings.yml"))
        self.assertEqual(_classify_path(p), SafetyTier.CONFIRM)

    def test_classify_safety_blocks_user_config_end_to_end(self):
        tool = WriteFileTool()
        self.assertEqual(
            tool.classify_safety(
                {"path": os.path.expanduser("~/.config/intergen/config.yml"),
                 "content": "sentinel:\n  scan:\n    mcp: off\n"}
            ),
            SafetyTier.BLOCKED,
        )


class ExecuteGuardTests(unittest.TestCase):
    def setUp(self):
        self.tool = WriteFileTool()

    def test_protected_blocked(self):
        result = self.tool.execute({"path": "/etc/shadow", "content": "x"})
        self.assertFalse(result.success)
        self.assertIn("protected", result.content.lower())

    def test_danger_equivalent_prefix_blocked(self):
        # Blocked in EVERY context — the message names the danger-equivalent path.
        result = self.tool.execute(
            {"path": "/etc/sudoers.d/evil", "content": "ALL ALL=(ALL) NOPASSWD:ALL"}
        )
        self.assertFalse(result.success)
        self.assertIn("danger-equivalent", result.content.lower())

    def test_unprivileged_sensitive_write_refused(self):
        # Tests run unprivileged (euid != 0); a sensitive-prefix write must be
        # refused rather than self-approved. This needs a path that is sensitive
        # but NOT on the signed never-list, so the euid backstop on the CONFIRM
        # tier is what refuses it. A systemd unit under /usr/lib/ used to be that
        # path and no longer is: the signed manifest's system_binaries prefix
        # rule covers /usr/lib/, so on any system where that manifest verifies
        # the write is hard-BLOCKED instead — a different, stronger refusal that
        # this test is not about, and one whose message says "blocked", not
        # "sensitive". A .desktop file under /usr/share is sensitive by prefix
        # and matches no never-list rule (both measured 2026-08-24).
        self.assertNotEqual(os.geteuid(), 0, "test assumes non-root runner")
        result = self.tool.execute(
            {"path": "/usr/share/applications/evil.desktop", "content": "x"}
        )
        self.assertFalse(result.success)
        self.assertIn("sensitive", result.content.lower())

    def test_root_context_sensitive_write_allowed(self):
        # In root context (the reviewed privileged path) the euid backstop does
        # not fire — the write proceeds. Mock geteuid to 0 and _is_sensitive_prefix
        # to True over a temp target so we observe the write without touching /etc.
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "sshd_config"
            with mock.patch("intergen.tools.write_file.os.geteuid", return_value=0), \
                 mock.patch(
                     "intergen.tools.write_file._is_sensitive_prefix",
                     return_value=True,
                 ):
                result = self.tool.execute(
                    {"path": str(target), "content": "ok\n"}
                )
            self.assertTrue(result.success, result.content)
            self.assertEqual(target.read_text(), "ok\n")

    def test_normal_user_path_writes(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "notes.txt"
            result = self.tool.execute({"path": str(target), "content": "hi\n"})
            self.assertTrue(result.success, result.content)
            self.assertEqual(target.read_text(), "hi\n")


class ManifestEnforcementTests(unittest.TestCase):
    """The signed-manifest never-list wired into _classify_path (PRIMARY) plus the
    canonicalized interim floor (defense-in-depth when the manifest is absent)."""

    def setUp(self):
        import intergen.tools.write_file as wf
        self._wf = wf
        self._saved = (wf._policy_cache, wf._policy_loaded, wf._policy_untrusted)

    def tearDown(self):
        (self._wf._policy_cache, self._wf._policy_loaded,
         self._wf._policy_untrusted) = self._saved

    def _set_policy(self, policy):
        self._wf._policy_cache = policy
        self._wf._policy_loaded = True   # skip the real signed-manifest load

    def test_manifest_protected_path_blocked(self):
        class _FakePolicy:
            manifest_version = 1
            def is_protected(self, p):
                return object() if p == "/usr/share/intergen/x.json" else None
        self._set_policy(_FakePolicy())
        self.assertEqual(
            _classify_path(Path("/usr/share/intergen/x.json")), SafetyTier.BLOCKED)
        # A path the manifest does NOT protect (and not on the floor) stays CONFIRM.
        self.assertEqual(_classify_path(Path("/srv/data/x")), SafetyTier.CONFIRM)

    def test_floor_holds_when_manifest_absent(self):
        # load_policy returned None -> matcher absent -> the interim floor must
        # still BLOCK InterGen's own AI-immutable config/state (fail-closed).
        self._set_policy(None)
        for p in (os.path.expanduser("~/.config/intergen/config.yml"),
                  "/var/lib/intergen/memory.db", "/var/log/intergen/events.jsonl"):
            self.assertEqual(_classify_path(Path(p)), SafetyTier.BLOCKED, p)
        self.assertEqual(
            _classify_path(Path("/home/u/notes.txt")), SafetyTier.CONFIRM)

    def test_manifest_is_consulted(self):
        seen = []
        class _RecPolicy:
            def is_protected(self, p):
                seen.append(p)
                return None
        self._set_policy(_RecPolicy())
        _classify_path(Path("/srv/data/y"))
        self.assertEqual(seen, ["/srv/data/y"])


class UntrustedBannerTests(unittest.TestCase):
    """PI-D hardening — a write that proceeds while the never-list is PRESENT but
    UNTRUSTED (tamper / corruption) must carry the loud user-visible banner; a
    write under a LOADED or benign-ABSENT never-list must NOT."""

    def setUp(self):
        import intergen.tools.write_file as wf
        self._wf = wf
        self._saved = (wf._policy_cache, wf._policy_loaded, wf._policy_untrusted)
        self.tool = WriteFileTool()

    def tearDown(self):
        (self._wf._policy_cache, self._wf._policy_loaded,
         self._wf._policy_untrusted) = self._saved

    def _force(self, *, untrusted):
        # Pin the cached load state so execute() does not hit the real manifest.
        self._wf._policy_cache = None
        self._wf._policy_loaded = True
        self._wf._policy_untrusted = untrusted

    def test_banner_present_when_never_list_untrusted(self):
        self._force(untrusted=True)
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "notes.txt"
            result = self.tool.execute({"path": str(target), "content": "hi\n"})
            self.assertTrue(result.success, result.content)
            self.assertIn("SECURITY ALERT", result.content)
            self.assertEqual(target.read_text(), "hi\n")  # write still proceeds

    def test_no_banner_when_never_list_trusted(self):
        self._force(untrusted=False)
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "notes.txt"
            result = self.tool.execute({"path": str(target), "content": "hi\n"})
            self.assertTrue(result.success, result.content)
            self.assertNotIn("SECURITY ALERT", result.content)


class CanonPrefixTests(unittest.TestCase):
    def test_home_anchor_resolves_through_symlinked_parent(self):
        # The interim floor's home anchor must canonicalize like the candidate, so a
        # Stow/chezmoi-symlinked ~/.config does not let the floor diverge (WC carry-
        # forward + the symmetry note). Real on-FS symlink.
        import shutil
        import tempfile
        from intergen.tools.write_file import _canon_prefix
        root = tempfile.mkdtemp(prefix="wf_canon_")
        prev = os.environ.get("HOME")
        try:
            home = os.path.join(root, "home")
            actual = os.path.join(root, "actual")
            os.makedirs(home)
            os.makedirs(actual)
            os.symlink(actual, os.path.join(home, ".config"))
            os.environ["HOME"] = home
            canon = _canon_prefix("~/.config/intergen/")
            # Resolved THROUGH the symlink to the real target; trailing slash kept.
            self.assertTrue(
                canon.startswith(os.path.join(actual, "intergen")), canon)
            self.assertTrue(canon.endswith("/"), canon)
        finally:
            if prev is not None:
                os.environ["HOME"] = prev
            else:
                os.environ.pop("HOME", None)
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
