# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Both writers redact the same secret, the same way, from one definition.

THE DEFECT. intergen/glass.py carries a comment saying its credential-key
pattern is "kept in lockstep with intergen/trace.py's", and its stated reason
for declaring a second copy rather than importing one was to avoid depending on
a private name in another module. The two copies then drifted in the way
duplicated security rules always drift: the turn record gained a predicate for
secrets that arrive as CONTENT and the decision tracer did not. Measured before
this change, with content capture deliberately on, the same database URL under
the same neutral key name came out of the two writers differently — redacted
from one, verbatim from the other.

The trace file is smaller than that sounds and it is worth saying why rather
than leaving a reader to assume the worst: its content capture is OFF unless
INTERGEN_TRACE_CONTENT=1 and the process is not running as root, and the file is
written by the same daemon under the same owner-only posture. It is still a
divergence from a stated lockstep, and a machine whose operator deliberately
turns capture on gets the older, weaker behaviour with no warning that it is
weaker.

THE FIX THESE TESTS DESCRIBE. One definition of the shapes, exported from the
module that owns them, imported by the other. Not a second copy kept in step by
good intentions — the lockstep becomes true because there is only one thing.

WHERE THE ASSERTIONS LOOK, AND WHY IT MOVED. These tests used to read
``span.attributes`` straight after ``set_content``, because that is where the
redaction happened. It happens at ``as_record()`` now — the one dictionary the
tracer serialises — so that ``set_attribute`` and ``set_attributes``, which
never redacted anything at all, cannot write a credential to the file by taking
a different door. The assertions therefore read the PERSISTED record, which is
also the thing that actually matters: what is in decisions.jsonl.

