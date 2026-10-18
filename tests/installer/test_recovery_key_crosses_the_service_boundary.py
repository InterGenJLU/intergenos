# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The recovery key an encrypted GUI install generates reaches the Done page.

The GUI runs as an unprivileged user; the install runs as root in another
process, behind the system bus. The install's result crosses between them
once, as the dictionary of the Complete signal: installer/backend_service.py
builds it from the InstallResult (_result_to_dict, _emit_complete_dict) and
installer/frontend/gui/dbus_client.py reads it back (_result_dict_from_variant).
The disk phase generates the key and adds its unlock slot, the InstallResult
carries it, and the Done page renders it when the GUI's state holds it. The
dictionary was built without the field on both sides, so an encrypted install
with "Add a recovery key" on ended with a second unlock slot on the disk and a
Done page that showed no key.

These tests hold the field through the whole boundary as the two modules
really exchange it (the service's variant handed to the client's dispatcher),
the empty value when no key was asked for, the failure path's shape, an older
service that sends no field, and that the service logs nothing of the key. The
last test reads the shipped bus policy: who may receive the service's signals
at all is a fact of that file, not an assumption about the bus.
"""

import logging
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

from gi.repository import GLib

from installer import backend_service
from installer.backend.install import InstallResult
from installer.frontend.gui import dbus_client

# The shape disks.generate_recovery_key() produces: eight groups of five from
# an alphabet without the characters people transcribe wrongly.
KEY = "ABCDE-FGHJK-MNPQR-STUVW-XYZ23-45678-9ABCD-EFGHJ"
SERVICE_NAME = "org.intergenos.ForgeInstaller1"


class _BusThatRecords:
    """Stands in for the Gio.DBusConnection: keeps every emitted signal."""

    def __init__(self):
        self.emitted = []

    def emit_signal(self, destination, path, interface, name, params):
        self.emitted.append((destination, path, interface, name, params))


def _service_with_recording_bus():
    service = backend_service.ForgeInstallerService()
    service._bus = _BusThatRecords()
    return service


def _only_complete_signal(service):
    completes = [e for e in service._bus.emitted if e[3] == "Complete"]
    assert len(completes) == 1, completes
    return completes[0][4]


def _what_the_gui_receives(params):
    """Hand the emitted (sa{sv}) variant to the client's completion dispatcher
    exactly as Gio does, and return what the GUI's callback is given."""
    received = {}
    client = dbus_client.ForgeInstallerClient()
    client._complete_cb = lambda install_id, result: received.update(
        install_id=install_id, result=result,
    )
    client._dispatch_complete(None, None, None, None, None, params)
    return received


class TestTheKeyCrossesTheBoundary(unittest.TestCase):

    def test_the_key_the_install_generated_reaches_the_gui(self):
        result = InstallResult(success=True, phase_completed="cleanup")
        result.luks_recovery_key = KEY
        service = _service_with_recording_bus()
        service._emit_complete_dict("id-1", service._result_to_dict(result))
        params = _only_complete_signal(service)
        self.assertEqual(params.get_type_string(), "(sa{sv})")

        received = _what_the_gui_receives(params)
        self.assertEqual(received["install_id"], "id-1")
        self.assertEqual(received["result"]["luks_recovery_key"], KEY)
        self.assertTrue(received["result"]["success"])

        # progress.py wraps the dictionary as attributes and stores
        # getattr(result, "luks_recovery_key", "") on the GUI state the Done
        # page reads; the same read on what arrived must give the key.
        as_the_progress_page_reads_it = SimpleNamespace(**received["result"])
        self.assertEqual(
            getattr(as_the_progress_page_reads_it, "luks_recovery_key", ""),
            KEY,
        )

    def test_no_key_asked_for_reads_as_empty_never_as_an_error(self):
        service = _service_with_recording_bus()
        service._emit_complete_dict(
            "id-2", service._result_to_dict(InstallResult(success=True)),
        )
        received = _what_the_gui_receives(_only_complete_signal(service))
        self.assertEqual(received["result"]["luks_recovery_key"], "")

    def test_the_failure_path_carries_the_same_shape(self):
        service = _service_with_recording_bus()
        service._emit_complete("id-3", success=False, cancelled=False,
                               error="RuntimeError: disk phase failed")
        received = _what_the_gui_receives(_only_complete_signal(service))
        self.assertFalse(received["result"]["success"])
        self.assertIn("luks_recovery_key", received["result"])
        self.assertEqual(received["result"]["luks_recovery_key"], "")

    def test_an_older_service_that_sends_no_field_reads_as_none(self):
        variant = GLib.Variant("a{sv}", {"success": GLib.Variant("b", True)})
        self.assertEqual(
            dbus_client._result_dict_from_variant(variant)["luks_recovery_key"],
            "",
        )


class _RecordingHandler(logging.Handler):

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class TestNothingElseCarriesTheKey(unittest.TestCase):

    def test_the_service_logs_nothing_of_the_key(self):
        handler = _RecordingHandler()
        root = logging.getLogger()
        previous_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            result = InstallResult(success=True, phase_completed="cleanup")
            result.luks_recovery_key = KEY
            service = _service_with_recording_bus()
            # The disk phase's own progress line, as install.py words it.
            service._emit_progress("id-4", "partition", 3, 9,
                                   "recovery key added as a second unlock slot")
            service._emit_complete_dict("id-4", service._result_to_dict(result))
        finally:
            root.removeHandler(handler)
            root.setLevel(previous_level)

        # The progress mirror IS logged (the journal keeps the phases) ...
        self.assertTrue(
            any(m.startswith("Progress install=id-4") for m in handler.messages),
            handler.messages,
        )
        # ... and no log line carries the key.
        for message in handler.messages:
            self.assertNotIn(KEY, message)

        # Of everything the service emitted, only the Complete signal's
        # payload holds the key.
        for _dest, _path, _iface, name, params in service._bus.emitted:
            if name != "Complete":
                self.assertNotIn(KEY, repr(params.unpack()))

    def test_only_root_and_the_administrators_group_may_receive_the_service_messages(self):
        conf = (Path(backend_service.__file__).resolve().parent
                / "data" / f"{SERVICE_NAME}.conf")
        busconfig = ET.fromstring(conf.read_text(encoding="utf-8"))
        policies = busconfig.findall("policy")

        default = [p for p in policies if p.get("context") == "default"]
        self.assertEqual(len(default), 1, "exactly one default policy")
        self.assertIn(
            SERVICE_NAME,
            [d.get("receive_sender") for d in default[0].findall("deny")],
            "the default policy must deny receiving the service's messages",
        )

        may_receive = {
            (p.get("user"), p.get("group"))
            for p in policies
            if any(a.get("receive_sender") == SERVICE_NAME
                   for a in p.findall("allow"))
        }
        self.assertEqual(may_receive, {("root", None), (None, "wheel")})


if __name__ == "__main__":
    unittest.main()
