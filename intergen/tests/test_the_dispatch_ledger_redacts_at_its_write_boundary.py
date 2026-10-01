# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The dispatch ledger is the third persisted record and nothing redacts it.

THE RULE AS THE TREE STATES IT. ``intergen/trace.py`` says of the credential
predicate: "credentials are never persisted to a trace (security-alignment
rule: secrets stay out of logs)". ``intergen/glass.py`` states the mechanism
that makes the rule true rather than intended -- one function,
:func:`intergen.glass.redact_persisted`, applied to the WHOLE row at the moment
of writing, "not at the moment a caller hands a value over", so that "a writer
that adds a field tomorrow gets the same treatment without knowing this
function exists".

WHAT THIS FILE MEASURES. Two persisted records in this package pass their row
through that chokepoint: ``glass.jsonl`` in ``GlassLogger._write_row`` and
``decisions.jsonl`` in ``Span.as_record``. There is a THIRD persisted record in
the same package, on the same owner-only per-user footing, written by the same
kind of best-effort appender: the tool-dispatch ledger,
``$XDG_STATE_HOME/intergen/tool-dispatch.jsonl``, written by
:func:`intergen.audit_log.write_record`. That writer serialises
``AuditRecord.to_jsonl_dict()`` directly, so ``arguments`` (the tool call's full
argument dict), ``excerpt`` (raw ingress content) and ``details`` (a raw
governance payload) reach the file exactly as the caller handed them over. The
credential shapes the tree already measured reaching the turn record --
password-option, bearer-token, credential-assignment -- land in this file
verbatim and are kept for the ledger's 30-day logrotate retention.

These are the same three assertions
``TheTurnRecordRedactsAtTheWriteBoundary`` makes for ``glass.jsonl``, asked of
the third writer, plus the same two controls: the redactor is shown to catch
these exact credentials before their absence is certified, and a record with no
credential in it is written byte-identical.

One further control belongs to the FIX rather than to the defect. The chokepoint
also replaces a value whose KEY is credential-shaped, so applying it to a whole
ledger row raises the question whether any of the ledger's own field names is
credential-shaped and would be replaced wholesale. The last case asserts that
every field of a plain dispatch record survives the chokepoint unchanged, so a
reader of ``intergen tool-log`` cannot be shown a placeholder where a timestamp
or a decision belongs.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen import audit_log
from intergen import glass
from intergen.interfaces.provenance import (
    AuditRecord,
    DispatchDecision,
    IngressTracker,
    Provenance,
    ToolRiskTier,
)
from intergen.interfaces.types import ToolCall
from intergen.tool_registry import ToolRegistry

# The same two credentials the chokepoint corpus cuts through the turn record,
# imported rather than copied so this file cannot drift from that corpus.
# Neither is real: one is a made-up token, the other the throwaway password
# that documentation examples use.
from intergen.tests.test_persisted_record_redaction_chokepoint import (
    BEARER_COMMAND,
    PASSWORD_COMMAND,
)


def _shape(shape_name: str):
    for name, pattern in glass.SECRET_SHAPES:
        if name == shape_name:
            return pattern
    raise AssertionError(f"the shared corpus no longer defines {shape_name!r}")


def _secret_in(command: str, shape_name: str) -> str:
    """The credential INSIDE a corpus command, derived from the shape itself.

    Copying the secret as a literal beside the imported command is what lets the
    two drift: the command can change in the corpus while the literal keeps
    asserting the absence of a string nothing writes any more, and the case stays
    green over a row that carries the new credential verbatim. So the expectation
    is read out of the corpus with the same pattern the writer redacts by.
    """
    match = _shape(shape_name).search(command)
    assert match is not None, f"{shape_name} no longer matches its corpus command"
    assert "secret" in match.re.groupindex and match.start("secret") >= 0, (
        f"{shape_name} no longer carries a named secret group")
    secret = match.group("secret")
    assert secret and secret in command, (
        f"the derived {shape_name} secret is empty or absent from the corpus")
    return secret


def _around(command: str, shape_name: str) -> tuple[str, str]:
    """The ordinary content on either side of the credential, also derived."""
    match = _shape(shape_name).search(command)
    assert match is not None, f"{shape_name} no longer matches its corpus command"
    return command[:match.start("secret")], command[match.end("secret"):]


def password_secret() -> str:
    """Derived AT USE TIME, deliberately, not frozen into a module constant.

    A constant computed at import is still stale the moment the corpus command
    changes underneath it: the cases would go on asserting the absence of a
    string nothing writes any more, over rows carrying the new credential. Read
    at use, the expectation follows whatever command the case actually sends.
    """
    return _secret_in(PASSWORD_COMMAND, "password-option")


def bearer_secret() -> str:
    return _secret_in(BEARER_COMMAND, "bearer-token")


