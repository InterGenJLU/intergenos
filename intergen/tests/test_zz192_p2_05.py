# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The Audit log health check states only what it measured.

`HealthAggregator._check_daemon()` appends the "Audit log" check like this
(intergen/health.py:222-226):

    checks.append(HealthCheck(
        name="Audit log",
        status="green",
        summary=f"{self._audit_log_count} entries, 0 tamper events",
    ))

Both halves of that sentence are constants, not readings.

  * Nothing in health.py opens, counts, hashes or verifies an audit record —
    the module never imports `intergen.audit_log` at all — so "0 tamper
    events" is a literal. It is an integrity verdict on the user-owned
    dispatch ledger that nothing in the process established.

  * `self._audit_log_count` is whatever the constructor was handed, and the
    one production caller hands it the literal 0
    (intergen/dbus_daemon.py:2301-2307, `audit_log_count=0`), so the entry
    count never moves either. `intergen.audit_log.record_count()` exists and
    is exactly this number, but only `intergen tool-log` calls it.

  * `status` is the string "green" with no condition attached, so the console
    renders the whole sentence in the green style
    (intergen/console/shell.py:672-690) and the panel in the green card.

A check is a function of the thing it checks. These three cases put a real
dispatch ledger under the canonical path and ask the health surface what it
sees: the entry count must be the number of records the log holds; the
verdict must not be byte-identical on an empty log and a full one; and the
report must not carry a tamper-event count on a log that was rewritten
underneath it and never verified.

Hermetic by construction: no llama_manager is wired, so the aggregator never
reaches the inference server's HTTP health endpoint; `gi` is blocked out of
sys.modules, so the D-Bus probe never opens the session bus; and lspci is
stubbed, so the GPU probe never shells out. Nothing here starts, queries or
talks to a service.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

from intergen import audit_log, health
from intergen.interfaces.provenance import AuditRecord

_ZERO_TAMPER = "0 tamper events"


def _record(n: int) -> AuditRecord:
    """One well-formed dispatch record, as the gate writes them."""
    return AuditRecord(
        timestamp=f"2026-09-30T12:0{n}:00Z",
        tool_name="run_command",
        arguments={"command": f"echo {n}"},
        declared_provenance="user",
        effective_provenance="user",
        ingress_tools_this_turn=[],
        user_decision="executed",
        exit_code=0,
        result_summary=str(n),
    )


class AuditLogHealthCheckTests(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="zz192-p2-05-")
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        # The canonical per-user ledger path, now inside the throwaway tree.
        self.log_path = audit_log.default_log_path()

    # -- fixtures -----------------------------------------------------------

    def _fill_the_ledger(self, count: int) -> None:
        """Put `count` real dispatch records in the canonical audit log."""
        for n in range(count):
            self.assertTrue(
                audit_log.write_record(_record(n)),
                "the audit-log writer failed — the fixture is broken, "
                "not the code under test",
            )
        self.assertEqual(
            audit_log.record_count(), count,
            "the fixture did not land: the canonical log does not hold the "
            "records this case is about",
        )

    def _audit_log_check(self) -> dict:
        """The Daemon layer's "Audit log" check from a full health report."""
        with mock.patch.dict(sys.modules, {"gi": None}), \
                mock.patch.object(
                    health.subprocess, "run",
                    side_effect=FileNotFoundError("lspci not run in this case")):
            report = health.quick_health()
        for layer in report.get("layers", []):
            for check in layer.get("checks", []):
                if check.get("name") == "Audit log":
                    return check
        raise AssertionError(
            "the health report carries no 'Audit log' check at all"
        )

    # -- the cases ----------------------------------------------------------

    def test_the_entry_count_is_the_number_of_records_the_log_holds(self) -> None:
        """Two records in the ledger -> the health surface says two."""
        self._fill_the_ledger(2)

        check = self._audit_log_check()

        self.assertIn(
            "2 entries", check["summary"],
            "the audit log holds 2 dispatch records at "
            f"{self.log_path}, and the health surface reports "
            f"{check['summary']!r} with status {check['status']!r} — the "
            "count is the constructor's literal, not a reading of the log",
        )

    def test_the_verdict_moves_when_the_log_moves(self) -> None:
        """A check whose verdict cannot change is not a check."""
        empty = self._audit_log_check()
        self.assertEqual(audit_log.record_count(), 0,
                         "fixture: the ledger should start empty")

        self._fill_the_ledger(2)
        full = self._audit_log_check()

        self.assertNotEqual(
            (empty["status"], empty["summary"]),
            (full["status"], full["summary"]),
            "the Audit log check returned the identical verdict "
            f"({full['status']!r}, {full['summary']!r}) on an EMPTY ledger "
            "and on one holding 2 dispatch records — the check is a constant "
            "string, so it measures nothing",
        )

    def test_the_audit_log_health_check_states_only_what_it_measured(self) -> None:
        """A rewritten ledger gets no zero-tamper certificate."""
        self._fill_the_ledger(2)
        # Rewrite the ledger the way a tamperer would: drop one record and
        # truncate the survivor mid-line, so what is on disk is demonstrably
        # not the log that was written.
        raw = self.log_path.read_text(encoding="utf-8")
        self.log_path.write_text(raw.splitlines()[0][:40], encoding="utf-8")

        check = self._audit_log_check()

        self.assertNotIn(
            _ZERO_TAMPER, check["summary"],
            "the ledger at "
            f"{self.log_path} was rewritten and truncated mid-record, and the "
            f"health surface still reports {check['summary']!r} with status "
            f"{check['status']!r} — nothing in the aggregator counts, hashes "
            "or verifies an audit record, so the tamper-event count is a "
            "literal certifying an integrity property nobody checked",
        )


if __name__ == "__main__":
    unittest.main()
