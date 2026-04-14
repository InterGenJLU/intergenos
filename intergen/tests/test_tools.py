"""Test suite for InterGen core tools.

Briefing requirement: 20 read-only (auto), 10 write (confirm), 10 destructive (blocked).
Tests run against the real InterGenOS system — that's our advantage.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from intergen.interfaces.types import SafetyTier
from intergen.tools.run_command import RunCommandTool
from intergen.tools.read_file import ReadFileTool
from intergen.tools.write_file import WriteFileTool
from intergen.tools.manage_packages import ManagePackagesTool
from intergen.tools.manage_services import ManageServicesTool
from intergen.tools.open_application import OpenApplicationTool


class TestRunCommandSafety(unittest.TestCase):
    """Test run_command safety classification — the most critical tool."""

    def setUp(self):
        self.tool = RunCommandTool()

    def _classify(self, cmd: str) -> SafetyTier:
        return self.tool.classify_safety({"command": cmd})

    # === 20 AUTO (read-only) commands ===

    def test_auto_ls(self):
        self.assertEqual(self._classify("ls -la"), SafetyTier.AUTO)

    def test_auto_cat(self):
        self.assertEqual(self._classify("cat /etc/hostname"), SafetyTier.AUTO)

    def test_auto_grep(self):
        self.assertEqual(self._classify("grep -r error /var/log"), SafetyTier.AUTO)

    def test_auto_df(self):
        self.assertEqual(self._classify("df -h"), SafetyTier.AUTO)

    def test_auto_ps(self):
        self.assertEqual(self._classify("ps aux"), SafetyTier.AUTO)

    def test_auto_uname(self):
        self.assertEqual(self._classify("uname -a"), SafetyTier.AUTO)

    def test_auto_free(self):
        self.assertEqual(self._classify("free -h"), SafetyTier.AUTO)

    def test_auto_uptime(self):
        self.assertEqual(self._classify("uptime"), SafetyTier.AUTO)

    def test_auto_whoami(self):
        self.assertEqual(self._classify("whoami"), SafetyTier.AUTO)

    def test_auto_hostname(self):
        self.assertEqual(self._classify("hostname"), SafetyTier.AUTO)

    def test_auto_date(self):
        self.assertEqual(self._classify("date"), SafetyTier.AUTO)

    def test_auto_id(self):
        self.assertEqual(self._classify("id"), SafetyTier.AUTO)

    def test_auto_pwd(self):
        self.assertEqual(self._classify("pwd"), SafetyTier.AUTO)

    def test_auto_which(self):
        self.assertEqual(self._classify("which python3"), SafetyTier.AUTO)

    def test_auto_lsblk(self):
        self.assertEqual(self._classify("lsblk"), SafetyTier.AUTO)

    def test_auto_systemctl_status(self):
        self.assertEqual(self._classify("systemctl status sshd"), SafetyTier.AUTO)

    def test_auto_systemctl_is_active(self):
        self.assertEqual(self._classify("systemctl is-active NetworkManager"), SafetyTier.AUTO)

    def test_auto_pipe_read_only(self):
        self.assertEqual(self._classify("ls | grep txt"), SafetyTier.AUTO)

    def test_auto_sudo_read(self):
        self.assertEqual(self._classify("sudo ls /root"), SafetyTier.AUTO)

    def test_auto_env_prefix(self):
        self.assertEqual(self._classify("env HOME=/tmp ls"), SafetyTier.AUTO)

    # === 10 CONFIRM (write) commands ===

    def test_confirm_mkdir(self):
        self.assertEqual(self._classify("mkdir /tmp/test"), SafetyTier.CONFIRM)

    def test_confirm_cp(self):
        self.assertEqual(self._classify("cp file1 file2"), SafetyTier.CONFIRM)

    def test_confirm_mv(self):
        self.assertEqual(self._classify("mv old new"), SafetyTier.CONFIRM)

    def test_confirm_chmod(self):
        self.assertEqual(self._classify("chmod 755 script.sh"), SafetyTier.CONFIRM)

    def test_confirm_touch(self):
        self.assertEqual(self._classify("touch newfile"), SafetyTier.CONFIRM)

    def test_confirm_git(self):
        self.assertEqual(self._classify("git commit -m test"), SafetyTier.CONFIRM)

    def test_confirm_rm_single(self):
        self.assertEqual(self._classify("rm tempfile"), SafetyTier.CONFIRM)

    def test_confirm_pip(self):
        self.assertEqual(self._classify("pip install requests"), SafetyTier.CONFIRM)

    def test_confirm_systemctl_restart(self):
        self.assertEqual(self._classify("sudo systemctl restart sshd"), SafetyTier.CONFIRM)

    def test_confirm_wget(self):
        self.assertEqual(self._classify("wget https://example.com/file"), SafetyTier.CONFIRM)

    # === 10 BLOCKED (destructive) commands ===

    def test_blocked_rm_rf_root(self):
        self.assertEqual(self._classify("rm -rf /"), SafetyTier.BLOCKED)

    def test_blocked_rm_rf_path(self):
        self.assertEqual(self._classify("rm -rf /home"), SafetyTier.BLOCKED)

    def test_blocked_dd_zero(self):
        self.assertEqual(self._classify("dd if=/dev/zero of=/dev/sda"), SafetyTier.BLOCKED)

    def test_blocked_dd_urandom(self):
        self.assertEqual(self._classify("dd if=/dev/urandom of=/dev/sdb"), SafetyTier.BLOCKED)

    def test_blocked_mkfs(self):
        self.assertEqual(self._classify("mkfs.ext4 /dev/sda1"), SafetyTier.BLOCKED)

    def test_blocked_shutdown(self):
        self.assertEqual(self._classify("shutdown now"), SafetyTier.BLOCKED)

    def test_blocked_reboot(self):
        self.assertEqual(self._classify("reboot"), SafetyTier.BLOCKED)

    def test_blocked_fdisk(self):
        self.assertEqual(self._classify("fdisk /dev/sda"), SafetyTier.BLOCKED)

    def test_blocked_empty(self):
        self.assertEqual(self._classify(""), SafetyTier.BLOCKED)

    def test_blocked_chmod_777_root(self):
        self.assertEqual(self._classify("chmod -R 777 /"), SafetyTier.BLOCKED)

    # === Edge cases ===

    def test_blocked_dd_to_nvme(self):
        self.assertEqual(self._classify("dd if=/dev/zero of=/dev/nvme0n1"), SafetyTier.BLOCKED)

    def test_blocked_redirect_to_nvme(self):
        self.assertEqual(self._classify("echo foo > /dev/nvme0n1"), SafetyTier.BLOCKED)

    def test_blocked_disable_networkmanager(self):
        self.assertEqual(self._classify("systemctl disable NetworkManager"), SafetyTier.BLOCKED)

    def test_blocked_mask_dbus(self):
        self.assertEqual(self._classify("systemctl mask dbus"), SafetyTier.BLOCKED)

    def test_blocked_iptables_flush(self):
        self.assertEqual(self._classify("iptables -F"), SafetyTier.BLOCKED)

    def test_blocked_shred(self):
        self.assertEqual(self._classify("shred /dev/sda"), SafetyTier.BLOCKED)

    def test_blocked_poweroff(self):
        self.assertEqual(self._classify("poweroff"), SafetyTier.BLOCKED)

    def test_auto_unknown_defaults_confirm(self):
        """Unknown commands should default to confirm, not auto."""
        self.assertEqual(self._classify("some_unknown_tool --flag"), SafetyTier.CONFIRM)

    def test_confirm_pipe_with_write(self):
        """Pipe chain with a write command should be confirm."""
        self.assertEqual(self._classify("ls | tee output.txt"), SafetyTier.CONFIRM)

    def test_blocked_pipe_with_destructive(self):
        """Pipe chain with destructive command should be blocked."""
        self.assertEqual(self._classify("echo yes | mkfs.ext4 /dev/sda1"), SafetyTier.BLOCKED)

    def test_auto_compound_read_only(self):
        """Multiple read-only commands chained should be auto."""
        self.assertEqual(self._classify("uname -a && hostname"), SafetyTier.AUTO)

    def test_confirm_semicolon_with_write(self):
        """Semicolon chain with write should be confirm."""
        self.assertEqual(self._classify("ls; touch newfile"), SafetyTier.CONFIRM)


class TestRunCommandExecution(unittest.TestCase):
    """Test actual command execution on real system."""

    def setUp(self):
        self.tool = RunCommandTool()

    def test_execute_uname(self):
        result = self.tool.execute({"command": "uname -a"})
        self.assertTrue(result.success)
        self.assertIn("Linux", result.content)
        self.assertIn("intergenos", result.content)

    def test_execute_hostname(self):
        result = self.tool.execute({"command": "hostname"})
        self.assertTrue(result.success)
        self.assertEqual(result.content.strip(), "intergenos")

    def test_execute_blocked_refuses(self):
        result = self.tool.execute({"command": "rm -rf /"})
        self.assertFalse(result.success)
        self.assertIn("blocked", result.content.lower())

    def test_execute_empty(self):
        result = self.tool.execute({"command": ""})
        self.assertFalse(result.success)

    def test_execute_timeout(self):
        result = self.tool.execute({"command": "sleep 10", "timeout": 1})
        self.assertFalse(result.success)
        self.assertIn("timed out", result.content)


class TestReadFile(unittest.TestCase):
    """Test read_file against real system files."""

    def setUp(self):
        self.tool = ReadFileTool()

    def test_read_hostname(self):
        result = self.tool.execute({"path": "/etc/hostname"})
        self.assertTrue(result.success)
        self.assertIn("intergenos", result.content)

    def test_read_nonexistent(self):
        result = self.tool.execute({"path": "/nonexistent/file"})
        self.assertFalse(result.success)
        self.assertIn("not found", result.content.lower())

    def test_read_line_range(self):
        result = self.tool.execute({"path": "/etc/hostname", "start_line": 1, "end_line": 1})
        self.assertTrue(result.success)

    def test_read_empty_path(self):
        result = self.tool.execute({"path": ""})
        self.assertFalse(result.success)

    def test_read_directory(self):
        result = self.tool.execute({"path": "/etc"})
        self.assertFalse(result.success)
        self.assertIn("not a regular file", result.content.lower())


class TestWriteFile(unittest.TestCase):
    """Test write_file with temp files and protected path blocking."""

    def setUp(self):
        self.tool = WriteFileTool()

    def test_write_new_file(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            path = f.name
        os.unlink(path)  # remove so we test creation
        try:
            result = self.tool.execute({"path": path, "content": "hello\n"})
            self.assertTrue(result.success)
            self.assertIn("Created", result.content)
            self.assertEqual(Path(path).read_text(), "hello\n")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_write_diff(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("old content\n")
            path = f.name
        try:
            result = self.tool.execute({"path": path, "content": "new content\n"})
            self.assertTrue(result.success)
            self.assertIn("Updated", result.content)
            self.assertIn("-old content", result.content)
            self.assertIn("+new content", result.content)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_write_no_change(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("same\n")
            path = f.name
        try:
            result = self.tool.execute({"path": path, "content": "same\n"})
            self.assertTrue(result.success)
            self.assertIn("no changes", result.content.lower())
        finally:
            Path(path).unlink(missing_ok=True)

    def test_blocked_shadow(self):
        tier = self.tool.classify_safety({"path": "/etc/shadow"})
        self.assertEqual(tier, SafetyTier.BLOCKED)

    def test_blocked_passwd(self):
        tier = self.tool.classify_safety({"path": "/etc/passwd"})
        self.assertEqual(tier, SafetyTier.BLOCKED)


class TestManagePackages(unittest.TestCase):
    """Test manage_packages — pkm not installed, should handle gracefully."""

    def setUp(self):
        self.tool = ManagePackagesTool()

    def test_pkm_not_installed(self):
        result = self.tool.execute({"action": "list"})
        self.assertFalse(result.success)
        self.assertIn("not installed", result.content.lower())

    def test_safety_list_is_auto(self):
        tier = self.tool.classify_safety({"action": "list"})
        self.assertEqual(tier, SafetyTier.AUTO)

    def test_safety_install_is_confirm(self):
        tier = self.tool.classify_safety({"action": "install"})
        self.assertEqual(tier, SafetyTier.CONFIRM)


class TestManageServices(unittest.TestCase):
    """Test manage_services against real systemctl."""

    def setUp(self):
        self.tool = ManageServicesTool()

    def test_networkmanager_active(self):
        result = self.tool.execute({"action": "is-active", "service": "NetworkManager"})
        self.assertTrue(result.success)
        self.assertIn("active", result.content)

    def test_list_units(self):
        result = self.tool.execute({"action": "list-units"})
        self.assertTrue(result.success)

    def test_safety_status_is_auto(self):
        tier = self.tool.classify_safety({"action": "status"})
        self.assertEqual(tier, SafetyTier.AUTO)

    def test_safety_restart_is_confirm(self):
        tier = self.tool.classify_safety({"action": "restart"})
        self.assertEqual(tier, SafetyTier.CONFIRM)

    def test_missing_service_name(self):
        result = self.tool.execute({"action": "status"})
        self.assertFalse(result.success)
        self.assertIn("requires a service name", result.content)


class TestOpenApplication(unittest.TestCase):
    """Test open_application app discovery."""

    def setUp(self):
        self.tool = OpenApplicationTool()

    def test_list_apps(self):
        result = self.tool.execute({"list_apps": True})
        self.assertTrue(result.success)
        self.assertIn("Installed applications", result.content)

    def test_app_not_found(self):
        result = self.tool.execute({"name": "ThisAppDoesNotExist12345"})
        self.assertFalse(result.success)
        self.assertIn("not found", result.content.lower())

    def test_empty_name(self):
        result = self.tool.execute({"name": ""})
        self.assertFalse(result.success)


class TestHardwareDetector(unittest.TestCase):
    """Test hardware detector against real system hardware."""

    def test_detect(self):
        from intergen.hardware import HardwareDetector
        from intergen.interfaces.types import HardwareTierLevel

        detector = HardwareDetector()
        tier = detector.detect()

        # This laptop: 15.3 GB RAM, Intel iGPU
        self.assertGreater(tier.ram_gb, 10.0)
        self.assertLess(tier.ram_gb, 20.0)
        self.assertEqual(tier.gpu_vendor, "intel")
        self.assertEqual(tier.tier, HardwareTierLevel.TIER_2)
        self.assertEqual(tier.recommended_model, "Qwen3.5-9B")

    def test_caching(self):
        from intergen.hardware import HardwareDetector

        detector = HardwareDetector()
        tier1 = detector.get_tier()
        tier2 = detector.get_tier()
        self.assertIs(tier1, tier2)  # same object, cached


class TestModelManager(unittest.TestCase):
    """Test model manager catalog and manifest handling."""

    def test_tier_lookup(self):
        from intergen.model_manager import ModelManager
        from intergen.interfaces.types import HardwareTierLevel

        with tempfile.TemporaryDirectory() as tmpdir:
            mm = ModelManager(
                model_dir=Path(tmpdir) / "models",
                manifest_path=Path(tmpdir) / "manifest.json",
            )
            model = mm.get_model_for_tier(HardwareTierLevel.TIER_2)
            self.assertEqual(model.name, "Qwen3.5-9B")
            self.assertEqual(model.quant, "Q4_K_M")
            self.assertFalse(model.downloaded)

    def test_embedding_model(self):
        from intergen.model_manager import ModelManager

        with tempfile.TemporaryDirectory() as tmpdir:
            mm = ModelManager(
                model_dir=Path(tmpdir) / "models",
                manifest_path=Path(tmpdir) / "manifest.json",
            )
            emb = mm.get_embedding_model()
            self.assertEqual(emb.name, "nomic-embed-text-v1.5")
            self.assertLess(emb.size_gb, 1.0)

    def test_list_empty(self):
        from intergen.model_manager import ModelManager

        with tempfile.TemporaryDirectory() as tmpdir:
            mm = ModelManager(
                model_dir=Path(tmpdir) / "models",
                manifest_path=Path(tmpdir) / "manifest.json",
            )
            self.assertEqual(len(mm.list_downloaded()), 0)


if __name__ == "__main__":
    unittest.main()
