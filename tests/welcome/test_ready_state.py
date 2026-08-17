"""Unit tests for the intergen-welcome Meet-InterGen ready-state engine probe.

Covers the decision logic added 2026-07-11 (the development8 §8-eval fix): the
daemon Status -> engine-state mapping, the three display states (setup / ready /
starting), and the inconclusive-probe discipline mirrored from the panel
extension's _checkReady. GTK widget construction (the card builders /
build_intergen_page) needs a display and is exercised by the local render proof,
not here — these tests are pure and run headless.
"""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, GLib  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WELCOME_PY = REPO_ROOT / "assets" / "intergen-welcome" / "intergen-welcome.py"

_spec = importlib.util.spec_from_file_location("intergen_welcome", WELCOME_PY)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)


class TestEngineStateFromJson(unittest.TestCase):
    """Status (s) JSON payload -> 'ready'/'down' (the components.llama_server
    gate the panel extension uses)."""

    def test_llama_server_true_is_ready(self):
        self.assertEqual(
            welcome._engine_state_from_json('{"components": {"llama_server": true}}'),
            "ready")

    def test_llama_server_false_is_down(self):
        self.assertEqual(
            welcome._engine_state_from_json('{"components": {"llama_server": false}}'),
            "down")

    def test_no_llama_server_key_is_down(self):
        self.assertEqual(
            welcome._engine_state_from_json('{"components": {}}'), "down")

    def test_no_components_key_is_down(self):
        self.assertEqual(welcome._engine_state_from_json('{}'), "down")

    def test_malformed_json_raises(self):
        # The caller (_intergen_engine_state_blocking) turns this into 'inconclusive'.
        with self.assertRaises(Exception):
            welcome._engine_state_from_json('not json')


class TestNextShownState(unittest.TestCase):
    """The extension.js _checkReady inconclusive discipline, as a pure function."""

    def test_definitive_ready_from_nothing(self):
        self.assertEqual(welcome._next_shown_state(None, "ready"), "ready")

    def test_definitive_down_from_nothing(self):
        self.assertEqual(welcome._next_shown_state(None, "down"), "down")

    def test_inconclusive_from_nothing_defaults_down(self):
        # Nothing shown yet -> honest not-ready default.
        self.assertEqual(welcome._next_shown_state(None, "inconclusive"), "down")

    def test_inconclusive_does_not_flip_shown_ready(self):
        # THE core guard: a busy daemon (inconclusive) must not downgrade ready.
        self.assertEqual(welcome._next_shown_state("ready", "inconclusive"), "ready")

    def test_inconclusive_keeps_shown_down(self):
        self.assertEqual(welcome._next_shown_state("down", "inconclusive"), "down")

    def test_definitive_down_downgrades_ready(self):
        # A definitive engine-down (daemon answered false, or name gone) DOES
        # downgrade a shown ready card — mirrors the extension's _hideIndicator.
        self.assertEqual(welcome._next_shown_state("ready", "down"), "down")

    def test_ready_upgrades_from_down(self):
        self.assertEqual(welcome._next_shown_state("down", "ready"), "ready")


class _FakeBus:
    def __init__(self, exc=None, payload=None):
        self._exc = exc
        self._payload = payload

    def call_sync(self, *a, **k):
        if self._exc is not None:
            raise self._exc
        return GLib.Variant("(s)", (self._payload,))


class TestEngineStateBlocking(unittest.TestCase):
    """The D-Bus probe's mapping of replies/errors to states, mocking the bus."""

    def setUp(self):
        self._orig = welcome.Gio.bus_get_sync

    def tearDown(self):
        welcome.Gio.bus_get_sync = self._orig

    def _run(self, fake):
        welcome.Gio.bus_get_sync = lambda *a, **k: fake
        return welcome._intergen_engine_state_blocking()

    def test_ready_reply(self):
        self.assertEqual(
            self._run(_FakeBus(payload='{"components":{"llama_server":true}}')),
            "ready")

    def test_down_reply(self):
        self.assertEqual(
            self._run(_FakeBus(payload='{"components":{"llama_server":false}}')),
            "down")

    def test_service_unknown_is_down(self):
        # Daemon not running at all -> definitive down (the extension's
        # name-vanished path).
        e = GLib.Error.new_literal(Gio.dbus_error_quark(), "no service",
                                   int(Gio.DBusError.SERVICE_UNKNOWN))
        self.assertEqual(self._run(_FakeBus(exc=e)), "down")

    def test_name_has_no_owner_is_down(self):
        e = GLib.Error.new_literal(Gio.dbus_error_quark(), "no owner",
                                   int(Gio.DBusError.NAME_HAS_NO_OWNER))
        self.assertEqual(self._run(_FakeBus(exc=e)), "down")

    def test_timeout_is_inconclusive(self):
        # Daemon busy inferring, can't answer Status -> inconclusive (no flip).
        e = GLib.Error.new_literal(Gio.dbus_error_quark(), "timeout",
                                   int(Gio.DBusError.TIMEOUT))
        self.assertEqual(self._run(_FakeBus(exc=e)), "inconclusive")

    def test_no_reply_is_inconclusive(self):
        e = GLib.Error.new_literal(Gio.dbus_error_quark(), "no reply",
                                   int(Gio.DBusError.NO_REPLY))
        self.assertEqual(self._run(_FakeBus(exc=e)), "inconclusive")

    def test_malformed_payload_is_inconclusive(self):
        self.assertEqual(self._run(_FakeBus(payload='not json')), "inconclusive")


class TestIntergenIsSetUp(unittest.TestCase):
    """The store read that selects the SETUP state (store empty) vs a set-up
    machine — unchanged behavior, kept green alongside the new engine probe.
    Monkeypatches os.listdir/getsize since the store path is hardcoded."""

    def setUp(self):
        self._listdir = welcome.os.listdir
        self._getsize = welcome.os.path.getsize

    def tearDown(self):
        welcome.os.listdir = self._listdir
        welcome.os.path.getsize = self._getsize

    def _set(self, names, sizes):
        welcome.os.listdir = lambda p: names
        welcome.os.path.getsize = lambda p: sizes.get(os.path.basename(p), 1)

    def test_empty_store_not_set_up(self):
        # SETUP state: store empty -> first-run setup card path.
        self._set([], {})
        self.assertFalse(welcome._intergen_is_set_up())

    def test_only_companions_not_set_up(self):
        # embedding + projector present but no chat model -> not set up.
        self._set(["nomic-embed-text.gguf", "mmproj-model.gguf"],
                  {"nomic-embed-text.gguf": 100, "mmproj-model.gguf": 100})
        self.assertFalse(welcome._intergen_is_set_up())

    def test_chat_model_is_set_up(self):
        self._set(["InternVL3_5.gguf"], {"InternVL3_5.gguf": 1000})
        self.assertTrue(welcome._intergen_is_set_up())

    def test_zero_byte_model_not_set_up(self):
        self._set(["model.gguf"], {"model.gguf": 0})
        self.assertFalse(welcome._intergen_is_set_up())


if __name__ == "__main__":
    unittest.main()
