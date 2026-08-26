# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The stub gate reads what a package produced instead of guessing from its name.

THE DEFECT THIS CLOSES. Rule 21's gate answers "does anything in this tree
produce the path this surface cites?". When neither a verify_paths entry nor the
known-system set answers, it falls back to a guess: `is_citing_package_owned()`
rule (a) treats the claim as owned when the path's basename and the citing
package's name share a substring in either direction. So a surface owned by
package `foo` citing `/usr/bin/foo-does-not-exist` resolves, because "foo" is a
substring of "foo-does-not-exist". That is the exact shape the gate exists to
refuse, and it was left standing when lane 13 tightened the directory rule
(finding c1, 2026-08-24): the recommendation recorded there was to replace the
guess with a build-time record of the paths each package actually installs
rather than to tighten a heuristic.

Measured against the real tree at 12cb92f2c, read off the gate's own resolution
breakdown: 88 claimed paths scanned, 88 resolved, zero stubs — 43 as
system-path, 20 through this guess, the rest by a named verify_paths owner.
Those 20 are the claims the record replaces, and they come from 13 packages.

WHAT THE RECORD IS. The builder already writes a sidecar beside each recipe,
`auto-verify-paths.json`, holding the 2-3 identity-signal paths
`igos-build/verify_paths_derive.py` picks out of the install list. The record is
a second key in that same file, `produced_paths`, holding the WHOLE list rather
than a sample — an extension of the sidecar that exists, not a parallel file.

WHAT THE GATE DOES WITH IT. Where a citing package has a record, the record
answers the question and the guess is not consulted: a cited path in the record
resolves, and a cited path absent from it is a stub. Where a package has no
record the gate behaves exactly as it does today, so landing this changes no
verdict in the current tree — every one of those 21 claims keeps resolving until
a build produces the records. Rule (b), the path appearing literally in the
citing package's build.sh, is independent evidence and still resolves.

Every case below builds a probe tree and runs the real gate against it as a
subprocess, in the pattern tests/preflight/test_stub_gate_refuses_file_claims.py
established: the point is what the gate SAYS, not what a re-implementation of it
would say.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "check-aspirational-stubs.py"
SIDECAR_NAME = "auto-verify-paths.json"

# The probe package's name is a substring of the fabricated basename, which is
# precisely what makes the guess resolve it today.
PROBE_PKG = "zzqtool"
REAL_PATH = f"/usr/bin/{PROBE_PKG}"
FABRICATED = f"/usr/bin/{PROBE_PKG}-does-not-exist"
BUILD_SH_PATH = f"/etc/{PROBE_PKG}/staged.conf"


def _write_probe_package(root: Path, name: str, *, produced: list[str] | None,
                         cites: list[str], build_sh_installs: str | None = None,
                         tier: str = "core") -> Path:
    """One probe package: a recipe, a unit citing paths, and optionally a record."""
    pkg = root / "packages" / tier / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "package.yml").write_text(
        f"name: {name}\n"
        "version: 1.0\n"
        "release: 1\n"
        f"description: Probe package for the produced-paths record\n"
        "license: GPL-3.0-or-later\n"
        f"tier: {tier}\n"
        "build_style: manual\n"
        "\n"
        "verify_paths:\n"
        f"  - /usr/lib/systemd/system/{name}.service\n"
    )
    exec_line = cites[0]
    extra = "".join(f"ConditionPathExists={p}\n" for p in cites[1:])
    (pkg / f"{name}.service").write_text(
        "[Unit]\n"
        f"Description=Probe owned by {name}\n"
        f"{extra}"
        "\n"
        "[Service]\n"
        f"ExecStart={exec_line} --flag\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    if build_sh_installs:
        (pkg / "build.sh").write_text(
            "do_install() {\n"
            f'    install -d "$DESTDIR{build_sh_installs}"\n'
            "}\n"
        )
    if produced is not None:
        (pkg / SIDECAR_NAME).write_text(json.dumps({
            "auto_derived": True,
            "verify_paths": produced[:3],
            "produced_paths": produced,
            "comment": "probe record",
        }, indent=2, sort_keys=True) + "\n")
    return pkg


def _build_probe_tree(**kwargs) -> Path:
    root = Path(tempfile.mkdtemp(prefix="stub-gate-produced-"))
    hooks = root / "config" / "aspirational-stub-hook-allowlist.txt"
    hooks.parent.mkdir(parents=True, exist_ok=True)
    hooks.write_text("# no orphan-hook entries: this tree ships no hooks\n")
    _write_probe_package(root, PROBE_PKG, **kwargs)
    return root


def _run_gate(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), "--project", str(root), "--verbose", *extra],
        capture_output=True, text=True, check=False,
    )


