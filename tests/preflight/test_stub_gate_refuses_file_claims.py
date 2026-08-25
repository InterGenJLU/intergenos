# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The stub gate must be able to refuse an invented path in THIS repository.

THE DEFECT, measured by planting probes and reading what the gate said. Rule 21
gives scripts/check-aspirational-stubs.py one job: refuse a reference to a path
nothing in the tree produces. Against the real repository it could not do that
job for two whole classes of path, and reported them RESOLVED.

  * A file claim under /usr/bin, /usr/sbin, /usr/lib or /lib64. The gate treats
    every verify_paths entry as a directory that owns everything beneath it, and
    the base-files package legitimately declares the merged-usr compat
    directories /bin, /lib, /sbin and /lib64. Through the UsrMerge sibling rule
    those four entries own the whole of /usr/bin, /usr/sbin, /usr/lib and
    /lib64 — so an invented binary under any of them resolved as
    "base-files (parent dir)".
  * Any claim under /opt. /opt is a blanket known-system prefix, so an invented
    path under a vendor directory resolved as "system-path".

Those are the two places a fabricated path is most likely to be written, which
is what makes this the gate failing at its own purpose rather than a gap at the
edges.

WHY THE EXISTING FIXTURE SUITE DID NOT CATCH IT. tests/check-aspirational-stubs
runs the gate against synthetic trees that contain no base-files package, so the
compat-directory entries that cause the resolution are not there and its
should-fail fixture refuses exactly as it should. A gate can pass its own
fixtures and still be unable to refuse anything in the repository it guards.
These cases build their probe tree from the REAL package declarations, so they
measure the resolution the repository actually performs.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "check-aspirational-stubs.py"

# Names chosen so nothing resolves by accident: the gate also treats a claim as
# owned when the citing package's name and the path's basename share a
# substring, so the probe package's name and these basenames have no letters in
# common beyond the unavoidable.
PROBE_PKG = "stubprobe"
INVENTED_BINARY = "/usr/bin/zzq-invented-tool"
INVENTED_OPT = "/opt/zzqvendor/zzq-invented-helper"
REAL_BINARY = "/usr/bin/pkm"          # declared in packages/core/pkm/package.yml
REAL_COMPAT_DIR = "/bin"              # declared as a directory by base-files


def _copy_real_package(root: Path, tier: str, name: str) -> None:
    """Copy one real package.yml into the probe tree.

    The point of the probe tree is that the DECLARATIONS are the repository's
    own. Copying rather than paraphrasing means a change to what base-files
    declares reaches these cases instead of leaving them measuring a fiction.
    """
    src = REPO_ROOT / "packages" / tier / name / "package.yml"
    if not src.exists():  # pragma: no cover — guarded by test_the_probe_tree_is_real
        return
    dst = root / "packages" / tier / name / "package.yml"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def _build_probe_tree() -> Path:
    """A project root carrying the real declarations plus planted claims."""
    root = Path(tempfile.mkdtemp(prefix="stub-gate-probe-"))
    # The two real packages whose declarations decide these cases: base-files
    # declares the merged-usr compat directories, pkm declares a real binary.
    _copy_real_package(root, "core", "intergenos-base-files")
    _copy_real_package(root, "core", "pkm")
    # The gate refuses to run without its hook allowlist; an empty one keeps
    # these cases about path resolution and nothing else.
    hooks = root / "config" / "aspirational-stub-hook-allowlist.txt"
    hooks.parent.mkdir(parents=True, exist_ok=True)
    hooks.write_text("# no orphan-hook entries: this tree ships no hooks\n")

    pkg = root / "packages" / "test" / PROBE_PKG
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "package.yml").write_text(
        f"name: {PROBE_PKG}\n"
        "version: 1.0\n"
        "release: 1\n"
        "description: Probe package for the Rule 21 gate\n"
        "license: GPL-3.0-or-later\n"
        "tier: core\n"
        "build_style: manual\n"
        "\n"
        "verify_paths:\n"
        f"  - /usr/lib/systemd/system/{PROBE_PKG}.service\n"
    )
    # PROBE 1 — a systemd unit citing an invented binary under /usr/bin.
    (pkg / f"{PROBE_PKG}.service").write_text(
        "[Unit]\n"
        "Description=Probe citing a binary nothing in this tree produces\n"
        "\n"
        "[Service]\n"
        f"ExecStart={INVENTED_BINARY} --flag\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    # PROBE 2 — a desktop entry citing an invented path under /opt.
    (pkg / f"{PROBE_PKG}.desktop").write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Probe\n"
        f"Exec={INVENTED_OPT} --flag\n"
    )
    # CONTROLS — a real declared binary and a real declared directory, cited
    # from the same package so the only difference is the path itself.
    (pkg / f"{PROBE_PKG}-control.service").write_text(
        "[Unit]\n"
        "Description=Control citing paths the tree really declares\n"
        "\n"
        "[Service]\n"
        f"ExecStart={REAL_BINARY} --version\n"
        f"ConditionPathExists={REAL_COMPAT_DIR}\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    return root


def _run_gate(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), "--project", str(root), "--verbose"],
        capture_output=True, text=True, check=False,
    )


