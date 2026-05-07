"""Tests for installer/frontend/gui/state.py — InstallerState transitions.

Validates the state-machine paths the GUI screens depend on:
  * `is_ready_for_install()` returns True only when all required fields
    are populated AND the destructive op is confirmed AND password
    confirms match.
  * `validation_errors()` produces a deterministic list of human-readable
    problems for the UI / Confirm screen toast surface.

Per dispatch (Phase 6 GUI 2026-05-07): rendering NOT tested. These tests
exercise InstallerState as a dataclass — no Gtk import, no display
required. Safe to run in CI without a Wayland session.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from installer.frontend.gui.state import InstallerState


def _populate_full(state):
    """Helper: fill in a fully-valid state for tests that need ready=True."""
    state.target_disk = "/dev/sda"
    state.confirm_destructive = True
    state.username = "user"
    state.user_password = "pw1234"
    state.user_password_confirm = "pw1234"
    state.root_password = "root1234"
    state.root_password_confirm = "root1234"
    state.hostname = "intergenos"


class TestDefaults:
    def test_default_state_not_ready_for_install(self):
        """Fresh state has neither disk nor passwords; should not be ready."""
        state = InstallerState()
        assert state.is_ready_for_install() is False

    def test_default_keymap_locale_timezone_match_tui(self):
        """Defaults match the TUI walking sequence proposal so a user who
        Next-through-everything lands on the same install as a TUI default."""
        state = InstallerState()
        assert state.keymap == "us"
        assert state.locale == "en_US.UTF-8"
        assert state.timezone == "UTC"
        assert state.hostname == "intergenos"

    def test_default_package_groups_include_core(self):
        """`core` must be in the default package groups (orchestrator
        rejects yaml that omits it)."""
        state = InstallerState()
        assert "core" in state.package_groups

    def test_default_install_completion_flags_clear(self):
        state = InstallerState()
        assert state.install_started is False
        assert state.install_completed is False
        assert state.install_failed is False
        assert state.install_error_message == ""


class TestIsReadyForInstall:
    def test_fully_populated_state_is_ready(self):
        state = InstallerState()
        _populate_full(state)
        assert state.is_ready_for_install() is True

    def test_missing_disk_blocks_ready(self):
        state = InstallerState()
        _populate_full(state)
        state.target_disk = None
        assert state.is_ready_for_install() is False

    def test_unconfirmed_destructive_blocks_ready(self):
        state = InstallerState()
        _populate_full(state)
        state.confirm_destructive = False
        assert state.is_ready_for_install() is False

    def test_missing_username_blocks_ready(self):
        state = InstallerState()
        _populate_full(state)
        state.username = ""
        assert state.is_ready_for_install() is False

    def test_user_password_mismatch_blocks_ready(self):
        state = InstallerState()
        _populate_full(state)
        state.user_password_confirm = "different"
        assert state.is_ready_for_install() is False

    def test_root_password_mismatch_blocks_ready(self):
        state = InstallerState()
        _populate_full(state)
        state.root_password_confirm = "different"
        assert state.is_ready_for_install() is False

    def test_empty_user_password_blocks_ready(self):
        state = InstallerState()
        _populate_full(state)
        state.user_password = ""
        state.user_password_confirm = ""
        assert state.is_ready_for_install() is False

    def test_empty_root_password_blocks_ready(self):
        state = InstallerState()
        _populate_full(state)
        state.root_password = ""
        state.root_password_confirm = ""
        assert state.is_ready_for_install() is False


class TestValidationErrors:
    def test_fully_populated_state_returns_no_errors(self):
        state = InstallerState()
        _populate_full(state)
        assert state.validation_errors() == []

    def test_default_state_lists_multiple_errors(self):
        """Empty state should surface every problem at once — orchestrator's
        aggregate-then-raise philosophy applied at the UI layer."""
        state = InstallerState()
        errors = state.validation_errors()
        # We don't assert exact count (could grow); we assert key items present
        joined = " | ".join(errors)
        assert "target disk" in joined
        assert "destructive" in joined
        assert "username" in joined

    def test_password_mismatch_surfaces_in_errors(self):
        state = InstallerState()
        _populate_full(state)
        state.user_password_confirm = "different"
        errors = state.validation_errors()
        assert any("user passwords don't match" in e for e in errors)

    def test_missing_core_surfaces_in_errors(self):
        """If a code path bypasses build_install_yaml's force-include, we
        should still surface 'core required' at the UI."""
        state = InstallerState()
        _populate_full(state)
        state.package_groups = ["base", "desktop-gnome"]
        errors = state.validation_errors()
        assert any("core" in e for e in errors)

    def test_root_password_mismatch_surfaces_in_errors(self):
        state = InstallerState()
        _populate_full(state)
        state.root_password_confirm = "different"
        errors = state.validation_errors()
        assert any("root passwords don't match" in e for e in errors)


class TestClearSensitiveData:
    """Coverage for InstallerState.clear_sensitive_data() — F4 fix-wave 2026-05-07.

    Defense-in-depth credential-zeroization called by ProgressPage in both
    success and failure terminal paths. Tests confirm all sensitive fields
    are cleared and non-sensitive fields are preserved.
    """

    def test_clears_user_password(self):
        state = InstallerState()
        _populate_full(state)
        state.user_password = "secret123"
        state.user_password_confirm = "secret123"
        state.clear_sensitive_data()
        assert state.user_password == ""
        assert state.user_password_confirm == ""

    def test_clears_root_password(self):
        state = InstallerState()
        _populate_full(state)
        state.root_password = "rootsecret"
        state.root_password_confirm = "rootsecret"
        state.clear_sensitive_data()
        assert state.root_password == ""
        assert state.root_password_confirm == ""

    def test_clears_mok_password(self):
        state = InstallerState()
        _populate_full(state)
        state.mok_password = "moksecret"
        state.clear_sensitive_data()
        assert state.mok_password == ""

    def test_preserves_username_hostname_after_clear(self):
        """Non-sensitive identity fields stay populated for the Done page summary."""
        state = InstallerState()
        _populate_full(state)
        state.username = "alice"
        state.hostname = "thinkpad"
        state.clear_sensitive_data()
        assert state.username == "alice"
        assert state.hostname == "thinkpad"

    def test_preserves_install_outcome_flags(self):
        """Don't disturb install_completed/install_failed/install_error_message."""
        state = InstallerState()
        state.install_completed = True
        state.install_failed = False
        state.install_error_message = ""
        state.clear_sensitive_data()
        assert state.install_completed is True
        assert state.install_failed is False
        assert state.install_error_message == ""

    def test_idempotent(self):
        """Calling twice should be safe (failure-path may double-call)."""
        state = InstallerState()
        _populate_full(state)
        state.clear_sensitive_data()
        state.clear_sensitive_data()
        assert state.user_password == ""
        assert state.root_password == ""


