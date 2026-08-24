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
        # block, protects them.
        #
        # Two paths have left this list as the never-list grew, and each left for
        # the same reason — the manifest became stricter and the expectation here
        # did not follow. /etc/ssh/sshd_config is an exact entry in
        # identity_auth_privilege. /usr/lib/systemd/system/x.service is covered by
        # the system_binaries prefix /usr/lib/, added in r179; that case now lives
        # in test_system_binaries_never_list_prefixes_blocked below.
        #
        # /usr/share/ is deliberately what remains: it is a SENSITIVE_PREFIXES entry
        # and carries no never-list coverage at all, so it is the honest CONFIRM
        # case — sensitive enough to need the euid backstop, not so dangerous that
        # the signed manifest forbids it outright.
        for p in ("/usr/share/applications/x.desktop",
                  "/usr/share/dbus-1/services/x.service"):
            self.assertEqual(_classify_path(Path(p)), SafetyTier.CONFIRM, p)

    def test_system_binaries_never_list_prefixes_blocked(self):
        # r179 added the system_binaries category to the signed never-list, whose
        # prefixes cover the executable and library trees. A write under any of
        # them is BLOCKED in EVERY context — not CONFIRM, and not merely refused
        # by the euid backstop, which an unprivileged caller could otherwise read
        # as "allowed once I am root".
        for p in ("/usr/bin/ls",
                  "/usr/sbin/init",
                  "/usr/lib/systemd/system/x.service",
                  "/usr/lib/modules/x.ko",
                  "/usr/lib64/x.so"):
            self.assertEqual(_classify_path(Path(p)), SafetyTier.BLOCKED, p)

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
        # A never-listed path delegates to BLOCKED. /usr/lib/ is a system_binaries
        # prefix (r179), so this is the never-list speaking, not the interim floor.
        self.assertEqual(
            tool.classify_safety({"path": "/usr/lib/systemd/system/x.service", "content": "x"}),
            SafetyTier.BLOCKED,
        )
        # A sensitive-but-not-never-listed path still delegates to CONFIRM. This is
        # the case that proves the delegation has not simply collapsed to BLOCKED
        # for everything system-ish: /usr/share/ is sensitive and carries no
        # never-list coverage, so CONFIRM is the correct tier and the euid backstop
        # is what protects it.
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
        # refused rather than self-approved. The path must be sensitive AND NOT on
        # the never-list, or the hard block fires first and this asserts nothing
        # about the euid backstop at all — which is what happened while this case
        # used a systemd unit under /usr/lib/, a prefix r179 added to the
        # never-list. /usr/share/ is sensitive with no never-list coverage, so the
        # backstop is what refuses here.
        self.assertNotEqual(os.geteuid(), 0, "test assumes non-root runner")
        self.assertEqual(
            _classify_path(Path("/usr/share/applications/evil.desktop")),
            SafetyTier.CONFIRM,
            "this case is only meaningful while the path is NOT never-listed",
        )
        result = self.tool.execute(
            {"path": "/usr/share/applications/evil.desktop", "content": "x"}
        )
        self.assertFalse(result.success)
        self.assertIn("sensitive", result.content.lower())

    def test_never_listed_write_refused_in_every_context(self):
        # The stronger refusal, kept distinct from the one above. A never-listed
        # path is refused whether or not the caller is root.
        #
        # success=False on its own would prove nothing here: the runner is not
        # root, so a write to /usr/lib/ that got past every guard would still fail
        # on filesystem permissions, and the root-context case mocks geteuid
        # without actually gaining privilege — so it would fail the same way. Both
        # assertions therefore read the REFUSAL ITSELF. The block names the
        # never-list and says it holds in every context; the euid backstop's
        # message says "system-sensitive prefix" instead, so the two cannot be
        # confused, and a filesystem error would say neither.
        target = "/usr/lib/systemd/system/evil.service"
        result = self.tool.execute({"path": target, "content": "x"})
        self.assertFalse(result.success)
        self.assertIn("every context", result.content.lower(), result.content)

        with mock.patch("intergen.tools.write_file.os.geteuid", return_value=0):
            as_root = self.tool.execute({"path": target, "content": "x"})
        self.assertFalse(
            as_root.success,
            "a never-listed path was accepted in root context — the never-list is "
            "meant to hold in EVERY context, not only unprivileged ones",
        )
        self.assertIn("every context", as_root.content.lower(), as_root.content)

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
