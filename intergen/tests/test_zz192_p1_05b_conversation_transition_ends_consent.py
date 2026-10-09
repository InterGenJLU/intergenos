# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Item 192, P1-05 — a conversation that ends takes its consent with it.

An earlier change made the /new slash command end the conversation through the
same door the new-session frame uses, so the model buffer, the conversation's
consent decisions, the ingress watermark and the staged offers all stop at the
boundary. The boundary still let two things through: the cards on screen and the
work detached from the turn live on the CONNECTION, not on the conversation
object, so neither was touched. A review card raised in the
conversation that just ended stayed open across the transition, and an approval
typed afterwards still resolved it — the person authorised an action belonging to
a conversation they had already left, and the system ran it in the new one.

These cases drive the real handlers on a real connection context: the real card
renderer registers the card, the real transition handler ends the conversation,
and the real decision handler is then given the old card's id. Both doors are
covered, because a switch leaves a conversation exactly as a new one does.

WHAT THESE CASES DO NOT DO. They do not stand up a socket or a browser: the
websocket is a recorder, which is how every other case on this surface drives the
server's frames. The frames they assert are the ones the two shipped clients act
on (web/app.js handleGateResolved and the console shell), and the wire itself is
an installed-system test on a real session.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from intergen.interfaces.provenance import Provenance, ToolRiskTier
from intergen.interfaces.types import ToolCall
from intergen.tool_registry import ToolRegistry
from intergen.tools.read_file import ReadFileTool
from intergen.web_server import ConnectionContext, WebServer


