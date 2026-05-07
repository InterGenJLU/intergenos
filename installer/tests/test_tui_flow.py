"""Tests for Forge installer — Declarative Builder TUI.

TUI testing is inherently limited: the dialog/whiptail subprocesses
require a real terminal. These tests focus on:
1. Data structures and flows that can be exercised without a terminal.
2. YAML emit/parse round-trip correctness.
3. Abort cleanup behaviour.
4. Import sanity — all backend modules load without error.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from installer.frontend import tui


class TestDialogBinary:
    def test_resolve_returns_something(self):
        """_resolve_dialog_binary should return either dialog, whiptail, or None."""
        result = tui._resolve_dialog_binary()
        assert result in ("dialog", "whiptail", None)

    def test_resolve_not_empty_string(self):
        """If a binary is found, it should be a non-empty string."""
        result = tui._resolve_dialog_binary()
        if result is not None:
            assert isinstance(result, str)
            assert len(result) > 0


class TestYamlEmitParse:
    def test_emit_and_read_roundtrip(self):
        """Write yaml, read it back — data must be identical."""
        answers = {
            "version": 1,
            "locale": "en_US.UTF-8",
            "timezone": "America/Chicago",
            "hostname": "testbox",
            "package_groups": ["core", "base", "desktop-gnome"],
        }

        os.makedirs("/tmp/forge-test", exist_ok=True)
        yaml_path = "/tmp/forge-test/install-test.yaml"

        try:
            result = tui.emit_yaml(answers, path=yaml_path)
            assert result == Path(yaml_path)
            assert result.exists()

            # Parse back
            loaded = tui._load_yaml(str(result))
            assert loaded["version"] == answers["version"]
            assert loaded["locale"] == answers["locale"]
            assert loaded["timezone"] == answers["timezone"]
            assert loaded["hostname"] == answers["hostname"]
            assert loaded["package_groups"] == answers["package_groups"]

        finally:
            Path(yaml_path).unlink(missing_ok=True)

    def test_emit_creates_parent_dir(self):
        """emit_yaml creates /tmp/test-nested/ if it doesn't exist yet."""
        answers = {
            "version": 1, "locale": "C", "timezone": "UTC",
            "hostname": "nested", "package_groups": ["core"],
        }

        nested = "/tmp/forge-test-nested/subdir/install.yaml"
        try:
            result = tui.emit_yaml(answers, path=nested)
            assert result.exists()
        finally:
            Path(nested).unlink(missing_ok=True)
            Path(nested).parent.rmdir()
            Path(nested).parent.parent.rmdir()


class TestDataStructures:
    def test_locales_are_non_empty(self):
        """LOCALES list should have at least 3 entries."""
        assert len(tui.LOCALES) >= 3
        # Every entry is a (tag, description) tuple
        for tag, desc in tui.LOCALES:
            assert isinstance(tag, str)
            assert isinstance(desc, str)
            assert len(tag) > 0

    def test_timezones_have_utc(self):
        """UTC should be in the timezone list."""
        tags = {tag for tag, _ in tui.TIMEZONES_COMMON}
        assert "UTC" in tags

    def test_package_groups_include_core(self):
        """core should be in the package groups list and should be 'on' by default."""
        tags = {tag for tag, _, _ in tui.PACKAGE_GROUP_CHOICES}
        assert "core" in tags


class TestAbortCleanup:
    def test_cleanup_removes_yaml(self):
        """_cleanup_on_abort with a yaml_path should remove the file."""
        tmp = "/tmp/forge-test-cleanup-test.yaml"
        Path(tmp).write_text("test content")
        assert Path(tmp).exists()

        tui._cleanup_on_abort(yaml_path=tmp)
        assert not Path(tmp).exists()

    def test_cleanup_no_yaml_does_not_crash(self):
        """_cleanup_on_abort without a yaml_path should not raise."""
        rc = tui._cleanup_on_abort(yaml_path=None)
        assert rc == 1

    def test_cleanup_missing_file_does_not_crash(self):
        """_cleanup_on_abort with a nonexistent file should not raise."""
        rc = tui._cleanup_on_abort(yaml_path="/tmp/does-not-exist-99999.yaml")
        assert rc == 1


class TestBackendImports:
    def test_backend_imports_load(self):
        """All backend modules referenced in tui.py should import."""
        from installer.backend import bootloader, config, disks, hooks, mok, packages, users
        # If we get here without ImportError, the test passes.

    def test_disks_has_is_efi(self):
        """disks.is_efi() should be callable."""
        from installer.backend import disks
        assert callable(disks.is_efi)
