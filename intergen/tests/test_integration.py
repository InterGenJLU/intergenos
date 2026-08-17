# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Integration test — full InterGen stack on real hardware.

Run AFTER merging both branches (intergen-port + intergen-tools).
Validates: hardware detection → model loading → llama-server → tool
registry → router → tool execution → inference response.

Usage:
    python3 -m intergen.tests.test_integration

Requires:
    - llama-server installed (/usr/local/bin/llama-server)
    - At least one model downloaded (/var/lib/intergen/models/llm/)
"""

from __future__ import annotations

import json
import logging
import sys
import time
import unittest
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
log = logging.getLogger("integration")

# On-hardware integration suite (see module docstring): it exercises a REAL
# llama-server against REAL downloaded models. On a host without the
# provisioned stack these tests used to FAIL under plain pytest collection —
# indistinguishable from genuine regressions (the 22-failure wall,
# 2026-07-06). Skip loudly instead; the suite runs in full on provisioned
# fleet hardware.
import shutil as _shutil

_LLAMA_BIN = (
    _shutil.which("llama-server")
    or next((p for p in ("/usr/local/bin/llama-server", "/usr/bin/llama-server")
             if Path(p).exists()), None)
)
_MODELS_DIR = Path("/var/lib/intergen/models/llm")
_HAVE_MODELS = _MODELS_DIR.is_dir() and any(_MODELS_DIR.glob("*.gguf"))
requires_provisioned_stack = unittest.skipUnless(
    _LLAMA_BIN and _HAVE_MODELS,
    "on-hardware integration suite: requires installed llama-server + "
    "downloaded models (run on a provisioned fleet box)",
)


@requires_provisioned_stack
class TestHardwareToModelPipeline(unittest.TestCase):
    """Test: hardware detect → model select → model exists."""

    def test_tier_to_model(self):
        from intergen.hardware import HardwareDetector
        from intergen.model_manager import ModelManager

        detector = HardwareDetector()
        tier = detector.detect()
        log.info("Hardware: Tier %d, %.1f GB RAM, GPU=%s",
                 tier.tier.value, tier.ram_gb, tier.gpu_vendor)

        mm = ModelManager()
        model = mm.get_model_for_tier(tier.tier)
        log.info("Recommended: %s %s (%.1f GB)", model.name, model.quant, model.size_gb)

        # At minimum Tier 1 model should be downloaded from pipeline test
        downloaded = mm.list_downloaded()
        self.assertGreater(len(downloaded), 0, "No models downloaded")
        log.info("Downloaded models: %d", len(downloaded))


@requires_provisioned_stack
class TestLlamaServerLifecycle(unittest.TestCase):
    """Test: start → health → inference → stop."""

    def setUp(self):
        from intergen.llama_manager import LlamaManager
        self.mgr = LlamaManager()

        # Find a downloaded LLM (not embedding model)
        from intergen.model_manager import ModelManager
        mm = ModelManager()
        models = mm.list_downloaded()
        llm_models = [m for m in models if "embed" not in m.name.lower()]
        self.assertGreater(len(llm_models), 0,
                           "No LLM models downloaded (embedding models can't serve chat)")

        # Use the smallest LLM
        self.model = min(llm_models, key=lambda m: m.size_gb)
        log.info("Using model: %s (%s)", self.model.name, self.model.local_path)

    def test_server_lifecycle(self):
        # Start — on a free ephemeral port: this box's resident daemon
        # legitimately holds 8080, and the manager's foreign-holder guard
        # correctly refuses it (decided 2026-07-24: the suite never assumes
        # exclusive ownership of the production port).
        from intergen.tests.test_llama_launch import _free_port
        success = self.mgr.start(
            self.model.local_path,
            port=_free_port(),
            context_size=2048,
            gpu_layers=0,
        )
        self.assertTrue(success, f"Server failed to start: {self.mgr._last_error}")

        try:
            # Health
            health = self.mgr.health()
            self.assertTrue(health.running)
            self.assertTrue(health.model_loaded)
            log.info("Health: running=%s, model_loaded=%s", health.running, health.model_loaded)

            # Inference
            endpoint = self.mgr.get_endpoint()
            req_data = json.dumps({
                "model": "test",
                "messages": [
                    {"role": "user", "content": "Say hello in exactly 3 words."}
                ],
                "max_tokens": 200,
                "temperature": 0.1,
            }).encode()

            req = urllib.request.Request(
                endpoint,
                data=req_data,
                headers={"Content-Type": "application/json"},
            )

            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())

            choices = result.get("choices", [])
            self.assertGreater(len(choices), 0, "No choices in response")

            msg = choices[0].get("message", {})
            content = msg.get("content", "")
            reasoning = msg.get("reasoning_content", "")

            # Qwen3.5 may put everything in reasoning_content
            has_output = bool(content.strip()) or bool(reasoning.strip())
            self.assertTrue(has_output, "Both content and reasoning_content are empty")

            usage = result.get("usage", {})
            log.info("Inference: %d prompt + %d completion tokens",
                     usage.get("prompt_tokens", 0),
                     usage.get("completion_tokens", 0))
            if content.strip():
                log.info("Response: %s", content.strip()[:100])
            if reasoning.strip():
                log.info("Reasoning: %s...", reasoning.strip()[:100])

        finally:
            # Always stop the server
            self.mgr.stop()
            self.assertFalse(self.mgr.is_running())
            log.info("Server stopped cleanly")


@requires_provisioned_stack
class TestToolExecution(unittest.TestCase):
    """Test all 7 tools execute correctly on real system."""

    def test_run_command(self):
        from intergen.tools.run_command import RunCommandTool
        tool = RunCommandTool()
        result = tool.execute({"command": "uname -a"})
        self.assertTrue(result.success)
        self.assertIn("intergenos", result.content.lower())

    def test_read_file(self):
        from intergen.tools.read_file import ReadFileTool
        tool = ReadFileTool()
        result = tool.execute({"path": "/etc/os-release"})
        self.assertTrue(result.success)

    def test_write_file(self):
        from intergen.tools.write_file import WriteFileTool
        import tempfile
        tool = WriteFileTool()
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            path = f.name
        try:
            result = tool.execute({"path": path, "content": "integration test\n"})
            self.assertTrue(result.success)
            self.assertEqual(Path(path).read_text(), "integration test\n")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_manage_services(self):
        from intergen.tools.manage_services import ManageServicesTool
        tool = ManageServicesTool()
        result = tool.execute({"action": "is-active", "service": "NetworkManager"})
        self.assertTrue(result.success)

    def test_open_application_list(self):
        from intergen.tools.open_application import OpenApplicationTool
        tool = OpenApplicationTool()
        result = tool.execute({"list_apps": True})
        self.assertTrue(result.success)
        self.assertIn("Installed applications", result.content)

    def test_manage_packages_graceful_when_pkm_absent(self):
        # The pkm-absent guard must fail GRACEFULLY (clean message, success=False).
        # Mock absence so this is deterministic — pkm is a shipped InterGenOS
        # component now, so relying on the environment lacking it (the old test's
        # premise) goes red on every real install.
        from unittest.mock import patch
        from intergen.tools.manage_packages import ManagePackagesTool
        tool = ManagePackagesTool()
        with patch("intergen.tools.manage_packages.shutil.which", return_value=None):
            result = tool.execute({"action": "list"})
        self.assertFalse(result.success)
        self.assertIn("not installed", result.content.lower())

    def test_manage_packages_list_when_pkm_present(self):
        # When pkm IS present (the real InterGenOS state), `list` succeeds and
        # returns the package listing — never the absent-guard message.
        import shutil
        from intergen.tools.manage_packages import ManagePackagesTool
        if shutil.which("pkm") is None:
            self.skipTest("pkm not installed in this environment")
        result = ManagePackagesTool().execute({"action": "list"})
        self.assertTrue(result.success)
        self.assertNotIn("pkm is not installed", result.content.lower())


@requires_provisioned_stack
class TestDBusDaemon(unittest.TestCase):
    """Test D-Bus daemon initializes all subsystems."""

    def test_daemon_startup(self):
        # A unique per-run bus name: the resident production daemon owns
        # com.intergenos.InterGen on this box, and the single-instance guard
        # correctly refuses a duplicate (decided 2026-07-24: the suite never
        # assumes exclusive ownership of the production bus name). The guard
        # itself stays under test — it is the refusal path the resident daemon
        # exercised when this test collided with it.
        import os
        import socket
        from unittest import mock
        from intergen import dbus_daemon as dd
        from intergen.config import Config
        test_name = f"com.intergenos.InterGenTest{os.getpid()}"

        # The SAME reasoning as the bus name, applied to the SERVING PORTS.
        # start_service step 3 launches the model servers on the FIXED
        # llama_server.port / .embedding_port (8080/8081) — the ports the
        # resident daemon serves on. Standing a second daemon up on them made
        # this test contend for the live serving sockets: measured on a box
        # running a persistence battery, the resident daemon's restarts then hit
        # "port 8080 not yet bindable — a prior socket has not released" and came
        # up model-degraded. Handing the test daemon two ephemeral ports keeps it
        # a REAL daemon start (nothing is mocked away) while making the test
        # incapable of disturbing whatever is serving on the box.
        def _free_port() -> int:
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                return s.getsockname()[1]

        chat_port, embed_port = _free_port(), _free_port()
        real_get = Config.get

        def _get(self, key, default=None):
            if key == "llama_server.port":
                return chat_port
            if key == "llama_server.embedding_port":
                return embed_port
            return real_get(self, key, default)

        with mock.patch.object(dd, "SERVICE_NAME", test_name), \
                mock.patch.object(Config, "get", _get):
            daemon = dd.InterGenDaemon()
            daemon.start_service()

            status = json.loads(daemon.status())
            self.assertTrue(status["running"])
            self.assertIsNotNone(status["tier"])
            # Tier is HARDWARE-RESOLVED — a hardcoded expected level encodes
            # one box class (the old ==2 was false on any serving-class host).
            # Assert the two reporting surfaces agree and the level is valid.
            tier_info = json.loads(daemon.get_tier())
            self.assertIn("level", tier_info)
            self.assertIn("ram_gb", tier_info)
            self.assertIn(status["tier"]["level"], (1, 2, 3))
            self.assertEqual(status["tier"]["level"], tier_info["level"])

            # Ask returns skeleton response
            response = json.loads(daemon.ask("test"))
            self.assertIn("response", response)

            # The direct calls above never cross the bus, so they cannot
            # prove the registered dispatch path. Round-trip Status over the
            # bus so on_method_call -> invocation.return_value is exercised —
            # the surface register_object_with_closures2 owns. The call MUST
            # be async under a running main loop: the daemon registered from
            # this thread, so its dispatch fires only when this thread's main
            # context iterates — a call_sync here blocks that context and
            # deadlocks to a client-side timeout (measured, first wedge run).
            import time
            import warnings
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
            bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            holder = {}

            def _on_reply(source, res):
                try:
                    holder["reply"] = source.call_finish(res)
                except Exception as e:  # noqa: BLE001 — surfaced via assert
                    holder["error"] = e

            bus.call(
                test_name, dd.OBJECT_PATH, dd.INTERFACE_NAME, "Status",
                None, GLib.VariantType("(s)"),
                Gio.DBusCallFlags.NONE, 5000, None, _on_reply,
            )
            # Pump the default context until the reply lands. The timeout
            # source is the anti-hang guard — it wakes a blocked iteration so
            # the deadline is always re-checked, and the assertion reports
            # the failure instead of wedging the run.
            #
            # Scoped third-party warning disposition, NOT a mask of our own
            # surface: PyGObject's GLib override consults the asyncio
            # event-loop policy on every iteration; that API is deprecated on
            # Python 3.14 (removal slated 3.16), so gi/events.py and
            # gi/_ossighelper.py emit DeprecationWarnings that are
            # PyGObject's to fix upstream. Only those two gi modules are
            # filtered; a warning from intergen code still surfaces.
            GLib.timeout_add_seconds(8, lambda: False)
            ctx = GLib.MainContext.default()
            deadline = time.monotonic() + 8
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore", category=DeprecationWarning,
                    module=r"gi\.(events|_ossighelper)")
                while not holder and time.monotonic() < deadline:
                    ctx.iteration(True)
            self.assertIn(
                "reply", holder,
                f"bus round-trip failed: {holder.get('error', 'no reply')}")
            bus_status = json.loads(holder["reply"].unpack()[0])
            self.assertTrue(bus_status["running"])

            daemon.stop_service()


@requires_provisioned_stack
class TestCLI(unittest.TestCase):
    """Test CLI commands work."""

    def test_status(self):
        import subprocess
        repo_root = Path(__file__).resolve().parents[2]
        # The bound is an anti-hang guard, NOT a latency contract. `status` calls
        # the live daemon over D-Bus with cli.try_dbus's own 5s budget; when the
        # daemon is busy serving, that whole budget is spent before the CLI
        # prints its (correct, loud) "daemon is busy" banner and exits 0. Add
        # interpreter start + the intergen.cli import chain (~1.4s measured idle)
        # and a 10s bound leaves under 5s of headroom — which a full-suite run on
        # a live box, driving the same daemon from other tests, does not have.
        # The assertions below are what this test actually guarantees; only the
        # arbitrary wall bound moved.
        result = subprocess.run(
            [sys.executable, "-m", "intergen.cli", "status"],
            capture_output=True, text=True, timeout=60,
            cwd=str(repo_root),
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("InterGen Status", result.stdout)

    def test_tier(self):
        import subprocess
        repo_root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [sys.executable, "-m", "intergen.cli", "tier"],
            capture_output=True, text=True, timeout=10,
            cwd=str(repo_root),
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Hardware Tier", result.stdout)

    def test_tools(self):
        import subprocess
        repo_root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [sys.executable, "-m", "intergen.cli", "tools"],
            capture_output=True, text=True, timeout=10,
            cwd=str(repo_root),
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("run_command", result.stdout)


if __name__ == "__main__":
    print("=" * 60)
    print("InterGen Integration Test Suite")
    print("Running on real InterGenOS hardware")
    print("=" * 60)
    print()
    unittest.main(verbosity=2)
