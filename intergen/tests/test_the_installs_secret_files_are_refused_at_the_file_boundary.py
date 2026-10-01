# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The install's own secret files are refused at the file boundary, not by name.

The previous correction screened tool arguments for the canonical relative names
of the install's secret files. A second read of that change reproduced four
routes to the same material and two harmless requests it stopped, and both
classes have the same cause: the screen compares argument TEXT, while the thing
that returns the file is an OPEN, performed after the text has been compared and
on whatever object the name happens to resolve to by then.

These cells hold the promise at the open instead:

* a parent-directory spelling, a symbolic link and a second name for the same
  file (a hard link) all reach the same bytes and must all be refused;
* a path carried as a MAPPING KEY must be refused, because a tool is free to
  take its paths that way and the structure walk only looked at values;
* an ordinary longer filename that merely begins with the protected name, and
  descriptive prose that mentions the protected name, must NOT be refused: they
  open nothing, and refusing them is an outage, not a guard;
* the direct spelling, a command line naming the file, and an ordinary read
  beside the secret keep behaving as the previous correction left them.

One route is named and NOT closed here. A tool that runs as its own process can
be handed an ordinary containing directory and derive the protected child
itself; no check inside this package ever sees the protected name, so no change
in this package can refuse it. The cell for it records that limit and holds the
module text to stating it, rather than letting the package claim a boundary it
does not have. Closing it needs the boundary that confines the tool's process,
which is not this correction.