class TheRedactorIsShownToCatchTheseTwoCredentials(unittest.TestCase):
    """The instrument first: a zero below must mean "removed", not "no rule".

    If these assertions ever fail, the failures further down say nothing about
    the ledger -- they would only mean the shared shape definitions no longer
    recognise the corpus. Shown here so the ledger's result is unambiguous.
    """

    def test_the_expectations_are_derived_from_the_corpus_and_not_empty(self):
        """A "secret not in row" assertion is vacuous if the secret is empty."""
        self.assertTrue(password_secret())
        self.assertTrue(bearer_secret())
        self.assertIn(password_secret(), PASSWORD_COMMAND)
        self.assertIn(bearer_secret(), BEARER_COMMAND)
        self.assertNotEqual(password_secret(), bearer_secret())

    def test_the_password_option_shape_is_recognised(self):
        written = glass.redact_persisted({"command": PASSWORD_COMMAND})
        self.assertNotIn(password_secret(), json.dumps(written))
        self.assertIn("<redacted:password-option>", written["command"])

    def test_the_bearer_token_shape_is_recognised(self):
        written = glass.redact_persisted({"command": BEARER_COMMAND})
        self.assertNotIn(bearer_secret(), json.dumps(written))
        self.assertIn("<redacted:bearer-token>", written["command"])


