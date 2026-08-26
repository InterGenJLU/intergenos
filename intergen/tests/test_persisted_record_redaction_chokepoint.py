# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Redaction happens at the write boundary, so no call site can go round it.

THE DEFECT. Both persisted records applied their credential rules where a caller
handed a value over, not where the bytes were written. The turn record redacted
inside ``glass.emit()``; the decision tracer redacted inside
``Span.set_content()`` — and only there. ``Span.set_attribute()`` and
``Span.set_attributes()``, which is how a routing decision records what it
decided, wrote whatever they were given straight into ``decisions.jsonl``. The
security rule therefore lived in the call sites: it protected the callers who
happened to use the redacting door and no one else, and nothing failed when a
new caller used the other one.

THE FIX THESE TESTS DESCRIBE. One function, :func:`intergen.glass.redact_persisted`,
applied to the WHOLE row at the moment of writing — ``GlassLogger._write_row``
for the turn record, ``Span.as_record`` for the decision tracer. A field added
tomorrow, by a caller who has never read this file, is covered because the
writer covers it rather than because the caller remembered.

WHAT THESE TESTS DO NOT COVER. They measure the two writers in this package.
The installer's separate build tracer (``scripts/lib/igos_trace.py``) has its
own redaction and its own tests in ``tests/trace_lift_smoke/``; it is a
different mechanism on a different file and nothing here says anything about it.
"""

from __future__ import annotations

import ast
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

import intergen.glass as glass
import intergen.trace as trace


# The two credentials cut through this file are the ones measured as reaching
# the shipped record in full. Neither is real: one is a made-up token, the other
# the throwaway password that documentation examples use.
BEARER_COMMAND = (
    "curl -H 'Authorization: Bearer abc123secretvalue' https://example.invalid")
PASSWORD_COMMAND = "mysql -u admin -pHunter2Example --host db.example.invalid"


class TheTurnRecordRedactsAtTheWriteBoundary(unittest.TestCase):
    """A row reaching the file by any route is redacted on the way."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="glass-chokepoint-")
        os.environ["XDG_STATE_HOME"] = self.tmp
        os.environ.pop("INTERGEN_GLASS", None)
        glass._glass = None

    def _rows(self) -> list[dict]:
        path = Path(self.tmp) / "intergen" / "glass.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_a_row_written_without_going_through_emit_is_still_redacted(self):
        """_write_row is the boundary, so it is what the test drives.

        emit() is the ordinary door and it is covered by the corpus tests. This
        one deliberately skips it: a future emitter that builds its own row —
        the rotation marker is already one — must not be able to write a
        credential just by not calling emit().
        """
        logger = glass.get_glass()
        logger._write_row({
            "v": 1, "turn_id": "t", "seq": 0, "run": "r", "ts": 0.0,
            "t_rel_ms": None, "iface": "daemon", "phase": "probe",
            "event": "hand_built",
            "detail": {"command": BEARER_COMMAND, "shell": PASSWORD_COMMAND},
            "dur_ms": None,
        })
        detail = self._rows()[-1]["detail"]
        self.assertNotIn("abc123secretvalue", json.dumps(detail))
        self.assertNotIn("Hunter2Example", json.dumps(detail))
        self.assertIn("<redacted:bearer-token>", detail["command"])
        self.assertIn("<redacted:password-option>", detail["shell"])

    def test_the_surrounding_command_survives(self):
        """The control: only the credential goes, not the command around it."""
        logger = glass.get_glass()
        logger._write_row({
            "v": 1, "turn_id": "t", "seq": 0, "run": "r", "ts": 0.0,
            "t_rel_ms": None, "iface": "daemon", "phase": "probe",
            "event": "hand_built",
            "detail": {"command": PASSWORD_COMMAND}, "dur_ms": None,
        })
        written = self._rows()[-1]["detail"]["command"]
        self.assertTrue(written.startswith("mysql -u admin -p"), written)
        self.assertTrue(written.endswith(" --host db.example.invalid"), written)

    def test_a_row_that_carries_no_credential_is_written_byte_identical(self):
        """A redactor never shown to leave a row alone is not a redactor."""
        detail = {"command": "pkm install firefox", "count": 3, "ok": True}
        logger = glass.get_glass()
        logger._write_row({
            "v": 1, "turn_id": "t", "seq": 0, "run": "r", "ts": 0.0,
            "t_rel_ms": None, "iface": "daemon", "phase": "probe",
            "event": "hand_built", "detail": dict(detail), "dur_ms": None,
        })
        self.assertEqual(self._rows()[-1]["detail"], detail)

    def test_the_caller_s_own_dictionary_is_not_mutated(self):
        """Redaction builds a new row; it does not edit the caller's data.

        A daemon that emits a detail dict it still holds must not find its own
        values replaced underneath it.
        """
        detail = {"command": PASSWORD_COMMAND}
        glass.get_glass()._write_row({
            "v": 1, "turn_id": "t", "seq": 0, "run": "r", "ts": 0.0,
            "t_rel_ms": None, "iface": "daemon", "phase": "probe",
            "event": "hand_built", "detail": detail, "dur_ms": None,
        })
        self.assertEqual(detail["command"], PASSWORD_COMMAND)


