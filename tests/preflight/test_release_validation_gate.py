# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A release is not promoted, imaged or published without a green validation run.

WHY THIS EXISTS. The installed-system gate tier (tests/installed/) measures the
composition properties that a source-tree unit test cannot see — a permission
that depends on the real umask under a real HOME, a privilege boundary that
depends on the real hardened unit, a routing decision that depends on the real
embedding corpus and the real embedding server. The previous release shipped
with those properties broken and with every unit test passing, because nothing
in the pipeline required that tier to have been RUN.

An instrument nobody is required to use is a plan, not a gate. These tests are
the requirement: a promotion to master, an installation image, and a mirror
publish are each REFUSED unless a sealed run record exists for THAT candidate,
produced on a real installed machine, with zero failed gates and zero skips
nobody declared in advance.

THE ONE RULE EVERY TEST BELOW IS A RESTATEMENT OF: a missing or unreadable
record means NOT VALIDATED. It never means validated. There is no override
flag, because an override flag is how "just this once" becomes the pipeline.

WHAT IS DELIBERATELY NOT ASSERTED HERE. These tests do not run the installed
tier — it needs a real install and refuses to run anywhere else, which is the
point of it. The tier's own firing against a real R001.1 machine, and the
gate's refusal on that real red record, are proven separately and shipped as
captures with the cut. What is asserted here is the ENFORCEMENT logic, against
records this file writes, including the ones a careless or hostile pipeline
would produce.

THE SIGNATURE, added 2026-08-25. A seal makes a record tamper-EVIDENT: it
catches accident, drift and a partial write. It does not stand against anyone
who can write to the record directory, because they can re-seal it — and the
proof of that is in this repository's own history, where a synthetic green
record minted by hand passed this gate. A detached signature over the seal, made
by the release key on the operator's hardware token, is what turns the record
from a self-consistent artifact into the operator's attestation. Below, the
signature is checked with REAL crypto — an ephemeral key generated per run and
real gpgv over a real binary keyring. No mock verifier is used, because a mock
verifier proves that the code calls something, not that a signature holds.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "check-release-validation.py"
RUNNER = REPO_ROOT / "scripts" / "run-installed-gates.py"
EXPECTED_SKIPS = REPO_ROOT / "scripts" / "data" / "installed-gate-expected-skips.txt"

# Exit codes the gate promises. A refusal and a usage error must never share
# one, or a caller cannot tell "this release is not validated" from "I typed
# the command wrong" — and a pipeline that cannot tell them apart will
# eventually treat the second as the first.
EXIT_VALIDATED = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2

SEAL_NAME = "SHA256SUMS"
SIG_NAME = "SHA256SUMS.asc"

HAVE_GPG = shutil.which("gpg") is not None and shutil.which("gpgv") is not None


class _Signer:
    """An ephemeral OpenPGP key, its exported keyring, and a real signing call.

    Generated per test run in its own GNUPGHOME so nothing touches the operator's
    keyring or the hardware token. Two independent keys are made: one standing in
    for the release key, one standing in for any other key at all, so "signed by
    the wrong key" can be exercised as a real signature rather than as corrupt
    bytes — those are different failures and the gate must refuse both.
    """

    def __init__(self, home: Path, uid: str) -> None:
        self.home = home
        self.home.mkdir(parents=True, exist_ok=True)
        self.home.chmod(0o700)
        subprocess.run(
            ["gpg", "--homedir", str(self.home), "--batch", "--passphrase", "",
             "--quick-gen-key", uid, "ed25519", "sign", "0"],
            capture_output=True, text=True, check=True, timeout=120)
        colons = subprocess.run(
            ["gpg", "--homedir", str(self.home), "--list-keys", "--with-colons"],
            capture_output=True, text=True, check=True, timeout=60).stdout
        self.fingerprint = next(
            line.split(":")[9] for line in colons.splitlines()
            if line.startswith("fpr:"))
        self.keyring_bytes = subprocess.run(
            ["gpg", "--homedir", str(self.home), "--export"],
            capture_output=True, check=True, timeout=60).stdout

    def sign(self, target: Path, out: Path) -> None:
        """A real detached, armored signature over `target`."""
        if out.exists():
            out.unlink()
        subprocess.run(
            ["gpg", "--homedir", str(self.home), "--batch", "--passphrase", "",
             # -u pins this key explicitly: without it gpg consults a default
             # that may not be the key this signer represents.
             "-u", self.fingerprint, "--detach-sign", "--armor",
             "--output", str(out), str(target)],
            capture_output=True, text=True, check=True, timeout=120)


