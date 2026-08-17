# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Test suite for InterGen core tools.

Briefing requirement: 20 read-only (auto), 10 write (confirm), 10 destructive (blocked).
Tests run against the real InterGenOS system — that's our advantage.
"""

from __future__ import annotations

import os
import platform
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_systemctl_status_pager_class(self):
        # systemctl status default-paginates through $PAGER (interactive-pager
        # exec-vector hardening, 8f90c719): a BARE invocation is shell-escapable
        # -> CONFIRM; the non-interactive form (--no-pager, or piped) is a pure
        # data-sink -> AUTO.
        self.assertEqual(self._classify("systemctl status sshd"), SafetyTier.CONFIRM)
        self.assertEqual(
            self._classify("systemctl status sshd --no-pager"), SafetyTier.AUTO)
        self.assertEqual(
            self._classify("systemctl status sshd | cat"), SafetyTier.AUTO)

    def test_auto_systemctl_is_active(self):
        self.assertEqual(self._classify("systemctl is-active NetworkManager"), SafetyTier.AUTO)

    def test_auto_pipe_read_only(self):
        self.assertEqual(self._classify("ls | grep txt"), SafetyTier.AUTO)

    def test_blocked_sudo_prefix(self):
        # AI-4 (decided 2026-05-30): a sudo-prefixed run_command is
        # BLOCKED. The old behavior STRIPPED sudo and auto-ran the underlying
        # command, so an injected 'sudo ls /root' would auto-execute with no
        # confirmation. Privilege escalation belongs on the token-bound pkexec
        # path, never sudo in the unprivileged shell tool.
        self.assertEqual(self._classify("sudo ls /root"), SafetyTier.BLOCKED)

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
        # systemctl restart of a non-critical unit is CONFIRM (privileged, but
        # not a mask/disable of a critical unit). The sudo-prefixed form is now
        # BLOCKED by the AI-4 sudo-block (see test_blocked_sudo_prefix), so the
        # CONFIRM intent is asserted on the non-sudo command.
        self.assertEqual(self._classify("systemctl restart sshd"), SafetyTier.CONFIRM)

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
        # uname -a embeds the nodename — derive it, don't hardcode a hostname.
        self.assertIn(platform.node(), result.content)

    def test_execute_hostname(self):
        result = self.tool.execute({"command": "hostname"})
        self.assertTrue(result.success)
        # Assert run_command returns the system's ACTUAL hostname (env-derived),
        # not a hardcoded install name that varies per box.
        self.assertEqual(result.content.strip(), socket.gethostname())

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
        # This test is about read_file's correctness, not the hostname value —
        # assert the file's ACTUAL content appears in the (header-wrapped,
        # line-numbered) output. (/etc/hostname and gethostname() can differ;
        # don't hardcode an install name.)
        expected = Path("/etc/hostname").read_text().strip()
        self.assertIn(expected, result.content)

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
        # The pkm-absent behavior, tested deterministically: a real install HAS
        # pkm, so mock its absence rather than assuming an un-provisioned box.
        with mock.patch("shutil.which", return_value=None):
            result = self.tool.execute({"action": "list"})
        self.assertFalse(result.success)
        self.assertIn("not installed", result.content.lower())

    def test_safety_list_is_auto(self):
        tier = self.tool.classify_safety({"action": "list"})
        self.assertEqual(tier, SafetyTier.AUTO)

    def test_safety_install_is_confirm(self):
        tier = self.tool.classify_safety({"action": "install"})
        self.assertEqual(tier, SafetyTier.CONFIRM)

    # === G3-22 structured tool-result returns (model_summary) ===
    # A realistic slice of `pkm list` output (pkm/cli.py cmd_list format:
    # "  Installed packages (N):" header + indented "name version [tier] —
    # desc" lines). Count in the header is the ground truth, deliberately
    # larger than the number of sample lines below.
    _PKM_LIST = (
        "  Installed packages (824):\n"
        "    mako                           1.8.0          [core] — Wayland notification daemon\n"
        "    glibc                          2.40           [core] — GNU C Library\n"
        "    coreutils                      9.5            [core] — GNU core utilities\n"
        "    bash                           5.2.32         [core] — Bourne Again SHell\n"
        "    systemd                        256.7          [core] — system and service manager\n"
        "    llama-cpp                      b5545          [ai] — local inference engine\n"
        "    gnome-shell                    49.4           [desktop] — GNOME shell\n"
        "    mesa                           24.2.7         [desktop] — OpenGL implementation\n"
        "    openssh                        9.9p1          [core] — secure shell\n"
        "    websockets                     16.0           [extra] — WebSocket library\n"
        "    nano                           8.2            [extra] — text editor\n"
    )

    def test_summarize_list_leads_with_exact_count(self):
        from intergen.tools.manage_packages import summarize_package_list
        summary = summarize_package_list(self._PKM_LIST)
        self.assertIsNotNone(summary)
        # Exact count from the header, not the sample-line count (11 lines).
        self.assertIn("824", summary)
        self.assertNotIn("11 installed packages", summary)

    def test_summarize_list_samples_names_and_caps(self):
        from intergen.tools.manage_packages import (
            summarize_package_list, _LIST_SAMPLE,
        )
        summary = summarize_package_list(self._PKM_LIST)
        self.assertIn("mako", summary)          # first sampled name
        self.assertNotIn("nano", summary)       # 11th line, beyond the sample cap
        # Sample is capped; the remainder is acknowledged, not enumerated.
        self.assertIn(f"+{824 - _LIST_SAMPLE} more", summary)

    def test_summarize_list_is_small_enough_for_the_2b(self):
        from intergen.tools.manage_packages import summarize_package_list
        summary = summarize_package_list(self._PKM_LIST)
        # Design target: well under the 4000-char floor (≤ ~1500 chars).
        self.assertLess(len(summary), 1500)

    def test_summarize_list_is_user_displayable_no_model_steering(self):
        # The summary is shown verbatim in the web-UI tool card (D-2), so it
        # must read as a fact, not as an instruction to the model. The generic
        # "report counts exactly / don't enumerate" steering lives in
        # LLMRouter._SYNTHESIS_RULES, NOT in this user-facing line.
        from intergen.tools.manage_packages import summarize_package_list
        summary = summarize_package_list(self._PKM_LIST)
        low = summary.lower()
        self.assertNotIn("state the count", low)
        self.assertNotIn("do not", low)
        self.assertNotIn("enumerate", low)

    def test_summarize_non_list_output_returns_none(self):
        # Error text / non-listing output must not be mis-summarized — the
        # caller then leaves model_summary=None and the 4000-char floor guards.
        from intergen.tools.manage_packages import summarize_package_list
        self.assertIsNone(summarize_package_list("pkm: command not found"))
        self.assertIsNone(summarize_package_list(""))

    def test_pkm_absent_leaves_model_summary_none(self):
        # When pkm is not installed the list path never runs; model_summary
        # stays None and the legacy content-only behavior is preserved. Mock
        # pkm's absence so this holds on a real (pkm-present) install too.
        with mock.patch("shutil.which", return_value=None):
            result = self.tool.execute({"action": "list"})
        self.assertIsNone(result.model_summary)


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

    def _run_capturing_cmd(self, arguments):
        """Execute the tool with subprocess.run stubbed; return the argv it built."""
        import types
        import unittest.mock as mock
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return types.SimpleNamespace(returncode=0, stdout="active\n", stderr="")

        with mock.patch(
            "intergen.tools.manage_services.subprocess.run", side_effect=fake_run
        ):
            result = self.tool.execute(arguments)
        return captured.get("cmd", []), result

    def test_ssh_alias_canonicalized_to_sshd(self):
        # "ssh" is what users/the 2B say, but the unit on InterGenOS is sshd —
        # without canonicalization `systemctl status ssh` returns
        # "Unit ssh.service could not be found" (the lex_svc_worried miss).
        cmd, result = self._run_capturing_cmd({"action": "is-active", "service": "ssh"})
        self.assertIn("sshd", cmd)
        self.assertNotIn("ssh", cmd)  # the bare colloquial form never reaches systemctl
        self.assertTrue(result.success)

    def test_ssh_alias_tolerates_suffix_and_case(self):
        for svc in ("ssh.service", "SSH", "Ssh"):
            cmd, _ = self._run_capturing_cmd({"action": "status", "service": svc})
            self.assertIn("sshd", cmd, svc)
            self.assertNotIn(svc, cmd, svc)

    def test_non_aliased_service_passes_through_unchanged(self):
        # A unit with no alias keeps its exact name AND case (NetworkManager).
        cmd, _ = self._run_capturing_cmd(
            {"action": "is-active", "service": "NetworkManager"})
        self.assertIn("NetworkManager", cmd)

    # === G3-22 structured tool-result returns (model_summary) ===
    # A realistic slice of `systemctl list-units` output (real format: a
    # "UNIT LOAD ACTIVE SUB DESCRIPTION" header, indented unit rows, a blank
    # line, a Legend block, then the "N loaded units listed." footer). The
    # footer count (411) is the ground truth, deliberately larger than the
    # number of sampled rows. One row is deliberately failed.
    _LIST_UNITS = (
        "  UNIT                          LOAD   ACTIVE SUB       DESCRIPTION\n"
        "  systemd-journald.service      loaded active running   Journal Service\n"
        "  NetworkManager.service        loaded active running   Network Manager\n"
        "  dbus.service                  loaded active running   D-Bus System Bus\n"
        "  bluetooth.service             loaded active running   Bluetooth\n"
        "  cups.service                  loaded failed failed    CUPS Scheduler\n"
        "  polkit.service                loaded active running   Authorization Manager\n"
        "  cron.service                  loaded active running   Periodic Command Scheduler\n"
        "  timers.target                 loaded active active    Timer Units\n"
        "  tpm2.target                   loaded active active    Trusted Platform Module\n"
        "  update-pki.timer              loaded active running   Update PKI weekly\n"
        "\n"
        "Legend: LOAD   → Reflects whether the unit definition was properly loaded.\n"
        "        ACTIVE → The high-level unit activation state.\n"
        "        SUB    → The low-level unit activation state.\n"
        "\n"
        "411 loaded units listed. Pass --all to see loaded but inactive units, too.\n"
    )

    def test_summarize_units_leads_with_footer_count(self):
        from intergen.tools.manage_services import summarize_service_list
        summary = summarize_service_list(self._LIST_UNITS)
        self.assertIsNotNone(summary)
        # Authoritative count comes from the footer (411), not the 10 sampled
        # rows.
        self.assertIn("411", summary)

    def test_summarize_units_surfaces_failed(self):
        from intergen.tools.manage_services import summarize_service_list
        summary = summarize_service_list(self._LIST_UNITS)
        # The diagnostic answer: which unit is broken.
        self.assertIn("failed", summary.lower())
        self.assertIn("cups.service", summary)

    def test_summarize_units_samples_and_caps(self):
        from intergen.tools.manage_services import (
            summarize_service_list, _UNIT_SAMPLE,
        )
        summary = summarize_service_list(self._LIST_UNITS)
        self.assertIn("systemd-journald.service", summary)   # first sampled
        self.assertIn(f"+{411 - _UNIT_SAMPLE} more", summary)

    def test_summarize_units_is_facts_only_no_model_steering(self):
        # D-2 surfaces model_summary verbatim in the user card — it must carry
        # NO model-directed imperatives (those live in _SYNTHESIS_RULES rule 7,
        # per the eca08d9b exemplar refinement).
        from intergen.tools.manage_services import summarize_service_list
        summary = summarize_service_list(self._LIST_UNITS).lower()
        for imperative in ("do not", "don't", "state the", "summarize",
                           "the user", "exactly"):
            self.assertNotIn(imperative, summary)

    def test_summarize_units_small_enough_for_the_2b(self):
        from intergen.tools.manage_services import summarize_service_list
        summary = summarize_service_list(self._LIST_UNITS)
        self.assertLess(len(summary), 1500)

    def test_summarize_non_units_output_returns_none(self):
        from intergen.tools.manage_services import summarize_service_list
        self.assertIsNone(summarize_service_list("Failed to list units: ..."))
        self.assertIsNone(summarize_service_list(""))

    def test_status_action_leaves_model_summary_none(self):
        # Non-list actions stay pass-through (model_summary=None).
        result = self.tool.execute({"action": "is-active", "service": "NetworkManager"})
        self.assertIsNone(result.model_summary)


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

        # Assert a VALID, self-consistent detection rather than one machine's
        # specific values — gpu_vendor/tier/model vary by box (this runs on
        # Intel and AMD, 8-64GB), so hardcoding "intel"/TIER_2 fails everywhere
        # but one host. A broken detector still fails these sanity bounds.
        self.assertGreater(tier.ram_gb, 0.0)
        self.assertLess(tier.ram_gb, 1024.0)
        self.assertIn(tier.tier, set(HardwareTierLevel))
        self.assertIsInstance(tier.gpu_vendor, str)
        self.assertTrue(tier.gpu_vendor)
        self.assertTrue(tier.recommended_model)

    def test_caching(self):
        from intergen.hardware import HardwareDetector

        detector = HardwareDetector()
        tier1 = detector.get_tier()
        tier2 = detector.get_tier()
        self.assertIs(tier1, tier2)  # same object, cached


class TestHardwareCapability(unittest.TestCase):
    """Capability-based GPU classification (hardware-independent, pure logic).

    Regression coverage for the A12 integrated-AMD bug: a vendor-only test
    treated the APU as discrete and picked the 9B model. The pick must now
    follow real VRAM-backed capability, not the PCI vendor ID.
    """

    def setUp(self):
        from intergen.hardware import HardwareDetector
        self.det = HardwareDetector()

    def test_integrated_amd_apu_is_not_discrete(self):
        # A12: vendor reads "amd" but no/low dedicated VRAM → integrated.
        self.assertFalse(self.det._is_discrete_capable("amd", None))
        self.assertFalse(self.det._is_discrete_capable("amd", 512))
        self.assertFalse(self.det._is_discrete_capable("amd", 2048))

    def test_discrete_amd_is_discrete(self):
        self.assertTrue(self.det._is_discrete_capable("amd", 4096))
        self.assertTrue(self.det._is_discrete_capable("amd", 8192))

    def test_intel_igpu_is_not_discrete(self):
        self.assertFalse(self.det._is_discrete_capable("intel", None))
        self.assertFalse(self.det._is_discrete_capable("intel", 1024))

    def test_intel_arc_discrete_is_discrete(self):
        self.assertTrue(self.det._is_discrete_capable("intel", 8192))

    def test_relic_nvidia_known_low_vram_is_not_discrete(self):
        # WEDGE (9B lane item 1): a relic low-VRAM nvidia card (e.g. a 1 GB
        # GT 710) whose driver DOES export VRAM is a dedicated GPU but cannot
        # hold the 5.5 GB 9B — it must select the 2B floor. Pre-fix, nvidia
        # returned True for ANY vendor ID, so this relic wrongly picked the
        # 9B/35B; this asserts the VRAM-gate on the known-VRAM path.
        self.assertFalse(self.det._is_discrete_capable("nvidia", 1024))
        self.assertFalse(self.det._is_discrete_capable("nvidia", 2048))

    def test_capable_nvidia_is_discrete(self):
        # A real discrete nvidia (>= threshold VRAM) still selects the big model.
        from intergen.hardware import DISCRETE_VRAM_THRESHOLD_MB
        self.assertTrue(self.det._is_discrete_capable("nvidia", 6144))
        self.assertTrue(self.det._is_discrete_capable("nvidia", DISCRETE_VRAM_THRESHOLD_MB))

    def test_unknown_vram_nvidia_stays_tentatively_capable(self):
        # nvidia's proprietary driver frequently does NOT export VRAM. An
        # unknown-VRAM nvidia must NOT floor (that would pick an integrated APU
        # over a real dGPU — see test_most_capable_gpu_wins_selection); it stays
        # tentatively capable and the launch-time offload gate is the backstop.
        self.assertTrue(self.det._is_discrete_capable("nvidia", None))

    def test_unknown_or_no_gpu_is_not_discrete(self):
        self.assertFalse(self.det._is_discrete_capable(None, None))
        self.assertFalse(self.det._is_discrete_capable("unknown (0x1af4)", None))

    def test_threshold_boundary(self):
        from intergen.hardware import DISCRETE_VRAM_THRESHOLD_MB
        self.assertFalse(
            self.det._is_discrete_capable("amd", DISCRETE_VRAM_THRESHOLD_MB - 1)
        )
        self.assertTrue(
            self.det._is_discrete_capable("amd", DISCRETE_VRAM_THRESHOLD_MB)
        )

    def test_a12_class_assigns_tier1_floor(self):
        # Integrated-AMD box (no discrete GPU) = the Tier-1 2B floor,
        # regardless of RAM (RAM is never a tier input — 2026-07-24 design).
        from intergen.interfaces.types import HardwareTierLevel
        self.assertEqual(
            self.det._assign_tier(is_discrete=False),
            HardwareTierLevel.TIER_1,
        )

    def test_discrete_with_9b_class_vram_assigns_tier2(self):
        from intergen.hardware import TIER2_VRAM_MB
        from intergen.interfaces.types import HardwareTierLevel
        self.assertEqual(
            self.det._assign_tier(is_discrete=True, gpu_vram_mb=TIER2_VRAM_MB),
            HardwareTierLevel.TIER_2,
        )

    def test_most_capable_gpu_wins_selection(self):
        # Hybrid APU + discrete dGPU: the discrete part must be chosen.
        gpus = [("amd", "amd [0x15dd]", 512), ("nvidia", "nvidia [0x1f95]", None)]
        best = max(
            gpus,
            key=lambda g: (self.det._is_discrete_capable(g[0], g[2]), g[2] or 0),
        )
        self.assertEqual(best[0], "nvidia")


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
            # Hermetic pin state (never the box's installed manifest): pin
            # every catalog tier so the lookup itself is under test.
            from intergen.model_manager import MODEL_CATALOG
            mm._pins = {
                MODEL_CATALOG[level].filename: "a" * 64
                for level in MODEL_CATALOG
            }
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


class TestModelTwoSourceFetch(unittest.TestCase):
    """Two-source model fetch (locked order 2026-06-09: InterGenOS
    MIRROR first, vendor (HF) fallback — the mirror hosts the exact validated
    GGUFs; a mutable vendor file could silently diverge from the pin). SHA-pin
    verified on either source, fail-closed when both are exhausted."""

    GOOD = b"GGUF-TEST-PAYLOAD-bytes"

    def _model(self):
        from intergen.interfaces.types import HardwareTierLevel, ModelInfo
        import hashlib
        return ModelInfo(
            name="Test-Model",
            filename="test-model-Q4_K_M.gguf",
            repo_id="unsloth/Test-Model-GGUF",
            quant="Q4_K_M",
            size_gb=0.1,
            sha256=hashlib.sha256(self.GOOD).hexdigest(),
            tier=HardwareTierLevel.TIER_1,
        )

    def _mm(self, tmpdir):
        from intergen.model_manager import ModelManager
        mm = ModelManager(
            model_dir=Path(tmpdir) / "models",
            manifest_path=Path(tmpdir) / "manifest.json",
        )
        # Bypass the license gate (P-016) — orthogonal to fetch-source logic.
        mm.check_license_acceptance = lambda model: True
        return mm

    def _fake_urlopen(self, behavior):
        """behavior: dict mapping 'vendor'|'mirror' -> bytes | Exception."""
        from urllib.error import URLError

        class _Resp:
            def __init__(self, data):
                self._data = data
                self.headers = {"Content-Length": str(len(data))}

            def read(self, n):
                chunk, self._data = self._data[:n], self._data[n:]
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        calls = []

        def fake(req, timeout=30):
            url = req.full_url
            who = "vendor" if "huggingface.co" in url else "mirror"
            calls.append(who)
            outcome = behavior[who]
            if isinstance(outcome, Exception):
                raise outcome
            return _Resp(outcome)

        fake.calls = calls
        return fake

    def _run(self, behavior):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmpdir:
            mm = self._mm(tmpdir)
            model = self._model()
            fake = self._fake_urlopen(behavior)
            with mock.patch("intergen.model_manager.urllib.request.urlopen", fake):
                ok = mm.download_model(model)
            local = Path(tmpdir) / "models" / model.filename
            # Capture existence INSIDE the tempdir context (it is removed on
            # exit) so callers can assert on the on-disk result.
            return ok, fake.calls, local.exists()

    def test_url_builders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mm = self._mm(tmpdir)
            model = self._model()
            self.assertEqual(
                mm._huggingface_url(model),
                "https://huggingface.co/unsloth/Test-Model-GGUF"
                "/resolve/main/test-model-Q4_K_M.gguf",
            )
            self.assertEqual(
                mm._mirror_url(model),
                "https://repo.intergenos.org/models/test-model-Q4_K_M.gguf",
            )

    def test_mirror_first_success_skips_vendor(self):
        # locked 2026-06-09: mirror FIRST (authoritative). Mirror good
        # -> vendor never touched.
        from urllib.error import URLError
        ok, calls, existed = self._run(
            {"mirror": self.GOOD, "vendor": URLError("should-not-hit")}
        )
        self.assertTrue(ok)
        self.assertEqual(calls, ["mirror"])  # vendor never touched
        self.assertTrue(existed)

    def test_mirror_down_vendor_fallback(self):
        from urllib.error import URLError
        ok, calls, existed = self._run(
            {"mirror": URLError("mirror-down"), "vendor": self.GOOD}
        )
        self.assertTrue(ok)
        self.assertEqual(calls, ["mirror", "vendor"])  # mirror first, then vendor
        self.assertTrue(existed)

    def test_mirror_corrupt_vendor_recovers(self):
        # Mirror serves wrong bytes (pin mismatch) -> rejected -> vendor good.
        ok, calls, existed = self._run(
            {"mirror": b"WRONG-BYTES", "vendor": self.GOOD}
        )
        self.assertTrue(ok)
        self.assertEqual(calls, ["mirror", "vendor"])
        self.assertTrue(existed)

    def test_both_down_fail_closed(self):
        from urllib.error import URLError
        ok, calls, existed = self._run(
            {"mirror": URLError("mirror-down"), "vendor": URLError("hf-down")}
        )
        self.assertFalse(ok)
        self.assertEqual(calls, ["mirror", "vendor"])
        self.assertFalse(existed)  # no partial/unverified file left

    def test_both_corrupt_fail_closed(self):
        ok, calls, existed = self._run(
            {"mirror": b"WRONG-1", "vendor": b"WRONG-2"}
        )
        self.assertFalse(ok)
        self.assertEqual(calls, ["mirror", "vendor"])
        self.assertFalse(existed)  # mismatched bytes never kept


class TestReadFileStructuredSummary(unittest.TestCase):
    """G3-22/D-3: read_file's structural model_summary for large files."""

    def _body(self, n: int) -> str:
        return "\n".join(f"{i:>6}\t line {i} content" for i in range(1, n + 1))

    def test_small_file_no_summary(self):
        from intergen.tools.read_file import summarize_file_read
        self.assertIsNone(summarize_file_read("File: /x (3 lines)", self._body(3)))

    def test_large_file_head_tail_and_omitted_marker(self):
        from intergen.tools.read_file import summarize_file_read
        header = "File: /var/log/big.log (5000 lines)"
        s = summarize_file_read(header, self._body(5000))
        self.assertIsNotNone(s)
        self.assertIn(header, s)            # metadata preserved
        self.assertIn("line 1 ", s)         # head
        self.assertIn("line 5000 ", s)      # tail (answer may be at the bottom)
        self.assertIn("omitted", s)         # structural marker

    def test_summary_stays_under_the_synthesis_floor(self):
        # Even a pathological few-but-huge-lines file is char-capped under 4000.
        from intergen.tools.read_file import summarize_file_read
        body = "\n".join("X" * 9000 for _ in range(3))
        s = summarize_file_read("File: /x (3 lines)", body)
        self.assertIsNotNone(s)
        self.assertLess(len(s), 4000)

    def test_summary_carries_no_model_steering(self):
        # D-2 surfaces model_summary verbatim — it must be facts/structure only.
        from intergen.tools.read_file import summarize_file_read
        s = summarize_file_read("File: /x (5000 lines)", self._body(5000)).lower()
        for imperative in ("do not", "don't", "summarize", "state the", "you should"):
            self.assertNotIn(imperative, s)


