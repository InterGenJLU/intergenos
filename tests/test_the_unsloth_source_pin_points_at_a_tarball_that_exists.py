# SPDX-License-Identifier: GPL-3.0-or-later
"""The unsloth recipe must pin a source tarball that upstream actually serves.

Measured on 2026-09-19: the recipe pinned version 2026.7.4 at
https://files.pythonhosted.org/packages/source/u/unsloth/unsloth-2026.7.4.tar.gz,
and that URL answers 404. The release itself exists on PyPI but ships ONE file,
a wheel — upstream stopped publishing source tarballs after 2026.6.2 (uploaded
2026-06-10), and no artifact PyPI lists for any unsloth release carries the
sha256 the recipe pinned. A pin nobody can fetch is a hole in the corresponding
-source claim for the package: the project cannot rebuild it from its declared
source, and neither can a user.

The newest release that does publish a source tarball is 2026.6.2, whose bytes
were fetched and hashed on 2026-09-19 (77,606,507 bytes, sha256 below, equal to
the digest PyPI records). Its declared dependency band is the same band the
recipe already documents for 2026.7.4 — torch >=2.4,<2.11; bitsandbytes >=0.45.5
excluding 0.46 and 0.48; triton >=3.0; transformers >=4.51.3,<=5.5.0 with the
same exclusions; trl >=0.18.2,<=0.24 excluding 0.19; datasets >=3.4.1,<4.4
excluding 4.0.* and 4.1.0; peft >=0.18 excluding 0.11 — so moving the pin does
not move what the package is built against.

The live check against PyPI is opt-in (INTERGENOS_LIVE_SOURCE_CHECK=1) so the
suite stays hermetic; the pin itself is asserted here without a network.
"""
import os
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
RECIPE = REPO / "packages" / "ai" / "unsloth" / "package.yml"

# The newest unsloth release that upstream publishes as a source tarball, and
# the sha256 of those bytes as measured from the real download.
EXPECTED_VERSION = "2026.6.2"
EXPECTED_SHA256 = "4c64575cce1ff999e1a7a5c5d3508bc34b083f05c88bbdeaba298a1877e80bed"


def _recipe():
    return yaml.safe_load(RECIPE.read_text())


class TheUnslothPinPointsAtSomethingThatExists(unittest.TestCase):
    def test_the_version_is_one_upstream_publishes_as_a_source_tarball(self):
        """THE DEFECT: 2026.7.4 has no source tarball, so the pin cannot resolve."""
        self.assertEqual(str(_recipe()["version"]), EXPECTED_VERSION)

    def test_the_sha256_is_the_hash_of_those_real_bytes(self):
        meta = _recipe()
        self.assertEqual(len(meta["source"]), 1, meta["source"])
        self.assertEqual(meta["source"][0]["sha256"], EXPECTED_SHA256)

    def test_the_url_derives_its_filename_from_the_version(self):
        """Version and URL cannot drift apart if the URL substitutes ${version}."""
        url = _recipe()["source"][0]["url"]
        self.assertIn("${version}", url, url)
        self.assertTrue(url.startswith("https://"), url)

    @unittest.skipUnless(
        os.environ.get("INTERGENOS_LIVE_SOURCE_CHECK") == "1",
        "live PyPI check is opt-in: set INTERGENOS_LIVE_SOURCE_CHECK=1. While "
        "it is skipped, NOTHING about upstream availability is verified here.",
    )
    def test_the_pinned_url_answers_and_its_bytes_hash_to_the_pin(self):
        import hashlib
        import urllib.request

        meta = _recipe()
        url = meta["source"][0]["url"].replace("${version}", str(meta["version"]))
        url = url.replace("${name}", str(meta["name"]))
        digest = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=600) as fh:
            self.assertEqual(fh.status, 200, url)
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        self.assertEqual(digest.hexdigest(), meta["source"][0]["sha256"], url)


if __name__ == "__main__":
    unittest.main()
