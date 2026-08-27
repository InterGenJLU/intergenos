# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The Qwen attribution is rendered where a person actually converses.

Tongyi Qianwen License section 4 requires an attribution wherever a Qwen-family
model powers the assistant. `intergen --version` renders it. The places a
person actually reads did not: the web conversation view (which the GTK panel
displays through WebKit, so the two are one surface) and the terminal console
rendered nothing at all.

These cases pin the properties that matter across every surface:

* ONE source of truth. Every surface renders the SAME line from the SAME
  helper, so the surfaces cannot drift into three different sentences about
  the same license. The line's text is asserted once, and each surface is
  required to carry that exact string rather than a lookalike.
* The attribution names the model actually present, and NOTHING is rendered
  when no Qwen-family model is on the machine. A Tier-1 box serves
  InternVL3.5-2B; "Powered by Qwen" there is a false statement about what is
  running, so each surface's absence case is pinned as hard as its presence
  case.
* The web view renders it whether or not the request carried an auth token.
  The index handler had a fast path that returned the file unmodified for a
  token-less request, so an attribution injected only on the token path would
  be present or absent depending on how the page was opened — which is not a
  property a license obligation may have.
* A failure to determine the model renders no attribution rather than a guess.
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen import attribution
from intergen.hardware import HardwareTierLevel
from intergen.model_manager import ModelInfo


def _model(name: str, tier: HardwareTierLevel) -> ModelInfo:
    return ModelInfo(
        name=name, filename=f"{name}-Q4_K_M.gguf", repo_id=f"test/{name}",
        quant="Q4_K_M", size_gb=1.0, sha256="0" * 64, tier=tier,
        local_path=f"/nonexistent/{name}-Q4_K_M.gguf", downloaded=True,
    )


QWEN_9B = _model("Qwen3.5-9B", HardwareTierLevel.TIER_2)
INTERNVL_2B = _model("InternVL3.5-2B", HardwareTierLevel.TIER_1)

# The one sentence every surface must render, spelled out here so a surface
# that invents its own wording fails rather than passing on a substring.
EXPECTED_LINE = ("Powered by Qwen — Qwen3.5-9B, used under the "
                 "Tongyi Qianwen License.")


def _with_models(models):
    from intergen.model_manager import ModelManager
    return mock.patch.object(ModelManager, "list_downloaded",
                             return_value=list(models))


class TheSharedLineTests(unittest.TestCase):
    """intergen/attribution.py — the single source every surface reads."""

    def test_the_line_names_the_model_and_the_license(self):
        with _with_models([QWEN_9B]):
            self.assertEqual(attribution.attribution_line(), EXPECTED_LINE)

    def test_no_line_when_no_qwen_model_is_present(self):
        with _with_models([INTERNVL_2B]):
            self.assertIsNone(attribution.attribution_line())

    def test_no_line_on_an_empty_machine(self):
        with _with_models([]):
            self.assertIsNone(attribution.attribution_line())

    def test_a_failing_lookup_renders_nothing_rather_than_a_guess(self):
        from intergen.model_manager import ModelManager

        def _boom(*_a, **_k):
            raise OSError("manifest unreadable")

        with mock.patch.object(ModelManager, "list_downloaded", _boom):
            self.assertIsNone(attribution.attribution_line())

    def test_the_projector_entry_does_not_become_a_second_name(self):
        """The paired projector rides the manifest as "<model> (mmproj)". It is
        the same model for attribution purposes."""
        proj = _model("Qwen3.5-9B (mmproj)", HardwareTierLevel.TIER_2)
        with _with_models([QWEN_9B, proj]):
            self.assertEqual(attribution.attribution_line(), EXPECTED_LINE)

    def test_the_cli_renders_the_shared_line_and_not_its_own(self):
        """`intergen --version` must read the same helper, so the CLI and the
        views can never disagree about the license sentence."""
        import io
        from contextlib import redirect_stdout
        from intergen import cli

        buf = io.StringIO()
        with _with_models([QWEN_9B]), redirect_stdout(buf):
            cli.cmd_version()
        self.assertIn(EXPECTED_LINE, buf.getvalue())


