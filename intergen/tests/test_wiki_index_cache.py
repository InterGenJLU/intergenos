# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The computed documentation index is cached on disk, keyed on what it was
computed from, and a cache that does not match is never loaded.

The defect (every installed machine, first seen 2026-08-24): the passage
index over the verified wiki was computed in the retrieval object's
constructor, written nowhere, and re-embedded from scratch on every daemon
start against a one-slot embedding server under a start-up budget — on the
validation laptop 64 of 2182 passages within the budget, the rest between
turns; a restart re-paid the whole cost.

What is pinned here, on a throwaway verified wiki with a counting embedder:

  * the first build embeds and writes the cache under the daemon's own
    directory, files 0600 in a 0700 directory;
  * a second build with the same verified page hashes, the same embedder
    identity and the same index format loads without a single embedding
    request, and retrieval works from the loaded vectors;
  * a changed page hash, a changed embedder identity, a changed chunk count
    and a corrupted vectors file each re-embed (the mismatch is logged at
    INFO with its reason) — a stale or tampered cache is never loaded;
  * a world-writable cache directory or file is not read.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from intergen import wiki_retrieval as wr_mod
from intergen.tests.test_wiki_retrieval import _Fixture, _fake_embed, _good_verify
from intergen.wiki_retrieval import WikiRetrieval

IDENTITY = "nomic-embed-text-v1.5:abc123"


class _CountingEmbedder:
    def __init__(self):
        self.calls = 0

    def __call__(self, texts):
        self.calls += 1
        return _fake_embed(texts)


def _build(fx, cache_dir, embedder, identity=IDENTITY):
    return WikiRetrieval(fx.citations(_good_verify), embedder=embedder,
                         cache_dir=cache_dir, embedder_identity=identity)