Hermetic, by the discipline of the case this extends: every key is generated
into a throwaway directory (never the real ~/.config/intergen), the audit writer
is captured in process, the one cell that drives analyze_file refuses the
network outright and asserts it was never reached, and nothing here touches a
service, a socket, a model server or a display.
"""

from __future__ import annotations

import os
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from intergen import dispatch_token as dt
from intergen import tool_registry
from intergen.interfaces.provenance import Provenance
from intergen.interfaces.types import SafetyTier, ToolCall, ToolSchema
from intergen.tool_registry import ToolRegistry, _names_an_install_secret
from intergen.tools import write_file as write_file_tool


class TheSecretFilesAreRefusedAtTheFileBoundary(unittest.TestCase):
    """What is refused is the FILE, however the request spelled its name."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.key_path = self.tmp / ".config" / "intergen" / "dispatch-key"
        self.key = dt.generate_dispatch_key(self.key_path)
        self.registry = ToolRegistry()
        self.registry.discover_tools()
        self.reviews: list[str] = []
        self.audited: list[object] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # --- the one dispatch path, with the audit writer captured ---------------

    def _review_callback(self, call: ToolCall, decision: object) -> str:
        self.reviews.append(call.name)
        return "deny"

    def _dispatch(self, name: str, arguments: dict):
        call = ToolCall(
            name=name,
            arguments=arguments,
            source_of_request=Provenance.USER_DIRECT,
        )
        with mock.patch.object(tool_registry, "write_record", self.audited.append):
            return self.registry.execute(call, review_callback=self._review_callback)

    def _read(self, path: Path):
        return self._dispatch("read_file", {"path": str(path)})

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

    def _assert_refused(self, result, spelling: str) -> None:
        landed = self._where_the_secret_landed(self.key, result)
        self.assertEqual(
            landed, [],
            f"the signing key reached {', '.join(landed)} through {spelling}. "
            "The refusal is decided on the file that was opened, not on the "
            "name the request used to ask for it.",
        )
        self.assertFalse(
            result.success,
            f"a read of the signing key through {spelling} must not report "
            f"success; content was {result.content!r}",
        )

    def _as_if_the_install_lived_here(self):
        """Resolve the owning account's home to the fixture, as the module does.

        ``dispatch_token`` finds its own files through the password database and
        not through HOME, on purpose. A cell that needs the install's secret
        files to BE the fixture's files therefore answers that lookup, which is
        also the only honest way to exercise a second name for the same inode
        without touching the real key.
        """
        fake = mock.MagicMock()
        fake.getpwuid.return_value.pw_dir = str(self.tmp)
        fake.getpwnam.return_value.pw_dir = str(self.tmp)
        return mock.patch.object(dt, "pwd", fake)

    # --- a1: four spellings of the same file --------------------------------

    def test_a_parent_directory_spelling_of_the_key_is_refused(self) -> None:
        detour = self.key_path.parent / "detour"
        detour.mkdir()
        spelled = detour / ".." / self.key_path.name
        self.assertEqual(Path(os.path.realpath(spelled)), self.key_path.resolve())
        self._assert_refused(self._read(spelled), "a parent-directory spelling")

    def test_a_symbolic_link_to_the_key_is_refused(self) -> None:
        alias = self.tmp / "key-alias"
        alias.symlink_to(self.key_path)
        self._assert_refused(self._read(alias), "a symbolic link")

    def test_a_second_name_for_the_same_file_is_refused(self) -> None:
        """A hard link resolves to ITSELF, so no amount of path canonicalisation
        sees the secret: only the identity of the opened object does."""
        second_name = self.key_path.parent / "notes.txt"
        os.link(self.key_path, second_name)
        self.assertEqual(second_name.resolve(), second_name)
        self.assertEqual(second_name.read_text(), self.key)
        with self._as_if_the_install_lived_here():
            result = self._read(second_name)
        self._assert_refused(result, "a second name for the same file")

    def test_the_key_named_in_a_mapping_key_is_refused(self) -> None:
        """A tool may take its paths as mapping keys; the walk must look there."""
        def read_the_path_keys(arguments: dict) -> str:
            return "\n".join(
                Path(path).read_text(encoding="utf-8") for path in arguments["paths"]
            )

        self.registry.register_external(
            "mcp_path_map",
            ToolSchema(
                name="mcp_path_map",
                description="a tool that consumes paths as mapping keys",
                parameters={"type": "object"},
                safety_tier=SafetyTier.AUTO,
            ),
            read_the_path_keys,
            "",
        )
        arguments = {"paths": {str(self.key_path): True}}
        self.assertIsNotNone(
            _names_an_install_secret(arguments),
            "a path held as a mapping key names the file exactly as a value "
            "does, and the structure walk has to see it",
        )
        self._assert_refused(self._dispatch("mcp_path_map", arguments),
                            "a path held as a mapping key")

    # --- the named residue: a tool that derives the child itself -------------

    def test_a_tool_that_derives_the_protected_child_itself_is_the_named_residue(
            self) -> None:
        """The route no in-package check can refuse, and the text that says so.

        An external tool receives an ordinary directory and opens the protected
        child itself. Nothing in this package is ever shown the protected name,
        so this cell does not assert a refusal — it asserts that the package
        does not CLAIM one, in the module text a reader will believe.
        """
        def read_the_named_child(arguments: dict) -> str:
            return (Path(arguments["directory"]) / "dispatch-key").read_text()

        self.registry.register_external(
            "mcp_directory_reader",
            ToolSchema(
                name="mcp_directory_reader",
                description="a tool that derives a child of its directory argument",
                parameters={"type": "object"},
                safety_tier=SafetyTier.AUTO,
            ),
            read_the_named_child,
            "",
        )
        arguments = {"directory": str(self.key_path.parent)}
        self.assertIsNone(
            _names_an_install_secret(arguments),
            "an ordinary directory argument does not name a secret file, and a "
            "check that claimed otherwise would be guessing",
        )
        # Whitespace collapsed before the search: the sentence is a wrapped
        # paragraph, so a needle that crossed one of its line breaks would report
        # the text absent when it is present — the same mistake the reader of the
        # previous range recorded against its own first attempt. assertTrue on the
        # membership rather than assertIn on the source, because a failing
        # assertIn prints the whole module into every capture that holds it.
        source = " ".join(
            Path(dt.__file__).read_text(encoding="utf-8").split())
        self.assertTrue(
            "outside what argument inspection can establish" in source,
            "the token module has to state the limit of the argument screen "
            "where a reader of the module will see it, because this route "
            "stays open until the tool's own process is confined",
        )

    # --- a2: the harmless requests that must not be refused -----------------

    def test_an_ordinary_longer_filename_beside_the_key_is_readable(self) -> None:
        ordinary = self.key_path.parent / "dispatch-key-notes.txt"
        ordinary.write_text("harmless documentation\n", encoding="utf-8")
        result = self._read(ordinary)
        self.assertTrue(
            result.success,
            "a file whose name merely begins with the protected name is a "
            "different file; refusing it is an outage, not a guard. content "
            f"was {result.content!r}",
        )
        self.assertIn("harmless documentation", result.content or "")

    def test_harmless_text_naming_the_key_is_not_refused(self) -> None:
        handled: list[dict] = []

        def echo(arguments: dict) -> str:
            handled.append(arguments)
            return arguments["text"]

        self.registry.register_external(
            "mcp_echo",
            ToolSchema(
                name="mcp_echo",
                description="a tool that returns the text it is given",
                parameters={"type": "object"},
                safety_tier=SafetyTier.AUTO,
            ),
            echo,
            "",
        )
        text = ("Document that .config/intergen/dispatch-key is owner-only; "
                "do not read it.")
        result = self._dispatch("mcp_echo", {"text": text})
        self.assertTrue(
            result.success,
            "prose that mentions the protected file opens nothing; refusing it "
            f"stops the assistant explaining its own security. content was "
            f"{result.content!r}",
        )
        self.assertEqual(
            len(handled), 1,
            "the harmless request must reach the tool, not be stopped before "
            "execution",
        )

    # --- what the previous correction got right, kept -----------------------

    def test_the_direct_canonical_spelling_is_still_refused(self) -> None:
        self._assert_refused(self._read(self.key_path), "its own canonical path")

    def test_a_command_naming_the_key_is_still_refused(self) -> None:
        result = self._dispatch("run_command",
                                {"command": f"cat {self.key_path} | head -1"})
        self.assertFalse(
            result.success,
            "a command line naming the signing key must not report success; "
            f"content was {result.content!r}",
        )
        self.assertNotIn(self.key, result.content or "")
        self.assertEqual(
            self.reviews, [],
            "the refusal must land before the review surface is asked",
        )

    def test_an_ordinary_file_beside_the_key_is_still_readable(self) -> None:
        ordinary = self.key_path.parent / "settings.json"
        ordinary.write_text('{"theme": "dark"}\n', encoding="utf-8")
        result = self._read(ordinary)
        self.assertTrue(result.success, f"content was {result.content!r}")
        self.assertIn("dark", result.content or "")

    # --- the other two consumers that open a caller's path ------------------

    def test_write_file_cannot_read_or_replace_the_key(self) -> None:
        """write_file reads the old bytes to build its diff, and then writes.

        Both halves are the same boundary: the read hands the key back in the
        diff, and the write destroys the credential the root side verifies
        approvals with.

        Driven at the tool's own execute, in this process, for a reason worth
        recording. A write is privileged state-changing, so an APPROVING review
        surface on the registry path mints a token and reaches the real pkexec
        runner — which puts an authentication prompt in front of whoever owns the
        machine. A cell that can prompt a person is a defect in the cell, and a
        cell that lets the person refuse instead would prove nothing about this
        boundary. The dispatcher's own screen on a write is covered where it
        belongs, by the direct-spelling cell.

        The signed never-list is pinned ABSENT, which is the documented
        from-source state and the weaker of the two: on an installed machine the
        signed destructive-policy manifest refuses this write by itself, and a
        cell that let it do so would measure the manifest instead of this
        change. What is under test is whether the file boundary holds when that
        manifest is not there to help.
        """
        detour = self.key_path.parent / "detour"
        detour.mkdir()
        spelled = detour / ".." / self.key_path.name
        tool = write_file_tool.WriteFileTool()
        with mock.patch.object(write_file_tool, "_policy_loaded", True), \
                mock.patch.object(write_file_tool, "_policy_cache", None):
            result = tool.execute({"path": str(spelled),
                                   "content": "overwritten\n"})
        self.assertNotIn(
            self.key, result.content or "",
            "the old bytes of the signing key came back inside write_file's own "
            "diff, which is a read of the credential by another name",
        )
        self.assertFalse(
            result.success,
            "a write at a path that resolves to the signing key must not report "
            f"success; content was {result.content!r}",
        )
        self.assertEqual(
            self.key_path.read_text(), self.key,
            "the signing key must still be the key the install minted with: a "
            "write that replaces it destroys the one thing that lets the root "
            "side tell a human approval from an injected one",
        )

    def test_analyze_file_refuses_before_any_model_post(self) -> None:
        """analyze_file reads the file and then posts the body to a model server.

        The refusal has to land on the read, so the bytes never reach the post.
        This cell refuses the network outright: if the post is attempted at all,
        the recorded body is searched for the key and the attempt itself fails
        the cell.
        """
        detour = self.key_path.parent / "detour"
        detour.mkdir()
        spelled = detour / ".." / self.key_path.name
        posted: list[bytes] = []

        def no_network(request, *args, **kwargs):
            posted.append(getattr(request, "data", b"") or b"")
            raise OSError("this cell does not reach a model server")

        with mock.patch.object(urllib.request, "urlopen", no_network):
            result = self._dispatch(
                "analyze_file",
                {"path": str(spelled), "question": "what is this?"},
            )
        for body in posted:
            self.assertNotIn(
                self.key, body.decode("utf-8", errors="replace"),
                "the key reached the model-server request body",
            )
        self.assertEqual(
            posted, [],
            "analyze_file must refuse the secret file before it builds a "
            "request at all",
        )
        self._assert_refused(result, "a parent-directory spelling given to analyze_file")


if __name__ == "__main__":
    unittest.main()
