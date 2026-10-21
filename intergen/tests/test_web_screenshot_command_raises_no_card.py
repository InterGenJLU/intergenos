# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""/screenshot raises no review card: the platform's dialog is its consent.

/screenshot is a person pressing the screenshot button or typing the command.
The capture goes through the platform's screenshot portal in its interactive
form, so the compositor shows its own dialog: the person chooses what to
capture there and presses its button, and no other process can press it for
them. That is the one consent a screenshot asks for, so the command puts no
review card of the assistant's own in front of it.

What still applies to the command is everything else every tool call meets:
governance's two refusals no person can override, and the registry's dispatch
with its audit record. And the card is kept where the request is not the
person's own — a screenshot the model asks for still raises one.

These cases drive the real slash-command handler, the real registry, the real
tool and the real capture code. The portal is the private-bus double the tool's
own cases use (screenshot_portal_double.py), which records what each request
carried; no compositor, display or session bus of this machine is reached.
"""

from __future__ import annotations

import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from intergen import image_ingress
from intergen.image_ingress import ImageIngressGate
from intergen.interfaces.provenance import Provenance
from intergen.scanner.policy import ScannerPolicy
from intergen.interfaces.types import ToolCall
from intergen.tool_registry import ToolRegistry
import intergen.tools.take_screenshot as ts
from intergen.web_server import ConnectionContext, WebServer

try:
    from intergen.tests import screenshot_portal_double as portal

    _PORTAL_UNAVAILABLE = portal.unavailable_reason()
except Exception as exc:  # pragma: no cover - environment-dependent
    _PORTAL_UNAVAILABLE = f"the portal double cannot be imported: {exc}"

_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class _RecordingWS:
    """The frame recorder every case on this surface uses in place of a socket."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    def types(self) -> list[str]:
        return [frame.get("type") for frame in self.sent]

    def of_type(self, kind: str) -> list[dict]:
        return [frame for frame in self.sent if frame.get("type") == kind]


def _server(governance=None) -> WebServer:
    registry = ToolRegistry()
    registry.register(ts.TakeScreenshotTool())
    server = WebServer(router=mock.MagicMock(), llm=None, tools=registry,
                       governance=governance)
    server._sessions = mock.MagicMock()
    return server


def _hard_denying_governance():
    """A governance that refuses with a class no person can override."""
    governance = mock.MagicMock()
    governance.evaluate.return_value = SimpleNamespace(
        allowed=False,
        checks=[SimpleNamespace(gate_name="hash_integrity", passed=False)])
    return governance


async def _screenshot_command(server: WebServer, ws: _RecordingWS) -> None:
    ctx = ConnectionContext(client_id="case", source_interface="web", ws=ws)
    await server._handle_slash_command(ctx, {"command": "/screenshot"})
    for task in list(ctx.tool_tasks):
        await asyncio.wait_for(task, timeout=30)


@unittest.skipIf(_PORTAL_UNAVAILABLE, _PORTAL_UNAVAILABLE or "")
class TheScreenshotCommandConsentsInThePlatformDialogTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.bus = portal.PrivateBus()
        try:
            cls.portal = portal.PortalDouble(cls.bus)
            cls.portal.start()
            cls.client = cls.bus.connect()
        except BaseException:
            cls.bus.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.client.close_sync(None)
        cls.portal.stop()
        cls.bus.close()

    def setUp(self):
        # The image gate admits the case's blank 1x1 capture: the transcriber
        # is faked (no model here); the gate itself is test_image_ingress_gate.
        gate_patch = mock.patch.object(
            image_ingress, "GATE",
            ImageIngressGate(transcriber=lambda uri: "NO TEXT",
                             scanner=ScannerPolicy()))
        gate_patch.start()
        self.addCleanup(gate_patch.stop)
        tmp = tempfile.TemporaryDirectory(prefix="intergen-shot-case-")
        self.addCleanup(tmp.cleanup)
        self.shot = Path(tmp.name) / "Screenshot From 2026-10-08 12-00-00.png"
        self.shot.write_bytes(_PNG_1x1)
        patcher = mock.patch.object(ts, "_session_bus",
                                    return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.portal.reset(portal.Answer(code=0, uri=self.shot.as_uri()))

    def test_the_command_raises_no_card_and_asks_for_the_dialog(self):
        ws = _RecordingWS()
        asyncio.run(_screenshot_command(_server(), ws))

        self.assertEqual(
            ws.of_type("gate_prompt"), [],
            "/screenshot raised a review card in front of the platform's "
            "dialog, which is already the consent for the capture")
        self.assertEqual(len(self.portal.requests), 1,
                         "the command did not reach the screenshot portal")
        self.assertIs(
            self.portal.requests[0]["options"].get("interactive"), True,
            "the portal request must carry interactive=true: the dialog is "
            "the consent, and a non-interactive request is a silent grab")
        executed = ws.of_type("tool_executed")
        self.assertEqual(len(executed), 1, ws.types())
        self.assertTrue(executed[0]["success"], executed[0])
        self.assertIn(base64.b64encode(_PNG_1x1).decode("ascii"),
                      executed[0].get("full_output", ""))

    def test_a_refusal_no_person_can_override_still_stops_the_command(self):
        ws = _RecordingWS()
        asyncio.run(_screenshot_command(
            _server(governance=_hard_denying_governance()), ws))

        self.assertEqual(ws.of_type("gate_prompt"), [])
        self.assertEqual(self.portal.requests, [],
                         "a governance refusal still reached the portal")
        executed = ws.of_type("tool_executed")
        self.assertEqual(len(executed), 1, ws.types())
        self.assertFalse(executed[0]["success"])
        self.assertIn("was not run", executed[0]["summary"])

    def test_a_screenshot_the_model_asks_for_still_raises_a_card(self):
        """The control: only the person's own command goes without a card."""
        async def go():
            server, ws = _server(), _RecordingWS()
            ctx = ConnectionContext(client_id="case", source_interface="web",
                                    ws=ws)
            call = ToolCall(name="take_screenshot", arguments={},
                            call_id="model-asked",
                            source_of_request=Provenance.USER_IMPLIED)
            pending = asyncio.create_task(
                server._evaluate_tool_with_gate(ctx, "turn", call))
            for _ in range(50):
                if "model-asked" in ctx.open_gates:
                    break
                await asyncio.sleep(0)
            await server._handle_gate_decision(
                ctx, {"tool_call_id": "model-asked", "decision": "deny"})
            return await asyncio.wait_for(pending, timeout=5), ws

        verdict, ws = asyncio.run(go())
        self.assertEqual(
            [frame.get("tool_call_id") for frame in ws.of_type("gate_prompt")],
            ["model-asked"],
            "a screenshot the model asked for must still raise its card")
        self.assertEqual(verdict, "denied")
        self.assertEqual(self.portal.requests, [])


if __name__ == "__main__":
    unittest.main()
