#!/usr/bin/env python3
"""Tests for the SCOPED weak-digest verification path (operator decision 2).

helper-lib's igos_helper_verify_deb_via_signed_release grew an optional
7th arg `sha1_scoped_fpr` (steam helper ONLY): empty keeps step 2 exactly
plain `gpgv --keyring` for every existing caller; set, a STRICT attempt
that REJECTS SHA1 runs first and only the specific weak-digest failure
triggers a permissive retry gated on gpgv exit 0 + Good signature + the
pinned full-40-hex fingerprint, logged loudly via a SECURITY NOTICE.

These tests drive the REAL function end-to-end: a throwaway GPG keypair
signs a fake apt repo (InRelease + Packages + deb sha256 chain) served
over a local ephemeral-port HTTP server, and the bash function runs in a
subprocess against it.

Coverage:
  - SHA512-signed repo, no 7th arg  -> PASS (existing-caller regression)
  - SHA1-signed repo,  no 7th arg   -> REFUSE (the loosening does NOT
    leak to unscoped callers)
  - SHA1-signed repo, pinned signer -> ACCEPT with the SECURITY NOTICE
  - SHA512-signed repo, pinned      -> ACCEPT with NO notice (dormant)
  - SHA1-signed by an in-keyring but NON-pinned key -> REFUSE (pin holds)
  - malformed (short) pin           -> rc 2 misconfiguration
  - SHA1-signed then TAMPERED       -> REFUSE (retry still catches BAD)
"""

import functools
import hashlib
import http.server
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HELPER_LIB_SH = REPO_ROOT / "packages/core/intergenos-helper-lib/helper-lib.sh"

DEB_FILENAME = "steam-launcher_1.0.0.85_amd64.deb"
POOL_PATH = f"pool/steam/s/steam/{DEB_FILENAME}"
PACKAGES_RELPATH = "steam/binary-amd64/Packages"


def _have(cmd):
    return shutil.which(cmd) is not None