class TheToolDispatchLedgerRedactsAtTheWriteBoundary(unittest.TestCase):
    """A row reaching tool-dispatch.jsonl by any route is redacted on the way."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="dispatch-ledger-")
        self.addCleanup(self._td.cleanup)
        self.log_path = Path(self._td.name) / "intergen" / "tool-dispatch.jsonl"

    def _rows(self) -> list[dict]:
        if not self.log_path.is_file():
            return []
        return [json.loads(line) for line
                in self.log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    @staticmethod
    def _record(**overrides) -> AuditRecord:
        fields = dict(
            timestamp="2026-09-30T00:00:00+00:00",
            tool_name="run_command",
            arguments={"command": "pkm install firefox"},
            declared_provenance=Provenance.USER_DIRECT.value,
            effective_provenance=Provenance.USER_DIRECT.value,
            ingress_tools_this_turn=[],
            user_decision="executed",
        )
        fields.update(overrides)
        return AuditRecord(**fields)

    def test_the_tool_dispatch_ledger_redacts_a_credential_in_a_tool_argument(self):
        """The named case: the argument dict is the credential's ordinary route.

        A person types a credential into a command far more often than they
        paste a PEM block, and that command line is what ``arguments`` holds.
        """
        self.assertTrue(audit_log.write_record(
            self._record(arguments={"command": PASSWORD_COMMAND},
                         excerpt=BEARER_COMMAND),
            self.log_path))
        line = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn(password_secret(), line)
        self.assertNotIn(bearer_secret(), line)
        row = self._rows()[-1]
        # PRESENT with a NAMED REPLACEMENT, not merely absent. An absence
        # assertion alone is satisfied by a writer that deletes the field, and a
        # deleted excerpt is a worse record than a redacted one, so each
        # sensitive field is asserted to still be there and to say what went.
        self.assertIn("arguments", row)
        self.assertIn("excerpt", row)
        self.assertIn("<redacted:password-option>", row["arguments"]["command"])
        self.assertIn("<redacted:bearer-token>", row["excerpt"])
        head, tail = _around(BEARER_COMMAND, "bearer-token")
        self.assertTrue(row["excerpt"].startswith(head), row["excerpt"])
        self.assertTrue(row["excerpt"].endswith(tail), row["excerpt"])

    def test_the_surrounding_command_survives(self):
        """The control on the redaction: only the credential goes."""
        audit_log.write_record(
            self._record(arguments={"command": PASSWORD_COMMAND}),
            self.log_path)
        written = self._rows()[-1]["arguments"]["command"]
        head, tail = _around(PASSWORD_COMMAND, "password-option")
        self.assertTrue(written.startswith(head), written)
        self.assertTrue(written.endswith(tail), written)
        self.assertIn("<redacted:password-option>", written)

    def test_a_record_that_carries_no_credential_is_written_byte_identical(self):
        """A redactor never shown to leave a row alone is not a redactor.

        BYTES, not parsed objects. Parsed equality is blind to reordering and
        respacing, so a writer that pretty-printed or re-sorted every row would
        satisfy a parsed comparison while changing every line of the file a
        reader and every downstream parser of it see.
        """
        arguments = {"command": "pkm install firefox", "timeout": 30}
        record = self._record(arguments=dict(arguments))
        expected = json.dumps(record.to_jsonl_dict(),
                              separators=(",", ":")).encode("utf-8") + b"\n"
        self.assertTrue(audit_log.write_record(record, self.log_path))
        self.assertEqual(self.log_path.read_bytes(), expected)

    def test_a_governance_event_payload_is_redacted_too(self):
        """``details`` is a raw payload the governance writer hands straight over."""
        audit_log.write_record(
            self._record(
                tool_name="evaluation_blocked",
                arguments={},
                kind="governance_decision",
                event_type="evaluation_blocked",
                details={"tool_name": "run_command",
                         "attempted": PASSWORD_COMMAND}),
            self.log_path)
        self.assertNotIn(password_secret(),
                         self.log_path.read_text(encoding="utf-8"))
        row = self._rows()[-1]
        # The payload must SURVIVE, redacted. Dropping `details` also removes
        # the credential and would pass an absence-only assertion, while costing
        # the record the whole governance event it exists to describe.
        self.assertIn("details", row)
        self.assertEqual(row["details"]["tool_name"], "run_command")
        self.assertIn("<redacted:password-option>", row["details"]["attempted"])
        head, tail = _around(PASSWORD_COMMAND, "password-option")
        self.assertTrue(row["details"]["attempted"].startswith(head))
        self.assertTrue(row["details"]["attempted"].endswith(tail))

    def test_the_production_plumbing_reaches_the_writer_with_the_credential(self):
        """Reachability, through the objects the dispatcher actually builds.

        ``ToolRegistry._audit_log_decision`` hands the live ``ToolCall`` to
        ``build_audit_record``, which copies ``call.arguments`` into the record
        untouched, and then to ``write_record``. Nothing between the tool call
        and the file redacts, so the credential arrives at the boundary; the
        boundary is what has to stop it.
        """
        call = ToolCall(
            name="run_command",
            arguments={"command": PASSWORD_COMMAND},
            call_id="c1",
            source_of_request=Provenance.USER_DIRECT,
        )
        decision = DispatchDecision(
            action="execute",
            effective_provenance=Provenance.USER_DIRECT,
            needs_pkexec=False,
            reason="declared user_direct; no ingress fired this turn",
            risk_tier=ToolRiskTier.PRIVILEGED_STATE_CHANGING,
        )
        # THE PRODUCTION CALLER ITSELF. Calling build_audit_record and
        # write_record by hand here reproduces what the caller does, which means
        # the case stays green even if that caller stops calling them at all --
        # the one thing this case is named for. So the dispatcher's own method is
        # what runs, with the state home redirected so its default path lands in
        # this test's throwaway directory and never in the operator's record.
        state_home = self.log_path.parent.parent
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(state_home)}):
            self.assertEqual(audit_log.default_log_path(), self.log_path)
            ToolRegistry._audit_log_decision(
                call, decision, IngressTracker(),
                user_outcome="executed",
                exit_code=0,
                result_summary="ok",
                source_attribution="",
                excerpt=BEARER_COMMAND,
            )
        written = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn(password_secret(), written)
        self.assertNotIn(bearer_secret(), written)
        row = self._rows()[-1]
        self.assertIn("<redacted:password-option>", row["arguments"]["command"])
        self.assertIn("<redacted:bearer-token>", row["excerpt"])


class TheLedgersOwnFieldNamesAreNotCredentialShaped(unittest.TestCase):
    """The control on the fix: the chokepoint must not eat the record itself.

    ``redact_persisted`` replaces the value of any credential-shaped KEY. The
    ledger's row is now passed through it whole, so this asserts that none of
    the record's own field names trips that predicate -- otherwise a dispatch
    record would come back with a placeholder where its timestamp, tool name or
    user decision belongs, and the reader would be worse off than before.
    """

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="dispatch-ledger-keys-")
        self.addCleanup(self._td.cleanup)
        self.log_path = Path(self._td.name) / "intergen" / "tool-dispatch.jsonl"

    def test_every_field_of_a_plain_record_survives_unchanged(self):
        record = AuditRecord(
            timestamp="2026-10-01T00:00:00+00:00",
            tool_name="read_file",
            arguments={"path": "/etc/fstab", "timeout": 30},
            declared_provenance=Provenance.USER_DIRECT.value,
            effective_provenance=Provenance.USER_DIRECT.value,
            ingress_tools_this_turn=["read_file"],
            user_decision="allowed_once",
            exit_code=0,
            result_summary="read 7 lines",
            source_attribution="/etc/fstab",
            excerpt="",
        )
        expected = record.to_jsonl_dict()
        expected_bytes = json.dumps(
            expected, separators=(",", ":")).encode("utf-8") + b"\n"
        self.assertTrue(audit_log.write_record(record, self.log_path))
        # Raw bytes first: a reordered or respaced row is a changed file even
        # when the parsed object compares equal.
        self.assertEqual(self.log_path.read_bytes(), expected_bytes)
        written = json.loads(
            self.log_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(written, expected)
        self.assertEqual(list(written), list(expected),
                         "the row's field ORDER changed")
        for field, value in expected.items():
            self.assertNotIn(
                "<redacted:", json.dumps(written[field]),
                f"the chokepoint replaced {field}, whose name it must not match")


if __name__ == "__main__":
    unittest.main()
