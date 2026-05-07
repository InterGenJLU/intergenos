"""Tests for installer/frontend/gui/state.py — yaml + install_io accumulation.

The GUI's Confirm→Progress transition hands the collected InstallerState
to the Phase 4 backend orchestrator (`installer.backend.install.run_install`)
via two derived shapes:

  * `build_install_yaml()` / `write_install_yaml()` — yaml-schema-v1 dict
    written to disk and consumed by `run_install`'s `load_yaml_config`.
  * `to_install_io()` — interactive-collected disk + passwords dict.

These tests exercise the contract shape so a future refactor (e.g. schema
v2) catches divergence at test time rather than at install time.

Importantly: we round-trip the yaml through the orchestrator's own
`load_yaml_config` + `validate_install_inputs` to confirm GUI-emitted yaml
is accepted by the orchestrator unchanged. That's the integration point
that matters most.

Per dispatch (Phase 6 GUI 2026-05-07): rendering NOT tested.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from installer.backend.install import (
    REQUIRED_INSTALL_IO_FIELDS,
    REQUIRED_YAML_FIELDS,
    load_yaml_config,
    validate_install_inputs,
)
from installer.frontend.gui.state import (
    DEFAULT_YAML_PATH,
    YAML_SCHEMA_VERSION,
    InstallerState,
)


def _populate_full(state):
    state.target_disk = "/dev/sda"
    state.confirm_destructive = True
    state.username = "user"
    state.user_password = "pw1234"
    state.user_password_confirm = "pw1234"
    state.root_password = "root1234"
    state.root_password_confirm = "root1234"
    state.hostname = "intergenos"


class TestBuildInstallYaml:
    def test_includes_all_required_fields(self):
        """Every field in REQUIRED_YAML_FIELDS must be present in the
        emitted dict — that's the orchestrator's validate contract."""
        state = InstallerState()
        cfg = state.build_install_yaml()
        for field in REQUIRED_YAML_FIELDS:
            assert field in cfg, f"missing required yaml field: {field}"

    def test_includes_schema_version(self):
        state = InstallerState()
        cfg = state.build_install_yaml()
        assert cfg["version"] == YAML_SCHEMA_VERSION

    def test_uses_state_values(self):
        state = InstallerState()
        state.locale = "fr_FR.UTF-8"
        state.timezone = "Europe/Paris"
        state.hostname = "machine"
        state.keymap = "fr"
        cfg = state.build_install_yaml()
        assert cfg["locale"] == "fr_FR.UTF-8"
        assert cfg["timezone"] == "Europe/Paris"
        assert cfg["hostname"] == "machine"
        assert cfg["keymap"] == "fr"

    def test_force_includes_core_when_user_un_toggled(self):
        """`core` must be in package_groups even if the user removed it.
        Orchestrator validate_install_inputs rejects yaml without core."""
        state = InstallerState()
        state.package_groups = ["base", "desktop-gnome"]  # no core
        cfg = state.build_install_yaml()
        assert "core" in cfg["package_groups"]

    def test_dedupes_package_groups(self):
        state = InstallerState()
        state.package_groups = ["core", "base", "core", "extra"]
        cfg = state.build_install_yaml()
        # Should be deduped
        assert cfg["package_groups"].count("core") == 1
        assert "base" in cfg["package_groups"]
        assert "extra" in cfg["package_groups"]

    def test_package_groups_sorted_for_determinism(self):
        """Sorted output makes the yaml stable across runs — important for
        diffing emitted yaml in test/debug contexts."""
        state = InstallerState()
        state.package_groups = ["extra", "core", "base"]
        cfg = state.build_install_yaml()
        assert cfg["package_groups"] == sorted(cfg["package_groups"])

    def test_keymap_emitted_as_orchestrator_extension(self):
        """schema v1 doesn't document keymap; orchestrator falls back to
        'us' if absent. GUI emits keymap deliberately so the user's
        Keyboard screen choice flows through to config.generate_all."""
        state = InstallerState()
        state.keymap = "de"
        cfg = state.build_install_yaml()
        assert cfg.get("keymap") == "de"

    def test_emits_dict_not_string(self):
        state = InstallerState()
        cfg = state.build_install_yaml()
        assert isinstance(cfg, dict)


class TestWriteInstallYaml:
    def test_writes_parent_dir_creation(self):
        """Should mkdir parents=True like the TUI's emit_yaml does."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "nested" / "subdir" / "install.yaml"
            state = InstallerState()
            result = state.write_install_yaml(target)
            assert result == target
            assert target.exists()

    def test_round_trip_via_orchestrator_load(self):
        """write_install_yaml → orchestrator's load_yaml_config produces
        the same dict back. Round-trip integration with the orchestrator."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "install.yaml"
            state = InstallerState()
            state.locale = "ja_JP.UTF-8"
            state.timezone = "Asia/Tokyo"
            state.hostname = "kyoto"
            state.write_install_yaml(target)

            loaded = load_yaml_config(target)
            assert loaded["locale"] == "ja_JP.UTF-8"
            assert loaded["timezone"] == "Asia/Tokyo"
            assert loaded["hostname"] == "kyoto"
            assert loaded["package_groups"] == sorted(state.package_groups)

    def test_orchestrator_validates_emitted_yaml(self):
        """Emitted yaml + populated install_io must pass the orchestrator's
        validate_install_inputs without raising. Regression-guard against
        schema drift: if the orchestrator adds a required field, this
        test fails until the GUI's build_install_yaml is updated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "install.yaml"
            state = InstallerState()
            _populate_full(state)
            state.write_install_yaml(target)

            cfg = load_yaml_config(target)
            install_io = state.to_install_io()
            # Should not raise
            validate_install_inputs(cfg, install_io)

    def test_writes_header_comments(self):
        """Forge-emitted yaml has provenance header — useful for debugging
        which frontend wrote a given /var/lib/forge/install.yaml."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "install.yaml"
            state = InstallerState()
            state.write_install_yaml(target)
            content = target.read_text(encoding="utf-8")
            assert content.startswith("#")
            assert "Forge" in content

    def test_default_path_constant_matches_tui(self):
        """Both frontends should write to the same path so the live overlay
        ends up with one canonical install.yaml regardless of dispatch."""
        from installer.frontend.tui import YAML_PATH as tui_yaml_path
        assert DEFAULT_YAML_PATH == tui_yaml_path


class TestToInstallIo:
    def test_includes_required_fields(self):
        state = InstallerState()
        _populate_full(state)
        io = state.to_install_io()
        for field in REQUIRED_INSTALL_IO_FIELDS:
            assert field in io, f"missing required install_io field: {field}"

    def test_uses_state_values(self):
        state = InstallerState()
        state.target_disk = "/dev/nvme0n1"
        state.username = "alice"
        state.user_password = "userpw"
        state.root_password = "rootpw"
        io = state.to_install_io()
        assert io["disk"] == "/dev/nvme0n1"
        assert io["username"] == "alice"
        assert io["user_password"] == "userpw"
        assert io["root_password"] == "rootpw"

    def test_omits_mok_password_when_empty(self):
        """Empty mok_password should not surface in io — orchestrator's
        EFI branch only queues enrollment when mok_password is truthy."""
        state = InstallerState()
        _populate_full(state)
        state.mok_password = ""
        io = state.to_install_io()
        assert "mok_password" not in io

    def test_includes_mok_password_when_set(self):
        state = InstallerState()
        _populate_full(state)
        state.mok_password = "mokmokmok"
        io = state.to_install_io()
        assert io["mok_password"] == "mokmokmok"

    def test_omits_install_mode_when_default_fresh(self):
        """`fresh` is the default; the orchestrator doesn't need it
        explicitly. Only forward when the user picked alongside (not yet
        wired but kept in the contract for the future flow)."""
        state = InstallerState()
        _populate_full(state)
        state.install_mode = "fresh"
        io = state.to_install_io()
        assert "install_mode" not in io

    def test_includes_install_mode_when_alongside(self):
        state = InstallerState()
        _populate_full(state)
        state.install_mode = "alongside"
        io = state.to_install_io()
        assert io.get("install_mode") == "alongside"


class TestToRunInstallKwargs:
    def test_bundles_all_required_args(self):
        state = InstallerState()
        _populate_full(state)
        cb = lambda *a: None
        kwargs = state.to_run_install_kwargs(
            "/var/lib/forge/install.yaml",
            "/var/lib/igos/archives",
            packages_dir="/usr/share/intergenos/packages",
            progress_callback=cb,
            dry_run=True,
        )
        assert kwargs["yaml_path"] == "/var/lib/forge/install.yaml"
        assert kwargs["archive_dir"] == "/var/lib/igos/archives"
        assert kwargs["packages_dir"] == "/usr/share/intergenos/packages"
        assert kwargs["progress_callback"] is cb
        assert kwargs["dry_run"] is True
        assert "install_io" in kwargs
        assert kwargs["install_io"]["disk"] == "/dev/sda"

    def test_omits_target_when_none(self):
        """If caller doesn't pass target, kwargs shouldn't override the
        orchestrator's DEFAULT_TARGET."""
        state = InstallerState()
        _populate_full(state)
        kwargs = state.to_run_install_kwargs(
            "/x.yaml", "/y", progress_callback=None, dry_run=False
        )
        assert "target" not in kwargs

    def test_includes_target_when_set(self):
        state = InstallerState()
        _populate_full(state)
        kwargs = state.to_run_install_kwargs(
            "/x.yaml", "/y", target="/custom/target",
            progress_callback=None, dry_run=False,
        )
        assert kwargs["target"] == "/custom/target"

    def test_handles_none_packages_dir(self):
        state = InstallerState()
        _populate_full(state)
        kwargs = state.to_run_install_kwargs(
            "/x.yaml", "/y", packages_dir=None,
            progress_callback=None, dry_run=False,
        )
        assert kwargs["packages_dir"] is None

    def test_handles_none_archive_dir(self):
        state = InstallerState()
        _populate_full(state)
        kwargs = state.to_run_install_kwargs(
            "/x.yaml", None, progress_callback=None, dry_run=False,
        )
        assert kwargs["archive_dir"] is None

    def test_str_coerces_path_objects(self):
        """yaml_path / archive_dir / packages_dir often arrive as Path()
        objects; orchestrator wants strings."""
        state = InstallerState()
        _populate_full(state)
        kwargs = state.to_run_install_kwargs(
            Path("/x.yaml"), Path("/y"), packages_dir=Path("/z"),
            progress_callback=None, dry_run=False,
        )
        assert isinstance(kwargs["yaml_path"], str)
        assert isinstance(kwargs["archive_dir"], str)
        assert isinstance(kwargs["packages_dir"], str)


class TestSchemaParity:
    """Cross-check GUI-emitted yaml against the documented schema in
    `installer/data/install-schema.yaml`. If schema docs say `version: 1`
    has these fields, GUI must emit those fields with compatible types.
    """

    def test_emitted_yaml_is_safe_loadable(self):
        """yaml.safe_load of GUI-written yaml succeeds (no python tag/blob)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "install.yaml"
            state = InstallerState()
            _populate_full(state)
            state.write_install_yaml(target)

            loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
            assert isinstance(loaded, dict)

    def test_package_groups_is_list_not_string(self):
        """Schema says package_groups is a list of strings, not a comma-
        separated string. Catch a typo-style regression early."""
        state = InstallerState()
        cfg = state.build_install_yaml()
        assert isinstance(cfg["package_groups"], list)
        for group in cfg["package_groups"]:
            assert isinstance(group, str)

    def test_emitted_locale_is_string(self):
        state = InstallerState()
        cfg = state.build_install_yaml()
        assert isinstance(cfg["locale"], str)
        assert "." in cfg["locale"]  # locale strings contain charset suffix
