"""Tests for E1.A.2 download-sources.py flags."""

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "download-sources.py"
sys.path.insert(0, str(_PROJECT_ROOT))

# Load module by file path (scripts/ has no __init__.py)
spec = importlib.util.spec_from_file_location("download_sources", SCRIPT_PATH)
_ds = importlib.util.module_from_spec(spec)
sys.modules["download_sources"] = _ds
spec.loader.exec_module(_ds)


class TestMirrorUploadFlag:
    def test_parse_user_host_path(self):
        user_host, path = _ds._parse_mirror_upload(
            "intergenos@origin.intergenstudios.com:/home/intergenos/repo/sources"
        )
        assert user_host == "intergenos@origin.intergenstudios.com"
        assert path == "/home/intergenos/repo/sources"

    def test_parse_empty_returns_empty(self):
        user_host, path = _ds._parse_mirror_upload("")
        assert user_host == ""
        assert path == ""

    def test_help_shows_mirror_upload(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "--mirror-upload" in result.stdout


class TestCheckUpdatesFlag:
    def test_no_updates_json_graceful(self, tmp_path):
        """--check-updates with missing updates.json should warn, not crash."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--check-updates",
             "--updates-json", str(tmp_path / "nonexistent.json")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "not found" in result.stdout

    def test_empty_updates_shows_none(self, tmp_path):
        updates = tmp_path / "updates.json"
        updates.write_text("[]")
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--check-updates",
             "--updates-json", str(updates)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "No packages with available updates" in result.stdout

    def test_with_use_latest_flag(self, tmp_path):
        """--check-updates --use-latest downloads to .latest/ dir."""
        updates = tmp_path / "updates.json"
        # Create a mock entry for a package in toolchain tier
        updates.write_text(json.dumps([{
            "pkg": "binutils-pass1",
            "current_ver": "2.44",
            "latest_ver": "2.45",
            "source_url": "https://ftp.gnu.org/gnu/binutils/binutils-2.45.tar.xz",
            "checked_at": "2026-05-12T12:00:00Z",
        }]))
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--check-updates", "--use-latest",
             "--updates-json", str(updates), "--tier", "toolchain"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "binutils-pass1" in result.stdout
        assert "2.44" in result.stdout
        assert "2.45" in result.stdout


class TestDefaultMirror:
    def test_default_mirror_value(self):
        assert "intergenos@origin.intergenstudios.com" in _ds.DEFAULT_MIRROR
        assert "/home/intergenos/repo/sources" in _ds.DEFAULT_MIRROR


class TestParseMirrorEdgeCases:
    def test_no_at_sign_handled(self):
        """value without @ but with : should still parse."""
        user_host, path = _ds._parse_mirror_upload("hostname:/path")
        assert user_host == "hostname"
        assert path == "/path"

    def test_no_colon_returns_empty(self):
        user_host, path = _ds._parse_mirror_upload("user@host")
        assert user_host == ""
        assert path == ""