class TheDecisionTracerRedactsAtTheWriteBoundary(unittest.TestCase):
    """The door that was open: an attribute, not content."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="trace-chokepoint-")

    def _tracer(self) -> trace.Tracer:
        env = {"INTERGEN_TRACE": "1"}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            return trace.Tracer(log_dir=os.path.join(self.tmp, "intergen"))
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def _records(self, tracer) -> list[dict]:
        path = Path(tracer._log_file)
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_an_attribute_carrying_a_command_reaches_the_file_redacted(self):
        tracer = self._tracer()
        with tracer.span("route") as span:
            span.set_attribute("chosen_command", PASSWORD_COMMAND)
            span.set_attribute("probe_command", BEARER_COMMAND)
        attributes = self._records(tracer)[-1]["attributes"]
        self.assertNotIn("Hunter2Example", json.dumps(attributes))
        self.assertNotIn("abc123secretvalue", json.dumps(attributes))
        self.assertIn("<redacted:password-option>",
                      attributes["chosen_command"])
        self.assertIn("<redacted:bearer-token>", attributes["probe_command"])

    def test_content_capture_is_still_off_by_default(self):
        """Redaction is a second line of defence, not a reason to capture."""
        tracer = self._tracer()
        with tracer.span("route") as span:
            span.set_content("user_msg", "anything at all")
        self.assertNotIn("user_msg", self._records(tracer)[-1]["attributes"])

    def test_ordinary_attributes_are_recorded_unchanged(self):
        """The control, including the counts the key pattern nearly catches."""
        tracer = self._tracer()
        with tracer.span("route") as span:
            span.set_attribute("prompt_tok_count", 812)
            span.set_attribute("completion_tok_count", 96)
            span.set_attribute("eligibility_reason", "no tool matched")
            span.set_attribute("handled", True)
        attributes = self._records(tracer)[-1]["attributes"]
        self.assertEqual(attributes["prompt_tok_count"], 812)
        self.assertEqual(attributes["completion_tok_count"], 96)
        self.assertEqual(attributes["eligibility_reason"], "no tool matched")
        self.assertTrue(attributes["handled"])


class NoTracerAttributeKeyLooksLikeACredential(unittest.TestCase):
    """A count named "tokens_prompt" would be replaced by a placeholder.

    The credential-key pattern matches "token" as a SUBSTRING, which is what it
    has to do — "user_token" and "auth_token" are credentials. The cost is that
    an integer count named "tokens_prompt" is indistinguishable from one, and
    once the tracer redacts every attribute at the write boundary, such a count
    goes to the file as "<redacted:tokens_prompt>" and the number is gone with
    no error anywhere. The turn record already had this collision and already
    resolved it the same way, by naming counts "prompt_tok_count" (see
    intergen/llm.py and intergen/web_server.py, which both say do not rename
    them back). This test is that rule for the tracer, enforced instead of
    commented.

    Weakening the credential pattern to rescue a metric is the wrong trade and
    is not what this does: the pattern is untouched and the KEY is chosen.

    Only shipped modules are scanned. Test modules under intergen/tests use
    credential-shaped keys on purpose, to prove the redaction fires.
    """

    def _shipped_modules(self) -> list[Path]:
        package = Path(glass.__file__).resolve().parent
        return [p for p in sorted(package.rglob("*.py"))
                if "tests" not in p.relative_to(package).parts]

    def _attribute_keys(self) -> list[tuple[Path, int, str]]:
        found: list[tuple[Path, int, str]] = []
        for path in self._shipped_modules():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", None)
                if name == "set_attribute" and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and \
                            isinstance(first.value, str):
                        found.append((path, node.lineno, first.value))
                elif name == "set_attributes" and node.args and \
                        isinstance(node.args[0], ast.Dict):
                    for key in node.args[0].keys:
                        if isinstance(key, ast.Constant) and \
                                isinstance(key.value, str):
                            found.append((path, node.lineno, key.value))
        return found

    def test_the_scan_finds_attribute_keys_at_all(self):
        """A zero below must mean "none matched", never "none were looked at"."""
        keys = self._attribute_keys()
        self.assertGreater(len(keys), 20, (
            "the scan found almost no tracer attribute keys, so the check "
            f"below would pass on an empty set. Found: {keys}"))

    def test_the_scan_would_catch_a_credential_shaped_key(self):
        """The instrument is shown to detect a true positive before it certifies a zero."""
        pattern = glass._SECRET_KEY_RE
        for bad in ("tokens_prompt", "user_token", "api_key", "password"):
            with self.subTest(key=bad):
                self.assertIsNotNone(pattern.search(bad))

    def test_no_shipped_attribute_key_matches_the_credential_pattern(self):
        pattern = glass._SECRET_KEY_RE
        offenders = [(str(p), line, key) for p, line, key
                     in self._attribute_keys() if pattern.search(key)]
        self.assertEqual(offenders, [], (
            "these trace attribute keys look credential-shaped to "
            "glass._SECRET_KEY_RE, so their values will be replaced by a "
            "placeholder in decisions.jsonl. If the value really is a "
            "credential that is correct and this list should not contain it. "
            "If it is a count or a label, rename the KEY — a count is named "
            "'prompt_tok_count', not 'tokens_prompt'.\n"
            f"  {offenders}"))


class TheChokepointIsWhereTheDocumentationSaysItIs(unittest.TestCase):
    """The claim that there is one chokepoint, asserted rather than written."""

    def test_the_two_writers_call_the_same_function(self):
        self.assertIs(trace.redact_persisted, glass.redact_persisted)

    def test_the_private_alias_still_resolves_to_it(self):
        """tests/installed/ imports glass._redact by that name."""
        self.assertIs(glass._redact, glass.redact_persisted)

    def test_the_glass_writer_redacts_in_the_write_path_not_in_emit(self):
        """Read as source, deliberately: this is about WHERE the call sits.

        Reading the source is normally the weaker way to test something, and it
        is the right way here — the property under test is the location of a
        call, and a behaviour test cannot tell "emit redacts" apart from "the
        writer redacts" when the ordinary path runs both.
        """
        source = Path(glass.__file__).read_text(encoding="utf-8")
        write_row = source.split("def _write_row(", 1)[1].split("\n    def ", 1)[0]
        emit_body = source.split("    def emit(", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("redact_persisted(record)", write_row,
                      "the write path does not redact the row it is about to "
                      "serialise, so the chokepoint is not where it is said "
                      "to be.")
        self.assertNotIn("_redact(detail", emit_body,
                         "emit() still redacts, so there are two places doing "
                         "it and they can disagree.")

    def test_the_tracer_redacts_in_the_record_it_serialises(self):
        source = Path(trace.__file__).read_text(encoding="utf-8")
        as_record = source.split("    def as_record(", 1)[1].split("\n\nclass ", 1)[0]
        self.assertIn("redact_persisted(self.attributes)", as_record)


if __name__ == "__main__":
    unittest.main()
