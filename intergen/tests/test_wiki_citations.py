# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tests for verified wiki citations (intergen/wiki_citations.py).

Covers the doc_source normalizer, the fail-closed integrity gate, and the
security-critical REFUSE-TO-CITE path (a tampered/unsigned page is never cited).
The gpg verifier is INJECTED (no real gpg/keyring), mirroring
tests/test_destructive_policy.py, so the trust logic is exercised deterministically.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from intergen.destructive_policy import OPERATOR_FINGERPRINT
from intergen.wiki_citations import WikiCitations, wiki_page_from_doc_source

_FPR = OPERATOR_FINGERPRINT


def _valid_status(primary_fpr: str = _FPR) -> str:
    # gpg --status-fd VALIDSIG line: subkey is field 3, primary key is the LAST.
    return f"[GNUPG:] NEWSIG\n[GNUPG:] VALIDSIG SUB 2026-07-10 0 4 0 1 8 00 {primary_fpr}\n"


def _good_verify(sig_path: str, data: bytes) -> tuple[int, str]:
    return 0, _valid_status()


def _bad_verify(sig_path: str, data: bytes) -> tuple[int, str]:
    return 2, ""  # non-zero rc => gpg says the signature is bad


def _wrong_key_verify(sig_path: str, data: bytes) -> tuple[int, str]:
    return 0, _valid_status("0000000000000000000000000000000000000000")


class NormalizerTests(unittest.TestCase):
    def test_extracts_primary_wiki_page(self):
        self.assertEqual(
            wiki_page_from_doc_source("wiki packages/package-manager.md"),
            ("packages/package-manager.html", "Package Manager"),
        )

    def test_first_wiki_ref_wins_and_private_docs_ignored(self):
        self.assertEqual(
            wiki_page_from_doc_source(
                "wiki install/verified-boot.md; docs/users/secure-boot-and-mok.md"),
            ("install/verified-boot.html", "Verified Boot"),
        )

    def test_readme_maps_to_index(self):
        self.assertEqual(wiki_page_from_doc_source("wiki README.md")[0], "index.html")

    def test_section_readme_maps_to_section_index(self):
        self.assertEqual(
            wiki_page_from_doc_source("wiki packages/README.md")[0],
            "packages/index.html",
        )

    def test_non_wiki_source_yields_none(self):
        self.assertIsNone(wiki_page_from_doc_source("README.md; docs/VISION.md"))
        self.assertIsNone(wiki_page_from_doc_source("docs/getting-started.md"))
        self.assertIsNone(wiki_page_from_doc_source(""))


class _Fixture:
    """A throwaway wiki doc-root: two pages + a manifest pinning their real sha256s."""

    def __init__(self, tmp: str):
        self.root = Path(tmp)
        self.page_rel = "packages/package-manager.html"
        self.page = self.root / self.page_rel
        self.page.parent.mkdir(parents=True, exist_ok=True)
        self.page.write_bytes(b"<html><body>pkm docs v1</body></html>")
        (self.root / "index.html").write_bytes(b"<html>home</html>")
        pages = {
            self.page_rel: hashlib.sha256(self.page.read_bytes()).hexdigest(),
            "index.html": hashlib.sha256((self.root / "index.html").read_bytes()).hexdigest(),
        }
        (self.root / "pages-manifest.json").write_text(
            json.dumps({"manifest_version": 1, "pages": pages}), encoding="utf-8")
        (self.root / "pages-manifest.json.asc").write_text("sig", encoding="utf-8")

    def wc(self, gpg_verify=_good_verify) -> WikiCitations:
        return WikiCitations(doc_root=str(self.root), gpg_verify=gpg_verify)


