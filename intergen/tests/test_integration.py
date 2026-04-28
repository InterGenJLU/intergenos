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
        # Start
        success = self.mgr.start(
            self.model.local_path,
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

    def test_manage_packages_graceful(self):
        from intergen.tools.manage_packages import ManagePackagesTool
        tool = ManagePackagesTool()
        result = tool.execute({"action": "list"})
        # pkm not installed yet — should fail gracefully
        self.assertFalse(result.success)
        self.assertIn("not installed", result.content.lower())


class TestDBusDaemon(unittest.TestCase):
    """Test D-Bus daemon initializes all subsystems."""

    def test_daemon_startup(self):
        from intergen.dbus_daemon import InterGenDaemon
        daemon = InterGenDaemon()
        daemon.start_service()

        status = json.loads(daemon.status())
        self.assertTrue(status["running"])
        self.assertIsNotNone(status["tier"])
        self.assertEqual(status["tier"]["level"], 2)  # expected on a typical 16GB-class development host

        tier_info = json.loads(daemon.get_tier())
        self.assertIn("level", tier_info)
        self.assertIn("ram_gb", tier_info)

        # Ask returns skeleton response
        response = json.loads(daemon.ask("test"))
        self.assertIn("response", response)

        daemon.stop_service()


class TestCLI(unittest.TestCase):
    """Test CLI commands work."""

    def test_status(self):
        import subprocess
        repo_root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [sys.executable, "-m", "intergen.cli", "status"],
            capture_output=True, text=True, timeout=10,
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