class TheWebConversationViewTests(unittest.TestCase):
    """The view the GTK panel shows through WebKit and a browser shows directly."""

    def _serve_index(self, models, with_token: bool):
        """Render index.html through the server's own index handler."""
        import asyncio
        from intergen import web_server

        srv = web_server.WebServer.__new__(web_server.WebServer)

        class _Req:
            headers: dict = {}
            query: dict = {}
            cookies: dict = {}

        req = _Req()
        with _with_models(models), \
             mock.patch.object(web_server.WebServer, "_check_ready",
                               return_value=True), \
             mock.patch.object(web_server.WebServer,
                               "_extract_auth_token",
                               return_value="tok" if with_token else None):
            resp = asyncio.run(srv._handle_index(req))
        body = getattr(resp, "text", None)
        if body is None:  # a FileResponse never carries the injected text
            from pathlib import Path
            body = Path(web_server.STATIC_DIR / "index.html").read_text()
            body += "\n<!-- SERVED AS AN UNMODIFIED FILE RESPONSE -->"
        return body

    def test_the_view_renders_the_line_with_a_token(self):
        body = self._serve_index([QWEN_9B], with_token=True)
        self.assertIn(EXPECTED_LINE, body)

    def test_the_view_renders_the_line_without_a_token(self):
        """The token-less path used to return the file unmodified. A license
        attribution must not depend on how the page was opened."""
        body = self._serve_index([QWEN_9B], with_token=False)
        self.assertNotIn("SERVED AS AN UNMODIFIED FILE RESPONSE", body,
                         "the token-less path still bypasses injection, so the "
                         "attribution cannot reach the page")
        self.assertIn(EXPECTED_LINE, body)

    def test_the_view_renders_nothing_when_no_qwen_model_is_present(self):
        body = self._serve_index([INTERNVL_2B], with_token=True)
        self.assertNotIn("Powered by Qwen", body)
        self.assertNotIn("Tongyi", body)

    def test_the_shipped_page_carries_the_placeholder_the_server_fills(self):
        """The element has to exist in the shipped file, or the injection has
        nowhere to land and the test above could pass on a stray string."""
        from pathlib import Path
        from intergen import web_server
        html = Path(web_server.STATIC_DIR / "index.html").read_text()
        self.assertIn(attribution.HTML_PLACEHOLDER, html)


class TheTerminalConsoleTests(unittest.TestCase):
    """`intergen console` — its own renderer, never the web view."""

    def test_the_startup_messages_carry_the_line(self):
        from intergen.console import shell
        with _with_models([QWEN_9B]):
            msgs = shell.startup_messages()
        self.assertTrue(any(EXPECTED_LINE in m.get("content", "")
                            for m in msgs),
                        f"no startup message carried the attribution: {msgs!r}")

    def test_no_startup_message_when_no_qwen_model_is_present(self):
        from intergen.console import shell
        with _with_models([INTERNVL_2B]):
            msgs = shell.startup_messages()
        self.assertFalse(any("Powered by Qwen" in m.get("content", "")
                             for m in msgs))

    def test_the_shell_seeds_its_view_with_those_messages(self):
        """Proves the wiring, not just the helper: a function nobody calls
        renders nothing."""
        import io
        from contextlib import redirect_stderr
        from intergen.console import shell
        # prompt_toolkit prints "Input is not a terminal" to stderr when built
        # under pytest. It is captured here so it cannot pollute a suite
        # capture, and it says nothing about this behaviour either way.
        with _with_models([QWEN_9B]), redirect_stderr(io.StringIO()):
            sh = shell.ConsoleShell()
        self.assertTrue(any(EXPECTED_LINE in m.get("content", "")
                            for m in sh._messages),
                        "the console built its view without the attribution")


if __name__ == "__main__":
    unittest.main()
