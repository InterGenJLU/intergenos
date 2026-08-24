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
"""

from __future__ import annotations

import hashlib
import json
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


def _seal(record_dir: Path) -> None:
    """Write SHA256SUMS over every file in the record except itself."""
    lines = []
    for p in sorted(record_dir.rglob("*")):
        if p.is_file() and p.name != "SHA256SUMS":
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            lines.append(f"{digest}  {p.relative_to(record_dir)}\n")
    (record_dir / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


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


def _run_gate(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GATE), *args],
                          capture_output=True, text=True, timeout=120)


class TheGateRefusesWhatIsNotValidated(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="relval-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _args(self, record: Path, release: int = 190,
              content_hash: str = "a2a3e70562acf0f4") -> list[str]:
        return ["--record", str(record), "--candidate-release", str(release),
                "--candidate-content-hash", content_hash]

    # -- the control, first: a good record must PASS ----------------------
    def test_a_green_sealed_matching_record_validates(self) -> None:
        """Without this, every refusal below could be a gate that refuses
        everything, which proves nothing about what it detects."""
        rec = _good_record(self.tmp)
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
        rec = _good_record(self.tmp, release=189)
        r = _run_gate(*self._args(rec, release=190))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        self.assertIn("189", r.stdout + r.stderr)

    def test_a_record_for_other_content_is_refused(self) -> None:
        """Same release number, different bytes — the case a rebuild creates."""
        rec = _good_record(self.tmp, content_hash="ffffffffffffffff")
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)

    # -- the record has to come from a real install -----------------------
    def test_a_record_produced_from_a_source_checkout_is_refused(self) -> None:
        rec = _good_record(
            self.tmp, package_path="/home/someone/intergenos/intergen")
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        self.assertIn("checkout", (r.stdout + r.stderr).lower())

    def test_a_record_from_an_unnamed_machine_is_refused(self) -> None:
        rec = _good_record(self.tmp, hostname="")
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)

    # -- outcomes ---------------------------------------------------------
    def test_a_record_with_a_failed_gate_is_refused(self) -> None:
        rec = _good_record(self.tmp, gates=[
            {"id": "tests/installed/test_gate_state_permissions.py::test_modes",
             "outcome": "failed", "reason": "0755 where 0700 was required"}])
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)

    def test_a_record_with_an_undeclared_skip_is_refused(self) -> None:
        """A skip is 'not measured'. Letting it pass is how a tier reports
        green on gates that never ran."""
        rec = _good_record(self.tmp, gates=[
            {"id": "tests/installed/test_gate_netfilter_smoke.py::test_chains",
             "outcome": "skipped", "reason": "no idea"}])
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        self.assertIn("skip", (r.stdout + r.stderr).lower())

    def test_a_record_that_collected_nothing_is_refused(self) -> None:
        """Zero gates is the most dangerous green of all: everything passed
        because nothing ran."""
        rec = _good_record(self.tmp, gates=[])
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)

    # -- the seal ---------------------------------------------------------
    def test_a_record_edited_after_sealing_is_refused(self) -> None:
        rec = _good_record(self.tmp)
        data = json.loads((rec / "record.json").read_text())
        data["outcome"]["failed"] = 0
        data["gates"] = [{"id": "x::y", "outcome": "passed", "reason": ""}]
        (rec / "record.json").write_text(json.dumps(data), encoding="utf-8")
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)
        self.assertIn("seal", (r.stdout + r.stderr).lower())

    def test_a_record_with_no_seal_at_all_is_refused(self) -> None:
        rec = _good_record(self.tmp)
        (rec / "SHA256SUMS").unlink()
        r = _run_gate(*self._args(rec))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)

    def test_a_file_added_after_sealing_is_refused(self) -> None:
        """A seal that only checks the files it knows about does not notice a
        record growing a second capture that says something else."""
        rec = _good_record(self.tmp)
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
                rec = _good_record(self.tmp)
                r = _run_gate(*self._args(rec), flag)
                self.assertEqual(r.returncode, EXIT_USAGE,
                                 f"{flag} was accepted: {r.stdout}{r.stderr}")
        source = GATE.read_text(encoding="utf-8")
        for word in ("--force", "--override", "--allow-missing",
                     "--skip-validation"):
            self.assertNotIn(f'"{word}"', source)
            self.assertNotIn(f"'{word}'", source)


class TheExpectedSkipAllowlist(unittest.TestCase):
    """A skip may pass only when somebody wrote down, in the repo, why."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="relval-skips-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

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
        rec = _good_record(self.tmp, gates=[
            {"id": "tests/installed/test_gate_state_permissions.py::test_modes",
             "outcome": "passed", "reason": ""},
            {"id": "tests/installed/test_gate_netfilter_smoke.py::test_chains",
             "outcome": "skipped", "reason": "no dGPU on this machine"}])
        r = _run_gate("--record", str(rec), "--candidate-release", "190",
                      "--candidate-content-hash", "a2a3e70562acf0f4",
                      "--expected-skips", str(allow))
        self.assertEqual(r.returncode, EXIT_VALIDATED, r.stdout + r.stderr)

    def test_an_unreadable_allowlist_refuses_rather_than_widens(self) -> None:
        """Fail closed on the security-critical input, the same posture the
        public-language gate takes toward its private term list."""
        rec = _good_record(self.tmp)
        r = _run_gate("--record", str(rec), "--candidate-release", "190",
                      "--candidate-content-hash", "a2a3e70562acf0f4",
                      "--expected-skips", str(self.tmp / "absent.txt"))
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stdout + r.stderr)


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

    def test_the_runner_has_no_override_flag_either(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")
        for word in ("--force", "--override", "--allow-checkout",
                     "--skip-validation"):
            self.assertNotIn(f'"{word}"', source)
            self.assertNotIn(f"'{word}'", source)


if __name__ == "__main__":
    unittest.main()