class _RecordingWS:
    """The frame recorder every case on this surface uses in place of a socket."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


def _server() -> WebServer:
    server = WebServer(router=mock.MagicMock(), llm=None, tools=None,
                       governance=None)
    server._sessions = mock.MagicMock()
    server._send_session_list = mock.AsyncMock()
    return server


def _ctx(ws: _RecordingWS) -> ConnectionContext:
    return ConnectionContext(client_id="case", source_interface="web", ws=ws)


def _call(call_id: str = "old-conversation-card") -> ToolCall:
    return ToolCall(
        name="run_command",
        arguments={"command": "true"},
        call_id=call_id,
        source_of_request=Provenance.USER_DIRECT,
    )


async def _card_on_screen(server: WebServer, ctx: ConnectionContext,
                          call: ToolCall) -> asyncio.Task:
    """Raise one real review card and return once its waiter is registered."""
    pending = asyncio.create_task(server._render_review_card(
        ctx, "old-turn", call, ToolRiskTier.PRIVILEGED_STATE_CHANGING))
    for _ in range(50):
        if call.call_id in ctx.open_gates:
            break
        await asyncio.sleep(0)
    assert call.call_id in ctx.open_gates, "the card never registered its waiter"
    assert not pending.done(), "the card resolved before anyone answered it"
    return pending


def _frames(ws: _RecordingWS) -> list[str]:
    return [item.get("type") for item in ws.sent]


class AConversationThatEndsTakesItsConsentWithItTests(unittest.TestCase):
    """The boundary, driven on both doors."""

    def test_a_new_session_denies_the_card_the_old_conversation_left_open(self):
        async def go():
            server, ws = _server(), _RecordingWS()
            ctx = _ctx(ws)
            call = _call()
            pending = await _card_on_screen(server, ctx, call)
            old_session = ctx.current_session_id

            await server._handle_slash_command(ctx, {"command": "/new"})

            self.assertNotEqual(ctx.current_session_id, old_session,
                                "the conversation did not actually change")
            self.assertNotIn(
                call.call_id, ctx.open_gates,
                "the ended conversation's card is still open for a decision")
            verdict = await asyncio.wait_for(pending, timeout=5)
            self.assertEqual(
                verdict, "denied",
                "the card from the ended conversation was not denied")

        asyncio.run(go())

    def test_a_decision_arriving_after_the_new_session_authorises_nothing(self):
        async def go():
            server, ws = _server(), _RecordingWS()
            ctx = _ctx(ws)
            call = _call()
            pending = await _card_on_screen(server, ctx, call)

            await server._handle_slash_command(ctx, {"command": "/new"})
            verdict = await asyncio.wait_for(pending, timeout=5)
            ws.sent.clear()
            await server._handle_gate_decision(ctx, {
                "tool_call_id": call.call_id,
                "decision": "allow",
            })

            self.assertEqual(verdict, "denied")
            self.assertEqual(
                [(f.get("type"), f.get("code")) for f in ws.sent],
                [("error", "no_open_gate")],
                "an approval typed after the transition was not refused as "
                "having no open card")

        asyncio.run(go())

    def test_a_switch_denies_the_card_the_left_conversation_held(self):
        async def go():
            server, ws = _server(), _RecordingWS()
            server._sessions.load.return_value = {"messages": []}
            ctx = _ctx(ws)
            call = _call("left-behind-card")
            pending = await _card_on_screen(server, ctx, call)

            await server._handle_switch_session(
                ctx, {"session_id": "session_abcdef12"})

            self.assertEqual(ctx.current_session_id, "session_abcdef12")
            self.assertNotIn(
                call.call_id, ctx.open_gates,
                "the card of the conversation just left is still open")
            verdict = await asyncio.wait_for(pending, timeout=5)
            self.assertEqual(verdict, "denied")

        asyncio.run(go())

    def test_the_person_is_told_the_card_was_denied_by_the_transition(self):
        async def go():
            server, ws = _server(), _RecordingWS()
            ctx = _ctx(ws)
            call = _call()
            pending = await _card_on_screen(server, ctx, call)

            await server._handle_slash_command(ctx, {"command": "/new"})
            await asyncio.wait_for(pending, timeout=5)

            resolved = [f for f in ws.sent
                        if f.get("type") == "gate_resolved"
                        and f.get("tool_call_id") == call.call_id]
            self.assertEqual(
                len(resolved), 1,
                "the card's own resolution frame is sent exactly once")
            self.assertEqual(resolved[0].get("decision"), "deny")
            self.assertTrue(
                resolved[0].get("reason"),
                "a card denied by the transition says so, so the person does "
                "not read it as their own refusal")
            self.assertIn("gate_resolved", _frames(ws))

        asyncio.run(go())

    def test_work_detached_from_the_ended_conversation_is_cancelled(self):
        async def go():
            server, ws = _server(), _RecordingWS()
            ctx = _ctx(ws)

            async def never() -> None:
                await asyncio.sleep(3600)

            detached = asyncio.create_task(never())
            ctx.tool_tasks.add(detached)
            turn = asyncio.create_task(never())
            ctx.turn_task = turn

            await server._handle_slash_command(ctx, {"command": "/new"})
            for _ in range(50):
                if detached.cancelled() and turn.cancelled():
                    break
                await asyncio.sleep(0)

            self.assertTrue(detached.cancelled(),
                            "a tool detached by the ended conversation is still "
                            "running in the new one")
            self.assertTrue(turn.cancelled(),
                            "the ended conversation's turn is still running")

        asyncio.run(go())

    def test_a_transition_with_nothing_open_sends_no_resolution_frame(self):
        """The control: the boundary must not invent a card that never existed."""
        async def go():
            server, ws = _server(), _RecordingWS()
            ctx = _ctx(ws)

            await server._handle_slash_command(ctx, {"command": "/new"})

            self.assertNotIn(
                "gate_resolved", _frames(ws),
                "a transition with no card open still told the clients to "
                "clear one")
            self.assertIn("session_created", _frames(ws))

        asyncio.run(go())


class ACardRaisedByAUserInvokedToolTests(unittest.TestCase):
    """/file raises its card inside the tool's own detached task.

    The cases above raise their card as a task of its own, the way the model's
    cards run. A card raised by a user-invoked tool — /file, here — runs inside
    the one task the slash command detached for the tool, and the boundary
    cancels that very task. A cancelled waiter never reaches its own send, so
    the boundary itself must send the card's resolution frame, with its reason,
    before it cancels anything.
    """

    def test_the_boundary_resolves_a_file_card_with_its_reason(self):
        async def go():
            registry = ToolRegistry()
            registry.register(ReadFileTool())
            server, ws = _server(), _RecordingWS()
            server._tools = registry
            ctx = _ctx(ws)
            with mock.patch.object(registry, "execute",
                                   wraps=registry.execute) as execute:
                await server._handle_slash_command(
                    ctx, {"command": "/file", "path": "/etc/hostname"})
                for _ in range(100):
                    if ctx.open_gates:
                        break
                    await asyncio.sleep(0)
                self.assertEqual(len(ctx.open_gates), 1,
                                 "the /file card never registered its waiter")
                card_id = next(iter(ctx.open_gates))
                detached = list(ctx.tool_tasks)

                await server._handle_slash_command(ctx, {"command": "/new"})
                await asyncio.gather(*detached, return_exceptions=True)
            return ws, ctx, card_id, execute

        ws, ctx, card_id, execute = asyncio.run(go())
        resolved = [f for f in ws.sent if f.get("type") == "gate_resolved"
                    and f.get("tool_call_id") == card_id]
        self.assertEqual(
            len(resolved), 1,
            f"the /file card denied by the boundary was sent {len(resolved)} "
            f"resolution frames; frames: {_frames(ws)}")
        self.assertEqual(resolved[0].get("decision"), "deny")
        self.assertTrue(
            resolved[0].get("reason"),
            "the card's resolution does not say the boundary denied it")
        frames = _frames(ws)
        self.assertLess(
            frames.index("gate_resolved"), frames.index("session_created"),
            "the card was resolved after the new conversation began")
        self.assertEqual(ctx.gate_end_reasons, {},
                         "the boundary's reason was left behind on the "
                         "connection")
        execute.assert_not_called()



class _BlockingRouter:
    """A router whose route() has the real contract — it writes its exchange
    into the conversation it was given before it returns — and which holds
    every message beginning "old" until the case releases it, so a turn's
    worker can be kept running across a boundary. It counts the route() calls
    running at once."""

    def __init__(self) -> None:
        import threading
        self._lock = threading.Lock()
        self.release = threading.Event()
        self.started = threading.Event()
        self.finished = threading.Event()
        self.active = 0
        self.max_active = 0
        self.routed: list[str] = []

    def route(self, user_msg, *, conversation, **_kwargs):
        from intergen.interfaces.types import Message, MessageRole, RouteResult
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.routed.append(user_msg)
        self.started.set()
        try:
            if user_msg.startswith("old"):
                assert self.release.wait(10), "the case did not release the route"
            answer = f"answer to {user_msg}"
            conversation.history.extend([
                Message(role=MessageRole.USER, content=user_msg),
                Message(role=MessageRole.ASSISTANT, content=answer),
            ])
            conversation.transcript.append((user_msg, answer))
            return RouteResult(text=answer, source="keyword", handled=True)
        finally:
            with self._lock:
                self.active -= 1
            if user_msg.startswith("old"):
                self.finished.set()

    def reset_conversation_state(self, state):
        state.clear()

    def _append_history(self, *_args, **_kwargs):
        return None  # route() above already wrote, as the fast paths do

    def last_route_confidence(self):
        return None


async def _thread_event(event, timeout: float = 5.0) -> bool:
    return await asyncio.to_thread(event.wait, timeout)


class _TurnWorkerCase(unittest.TestCase):
    """A server on the blocking router, one connection, and the turn helpers."""

    def setUp(self) -> None:
        from intergen import web_server as web_server_module
        # create=True: the constant is new with the fix, and at the base the
        # cases must run the base's own behaviour rather than fail to patch.
        patcher = mock.patch.object(web_server_module, "_ROUTE_SETTLE_S", 0.2,
                                    create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.router = _BlockingRouter()
        self.server = WebServer(router=self.router, llm=None, tools=None,
                                governance=None)
        self.server._sessions = mock.MagicMock()
        self.server._send_session_list = mock.AsyncMock()
        self.ctx = _ctx(_RecordingWS())

    def _history(self):
        return [(m.role.value, m.content) for m in self.ctx.conversation.history]

    async def _start(self, content: str) -> asyncio.Task:
        task = asyncio.create_task(
            self.server._run_turn(self.ctx, {"content": content}))
        self.ctx.turn_task = task
        return task


class AnEndedTurnsRouteWorkerStaysInTheConversationItBelongedTo(
        _TurnWorkerCase):
    """A turn's route() runs in a worker thread. Ending the conversation
    cancelled the turn's task, but not the thread: it finished afterwards and
    wrote the ended exchange into the conversation that had just been emptied
    for the next one, and, because cancelling the task released the route lock,
    the next turn's route ran beside it on the shared router.

    The boundary now gives the worker a short while to finish (a worker that
    does is emptied with the rest of the conversation); one still running keeps
    the conversation object it was given and the connection takes a new one;
    and the route lock is held until the worker itself finishes. The first case
    is the control: a turn that finished before the boundary was always ended
    with it."""

    def test_control_a_turn_finished_before_the_boundary_is_ended_with_it(self):
        async def scenario():
            self.router.release.set()
            await asyncio.wait_for(await self._start("old control"), 5)
            self.assertTrue(self._history())
            await self.server._handle_new_session(self.ctx, {})
        asyncio.run(scenario())
        self.assertEqual(self._history(), [])
        self.assertEqual(self.ctx.conversation.transcript, [])

    def test_a_cancelled_turns_worker_does_not_write_into_the_new_conversation(
            self):
        async def scenario():
            turn = await self._start("old question")
            self.assertTrue(await _thread_event(self.router.started))
            await self.server._handle_new_session(self.ctx, {})
            await asyncio.sleep(0)
            self.assertTrue(turn.cancelled())
            self.router.release.set()
            self.assertTrue(await _thread_event(self.router.finished))
            await asyncio.sleep(0.05)
        asyncio.run(scenario())
        self.assertEqual(self._history(), [],
                         "the ended turn wrote into the new conversation")
        self.assertEqual(self.ctx.conversation.transcript, [])

    def test_a_worker_that_finishes_within_the_wait_is_ended_with_the_rest(self):
        import threading
        saved: list = []
        # Copied when save() is called: the list it is handed is the
        # conversation's own, which the reset empties right after.
        self.server._sessions.save.side_effect = (
            lambda _sid, history, **_kw: saved.append(list(history)))

        async def scenario():
            await self._start("old question")
            self.assertTrue(await _thread_event(self.router.started))
            threading.Timer(0.05, self.router.release.set).start()
            await self.server._handle_new_session(self.ctx, {})
        asyncio.run(scenario())
        self.assertEqual(self._history(), [])
        self.assertEqual(len(saved), 1)
        self.assertEqual([m.content for m in saved[0]],
                         ["old question", "answer to old question"],
                         "the ended conversation was saved without the exchange "
                         "its worker finished")

    def test_a_switch_does_not_receive_the_ended_turns_late_write(self):
        from intergen.interfaces.types import Message, MessageRole
        loaded = [Message(role=MessageRole.USER, content="from the other session")]
        self.server._sessions.load.return_value = {"messages": loaded}

        async def scenario():
            await self._start("old question")
            self.assertTrue(await _thread_event(self.router.started))
            await self.server._handle_switch_session(
                self.ctx, {"session_id": "session_0123abcd"})
            self.router.release.set()
            self.assertTrue(await _thread_event(self.router.finished))
            await asyncio.sleep(0.05)
        asyncio.run(scenario())
        self.assertEqual(self._history(), [("user", "from the other session")])

    def test_a_new_turn_does_not_route_beside_the_cancelled_turns_worker(self):
        async def scenario():
            await self._start("old question")
            self.assertTrue(await _thread_event(self.router.started))
            await self.server._handle_new_session(self.ctx, {})
            new = await self._start("new question")
            await asyncio.sleep(0.3)
            self.assertEqual(self.router.routed, ["old question"],
                             "the new turn's route ran while the ended turn's "
                             "worker was still running")
            self.router.release.set()
            await asyncio.wait_for(new, 5)
        asyncio.run(scenario())
        self.assertEqual(self.router.max_active, 1)
        self.assertEqual(self._history(), [("user", "new question"),
                                           ("assistant", "answer to new question")])

    def test_a_route_abandoned_at_the_deadline_keeps_the_lock_until_it_finishes(
            self):
        from intergen import web_server as web_server_module

        async def scenario():
            with mock.patch.object(web_server_module,
                                   "SERVER_ROUTE_DEADLINE_S", 0.2):
                await asyncio.wait_for(await self._start("old slow"), 5)
            self.assertIn("route_timeout",
                          [f.get("code") for f in self.ctx.ws.sent])
            second = await self._start("new question")
            await asyncio.sleep(0.3)
            self.assertEqual(self.router.routed, ["old slow"],
                             "a route ran beside the abandoned worker")
            self.router.release.set()
            await asyncio.wait_for(second, 5)
        asyncio.run(scenario())
        self.assertEqual(self.router.max_active, 1)



class AClearEndsTheTurnInFlight(_TurnWorkerCase):
    """/clear emptied the buffer and said so while a turn was still running:
    the turn then wrote its exchange into the emptied buffer and sent its
    answer to the page, which put it into the emptied pane. /clear now ends the
    turn in flight first — its task, its cards, and a short wait for its route
    worker — and a worker still running keeps the buffer it was writing into."""

    def _clear(self):
        return self.server._handle_slash_command(self.ctx, {"command": "/clear"})

    def _frames_after_clear(self):
        types = [f.get("type") for f in self.ctx.ws.sent]
        self.assertIn("buffer_cleared", types)
        return types[types.index("buffer_cleared") + 1:]

    def test_the_buffer_stays_empty_when_the_worker_finishes_late(self):
        async def scenario():
            turn = await self._start("old question")
            self.assertTrue(await _thread_event(self.router.started))
            await self._clear()
            await asyncio.sleep(0)
            self.assertTrue(turn.cancelled(), "the turn in flight was not ended")
            self.router.release.set()
            self.assertTrue(await _thread_event(self.router.finished))
            await asyncio.sleep(0.05)
        asyncio.run(scenario())
        self.assertEqual(self._history(), [],
                         "the ended turn wrote into the cleared buffer")
        self.assertNotIn("response", self._frames_after_clear())

    def test_a_worker_finishing_within_the_wait_is_cleared_with_the_buffer(self):
        import threading

        async def scenario():
            await self._start("old question")
            self.assertTrue(await _thread_event(self.router.started))
            threading.Timer(0.05, self.router.release.set).start()
            await self._clear()
            await asyncio.sleep(0.05)
        asyncio.run(scenario())
        self.assertEqual(self._history(), [])
        self.assertNotIn("response", self._frames_after_clear())

    def test_clear_keeps_the_conversations_consent_and_transcript(self):
        """Control: /clear empties the buffer; it does not end the
        conversation."""
        async def scenario():
            self.router.release.set()
            await asyncio.wait_for(await self._start("old question"), 5)
            await self._clear()
        trust_state = self.ctx.conversation.trust_state
        asyncio.run(scenario())
        self.assertEqual(self._history(), [])
        self.assertIs(self.ctx.conversation.trust_state, trust_state)
        self.assertEqual(self.ctx.conversation.transcript,
                         [("old question", "answer to old question")])


if __name__ == "__main__":
    unittest.main()