def _seal(record_dir: Path) -> None:
    """Write SHA256SUMS over every file in the record except itself.

    The signature sidecar is excluded as well, and cannot be otherwise: it signs
    SHA256SUMS, so it comes into being after SHA256SUMS is final and can never
    be listed inside it. The gate must apply the same exclusion or a signed
    record would fail its own seal check.
    """
    lines = []
    for p in sorted(record_dir.rglob("*")):
        if p.is_file() and p.name not in (SEAL_NAME, SIG_NAME):
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            lines.append(f"{digest}  {p.relative_to(record_dir)}\n")
    (record_dir / SEAL_NAME).write_text("".join(lines), encoding="utf-8")


def _good_record(root: Path, *, release: int = 190,
                 content_hash: str = "a2a3e70562acf0f4",
                 gates: list[dict] | None = None,
                 package_path: str = "/usr/lib/python3.13/site-packages/intergen",
                 hostname: str = "a-real-install") -> Path:
    """A record shaped exactly as the runner writes one, and green."""
    if gates is None:
        gates = [
            {"id": "tests/installed/test_gate_state_permissions.py::test_modes",
             "outcome": "passed", "reason": ""},
            {"id": "tests/installed/test_gate_privilege_boundary.py::test_unit",
             "outcome": "passed", "reason": ""},
        ]
    d = root / "record"
    d.mkdir(parents=True, exist_ok=True)
    outcome = {
        "collected": len(gates),
        "passed": sum(1 for g in gates if g["outcome"] == "passed"),
        "failed": sum(1 for g in gates if g["outcome"] == "failed"),
        "skipped": sum(1 for g in gates if g["outcome"] == "skipped"),
        "errors": sum(1 for g in gates if g["outcome"] == "error"),
    }
    (d / "pytest-output.txt").write_text(
        "the complete, untruncated pytest capture goes here\n", encoding="utf-8")
    (d / "record.json").write_text(json.dumps({
        "record_version": 1,
        "machine": {"hostname": hostname, "os_id": "intergenos",
                    "os_version_id": "R001.1", "kernel": "6.18.10"},
        "candidate": {"intergen_release": release,
                      "intergen_content_hash": content_hash,
                      "image_id": "intergenos", "image_build_id": "R001.1"},
        "installed_package_path": package_path,
        "invocation": {
            "argv": ["python3", "-m", "pytest", "-q", "tests/installed"],
            "env": {"INTERGENOS_INSTALLED_GATES": "1"},
            "cwd": "/var/lib/intergenos-validation"},
        "started_utc": "2026-08-24T19:00:00Z",
        "finished_utc": "2026-08-24T19:04:00Z",
        "outcome": outcome,
        "gates": gates,
        "capture": "pytest-output.txt",
    }, indent=2), encoding="utf-8")
    _seal(d)
    return d


