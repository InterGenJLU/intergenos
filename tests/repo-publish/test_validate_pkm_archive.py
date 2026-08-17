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
