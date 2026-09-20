# SPDX-License-Identifier: GPL-3.0-or-later
"""The v4l-utils recipe's pinned source must stay fetchable, and its URL must
stay in step with its version.

Why this file exists: on 2026-09-19 a sweep that mirrored declared sources
could not fetch this tarball. Two attempts from this project's machines ended
early — HTTP/2 answered 200 and then reset the stream after 89,885 of the
1,384,024 bytes, and an HTTP/1.1 retry ended the TLS connection after 81,599 —
so both partials hashed to something other than the pin and neither was kept.
That looked like a dead pin.

It was not. Re-measured on 2026-09-20 from the same machine, ten fetches in a
row — five with curl and five with wget, the two tools the source fetcher uses
— returned all 1,384,024 bytes and hashed to exactly the sha256 the recipe
pins. 1.32.0 is also the newest release the upstream download directory lists
(the listing was fetched and read on 2026-09-20). So the pin is correct, the
version is current, and what happened on 2026-09-19 was upstream refusing to
serve for a while, which is the case the project's own source mirror exists to
cover.

What this file pins, therefore, is not a fix but the two things that could
silently go wrong here:

1. The recipe's URL hard-codes the version instead of substituting ${version},
   as 1,075 of the tree's 1,323 source URLs do. Nothing stops someone raising
   version: to 1.34.0 while the URL still names 1.32.0 — the build would then
   fetch and compile 1.32.0 sources under a 1.34.0 label. The URL is left as it
   is, because rewriting it changes the recipe's template hash and forces a
   rebuild of the package on every installed system for no functional gain.
   The drift is caught here instead, at no cost.
2. The pinned bytes are the bytes upstream serves. The live fetch is opt-in
   (INTERGENOS_LIVE_SOURCE_CHECK=1) so the suite stays hermetic and a bad hour
   at upstream never reports itself as a defect in this tree. While it is
   skipped, NOTHING about upstream availability is verified here.
"""
import os
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
RECIPE = REPO / "packages" / "desktop" / "v4l-utils" / "package.yml"

# Measured 2026-09-20 from ten complete fetches of the declared URL.
EXPECTED_VERSION = "1.32.0"
EXPECTED_SHA256 = "6828828a17775526eb93fb258a9294d1d1073d633c344dd71ecd4e7a1ffb7dfc"
EXPECTED_BYTES = 1384024


def _recipe():
    return yaml.safe_load(RECIPE.read_text())


class TheV4lUtilsPinResolves(unittest.TestCase):
    def test_the_recipe_declares_exactly_one_source(self):
        self.assertEqual(len(_recipe()["source"]), 1)

    def test_the_pinned_sha256_is_the_hash_of_the_bytes_upstream_serves(self):
        self.assertEqual(_recipe()["source"][0]["sha256"], EXPECTED_SHA256)

    def test_the_url_names_the_recipes_own_version(self):
        """A version raised without the URL would build the old sources.

        This reads the version out of the recipe rather than comparing it to
        the constant above, so it fails on the DRIFT itself and not merely on
        the version having moved.
        """
        meta = _recipe()
        version = str(meta["version"])
        url = meta["source"][0]["url"]
        self.assertIn(f"v4l-utils-{version}.tar.", url, url)
        self.assertTrue(url.startswith("https://"), url)

    def test_the_version_is_the_one_these_bytes_came_from(self):
        self.assertEqual(str(_recipe()["version"]), EXPECTED_VERSION)

    @unittest.skipUnless(
        os.environ.get("INTERGENOS_LIVE_SOURCE_CHECK") == "1",
        "live upstream check is opt-in: set INTERGENOS_LIVE_SOURCE_CHECK=1. "
        "While it is skipped, NOTHING about upstream availability is verified "
        "here — the recipe's own consistency is all that is checked.",
    )
    def test_the_pinned_url_answers_and_its_bytes_hash_to_the_pin(self):
        import hashlib
        import urllib.request

        meta = _recipe()
        url = meta["source"][0]["url"]
        digest = hashlib.sha256()
        seen = 0
        with urllib.request.urlopen(url, timeout=600) as fh:
            self.assertEqual(fh.status, 200, url)
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
                seen += len(chunk)
        self.assertEqual(seen, EXPECTED_BYTES, url)
        self.assertEqual(digest.hexdigest(), meta["source"][0]["sha256"], url)


if __name__ == "__main__":
    unittest.main()
