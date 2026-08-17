"""Containment checks resist prefix-collision escapes (review finding H2).

The old checks compared paths with str.startswith, which blesses a sibling
whose name merely EXTENDS the root's ('/x/y-evil' startswith '/x/y'). Every
site now uses Path.is_relative_to; the parser additionally rejects
multi-component source cache filenames and malformed bundled_deps dests at
template time. Each *_collision test here fails against the old code.
"""

import hashlib
import importlib
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

from .factories import make_package, make_source  # noqa: E402

_builder_mod = importlib.import_module("igos-build.builder")
_tracker_mod = importlib.import_module("igos-build.tracker")
BuildExecutor = _builder_mod.BuildExecutor
_validate_tar_members = _builder_mod._validate_tar_members
PackageTracker = _tracker_mod.PackageTracker

from parser import (  # noqa: E402  (path manipulation must precede import)
    TemplateError,
    _parse_sources,
    parse_template,
)


class _CapturingLogger:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.infos = []

    def error(self, msg):
        self.errors.append(msg)

    def warning(self, msg):
        self.warnings.append(msg)

    def info(self, msg):
        self.infos.append(msg)


def _tar_with_member(dest: Path, member_name: str) -> None:
    """Craft a tarball whose single member carries an arbitrary name."""
    payload = dest.parent / "payload.bin"
    payload.write_text("x\n")
    with tarfile.open(dest, "w:gz") as tf:
        tf.add(payload, arcname=member_name)