class _GateOutput(unittest.TestCase):
    def _verdict_line(self, output: str, path: str) -> str:
        lines = [ln for ln in output.splitlines() if path in ln]
        self.assertTrue(lines, (
            f"the gate said nothing about {path}; it was never scanned. "
            "Output was:\n" + output))
        return lines[0]


class TheRecordAnswersInsteadOfTheGuess(_GateOutput):

    def test_a_path_absent_from_the_record_is_refused(self) -> None:
        """The defect. The package installs /usr/bin/zzqtool and nothing else;
        its unit cites /usr/bin/zzqtool-does-not-exist. The name is a substring
        of the basename, so the guess resolves it — the record must not."""
        root = _build_probe_tree(produced=[REAL_PATH], cites=[FABRICATED])
        try:
            r = _run_gate(root)
            out = r.stdout + r.stderr
            line = self._verdict_line(out, FABRICATED)
            self.assertIn("ASPIRATIONAL-STUB", line, (
                "a path the citing package's own build-time record does not "
                "contain resolved anyway:\n" + line))
            self.assertNotEqual(r.returncode, 0,
                                "the gate exited clean with a stub in the tree")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_a_path_in_the_record_resolves_and_says_so(self) -> None:
        root = _build_probe_tree(produced=[REAL_PATH], cites=[REAL_PATH])
        try:
            r = _run_gate(root)
            out = r.stdout + r.stderr
            line = self._verdict_line(out, REAL_PATH)
            self.assertIn("produced-path", line, (
                "a path the record contains did not resolve through the "
                "record:\n" + line))
            self.assertEqual(r.returncode, 0, out)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_build_sh_evidence_still_resolves_a_path_the_record_omits(self) -> None:
        """The record replaces the GUESS, not the evidence. A path the citing
        package's build.sh installs literally is evidence the package produces
        it, and a record that predates that line must not overrule it."""
        root = _build_probe_tree(produced=[REAL_PATH], cites=[BUILD_SH_PATH],
                                 build_sh_installs=BUILD_SH_PATH)
        try:
            r = _run_gate(root)
            out = r.stdout + r.stderr
            line = self._verdict_line(out, BUILD_SH_PATH)
            self.assertIn("citing-package", line, (
                "build.sh evidence stopped resolving once a record "
                "existed:\n" + line))
            self.assertEqual(r.returncode, 0, out)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class WithoutARecordNothingChanges(_GateOutput):
    """Landing this must not move a single verdict in the current tree, where
    no package has a record at all."""

    def test_the_guess_still_resolves_when_there_is_no_record(self) -> None:
        root = _build_probe_tree(produced=None, cites=[FABRICATED])
        try:
            r = _run_gate(root)
            out = r.stdout + r.stderr
            line = self._verdict_line(out, FABRICATED)
            self.assertIn("citing-package", line, (
                "a package with no record stopped resolving through the "
                "guess, which would change every verdict in the tree:\n"
                + line))
            self.assertEqual(r.returncode, 0, out)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_a_citing_package_without_a_record_is_named(self) -> None:
        """Reported, not silent: the gate says which citing packages have no
        record, so the coverage gap is visible before the flag makes it fatal."""
        root = _build_probe_tree(produced=None, cites=[FABRICATED])
        try:
            r = _run_gate(root)
            out = r.stdout + r.stderr
            self.assertIn("no produced-paths record", out, (
                "the gate did not name the citing package that has no "
                "record:\n" + out))
            self.assertIn(PROBE_PKG, out)
            self.assertEqual(r.returncode, 0, out)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_require_produced_paths_makes_the_gap_fatal(self) -> None:
        root = _build_probe_tree(produced=None, cites=[FABRICATED])
        try:
            r = _run_gate(root, "--require-produced-paths")
            out = r.stdout + r.stderr
            self.assertNotEqual(r.returncode, 0, (
                "--require-produced-paths did not refuse a citing package "
                "with no record:\n" + out))
            self.assertIn("no produced-paths record", out)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
