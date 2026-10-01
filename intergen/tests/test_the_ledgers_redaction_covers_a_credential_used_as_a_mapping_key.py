# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A credential used as a MAPPING KEY must not survive the write boundary.

Before this correction, the shared persistence transformation scrubbed mapping
values but left mapping keys unchanged. That former asymmetry let credential
material survive as a key and, for a credential-shaped key name, reappear
inside the value's redaction label. A bare secret used as a key did not reach
the content scanner. Both former paths are reachable through caller-supplied
argument mappings; the cases below preserve those reds and assert the corrected
boundary.

The cells below hold the function and the REAL writer to the rule the module
states in its own words: credentials are never persisted. They also pin the two
properties a fix must not break -- an ordinary record's field names must survive
unchanged, and two distinct secret keys must not collapse into one entry, since
this function's mandate is that content is never silently dropped.

Hermetic: a throwaway directory per test, no service, socket, model server,
display or network, and the secrets here are synthetic strings generated in this
file.
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
from intergen import trace
from intergen.glass import redact_persisted, redact_secret_shapes
from intergen.interfaces.provenance import (
    AuditRecord,
    DispatchDecision,
    IngressTracker,
    Provenance,
    ToolRiskTier,
)
from intergen.interfaces.types import ToolCall
from intergen.tool_registry import ToolRegistry

# The corpus credential and its DERIVATION, imported rather than copied, from the
# module that owns both. A literal copied beside an imported command goes stale
# the moment the command changes, which is the defect a second reader found in
# that module and which is corrected there.
from intergen.tests.test_the_dispatch_ledger_redacts_at_its_write_boundary import (
    BEARER_COMMAND,
    bearer_secret,
)

# Synthetic, never a real credential. Two distinct values of the same shape, so
# the no-silent-drop property can be measured.
SECRET_A = "sk-live-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
SECRET_B = "sk-live-" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4"


