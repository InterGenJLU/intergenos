# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Game-launch pause — the daemon side of handing the machine to a game.

Decided 2026-08-04: when a game starts, InterGen's default is to stop its model
servers so the video memory AND the system memory they held go back to the
machine, and to load them again when the last game window closes.

These tests exercise the daemon mechanism in isolation — no D-Bus, no real
llama-server, no GPU, nothing interactive — and cover the failures that would
actually hurt a user:

  * resuming while a second game is still running (the reference count),
  * a watchdog restarting a server behind the pause (both watchdogs),
  * a pause spending the restart budget, so that a few games in one session
    would leave InterGen unable to come back at all,
  * a pause outliving whatever placed it, leaving the assistant stopped with
    nothing alive to resume it,
  * "paused on purpose" being indistinguishable from "no model loaded".
"""

from __future__ import annotations

import json
import threading
import unittest
from unittest import mock

from intergen.dbus_daemon import INTROSPECTION_XML, InterGenDaemon


class _FakeManager:
    """Stands in for LlamaManager: counts the lifecycle calls that matter."""

    def __init__(self, start_ok: bool = True) -> None:
        self.stop_calls = 0
        self.start_saved_calls = 0
        self.restart_calls = 0
        self.running = True
        self.last_error = "fake did not start"
        self._start_ok = start_ok

    def stop(self) -> None:
        self.stop_calls += 1
        self.running = False

    def start_saved_config(self) -> bool:
        self.start_saved_calls += 1
        self.running = self._start_ok
        return self._start_ok

    def restart(self) -> bool:
        self.restart_calls += 1
        self.running = True
        return True

    def is_running(self) -> bool:
        return self.running


def _daemon(chat=None, embed=None):
    """A daemon with only the fields the pause mechanism touches.

    Bypasses __init__ the same way the other daemon unit tests do — the point is
    the pause logic, not daemon startup.
    """
    d = InterGenDaemon.__new__(InterGenDaemon)
    d._paused = False
    d._pause_holds = []
    d._pause_owner_watches = {}
    d._pause_lock = threading.Lock()
    d._llama = chat
    d._embed_llama = embed
    d._matcher = None
    d._mm = None
    d._bus = None
    d._config = {}
    d._requests_handled = 0
    d._router = None
    # Only needed by status(), which reports the whole daemon.
    d._running = True
    d._hardware_tier = None
    d._model_loaded = None
    d._last_error = None
    d._model_server_integrity_failure = None
    d._review_autopilot = None
    d._tools = None
    d._memory = None
    d._watchdog = None
    d._metrics = None
    return d


class TestPauseAndResume(unittest.TestCase):
    def setUp(self):
        # glass writes into the user's real state directory; a unit test must
        # never touch it, so the emitter is replaced for the whole class.
        patcher = mock.patch("intergen.dbus_daemon.glass.emit")
        self.glass = patcher.start()
        self.addCleanup(patcher.stop)

    def test_pause_stops_both_servers(self):
        chat, embed = _FakeManager(), _FakeManager()
        d = _daemon(chat, embed)
        out = json.loads(d.pause_for_game("steam_app_620"))
        self.assertTrue(out["paused"])
        self.assertEqual(out["games"], ["steam_app_620"])
        self.assertEqual(out["stopped"], {"chat": True, "embedding": True})
        self.assertEqual(chat.stop_calls, 1)
        self.assertEqual(embed.stop_calls, 1)
        self.assertTrue(d._paused)

    def test_resume_loads_both_servers_again(self):
        chat, embed = _FakeManager(), _FakeManager()
        d = _daemon(chat, embed)
        d.pause_for_game("steam_app_620")
        out = json.loads(d.resume_after_game("steam_app_620"))
        self.assertFalse(out["paused"])
        self.assertEqual(out["games"], [])
        self.assertEqual(chat.start_saved_calls, 1)
        self.assertFalse(d._paused)

    def test_second_game_holds_the_pause_until_it_also_exits(self):
        """The reference count: the first game to exit must NOT resume InterGen
        while another is still running, or the accelerator is handed back
        mid-session."""
        chat = _FakeManager()
        d = _daemon(chat)
        d.pause_for_game("steam_app_620")
        d.pause_for_game("gamescope")
        self.assertEqual(chat.stop_calls, 1, "only the first pause stops")

        still = json.loads(d.resume_after_game("steam_app_620"))
        self.assertTrue(still["paused"])
        self.assertEqual(still["games"], ["gamescope"])
        self.assertEqual(chat.start_saved_calls, 0)

        done = json.loads(d.resume_after_game("gamescope"))
        self.assertFalse(done["paused"])
        self.assertEqual(chat.start_saved_calls, 1)

    def test_two_windows_of_the_same_game_are_counted_separately(self):
        chat = _FakeManager()
        d = _daemon(chat)
        d.pause_for_game("steam_app_620")
        d.pause_for_game("steam_app_620")
        self.assertTrue(json.loads(d.resume_after_game("steam_app_620"))["paused"])
        self.assertEqual(chat.start_saved_calls, 0)
        self.assertFalse(json.loads(d.resume_after_game("steam_app_620"))["paused"])
        self.assertEqual(chat.start_saved_calls, 1)

    def test_resume_never_spends_the_restart_budget(self):
        """A pause is not a fault. Going through restart() would spend the
        manager's lifetime restart budget, so a handful of games in one session
        would leave InterGen unable to come back."""
        chat = _FakeManager()
        d = _daemon(chat)
        for i in range(5):
            d.pause_for_game(f"steam_app_{i}")
            d.resume_after_game(f"steam_app_{i}")
        self.assertEqual(chat.restart_calls, 0)
        self.assertEqual(chat.start_saved_calls, 5)

    def test_resume_naming_an_unknown_game_releases_the_oldest_hold(self):
        """A caller that renamed a window between the two edges must not be able
        to strand the pause forever."""
        chat = _FakeManager()
        d = _daemon(chat)
        d.pause_for_game("steam_app_620")
        out = json.loads(d.resume_after_game("something-else"))
        self.assertFalse(out["paused"])
        self.assertEqual(chat.start_saved_calls, 1)

    def test_resume_with_nothing_held_says_so_and_starts_nothing(self):
        chat = _FakeManager()
        d = _daemon(chat)
        out = json.loads(d.resume_after_game("steam_app_620"))
        self.assertFalse(out["paused"])
        self.assertEqual(out["detail"], "no pause was held")
        self.assertEqual(chat.start_saved_calls, 0)

    def test_pause_works_with_no_servers_at_all(self):
        """A machine that never provisioned a model still answers honestly
        rather than raising at the caller."""
        d = _daemon(None, None)
        out = json.loads(d.pause_for_game("steam_app_620"))
        self.assertTrue(out["paused"])
        self.assertEqual(out["stopped"], {"chat": False, "embedding": False})
        self.assertFalse(json.loads(d.resume_after_game("steam_app_620"))["paused"])

    def test_empty_game_identifier_is_accepted_and_named(self):
        d = _daemon(_FakeManager())
        out = json.loads(d.pause_for_game("   "))
        self.assertEqual(out["games"], ["a game"])
        self.assertFalse(json.loads(d.resume_after_game("   "))["paused"])


class TestPauseAgainstTheWatchdogs(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("intergen.dbus_daemon.glass.emit")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_chat_watchdog_will_not_restart_behind_a_pause(self):
        chat = _FakeManager()
        d = _daemon(chat)
        d.pause_for_game("steam_app_620")
        self.assertFalse(d._restart_chat_server())
        self.assertEqual(chat.restart_calls, 0)

    def test_chat_watchdog_restarts_normally_when_not_paused(self):
        chat = _FakeManager()
        d = _daemon(chat)
        self.assertTrue(d._restart_chat_server())
        self.assertEqual(chat.restart_calls, 1)

    def test_embed_watchdog_will_not_restart_behind_a_pause(self):
        chat, embed = _FakeManager(), _FakeManager()
        d = _daemon(chat, embed)
        called = []
        d._start_embed_server_and_recover_intents = lambda: called.append(1) or True
        d.pause_for_game("steam_app_620")
        self.assertFalse(d._restart_embed_server_watchdog())
        self.assertEqual(called, [])

    def test_embed_watchdog_restarts_normally_when_not_paused(self):
        d = _daemon(_FakeManager(), _FakeManager())
        called = []
        d._start_embed_server_and_recover_intents = lambda: called.append(1) or True
        self.assertTrue(d._restart_embed_server_watchdog())
        self.assertEqual(called, [1])


class TestPauseHolderDisappears(unittest.TestCase):
    """A hold belongs to a living caller. The real case is the desktop shell
    restarting or crashing while a game is open: its holds can never be released
    by it again, so the daemon releases them itself."""

    def setUp(self):
        patcher = mock.patch("intergen.dbus_daemon.glass.emit")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_vanished_owner_releases_its_holds_and_resumes(self):
        chat = _FakeManager()
        d = _daemon(chat)
        d.pause_for_game("steam_app_620", owner=":1.42")
        self.assertTrue(d._paused)
        d._on_pause_owner_vanished(":1.42")
        self.assertFalse(d._paused)
        self.assertEqual(chat.start_saved_calls, 1)

    def test_vanished_owner_leaves_another_owners_hold_alone(self):
        chat = _FakeManager()
        d = _daemon(chat)
        d.pause_for_game("steam_app_620", owner=":1.42")
        d.pause_for_game("gamescope", owner=":1.99")
        d._on_pause_owner_vanished(":1.42")
        self.assertTrue(d._paused)
        self.assertEqual(d._held_game_names(), ["gamescope"])
        self.assertEqual(chat.start_saved_calls, 0)

    def test_vanishing_owner_that_holds_nothing_changes_nothing(self):
        chat = _FakeManager()
        d = _daemon(chat)
        d.pause_for_game("steam_app_620", owner=":1.42")
        d._on_pause_owner_vanished(":1.7")
        self.assertTrue(d._paused)
        self.assertEqual(chat.start_saved_calls, 0)


class TestPauseIsVisible(unittest.TestCase):
    """"Paused on purpose" and "no model loaded" must never look the same."""

    def setUp(self):
        patcher = mock.patch("intergen.dbus_daemon.glass.emit")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_ask_answers_that_it_is_paused_without_reaching_the_router(self):
        d = _daemon(_FakeManager())
        d._router = object()  # would raise if the paused guard did not return
        d.pause_for_game("steam_app_620")
        out = json.loads(d.ask("what is my battery level"))
        self.assertEqual(out["source"], "paused")
        self.assertFalse(out["handled"])
        self.assertIn("steam_app_620", out["response"])

    def test_ask_resumes_normal_routing_after_the_game_exits(self):
        d = _daemon(_FakeManager())
        d.pause_for_game("steam_app_620")
        d.resume_after_game("steam_app_620")
        d._router = None  # the ordinary starting-up path
        out = json.loads(d.ask("hello"))
        self.assertEqual(out["source"], "startup")

    def test_status_reports_the_pause_and_who_holds_it(self):
        chat = _FakeManager()
        chat.offload_report = lambda: {}
        d = _daemon(chat)
        # glass_enabled() constructs the real logger, which creates the user's
        # state directory — a unit test must not, so it is stubbed here too.
        with mock.patch("intergen.dbus_daemon.glass.glass_enabled",
                        return_value=True):
            before = json.loads(d.status())
            self.assertFalse(before["paused"])
            self.assertEqual(before["paused_for"], [])
            d.pause_for_game("steam_app_620")
            during = json.loads(d.status())
        self.assertTrue(during["paused"])
        self.assertEqual(during["paused_for"], ["steam_app_620"])
        # The server really is down; the pause field is what tells a reader that
        # this is deliberate rather than a failure to load a model.
        self.assertFalse(during["components"]["llama_server"])

    def test_dbus_interface_declares_both_edges(self):
        self.assertIn('<method name="PauseForGame">', INTROSPECTION_XML)
        self.assertIn('<method name="ResumeAfterGame">', INTROSPECTION_XML)

    def test_the_interface_xml_still_parses(self):
        """A malformed edit here does not fail loudly at the edit — it fails at
        runtime, where _export_dbus catches the error and the daemon runs with
        NO D-Bus interface at all. Parsing it with the same parser the daemon
        uses turns that into a test failure."""
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio
        except (ImportError, ValueError) as e:
            self.skipTest(f"GLib/Gio not importable here: {e}")
        info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        iface = info.interfaces[0]
        methods = {m.name: m for m in iface.methods}
        for name in ("PauseForGame", "ResumeAfterGame"):
            self.assertIn(name, methods)
            self.assertEqual([a.signature for a in methods[name].in_args], ["s"])
            self.assertEqual([a.signature for a in methods[name].out_args], ["s"])
        # The methods that were already there must survive the addition.
        for name in ("Ask", "Escalate", "Status", "GetTier",
                     "ResetConversation"):
            self.assertIn(name, methods)


class TestStartSavedConfig(unittest.TestCase):
    """The relaunch path the pause uses, in LlamaManager itself.

    It exists so a resume can bring back exactly the server that was running
    without going through restart(), whose budget is there to stop a genuinely
    broken server being relaunched forever.
    """

    def _manager_with_config(self):
        from intergen.llama_manager import LlamaManager, ServerConfig
        mgr = LlamaManager()
        mgr._config = ServerConfig(
            model_path="/models/pinned.gguf", port=8080, context_size=16384,
            gpu_layers=999, parallel=2, jinja=True, reasoning="off",
            embedding=False, cache_reuse=256, cacheable=True,
            mmproj_path="/models/mmproj.gguf",
            chat_template_file="/templates/tools.jinja", has_vision=True,
            expect_tools=True, expect_offload=True, device="Vulkan1",
            server_path="/usr/bin/llama-server",
        )
        return mgr

    def test_every_saved_field_reaches_start(self):
        """The drift guard. A field added to ServerConfig and forgotten on the
        relaunch path is exactly how expect_offload was once dropped, leaving
        the offload check unfired on every watchdog restart."""
        import dataclasses
        mgr = self._manager_with_config()
        seen = {}

        def _capture(model_path, **kwargs):
            seen["model_path"] = model_path
            seen.update(kwargs)
            return True

        mgr.start = _capture
        self.assertTrue(mgr.start_saved_config())
        for field in dataclasses.fields(mgr._config):
            self.assertIn(field.name, seen,
                          f"{field.name} is saved but never reaches start()")
            self.assertEqual(seen[field.name], getattr(mgr._config, field.name),
                             f"{field.name} reached start() with a different value")

    def test_start_saved_config_does_not_spend_the_restart_budget(self):
        mgr = self._manager_with_config()
        mgr.start = lambda *a, **k: True
        before = mgr._restart_count
        for _ in range(10):
            self.assertTrue(mgr.start_saved_config())
        self.assertEqual(mgr._restart_count, before)

    def test_restart_still_spends_the_budget_and_stops_first(self):
        mgr = self._manager_with_config()
        order = []
        mgr.start = lambda *a, **k: order.append("start") or True
        mgr.stop = lambda: order.append("stop")
        self.assertTrue(mgr.restart())
        self.assertEqual(order, ["stop", "start"])
        self.assertEqual(mgr._restart_count, 1)

    def test_no_saved_config_refuses_and_says_why(self):
        from intergen.llama_manager import LlamaManager
        mgr = LlamaManager()
        self.assertFalse(mgr.start_saved_config())
        self.assertIn("No previous configuration", mgr.last_error or "")


if __name__ == "__main__":
    unittest.main()
