"""check-wiki-manifest gate: shipped book == signed manifest, fail-closed.

Base-red coverage for every refusal shape the gate exists to produce, with
real crypto (an ephemeral test key + gpgv over a real binary keyring — no
mocked verification), plus the two sanctioned non-failure paths (clean PASS;
absent-wiki WARN under UNSIGNED_TEST=1).
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GATE = REPO_ROOT / "scripts" / "check-wiki-manifest.py"
GENERATOR = REPO_ROOT / "scripts" / "build-wiki-page-manifest.py"

HAVE_GPG = shutil.which("gpg") is not None and shutil.which("gpgv") is not None


def run_gate(root: Path, fingerprint: str, env_extra=None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("UNSIGNED_TEST", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["python3", str(GATE), "--root", str(root), "--fingerprint", fingerprint],
        capture_output=True, text=True, env=env, timeout=120)


@unittest.skipUnless(HAVE_GPG, "gpg + gpgv required (real-crypto tests, no mocks)")
class CheckWikiManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="wiki-gate-test.")
        base = Path(cls._tmp.name)
        cls.gnupghome = base / "gnupg"
        cls.gnupghome.mkdir(mode=0o700)
        subprocess.run(
            ["gpg", "--homedir", str(cls.gnupghome), "--batch", "--passphrase", "",
             "--quick-gen-key", "wiki-gate-test@example.invalid", "ed25519", "sign", "0"],
            check=True, capture_output=True, timeout=120)
        out = subprocess.run(
            ["gpg", "--homedir", str(cls.gnupghome), "--list-keys", "--with-colons"],
            check=True, capture_output=True, text=True, timeout=60)
        cls.fpr = next(l.split(":")[9] for l in out.stdout.splitlines()
                       if l.startswith("fpr:"))
        cls.keyring_bytes = subprocess.run(
            ["gpg", "--homedir", str(cls.gnupghome), "--export",
             "wiki-gate-test@example.invalid"],
            check=True, capture_output=True, timeout=60).stdout

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _build_root(self) -> Path:
        """A minimal built root: two rendered pages, generator-built manifest,
        real detached signature, the exported keyring at the shipped path."""
        root = Path(tempfile.mkdtemp(prefix="root.", dir=self._tmp.name))
        doc = root / "usr/share/doc/intergenos/wiki"
        doc.mkdir(parents=True)
        (doc / "index.html").write_text("<html>home</html>")
        (doc / "install").mkdir()
        (doc / "install" / "mok.html").write_text("<html>mok guide</html>")
        manifest = doc / "pages-manifest.json"
        subprocess.run(
            ["python3", str(GENERATOR), str(doc), str(manifest)],
            check=True, capture_output=True, timeout=60)
        subprocess.run(
            ["gpg", "--homedir", str(self.gnupghome), "--batch", "--passphrase", "",
             # -u pins the ephemeral key explicitly: without it, gpg consults an
             # inserted OpenPGP card (SCD SERIALNO) to derive the default signing
             # key, and the card's key is not in this homedir — so every signing
             # test fails whenever the operator's token is plugged in (measured
             # live 2026-08-06, Nitrokey inserted post-ceremony: 9 failed; with
             # -u and the card still inserted: all pass).
             "-u", self.fpr,
             "--detach-sign", "--armor", "--output", str(manifest) + ".asc",
             str(manifest)],
            check=True, capture_output=True, timeout=60)
        keyring = root / "etc/pkm"
        keyring.mkdir(parents=True)
        (keyring / "trusted.gpg").write_bytes(self.keyring_bytes)
        return root

    def test_clean_root_passes(self):
        root = self._build_root()
        proc = run_gate(root, self.fpr)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PASS", proc.stdout)

    def test_tampered_page_refused(self):
        root = self._build_root()
        page = root / "usr/share/doc/intergenos/wiki/index.html"
        page.write_text("<html>tampered</html>")
        proc = run_gate(root, self.fpr)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("does not match signed manifest", proc.stdout)

    def test_unlisted_page_refused(self):
        root = self._build_root()
        (root / "usr/share/doc/intergenos/wiki/extra.html").write_text("<html>drift</html>")
        proc = run_gate(root, self.fpr)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("NOT COVERED", proc.stdout)

    def test_missing_page_refused(self):
        root = self._build_root()
        (root / "usr/share/doc/intergenos/wiki/install/mok.html").unlink()
        proc = run_gate(root, self.fpr)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("MISSING from shipped book", proc.stdout)

    def test_corrupt_signature_refused_even_under_unsigned_test(self):
        root = self._build_root()
        sig = root / "usr/share/doc/intergenos/wiki/pages-manifest.json.asc"
        sig.write_text(sig.read_text().replace("A", "B", 3))
        for env in (None, {"UNSIGNED_TEST": "1"}):
            proc = run_gate(root, self.fpr, env_extra=env)
            self.assertEqual(proc.returncode, 1,
                             f"env={env}: {proc.stdout}{proc.stderr}")
            self.assertIn("signature verification failed", proc.stdout)

    def test_wrong_fingerprint_pin_refused(self):
        root = self._build_root()
        proc = run_gate(root, "0" * 40)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("!= pinned", proc.stdout)

    def test_absent_manifest_pair_fails_release_warns_dev(self):
        root = self._build_root()
        (root / "usr/share/doc/intergenos/wiki/pages-manifest.json.asc").unlink()
        proc = run_gate(root, self.fpr)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("pair incomplete", proc.stdout)
        proc = run_gate(root, self.fpr, env_extra={"UNSIGNED_TEST": "1"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("WARN", proc.stdout)

    def test_absent_doc_root_fails_release_warns_dev(self):
        root = self._build_root()
        shutil.rmtree(root / "usr/share/doc/intergenos/wiki")
        proc = run_gate(root, self.fpr)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        proc = run_gate(root, self.fpr, env_extra={"UNSIGNED_TEST": "1"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_missing_keyring_refused(self):
        root = self._build_root()
        (root / "etc/pkm/trusted.gpg").unlink()
        proc = run_gate(root, self.fpr)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("keyring absent", proc.stdout)

    def test_pin_lockstep_with_runtime(self):
        """The gate's pin must equal the runtime consumer's pin — gate-green
        must imply cite-green."""
        gate_src = GATE.read_text()
        runtime_src = (REPO_ROOT / "intergen" / "destructive_policy.py").read_text()
        import re
        gate_pin = re.search(r'OPERATOR_FINGERPRINT = "([0-9A-F]{40})"', gate_src).group(1)
        runtime_pin = re.search(r'OPERATOR_FINGERPRINT = "([0-9A-F]{40})"', runtime_src).group(1)
        self.assertEqual(gate_pin, runtime_pin)


if __name__ == "__main__":
    unittest.main()