class TestTarMemberCollision(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.logger = _CapturingLogger()

    def tearDown(self):
        self._tmp.cleanup()

    def test_sibling_prefix_collision_rejected(self):
        # dest = <tmp>/x/y; member resolves to <tmp>/x/y-evil/f, which
        # startswith(str(dest)) — the old check PASSED this escape.
        dest = self.tmp / "x" / "y"
        dest.mkdir(parents=True)
        tb = self.tmp / "evil.tar.gz"
        _tar_with_member(tb, "../y-evil/f")
        self.assertFalse(_validate_tar_members(tb, dest, self.logger),
                         "prefix-collision sibling escape must be rejected")

    def test_plain_traversal_still_rejected(self):
        dest = self.tmp / "x" / "y"
        dest.mkdir(parents=True)
        tb = self.tmp / "evil2.tar.gz"
        _tar_with_member(tb, "../../outside")
        self.assertFalse(_validate_tar_members(tb, dest, self.logger))

    def test_clean_archive_passes(self):
        dest = self.tmp / "x" / "y"
        dest.mkdir(parents=True)
        tb = self.tmp / "ok.tar.gz"
        _tar_with_member(tb, "top/inner.txt")
        self.assertTrue(_validate_tar_members(tb, dest, self.logger))


class _ExtractStub:
    _verify_source_checksum = BuildExecutor._verify_source_checksum
    extract_source = BuildExecutor.extract_source

    def __init__(self, sources_dir, logger):
        self.sources_dir = sources_dir
        self.logger = logger

    def run_command(self, cmd, env=None, cwd=None):
        return subprocess.run(cmd, shell=True).returncode


def _real_tarball(dest: Path, inner: str) -> str:
    d = dest.parent / f"{dest.name}.p"
    d.mkdir()
    (d / inner).write_text("c\n")
    with tarfile.open(dest, "w:gz") as tf:
        tf.add(d, arcname="top")
    return hashlib.sha256(dest.read_bytes()).hexdigest()


class TestZipAndBundledDestCollision(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.sources = self.tmp / "sources"
        self.sources.mkdir()
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.logger = _CapturingLogger()
        self.stub = _ExtractStub(self.sources, self.logger)

    def tearDown(self):
        self._tmp.cleanup()

    def test_zip_member_collision_rejected(self):
        zpath = self.sources / "src.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("../src-evil/f", "x")
        sha = hashlib.sha256(zpath.read_bytes()).hexdigest()
        pkg = make_package(bundled_deps=[],
                           source=[make_source("https://x/src.zip",
                                               sha256=sha)])
        self.assertIsNone(BuildExecutor.extract_source(self.stub, pkg, self.work))
        self.assertTrue(any("SECURITY" in e for e in self.logger.errors))

    def test_bundled_dest_sibling_collision_rejected(self):
        # dest_rel '../src-evil' resolves to <work>/src-evil — startswith
        # str(<work>/src) is True, so the old B8 check PASSED this escape.
        sha1 = _real_tarball(self.sources / "primary.tar.gz", "a.txt")
        sha2 = _real_tarball(self.sources / "dep-1.0.tar.gz", "d.txt")
        pkg = make_package(
            bundled_deps=["dep -> ../src-evil"],
            source=[
                make_source("https://x/primary.tar.gz", sha256=sha1),
                make_source("https://x/dep-1.0.tar.gz", sha256=sha2),
            ])
        self.assertIsNone(BuildExecutor.extract_source(self.stub, pkg, self.work))
        self.assertTrue(any("escapes source tree" in e
                            for e in self.logger.errors))
        self.assertFalse((self.work / "src-evil").exists())


class TestStagingSymlinkCollision(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.logger = _CapturingLogger()
        # SimpleNamespace is correct HERE and only here: this stands in for the
        # BuildExecutor's `self`, not for a parser dataclass. It carries the one
        # attribute the unbound method reads (logger); BuildExecutor is not a
        # dataclass, so there is no field set that could drift out from under it.
        # Parser objects use the factories instead — see .factories.
        self.stub = SimpleNamespace(logger=self.logger)
        self.pkg = make_package()

    def tearDown(self):
        self._tmp.cleanup()

    def _validate(self, staging):
        return PackageTracker._validate_staging_paths(self.stub, self.pkg,
                                                      staging)

    def test_symlink_to_prefix_collision_sibling_rejected(self):
        # staging = <tmp>/stag; link target resolves into <tmp>/stag-evil —
        # startswith(str(staging)) is True, so the old check ALLOWED it.
        staging = self.tmp / "stag"
        (staging / "usr").mkdir(parents=True)
        evil = self.tmp / "stag-evil"
        evil.mkdir()
        (evil / "t.txt").write_text("x\n")
        (staging / "usr" / "link").symlink_to("../../stag-evil/t.txt")
        self.assertFalse(self._validate(staging),
                         "symlink escaping into a name-colliding sibling "
                         "must be rejected")

    def test_symlink_within_staging_allowed(self):
        staging = self.tmp / "stag"
        (staging / "usr").mkdir(parents=True)
        (staging / "usr" / "real.txt").write_text("x\n")
        (staging / "usr" / "link").symlink_to("real.txt")
        self.assertTrue(self._validate(staging))

    def test_intra_package_deploy_target_allowed(self):
        # Escapes staging via relative path but the post-deploy target is in
        # this package's own manifest — the documented ALLOW case.
        staging = self.tmp / "stag"
        (staging / "usr" / "bin").mkdir(parents=True)
        (staging / "usr" / "lib").mkdir(parents=True)
        (staging / "usr" / "lib" / "libx.so").write_text("x\n")
        (staging / "usr" / "bin" / "link").symlink_to(
            "../../../usr/lib/libx.so")
        self.assertTrue(self._validate(staging))


class TestParserFilenameAndBundledDeps(unittest.TestCase):
    def _parse_src(self, entry):
        return _parse_sources([entry], {}, Path("t/package.yml"))

    def test_multi_component_filename_rejected(self):
        with self.assertRaises(TemplateError):
            self._parse_src({"url": "https://x/y", "sha256": "a" * 64,
                             "filename": "sub/dir.tar.gz"})

    def test_dotdot_filename_rejected(self):
        with self.assertRaises(TemplateError):
            self._parse_src({"url": "https://x/y", "sha256": "a" * 64,
                             "filename": ".."})

    def test_hidden_filename_rejected(self):
        with self.assertRaises(TemplateError):
            self._parse_src({"url": "https://x/y", "sha256": "a" * 64,
                             "filename": ".hidden.tar.gz"})

    def test_plain_filename_passes(self):
        srcs = self._parse_src({"url": "https://x/y", "sha256": "a" * 64,
                                "filename": "clean-1.0.tar.gz"})
        self.assertEqual(srcs[0].filename, "clean-1.0.tar.gz")

    def _template(self, tmp: Path, bundled) -> Path:
        p = tmp / "package.yml"
        deps = "".join(f"  - {b!r}\n" for b in bundled)
        p.write_text(
            "name: demo\nversion: '1.0'\nrelease: 1\n"
            "description: d\nlicense: MIT\nbuild_style: custom\n"
            "source:\n  - url: https://x/y.tar.gz\n"
            f"    sha256: {'a' * 64}\n"
            f"bundled_deps:\n{deps}")
        return p

    def test_bundled_dest_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._template(Path(td), ["dep -> ../outside"])
            with self.assertRaises(TemplateError):
                parse_template(p)

    def test_bundled_dest_absolute_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._template(Path(td), ["dep -> /etc"])
            with self.assertRaises(TemplateError):
                parse_template(p)

    def test_bundled_empty_name_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._template(Path(td), ["  -> lib/dest"])
            with self.assertRaises(TemplateError):
                parse_template(p)

    def test_bundled_corpus_shape_passes(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._template(Path(td),
                               ["gmp-6.3.0 -> gcc-${version}/gmp"])
            pkg = parse_template(p)
            self.assertEqual(pkg.bundled_deps,
                             ["gmp-6.3.0 -> gcc-${version}/gmp"])


if __name__ == "__main__":
    unittest.main()
