# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Destructive-policy never-list path matcher.

Drives DestructivePolicy over a fixture manifest mirroring the ratified schema
(exact / prefix / ~-prefix / glob + match_rules) and asserts: exact, prefix
(incl. the bare-dir and no-false-prefix cases), the ~/.config/intergen/ decision
#5 AI-immutable config protection, glob, the symlink/.. bypass defense, that
fair-game paths are NOT protected, and fail-closed on an un-normalizable path.
"""

from __future__ import annotations

import os
import unittest

from intergen.destructive_policy import DestructivePolicy


_MANIFEST = {
    "manifest_version": 1,
    "match_rules": {
        "expand_user": True,
        "resolve_symlinks": True,
        "default_on_ambiguity": "block",
    },
    "categories": {
        "system_ai": {
            "exact": [],
            "prefix": ["~/.config/intergen/", "/var/lib/intergen/", "/var/log/intergen/"],
        },
        "boot_integrity": {
            "exact": ["/etc/mkinitcpio.conf"],
            "prefix": ["/boot/", "/efi/"],
        },
        "identity_auth_privilege": {
            "exact": ["/etc/passwd", "/etc/shadow", "/etc/sudoers"],
            "prefix": ["/etc/sudoers.d/", "/etc/pam.d/"],
            "glob": ["/etc/ssh/ssh_host_*"],
        },
    },
}


class DestructivePolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = DestructivePolicy.from_manifest(_MANIFEST)

    def test_exact_match(self):
        m = self.policy.is_protected("/etc/passwd")
        self.assertIsNotNone(m)
        self.assertEqual(m.category, "identity_auth_privilege")
        self.assertEqual(m.rule, "exact")

    def test_prefix_bare_dir_and_children(self):
        self.assertIsNotNone(self.policy.is_protected("/boot"))          # ==rstrip('/')
        self.assertIsNotNone(self.policy.is_protected("/boot/grub/grub.cfg"))  # startswith
        m = self.policy.is_protected("/boot/grub/grub.cfg")
        self.assertEqual(m.category, "boot_integrity")
        self.assertEqual(m.rule, "prefix")

    def test_prefix_no_false_match(self):
        # "/booty" must NOT match the "/boot/" prefix (trailing slash guards it).
        self.assertIsNone(self.policy.is_protected("/booty"))
        self.assertIsNone(self.policy.is_protected("/bootloader.txt"))

    def test_tilde_prefix_decision5_config(self):
        # ~/.config/intergen/ -> the AI-immutable Sentinel/destructive config.
        cfg = os.path.expanduser("~/.config/intergen/config.yml")
        m = self.policy.is_protected(cfg)
        self.assertIsNotNone(m, "decision #5 config path not protected")
        self.assertEqual(m.category, "system_ai")

    def test_glob_match(self):
        m = self.policy.is_protected("/etc/ssh/ssh_host_ed25519_key")
        self.assertIsNotNone(m)
        self.assertEqual(m.rule, "glob")

    def test_symlink_dotdot_bypass_defense(self):
        # A .. detour must not smuggle a write past the never-list: the candidate
        # is resolved before matching, so /etc/foo/../passwd -> /etc/passwd.
        m = self.policy.is_protected("/etc/foo/../passwd")
        self.assertIsNotNone(m, ".. detour bypassed the never-list")
        self.assertEqual(m.candidate, "/etc/passwd")

    def test_symlinked_parent_location_bypass_defense(self):
        # WC finding (2026-05-30): a PRE-EXISTING symlink in a protected
        # location's PARENT (Stow/chezmoi routinely symlink ~/.config) must not
        # bypass the never-list. The candidate is resolved through the symlink;
        # the pattern must be resolved the SAME way at construction, or the
        # resolved candidate no longer prefix-matches and the AI-immutable config
        # loses protection. Real on-FS symlink, not modeled.
        import shutil
        import tempfile
        root = tempfile.mkdtemp(prefix="dp_symparent_")
        prev_home = os.environ.get("HOME")
        try:
            home = os.path.join(root, "home")
            actual = os.path.join(root, "actual_config")
            os.makedirs(home)
            os.makedirs(os.path.join(actual, "intergen"))
            os.symlink(actual, os.path.join(home, ".config"))
            cfg = os.path.join(home, ".config", "intergen", "config.yml")
            os.environ["HOME"] = home
            # Construct AFTER HOME is set: patterns are expanduser+resolved here.
            policy = DestructivePolicy.from_manifest(_MANIFEST)
            m = policy.is_protected(cfg)
            self.assertIsNotNone(
                m, "symlinked ~/.config parent bypassed the AI-immutable guard")
            self.assertEqual(m.category, "system_ai")
            # No over-block: a sibling of the resolved dir stays free.
            self.assertIsNone(policy.is_protected(
                os.path.join(actual, "intergen-foo")))
        finally:
            if prev_home is not None:
                os.environ["HOME"] = prev_home
            else:
                os.environ.pop("HOME", None)
            shutil.rmtree(root, ignore_errors=True)

    def test_sudoers_dropin_prefix(self):
        m = self.policy.is_protected("/etc/sudoers.d/90-custom")
        self.assertIsNotNone(m)
        self.assertEqual(m.category, "identity_auth_privilege")

    def test_fair_game_not_protected(self):
        # General user files + general /var/log are fair game (only
        # /var/log/intergen/ is carved out).
        self.assertIsNone(self.policy.is_protected("/tmp/scratch.txt"))
        self.assertIsNone(self.policy.is_protected("/var/log/syslog"))
        self.assertIsNone(self.policy.is_protected(os.path.expanduser("~/notes.md")))

    def test_var_log_intergen_carveout_protected(self):
        m = self.policy.is_protected("/var/lib/intergen/memory.db")
        self.assertIsNotNone(m)
        self.assertEqual(m.category, "system_ai")

    def test_unresolvable_path_fails_closed(self):
        # A NUL byte makes Path.resolve raise -> ambiguity -> default block.
        m = self.policy.is_protected("/etc/\x00/passwd")
        self.assertIsNotNone(m, "un-normalizable path was not fail-closed")
        self.assertEqual(m.rule, "ambiguity-default")

    def test_ambiguity_can_be_configured_off(self):
        manifest = dict(_MANIFEST)
        manifest["match_rules"] = dict(_MANIFEST["match_rules"], default_on_ambiguity="allow")
        policy = DestructivePolicy.from_manifest(manifest)
        self.assertIsNone(policy.is_protected("/etc/\x00/passwd"))


_FPR = "5597A3E0587B253006D0DD7B8C50826182083050"


def _valid_status(primary_fpr=_FPR):
    # gpg --status-fd VALIDSIG line: subkey is field 3, primary key is the last.
    sub = "AAAA1111BBBB2222CCCC3333DDDD4444EEEE5555"
    return f"[GNUPG:] NEWSIG\n[GNUPG:] VALIDSIG {sub} 2026-05-30 0 4 0 1 8 00 {primary_fpr}\n"


class LoaderTests(unittest.TestCase):
    """Signature-verifying loader — fail-closed on any doubt (injected gpg)."""

    def setUp(self):
        import json
        import tempfile
        self._tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(_MANIFEST, self._tmp)
        self._tmp.close()
        self.manifest_path = self._tmp.name
        self.sig_path = self._tmp.name + ".asc"  # unread (gpg_verify injected)

    def tearDown(self):
        os.unlink(self.manifest_path)

    def _load(self, gpg_verify):
        from intergen.destructive_policy import load_verified_manifest
        return load_verified_manifest(
            self.manifest_path, self.sig_path, fingerprint=_FPR, gpg_verify=gpg_verify
        )

    def test_valid_signature_loads_manifest(self):
        data = self._load(lambda sig, data: (0, _valid_status()))
        self.assertIsNotNone(data)
        self.assertEqual(data["manifest_version"], 1)

    def test_nonzero_gpg_rc_fails_closed(self):
        self.assertIsNone(self._load(lambda sig, data: (1, "[GNUPG:] BADSIG\n")))

    def test_valid_but_wrong_key_fails_closed(self):
        wrong = "0000111122223333444455556666777788889999"
        self.assertIsNone(self._load(lambda sig, data: (0, _valid_status(wrong))))

    def test_no_validsig_line_fails_closed(self):
        self.assertIsNone(self._load(lambda sig, data: (0, "[GNUPG:] NODATA\n")))

    def test_gpg_not_found_fails_closed(self):
        def _raise(sig, data):
            raise FileNotFoundError("gpg")
        self.assertIsNone(self._load(_raise))

    def test_read_once_verifies_the_exact_bytes_parsed(self):
        # Read-once / no-TOCTOU: the bytes handed to gpg_verify must be the exact
        # file content that is then parsed — verified in-hand, not re-read.
        seen = {}

        def _capture(sig, data):
            seen["data"] = data
            return (0, _valid_status())

        result = self._load(_capture)
        self.assertIsNotNone(result)
        with open(self.manifest_path, "rb") as fh:
            self.assertEqual(seen["data"], fh.read())
        self.assertIsInstance(seen["data"], bytes)

    def test_fingerprint_match_is_case_and_space_insensitive(self):
        from intergen.destructive_policy import load_verified_manifest
        spaced = "5597 A3E0 587B 2530 06D0  DD7B 8C50 8261 8208 3050"
        data = load_verified_manifest(
            self.manifest_path, self.sig_path,
            fingerprint=spaced, gpg_verify=lambda sig, data: (0, _valid_status(_FPR.lower())),
        )
        self.assertIsNotNone(data)

    def test_load_policy_returns_matcher_on_valid_sig(self):
        from intergen.destructive_policy import load_policy
        policy = load_policy(
            self.manifest_path, self.sig_path, fingerprint=_FPR,
            gpg_verify=lambda sig, data: (0, _valid_status()),
        )
        self.assertIsNotNone(policy)
        self.assertIsNotNone(policy.is_protected("/etc/passwd"))

    def test_load_policy_none_on_bad_sig(self):
        from intergen.destructive_policy import load_policy
        self.assertIsNone(load_policy(
            self.manifest_path, self.sig_path, fingerprint=_FPR,
            gpg_verify=lambda sig, data: (2, ""),
        ))


class LoaderStatusTests(unittest.TestCase):
    """PI-D: load_policy_status distinguishes a benign ABSENT manifest (quiet
    floor fallback) from an UNTRUSTED one (present-but-unverifiable — tamper /
    corruption, must fail loud). The bare load_policy / load_verified_manifest
    None-contract is unchanged; this only adds the WHY."""

    def setUp(self):
        import json
        import tempfile
        self._tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(_MANIFEST, self._tmp)
        self._tmp.close()
        self.manifest_path = self._tmp.name
        self.sig_path = self._tmp.name + ".asc"

    def tearDown(self):
        if os.path.exists(self.manifest_path):
            os.unlink(self.manifest_path)

    def _status(self, gpg_verify, manifest_path=None):
        from intergen.destructive_policy import load_policy_status
        return load_policy_status(
            manifest_path or self.manifest_path, self.sig_path,
            fingerprint=_FPR, gpg_verify=gpg_verify,
        )

    def test_valid_sig_is_loaded(self):
        from intergen.destructive_policy import PolicyLoad
        policy, status = self._status(lambda sig, data: (0, _valid_status()))
        self.assertEqual(status, PolicyLoad.LOADED)
        self.assertIsNotNone(policy)
        self.assertIsNotNone(policy.is_protected("/etc/passwd"))

    def test_missing_manifest_is_absent_not_untrusted(self):
        # A genuinely-absent artifact is the benign floor fallback, NOT tamper.
        from intergen.destructive_policy import PolicyLoad
        os.unlink(self.manifest_path)
        policy, status = self._status(
            lambda sig, data: (0, _valid_status()),
            manifest_path=self.manifest_path)
        self.assertEqual(status, PolicyLoad.ABSENT)
        self.assertIsNone(policy)

    def test_bad_sig_is_untrusted(self):
        # PRESENT manifest + failed signature = tamper/corruption = UNTRUSTED.
        from intergen.destructive_policy import PolicyLoad
        policy, status = self._status(lambda sig, data: (1, "[GNUPG:] BADSIG\n"))
        self.assertEqual(status, PolicyLoad.UNTRUSTED)
        self.assertIsNone(policy)

    def test_wrong_key_is_untrusted(self):
        from intergen.destructive_policy import PolicyLoad
        wrong = "0000111122223333444455556666777788889999"
        _policy, status = self._status(lambda sig, data: (0, _valid_status(wrong)))
        self.assertEqual(status, PolicyLoad.UNTRUSTED)

    def test_gpg_cannot_run_is_untrusted(self):
        # A missing keyring / gpgv (verify raises) is tamper-class, not benign.
        from intergen.destructive_policy import PolicyLoad
        def _raise(sig, data):
            raise FileNotFoundError("gpgv")
        _policy, status = self._status(_raise)
        self.assertEqual(status, PolicyLoad.UNTRUSTED)


class ShippedManifestTests(unittest.TestCase):
    """Guards the SHIPPED signed manifest itself — the artifact load_policy() reads
    at runtime. These exercise the REAL in-tree files (not an injected fake), so a
    missing/malformed/mis-signed manifest, or one not wired to ship, is caught here
    rather than silently fail-closing to the floor on a real install (the disconnect
    that left the signed never-list inactive on master until 2026-05-30 night)."""

    def setUp(self):
        import os
        # intergen/tests/ -> intergen/ -> intergen/data/
        self._data = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        self._manifest = os.path.join(self._data, "destructive-policy-manifest.json")
        self._sig = self._manifest + ".asc"

    def test_shipped_manifest_and_signature_exist(self):
        import os
        self.assertTrue(os.path.isfile(self._manifest),
                        "destructive-policy-manifest.json missing from intergen/data/")
        self.assertTrue(os.path.isfile(self._sig),
                        "detached .asc signature missing from intergen/data/")

    def test_shipped_manifest_builds_a_real_policy(self):
        # The actual shipped bytes must parse into a working matcher with the
        # expected anti-self-tamper coverage — not just be present.
        import json
        import os
        with open(self._manifest, "rb") as fh:
            manifest = json.loads(fh.read())
        policy = DestructivePolicy.from_manifest(manifest)
        cfg = os.path.expanduser("~/.config/intergen/config.yml")
        self.assertIsNotNone(policy.is_protected(cfg),
                             "shipped manifest does not protect the AI-immutable config")
        # The identity/auth never-list MUST hard-block the auth stack — this is the
        # coverage that silently degraded to the interim floor before the PI-D fix
        # (the manifest failed to verify, so these fell through to CONFIRM). sshd_config
        # is an exact never-list entry; once the manifest verifies it is BLOCKED.
        # (2026-07-23 ratification: /etc/securetty and the bare /etc/sshd_config were
        # PRUNED — verified nonexistent on shipped systems; a never-list states facts.)
        for never in ("/etc/ssh/sshd_config", "/etc/login.defs"):
            self.assertIsNotNone(policy.is_protected(never),
                                 f"shipped manifest does not never-list {never}")
        # The 2026-07-23 extension entries — supply-chain trust config, network
        # trust, login/link persistence, kernel-hardening config — must hold too.
        for never in ("/etc/pkm/trusted.gpg", "/etc/pkm/repos.conf",
                      "/etc/nftables.conf", "/etc/ssl/certs/ca-certificates.crt",
                      "/etc/profile", "/etc/environment", "/etc/ld.so.conf",
                      "/etc/modprobe.d/disable-algif.conf"):
            self.assertIsNotNone(policy.is_protected(never),
                                 f"shipped manifest does not never-list {never}")
        # And the prunes must actually be gone (a phantom entry is a lie).
        for pruned in ("/etc/securetty", "/etc/sshd_config", "/etc/mkinitcpio.conf"):
            self.assertIsNone(policy.is_protected(pruned),
                              f"pruned entry {pruned} still on the shipped never-list")

    def test_shipped_manifest_install_wired(self):
        # The reader looks at /usr/share/intergen/; the ai/intergen package build.sh
        # MUST install the manifest there, or load_policy can only ever fail-close.
        import os
        build_sh = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "packages", "ai", "intergen", "build.sh")
        if not os.path.isfile(build_sh):
            self.skipTest("ai/intergen build.sh not present in this checkout")
        with open(build_sh) as fh:
            body = fh.read()
        self.assertIn("/usr/share/intergen/destructive-policy-manifest.json", body,
                      "ai/intergen build.sh does not install the destructive-policy "
                      "manifest to /usr/share/intergen/ — reader would always fail-close")


class DefaultVerifierTests(unittest.TestCase):
    """The REAL default verifier — guards the PI-D fix: on an installed system the
    manifest must verify against the SHIPPED master keyring via gpgv, not the empty
    per-user default keyring via `gpg --verify` (which failed NO_PUBKEY and silently
    degraded the never-list to the interim floor on every install)."""

    def test_default_verifier_uses_gpgv_against_shipped_keyring(self):
        # Regression guard: a switch back to `gpg --verify` (default keyring) is the
        # PI-D bug. The verifier MUST invoke gpgv with --keyring DEFAULT_KEYRING_PATH
        # and feed the in-hand bytes on stdin (read-once / no TOCTOU).
        import unittest.mock as mock
        from intergen import destructive_policy as dp
        captured = {}

        class _Proc:
            returncode = 0
            stdout = b"[GNUPG:] VALIDSIG x 1 1 0 4 0 1 10 00 " + _FPR.encode() + b"\n"

        def _fake_run(argv, **kwargs):
            captured["argv"] = argv
            captured["input"] = kwargs.get("input")
            return _Proc()

        with mock.patch.object(dp.subprocess, "run", _fake_run):
            rc, status = dp._default_gpg_verify("/some/sig.asc", b"MANIFEST-BYTES")
        self.assertEqual(rc, 0)
        self.assertEqual(captured["argv"][0], "gpgv")
        self.assertIn("--keyring", captured["argv"])
        self.assertIn(dp.DEFAULT_KEYRING_PATH, captured["argv"])
        self.assertEqual(captured["argv"][-1], "-")  # signed data on stdin
        self.assertEqual(captured["input"], b"MANIFEST-BYTES")

    def test_keyring_ship_wired(self):
        # The loader now depends on the shipped master keyring at DEFAULT_KEYRING_PATH;
        # the intergenos-keyring package MUST install it there, or load_policy can only
        # ever fail-close again (the PI-D shape).
        import os
        from intergen.destructive_policy import DEFAULT_KEYRING_PATH
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        build_sh = os.path.join(root, "packages", "core", "intergenos-keyring", "build.sh")
        if not os.path.isfile(build_sh):
            self.skipTest("intergenos-keyring build.sh not present in this checkout")
        with open(build_sh) as fh:
            body = fh.read()
        self.assertIn(DEFAULT_KEYRING_PATH, body,
                      "intergenos-keyring build.sh does not install the master keyring "
                      f"to {DEFAULT_KEYRING_PATH} — destructive-policy verify would fail-close")

    def test_live_install_manifest_verifies_end_to_end(self):
        # On an actual install (shipped keyring + manifest present), the REAL default
        # verifier path must establish a trusted policy — the end-to-end PI-D proof.
        # Skips off-install (CI / dev checkout without the shipped artifacts).
        import os
        from intergen.destructive_policy import (
            DEFAULT_KEYRING_PATH, DEFAULT_MANIFEST_PATH, DEFAULT_SIGNATURE_PATH,
            load_policy,
        )
        for p in (DEFAULT_KEYRING_PATH, DEFAULT_MANIFEST_PATH, DEFAULT_SIGNATURE_PATH):
            if not os.path.isfile(p):
                self.skipTest(f"not an install: {p} absent")
        policy = load_policy()
        self.assertIsNotNone(
            policy, "shipped manifest failed to verify against the shipped keyring — "
            "the never-list would silently degrade to the interim floor (PI-D)")


class ShippedManifestSystemBinariesTests(unittest.TestCase):
    """The SHIPPED manifest carries the system_binaries decision (2026-08-16).

    Unlike the fixture-driven classes above, this loads the real
    intergen/data/destructive-policy-manifest.json: on an installed system the
    binary directories are root-owned but writable (dm-verity seals only the
    live/install media), so their never-list entries are the barrier. A future
    manifest edit that drops them must fail here, not ship silently.
    """

    @classmethod
    def setUpClass(cls):
        import json
        from pathlib import Path
        shipped = (Path(__file__).resolve().parents[1]
                   / "data" / "destructive-policy-manifest.json")
        cls.policy = DestructivePolicy(json.loads(shipped.read_text()))

    def test_binary_dirs_are_protected(self):
        for path in ("/usr/bin/ls", "/usr/sbin/nft", "/usr/lib/libc.so.6",
                     "/bin/sh", "/usr/lib"):
            match = self.policy.is_protected(path)
            self.assertIsNotNone(match, path)
            self.assertEqual(match.category, "system_binaries", path)

    def test_no_overmatch_beyond_the_prefixes(self):
        # /usr/libexec and /usr/share are NOT in the category — the prefix
        # "/usr/lib/" must not swallow "/usr/libexec".
        self.assertIsNone(self.policy.is_protected("/usr/libexec/some-helper"))
        match = self.policy.is_protected("/usr/share/doc/readme")
        if match is not None:
            self.assertNotEqual(match.category, "system_binaries")


if __name__ == "__main__":
    unittest.main()
