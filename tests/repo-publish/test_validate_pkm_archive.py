"""Tests for validate-pkm-archive.py — silent failure detection."""

import importlib
import sys
import tarfile
import io
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "validate-pkm-archive.py"
sys.path.insert(0, str(_PROJECT_ROOT))

spec = importlib.util.spec_from_file_location("validate_pkm_archive", SCRIPT_PATH)
_vpa = importlib.util.module_from_spec(spec)
sys.modules["validate_pkm_archive"] = _vpa
spec.loader.exec_module(_vpa)


def _make_archive(path, files):
    """Create a valid .igos.tar.gz with given file entries.

    files: list of (arcname, content, is_dir) tuples.
    """
    with tarfile.open(path, "w:gz") as tar:
        for arcname, content, is_dir in files:
            if is_dir:
                info = tarfile.TarInfo(name=arcname)
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info = tarfile.TarInfo(name=arcname)
                data = content.encode() if isinstance(content, str) else content
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))


class TestValidateArchive:
    def test_normal_autotools_passes(self, tmp_path):
        archive = tmp_path / "normpkg-1.0.igos.tar.gz"
        _make_archive(archive, [
            ("usr/lib/", None, True),
            ("usr/lib/libfoo.so", "ELF\x02...", False),
            ("usr/bin/foo", "#!/bin/sh", False),
        ])
        cfg = _vpa.load_config()
        result = _vpa.validate_archive(archive, cfg)
        assert result is None

    def test_empty_payload_suspect(self, tmp_path):
        archive = tmp_path / "empty-1.0.igos.tar.gz"
        _make_archive(archive, [])
        cfg = _vpa.load_config()
        result = _vpa.validate_archive(archive, cfg)
        assert result is not None
        assert any("payload" in i.lower() for i in result["issues"])

    def test_small_autotools_suspect(self, tmp_path, monkeypatch):
        archive = tmp_path / "smallpkg-1.0.igos.tar.gz"
        _make_archive(archive, [
            ("usr/bin/tiny", "tiny", False),
        ])
        monkeypatch.setattr(_vpa, "get_build_style", lambda n: "autotools")
        cfg = _vpa.load_config()
        cfg["min_size_bytes"] = 10_000_000  # 10MB — our tiny archive will fail
        result = _vpa.validate_archive(archive, cfg)
        assert result is not None
        assert any("size" in i.lower() for i in result["issues"])

    def test_small_custom_bypasses_size_check(self, tmp_path, monkeypatch):
        archive = tmp_path / "custompkg-1.0.igos.tar.gz"
        _make_archive(archive, [
            ("usr/bin/tool", "custom", False),
        ])
        cfg = _vpa.load_config()
        # Override get_build_style to return custom
        monkeypatch.setattr(_vpa, "get_build_style", lambda n: "custom")
        result = _vpa.validate_archive(archive, cfg)
        # Should NOT be flagged for size (custom type skips size check)
        if result:
            assert not any("size" in i.lower() for i in result["issues"])