class _ProbeTree(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = _build_probe_tree()
        cls.result = _run_gate(cls.root)
        cls.output = cls.result.stdout + cls.result.stderr

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def _verdict_line(self, path: str) -> str:
        """The gate's own line about one claimed path, whatever it said."""
        lines = [ln for ln in self.output.splitlines() if path in ln]
        self.assertTrue(lines, (
            f"the gate said nothing at all about {path}; it was never scanned. "
            "Output was:\n" + self.output))
        return lines[0]


class TheProbeTreeIsReal(_ProbeTree):
    """Controls on the measurement itself. If these fail, every case below is
    measuring a tree that does not resemble the repository."""

    def test_the_real_declarations_were_copied_in(self) -> None:
        for tier, name in (("core", "intergenos-base-files"), ("core", "pkm")):
            self.assertTrue(
                (self.root / "packages" / tier / name / "package.yml").exists(),
                f"{name}/package.yml is missing from the probe tree, so the "
                "resolution this file measures is not the repository's")

    def test_base_files_still_declares_the_compat_directories(self) -> None:
        declared = (self.root / "packages" / "core" / "intergenos-base-files"
                    / "package.yml").read_text()
        for compat in ("/bin", "/lib", "/sbin", "/lib64"):
            self.assertIn(f"- {compat}\n", declared, (
                f"base-files no longer declares {compat}; the resolution these "
                "cases exist for may have moved, so re-measure before trusting "
                "a pass here"))

    def test_the_real_binary_is_still_declared(self) -> None:
        declared = (self.root / "packages" / "core" / "pkm"
                    / "package.yml").read_text()
        self.assertIn(f"- {REAL_BINARY}\n", declared,
                      "the control path is no longer declared by its package")


class AnInventedFileClaimIsRefused(_ProbeTree):
    """The class the gate exists for, in the place it is most likely to occur."""

    def test_an_invented_usr_bin_binary_is_flagged(self) -> None:
        line = self._verdict_line(INVENTED_BINARY)
        self.assertIn("ASPIRATIONAL-STUB", line, (
            "the gate resolved a binary nothing in this tree produces. Its "
            f"verdict was: {line.strip()}"))

    def test_an_invented_opt_path_is_flagged(self) -> None:
        line = self._verdict_line(INVENTED_OPT)
        self.assertIn("ASPIRATIONAL-STUB", line, (
            "the gate resolved a path under /opt that nothing in this tree "
            f"produces. Its verdict was: {line.strip()}"))

    def test_the_gate_exits_one_when_it_finds_them(self) -> None:
        self.assertEqual(self.result.returncode, 1, (
            "exit 1 is findings; exit 2 is a refusal to run, which reports "
            "nothing about the content. Output was:\n" + self.output))


class ARealClaimStillResolves(_ProbeTree):
    """The control on the fix. A gate that refuses everything is no more use
    than one that refuses nothing."""

    def test_a_declared_binary_resolves(self) -> None:
        line = self._verdict_line(REAL_BINARY)
        self.assertNotIn("ASPIRATIONAL-STUB", line, (
            "a path a package really declares was called aspirational: "
            f"{line.strip()}"))

    def test_a_declared_directory_resolves_as_itself(self) -> None:
        # Matched with its surrounding separators: "/bin" is a substring of
        # every /usr/bin path the gate also reports, and a case that reads the
        # wrong line is not a measurement.
        line = self._verdict_line(f": {REAL_COMPAT_DIR} ")
        self.assertNotIn("ASPIRATIONAL-STUB", line, (
            "a directory a package really declares was called aspirational: "
            f"{line.strip()}"))


if __name__ == "__main__":
    unittest.main()