class WikiIndexCacheTests(unittest.TestCase):

    def test_first_build_embeds_and_writes_a_private_cache(self):
        with TemporaryDirectory() as tmp:
            fx = _Fixture(tmp)
            cache = Path(tmp) / "state" / "wiki-index"
            emb = _CountingEmbedder()
            wr = _build(fx, cache, emb)
            self.assertTrue(wr.embeddings_ready)
            self.assertGreater(emb.calls, 0)
            self.assertTrue(cache.is_dir())
            self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o700)
            files = sorted(p.name for p in cache.iterdir())
            self.assertEqual(files, ["index.json", "index.npy"])
            for p in cache.iterdir():
                self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600, p)
            header = json.loads((cache / "index.json").read_text())
            self.assertEqual(header["key"], wr._cache_key())
            self.assertEqual(header["chunks"], wr.chunk_count)

    def test_a_matching_cache_loads_without_embedding(self):
        with TemporaryDirectory() as tmp:
            fx = _Fixture(tmp)
            cache = Path(tmp) / "state" / "wiki-index"
            _build(fx, cache, _CountingEmbedder())
            emb = _CountingEmbedder()
            with self.assertLogs("intergen.wiki_retrieval", level="INFO") as cm:
                wr = _build(fx, cache, emb)
            self.assertEqual(emb.calls, 0, "a matching cache still embedded")
            self.assertTrue(wr.embeddings_ready)
            self.assertTrue(any("loaded" in line.lower() and "cache" in line.lower()
                                for line in cm.output), cm.output)
            hit = wr.retrieve("encrypt my disk with luks")
            self.assertIsNotNone(hit)
            self.assertEqual(hit.rel_html, "install/disk-encryption.html")

    def _reembeds(self, fx, cache, identity=IDENTITY, reason_word=None):
        emb = _CountingEmbedder()
        with self.assertLogs("intergen.wiki_retrieval", level="INFO") as cm:
            wr = _build(fx, cache, emb, identity=identity)
        self.assertGreater(emb.calls, 0, "a mismatching cache was loaded")
        self.assertTrue(wr.embeddings_ready)
        if reason_word:
            self.assertTrue(any(reason_word in line for line in cm.output),
                            (reason_word, cm.output))
        return wr

    def test_a_changed_page_hash_reembeds(self):
        with TemporaryDirectory() as tmp:
            fx = _Fixture(tmp)
            cache = Path(tmp) / "state" / "wiki-index"
            _build(fx, cache, _CountingEmbedder())
            # A page changed and the manifest with it (a re-signed release).
            fx._write(fx.pkg_rel, "<html><body><main><h1>Package Manager</h1>"
                      "<p>pkm now also upgrades packages.</p></main></body></html>")
            pages = {fx.disk_rel: fx._sha(fx.disk_rel), fx.pkg_rel: fx._sha(fx.pkg_rel)}
            (fx.root / "pages-manifest.json").write_text(
                json.dumps({"manifest_version": 1, "pages": pages}), encoding="utf-8")
            self._reembeds(fx, cache, reason_word="key")

    def test_a_changed_embedder_identity_reembeds(self):
        with TemporaryDirectory() as tmp:
            fx = _Fixture(tmp)
            cache = Path(tmp) / "state" / "wiki-index"
            _build(fx, cache, _CountingEmbedder())
            self._reembeds(fx, cache, identity="other-model:def456",
                           reason_word="key")

    def test_a_corrupted_vectors_file_reembeds_and_is_replaced(self):
        with TemporaryDirectory() as tmp:
            fx = _Fixture(tmp)
            cache = Path(tmp) / "state" / "wiki-index"
            _build(fx, cache, _CountingEmbedder())
            vec = cache / "index.npy"
            data = bytearray(vec.read_bytes())
            data[-1] ^= 0xFF
            vec.write_bytes(bytes(data))
            self._reembeds(fx, cache, reason_word="hash")
            # The rebuilt index re-wrote a cache that verifies again.
            emb = _CountingEmbedder()
            _build(fx, cache, emb)
            self.assertEqual(emb.calls, 0)

    def test_a_header_with_the_wrong_chunk_count_reembeds(self):
        with TemporaryDirectory() as tmp:
            fx = _Fixture(tmp)
            cache = Path(tmp) / "state" / "wiki-index"
            _build(fx, cache, _CountingEmbedder())
            header = json.loads((cache / "index.json").read_text())
            header["chunks"] = header["chunks"] + 1
            (cache / "index.json").write_text(json.dumps(header))
            self._reembeds(fx, cache, reason_word="chunk")

    def test_a_world_writable_cache_is_not_read(self):
        with TemporaryDirectory() as tmp:
            fx = _Fixture(tmp)
            cache = Path(tmp) / "state" / "wiki-index"
            _build(fx, cache, _CountingEmbedder())
            os.chmod(cache, 0o777)
            try:
                self._reembeds(fx, cache, reason_word="writable")
            finally:
                os.chmod(cache, 0o700)
            os.chmod(cache / "index.npy", 0o666)
            try:
                self._reembeds(fx, cache, reason_word="writable")
            finally:
                os.chmod(cache / "index.npy", 0o600)

    def test_no_cache_dir_means_no_cache_and_no_write(self):
        with TemporaryDirectory() as tmp:
            fx = _Fixture(tmp)
            emb = _CountingEmbedder()
            wr = WikiRetrieval(fx.citations(_good_verify), embedder=emb)
            self.assertTrue(wr.embeddings_ready)
            self.assertIsNone(wr._cache_key())
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()),
                             ["install", "packages", "pages-manifest.json",
                              "pages-manifest.json.asc"])

    def test_the_key_binds_the_manifest_the_embedder_and_the_format(self):
        with TemporaryDirectory() as tmp:
            fx = _Fixture(tmp)
            cache = Path(tmp) / "state" / "wiki-index"
            wr = _build(fx, cache, _CountingEmbedder())
            key = wr._cache_key()
            self.assertEqual(len(key), 64)
            other = _build(fx, cache, _CountingEmbedder(), identity="x:y")
            self.assertNotEqual(key, other._cache_key())
            self.assertIn("_INDEX_FORMAT_VERSION", dir(wr_mod))


if __name__ == "__main__":
    unittest.main()