class TestLoadConfig:
    def test_defaults(self):
        cfg = _vpa.load_config()
        assert cfg["min_size_bytes"] == 200000
        assert "usr/lib" in cfg["payload_dirs"]

    def test_custom_yaml(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("min_size_bytes: 500000\n")
        cfg = _vpa.load_config(str(cfg_file))
        assert cfg["min_size_bytes"] == 500000


class TestReleaseCarryingNames:
    """Since 2026-09-22 an archive is named <name>-<version>-<release>.igos.tar.gz.
    The validator split the name at the last hyphen, which then kept the
    version in it (apparmor-3.1.7-1 read as apparmor-3.1.7); no recipe matched,
    the build style came back empty and the size check was skipped for every
    archive, without a word. The name is now read by pkm/archive_names.py
    against the versions the real recipes state."""

    @staticmethod
    def _compiled_recipe():
        import yaml
        for pkg_yml in sorted((_PROJECT_ROOT / "packages").glob("*/*/package.yml")):
            data = yaml.safe_load(pkg_yml.read_text()) or {}
            if (data.get("build_style") in _vpa.COMPILED_STYLES
                    and data.get("name") == pkg_yml.parent.name
                    and "-" not in str(data.get("version"))):
                return data
        raise AssertionError("the real tree has a compiled-style recipe")

    def test_the_name_is_read_the_way_the_producers_write_it(self):
        from pkm.archive_names import archive_filename
        recipe = self._compiled_recipe()
        name, version = recipe["name"], str(recipe["version"])
        for release in (recipe["release"], None):
            arc = archive_filename(name, version, release)
            assert _vpa.archive_package_name(Path(arc)) == name, arc

    def test_a_tiny_compiled_archive_named_with_its_release_is_suspect(self, tmp_path):
        from pkm.archive_names import archive_filename
        recipe = self._compiled_recipe()
        archive = tmp_path / archive_filename(
            recipe["name"], str(recipe["version"]), recipe["release"])
        _make_archive(archive, [("usr/bin/tiny", "tiny", False)])
        result = _vpa.validate_archive(archive, _vpa.load_config())
        assert result is not None, "the size check must fire"
        assert result["pkg_name"] == recipe["name"]
        assert result["build_style"] == recipe["build_style"]
        assert any("size" in i.lower() for i in result["issues"])


class TestMembersNamedTheWayTheProducersNameThem:
    """Every producer archives its staging tree with `tar -C <dir> .`, so a
    real member is ./usr/bin/x. The payload check compared that raw name with
    usr/bin/ and matched nothing: every real archive read as having no payload,
    which buried any real finding under one per archive."""

    def test_a_dot_slash_payload_member_is_payload(self, tmp_path):
        archive = tmp_path / "normpkg-1.0-1.igos.tar.gz"
        _make_archive(archive, [
            ("./usr/lib/", None, True),
            ("./usr/lib/libfoo.so", "ELF\x02...", False),
        ])
        with tarfile.open(archive, "r:gz") as tar:
            assert _vpa.has_real_payload(tar, _vpa.PAYLOAD_DIRS)

    def test_an_archive_written_by_tar_itself_is_read_the_same_way(self, tmp_path):
        import subprocess
        staging = tmp_path / "staging"
        (staging / "usr/bin").mkdir(parents=True)
        (staging / "usr/bin/tool").write_text("#!/bin/sh\n")
        archive = tmp_path / "tool-1.0-1.igos.tar.gz"
        subprocess.run(["tar", "-C", str(staging), "-czf", str(archive), "."],
                       check=True)
        with tarfile.open(archive, "r:gz") as tar:
            assert any(m.name == "./usr/bin/tool" for m in tar.getmembers())
            assert _vpa.has_real_payload(tar, _vpa.PAYLOAD_DIRS)


class TestAnArchiveItCannotRead:
    """An archive the validator cannot read to its end is reported as
    unreadable, with the others, and the run still writes its report.

    Measured 2026-09-22 by a second reader: the unreadable branch called the
    size as a method, so the first unreadable archive ended the run in a
    TypeError and no report was written. Measured while correcting it: an
    archive cut short raises EOFError, which fell to the catch-all branch
    whose entry lacked the fields the report writer reads; and an archive
    missing only its last bytes passed as readable, because the members were
    all there and nothing read the stream to its end."""

    SHAPES = ("empty", "cut short", "missing its last bytes", "not gzip",
              "not tar")

    @staticmethod
    def _shape(tmp_path, shape):
        import gzip
        import subprocess
        staging = tmp_path / "staging"
        (staging / "usr/bin").mkdir(parents=True)
        (staging / "usr/bin/tool").write_bytes(b"\x7fELF" + bytes(range(256)) * 400)
        real = tmp_path / "real.igos.tar.gz"
        subprocess.run(["tar", "-C", str(staging), "-czf", str(real), "."],
                       check=True)
        data = real.read_bytes()
        return {"empty": b"",
                "cut short": data[: len(data) // 2],
                "missing its last bytes": data[:-8],
                "not gzip": b"not a gzip stream\n" * 64,
                "not tar": gzip.compress(b"not a tar archive\n" * 100)}[shape]

    @pytest.mark.parametrize("shape", SHAPES)
    def test_it_is_reported_unreadable(self, tmp_path, shape):
        data = self._shape(tmp_path, shape)
        archive = tmp_path / "demo-1.0-1.igos.tar.gz"
        archive.write_bytes(data)
        result = _vpa.validate_archive(archive, _vpa.load_config())
        assert result is not None, f"an archive {shape} passed"
        assert len(result["issues"]) == 1, result
        assert result["issues"][0].startswith("unreadable archive: "), result
        assert result["size"] == len(data), result
        assert result["pkg_name"] == "demo", result
        assert result["name"] == "demo-1.0-1", result

    def test_the_run_reports_them_with_the_others(self, tmp_path):
        import json
        import subprocess
        archives = tmp_path / "archives"
        archives.mkdir()
        for i, shape in enumerate(self.SHAPES):
            (archives / f"demo{i}-1.0-1.igos.tar.gz").write_bytes(
                self._shape(tmp_path / f"s{i}", shape))
        _make_archive(archives / "good-1.0-1.igos.tar.gz",
                      [("./usr/bin/good", "#!/bin/sh\n", False)])
        out = tmp_path / "out"
        run = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--archives-dir", str(archives),
             "-o", str(out)], capture_output=True, text=True)
        assert run.returncode == 1, run.stderr
        assert "Traceback" not in run.stderr, run.stderr
        assert run.stderr.count("unreadable archive: ") == 5, run.stderr
        assert "OK: good-1.0-1.igos.tar.gz" in run.stderr, run.stderr
        (report,) = out.glob("pkm-archive-validation-*.json")
        findings = json.loads(report.read_text())
        assert (findings["total"], findings["passed"], findings["suspects"]) == (6, 1, 5)
        (tsv,) = out.glob("pkm-archive-validation-*.tsv")
        rows = tsv.read_text().splitlines()[1:]
        assert len(rows) == 5 and all("unreadable archive: " in r for r in rows), rows
