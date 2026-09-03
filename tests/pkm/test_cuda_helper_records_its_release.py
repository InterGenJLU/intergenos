#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The CUDA toolkit helper records its own package release in the footprint.

Measured on a fresh R001.2 install on 2026-09-03: the package manager on the
installed image fills a missing release with 1 when it merges a helper's
footprint manifest into the package record. The toolkit installed at release
5 was recorded at release 1; `pkm list upgradable` then offered a same-version
"upgrade" forever, and taking it deleted the toolkit. The helper now writes
`release_installed` into the manifest itself, so every package manager
release — including the one already on installed machines — records the
release that was installed.

Asserted here: the release the helper carries equals the release in
package.yml (the build refuses a mismatch; this holds the line in the tree),
and the stamping function, run for real against a manifest the real helper
library wrote, adds the field and leaves the rest of the manifest intact.
"""

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "packages" / "compute" / "cuda-toolkit"
HELPER = PKG / "helper" / "igos-install-cuda-toolkit"
HELPER_LIB = REPO / "packages" / "core" / "intergenos-helper-lib" / "helper-lib.sh"


def _helper_release():
    m = re.search(r'^HELPER_RELEASE="(\d+)"$', HELPER.read_text(), re.M)
    return int(m.group(1)) if m else None


def _package_release():
    m = re.search(r"^release:\s*(\d+)", (PKG / "package.yml").read_text(), re.M)
    return int(m.group(1))


def _stamp_function_source():
    src = HELPER.read_text()
    start = src.index("stamp_release_into_manifest() {")
    end = src.index("\n}\n", start) + 3
    return src[start:end]


class TheReleaseIsCarriedAndInLockstep(unittest.TestCase):

    def test_the_helper_carries_a_release(self):
        self.assertIsNotNone(_helper_release(),
                             'the helper must define HELPER_RELEASE="<n>"')

    def test_the_release_equals_the_package_release(self):
        self.assertEqual(_helper_release(), _package_release())

    def test_the_stamp_runs_after_the_commit(self):
        src = HELPER.read_text()
        self.assertGreater(src.index("\nstamp_release_into_manifest\n"),
                           src.index("\nigos_helper_commit\n"),
                           "the stamp must run after the library has "
                           "written the manifest")

    def test_the_build_refuses_a_mismatch(self):
        self.assertIn("HELPER_RELEASE", (PKG / "build.sh").read_text())


class TheStampWritesTheReleaseIntoARealManifest(unittest.TestCase):

    def test_the_field_is_added_and_the_rest_kept(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            payload = td / "opt" / "cuda" / "bin" / "nvcc"
            payload.parent.mkdir(parents=True)
            payload.write_bytes(b"\x7fELF\x02" + b"\x00" * 60)
            script = (
                f"source {HELPER_LIB}\n"
                f"export IGOS_HELPER_MANIFEST_DIR={td}/m\n"
                f"mkdir -p {td}/m\n"
                "export PKM_HELPER_INVOCATION=1\n"
                "igos_helper_init cuda-toolkit || exit 90\n"
                "igos_helper_set_version 13.3.1\n"
                f"igos_helper_record_file {payload} || exit 91\n"
                "igos_helper_record_dep nvidia\n"
                "igos_helper_commit || exit 92\n"
                f"HELPER_RELEASE={_helper_release()}\n"
                + _stamp_function_source()
                + "stamp_release_into_manifest || exit 93\n"
            )
            r = subprocess.run(["bash", "-c", script], capture_output=True,
                               text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            manifest = json.loads((td / "m" / "cuda-toolkit.manifest").read_text())
            self.assertEqual(manifest["release_installed"], _helper_release())
            self.assertEqual(manifest["version_installed"], "13.3.1")
            self.assertEqual(manifest["files"], [str(payload)])
            self.assertEqual(manifest["depends"], ["nvidia"])
            self.assertFalse((td / "m" / "cuda-toolkit.manifest.tmp").exists())


if __name__ == "__main__":
    unittest.main()
