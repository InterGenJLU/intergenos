# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A turn served over the web surface also gives the wiki index its pass.

THE GAP THIS PINS. The bounded catch-up pass — InterGenDaemon
._resume_wiki_embedding_after_turn, which calls WikiRetrieval.resume_embedding()
— is called from exactly one place: the D-Bus turn path in
intergen/dbus_daemon.py. intergen/web_server.py runs its own turn
(WebServer._run_turn -> _handle_client_message) and calls nothing of the sort.

On a machine driven only through the web surface at 127.0.0.1:8089 the wiki
index therefore never finishes. Every condition that makes the catch-up
necessary is unchanged — a slow embedding server at boot leaves the corpus
part-embedded and `embeddings_ready` False — and the one mechanism that repairs
it is unreachable, so the wiki answers by keyword match for the life of the
daemon exactly as it did before the pass existed. That is the same defect, on a
different door.

WHY THE CALL AND NOT A COPY. The pass must stay ONE pass at a time: the
embedding server runs --parallel 1, and two callers each starting their own
pass would only wait out each other's timeouts. The daemon holds that guard
(_wiki_resume_running under _wiki_resume_lock). So the web turn must call the
DAEMON'S method, sharing that guard, rather than grow a second copy with a
second lock — which is the "lazily creating a lock under no lock gives you two"
failure the daemon's own comment warns about, reintroduced across modules.

WHAT IS ASSERTED. The turn path invokes the hook exactly once per turn, and it
does so on EVERY exit — a turn that answers, and a turn whose client vanished
mid-way. The second matters because a disconnect is not a reason to skip index
maintenance: the model server has just as surely finished work.

WHAT IS NOT ASSERTED HERE. Whether the pass then embeds anything is the
daemon-side behaviour that test_wiki_index_finishes_in_idle.py already pins
against the real WikiRetrieval. This fixture pins the WIRING — that the web door
reaches the hook at all — which is the part that did not exist.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from intergen.web_server import WebServer


class _Recorder:
    """Stands in for the daemon's bound _resume_wiki_embedding_after_turn."""

    def __init__(self, raises: BaseException | None = None) -> None:
        self.calls = 0
        self._raises = raises

    def __call__(self) -> None:
        self.calls += 1
        if self._raises is not None:
            raise self._raises