class ACredentialUsedAsAMappingKeyIsNotPersisted(unittest.TestCase):
    """The key side of the chokepoint, through the function and the writer."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="ledger-mapping-key-")
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
            timestamp="2026-10-01T00:00:00+00:00",
            tool_name="run_command",
            arguments={"command": "pkm install firefox"},
            declared_provenance=Provenance.USER_DIRECT.value,
            effective_provenance=Provenance.USER_DIRECT.value,
            ingress_tools_this_turn=[],
            user_decision="executed",
        )
        fields.update(overrides)
        return AuditRecord(**fields)

    # ---- the two defects -------------------------------------------------

    def test_a_credential_shaped_mapping_key_is_not_copied_into_its_placeholder(self):
        """``<redacted:{key}>`` must not echo the key's own secret material."""
        row = redact_persisted({"arguments": {"env": {f"api_key={SECRET_A}": "set"}}})
        self.assertNotIn(
            SECRET_A, json.dumps(row),
            "the credential-shaped key was copied into the placeholder that "
            "claims to have removed it, so the secret is persisted twice: "
            f"{json.dumps(row)}")

    def test_a_bare_secret_used_as_a_mapping_key_is_not_persisted(self):
        """Before this correction, a key that was itself the secret never
        reached the shape scanner."""
        row = redact_persisted({"arguments": {"headers": {SECRET_A: "bearer"}}})
        self.assertNotIn(
            SECRET_A, json.dumps(row),
            "a secret used as a mapping key reached the record verbatim: "
            f"{json.dumps(row)}")

    def test_the_real_writer_keeps_a_mapping_key_credential_out_of_the_file(self):
        """End to end, through the shipped writer, read back from disk."""
        self.assertTrue(audit_log.write_record(
            self._record(arguments={"env": {f"api_key={SECRET_A}": "set"},
                                    "headers": {SECRET_B: "bearer"}}),
            self.log_path))
        written = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn(SECRET_A, written,
                         "the credential-shaped mapping key reached the file")
        self.assertNotIn(SECRET_B, written,
                         "the bare secret mapping key reached the file")
        row = self._rows()[-1]
        # Present with a named replacement, not merely absent: a writer that
        # dropped the mapping would also pass an absence-only assertion.
        self.assertIn("env", row["arguments"])
        self.assertIn("headers", row["arguments"])
        self.assertEqual(list(row["arguments"]["env"].values()), ["<redacted:credential-shaped-key>"])
        self.assertEqual(list(row["arguments"]["headers"].values()), ["bearer"])

    def test_the_production_caller_itself_keeps_a_mapping_key_credential_out(self):
        """The dispatcher's own method, not a reproduction of what it does.

        The corpus credential is used here, derived at use time, so this cell
        cannot drift from the shared corpus; and the state home is redirected so
        the production default path lands in this test's throwaway directory.
        """
        secret = bearer_secret()
        # The derivation really is the shared corpus's, not a value invented here.
        self.assertIn(secret, BEARER_COMMAND)
        call = ToolCall(
            name="probe",
            arguments={"headers": {f"Authorization: Bearer {secret}": "present"}},
            call_id="mapping-key-production-caller",
            source_of_request=Provenance.USER_DIRECT,
        )
        decision = DispatchDecision(
            action="execute",
            effective_provenance=Provenance.USER_DIRECT,
            needs_pkexec=False,
            reason="no ingress fired this turn",
            risk_tier=ToolRiskTier.READ_ONLY,
        )
        state_home = self.log_path.parent.parent
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(state_home)}):
            self.assertEqual(audit_log.default_log_path(), self.log_path)
            ToolRegistry._audit_log_decision(
                call, decision, IngressTracker(),
                user_outcome="executed",
                exit_code=0,
                result_summary="ok",
                source_attribution="",
                excerpt="",
            )
        written = self.log_path.read_text(encoding="utf-8")
        self.assertTrue(written, "the production caller wrote nothing at all")
        self.assertNotIn(secret, written,
                         "the production caller persisted a mapping-key credential")
        row = self._rows()[-1]
        self.assertIn("headers", row["arguments"])
        self.assertEqual(len(row["arguments"]["headers"]), 1)

    # ---- the controls, which must pass before the fix as well as after ----

    def test_the_same_secret_as_a_VALUE_is_redacted_so_the_scanner_can_see_it(self):
        """The contrast control: the shape IS detectable, so an absence means
        something. Without this cell, the two assertions above could pass on a
        scanner that sees nothing at all."""
        row = redact_persisted(
            {"arguments": {"command": f"curl -H 'api_key: {SECRET_A}'"}})
        self.assertNotIn(SECRET_A, json.dumps(row))
        self.assertIn("<redacted:provider-token>",
                      row["arguments"]["command"])
        self.assertNotEqual(redact_secret_shapes(SECRET_A), SECRET_A,
                            "the shape scanner must recognise this shape")

    def test_every_field_name_of_a_plain_record_survives_unchanged(self):
        """The over-reach control: a reader must not be shown a placeholder
        where a field name belongs."""
        plain = self._record(arguments={"path": "/etc/fstab", "timeout": 30})
        before = plain.to_jsonl_dict()
        after = redact_persisted(before)
        self.assertEqual(sorted(before), sorted(after),
                         "a plain record's own field names were rewritten")
        self.assertEqual(sorted(before["arguments"]),
                         sorted(after["arguments"]),
                         "a plain argument dict's keys were rewritten")

    def test_two_distinct_secret_keys_do_not_collapse_into_one_entry(self):
        """No silent drop: scrubbing keys must keep them distinct, because this
        function's stated mandate is that content is never dropped silently."""
        row = redact_persisted(
            {"arguments": {"env": {SECRET_A: "one", SECRET_B: "two"}}})
        env = row["arguments"]["env"]
        self.assertEqual(len(env), 2,
                         "two distinct secret keys collapsed into one entry, "
                         f"so a row was silently dropped: {json.dumps(row)}")
        self.assertEqual(sorted(env.values()), ["one", "two"],
                         "both values must still be present and paired")
        self.assertNotIn(SECRET_A, json.dumps(row))
        self.assertNotIn(SECRET_B, json.dumps(row))


