# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An audit write that cannot land must not turn a call that ran into a failure.

Found 2026-09-17 while correcting the logging configuration. The MCP audit log
is written to the root-owned /var/log/intergen, which a per-user service sees as
a READ-ONLY FILE SYSTEM under ProtectSystem=strict. Python raises that as a
plain OSError (errno 30), not PermissionError — measured, and the reason the
event logger's own comment names EROFS specifically — and the audit writer
caught only PermissionError.

The consequence was not a lost log line. `audit_log` is called inside the same
try that wraps the tool call itself, so the unhandled OSError was caught by the
call's own handler AFTER the tool had already run: the result was thrown away,
the caller was told "MCP error: Read-only file system" for a call that had
succeeded, and no audit record of that executed call existed anywhere.

Asserted here: the audit path is resolved where a user service can write it, an
audit write that still cannot land is reported rather than raised, and the
result of a tool that ran is returned as the success it was.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen import mcp_client
from intergen.interfaces.mcp import MCPTrustTier


class TheAuditWriteDoesNotFailTheCall(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.state = Path(self._td.name) / "state"

    def _guard(self):
        return mcp_client.SentinelGuard()

    def test_the_audit_path_is_under_the_user_state_dir_for_a_user_service(self):
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state)}), \
                mock.patch("intergen.mcp_client.os.geteuid", return_value=1000):
            path = mcp_client._audit_log_path()
        self.assertEqual(path, self.state / "intergen" / "mcp-audit.log")

    def test_a_root_deployment_keeps_the_system_path(self):
        with mock.patch("intergen.mcp_client.os.geteuid", return_value=0):
            path = mcp_client._audit_log_path()
        self.assertEqual(str(path), mcp_client._MCP_AUDIT_LOG)

    def test_an_unwritable_audit_path_is_reported_not_raised(self):
        guard = self._guard()
        with mock.patch("intergen.mcp_client._audit_log_path",
                        side_effect=OSError(30, "Read-only file system")), \
                self.assertLogs("intergen.mcp_client", level="ERROR") as cm:
            guard.audit_log("srv", "tool", {"a": 1}, "ok",
                            MCPTrustTier.UNTRUSTED)
        text = "\n".join(cm.output)
        self.assertIn("audit", text.lower())
        self.assertIn("tool", text)

    def test_the_entry_reaches_the_file_when_the_path_is_writable(self):
        guard = self._guard()
        target = self.state / "intergen" / "mcp-audit.log"
        with mock.patch("intergen.mcp_client._audit_log_path",
                        return_value=target):
            guard.audit_log("srv", "tool", {"a": 1}, "ok",
                            MCPTrustTier.UNTRUSTED)
        entry = json.loads(target.read_text().strip().splitlines()[-1])
        self.assertEqual(entry["server"], "srv")
        self.assertEqual(entry["tool"], "tool")


if __name__ == "__main__":
    unittest.main()