class IntegrityGateTests(unittest.TestCase):
    def test_green_verified_page_is_cited(self):
        with TemporaryDirectory() as tmp:
            fx = _Fixture(tmp)
            wc = fx.wc()
            self.assertTrue(wc.available)
            cite = wc.cite("wiki packages/package-manager.md")
            self.assertIsNotNone(cite)
            self.assertIn("Package Manager", cite)
            self.assertIn(f"file://{fx.page}", cite)
            self.assertIn(
                "https://wiki.intergenos.org/packages/package-manager.html", cite)

    def test_red_tampered_page_refused(self):
        with TemporaryDirectory() as tmp:
            fx = _Fixture(tmp)
            wc = fx.wc()  # manifest loaded from the pristine bytes
            # An attacker slipstreams modified content into the installed page.
            fx.page.write_bytes(b"<html><body>pkm docs v1 EVIL rm -rf ~</body></html>")
            self.assertIsNone(
                wc.cite("wiki packages/package-manager.md"),
                "a page that no longer matches its signed hash must NOT be cited")

    def test_untrusted_manifest_disables_all_citation(self):
        with TemporaryDirectory() as tmp:
            fx = _Fixture(tmp)
            wc = fx.wc(gpg_verify=_bad_verify)
            self.assertFalse(wc.available)
            self.assertIsNone(wc.cite("wiki packages/package-manager.md"))

    def test_wrong_key_signature_disables_citation(self):
        with TemporaryDirectory() as tmp:
            fx = _Fixture(tmp)
            wc = fx.wc(gpg_verify=_wrong_key_verify)
            self.assertFalse(wc.available)

    def test_absent_manifest_is_quiet_off(self):
        with TemporaryDirectory() as tmp:
            # no manifest written into this dir
            wc = WikiCitations(doc_root=tmp, gpg_verify=_good_verify)
            self.assertFalse(wc.available)
            self.assertIsNone(wc.cite("wiki packages/package-manager.md"))

    def test_page_not_in_manifest_is_refused(self):
        with TemporaryDirectory() as tmp:
            fx = _Fixture(tmp)
            # a real, unmodified page that the signed manifest does not pin
            (fx.root / "rogue.html").write_bytes(b"<html>rogue</html>")
            wc = fx.wc()
            self.assertIsNone(wc.cite("wiki rogue.md"),
                              "an unsigned page (not in the manifest) must be refused")

    def test_non_wiki_source_not_cited_even_when_available(self):
        with TemporaryDirectory() as tmp:
            wc = _Fixture(tmp).wc()
            self.assertIsNone(wc.cite("docs/getting-started.md"))


class ManifestGeneratorTests(unittest.TestCase):
    """The scripts/build-wiki-page-manifest.py generator: correctness + determinism."""

    @staticmethod
    def _generator_path():
        return (Path(__file__).resolve().parents[2]
                / "scripts" / "build-wiki-page-manifest.py")

    def setUp(self):
        """Skip when the repository's scripts/ is not beside us.

        This file is SHIPPED into the installed package. The generator under
        test is a repository tool that is not installed with it, so on a user's
        machine the import below raises FileNotFoundError and the test fails
        for a reason that has nothing to do with the code it covers. Same class
        and same answer as the packaging-tree skips elsewhere in this suite; in
        a checkout the script is present and the test runs in full.
        """
        if not self._generator_path().is_file():
            self.skipTest("repository scripts/ not present (installed layout)")

    @staticmethod
    def _load_generator():
        path = (Path(__file__).resolve().parents[2]
                / "scripts" / "build-wiki-page-manifest.py")
        spec = importlib.util.spec_from_file_location("wiki_page_manifest", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_hashes_only_html_and_is_deterministic(self):
        gen = self._load_generator()
        with TemporaryDirectory() as tmp:
            book = Path(tmp)
            (book / "a.html").write_bytes(b"<html>a</html>")
            (book / "sub").mkdir()
            (book / "sub" / "b.html").write_bytes(b"<html>b</html>")
            (book / "image.png").write_bytes(b"\x89PNG-not-hashed")
            m1 = gen.build_manifest(book)
            m2 = gen.build_manifest(book)
            self.assertEqual(m1, m2)                       # deterministic
            self.assertEqual(set(m1["pages"]), {"a.html", "sub/b.html"})  # html only
            self.assertEqual(m1["page_count"], 2)
            self.assertEqual(
                m1["pages"]["a.html"],
                hashlib.sha256(b"<html>a</html>").hexdigest())


if __name__ == "__main__":
    unittest.main()
