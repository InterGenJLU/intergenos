# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A credential used as a MAPPING KEY must not survive the write boundary.

``intergen.glass.redact_persisted`` is the chokepoint every persisted row
passes through, and it scrubs VALUES: a credential-shaped key name turns its
value into ``<redacted:key-name>``, and a secret-shaped run inside any string
turns into ``<redacted:shape-name>``. Dicts are walked recursively.

What it does not do is scrub the KEY. Two consequences, both of which put a
credential into a file the function exists to keep credentials out of:

1. A credential-shaped key is copied verbatim INTO the placeholder that
   replaces its value (``<redacted:{key}>``), so a key such as
   ``api_key=<secret>`` is persisted twice over -- once as the key and once
   inside the attestation that claims the secret was removed.
2. A key that is simply the secret itself is never scanned at all, because
   only values reach ``redact_secret_shapes``.

Both are reachable through ``arguments``, which is a tool call's own argument
structure: an environment mapping, a header mapping, or any dict a caller
builds with caller-supplied text for keys.

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
import tempfile
import unittest
from pathlib import Path

from intergen import audit_log
from intergen.glass import redact_persisted, redact_secret_shapes
from intergen.interfaces.provenance import AuditRecord, Provenance

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
        """A key that IS the secret is never scanned, because only values are."""
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


if __name__ == "__main__":
    unittest.main()