def _run_gate(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    run_env = None
    if env is not None:
        run_env = dict(os.environ)
        run_env.update(env)
    return subprocess.run([sys.executable, str(GATE), *args],
                          capture_output=True, text=True, timeout=120,
                          env=run_env)


class _SignedRecordCase(unittest.TestCase):
    """Base: a scratch directory, an ephemeral release key, and a keyring.

    Every record built here is signed by `self.release_key` unless a test
    deliberately does otherwise. The gate is always pointed at this run's
    ephemeral fingerprint rather than the shipped pin, so these tests measure
    the VERIFICATION, not the pin. The pin itself is asserted separately, in
    ThePinIsInLockstep, which is the only place it may be asserted — a test that
    both chose the key and checked the pin would be checking its own arithmetic.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._keydir = Path(tempfile.mkdtemp(prefix="relval-keys-"))
        cls.release_key = _Signer(cls._keydir / "release", "relval-release@example.invalid")
        cls.other_key = _Signer(cls._keydir / "other", "relval-other@example.invalid")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._keydir, ignore_errors=True)

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="relval-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # BOTH keys are in the keyring on purpose. If the other key were absent,
        # a signature by it would fail at "no public key" and the FINGERPRINT
        # pin would never be exercised — the check would look proven while the
        # case it exists for went untested. With both present, a wrong-key
        # signature verifies cryptographically and can only be rejected by the
        # pin. The key-not-in-the-keyring case is asserted separately below.
        self.keyring = self.tmp / "trusted.gpg"
        self.keyring.write_bytes(
            self.release_key.keyring_bytes + self.other_key.keyring_bytes)
        self.keyring_release_only = self.tmp / "trusted-release-only.gpg"
        self.keyring_release_only.write_bytes(self.release_key.keyring_bytes)

    def _sign(self, record: Path, signer: "_Signer | None" = None) -> None:
        (signer or self.release_key).sign(record / SEAL_NAME, record / SIG_NAME)

    def _signed_record(self, **kw) -> Path:
        rec = _good_record(self.tmp, **kw)
        self._sign(rec)
        return rec

    def _args(self, record: Path, release: int = 190,
              content_hash: str = "a2a3e70562acf0f4") -> list[str]:
        return ["--record", str(record), "--candidate-release", str(release),
                "--candidate-content-hash", content_hash,
                "--keyring", str(self.keyring),
                "--fingerprint", self.release_key.fingerprint]


@unittest.skipUnless(HAVE_GPG, "gpg + gpgv required (real crypto, no mocks)")
class TheGateRefusesWhatIsNotValidated(_SignedRecordCase):

    # -- the control, first: a good record must PASS ----------------------
    def test_a_green_sealed_matching_record_validates(self) -> None:
        """Without this, every refusal below could be a gate that refuses
        everything, which proves nothing about what it detects."""
        rec = self._signed_record()
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_VALIDATED, r.stdout + r.stderr)

    # -- absence is never validation --------------------------------------
    def test_a_missing_record_is_refused(self) -> None:
        r = _run_gate(*self._args(self.tmp / "nothing-here"))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        self.assertIn("not validated", (r.stdout + r.stderr).lower())

    def test_the_refusal_does_not_share_an_exit_code_with_a_usage_error(self) -> None:
        """Probe BOTH states. A caller that cannot tell them apart will
        eventually read a typo as a verdict."""
        refusal = _run_gate(*self._args(self.tmp / "nothing-here"))
        usage = _run_gate("--record")  # missing its value
        self.assertEqual(refusal.returncode, EXIT_REFUSED)
        self.assertEqual(usage.returncode, EXIT_USAGE)
        self.assertNotEqual(refusal.returncode, usage.returncode)

    # -- the record has to be about THIS candidate ------------------------
    def test_a_record_for_another_release_is_refused(self) -> None:
        rec = self._signed_record(release=189)
        r = _run_gate(*self._args(rec, release=190))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        self.assertIn("189", r.stdout + r.stderr)

    def test_a_record_for_other_content_is_refused(self) -> None:
        """Same release number, different bytes — the case a rebuild creates."""
        rec = self._signed_record(content_hash="ffffffffffffffff")
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)

    # -- the record has to come from a real install -----------------------
    def test_a_record_produced_from_a_source_checkout_is_refused(self) -> None:
        rec = self._signed_record(
            package_path="/home/someone/intergenos/intergen")
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        self.assertIn("checkout", (r.stdout + r.stderr).lower())

    def test_a_record_from_an_unnamed_machine_is_refused(self) -> None:
        rec = self._signed_record(hostname="")
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)

    # -- outcomes ---------------------------------------------------------
    def test_a_record_with_a_failed_gate_is_refused(self) -> None:
        rec = self._signed_record(gates=[
            {"id": "tests/installed/test_gate_state_permissions.py::test_modes",
             "outcome": "failed", "reason": "0755 where 0700 was required"}])
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)

    def test_a_record_with_an_undeclared_skip_is_refused(self) -> None:
        """A skip is 'not measured'. Letting it pass is how a tier reports
        green on gates that never ran."""
        rec = self._signed_record(gates=[
            {"id": "tests/installed/test_gate_netfilter_smoke.py::test_chains",
             "outcome": "skipped", "reason": "no idea"}])
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        self.assertIn("skip", (r.stdout + r.stderr).lower())

    def test_a_record_that_collected_nothing_is_refused(self) -> None:
        """Zero gates is the most dangerous green of all: everything passed
        because nothing ran."""
        rec = self._signed_record(gates=[])
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)

    # -- the seal ---------------------------------------------------------
    def test_a_record_edited_after_sealing_is_refused(self) -> None:
        rec = self._signed_record()
        data = json.loads((rec / "record.json").read_text())
        data["outcome"]["failed"] = 0
        data["gates"] = [{"id": "x::y", "outcome": "passed", "reason": ""}]
        (rec / "record.json").write_text(json.dumps(data), encoding="utf-8")
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        self.assertIn("seal", (r.stdout + r.stderr).lower())

    def test_a_record_with_no_seal_at_all_is_refused(self) -> None:
        rec = self._signed_record()
        (rec / "SHA256SUMS").unlink()
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)

    def test_a_file_added_after_sealing_is_refused(self) -> None:
        """A seal that only checks the files it knows about does not notice a
        record growing a second capture that says something else."""
        rec = self._signed_record()
        (rec / "pytest-output-2.txt").write_text("a nicer story\n",
                                                 encoding="utf-8")
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)

    # -- there is no way around it ----------------------------------------
    def test_the_gate_has_no_override_flag(self) -> None:
        """Asserted twice: the flags are not accepted, and the source carries
        no such option. An override is how 'just this once' becomes policy."""
        for flag in ("--force", "--override", "--allow-missing",
                     "--skip-validation", "--no-verify"):
            with self.subTest(flag=flag):
                rec = self._signed_record()
                r = _run_gate(*self._args(rec), flag)
                self.assertEqual(r.returncode, EXIT_USAGE,
                                 f"{flag} was accepted: {r.stdout}{r.stderr}")
        source = GATE.read_text(encoding="utf-8")
        for word in ("--force", "--override", "--allow-missing",
                     "--skip-validation"):
            self.assertNotIn(f'"{word}"', source)
            self.assertNotIn(f"'{word}'", source)


@unittest.skipUnless(HAVE_GPG, "gpg + gpgv required (real crypto, no mocks)")
class TheExpectedSkipAllowlist(_SignedRecordCase):
    """A skip may pass only when somebody wrote down, in the repo, why."""

    def test_the_allowlist_file_exists_and_is_reviewable(self) -> None:
        self.assertTrue(EXPECTED_SKIPS.is_file(),
                        f"{EXPECTED_SKIPS} must exist — an absent allowlist "
                        f"must not mean 'anything may skip'")

    def test_a_skip_named_in_the_allowlist_validates(self) -> None:
        allow = self.tmp / "allow.txt"
        allow.write_text(
            "# id  reason\n"
            "tests/installed/test_gate_netfilter_smoke.py::test_chains"
            "  no dGPU on this validation machine\n", encoding="utf-8")
        rec = self._signed_record(gates=[
            {"id": "tests/installed/test_gate_state_permissions.py::test_modes",
             "outcome": "passed", "reason": ""},
            {"id": "tests/installed/test_gate_netfilter_smoke.py::test_chains",
             "outcome": "skipped", "reason": "no dGPU on this machine"}])
        r = _run_gate(*self._args(rec), "--expected-skips", str(allow))
        self.assertEqual(r.returncode, EXIT_VALIDATED, r.stdout + r.stderr)

    def test_an_unreadable_allowlist_refuses_rather_than_widens(self) -> None:
        """Fail closed on the security-critical input, the same posture the
        public-language gate takes toward its private term list."""
        rec = self._signed_record()
        r = _run_gate(*self._args(rec),
                      "--expected-skips", str(self.tmp / "absent.txt"))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)


@unittest.skipUnless(HAVE_GPG, "gpg + gpgv required (real crypto, no mocks)")
class TheRecordMustCarryTheOperatorsSignature(_SignedRecordCase):
    """A seal says the record did not change. A signature says who stands behind it.

    The distinction is not academic here. This gate was passed, in this
    repository, by a synthetic green record minted by hand — sealed, internally
    consistent, and about nothing. Anyone who can run the runner can do that
    again. Only a signature made by the release key, on the operator's hardware
    token, makes the record an attestation rather than an artifact.
    """

    def test_a_record_with_no_signature_is_refused_on_a_release_path(self) -> None:
        rec = _good_record(self.tmp)          # sealed, green, matching — unsigned
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        out = (r.stdout + r.stderr).lower()
        self.assertIn("signature", out,
                      "the refusal does not say a signature was missing, so the "
                      "operator cannot tell this apart from a failed gate")

    def test_a_signature_by_another_key_IN_the_keyring_is_refused_on_the_pin(self) -> None:
        """A real, valid signature — by the wrong key, which the keyring trusts.

        This is the case a naive check misses. The bytes verify, gpgv exits 0,
        and nothing but the primary-key fingerprint separates the release key
        from anyone else the keyring happens to carry. A keyring is a set of
        keys, not a statement about which one may sign a release.
        """
        rec = _good_record(self.tmp)
        self._sign(rec, self.other_key)
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        self.assertIn("fingerprint", (r.stdout + r.stderr).lower())
        self.assertIn(self.other_key.fingerprint.lower(),
                      (r.stdout + r.stderr).lower(),
                      "the refusal does not name the key that actually signed it")

    def test_a_signature_by_a_key_the_keyring_does_not_carry_is_refused(self) -> None:
        """The other refusal: not the wrong key, but an unknown one.

        Distinct from the case above and from a corrupt signature. gpgv cannot
        even check it, and that is a refusal rather than a pass.
        """
        rec = _good_record(self.tmp)
        self._sign(rec, self.other_key)
        args = ["--record", str(rec), "--candidate-release", "190",
                "--candidate-content-hash", "a2a3e70562acf0f4",
                "--keyring", str(self.keyring_release_only),
                "--fingerprint", self.release_key.fingerprint]
        r = _run_gate(*args)
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)

    def test_a_corrupt_signature_is_refused(self) -> None:
        rec = self._signed_record()
        sig = rec / SIG_NAME
        sig.write_text(sig.read_text().replace("A", "B"), encoding="utf-8")
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)

    def test_a_signature_over_a_different_seal_is_refused(self) -> None:
        """The signature must cover THIS record's seal, not merely be valid.

        A signature lifted from another record verifies perfectly against the
        file it was made for. Carried across, it must not authenticate a seal it
        never covered.
        """
        other = _good_record(self.tmp / "elsewhere", release=189)
        self.release_key.sign(other / SEAL_NAME, other / SIG_NAME)
        rec = _good_record(self.tmp)
        (rec / SIG_NAME).write_bytes((other / SIG_NAME).read_bytes())
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)

    def test_an_absent_keyring_refuses_rather_than_skips(self) -> None:
        """No keyring is no verification, and no verification is not validation."""
        rec = self._signed_record()
        self.keyring.unlink()
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        self.assertIn("keyring", (r.stdout + r.stderr).lower())

    def test_the_signature_file_does_not_break_the_seal(self) -> None:
        """The sidecar signs SHA256SUMS, so it can never be listed inside it.

        The seal check refuses on any file it does not cover. Without an explicit
        exclusion for the sidecar, signing a record would make it fail its own
        seal — a signed record refused for being signed.
        """
        rec = self._signed_record()
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_VALIDATED, r.stdout + r.stderr)

    def test_a_capture_shows_the_verification_before_the_refusal_it_preceded(self) -> None:
        """Order in a merged capture must match order in time.

        Refusals go to stderr (unbuffered); the verification note goes to stdout
        (block-buffered when redirected). Without an explicit flush the capture
        shows them reversed, and a reader reconstructing the run from the file
        concludes the gate verified a signature after it had already refused.
        Measured on a real capture during this cut, then fixed.
        """
        rec = self._signed_record(gates=[
            {"id": "tests/installed/test_x.py::test_y",
             "outcome": "failed", "reason": "it failed"}])
        merged = subprocess.run(
            [sys.executable, str(GATE), *self._args(rec)],
            capture_output=False, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, timeout=120)
        out = merged.stdout
        self.assertIn("signature verified", out)
        self.assertIn("REFUSED", out)
        self.assertLess(
            out.index("signature verified"), out.index("REFUSED"),
            "the capture shows the signature verification AFTER the refusal it "
            "actually preceded:\n" + out)

    # -- the test marker relaxes ABSENCE only, and never quietly ------------
    def test_unsigned_test_downgrades_a_MISSING_signature_to_a_loud_warning(self) -> None:
        rec = _good_record(self.tmp)
        r = _run_gate(*self._args(rec), env={"UNSIGNED_TEST": "1"})
        self.assertEqual(r.returncode, EXIT_VALIDATED, r.stdout + r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("UNSIGNED_TEST", out,
                      "the marker relaxed the check without saying so; a silent "
                      "downgrade is indistinguishable from a verified record")
        self.assertIn("WARN", out)

    def test_unsigned_test_does_NOT_excuse_a_signature_that_is_present_and_bad(self) -> None:
        """Present-but-wrong is never downgraded, in any mode.

        Mirrors the wiki-manifest gate's two-tier posture exactly. An absent
        signature can mean a development image that was never signed. A present
        one that does not verify means something is wrong with THIS record, and
        a test marker must not wave that through.
        """
        for label, signer in (("wrong key", self.other_key),):
            with self.subTest(case=label):
                rec = _good_record(self.tmp)
                self._sign(rec, signer)
                r = _run_gate(*self._args(rec), env={"UNSIGNED_TEST": "1"})
                self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        rec = self._signed_record()
        sig = rec / SIG_NAME
        sig.write_text(sig.read_text().replace("A", "B"), encoding="utf-8")
        r = _run_gate(*self._args(rec), env={"UNSIGNED_TEST": "1"})
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)


class ThePinIsInLockstep(unittest.TestCase):
    """The shipped default fingerprint is the project's, not a test's.

    Every signature test above pins an ephemeral key, which is what lets them
    measure verification rather than the pin. That leaves exactly one thing
    unasserted — that the DEFAULT the gate ships with is the real release key —
    and this is where it is asserted, against the pin the wiki-manifest gate
    already carries. Two gates disagreeing about the operator's key is a
    condition nobody would notice until a release.
    """

    def test_the_default_fingerprint_matches_the_wiki_manifest_gate(self) -> None:
        import re
        gate_src = GATE.read_text(encoding="utf-8")
        wiki_src = (REPO_ROOT / "scripts" / "check-wiki-manifest.py").read_text(
            encoding="utf-8")
        pat = r'OPERATOR_FINGERPRINT = "([0-9A-F]{40})"'
        mine = re.search(pat, gate_src)
        theirs = re.search(pat, wiki_src)
        self.assertIsNotNone(
            mine, "the release-validation gate carries no OPERATOR_FINGERPRINT pin")
        self.assertIsNotNone(
            theirs, "the wiki-manifest gate's pin could not be read, so this "
                    "comparison would have passed vacuously")
        self.assertEqual(
            mine.group(1), theirs.group(1),
            "the two gates pin different operator fingerprints")


class TheRunnerRefusesToProduceAMeaninglessRecord(unittest.TestCase):
    """The other half: a record that should never have been written."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="relval-runner-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_the_runner_exists_and_is_executable(self) -> None:
        self.assertTrue(RUNNER.is_file(), f"{RUNNER} is missing")

    def test_the_runner_refuses_to_run_from_a_source_checkout(self) -> None:
        """Run from inside this repo, which IS a checkout. A record produced
        here would describe the source tree, not the shipped system, and would
        then satisfy a gate about a release nobody installed."""
        r = subprocess.run([sys.executable, str(RUNNER),
                            "--output", str(self.tmp / "rec")],
                           capture_output=True, text=True, cwd=str(REPO_ROOT),
                           timeout=300)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("checkout", (r.stdout + r.stderr).lower())
        self.assertFalse((self.tmp / "rec").exists(),
                         "a refused run must leave no record behind")

    def test_the_runner_tells_the_operator_how_to_sign_the_record(self) -> None:
        """The record is not an attestation until it is signed, and the moment
        it is written is the moment that needs saying.

        Asserted against the runner's source rather than a live run, because the
        runner refuses to execute anywhere but a real installed machine — which
        is the property that makes it trustworthy and also the reason this cannot
        be a behavioural test here. Named as source-level in the delivery.
        """
        source = RUNNER.read_text(encoding="utf-8")
        self.assertIn("sign-with-gpg.sh", source,
                      "the runner never names the signing command, so an "
                      "operator who just produced a record is not told the one "
                      "thing that makes it usable")
        self.assertIn("SHA256SUMS.asc", source)
        self.assertIn("--sha256", source,
                      "the printed command does not pass the seal digest, so "
                      "the signing step would not check it is signing the bytes "
                      "that were just written")

    def test_the_runner_excludes_the_signature_from_the_seal(self) -> None:
        """The runner and the gate must agree, or a signed record fails its own
        seal — refused for being signed."""
        source = RUNNER.read_text(encoding="utf-8")
        self.assertIn("(SEAL_NAME, SIG_NAME)", source,
                      "the runner's seal does not exclude the signature sidecar")

    def test_the_runner_has_no_override_flag_either(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")
        for word in ("--force", "--override", "--allow-checkout",
                     "--skip-validation"):
            self.assertNotIn(f'"{word}"', source)
            self.assertNotIn(f"'{word}'", source)


if __name__ == "__main__":
    unittest.main()