class EveryPersistedWriterIsCoveredForMappingKeys(unittest.TestCase):
    """The key path, through EVERY persisted writer, with its ordinary control.

    The chokepoint is shared, so a key-path gap is a gap in all three records at
    once: the tool-dispatch ledger, the turn record and the decision tracer. One
    writer proving the point would leave the other two asserted by argument
    rather than by measurement, so each is driven here with the same pair -- a
    credential used as a mapping key, which must not survive, and an ordinary
    mapping whose keys must survive exactly.
    """

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="every-writer-keys-")
        self.addCleanup(self._td.cleanup)
        self.state_home = Path(self._td.name)
        self.secret = bearer_secret()
        self.assertIn(self.secret, BEARER_COMMAND)
        self.key_with_secret = f"Authorization: Bearer {self.secret}"

    # ---- writer 1: the tool-dispatch ledger ---------------------------------

    def _ledger_row(self, arguments: dict) -> dict:
        path = self.state_home / "intergen" / "tool-dispatch.jsonl"
        record = AuditRecord(
            timestamp="2026-10-01T00:00:00+00:00",
            tool_name="probe",
            arguments=arguments,
            declared_provenance=Provenance.USER_DIRECT.value,
            effective_provenance=Provenance.USER_DIRECT.value,
            ingress_tools_this_turn=[],
            user_decision="executed",
        )
        self.assertTrue(audit_log.write_record(record, path))
        return json.loads(path.read_text(encoding="utf-8").splitlines()[-1])

    def test_the_tool_dispatch_ledger_covers_the_key_path(self):
        row = self._ledger_row({"headers": {self.key_with_secret: "present"}})
        self.assertNotIn(self.secret, json.dumps(row))

    def test_the_tool_dispatch_ledger_leaves_an_ordinary_mapping_alone(self):
        ordinary = {"path": "/etc/fstab", "timeout": 30, "retries": 2}
        row = self._ledger_row({"options": dict(ordinary)})
        self.assertEqual(row["arguments"]["options"], ordinary)

    # ---- writer 2: the turn record -----------------------------------------

    def _glass_detail(self, detail: dict) -> dict:
        previous = glass._glass
        self.addCleanup(setattr, glass, "_glass", previous)
        with mock.patch.dict(os.environ,
                             {"XDG_STATE_HOME": str(self.state_home)}):
            os.environ.pop("INTERGEN_GLASS", None)
            glass._glass = None
            logger = glass.get_glass()
            logger._write_row({
                "v": 1, "turn_id": "t", "seq": 0, "run": "r", "ts": 0.0,
                "t_rel_ms": None, "iface": "daemon", "phase": "probe",
                "event": "hand_built", "detail": detail, "dur_ms": None,
            })
        path = self.state_home / "intergen" / "glass.jsonl"
        rows = [json.loads(line) for line
                in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return rows[-1]["detail"]

    def test_the_turn_record_covers_the_key_path(self):
        detail = self._glass_detail({"headers": {self.key_with_secret: "yes"}})
        self.assertNotIn(self.secret, json.dumps(detail))

    def test_the_turn_record_leaves_an_ordinary_mapping_alone(self):
        ordinary = {"command": "pkm install firefox", "count": 3, "ok": True}
        self.assertEqual(self._glass_detail({"detail": dict(ordinary)})["detail"],
                         ordinary)

    # ---- writer 3: the decision tracer -------------------------------------

    def _traced_attributes(self, name: str, value) -> dict:
        with mock.patch.dict(os.environ, {"INTERGEN_TRACE": "1"}):
            tracer = trace.Tracer(
                log_dir=str(self.state_home / "intergen"))
            with tracer.span("route") as span:
                span.set_attribute(name, value)
        rows = [json.loads(line) for line
                in Path(tracer._log_file).read_text(
                    encoding="utf-8").splitlines() if line.strip()]
        return rows[-1]["attributes"]

    def test_the_decision_tracer_covers_the_key_path(self):
        attributes = self._traced_attributes(
            "headers", {self.key_with_secret: "present"})
        self.assertNotIn(self.secret, json.dumps(attributes))

    def test_the_decision_tracer_leaves_an_ordinary_mapping_alone(self):
        ordinary = {"intent": "install", "clauses": 2}
        self.assertEqual(self._traced_attributes("route", dict(ordinary)),
                         {"route": ordinary})


# Two distinct synthetic strings of the SAME shape, chosen because that shape's
# placeholder name is not itself credential-shaped: the key-name predicate leaves
# a value whose key spells ``<redacted:crypt-hash>`` alone, so these cases can
# assert the VALUES are still paired with their keys and not merely that the
# entries are still counted. Neither string is a real hash of anything.
CRYPT_A = "$6$" + "abcdefgh" + "$" + "0123456789abcdefghijABCD"
CRYPT_B = "$6$" + "zyxwvuts" + "$" + "9876543210zyxwvutsrqPQRS"


class AMappingKeyCollisionNeverLosesAnEntry(unittest.TestCase):
    """Scrubbing a key must not take another entry of the mapping with it.

    A scrub maps every secret of one shape onto ONE placeholder spelling, so the
    transform has to disambiguate. Disambiguating against the entries already
    emitted is not enough: an ORDINARY key -- one the scanner leaves alone --
    keeps its own spelling, and a caller can spell it exactly like the generated
    placeholder, or exactly like the occurrence marker the transform appends.
    When such a key arrives AFTER the scrubbed one it is placed unchanged over
    the entry already there and the mapping comes back one entry short, with no
    signal anywhere that a row of the record went missing; when it arrives
    BEFORE, both survive. That is a silent drop in a function whose stated
    mandate is that content is never dropped silently, and it makes the record
    depend on the order a caller happened to build a dict in.

    The property these cells hold the transform to: the output mapping has
    exactly as many entries as the input, whichever order the keys arrive in,
    with every value still paired with the key it came in on. The marker a
    particular secret key is given may differ between orders -- two keys of one
    shape are told apart by the order they were read, which is visible in the
    record and is not a loss -- so these cells assert the entry count, the set of
    surviving ordinary spellings and the values, not which secret got which
    marker.

    Keys are the caller's: ``arguments`` is a tool call's own argument structure,
    so an environment mapping or a header mapping carries whatever text the
    caller put there, including text that spells this module's own placeholders.

    Hermetic, as the rest of this file: a throwaway directory per test, no
    service, socket, model server, display or network, and every secret here is a
    synthetic string generated in this file or derived from the shared corpus.
    """

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="ledger-key-collision-")
        self.addCleanup(self._td.cleanup)
        self.state_home = Path(self._td.name)
        self.log_path = self.state_home / "intergen" / "tool-dispatch.jsonl"
        self.placeholder = redact_secret_shapes(CRYPT_A)
        self.marker = f"{self.placeholder}#2"
        # The shape really is recognised and the two spellings really do pass the
        # scanner untouched -- without this the cells below could pass on a
        # scanner that sees nothing and a transform that therefore never
        # collides.
        self.assertNotEqual(self.placeholder, CRYPT_A)
        self.assertEqual(redact_secret_shapes(CRYPT_B), self.placeholder)
        self.assertEqual(redact_secret_shapes(self.placeholder), self.placeholder)
        self.assertEqual(redact_secret_shapes(self.marker), self.marker)

    def _env(self, mapping: dict) -> dict:
        """The transform's own output for one caller-built mapping."""
        return redact_persisted(
            {"arguments": {"env": dict(mapping)}})["arguments"]["env"]

    def _assert_nothing_lost(self, mapping: dict, out: dict) -> None:
        self.assertEqual(
            len(out), len(mapping),
            "the mapping came back short, so an entry of the record was "
            f"dropped with no signal: in {list(mapping)!r} out {out!r}")
        self.assertEqual(sorted(out.values()), sorted(mapping.values()),
                         f"a value was lost or re-paired: {out!r}")
        for key in mapping:
            if redact_secret_shapes(key) == key:
                self.assertIn(key, out,
                              "an ordinary key lost its own spelling, which is "
                              "the one thing a reader uses to find the field")
            else:
                self.assertNotIn(key, json.dumps(out),
                                 "a secret key survived into the record")

    # ---- the defect: the ordinary key arrives after the scrubbed one --------

    def test_an_ordinary_key_spelling_the_placeholder_after_a_scrubbed_key(self):
        """The drop. Both entries must come back, distinguishable."""
        mapping = {CRYPT_A: "from the secret key",
                   self.placeholder: "from the ordinary key"}
        self._assert_nothing_lost(mapping, self._env(mapping))

    def test_an_ordinary_key_spelling_the_marker_after_two_scrubbed_keys(self):
        """The same defect reached through the occurrence marker itself."""
        mapping = {CRYPT_A: "one", CRYPT_B: "two", self.marker: "ordinary"}
        self._assert_nothing_lost(mapping, self._env(mapping))

    # ---- the orders that already held, kept as controls ---------------------

    def test_an_ordinary_key_spelling_the_placeholder_before_a_scrubbed_key(self):
        """The order that already survived: it must keep surviving."""
        mapping = {self.placeholder: "from the ordinary key",
                   CRYPT_A: "from the secret key"}
        self._assert_nothing_lost(mapping, self._env(mapping))

    def test_an_ordinary_key_spelling_the_marker_before_two_scrubbed_keys(self):
        mapping = {self.marker: "ordinary", CRYPT_A: "one", CRYPT_B: "two"}
        self._assert_nothing_lost(mapping, self._env(mapping))

    # ---- the order-independence the two together establish ------------------

    def test_the_record_does_not_depend_on_the_order_the_keys_arrived_in(self):
        """A record that survives one ordering and not its reverse is a record
        whose content depends on how a caller happened to build a dict."""
        forward = {CRYPT_A: "one", CRYPT_B: "two", self.marker: "ordinary",
                   self.placeholder: "also ordinary"}
        reverse = dict(reversed(list(forward.items())))
        out_forward = self._env(forward)
        out_reverse = self._env(reverse)
        self._assert_nothing_lost(forward, out_forward)
        self._assert_nothing_lost(reverse, out_reverse)
        self.assertEqual(len(out_forward), len(out_reverse))
        self.assertEqual(sorted(out_forward), sorted(out_reverse),
                         "the two orders produced different KEY SETS, so the "
                         f"record depends on input order: {out_forward!r} "
                         f"vs {out_reverse!r}")
        self.assertEqual(sorted(out_forward.values()),
                         sorted(out_reverse.values()))

    def test_the_readers_measured_provider_token_pair_keeps_both_entries(self):
        """The reader's own spelling, kept so its reproduction is pinned here.

        ``<redacted:provider-token>`` is itself credential-shaped as a field
        NAME, so the value behind it is replaced by the key-name predicate -- an
        older, separate and correct behaviour. Only the entry count is asserted
        here; the value pairing is measured on the shape above, whose placeholder
        name the predicate does not match.
        """
        mapping = {SECRET_A: "one", "<redacted:provider-token>": "two"}
        out = self._env(mapping)
        self.assertEqual(len(out), 2,
                         f"an entry was dropped silently: {out!r}")
        self.assertNotIn(SECRET_A, json.dumps(out))

    # ---- the over-reach control on the reservation pass ---------------------

    def test_a_mapping_with_no_secret_key_at_all_is_left_exactly_alone(self):
        """Reserving spellings must cost an ordinary record nothing."""
        ordinary = {"path": "/etc/fstab", "timeout": 30, "retries": 2,
                    "<redacted:crypt-hash>": "a caller's own odd field name"}
        out = self._env(ordinary)
        self.assertEqual(out, ordinary,
                         "an ordinary mapping was rewritten")
        self.assertEqual(list(out), list(ordinary),
                         "an ordinary mapping's key ORDER changed")

    def test_a_non_string_key_beside_a_colliding_pair_survives_unchanged(self):
        """Non-string keys have nothing to scan and must not be moved."""
        mapping = {CRYPT_A: "secret", self.placeholder: "ordinary",
                   7: "seven", True: "true"}
        out = self._env(mapping)
        self.assertEqual(len(out), len(mapping), f"an entry was dropped: {out!r}")
        self.assertEqual(out[7], "seven")
        self.assertEqual(out[True], "true")

    # ---- the real writers, read back from the files they wrote -------------

    def _record(self, arguments: dict) -> AuditRecord:
        return AuditRecord(
            timestamp="2026-10-01T00:00:00+00:00",
            tool_name="probe",
            arguments=arguments,
            declared_provenance=Provenance.USER_DIRECT.value,
            effective_provenance=Provenance.USER_DIRECT.value,
            ingress_tools_this_turn=[],
            user_decision="executed",
        )

    def test_the_tool_dispatch_ledger_keeps_both_colliding_entries_on_disk(self):
        mapping = {CRYPT_A: "from the secret key",
                   self.placeholder: "from the ordinary key"}
        self.assertTrue(audit_log.write_record(
            self._record({"env": dict(mapping)}), self.log_path))
        written = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn(CRYPT_A, written, "the secret key reached the file")
        row = json.loads(written.splitlines()[-1])
        self._assert_nothing_lost(mapping, row["arguments"]["env"])

    def test_the_production_caller_keeps_both_colliding_entries_on_disk(self):
        """The dispatcher's own method, with the state home redirected.

        The corpus credential is derived at use time so this cell cannot drift
        from the shared corpus, and the collision is built from it: the ordinary
        key spells the placeholder the corpus secret scrubs to.
        """
        secret = bearer_secret()
        self.assertIn(secret, BEARER_COMMAND)
        key_with_secret = f"Authorization: Bearer {secret}"
        placeholder = redact_secret_shapes(key_with_secret)
        self.assertNotEqual(placeholder, key_with_secret)
        call = ToolCall(
            name="probe",
            arguments={"headers": {key_with_secret: "from the secret key",
                                   placeholder: "from the ordinary key"}},
            call_id="mapping-key-collision-production-caller",
            source_of_request=Provenance.USER_DIRECT,
        )
        decision = DispatchDecision(
            action="execute",
            effective_provenance=Provenance.USER_DIRECT,
            needs_pkexec=False,
            reason="no ingress fired this turn",
            risk_tier=ToolRiskTier.READ_ONLY,
        )
        with mock.patch.dict(os.environ,
                             {"XDG_STATE_HOME": str(self.state_home)}):
            self.assertEqual(audit_log.default_log_path(), self.log_path)
            ToolRegistry._audit_log_decision(
                call, decision, IngressTracker(),
                user_outcome="executed",
                exit_code=0,
                result_summary="ok",
                source_attribution="",
                excerpt="",
            )
        written = self.log_path.read_text(encoding="utf-8")
        self.assertTrue(written, "the production caller wrote nothing at all")
        self.assertNotIn(secret, written,
                         "the production caller persisted a mapping-key "
                         "credential")
        headers = json.loads(written.splitlines()[-1])["arguments"]["headers"]
        self.assertEqual(
            len(headers), 2,
            "the production caller dropped an entry of the record with no "
            f"signal: {headers!r}")
        self.assertIn(placeholder, headers,
                      "the ordinary key lost its own spelling")

    def test_the_turn_record_keeps_both_colliding_entries_on_disk(self):
        mapping = {CRYPT_A: "from the secret key",
                   self.placeholder: "from the ordinary key"}
        previous = glass._glass
        self.addCleanup(setattr, glass, "_glass", previous)
        with mock.patch.dict(os.environ,
                             {"XDG_STATE_HOME": str(self.state_home)}):
            os.environ.pop("INTERGEN_GLASS", None)
            glass._glass = None
            glass.get_glass()._write_row({
                "v": 1, "turn_id": "t", "seq": 0, "run": "r", "ts": 0.0,
                "t_rel_ms": None, "iface": "daemon", "phase": "probe",
                "event": "hand_built", "detail": {"env": dict(mapping)},
                "dur_ms": None,
            })
        written = (self.state_home / "intergen" / "glass.jsonl").read_text(
            encoding="utf-8")
        self.assertNotIn(CRYPT_A, written, "the secret key reached the file")
        row = json.loads(written.splitlines()[-1])
        self._assert_nothing_lost(mapping, row["detail"]["env"])

    def test_the_decision_tracer_keeps_both_colliding_entries_on_disk(self):
        mapping = {CRYPT_A: "from the secret key",
                   self.placeholder: "from the ordinary key"}
        with mock.patch.dict(os.environ, {"INTERGEN_TRACE": "1"}):
            tracer = trace.Tracer(log_dir=str(self.state_home / "intergen"))
            with tracer.span("route") as span:
                span.set_attribute("env", dict(mapping))
        written = Path(tracer._log_file).read_text(encoding="utf-8")
        self.assertNotIn(CRYPT_A, written, "the secret key reached the file")
        row = json.loads(written.splitlines()[-1])
        self._assert_nothing_lost(mapping, row["attributes"]["env"])



if __name__ == "__main__":
    unittest.main()
