"""Every declared source is sha-verified before ANY use (review finding H1).

Pins the two halves of the fix:
  parser  — a present sha256 must be exactly 64 hex chars (the old
            'placeholder…' bypass strings no longer parse), while
            `generated: true` first-party sources legitimately carry none;
  builder — extract_source verifies EVERY pkg.source entry (not just
            source[0]) before extraction begins, so bundled_deps tarballs
            and Rule-5 build.sh-consumed secondaries can no longer reach
            the build unverified.
"""

import hashlib
import importlib
import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

from .factories import make_package, make_source  # noqa: E402

_builder_mod = importlib.import_module("igos-build.builder")
BuildExecutor = _builder_mod.BuildExecutor

from parser import (  # noqa: E402  (path manipulation must precede import)
    TemplateError,
    _parse_sources,
)

GOOD_SHA = "a" * 64


class _CapturingLogger:
    def __init__(self):
        self.errors = []
        self.infos = []

    def error(self, msg):
        self.errors.append(msg)

    def info(self, msg):
        self.infos.append(msg)


def _make_tarball(dest: Path, inner_name: str) -> str:
    """Write a tiny single-file tarball; return its real sha256."""
    payload_dir = dest.parent / f"{dest.name}.payload"
    payload_dir.mkdir()
    (payload_dir / inner_name).write_text("content\n")
    with tarfile.open(dest, "w:gz") as tf:
        tf.add(payload_dir, arcname="top")
    return hashlib.sha256(dest.read_bytes()).hexdigest()


def _src(url, sha256=None, filename=None, generated=False):
    # A REAL parser.Source: a field added to the dataclass arrives here with its
    # default rather than stranding a hand-rolled stand-in (see .factories).
    return make_source(url=url, sha256=sha256, filename=filename,
                       generated=generated)


class TestParserSha256Grammar(unittest.TestCase):
    def _parse_one(self, entry):
        return _parse_sources([entry], {}, Path("test/package.yml"))

    def test_64_hex_passes(self):
        srcs = self._parse_one({"url": "https://x/y.tar.gz", "sha256": GOOD_SHA})
        self.assertEqual(srcs[0].sha256, GOOD_SHA)

    def test_placeholder_prefix_rejected(self):
        with self.assertRaises(TemplateError):
            self._parse_one({"url": "https://x/y.tar.gz",
                             "sha256": "placeholder-fill-me-in"})

    def test_truncated_hex_rejected(self):
        with self.assertRaises(TemplateError):
            self._parse_one({"url": "https://x/y.tar.gz", "sha256": "a" * 63})

    def test_non_hex_rejected(self):
        with self.assertRaises(TemplateError):
            self._parse_one({"url": "https://x/y.tar.gz",
                             "sha256": "z" * 64})

    def test_generated_without_sha_passes(self):
        srcs = self._parse_one({"url": "file://in-tree.tar.xz",
                                "generated": True})
        self.assertIsNone(srcs[0].sha256)

    def test_missing_sha_not_generated_rejected(self):
        with self.assertRaises(TemplateError):
            self._parse_one({"url": "https://x/y.tar.gz"})


class _ExecutorStub:
    """Carries only the two methods under test, on a minimal self."""

    _verify_source_checksum = BuildExecutor._verify_source_checksum
    extract_source = BuildExecutor.extract_source

    def __init__(self, sources_dir, logger):
        self.sources_dir = sources_dir
        self.logger = logger

    def run_command(self, cmd, env=None, cwd=None):
        return subprocess.run(cmd, shell=True).returncode


class TestBuilderVerifiesEverySource(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.sources = self.tmp / "sources"
        self.sources.mkdir()
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.logger = _CapturingLogger()
        self.stub = _ExecutorStub(self.sources, self.logger)

    def tearDown(self):
        self._tmp.cleanup()

    def _extract(self, pkg):
        return BuildExecutor.extract_source(self.stub, pkg, self.work)

    def _pkg(self, sources, bundled_deps=None):
        return make_package(source=sources, bundled_deps=bundled_deps or [])

    def test_secondary_mismatch_fails_before_extraction(self):
        sha1 = _make_tarball(self.sources / "primary.tar.gz", "a.txt")
        _make_tarball(self.sources / "second.tar.gz", "b.txt")
        pkg = self._pkg([
            _src("https://x/primary.tar.gz", sha256=sha1),
            _src("https://x/second.tar.gz", sha256="b" * 64),
        ])
        self.assertIsNone(self._extract(pkg),
                          "secondary with a wrong pin must fail the build")
        self.assertFalse((self.work / "src").exists(),
                         "nothing may be extracted when any source fails")
        self.assertTrue(any("mismatch" in e.lower() for e in self.logger.errors))

    def test_secondary_missing_fails(self):
        sha1 = _make_tarball(self.sources / "primary.tar.gz", "a.txt")
        pkg = self._pkg([
            _src("https://x/primary.tar.gz", sha256=sha1),
            _src("https://x/absent.tar.gz", sha256=GOOD_SHA),
        ])
        self.assertIsNone(self._extract(pkg))
        self.assertTrue(any("absent.tar.gz" in e for e in self.logger.errors))

    def test_all_pinned_and_correct_extracts(self):
        sha1 = _make_tarball(self.sources / "primary.tar.gz", "a.txt")
        sha2 = _make_tarball(self.sources / "second.tar.gz", "b.txt")
        pkg = self._pkg([
            _src("https://x/primary.tar.gz", sha256=sha1),
            _src("https://x/second.tar.gz", sha256=sha2),
        ])
        src_dir = self._extract(pkg)
        self.assertIsNotNone(src_dir)
        self.assertTrue((src_dir / "a.txt").exists(),
                        "primary payload extracted (one component stripped)")

    def test_bundled_dep_verified_via_source_loop(self):
        # The bundled tarball is a pkg.source entry, so the up-front loop
        # covers it: a wrong pin fails before the bundled extraction runs.
        sha1 = _make_tarball(self.sources / "primary.tar.gz", "a.txt")
        _make_tarball(self.sources / "gmp-6.0.tar.gz", "gmp.c")
        pkg = self._pkg(
            [_src("https://x/primary.tar.gz", sha256=sha1),
             _src("https://x/gmp-6.0.tar.gz", sha256="c" * 64)],
            bundled_deps=["gmp -> gmp"],
        )
        self.assertIsNone(self._extract(pkg))

    def test_generated_source_skips_pin_by_design(self):
        path = self.sources / "gen.tar.gz"
        _make_tarball(path, "g.txt")
        src = _src("file://gen.tar.gz", generated=True)
        ok = BuildExecutor._verify_source_checksum(
            self.stub, self._pkg([src]), src, "gen.tar.gz", path)
        self.assertTrue(ok)
        self.assertTrue(any("generated first-party" in m
                            for m in self.logger.infos))

    def test_unpinned_not_generated_refused(self):
        path = self.sources / "nopin.tar.gz"
        _make_tarball(path, "n.txt")
        src = _src("https://x/nopin.tar.gz")
        ok = BuildExecutor._verify_source_checksum(
            self.stub, self._pkg([src]), src, "nopin.tar.gz", path)
        self.assertFalse(ok, "an unpinned non-generated source must refuse")


if __name__ == "__main__":
    unittest.main()
