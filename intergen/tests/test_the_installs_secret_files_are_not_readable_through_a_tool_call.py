# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The install's own secret files must not be readable through a tool call.

``intergen/dispatch_token.py`` (the WHY A FILE KEY paragraph, lines 31-39)
justifies keeping the per-install HMAC signing key in a 0600 file rather than in
the login keyring, and closes with the threat model that is supposed to make a
file key sufficient:

    the threat is prompt-INJECTION (which can only make the LLM emit a ToolCall
    -- it cannot read the key file or call the mint), not RCE.

These cells hold that sentence to its promise on the one path a ToolCall takes:
:meth:`intergen.tool_registry.ToolRegistry.execute`. ``read_file`` declares
``SafetyTier.AUTO``, so ``_classify_risk_tier`` maps it to ``READ_ONLY``, and the
RFC section 6 behaviour matrix executes ``READ_ONLY`` for every provenance -- so
the read runs with no review callback consulted, the key comes back inside
``ToolResult.content``, and the registry hands that same string to the audit
record as ``result_summary``. The key file is 0600 and owned by the same account
the daemon runs as, so the tool's ``Path.read_text()`` succeeds.

The assertion is deliberately mechanism-agnostic: it says only that the key's
value must not reach a tool result or the audit record. It does not say HOW --
refusing the path in the tool, classifying such a read as state-changing, or
moving the key -- so it stays valid whichever remedy is chosen.

Hermetic, by the same discipline as intergen/tests/test_dispatch_token.py and
intergen/tests/test_spotlight_chokepoint.py: the keys are generated into a
throwaway directory (never the real ~/.config/intergen), the audit writer is
captured in-process rather than written to disk, and nothing here touches a
service, a socket, a model server, a display or the network. ``analyze_file`` --
the other AUTO file-reading tool named by this finding -- is deliberately NOT
executed: its ``execute()`` posts the whole file body to a local model server,
which this case may not do.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen import dispatch_token as dt
from intergen import tool_registry
from intergen.interfaces.provenance import Provenance
from intergen.interfaces.types import ToolCall
from intergen.tool_registry import ToolRegistry


class SigningKeyIsNotReadableThroughAToolCall(unittest.TestCase):
    """A ToolCall must not be able to hand back the install's secret material."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        # NEVER the real key: dispatch_key_path() resolves the home from the
        # password database, so it would point at the running user's own key.
        self.key_path = self.tmp / ".config" / "intergen" / "dispatch-key"
        self.key = dt.generate_dispatch_key(self.key_path)
        # The web-token precedent the same paragraph leans on, same shape.
        self.token_path = self.tmp / ".config" / "intergen" / "web-token"
        self.web_token = dt.generate_dispatch_key(self.token_path)
        self.registry = ToolRegistry()
        self.registry.discover_tools()
        self.reviews: list[str] = []
        self.audited: list[object] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _review_callback(self, call: ToolCall, decision: object) -> str:
        """A human surface that always refuses. Records that it was consulted."""
        self.reviews.append(call.name)
        return "deny"

    def _read(self, path: Path):
        """Dispatch read_file at `path` through the one gate, capturing audit."""
        call = ToolCall(
            name="read_file",
            arguments={"path": str(path)},
            source_of_request=Provenance.USER_DIRECT,
        )
        with mock.patch.object(tool_registry, "write_record", self.audited.append):
            return self.registry.execute(call, review_callback=self._review_callback)

    def _where_the_secret_landed(self, secret: str, result) -> list[str]:
        audit = "\n".join(
            str(getattr(record, "result_summary", "")) for record in self.audited
        )
        landed = []
        if secret in (result.content or ""):
            landed.append("the tool result's content")
        if secret in (result.model_summary or ""):
            landed.append("the model-facing summary")
        if secret in audit:
            landed.append("the audit record's result_summary")
        return landed

    def test_the_dispatch_signing_key_is_not_readable_through_a_tool_call(self) -> None:
        # Precondition: the fixture really holds the secret we search for.
        self.assertEqual(self.key_path.read_text(), self.key)

        result = self._read(self.key_path)

        landed = self._where_the_secret_landed(self.key, result)
        self.assertEqual(
            landed, [],
            "dispatch_token.py's threat model says a ToolCall 'cannot read the "
            "key file'. read_file dispatched at the key returned it: the "
            f"per-install HMAC signing key reached {', '.join(landed)}. "
            f"Review surface consulted: {self.reviews or 'never'}; "
            f"tool reported success={result.success}. With that key an injected "
            "ToolCall can mint a token for any privileged call, which is the "
            "one thing the root-side verify exists to prevent.",
        )
        self.assertFalse(
            result.success,
            "a read of the install's signing key must not report success; "
            f"content was {result.content!r}",
        )

    def test_the_web_token_is_not_readable_through_a_tool_call(self) -> None:
        # The same paragraph cites the shipped web-token as its precedent, so
        # the promise has to hold for that file too.
        self.assertEqual(self.token_path.read_text(), self.web_token)

        result = self._read(self.token_path)

        landed = self._where_the_secret_landed(self.web_token, result)
        self.assertEqual(
            landed, [],
            "read_file dispatched at the web token returned it: the token "
            f"reached {', '.join(landed)}. Review surface consulted: "
            f"{self.reviews or 'never'}; tool reported success={result.success}.",
        )
        self.assertFalse(
            result.success,
            "a read of the install's web token must not report success; "
            f"content was {result.content!r}",
        )

    def test_a_command_naming_the_key_file_is_refused_too(self) -> None:
        """The same refusal, reached by a string that merely NAMES the file.

        A path argument is not the only way to ask for a file: a command line
        carrying the key's own path is the same request wearing different
        clothes. This cell drives the refusal through a tool whose argument is
        not a path at all, so the guard cannot be one that only inspects
        arguments named ``path``.
        """
        call = ToolCall(
            name="run_command",
            arguments={"command": f"cat {self.key_path}"},
            source_of_request=Provenance.USER_DIRECT,
        )
        with mock.patch.object(tool_registry, "write_record", self.audited.append):
            result = self.registry.execute(call, review_callback=self._review_callback)
        self.assertFalse(
            result.success,
            "a command naming the install's signing key must not report success; "
            f"content was {result.content!r}")
        self.assertNotIn(self.key, result.content or "")
        self.assertEqual(
            self.reviews, [],
            "the refusal must land before the review surface is asked: a human "
            "should never be shown an approval modal for reading the key that "
            "makes approvals verifiable")

    def test_an_ordinary_file_in_the_same_directory_is_still_readable(self) -> None:
        """The control on the refusal: it must refuse the secrets and nothing else.

        A guard that refused the whole configuration directory would break the
        assistant's ability to read its own settings for a person who asked,
        and a refusal that cannot be shown to allow anything is not a guard but
        an outage.
        """
        ordinary = self.key_path.parent / "settings.json"
        ordinary.write_text('{"theme": "dark"}\n', encoding="utf-8")
        result = self._read(ordinary)
        self.assertTrue(
            result.success,
            f"an ordinary file beside the key must still be readable; "
            f"content was {result.content!r}")
        self.assertIn("dark", result.content or "")


if __name__ == "__main__":
    unittest.main()