class TestWebSearchStructuredSummary(unittest.TestCase):
    """G3-22/D-3: web_search's overflow-gated, snippet-trimmed model_summary."""

    def test_small_result_set_no_summary(self):
        from intergen.tools.web_search import render_search_results
        content, summary = render_search_results(
            "q", [("T1", "http://a", "short snippet"), ("T2", "http://b", "another")])
        self.assertIsNone(summary)
        self.assertIn("T1", content)

    def test_large_result_set_trims_snippets(self):
        from intergen.tools.web_search import render_search_results, _SNIPPET_CAP
        results = [(f"Title{i}", f"http://x/{i}", "Z" * 500) for i in range(10)]
        content, summary = render_search_results("q", results)
        self.assertIsNotNone(summary)
        self.assertIn("Title0", summary)
        self.assertIn("http://x/0", summary)
        self.assertIn("…", summary)                 # snippet was trimmed
        self.assertLess(len(summary), len(content))  # genuinely smaller
        self.assertNotIn("Z" * (_SNIPPET_CAP + 50), summary)  # full snippet not echoed


class TestNeverListIntegrity(unittest.TestCase):
    """PI-D self-test tripwire — `intergen test` must go RED on a tamper-induced
    downgrade of the authoritative never-list, so it cannot hide behind a green
    self-test. This reads the REAL shipped artifact (the system manifest +
    keyring), not an injected fake."""

    def test_shipped_never_list_is_not_present_but_untrusted(self):
        # LOADED (a healthy install) and ABSENT (a from-source/dev box with
        # nothing installed at the system path) both PASS. Only UNTRUSTED — the
        # manifest artifact PRESENT but its signature/key unverifiable (tamper or
        # corruption) — fails, turning `intergen test` red.
        from intergen.destructive_policy import PolicyLoad, load_policy_status
        _policy, status = load_policy_status()
        self.assertNotEqual(
            status, PolicyLoad.UNTRUSTED,
            "destructive-policy never-list is PRESENT but UNTRUSTED — signature/key "
            "verification failed (possible tampering or corruption); the "
            "authoritative never-list has silently downgraded to the interim floor. "
            "Re-verify /usr/share/intergen/destructive-policy-manifest.json and "
            "/etc/pkm/trusted.gpg.")


if __name__ == "__main__":
    unittest.main()