class TestStateMutationOrdering:
    """Sanity tests that state can be progressively built up across screens
    and ends up valid — mirrors the actual user flow through the 7 screens.
    """

    def test_progressive_walk_through_screens_yields_ready_state(self):
        state = InstallerState()
        # Welcome
        state.welcome_acked = True
        # Keyboard/Locale/Timezone
        state.keymap = "gb"
        state.locale = "en_GB.UTF-8"
        state.timezone = "Europe/London"
        # Disk
        state.target_disk = "/dev/nvme0n1"
        state.confirm_destructive = True
        # User
        state.hostname = "thinkpad"
        state.username = "alice"
        state.user_password = state.user_password_confirm = "pwpwpw"
        state.root_password = state.root_password_confirm = "rootroot"
        state.mok_password = "mokmok"
        # Confirm should now find state ready
        assert state.is_ready_for_install() is True
        assert state.validation_errors() == []

    def test_partial_walk_blocks_at_confirm(self):
        """User abandoned at User screen halfway — Confirm should reject."""
        state = InstallerState()
        state.target_disk = "/dev/sda"
        state.confirm_destructive = True
        state.username = "alice"
        # Forgot to fill in passwords
        assert state.is_ready_for_install() is False
        errors = state.validation_errors()
        assert any("user password" in e.lower() for e in errors)
