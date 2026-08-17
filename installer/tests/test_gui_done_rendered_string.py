# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Done screen — assert the RENDERED success string, not the diff.

The guidance correction once landed only in _build_body()'s default
description, which on_load() unconditionally overwrites before the page is
ever displayed — a diff review passed while the user kept seeing the retired
"you'll be prompted on first boot" promise. These tests drive the real
runtime sequence (_build_body -> on_load) against a recording StatusPage and
assert on the description that WINS, so a regression of either assignment —
or of the shared-constant discipline that keeps them identical — fails here.

No Gtk/Adw/display required: gi and the page base are stubbed before import.
"""

import sys
import types
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class _RecordingStatusPage:
    """Stands in for Adw.StatusPage; records the last value of each setter."""

    def __init__(self):
        self.icon_name = None
        self.title = None
        self.description = None

    def set_icon_name(self, value):
        self.icon_name = value

    def set_title(self, value):
        self.title = value

    def set_description(self, value):
        self.description = value


class _FakeForgePage:
    """Minimal _ForgePage surface DonePage.__init__ touches."""

    def __init__(self, window):
        self._window = window
        self.back_button = mock.Mock()
        self.next_button = mock.Mock()


def _tolerant_gi_module(name, **fixed):
    """A gi.repository.<name> stand-in: named base classes are real classes
    (so screen modules can subclass them at import time); everything else
    resolves to a fresh MagicMock via PEP 562 module __getattr__."""
    module = types.ModuleType(name)
    for attr, value in fixed.items():
        setattr(module, attr, value)
    module.__getattr__ = lambda attr: mock.MagicMock(name=f"{name}.{attr}")
    return module


def _import_done_page():
    """Import screens.done with gi + _base stubbed, fresh each call.

    The screens package __init__ imports every sibling screen, so the stubs
    must satisfy all of them at import time — subclassable bases for the
    widget classes, MagicMock for enum/const chains.
    """
    gi = types.ModuleType("gi")
    gi_repository = types.ModuleType("gi.repository")
    adw = _tolerant_gi_module(
        "Adw",
        StatusPage=_RecordingStatusPage,
        NavigationPage=type("NavigationPage", (), {}),
    )
    gtk = _tolerant_gi_module(
        "Gtk",
        Widget=type("Widget", (), {}),
        DrawingArea=type("DrawingArea", (), {}),
        Box=type("Box", (), {}),
    )
    gi_repository.Adw = adw
    gi_repository.Gtk = gtk
    gi_repository.GLib = _tolerant_gi_module("GLib")
    gi_repository.Gio = _tolerant_gi_module("Gio")
    gi.repository = gi_repository

    base = types.ModuleType("installer.frontend.gui.screens._base")
    base._ForgePage = _FakeForgePage
    base._toast = lambda *a, **k: None

    modules = {
        "gi": gi,
        "gi.repository": gi_repository,
        "installer.frontend.gui.screens._base": base,
    }
    # Import fresh under the stubs, then drop every module imported under
    # them from the cache so other tests never see the stub-built variants.
    stale = [m for m in sys.modules
             if m.startswith("installer.frontend.gui.screens")]
    for m in stale:
        sys.modules.pop(m, None)
    with mock.patch.dict(sys.modules, modules):
        import installer.frontend.gui.screens.done as done_module
        for m in [m for m in sys.modules
                  if m.startswith("installer.frontend.gui.screens")]:
            sys.modules.pop(m, None)
        return done_module


def _rendered_success_description(mok_chosen=True, capable=True,
                                  media_kind=None, done_module=None):
    """Drive _build_body -> on_load with the capability + venue probes
    pinned, and return the description that WINS at render time.

    capable is passed straight through as allows_mok_enrollment()'s
    return (True / False / None — the probe's full tri-state).
    """
    done_module = done_module or _import_done_page()
    page = done_module.DonePage(window=mock.Mock())
    page._build_body()
    state = types.SimpleNamespace(install_failed=False,
                                  install_error_message=None,
                                  mok_enrollment_chosen=mok_chosen)
    with mock.patch.object(done_module, "allows_mok_enrollment",
                           return_value=capable), \
         mock.patch.object(done_module, "live_media_kind",
                           return_value=media_kind):
        page.on_load(state)
    return page._status.description


class TestDoneSuccessRenderedString:
    def test_success_string_names_the_sb_reenable_trigger(self):
        """With BOTH display conditions met (machine can enroll + user set
        a passphrase), the rendered string must name the real trigger:
        re-enabling Secure Boot in firmware setup is what fires MokManager."""
        desc = _rendered_success_description(mok_chosen=True, capable=True)
        assert "Re-enable Secure Boot" in desc
        assert "MokManager" in desc

    def test_success_string_drops_the_retired_firstboot_promise(self):
        """The retired wording promised an unconditional first-boot prompt
        that never comes on an SB-off install."""
        desc = _rendered_success_description(mok_chosen=True, capable=True)
        assert "prompted to enroll" not in desc
        assert "vendor cert" not in desc

    def test_success_string_orders_reboot_before_media_removal(self):
        """Ejecting the media first squashfs-panics the live session; the
        TUI already prints the safe order — the GUI must match it."""
        desc = _rendered_success_description()
        assert desc.index("Reboot now") < desc.index("remove the install media")

    def test_no_reminder_when_enrollment_not_chosen(self):
        """Condition 2 unmet: user left the MOK passphrase blank — the
        Secure-Boot trip would be pointless, so no reminder renders."""
        desc = _rendered_success_description(mok_chosen=False, capable=True)
        assert "Secure Boot" not in desc
        assert "MokManager" not in desc

    def test_no_reminder_when_machine_cannot_enroll(self):
        """Condition 1 unmet (known-incapable, e.g. BIOS): the reminder
        would instruct an impossible firmware trip."""
        desc = _rendered_success_description(mok_chosen=True, capable=False)
        assert "Secure Boot" not in desc
        assert "MokManager" not in desc

    def test_no_reminder_when_capability_unknown(self):
        """The probe's None (EFI but unreadable machinery) must withhold
        the reminder — confident guidance keys on `is True` only."""
        desc = _rendered_success_description(mok_chosen=True, capable=None)
        assert "MokManager" not in desc

    def test_media_wording_usb(self):
        desc = _rendered_success_description(media_kind="usb")
        assert "USB install stick" in desc
        assert "install media" not in desc

    def test_media_wording_cdrom_names_the_vm_detach(self):
        desc = _rendered_success_description(media_kind="cdrom")
        assert "eject the install disc" in desc
        assert "detach the ISO" in desc

    def test_media_wording_unknown_falls_back_generic(self):
        desc = _rendered_success_description(media_kind=None)
        assert "remove the install media" in desc

    def test_default_and_success_assignments_cannot_drift(self):
        """Both assignments must consume the ONE composer — the structural
        guard against the dead-default-string regression. The default is
        the composer's conservative form; the success render must equal
        the composer's output for the same probed inputs."""
        done_module = _import_done_page()
        page = done_module.DonePage(window=mock.Mock())
        page._build_body()
        default_desc = page._status.description
        assert default_desc == done_module._success_description()
        rendered = _rendered_success_description(
            mok_chosen=True, capable=True, media_kind="usb",
            done_module=done_module)
        assert rendered == done_module._success_description(
            mok_reminder=True, media_kind="usb")

    def test_failure_path_unaffected(self):
        """The failure branch still renders its own error text."""
        done_module = _import_done_page()
        page = done_module.DonePage(window=mock.Mock())
        page._build_body()
        state = types.SimpleNamespace(install_failed=True,
                                      install_error_message="disk vanished")
        page.on_load(state)
        assert "disk vanished" in page._status.description
        assert "MokManager" not in page._status.description
