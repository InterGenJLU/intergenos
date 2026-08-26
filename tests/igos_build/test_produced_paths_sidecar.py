# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The builder records the complete install list, not just the sample.

igos-build/verify_paths_derive.py has always written a sidecar beside each
recipe holding 2-3 identity-signal paths for the pre-squashfs audit. It now
also carries `produced_paths`: every file the package installed at that build.

WHY THE WHOLE LIST. scripts/check-aspirational-stubs.py asks "does this package
produce the path this surface cites?". A sample cannot answer that, because
REFUSING a path requires knowing the package installs nothing of that name.
Without the complete list the gate falls back to a substring guess on the
package's name, which resolves /usr/bin/foo-does-not-exist for a surface owned
by foo — the shape the gate exists to refuse (lane 13 finding c1, 2026-08-24).

These cases drive the real helpers, not a re-implementation of them, and they
run against a temporary recipe directory: nothing here touches the packages
tree or needs a build.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

import verify_paths_derive as vpd  # noqa: E402


class TestNormalizeProducedPaths(unittest.TestCase):

    def test_directories_are_dropped_and_files_are_absolute(self) -> None:
        got = vpd.normalize_produced_paths(
            ["usr/bin/tool", "usr/share/", "/usr/lib/libtool.so"])
        self.assertEqual(got, ["/usr/bin/tool", "/usr/lib/libtool.so"])

    def test_duplicates_collapse(self) -> None:
        got = vpd.normalize_produced_paths(
            ["usr/bin/tool", "/usr/bin/tool", "usr/bin/tool"])
        self.assertEqual(got, ["/usr/bin/tool"])

    def test_the_order_is_stable(self) -> None:
        """A rebuild installing the same files must produce the same bytes, or
        the unchanged-check below can never hold and every build reports a
        sidecar change."""
        a = vpd.normalize_produced_paths(["usr/bin/b", "usr/bin/a", "usr/bin/c"])
        b = vpd.normalize_produced_paths(["usr/bin/c", "usr/bin/a", "usr/bin/b"])
        self.assertEqual(a, b)
        self.assertEqual(a, ["/usr/bin/a", "/usr/bin/b", "/usr/bin/c"])

    def test_a_non_string_entry_is_ignored_rather_than_crashing(self) -> None:
        self.assertEqual(
            vpd.normalize_produced_paths(["usr/bin/tool", None, 7]),
            ["/usr/bin/tool"])


class _Pkg:
    """The two attributes derive_and_write_sidecar reads off a Package."""

    def __init__(self, name: str, template_path: Path) -> None:
        self.name = name
        self.template_path = template_path


class TestSidecarCarriesTheWholeList(unittest.TestCase):

    def _pkg(self, tmp: Path, name: str = "widget") -> _Pkg:
        d = tmp / "packages" / "core" / name
        d.mkdir(parents=True)
        tpl = d / "package.yml"
        tpl.write_text(f"name: {name}\nversion: '1.0'\nrelease: 1\n")
        return _Pkg(name, tpl)

    def test_produced_paths_holds_every_file_while_verify_paths_stays_a_sample(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            pkg = self._pkg(tmp)
            files = [f"usr/share/widget/data{i}" for i in range(20)]
            files += ["usr/bin/widget", "usr/lib/libwidget.so"]
            self.assertTrue(vpd.derive_and_write_sidecar(pkg, files))
            payload = json.loads(
                (pkg.template_path.parent / vpd.SIDECAR_NAME).read_text())
            self.assertEqual(len(payload["produced_paths"]), len(files))
            self.assertIn("/usr/bin/widget", payload["produced_paths"])
            self.assertIn("/usr/share/widget/data7", payload["produced_paths"])
            self.assertLessEqual(len(payload["verify_paths"]), 3)
            for p in payload["verify_paths"]:
                self.assertIn(p, payload["produced_paths"])

    def test_an_unchanged_rebuild_does_not_rewrite_the_sidecar(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            pkg = self._pkg(tmp)
            files = ["usr/bin/widget", "usr/lib/libwidget.so"]
            self.assertTrue(vpd.derive_and_write_sidecar(pkg, files))
            self.assertFalse(
                vpd.derive_and_write_sidecar(pkg, list(reversed(files))),
                "the same install list in a different order rewrote the "
                "sidecar; every rebuild would report a change")

    def test_a_changed_install_list_does_rewrite(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            pkg = self._pkg(tmp)
            self.assertTrue(vpd.derive_and_write_sidecar(pkg, ["usr/bin/widget"]))
            self.assertTrue(vpd.derive_and_write_sidecar(
                pkg, ["usr/bin/widget", "usr/bin/widget-helper"]))
            payload = json.loads(
                (pkg.template_path.parent / vpd.SIDECAR_NAME).read_text())
            self.assertIn("/usr/bin/widget-helper", payload["produced_paths"])


if __name__ == "__main__":
    unittest.main()
