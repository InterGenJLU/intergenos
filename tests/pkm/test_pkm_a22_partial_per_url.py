#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PKM-A22 regression: per-URL download partials + sha-mismatch .part cleanup.

The resumable download `.part` was keyed only by filename, shared across all
mirror-failover attempts — so mirror B's Range-resume appended its body onto
mirror A's leftover partial bytes, splicing two different bodies into a corrupt
archive. sha256 then rejected it, but the corrupt `.part` lingered and every
retry resumed it, so the download could never converge. Separately, a sha
mismatch unlinked only local_path, not the partial.

Fixed: the default `.part` is keyed PER-URL (<dest>.<url-hash>.part), so a
failover never resumes another mirror's bytes; and a sha mismatch clears every
per-URL partial for the filename.
"""

import hashlib
import io
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pkm.repo as repo_mod
from pkm.repo import RepoManager


class _FakeResp:
    def __init__(self, body, status=200):
        self._b = io.BytesIO(body)
        self.status = status

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _url_tag(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


class PerUrlPartialTest(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.cache = Path(self._td.name)
        (self.cache / "partial").mkdir()
        self.mgr = RepoManager.__new__(RepoManager)

    def tearDown(self):
        self._td.cleanup()

    def test_failover_does_not_resume_other_mirrors_partial(self):
        dest = self.cache / "pkg-1.0.igos.tar.gz"
        url_a = "https://mirrorA.example/pkg-1.0.igos.tar.gz"
        url_b = "https://mirrorB.example/pkg-1.0.igos.tar.gz"
        # Mirror A left a partial behind (its per-URL .part).
        a_part = self.cache / "partial" / f"{dest.name}.{_url_tag(url_a)}.part"
        a_part.write_bytes(b"AAAAAAAA")  # 8 bytes of mirror A's body

        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["range"] = req.get_header("Range")
            return _FakeResp(b"BBBB-complete-body-from-mirror-B")

        with patch.object(repo_mod, "REPO_CACHE_DIR", self.cache), \
             patch.object(urllib.request, "urlopen", fake_urlopen):
            self.mgr._download(url_b, dest)

        # Mirror B started fresh: NO Range header (its own per-URL .part did
        # not exist), so it did not resume mirror A's 8 bytes.
        self.assertIsNone(captured["range"])
        # The final file is mirror B's body, intact (not spliced with AAAA).
        self.assertEqual(dest.read_bytes(), b"BBBB-complete-body-from-mirror-B")
        # Mirror A's leftover partial is untouched (per-URL, separate file).
        self.assertTrue(a_part.exists())

    def test_sha_mismatch_clears_every_partial_for_file(self):
        filename = "pkg-1.0.igos.tar.gz"
        pkgcache = self.cache / "packages"
        pkgcache.mkdir()
        # Stale per-URL partials lingering for this filename.
        for tag in ("aaaaaaaaaaaa", "bbbbbbbbbbbb"):
            (self.cache / "partial" / f"{filename}.{tag}.part").write_bytes(b"x")
        # An unrelated partial that must survive.
        (self.cache / "partial" / "other-2.0.igos.tar.gz.cccc.part").write_bytes(b"y")

        mgr = self.mgr
        mgr.repos = {"r": {"url": "https://m/"}}
        with patch.object(repo_mod, "REPO_CACHE_DIR", self.cache), \
             patch.object(repo_mod, "REPO_PKG_CACHE", pkgcache), \
             patch.object(mgr, "get_package",
                          return_value={"filename": filename, "sha256": "0" * 64,
                                        "repo": "r"}), \
             patch.object(mgr, "_mirror_urls_for_pkg",
                          return_value=["https://m/" + filename]), \
             patch.object(mgr, "_download",
                          side_effect=lambda url, dst, **_kw:
                              Path(dst).write_bytes(b"bad")), \
             patch.object(mgr, "_verify_checksum", return_value=False):
            ok, msg = mgr.download_package("pkg")

        self.assertFalse(ok)
        self.assertIn("FAILED", msg)
        remaining = {p.name for p in (self.cache / "partial").iterdir()}
        # Both partials for this filename are gone...
        self.assertNotIn(f"{filename}.aaaaaaaaaaaa.part", remaining)
        self.assertNotIn(f"{filename}.bbbbbbbbbbbb.part", remaining)
        # ...the unrelated one survives.
        self.assertIn("other-2.0.igos.tar.gz.cccc.part", remaining)


if __name__ == "__main__":
    unittest.main()