The corpus is the same one the turn record's own tests use, so a case added
there is a case both writers must handle. None of its values is a real
credential: they are the documentation examples those formats publish for the
purpose.
"""

from __future__ import annotations

import unittest

from intergen.tests.test_glass_secret_shape_redaction import (
    INNOCENT_CORPUS, SECRET_CORPUS,
)
import intergen.glass as glass
import intergen.trace as trace


def _shared_redactor():
    """The one redaction the two writers are supposed to share, at call time."""
    fn = getattr(glass, "redact_secret_shapes", None)
    if fn is None:
        raise AssertionError(
            "intergen.glass has no PUBLIC redact_secret_shapes. The shapes are "
            "still a private name, so the decision tracer cannot import them "
            "and the only way to give it the same behaviour is a second copy — "
            "which is what drifted in the first place.")
    return fn


def _span_with_capture():
    span = trace.Span(name="probe", kind="internal", trace_id="t", span_id="s")
    span._capture_content = True
    return span


def _persisted(span):
    """The attributes as they reach decisions.jsonl.

    as_record() is the only thing Tracer._write() serialises, so this is the
    record — not a rendering of it, and not the in-memory dictionary a caller
    handed over.
    """
    return span.as_record()["attributes"]


class TheTracerRedactsSecretShapedContent(unittest.TestCase):
    """Every case the turn record catches, the decision tracer must catch."""

    def test_every_corpus_case_is_no_longer_recorded_verbatim(self):
        for label, key, value, shape in SECRET_CORPUS:
            with self.subTest(case=label):
                span = _span_with_capture()
                span.set_content(key, value)
                self.assertNotEqual(_persisted(span)[key], value, (
                    f"{label}: the decision tracer recorded the value "
                    f"byte-identical under the key {key!r}."))

    def test_every_corpus_case_names_the_shape_that_matched(self):
        for label, key, value, shape in SECRET_CORPUS:
            with self.subTest(case=label):
                span = _span_with_capture()
                span.set_content(key, value)
                self.assertIn(f"<redacted:{shape}>", _persisted(span)[key], (
                    f"{label}: the trace record does not say WHAT was removed."))

    def test_ordinary_content_survives_byte_identical(self):
        for label, key, value in INNOCENT_CORPUS:
            with self.subTest(case=label):
                span = _span_with_capture()
                span.set_content(key, value)
                self.assertEqual(_persisted(span)[key], value, (
                    f"{label}: ordinary content was altered in the trace."))

    def test_the_key_name_predicate_is_not_weakened(self):
        span = _span_with_capture()
        span.set_content("api_key", "sk-ant-api03-QYh2n8Zx7RmL0pWvTbKcJdFgHs")
        self.assertEqual(_persisted(span)["api_key"], "<redacted:api_key>")

    def test_an_attribute_is_redacted_even_though_no_setter_redacts_it(self):
        """The door that was open: set_attribute never redacted anything.

        A decision attribute is where a command line most plausibly lands, and
        until the redaction moved to as_record() this wrote straight through.
        """
        span = trace.Span(name="probe", kind="internal",
                          trace_id="t", span_id="s")
        span.set_attribute(
            "chosen_command",
            "mysql -u admin -pHunter2Example --host db.example.invalid")
        span.set_attributes({"api_key": "sk-live-51H8ZqExampleOnlyNotAReal"})
        persisted = _persisted(span)
        self.assertNotIn("Hunter2Example", persisted["chosen_command"])
        self.assertIn("<redacted:password-option>", persisted["chosen_command"])
        self.assertEqual(persisted["api_key"], "<redacted:api_key>")

    def test_the_in_memory_attributes_are_not_mutated(self):
        """Redacting at the write boundary must not corrupt the live span.

        as_record() builds the row that goes to disk. A span is also read in
        process while a turn is running, so a redactor that edited the dict in
        place would change what the running code sees, not just what is stored.
        """
        span = _span_with_capture()
        raw = "mysql -u admin -pHunter2Example --host db.example.invalid"
        span.set_content("command", raw)
        _persisted(span)
        self.assertEqual(span.attributes["command"], raw)

    def test_content_capture_still_off_by_default(self):
        """The redaction is a second line of defence, not a reason to capture."""
        span = trace.Span(name="probe", kind="internal",
                          trace_id="t", span_id="s")
        span.set_content("user_msg", "anything at all")
        self.assertNotIn("user_msg", span.attributes)

    def test_non_string_values_are_left_alone(self):
        span = _span_with_capture()
        for key, value in (("count", 39), ("ok", True), ("ratio", 0.5)):
            span.set_content(key, value)
        self.assertEqual([span.attributes["count"], span.attributes["ok"],
                          span.attributes["ratio"]], [39, True, 0.5])


class TheTwoWritersShareOneDefinition(unittest.TestCase):
    """The lockstep, asserted as identity rather than as a comment."""

    def test_the_tracer_uses_the_turn_record_s_chokepoint_itself(self):
        self.assertIs(getattr(trace, "redact_persisted", None),
                      glass.redact_persisted, (
            "the decision tracer does not name the same redaction object the "
            "turn record writes through; if it has its own copy the two can "
            "drift again, which is exactly the history this change closes."))

    def test_the_tracer_uses_the_turn_record_s_redaction_itself(self):
        fn = _shared_redactor()
        self.assertIs(getattr(trace, "redact_secret_shapes", None), fn, (
            "the decision tracer does not reference the same redaction object; "
            "if it has its own copy the two can drift again, which is exactly "
            "the history this change closes."))

    def test_there_is_no_second_shape_table(self):
        tracer_shapes = getattr(trace, "_SECRET_SHAPES", None)
        if tracer_shapes is not None:
            self.assertIs(tracer_shapes, glass.SECRET_SHAPES, (
                "the decision tracer declares its own shape table. One "
                "definition or none — a second table is the drift."))

    def test_both_writers_produce_the_same_bytes_for_every_case(self):
        for label, key, value, _shape in SECRET_CORPUS:
            with self.subTest(case=label):
                span = _span_with_capture()
                span.set_content(key, value)
                self.assertEqual(_persisted(span)[key],
                                 glass.redact_persisted(value, key), (
                    f"{label}: the two writers redacted the same value "
                    f"differently."))

    def test_the_key_name_patterns_are_still_identical(self):
        """The older half of the lockstep, still asserted rather than assumed."""
        self.assertEqual(glass._SECRET_KEY_RE.pattern,
                         trace._SECRET_KEY_RE.pattern)


if __name__ == "__main__":
    unittest.main()