@unittest.skipUnless(
    HELPER_LIB_SH.is_file()
    and _have("gpg")
    and _have("gpgv")
    and _have("wget"),
    "weak-digest end-to-end tests need helper-lib.sh + gpg + gpgv + wget",
)
class WeakDigestScopedVerifyTests(unittest.TestCase):
    """End-to-end tests of the 7th-arg scoped weak-digest posture."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmpdir.name)
        cls.tmp = tmp

        # --- throwaway keys: A = the pinned signer, B = in-keyring
        # non-pinned (mirrors Valve's two-key keyring). ---
        cls.gnupghome = tmp / "gnupg"
        cls.gnupghome.mkdir(mode=0o700)
        cls.gpg_env = os.environ.copy()
        cls.gpg_env["GNUPGHOME"] = str(cls.gnupghome)
        for uid in ("keyA@igos.test", "keyB@igos.test"):
            subprocess.run(
                ["gpg", "--batch", "--quick-gen-key", "--passphrase", "",
                 uid, "rsa2048", "sign", "never"],
                env=cls.gpg_env, check=True, capture_output=True,
            )
        cls.fpr_a = cls._fingerprint("keyA@igos.test")
        cls.fpr_b = cls._fingerprint("keyB@igos.test")
        cls.keyring = tmp / "keyring.gpg"
        cls.keyring.write_bytes(
            subprocess.run(
                ["gpg", "--export", "keyA@igos.test", "keyB@igos.test"],
                env=cls.gpg_env, check=True, capture_output=True,
            ).stdout
        )

        # --- the fake .deb (provided locally to the function; only the
        # metadata chain is served over HTTP) ---
        cls.deb_path = tmp / DEB_FILENAME
        cls.deb_path.write_bytes(b"igos test deb payload\n")
        cls.deb_sha = hashlib.sha256(cls.deb_path.read_bytes()).hexdigest()

        # --- repo variants (each = its own served subtree) ---
        cls.www = tmp / "www"
        cls.www.mkdir()
        cls._build_repo("sha512-a", "keyA@igos.test", "SHA512")
        cls._build_repo("sha1-a", "keyA@igos.test", "SHA1")
        cls._build_repo("sha1-b", "keyB@igos.test", "SHA1")
        tampered = cls._build_repo("sha1-a-tampered", "keyA@igos.test", "SHA1")
        inrelease = tampered / "dists/stable/InRelease"
        inrelease.write_text(
            inrelease.read_text().replace("Suite: stable", "Suite: hacked")
        )

        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=str(cls.www)
        )
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        cls._server_thread = threading.Thread(
            target=cls.httpd.serve_forever, daemon=True
        )
        cls._server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls._tmpdir.cleanup()

    # ---- helpers ----

    @classmethod
    def _fingerprint(cls, uid):
        out = subprocess.run(
            ["gpg", "--with-colons", "--list-keys", uid],
            env=cls.gpg_env, check=True, capture_output=True, text=True,
        ).stdout
        return next(
            line.split(":")[9]
            for line in out.splitlines()
            if line.startswith("fpr:")
        )

    @classmethod
    def _build_repo(cls, name, signer_uid, digest_algo):
        """Write dists/stable/{InRelease,steam/binary-amd64/Packages}."""
        repo = cls.www / name
        pkgdir = repo / "dists/stable/steam/binary-amd64"
        pkgdir.mkdir(parents=True)
        packages = (
            f"Package: steam-launcher\n"
            f"Version: 1:1.0.0.85\n"
            f"Architecture: amd64\n"
            f"Filename: {POOL_PATH}\n"
            f"SHA256: {cls.deb_sha}\n"
            f"\n"
        )
        (pkgdir / "Packages").write_text(packages)
        pkg_sha = hashlib.sha256(packages.encode()).hexdigest()
        release = (
            f"Suite: stable\n"
            f"Codename: stable\n"
            f"SHA256:\n"
            f" {pkg_sha} {len(packages)} {PACKAGES_RELPATH}\n"
        )
        release_path = repo / "dists/stable/Release.plain"
        release_path.write_text(release)
        subprocess.run(
            ["gpg", "--batch", "--yes", "--local-user", signer_uid,
             "--digest-algo", digest_algo, "--allow-weak-digest-algos",
             "--clearsign", "--output",
             str(repo / "dists/stable/InRelease"), str(release_path)],
            env=cls.gpg_env, check=True, capture_output=True,
        )
        return repo

    def _verify(self, repo_name, *extra_args):
        """Invoke the real bash function; returns CompletedProcess."""
        base_url = f"http://127.0.0.1:{self.port}/{repo_name}"
        script = (
            f"source {HELPER_LIB_SH}\n"
            f'igos_helper_verify_deb_via_signed_release "$@"\n'
        )
        return subprocess.run(
            ["bash", "-c", script, "bash",
             DEB_FILENAME, str(self.deb_path), base_url,
             str(self.keyring), "stable", "steam", *extra_args],
            capture_output=True, text=True, timeout=60,
        )

    # ---- cases ----

    def test_sha512_unscoped_passes(self):
        proc = self._verify("sha512-a")
        self.assertEqual(
            proc.returncode, 0,
            f"strict verify of a SHA512-signed repo must pass: {proc.stderr}",
        )

    def test_sha1_unscoped_refuses(self):
        # HARDENED 2026-07-02 (authorized): the unscoped path
        # rejects SHA1 signature digests outright, no retry. History:
        # the pre-hardening path was plain gpgv, which rejects only MD5
        # — the review found the four existing callers were never
        # SHA1-strict, the operator authorized the hardening, and it was
        # proven behavior-identical against all four vendors' live
        # InRelease (all SHA256-signed) in the shipped chroot gpgv
        # before landing. The scoped 7th-arg path is now the ONLY
        # sanctioned weak-digest exception.
        proc = self._verify("sha1-a")
        self.assertEqual(
            proc.returncode, 1,
            f"the hardened unscoped path must refuse a SHA1-digest "
            f"signature: {proc.stderr}",
        )

    def test_sha1_scoped_pinned_signer_accepts_loudly(self):
        proc = self._verify("sha1-a", self.fpr_a)
        self.assertEqual(
            proc.returncode, 0,
            f"the scoped path must accept the pinned signer's SHA1 sig: "
            f"{proc.stderr}",
        )
        self.assertIn(
            "SECURITY NOTICE", proc.stderr,
            "the weak-digest acceptance must be logged loudly",
        )

    def test_sha512_scoped_stays_dormant(self):
        proc = self._verify("sha512-a", self.fpr_a)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn(
            "SECURITY NOTICE", proc.stderr,
            "a strong signature must take the strict path (no weak retry)",
        )

    def test_sha1_scoped_pin_mismatch_refuses(self):
        # Signed by key B (in the keyring!) but the pin names key A: the
        # fingerprint gate, not mere keyring membership, must decide.
        proc = self._verify("sha1-b", self.fpr_a)
        self.assertEqual(
            proc.returncode, 1,
            f"a SHA1 sig from a non-pinned (though in-keyring) key must "
            f"refuse: {proc.stderr}",
        )

    def test_malformed_pin_is_misconfiguration(self):
        proc = self._verify("sha512-a", "DEADBEEF")
        self.assertEqual(
            proc.returncode, 2,
            f"a non-40-hex pin must be rejected as caller misconfiguration: "
            f"{proc.stderr}",
        )

    def test_sha1_scoped_tampered_content_refuses(self):
        # Content modified after signing: the strict attempt classifies as
        # weak-digest (it cannot check a rejected-digest sig), and the
        # permissive retry must then catch the BAD signature.
        proc = self._verify("sha1-a-tampered", self.fpr_a)
        self.assertEqual(
            proc.returncode, 1,
            f"tampered signed content must refuse even through the scoped "
            f"retry: {proc.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