class _FakeWS:
    """The minimum of an aiohttp WebSocketResponse this turn path touches."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload) -> None:
        self.sent.append(payload)


class _Ctx:
    def __init__(self) -> None:
        self.ws = _FakeWS()
        self.client_id = "test-client"


class WebTurnResumesWikiIndexTests(unittest.IsolatedAsyncioTestCase):

    def _server(self, hook) -> WebServer:
        # router=None keeps _handle_client_message on its own early-return path;
        # this fixture is about what happens AROUND the turn body, not inside it.
        return WebServer(host="127.0.0.1", port=0, after_turn=hook)

    async def test_a_completed_web_turn_gives_the_index_one_pass(self):
        """RED BEFORE THE FIX: nothing in _run_turn reaches the hook."""
        hook = _Recorder()
        server = self._server(hook)
        ctx = _Ctx()

        with mock.patch.object(WebServer, "_handle_client_message",
                               new=mock.AsyncMock(return_value=None)):
            await server._run_turn(ctx, {"content": "what is a wiki page?"})

        self.assertEqual(
            hook.calls, 1,
            "a completed web turn did not give the wiki index its bounded "
            "catch-up pass — on a machine driven only through the web surface "
            "the index never finishes")

    async def test_the_pass_is_offered_once_per_turn_not_once_per_frame(self):
        """Two turns, two passes — never more. The guard is the daemon's."""
        hook = _Recorder()
        server = self._server(hook)
        ctx = _Ctx()

        with mock.patch.object(WebServer, "_handle_client_message",
                               new=mock.AsyncMock(return_value=None)):
            await server._run_turn(ctx, {"content": "first"})
            await server._run_turn(ctx, {"content": "second"})

        self.assertEqual(hook.calls, 2,
                         "the hook must fire exactly once per turn")

    async def test_a_turn_whose_client_vanished_still_gives_the_index_its_pass(self):
        """A disconnect is not a reason to skip maintenance.

        The model server has finished work either way, and the disconnected
        client is the case where the machine is MOST idle.
        """
        hook = _Recorder()
        server = self._server(hook)
        ctx = _Ctx()

        with mock.patch.object(
                WebServer, "_handle_client_message",
                new=mock.AsyncMock(side_effect=ConnectionResetError())):
            await server._run_turn(ctx, {"content": "goodbye"})

        self.assertEqual(
            hook.calls, 1,
            "a turn abandoned by its client skipped the index pass")

    async def test_a_crashed_turn_still_gives_the_index_its_pass(self):
        hook = _Recorder()
        server = self._server(hook)
        ctx = _Ctx()

        with mock.patch.object(WebServer, "_handle_client_message",
                               new=mock.AsyncMock(side_effect=RuntimeError("boom"))):
            await server._run_turn(ctx, {"content": "explode"})

        self.assertEqual(hook.calls, 1,
                         "a crashed turn skipped the index pass")

    async def test_a_failing_hook_never_reaches_the_user(self):
        """Index maintenance may not break the turn that triggered it.

        The daemon's own method is best-effort throughout; the call site must
        be too, or a maintenance bug becomes a user-visible turn failure.
        """
        hook = _Recorder(raises=RuntimeError("maintenance blew up"))
        server = self._server(hook)
        ctx = _Ctx()

        with mock.patch.object(WebServer, "_handle_client_message",
                               new=mock.AsyncMock(return_value=None)):
            await server._run_turn(ctx, {"content": "hello"})

        self.assertEqual(hook.calls, 1)
        self.assertNotIn(
            "internal_error",
            [f.get("code") for f in ctx.ws.sent if isinstance(f, dict)],
            "a failure inside index maintenance was reported to the user as a "
            "failed turn")

    async def test_no_hook_configured_is_not_an_error(self):
        """A web server built without the hook behaves exactly as it did.

        The daemon is the only caller that supplies it; every other
        construction — tests, tools, a future embedder-less mode — must be
        unaffected.
        """
        server = WebServer(host="127.0.0.1", port=0)
        ctx = _Ctx()

        with mock.patch.object(WebServer, "_handle_client_message",
                               new=mock.AsyncMock(return_value=None)):
            await server._run_turn(ctx, {"content": "hello"})

        self.assertTrue(
            any(f.get("type") == "turn_ack" for f in ctx.ws.sent),
            "the turn did not run normally without a hook configured")


class DaemonWiresTheHookTests(unittest.TestCase):
    """The daemon must actually hand its method over.

    A WebServer that accepts a hook nobody passes is the same defect in a new
    place: a recovery path with no caller. This reads the daemon's construction
    site rather than trusting that the parameter exists.
    """

    def test_the_daemon_passes_its_resume_method_to_the_web_server(self):
        """Read the CALL, not a window of source text.

        An earlier form of this test sliced the source between two string
        offsets and was defeated by a bracket inside a comment — it reported the
        wiring missing while the wiring was there. Parsing the module and
        reading the call's keyword arguments cannot be fooled that way, and it
        keeps working when the call is reformatted.
        """
        import ast
        import inspect
        from intergen import dbus_daemon

        tree = ast.parse(inspect.getsource(dbus_daemon))
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name)
                 and n.func.id == "WebServer"]
        self.assertEqual(
            len(calls), 1,
            f"expected exactly one WebServer(...) construction in the daemon, "
            f"found {len(calls)}; this test must be re-pointed at the right one")

        kwargs = {k.arg: k.value for k in calls[0].keywords if k.arg}
        self.assertIn(
            "after_turn", kwargs,
            "the daemon builds the web server without handing it the wiki "
            "catch-up hook, so the web turn path still cannot reach it")

        hook = kwargs["after_turn"]
        self.assertIsInstance(
            hook, ast.Attribute,
            "after_turn is not an attribute of the daemon — it must be the "
            "daemon's own bound pass so the single-pass guard is shared")
        self.assertEqual(
            hook.attr, "_resume_wiki_embedding_after_turn",
            f"the web server is given after_turn={hook.attr}, not the daemon's "
            f"own bounded pass — the single-pass guard is not shared")


if __name__ == "__main__":
    unittest.main()
