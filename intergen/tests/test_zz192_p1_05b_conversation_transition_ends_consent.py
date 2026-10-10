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

import pytest

from intergen.interfaces.cloud import CloudProviderAdapter
from intergen.interfaces.provenance import Provenance, ToolRiskTier
from intergen.interfaces.types import LLMResponse, ToolCall
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


class _RecordingIndex:
    """A stand-in relevance index that records being emptied."""

    def __init__(self) -> None:
        self.cleared = 0

    def clear(self) -> None:
        self.cleared += 1


class AClearEndsTheTurnInFlight(_TurnWorkerCase):
    """/clear emptied the buffer and said so while a turn was still running:
    the turn then wrote its exchange into the emptied buffer and sent its
    answer to the page, which put it into the emptied pane. /clear now ends the
    turn in flight first — its task, its cards, and a short wait for its route
    worker — and a worker still running keeps the buffer it was writing into.
    It also empties the relevance index, which brought cleared exchanges back
    into the prompt by retrieval."""

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

    def test_clear_empties_the_relevance_index(self):
        index = _RecordingIndex()
        self.ctx.conversation.turn_index = index
        asyncio.run(self._clear())
        self.assertEqual(index.cleared, 1,
                         "the cleared exchanges stayed retrievable into the prompt")


class _PacedModel:
    """A model client whose stream has the real one's shape (llm.py
    stream_with_tools): it opens a reply, yields one item each time the case
    allows one, and closes the reply in its own cleanup, as the real stream
    closes its HTTP response. The case reads, on the model's side, how many
    items were produced after the turn ended and whether the reply closed."""

    _endpoint = "http://127.0.0.1:8080/v1/chat/completions"

    def __init__(self, items: int) -> None:
        import threading
        self.items = items
        self.allow = threading.Semaphore(0)
        self.finish = threading.Event()
        self.reply_closed = threading.Event()
        self.produced = 0

    def build_system_messages(self, query_type="general", with_tools=True):
        from intergen.interfaces.types import Message, MessageRole
        return [Message(role=MessageRole.SYSTEM, content="system prompt")]

    def stream_with_tools(self, messages, tools=None, image_data=None,
                          max_tokens=None):
        try:
            for index in range(self.items):
                while not self.allow.acquire(timeout=0.05):
                    if self.finish.is_set():
                        return
                self.produced += 1
                yield f"word{index} "
        finally:
            self.reply_closed.set()


def _model_router(llm):
    """A conversation router with no daemon behind it, as the streamed-turn
    cases beside this module build one."""
    from intergen.router import ConversationRouter
    router = ConversationRouter.__new__(ConversationRouter)
    router._max_history = 20
    router._record = lambda *a, **k: None
    router._current_query_type = "general"
    router._memory = None
    router._embedder = None
    router._llm = llm
    router._last_semantic_score = None
    router.detach_conversation()
    return router


class _GoneAfterFirstToken(_RecordingWS):
    """A page that goes away after the first token it is sent."""

    async def send_json(self, payload: dict) -> None:
        if payload.get("type") == "stream_token" and "stream_token" in [
                item.get("type") for item in self.sent]:
            raise ConnectionResetError("the page went away")
        self.sent.append(payload)


class AnEndedTurnsModelStreamIsClosedWithTheTurn(unittest.TestCase):
    """The streamed answer is read from the model in a thread (_run_llm).
    Ending the turn cancelled its task but not the thread, which read the
    model's stream to its end: after a /clear the chat server generated the
    ended answer for 25.5 s more and, with its one slot, the next turn's request
    waited behind it. The thread now checks at every item whether its turn has
    ended and then closes the stream, whose cleanup closes the reply. Each case
    lets the model offer five more items after the end and counts, on the
    model's side, how many it produced. The first case is the control: a turn
    that is not ended reads its stream to the end."""

    ITEMS = 40

    def setUp(self) -> None:
        self.llm = _PacedModel(self.ITEMS)
        # The case's own end: a stream still running is let finish, so no
        # thread outlives the case.
        self.addCleanup(self.llm.finish.set)
        self.router = _model_router(self.llm)
        self.server = WebServer(router=self.router, llm=self.llm, tools=None,
                                governance=None)
        self.server._sessions = mock.MagicMock()
        self.server._send_session_list = mock.AsyncMock()
        self.ctx = ConnectionContext(
            client_id="case", source_interface="web", ws=_RecordingWS(),
            conversation=self.router.new_conversation())

    def _stream(self) -> asyncio.Task:
        from intergen.interfaces.types import RouteResult
        task = asyncio.create_task(self.server._stream_llm_response(
            self.ctx, "old-turn", "tell me a long story",
            RouteResult(text="", source="llm_freeform", handled=False)))
        self.ctx.turn_task = task
        return task

    async def _first_token(self) -> None:
        self.llm.allow.release()
        for _ in range(500):
            if "stream_token" in _frames(self.ctx.ws):
                return
            await asyncio.sleep(0.01)
        self.fail("the stream never started")

    async def _produced_after_the_end(self, at_end: int) -> tuple[bool, int]:
        for _ in range(5):
            self.llm.allow.release()
        closed = await asyncio.to_thread(self.llm.reply_closed.wait, 2.0)
        return closed, self.llm.produced - at_end

    def _assert_closed_within_one_item(self, closed: bool, after: int) -> None:
        self.assertTrue(
            closed,
            f"the ended turn's stream was not closed: the model produced "
            f"{after} more item(s) after the turn ended and was still asked "
            f"for the next")
        self.assertLessEqual(
            after, 1, "the ended turn's stream was read past one item")

    def _ended_by(self, end) -> None:
        async def scenario():
            task = self._stream()
            await self._first_token()
            await end()
            await asyncio.gather(task, return_exceptions=True)
            self.assertTrue(task.cancelled(), "the turn was not ended")
            return await self._produced_after_the_end(self.llm.produced)
        self._assert_closed_within_one_item(*asyncio.run(scenario()))

    def test_control_a_turn_that_is_not_ended_reads_its_stream_to_the_end(self):
        for _ in range(self.ITEMS):
            self.llm.allow.release()

        async def scenario():
            await self._stream()
        asyncio.run(scenario())
        tokens = [f.get("token") for f in self.ctx.ws.sent
                  if f.get("type") == "stream_token"]
        self.assertEqual(self.llm.produced, self.ITEMS)
        self.assertEqual(tokens, [f"word{i} " for i in range(self.ITEMS)])
        self.assertTrue(self.llm.reply_closed.is_set())
        self.assertIn("stream_end", _frames(self.ctx.ws))

    def test_clear_closes_the_ended_turns_stream_within_one_item(self):
        self._ended_by(lambda: self.server._handle_slash_command(
            self.ctx, {"command": "/clear"}))

    def test_a_new_session_closes_the_ended_turns_stream_within_one_item(self):
        self._ended_by(lambda: self.server._handle_new_session(self.ctx, {}))

    def test_a_page_that_goes_away_closes_the_stream_within_one_item(self):
        self.ctx.ws = _GoneAfterFirstToken()

        async def scenario():
            task = self._stream()
            await self._first_token()
            self.llm.allow.release()
            outcome = (await asyncio.gather(task, return_exceptions=True))[0]
            self.assertIsInstance(outcome, ConnectionResetError)
            return await self._produced_after_the_end(self.llm.produced)
        self._assert_closed_within_one_item(*asyncio.run(scenario()))


class _ServerReply:
    """The chat server's streamed reply as the model client reads it (llm.py
    _parse_sse_stream): one server-sent-event line per item, a finish line,
    then [DONE]. A paced reply produces an item only when the case allows one;
    close() is the client closing the HTTP reply, after which the server
    produces nothing more. The case counts on the server's side."""

    def __init__(self, words: list[str], paced: bool, finish,
                 interval: float | None = None) -> None:
        import threading
        self.words = words
        self.paced = paced
        # With an interval the reply is paced by the clock, one item each
        # interval, as a server generating at its own speed; otherwise by the
        # case, one item per permit.
        self.interval = interval
        self.closed_at: float | None = None
        self.allow = threading.Semaphore(0)
        # The case's own end, shared by every reply the server gives: a reply
        # still waiting is let finish, so no thread outlives the case.
        self.finish = finish
        self.closed = threading.Event()
        self.produced = 0

    def _line(self, chunk: dict) -> bytes:
        import json
        return ("data: " + json.dumps(chunk) + "\n").encode()

    def __iter__(self):
        for word in self.words:
            if self.paced and self.interval is not None:
                if self.finish.wait(self.interval) or self.closed.is_set():
                    return
            elif self.paced:
                while not self.allow.acquire(timeout=0.05):
                    if self.finish.is_set() or self.closed.is_set():
                        return
            if self.closed.is_set():
                return
            self.produced += 1
            yield self._line({"choices": [{"delta": {"content": word}}]})
        yield self._line({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        yield b"data: [DONE]\n"

    def close(self) -> None:
        import time
        if self.closed_at is None:
            self.closed_at = time.monotonic()
        self.closed.set()


class _ChatServer:
    """Stands in for the machine's chat server behind urllib: the turn's first
    request (the streamed draft) is answered at once; every later request (a
    regeneration, a synthesis) gets a paced reply. Records each request."""

    def __init__(self, draft: str, later: str, *, paced_from: int = 1,
                 interval: float | None = None) -> None:
        import threading
        self.draft = draft
        self.later = later
        self.paced_from = paced_from
        self.interval = interval
        self.requests: list[list[dict]] = []
        self.replies: list[_ServerReply] = []
        self.finished = threading.Event()
        self._lock = threading.Lock()

    def urlopen(self, req, timeout=None):
        import json
        with self._lock:
            self.requests.append(json.loads(req.data)["messages"])
            index = len(self.requests) - 1
            text = self.draft if index == 0 else self.later
            reply = _ServerReply([w + " " for w in text.split()],
                                 paced=index >= self.paced_from,
                                 finish=self.finished, interval=self.interval)
            self.replies.append(reply)
        return reply

    def finish(self) -> None:
        self.finished.set()


def _model_client(chat: _ChatServer, case: unittest.TestCase, cls=None):
    """The real model client (llm.py LLMRouter, or ``cls`` built on it) with
    urllib answered by ``chat``. Its endpoint is a port nothing listens on, so
    a request that escaped the stand-in could reach no chat server; no cloud
    escalation."""
    from intergen.llm import LLMRouter
    patcher = mock.patch("urllib.request.urlopen", chat.urlopen)
    patcher.start()
    case.addCleanup(patcher.stop)
    case.addCleanup(chat.finish)
    return (cls or LLMRouter)({
        "endpoint": "http://127.0.0.1:9/v1/chat/completions",
        "escalation_mode": "never", "request_timeout": 5})


# What the model writes again when a screen asks it to: clean for every screen.
REGENERATED = (
    "Nothing was run this turn and nothing on this machine changed. Here is "
    "what the package does, how you could install it yourself when you are "
    "ready, and the exact command to type, shown first so you can read it "
    "before anything changes on this machine; ask me to explain any step of "
    "it in more detail and I will walk you through that part.")


class AnEndedTurnsRegenerationStopsWithinOneItem(unittest.TestCase):
    """After the streamed draft, the honesty screens may ask the model to write
    the answer again: the claim, self-offer and capability screens in
    _stream_llm_response. Each regeneration runs on a worker thread through the
    model client's chat(), which collects stream(). Ending the turn cancelled
    the coroutine awaiting it, not the thread, so the regeneration read its reply
    to the end and the chat server's one slot generated an answer nobody would
    see. The turn's stop is now bound in the context the turn's model calls run
    under, and stream() checks it at every item and closes the reply.

    Each door ends the turn (the real _run_turn, its body the streamed path for a
    freeform route) while a regeneration is reading its reply, lets the server
    offer five more items, and counts on the server's side how many it produced
    and whether the reply was closed; no request may be opened for the ended
    turn after that. The control: a turn that is not ended keeps the
    regenerated answer."""

    def _screens(self):
        from intergen.safety import (capability_grounding_note,
                                     model_offer_correction_note)
        return (
            # (screen, a draft that trips it and no screen before it, words
            #  the regeneration's request carries)
            ("claim", "Done! I have installed the package.",
             "You did NOT run"),
            ("self-offer", "Want me to restart the bluetooth service for you?",
             model_offer_correction_note()),
            ("capability", "Run pkm frobnicate to fix it.",
             capability_grounding_note("pkm frobnicate")),
        )

    def _turn(self, draft: str):
        from intergen.interfaces.types import RouteResult
        chat = _ChatServer(draft, REGENERATED)
        llm = _model_client(chat, self)
        router = _model_router(llm)
        server = WebServer(router=router, llm=llm, tools=None, governance=None)
        server._sessions = mock.MagicMock()
        server._send_session_list = mock.AsyncMock()
        ctx = ConnectionContext(client_id="case", source_interface="web",
                                ws=_RecordingWS(),
                                conversation=router.new_conversation())

        async def streamed_turn(c, data):
            await server._stream_llm_response(
                c, "old-turn", data["content"],
                RouteResult(text="", source="llm_freeform", handled=False))
        server._handle_client_message = streamed_turn

        def start() -> asyncio.Task:
            task = asyncio.create_task(server._run_turn(
                ctx, {"type": "message", "content": "is the package installed?"}))
            ctx.turn_task = task
            return task
        return chat, server, ctx, start

    async def _regenerating(self, chat: _ChatServer) -> _ServerReply:
        """Wait until a regeneration's request is open and has read one item."""
        for _ in range(500):
            if len(chat.replies) >= 2:
                break
            await asyncio.sleep(0.01)
        else:
            self.fail(f"no regeneration was requested: {len(chat.requests)} "
                      "request(s)")
        reply = chat.replies[1]
        reply.allow.release()
        for _ in range(500):
            if reply.produced >= 1:
                return reply
            await asyncio.sleep(0.01)
        self.fail("the regeneration never read its first item")

    def test_control_a_turn_that_is_not_ended_keeps_the_regenerated_answer(self):
        for screen, draft, carried in self._screens():
            with self.subTest(screen=screen):
                chat, server, ctx, start = self._turn(draft)

                async def scenario():
                    try:
                        task = start()
                        reply = await self._regenerating(chat)
                        for _ in range(len(reply.words)):
                            reply.allow.release()
                        await asyncio.wait_for(task, 10)
                        return reply
                    finally:
                        chat.finish()
                reply = asyncio.run(scenario())
                self.assertIn(carried, chat.requests[1][-1]["content"],
                              f"the {screen} screen did not ask for the "
                              "regeneration")
                self.assertEqual(reply.produced, len(reply.words))
                self.assertTrue(reply.closed.is_set())
                ends = [f for f in ctx.ws.sent if f.get("type") == "stream_end"]
                self.assertEqual(len(ends), 1, _frames(ctx.ws))
                self.assertEqual(ends[0].get("full_response", "").strip(),
                                 REGENERATED)

    def test_an_ended_turns_regeneration_stops_within_one_item(self):
        for screen, draft, carried in self._screens():
            for door in ("/clear", "/new"):
                with self.subTest(screen=screen, door=door):
                    chat, server, ctx, start = self._turn(draft)

                    async def scenario():
                        try:
                            task = start()
                            reply = await self._regenerating(chat)
                            at_end, opened = reply.produced, len(chat.requests)
                            if door == "/clear":
                                await server._handle_slash_command(
                                    ctx, {"command": "/clear"})
                            else:
                                await server._handle_new_session(ctx, {})
                            await asyncio.gather(task, return_exceptions=True)
                            self.assertTrue(task.cancelled(),
                                            "the turn was not ended")
                            for _ in range(5):
                                reply.allow.release()
                            closed = await asyncio.to_thread(reply.closed.wait,
                                                             2.0)
                            await asyncio.sleep(0.3)
                            return (reply.produced - at_end, closed,
                                    len(chat.requests) - opened)
                        finally:
                            chat.finish()
                    after, closed, opened_after = asyncio.run(scenario())
                    self.assertIn(carried, chat.requests[1][-1]["content"])
                    self.assertTrue(
                        closed,
                        f"the ended turn's {screen} regeneration was not "
                        f"closed: the server produced {after} more item(s) "
                        "after the turn ended and was still asked for the next")
                    self.assertLessEqual(
                        after, 1,
                        f"the ended turn's {screen} regeneration was read past "
                        "one item")
                    self.assertEqual(
                        opened_after, 0,
                        f"{opened_after} model request(s) were opened for the "
                        "turn after it ended")


class _TimedWS(_RecordingWS):
    """A frame recorder that notes when each frame was sent."""

    def __init__(self) -> None:
        super().__init__()
        self.sent_at: list[float] = []

    async def send_json(self, payload: dict) -> None:
        import time
        self.sent_at.append(time.monotonic())
        await super().send_json(payload)


def _tool_calling_client(chat: _ChatServer, case: unittest.TestCase):
    """The real model client whose streamed answer is one tool call: the
    stream is not what these cases measure; the synthesis after the tool is,
    and it runs through the real client against the stand-in chat server."""
    from intergen.interfaces.provenance import Provenance
    from intergen.interfaces.types import ToolCall
    from intergen.llm import LLMRouter

    class _AnswersWithOneToolCall(LLMRouter):
        def stream_with_tools(self, messages, *, tools, max_tokens=None,
                              temperature=None, image_data=None):
            yield ToolCall(name="system_info", arguments={},
                           call_id="call-1",
                           source_of_request=Provenance.USER_DIRECT)

    return _model_client(chat, case, cls=_AnswersWithOneToolCall)


class _OneTool:
    """The tool registry as the streamed path uses it: no schemas to offer and
    one executed, successful result for any call."""

    OUTPUT = "CPU: 4 cores at 2.4 GHz. Memory: 15 GB, of which 9 GB is free."

    def get_tool_schemas(self):
        return []

    def execute(self, call, **_kwargs):
        from intergen.interfaces.types import ToolResult
        return ToolResult(call_id=call.call_id, name=call.name,
                          content=self.OUTPUT, success=True, executed=True)


# The model's account of the tool's result, read one item per interval.
SYNTHESIS = (
    "Your machine has four processor cores running at two point four "
    "gigahertz, and fifteen gigabytes of memory of which nine are free right "
    "now, so there is plenty of room for what you described.")


class TheToolSynthesisRunsOffTheLoopAndStopsWithItsTurn(unittest.TestCase):
    """After a tool runs, the streamed path asks the model to put its result
    into words (llm.py continue_after_tool_call): a model call of up to 400
    tokens. It was called directly in the coroutine, so the event loop served
    nothing while it ran: no frame of any other connection, and no /clear for
    this one - a person who typed /clear waited for the synthesis to finish.
    It now runs in the default executor under the turn's bound context, as the
    regenerations do, and stops with its turn within one item.

    The stand-in chat server reads the synthesis out one item every 0.1 s,
    as a server generating at its own speed. The control: the synthesis reaches
    the page whole."""

    INTERVAL = 0.1

    def _turn(self):
        from intergen.interfaces.types import RouteResult
        # The streamed answer is the tool call, so the synthesis is the turn's
        # first request to the chat server.
        chat = _ChatServer(SYNTHESIS, SYNTHESIS, paced_from=0,
                           interval=self.INTERVAL)
        llm = _tool_calling_client(chat, self)
        router = _model_router(llm)
        server = WebServer(router=router, llm=llm, tools=_OneTool(),
                           governance=None)
        server._sessions = mock.MagicMock()
        server._sessions.list_sessions.return_value = []
        ctx = ConnectionContext(client_id="case", source_interface="web",
                                ws=_TimedWS(),
                                conversation=router.new_conversation())
        other = ConnectionContext(client_id="other", source_interface="web",
                                  ws=_TimedWS())

        async def streamed_turn(c, data):
            await server._stream_llm_response(
                c, "tool-turn", data["content"],
                RouteResult(text="", source="llm_tools", handled=False))
        server._handle_client_message = streamed_turn

        def start() -> asyncio.Task:
            task = asyncio.create_task(server._run_turn(
                ctx, {"type": "message", "content": "how much memory is free?"}))
            ctx.turn_task = task
            return task
        return chat, server, ctx, other, start

    async def _synthesizing(self, chat: _ChatServer) -> _ServerReply:
        """Wait until the synthesis has read its first item. With the call on
        the loop, this returns only once the call has finished."""
        for _ in range(1000):
            if chat.replies and chat.replies[0].produced >= 1:
                return chat.replies[0]
            await asyncio.sleep(0.01)
        self.fail("no synthesis was requested")

    def test_control_the_synthesis_reaches_the_page(self):
        chat, server, ctx, _other, start = self._turn()

        async def scenario():
            try:
                await asyncio.wait_for(start(), 20)
            finally:
                chat.finish()
        asyncio.run(scenario())
        self.assertEqual(len(chat.requests), 1)
        self.assertEqual(chat.replies[0].produced, len(SYNTHESIS.split()))
        self.assertTrue(chat.replies[0].closed.is_set())
        ends = [f for f in ctx.ws.sent if f.get("type") == "stream_end"]
        self.assertEqual(len(ends), 1, _frames(ctx.ws))
        self.assertEqual(ends[0].get("full_response", "").strip(), SYNTHESIS)

    def test_another_connections_frame_is_served_while_the_synthesis_runs(self):
        chat, server, ctx, other, start = self._turn()

        async def scenario():
            try:
                task = start()
                reply = await self._synthesizing(chat)
                await server._handle_list_sessions(other, {})
                await asyncio.wait_for(task, 20)
                return reply
            finally:
                chat.finish()
        reply = asyncio.run(scenario())
        served = [at for f, at in zip(other.ws.sent, other.ws.sent_at)
                  if f.get("type") == "session_list"]
        self.assertTrue(served, "the other connection's frame was never served")
        self.assertIsNotNone(reply.closed_at)
        self.assertLess(
            served[0], reply.closed_at,
            "the other connection's frame was served only after the tool "
            f"synthesis had read all {reply.produced} items and closed its "
            f"reply ({served[0] - reply.closed_at:+.2f} s): the synthesis held "
            "the event loop, and nothing else on this server could run")

    def test_an_ended_turns_synthesis_stops_within_one_item(self):
        for door in ("/clear", "/new"):
            with self.subTest(door=door):
                chat, server, ctx, _other, start = self._turn()

                async def scenario():
                    try:
                        task = start()
                        reply = await self._synthesizing(chat)
                        at_end, opened = reply.produced, len(chat.requests)
                        if door == "/clear":
                            await server._handle_slash_command(
                                ctx, {"command": "/clear"})
                        else:
                            await server._handle_new_session(ctx, {})
                        await asyncio.gather(task, return_exceptions=True)
                        ended = task.cancelled()
                        closed = await asyncio.to_thread(reply.closed.wait, 2.0)
                        await asyncio.sleep(6 * self.INTERVAL)
                        return (ended, reply.produced - at_end, closed,
                                len(chat.requests) - opened, reply)
                    finally:
                        chat.finish()
                ended, after, closed, opened_after, reply = asyncio.run(
                    scenario())
                self.assertTrue(
                    ended,
                    f"{door} did not end the turn during its synthesis: the "
                    f"synthesis had already read all {reply.produced} items "
                    "and the turn had finished, because the call held the "
                    "event loop and the door could not run until it returned")
                self.assertTrue(closed, "the ended turn's synthesis reply "
                                        "was not closed")
                self.assertLessEqual(
                    after, 1,
                    f"the ended turn's synthesis read {after} more item(s)")
                self.assertEqual(opened_after, 0)


# ---- the registered cloud fallback ------------------------------------------

CLOUD_ANSWER = ("The forecast for tomorrow is clear skies with a light breeze "
                "from the west.")

_LOCAL = "http://127.0.0.1:9/v1/chat/completions"


def _refused() -> Exception:
    import urllib.error
    return urllib.error.URLError(
        ConnectionRefusedError(111, "nothing is listening"))


class _PacedCloud(CloudProviderAdapter):
    """A registered cloud provider that keeps the cloud-provider contract
    (intergen/interfaces/cloud.py). Its reply arrives one item each time the
    case allows one, and before each read it checks the caller's stop, as the
    shipped HTTP adapter does chunk by chunk; on the stop it raises
    CloudCallStopped with nothing handed back. The case reads, on the
    provider's side, how many items were read, whether the stop was seen and
    whether an answer was handed back."""

    def __init__(self, text: str) -> None:
        import threading
        self.words = [w + " " for w in text.split()]
        self.allow = threading.Semaphore(0)
        # The case's own end: a call still waiting is let finish, so no
        # thread outlives the case.
        self.finish = threading.Event()
        self.entered = threading.Event()
        self.done = threading.Event()
        self.produced = 0
        self.stopped = False
        self.answered = False
        self.calls = 0

    @property
    def name(self) -> str:
        return "controlled"

    def _stop(self, should_stop) -> None:
        if should_stop is not None and should_stop():
            # Imported where it is raised: this module also runs against a
            # base whose contract carries no stop.
            from intergen.interfaces.cloud import CloudCallStopped
            self.stopped = True
            raise CloudCallStopped("controlled: the turn ended")

    def send(self, messages, *, tools=None, max_tokens=None, temperature=None,
             should_stop=None):
        self.calls += 1
        self.entered.set()
        try:
            for _word in self.words:
                self._stop(should_stop)
                while not self.allow.acquire(timeout=0.05):
                    if self.finish.is_set():
                        raise TimeoutError("the case ended before the reply "
                                           "was read")
                self.produced += 1
            self._stop(should_stop)
            self.answered = True
            return LLMResponse(text="".join(self.words).strip(),
                               model="controlled", local=False)
        finally:
            self.done.set()

    def stream(self, messages, *, tools=None, max_tokens=None,
               temperature=None, should_stop=None):
        yield self.send(messages, tools=tools, max_tokens=max_tokens,
                        temperature=temperature, should_stop=should_stop).text

    def test_connection(self):
        return True, "controlled"


class _BlockingCloud:
    """A registered provider that does NOT keep the contract, the shape the
    defect was first shown with: its send() takes no stop and blocks until the
    case releases it; then it answers, or raises when told to."""

    def __init__(self, fails: bool = False) -> None:
        import threading
        self.fails = fails
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def send(self, messages, *, max_tokens=None):
        self.calls += 1
        self.entered.set()
        if not self.release.wait(5):
            raise TimeoutError("the case did not release the provider")
        if self.fails:
            raise OSError("the provider went away")
        return LLMResponse(text=CLOUD_ANSWER, model="blocking", local=False)


class _ContractCloud(CloudProviderAdapter):
    """A registered provider that keeps the contract and blocks until the case
    releases it; then it answers, or fails before reading any item of its
    reply when told to (a connection lost early: there was no item at which to
    check the stop)."""

    def __init__(self, fails: bool = False) -> None:
        import threading
        self.fails = fails
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    @property
    def name(self) -> str:
        return "contract"

    def send(self, messages, *, tools=None, max_tokens=None, temperature=None,
             should_stop=None):
        self.calls += 1
        self.entered.set()
        if not self.release.wait(5):
            raise TimeoutError("the case did not release the provider")
        if self.fails:
            raise OSError("the provider went away before its first item")
        return LLMResponse(text=CLOUD_ANSWER, model="contract", local=False)

    def stream(self, messages, **kwargs):
        yield self.send(messages, **kwargs).text

    def test_connection(self):
        return True, "contract"


class _StopThenFailCloud(CloudProviderAdapter):
    """A registered provider that keeps the contract and blocks until the case
    releases it; then it reads the caller's stop, and if it is set ends the
    call in an ORDINARY error (its reply abandoned), not CloudCallStopped.
    With no stop set it answers."""

    def __init__(self) -> None:
        import threading
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.saw_stop = False

    @property
    def name(self) -> str:
        return "stop-then-fail"

    def send(self, messages, *, tools=None, max_tokens=None, temperature=None,
             should_stop=None):
        self.calls += 1
        self.entered.set()
        if not self.release.wait(5):
            raise TimeoutError("the case did not release the provider")
        if should_stop is not None and should_stop():
            self.saw_stop = True
            raise ConnectionError("the reply was abandoned")
        return LLMResponse(text=CLOUD_ANSWER, model="stop-then-fail",
                           local=False)

    def stream(self, messages, **kwargs):
        yield self.send(messages, **kwargs).text

    def test_connection(self):
        return True, "stop-then-fail"


class _StopByPositionCloud(CloudProviderAdapter):
    """A provider whose send() names should_stop, but takes it only by
    position: the router hands the stop by keyword, so it could never reach
    the parameter."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "by-position"

    def send(self, messages, should_stop=None, /, *, tools=None,
             max_tokens=None, temperature=None):
        self.calls += 1
        return LLMResponse(text=CLOUD_ANSWER, model="by-position", local=False)

    def stream(self, messages, should_stop=None, /, **kwargs):
        yield CLOUD_ANSWER

    def test_connection(self):
        return True, "by-position"


class _StopByPositionIntoKeywordsCloud(_StopByPositionCloud):
    """The same parameter beside a **kwargs: the router's should_stop would
    land in the keywords, and the parameter would keep its default."""

    def send(self, messages, should_stop=None, /, **kwargs):
        self.calls += 1
        return LLMResponse(text=CLOUD_ANSWER, model="by-position", local=False)


class _RequiredStopCloud(CloudProviderAdapter):
    """A provider whose send() takes should_stop by keyword but has no default
    for it: a caller with no stop bound calls send() without the keyword, and
    Python refuses that call before the provider's body."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "required-stop"

    def send(self, messages, *, tools=None, max_tokens=None,
             temperature=None, should_stop):
        self.calls += 1
        return LLMResponse(text=CLOUD_ANSWER, model="required-stop",
                           local=False)

    def stream(self, messages, *, tools=None, max_tokens=None,
               temperature=None, should_stop=None):
        yield CLOUD_ANSWER

    def test_connection(self):
        return True, "required-stop"


class _CloudWire:
    """The cloud provider's side of an HTTP reply as the shipped adapter reads
    it (intergen/cloud/http_adapter.py): the body arrives in pieces, one each
    time the case allows one; read1(n) and read(n) return the next piece, as
    one receive brings it, and read() with no size every piece left. The case
    counts the pieces sent and whether the reply was closed."""

    def __init__(self, body: bytes, pieces: int, finish) -> None:
        import threading
        size = -(-len(body) // pieces)
        self.pieces = [body[i:i + size] for i in range(0, len(body), size)]
        self.allow = threading.Semaphore(0)
        self.finish = finish
        self.sent = 0
        self.closed = threading.Event()

    def _next(self) -> bytes:
        if self.sent >= len(self.pieces):
            return b""
        while not self.allow.acquire(timeout=0.05):
            if self.finish.is_set():
                raise TimeoutError("the case ended before the reply was read")
        piece = self.pieces[self.sent]
        self.sent += 1
        return piece

    def read1(self, n: int) -> bytes:
        return self._next()

    def read(self, n: int = -1) -> bytes:
        if n is not None and n >= 0:
            return self._next()
        out = []
        while True:
            piece = self._next()
            if not piece:
                return b"".join(out)
            out.append(piece)

    def close(self) -> None:
        self.closed.set()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class _CloudTransport:
    """urllib for the router-level cases: nothing listens on the machine's
    chat server, so every local request is refused (and chat() escalates
    without a local retry), and the cloud endpoint answers with a reply
    released piece by piece. Records each request."""

    def __init__(self, answer: str = CLOUD_ANSWER, pieces: int = 6) -> None:
        import json
        import threading
        self.body = json.dumps({
            "choices": [{"message": {"content": answer}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 9},
        }).encode()
        self.pieces = pieces
        self.finish = threading.Event()
        self.local_requests = 0
        self.wires: list[_CloudWire] = []

    def urlopen(self, req, timeout=None):
        if req.full_url == _LOCAL:
            self.local_requests += 1
            raise _refused()
        wire = _CloudWire(self.body, self.pieces, self.finish)
        self.wires.append(wire)
        return wire


class _CloudErrorTransport(_CloudTransport):
    """The same urllib, with the cloud endpoint answering an HTTP error (a
    429): urlopen raises it as HTTPError, its body a _CloudWire released piece
    by piece, longer than the 500 characters of detail an error keeps."""

    def __init__(self, code: int = 429, pieces: int = 6) -> None:
        import json
        super().__init__(pieces=pieces)
        self.code = code
        self.body = json.dumps({"error": {
            "message": "rate limited: " + "try again later. " * 40}}).encode()

    def urlopen(self, req, timeout=None):
        import urllib.error
        if req.full_url == _LOCAL:
            self.local_requests += 1
            raise _refused()
        wire = _CloudWire(self.body, self.pieces, self.finish)
        self.wires.append(wire)
        raise urllib.error.HTTPError(req.full_url, self.code, "controlled",
                                     None, wire)


class AnEndedTurnsCloudCallStopsWithinOneItem(unittest.TestCase):
    """When the machine's chat server cannot answer, chat() escalates to the
    registered cloud providers (_escalate_to_cloud). The turn's stop was checked
    before the escalation, but a call already inside a provider's send() never
    read it: the call went on to the end of its reply after the turn ended and
    handed its answer back into the ended turn. The stop is now part of the
    cloud-provider contract: a provider that keeps it is handed the turn's stop
    and checks it at each item, no provider is asked once the turn has ended,
    and an answer that arrives after the end is discarded. A provider that
    keeps no contract, or whose send() takes no stop, is refused at
    registration and so never called.

    Each case binds the turn's stop in the context its model call runs under,
    as the web turn does, makes the call on a worker thread, ends the turn while
    the provider is reading its reply, and then lets the reply go on. The
    controls: a turn that is not ended gets the cloud answer."""

    maxDiff = None

    def setUp(self) -> None:
        self.transport = _CloudTransport()
        patcher = mock.patch("urllib.request.urlopen", self.transport.urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.transport.finish.set)
        # What registration logged at WARNING or above, and what it answered.
        self.logged: list[str] = []
        self.registered: list = []

    def _router(self, *providers):
        import logging
        from intergen.llm import LLMRouter
        llm = LLMRouter({"endpoint": _LOCAL, "escalation_mode": "auto",
                         "request_timeout": 1})
        log = logging.getLogger("intergen.llm")
        handler = logging.Handler(level=logging.WARNING)
        handler.emit = lambda record: self.logged.append(record.getMessage())
        log.addHandler(handler)
        try:
            for name, provider in providers:
                self.registered.append(
                    llm.register_cloud_provider(name, provider))
        finally:
            log.removeHandler(handler)
        return llm

    def _call_in_turn(self, llm, stop):
        """chat() on a worker thread whose context binds ``stop``."""
        import threading
        from intergen.interfaces.types import Message, MessageRole
        from intergen.llm import bind_turn_stop, unbind_turn_stop
        out: dict = {}

        def call() -> None:
            token = bind_turn_stop(stop)
            try:
                out["reply"] = llm.chat([Message(
                    role=MessageRole.USER, content="what is the forecast?")])
            except BaseException as exc:  # read by the case
                out["error"] = exc
            finally:
                unbind_turn_stop(token)

        thread = threading.Thread(target=call, name="cloud-turn", daemon=True)
        thread.start()
        return thread, out

    @staticmethod
    def _text(out: dict) -> str:
        if "error" in out:
            return f"<raised {out['error']!r}>"
        if "reply" not in out:
            return "<still running>"
        return out["reply"].text

    def _wait(self, predicate, what: str) -> None:
        import time
        for _ in range(500):
            if predicate():
                return
            time.sleep(0.01)
        self.fail(what)

    def test_control_a_turn_that_is_not_ended_gets_the_cloud_answer(self):
        import threading
        cloud = _PacedCloud(CLOUD_ANSWER)
        self.addCleanup(cloud.finish.set)
        llm = self._router(("controlled", cloud))
        thread, out = self._call_in_turn(llm, threading.Event())
        self._wait(cloud.entered.is_set, "the cloud provider was never asked")
        for _ in cloud.words:
            cloud.allow.release()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self._text(out), CLOUD_ANSWER)
        self.assertEqual(out["reply"].model, "cloud:controlled")
        self.assertEqual(cloud.produced, len(cloud.words))
        self.assertEqual(self.transport.local_requests, 1)

    def test_an_ended_turns_cloud_call_stops_within_one_item(self):
        import threading
        cloud = _PacedCloud(CLOUD_ANSWER)
        self.addCleanup(cloud.finish.set)
        llm = self._router(("controlled", cloud))
        stop = threading.Event()
        thread, out = self._call_in_turn(llm, stop)
        self._wait(cloud.entered.is_set, "the cloud provider was never asked")
        cloud.allow.release()
        self._wait(lambda: cloud.produced >= 1,
                   "the provider never read its first item")
        at_end = cloud.produced
        stop.set()
        for _ in cloud.words[1:]:
            cloud.allow.release()
        thread.join(2)
        self.assertEqual({
            "items read after the turn ended, at most one":
                cloud.produced - at_end <= 1,
            "the provider saw the turn's stop": cloud.stopped,
            "the provider handed an answer back": cloud.answered,
            "chat() returned": self._text(out),
            "the call still running 2 s after the turn ended":
                thread.is_alive(),
        }, {
            "items read after the turn ended, at most one": True,
            "the provider saw the turn's stop": True,
            "the provider handed an answer back": False,
            "chat() returned": "",
            "the call still running 2 s after the turn ended": False,
        }, f"{cloud.produced - at_end} item(s) were read after the turn ended")

    def _http_provider(self):
        from intergen.cloud import create_adapter
        from intergen.interfaces.cloud import ProviderConfig
        patcher = mock.patch("intergen.cloud.http_adapter.lookup_secret",
                             lambda keyring_id: "TEST-KEY")
        patcher.start()
        self.addCleanup(patcher.stop)
        return create_adapter(ProviderConfig(
            name="controlled", adapter="openai", model="m1",
            api_key_keyring_id="kid", base_url="https://cloud.invalid/v1"))

    def test_control_the_shipped_http_adapter_answers_a_turn_not_ended(self):
        import threading
        llm = self._router(("controlled", self._http_provider()))
        thread, out = self._call_in_turn(llm, threading.Event())
        self._wait(lambda: self.transport.wires, "no cloud request was opened")
        wire = self.transport.wires[0]
        for _ in wire.pieces:
            wire.allow.release()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self._text(out), CLOUD_ANSWER)
        self.assertEqual(wire.sent, len(wire.pieces))
        self.assertTrue(wire.closed.is_set())

    def test_the_shipped_http_adapter_closes_an_ended_turns_reply(self):
        import threading
        llm = self._router(("controlled", self._http_provider()))
        stop = threading.Event()
        thread, out = self._call_in_turn(llm, stop)
        self._wait(lambda: self.transport.wires, "no cloud request was opened")
        wire = self.transport.wires[0]
        wire.allow.release()
        self._wait(lambda: wire.sent >= 1, "the adapter never read its reply")
        at_end = wire.sent
        stop.set()
        for _ in wire.pieces[1:]:
            wire.allow.release()
        thread.join(2)
        self.assertEqual({
            "pieces read after the turn ended, at most one":
                wire.sent - at_end <= 1,
            "the reply closed": wire.closed.is_set(),
            "chat() returned": self._text(out),
            "cloud requests opened": len(self.transport.wires),
            "the call still running 2 s after the turn ended":
                thread.is_alive(),
        }, {
            "pieces read after the turn ended, at most one": True,
            "the reply closed": True,
            "chat() returned": "",
            "cloud requests opened": 1,
            "the call still running 2 s after the turn ended": False,
        }, f"{wire.sent - at_end} piece(s) of {len(wire.pieces)} were read "
           "after the turn ended")

    def test_the_shipped_http_adapter_opens_nothing_for_an_ended_turn(self):
        """The contract's own check, before the key is read or a request
        opened: a caller whose turn has ended gets CloudCallStopped."""
        provider = self._http_provider()
        keys: list[str] = []
        with mock.patch("intergen.cloud.http_adapter.lookup_secret",
                        lambda keyring_id: keys.append(keyring_id) or "K"):
            from intergen.interfaces.types import Message, MessageRole
            with self.assertRaises(Exception) as raised:
                provider.send([Message(role=MessageRole.USER, content="hi")],
                              should_stop=lambda: True)
        self.assertEqual(type(raised.exception).__name__, "CloudCallStopped",
                         repr(raised.exception))
        self.assertEqual((keys, self.transport.wires), ([], []))

    def _error_reply(self) -> _CloudErrorTransport:
        """The cloud endpoint answers with an HTTP error, its body in
        pieces."""
        transport = _CloudErrorTransport()
        patcher = mock.patch("urllib.request.urlopen", transport.urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(transport.finish.set)
        return transport

    @staticmethod
    def _send_on_a_thread(provider, should_stop):
        """The provider's send() on a worker thread; what it raised."""
        import threading
        from intergen.interfaces.types import Message, MessageRole
        out: dict = {}

        def call() -> None:
            try:
                provider.send([Message(role=MessageRole.USER, content="hi")],
                              should_stop=should_stop)
            except BaseException as exc:  # read by the case
                out["raised"] = exc

        thread = threading.Thread(target=call, name="cloud-send", daemon=True)
        thread.start()
        return thread, out

    def test_the_shipped_http_adapter_stops_reading_an_ended_turns_error_reply(
            self):
        """The cloud endpoint answers with an HTTP error and the turn ends
        while the adapter reads its body. The body was read in one unbounded
        read outside the stop-aware reader and never closed, so the call read
        it to its end after the turn ended and came back as a provider error.
        It is now read through the same reader as a success's: the call stops
        within the piece in flight, as CloudCallStopped, and the reply is
        closed."""
        import threading
        transport = self._error_reply()
        provider = self._http_provider()
        stop = threading.Event()
        thread, out = self._send_on_a_thread(provider, stop.is_set)
        self._wait(lambda: transport.wires, "no cloud request was opened")
        wire = transport.wires[0]
        wire.allow.release()
        self._wait(lambda: wire.sent >= 1,
                   "the adapter never read the error reply")
        at_end = wire.sent
        stop.set()
        for _ in wire.pieces[1:]:
            wire.allow.release()
        thread.join(2)
        self.assertEqual({
            "pieces read after the turn ended, at most one":
                wire.sent - at_end <= 1,
            "the error reply closed": wire.closed.is_set(),
            "the call ended as": type(out.get("raised")).__name__,
            "the call still running 2 s after the turn ended":
                thread.is_alive(),
        }, {
            "pieces read after the turn ended, at most one": True,
            "the error reply closed": True,
            "the call ended as": "CloudCallStopped",
            "the call still running 2 s after the turn ended": False,
        }, f"{wire.sent - at_end} piece(s) of {len(wire.pieces)} were read "
           "after the turn ended")

    def test_the_shipped_http_adapter_gives_an_ended_turn_nothing_on_an_error_reply(
            self):
        """The same through chat(): the ended turn is given nothing, the error
        reply is read no further than the piece in flight, and the call is
        noted as stopped in its send, never an error read after the turn
        ended. (That the reply is closed is read where the error is held, in
        the adapter's own cases: here nothing holds it once the call ends,
        and a freed error closes its body by itself.)"""
        import threading
        transport = self._error_reply()
        llm = self._router(("controlled", self._http_provider()))
        logged = self._llm_log()
        stop = threading.Event()
        thread, out = self._call_in_turn(llm, stop)
        self._wait(lambda: transport.wires, "no cloud request was opened")
        wire = transport.wires[0]
        wire.allow.release()
        self._wait(lambda: wire.sent >= 1,
                   "the adapter never read the error reply")
        at_end = wire.sent
        stop.set()
        for _ in wire.pieces[1:]:
            wire.allow.release()
        thread.join(2)
        self.assertEqual({
            "pieces read after the turn ended, at most one":
                wire.sent - at_end <= 1,
            "chat() returned": self._text(out),
            "the call noted as stopped in its send": any(
                "cloud call stopped: its turn ended (controlled, in_send)" in m
                for m in logged),
            "an error read after the turn ended": any(
                "ended in an error after its turn ended" in m
                for m in logged),
            "cloud requests opened": len(transport.wires),
            "the call still running 2 s after the turn ended":
                thread.is_alive(),
        }, {
            "pieces read after the turn ended, at most one": True,
            "chat() returned": "",
            "the call noted as stopped in its send": True,
            "an error read after the turn ended": False,
            "cloud requests opened": 1,
            "the call still running 2 s after the turn ended": False,
        }, f"{wire.sent - at_end} piece(s) of {len(wire.pieces)} were read "
           "after the turn ended")

    def test_the_shipped_http_adapter_closes_an_error_reply_it_read(self):
        """A turn that does not end: the error reply is read to its end,
        through the same reader, and closed; the call is a provider error."""
        import threading
        transport = self._error_reply()
        provider = self._http_provider()
        thread, out = self._send_on_a_thread(provider,
                                             threading.Event().is_set)
        self._wait(lambda: transport.wires, "no cloud request was opened")
        wire = transport.wires[0]
        for _ in wire.pieces:
            wire.allow.release()
        thread.join(5)
        self.assertEqual({
            "pieces read": wire.sent,
            "the error reply closed": wire.closed.is_set(),
            "the call ended as": type(out.get("raised")).__name__,
            "the call still running": thread.is_alive(),
        }, {
            "pieces read": len(wire.pieces),
            "the error reply closed": True,
            "the call ended as": "CloudAdapterError",
            "the call still running": False,
        })

    def test_control_an_error_reply_keeps_its_first_500_characters(self):
        """The control: a caller that hands no stop (test_connection, a call
        with nothing bound) is answered with the provider's error as before,
        the whole body read and its first 500 characters kept as the
        detail."""
        transport = self._error_reply()
        provider = self._http_provider()
        thread, out = self._send_on_a_thread(provider, None)
        self._wait(lambda: transport.wires, "no cloud request was opened")
        wire = transport.wires[0]
        for _ in wire.pieces:
            wire.allow.release()
        thread.join(5)
        raised = out.get("raised")
        self.assertEqual({
            "the call ended as": type(raised).__name__,
            "its text": str(raised),
            "pieces read": wire.sent,
        }, {
            "the call ended as": "CloudAdapterError",
            "its text": f"{provider.name} HTTP 429: "
                        + transport.body.decode()[:500],
            "pieces read": len(wire.pieces),
        })

    def _refusal(self, name: str, cloud, gap: str) -> dict:
        """Register ``cloud``, ask the router a question with no stop bound
        (as the command line does), and read what registration answered and
        logged and whether the provider was asked."""
        from intergen.interfaces.types import Message, MessageRole
        llm = self._router((name, cloud))
        reply = llm.chat([Message(role=MessageRole.USER,
                                  content="what is the forecast?")])
        answer = self.registered[0]
        return {
            "the registration refused it, naming why": answer if not
                isinstance(answer, tuple) else (answer[0], gap in answer[1]),
            "the refusal logged": any(f"refused cloud provider {name}" in m
                                      for m in self.logged),
            "the provider was asked": cloud.calls,
            "chat() answered from the cloud": reply.model.startswith("cloud:"),
        }

    REFUSED = {
        "the registration refused it, naming why": (False, True),
        "the refusal logged": True,
        "the provider was asked": 0,
        "chat() answered from the cloud": False,
    }

    def test_a_provider_without_the_contract_is_refused_and_never_called(
            self):
        """A provider that keeps no cloud-provider contract could not be
        stopped when its turn ends. The router refuses to register it, says
        why in words, and so never calls it."""
        cloud = _BlockingCloud()
        cloud.release.set()
        self.assertEqual(self._refusal(
            "blocking", cloud, "does not implement the cloud-provider contract"),
            self.REFUSED)

    def test_a_provider_whose_send_takes_no_stop_is_refused(self):
        """A provider written against the contract as it was before the stop:
        its send() takes no should_stop, so the router could not hand it the
        turn's stop."""

        class _BeforeTheStop(CloudProviderAdapter):
            def __init__(self) -> None:
                self.calls = 0

            @property
            def name(self) -> str:
                return "before-the-stop"

            def send(self, messages, *, tools=None, max_tokens=None,
                     temperature=None):
                self.calls += 1
                return LLMResponse(text=CLOUD_ANSWER, model="before",
                                   local=False)

            def stream(self, messages, *, tools=None, max_tokens=None,
                       temperature=None):
                yield CLOUD_ANSWER

            def test_connection(self):
                return True, "before-the-stop"

        cloud = _BeforeTheStop()
        self.assertEqual(self._refusal(
            "before-the-stop", cloud, "its send() takes no should_stop"),
            self.REFUSED)

    def test_a_provider_whose_send_requires_the_stop_is_refused(self):
        """A provider whose send() takes should_stop by keyword but has no
        default for it. The contract's check read only how the parameter is
        taken, so it was registered; then a caller with no stop bound - the
        command line, the bus - called it without the keyword, Python refused
        the call before the provider's body, and the escalation came back with
        no answer. It is now refused at registration, and the refusal names
        that gap and what it costs, nothing else."""
        refusal = self._refusal("required-stop", _RequiredStopCloud(),
                                "its send() requires should_stop")
        self.assertEqual({
            **refusal, "the refusal's words": self.registered[0][1],
        }, {
            **self.REFUSED, "the refusal's words": (
                "refused cloud provider required-stop: its send() requires "
                "should_stop, so a caller with no stop bound could not call "
                "it"),
        })

    def test_a_provider_that_requires_the_stop_put_into_the_router_is_skipped(
            self):
        """Put into the router's table without registration, the same
        provider is skipped at the call, with an ERROR naming the gap and
        what it costs, nothing else."""
        import logging
        from intergen.interfaces.types import Message, MessageRole
        llm = self._router()
        cloud = _RequiredStopCloud()
        llm._cloud_providers["direct"] = cloud
        errors: list[str] = []
        handler = logging.Handler(level=logging.ERROR)
        handler.emit = lambda record: errors.append(record.getMessage())
        log = logging.getLogger("intergen.llm")
        log.addHandler(handler)
        try:
            reply = llm.chat([Message(role=MessageRole.USER,
                                      content="what is the forecast?")])
        finally:
            log.removeHandler(handler)
        self.assertEqual({
            "the provider was asked": cloud.calls,
            "chat() answered from the cloud": reply.model.startswith("cloud:"),
            "the skip logged, naming why": [
                m for m in errors if m.startswith("skipped cloud provider")],
        }, {
            "the provider was asked": 0,
            "chat() answered from the cloud": False,
            "the skip logged, naming why": [
                "skipped cloud provider direct: its send() requires "
                "should_stop, so a caller with no stop bound could not call "
                "it"],
        })

    def test_each_gap_is_named_with_its_own_cost(self):
        """The router's two messages print the gap alone, so each gap carries
        its own cost. The words that once followed every gap - a turn that
        ends could not stop its call - are not true of a send() that cannot
        be read, of one the router's stop could never reach, or of one that
        requires the stop."""
        from intergen.llm import cloud_contract_gap

        class _NoStop(_ContractCloud):
            def send(self, messages, *, tools=None, max_tokens=None,
                     temperature=None):
                raise AssertionError("never called")

        class _Unreadable(_ContractCloud):
            send = None

        self.assertEqual({
            name: cloud_contract_gap(adapter) for name, adapter in (
                ("no contract", _BlockingCloud()),
                ("unreadable", _Unreadable()),
                ("no stop", _NoStop()),
                ("by position", _StopByPositionCloud()),
                ("by position beside keywords",
                 _StopByPositionIntoKeywordsCloud()),
                ("required", _RequiredStopCloud()),
                ("the contract", _ContractCloud()),
            )
        }, {
            "no contract": "it does not implement the cloud-provider "
                           "contract, so a turn that ends could not stop its "
                           "call",
            "unreadable": "its send() cannot be read, so whether it takes the "
                          "turn's stop cannot be checked",
            "no stop": "its send() takes no should_stop, so a turn that ends "
                       "could not stop its call",
            "by position": "its send() does not take should_stop by keyword, "
                           "so the stop the router hands by keyword could "
                           "never reach it",
            "by position beside keywords": "its send() does not take "
                                           "should_stop by keyword, so the "
                                           "stop the router hands by keyword "
                                           "could never reach it",
            "required": "its send() requires should_stop, so a caller with no "
                        "stop bound could not call it",
            "the contract": None,
        })

    def test_a_provider_put_into_the_router_without_registration_is_never_called(
            self):
        """Registration refuses a provider without the contract, but the
        router's table can be written some other way. Such a provider is
        skipped at the call, with an ERROR naming why: no call is made that
        the turn's stop could not reach."""
        import logging
        from intergen.interfaces.types import Message, MessageRole
        llm = self._router()
        cloud = _BlockingCloud()
        cloud.release.set()
        llm._cloud_providers["direct"] = cloud
        errors: list[str] = []
        handler = logging.Handler(level=logging.ERROR)
        handler.emit = lambda record: errors.append(record.getMessage())
        log = logging.getLogger("intergen.llm")
        log.addHandler(handler)
        try:
            reply = llm.chat([Message(role=MessageRole.USER,
                                      content="what is the forecast?")])
        finally:
            log.removeHandler(handler)
        self.assertEqual({
            "the provider was asked": cloud.calls,
            "chat() answered from the cloud": reply.model.startswith("cloud:"),
            "the skip logged, naming why": any(
                "skipped cloud provider direct: it does not implement the "
                "cloud-provider contract" in m for m in errors),
        }, {
            "the provider was asked": 0,
            "chat() answered from the cloud": False,
            "the skip logged, naming why": True,
        })

    def test_an_ended_turn_is_not_handed_on_to_the_next_provider(self):
        import threading
        first = _ContractCloud(fails=True)
        second = _ContractCloud()
        second.release.set()
        self.addCleanup(first.release.set)
        llm = self._router(("first", first), ("second", second))
        stop = threading.Event()
        thread, out = self._call_in_turn(llm, stop)
        self._wait(first.entered.is_set, "the first provider was never asked")
        stop.set()
        first.release.set()
        thread.join(2)
        self.assertEqual({
            "the next provider was asked": second.calls,
            "chat() returned": self._text(out),
        }, {
            "the next provider was asked": 0,
            "chat() returned": "",
        })

    def _llm_log(self) -> list[str]:
        """What intergen.llm logs at INFO and above during the case."""
        import logging
        records: list[str] = []
        log = logging.getLogger("intergen.llm")
        handler = logging.Handler(level=logging.INFO)
        handler.emit = lambda record: records.append(record.getMessage())
        level = log.level
        log.setLevel(logging.INFO)
        log.addHandler(handler)
        self.addCleanup(log.setLevel, level)
        self.addCleanup(log.removeHandler, handler)
        return records

    def test_an_ended_turns_call_that_ends_in_an_error_is_given_nothing(self):
        """A provider keeping the contract sees the turn's stop and ends its
        call in an ordinary error, not CloudCallStopped. The router took the
        error for a provider failure and went on; with no provider left it
        came back with no cloud answer, and chat() served the ended turn its
        local fallback. The error is now read against the stop: the call is
        noted as stopped in its send, and the ended turn is given nothing."""
        import threading
        cloud = _StopThenFailCloud()
        self.addCleanup(cloud.release.set)
        llm = self._router(("only", cloud))
        logged = self._llm_log()
        stop = threading.Event()
        thread, out = self._call_in_turn(llm, stop)
        self._wait(cloud.entered.is_set, "the cloud provider was never asked")
        stop.set()
        cloud.release.set()
        thread.join(2)
        self.assertEqual({
            "the provider saw the turn's stop": cloud.saw_stop,
            "chat() returned": self._text(out),
            "the call noted as stopped in its send": any(
                "cloud call stopped: its turn ended (only, in_send)" in m
                for m in logged),
            "logged as a provider failure": any(
                m.startswith("Cloud provider only failed") for m in logged),
        }, {
            "the provider saw the turn's stop": True,
            "chat() returned": "",
            "the call noted as stopped in its send": True,
            "logged as a provider failure": False,
        })

    def test_control_a_call_that_fails_in_a_turn_not_ended_is_a_failure(self):
        """The control: a provider that fails in a turn that has not ended
        is still a provider failure, logged as one, and the turn is served
        its local fallback, as before."""
        import threading
        cloud = _ContractCloud(fails=True)
        cloud.release.set()
        llm = self._router(("only", cloud))
        logged = self._llm_log()
        thread, out = self._call_in_turn(llm, threading.Event())
        thread.join(5)
        reply = out.get("reply")
        self.assertEqual({
            "chat() served its local fallback": bool(
                reply is not None and reply.model == "local"
                and reply.text.strip()),
            "logged as a provider failure": any(
                m.startswith("Cloud provider only failed") for m in logged),
            "the call noted as stopped": any(
                "cloud call stopped" in m for m in logged),
        }, {
            "chat() served its local fallback": True,
            "logged as a provider failure": True,
            "the call noted as stopped": False,
        })

    def test_an_ended_turn_is_given_no_local_answer_after_the_escalation(self):
        """chat() reads the stop again once the escalation has come back with
        no cloud answer, before its local fallback, so a turn that ended
        during the escalation, by whatever path, is given nothing. Here the
        escalation ends the turn and comes back with no answer."""
        import threading
        llm = self._router()
        stop = threading.Event()

        def an_escalation_that_ends_the_turn(messages, **_kwargs):
            stop.set()
            return None

        llm._escalate_to_cloud = an_escalation_that_ends_the_turn
        thread, out = self._call_in_turn(llm, stop)
        thread.join(5)
        self.assertEqual(self._text(out), "")

    def test_a_provider_that_takes_the_stop_only_by_position_is_refused(
            self):
        """send() names should_stop but takes it only by position, so the
        router's call, which hands the stop by keyword, could never reach
        it. Registration read the name and not how it is taken."""
        self.assertEqual(self._refusal(
            "by-position", _StopByPositionCloud(),
            "does not take should_stop by keyword"), self.REFUSED)

    def test_a_stop_by_position_beside_keywords_is_refused(self):
        """The same parameter beside a **kwargs: the router's should_stop
        would go into the keywords and the parameter keep its default, so
        the stop would never be read."""
        self.assertEqual(self._refusal(
            "by-position", _StopByPositionIntoKeywordsCloud(),
            "does not take should_stop by keyword"), self.REFUSED)

    def test_a_stop_by_position_put_into_the_router_directly_is_skipped(
            self):
        """Put into the router's table without registration, the same
        provider is skipped at the call, with an ERROR naming why."""
        import logging
        from intergen.interfaces.types import Message, MessageRole
        llm = self._router()
        cloud = _StopByPositionCloud()
        llm._cloud_providers["direct"] = cloud
        errors: list[str] = []
        handler = logging.Handler(level=logging.ERROR)
        handler.emit = lambda record: errors.append(record.getMessage())
        log = logging.getLogger("intergen.llm")
        log.addHandler(handler)
        try:
            reply = llm.chat([Message(role=MessageRole.USER,
                                      content="what is the forecast?")])
        finally:
            log.removeHandler(handler)
        self.assertEqual({
            "the provider was asked": cloud.calls,
            "chat() answered from the cloud": reply.model.startswith("cloud:"),
            "the skip logged, naming why": any(
                "skipped cloud provider direct: its send() does not take "
                "should_stop by keyword" in m for m in errors),
        }, {
            "the provider was asked": 0,
            "chat() answered from the cloud": False,
            "the skip logged, naming why": True,
        })


class _LongReply:
    """A reply's body as an endpoint sends a long one: read(n) and read1(n)
    return its next n bytes (fewer only at its end; for read1, one receive
    that brings them all) and read() with no size all the rest.
    ``during``, when given, is called inside each read with the reply
    and the piece it returns - what happens while that read is in flight.
    The case reads how many bytes the adapter read and whether it closed the
    reply."""

    def __init__(self, body: bytes, during=None) -> None:
        self.body = body
        self.during = during
        self.sent = 0
        self.closed = False

    def read(self, n: int = -1) -> bytes:
        end = len(self.body) if n is None or n < 0 else self.sent + n
        piece = self.body[self.sent:end]
        self.sent += len(piece)
        if self.during is not None:
            self.during(self, piece)
        return piece

    read1 = read

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


#: How much of an error reply's body the adapter reads as its prefix: 64 KiB.
_ERROR_BODY_BOUND = 64 * 1024

_OVERLOADED = b'{"error": {"message": "overloaded: '


class AnErrorReplysBodyIsReadAsABoundedPrefix(unittest.TestCase):
    """An endpoint can answer an HTTP error with a body of any length. The
    adapter read the whole of an error's body to keep its first 500
    characters as the error's detail, so an endpoint answering with a huge
    or unending body held the call while it kept sending, the buffer growing
    with it: the socket's timeout bounds each wait, never the read. The body
    is now read through the same stop-aware reader under a bound - 64 KiB,
    and one byte more, which tells a body cut at the bound from one that
    ends there - and the reply is closed after the bound as after its end;
    the detail keeps its first 500 characters, and a body cut at the bound
    says so. The stop of a turn that ended during the read that found the
    end of the body was lost: the reader checked it only before each read,
    so the body was handed back and the call came back as a provider error.
    The stop is now checked once more after the last read.

    Each case calls the shipped adapter's send() with urllib's urlopen
    standing in for the network, with no stop (test_connection, the command
    line) or with a turn's stop. The controls: an error body that ends
    exactly at the bound is read to its end and not said to be cut, a
    successful answer far past the bound is read whole, as before, and a
    turn that ends during the read that reaches the bound stops the call."""

    maxDiff = None

    def setUp(self) -> None:
        patcher = mock.patch("intergen.cloud.http_adapter.lookup_secret",
                             lambda keyring_id: "TEST-KEY")
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _provider():
        from intergen.cloud import create_adapter
        from intergen.interfaces.cloud import ProviderConfig
        return create_adapter(ProviderConfig(
            name="controlled", adapter="openai", model="m1",
            api_key_keyring_id="kid", base_url="https://cloud.invalid/v1"))

    def _error_case(self, body: bytes, should_stop, during=None) -> dict:
        """send() against an HTTP error (a 503) whose body is ``body``: what
        the call raised, and how much of the body it read and whether it
        closed the reply, read while the error is still held."""
        import urllib.error
        from intergen.interfaces.types import Message, MessageRole
        replies: list[_LongReply] = []

        def urlopen(req, timeout=None):
            replies.append(_LongReply(body, during))
            raise urllib.error.HTTPError(req.full_url, 503, "controlled",
                                         None, replies[-1])

        with mock.patch("urllib.request.urlopen", urlopen), \
                self.assertRaises(Exception) as raised:
            self._provider().send(
                [Message(role=MessageRole.USER, content="hi")],
                should_stop=should_stop)
        return {
            "the call ended as": type(raised.exception).__name__,
            "its text": str(raised.exception),
            "requests opened": len(replies),
            "bytes read": replies[0].sent if replies else None,
            "the reply closed": replies[0].closed if replies else None,
        }

    @staticmethod
    def _past_the_bound() -> tuple[bytes, dict]:
        """An error body far past the bound (about 1 MiB) and what the call
        must come to: the bound and one byte read, the reply closed, the
        first 500 characters kept and the cut said."""
        body = _OVERLOADED + b"try again later. " * 65536
        return body, {
            "the call ended as": "CloudAdapterError",
            "its text": "openai HTTP 503: " + body[:500].decode()
                        + " (the error body is longer than 65536 bytes;"
                          " the rest was not read)",
            "requests opened": 1,
            "bytes read": _ERROR_BODY_BOUND + 1,
            "the reply closed": True,
        }

    def test_an_error_body_past_the_bound_is_read_no_further_without_a_stop(
            self):
        """A caller with no stop (test_connection, the command line): the
        body was read whole in one read; it is now read to the bound."""
        body, want = self._past_the_bound()
        self.assertEqual(self._error_case(body, None), want)

    def test_an_error_body_past_the_bound_is_read_no_further_under_a_stop(
            self):
        """A turn that does not end: the body was read chunk by chunk to its
        end; it is now read to the bound, the stop checked as before."""
        import threading
        body, want = self._past_the_bound()
        self.assertEqual(
            self._error_case(body, threading.Event().is_set), want)

    def test_control_an_error_body_that_ends_at_the_bound_is_not_said_to_be_cut(
            self):
        import threading
        body = _OVERLOADED + b"x" * (_ERROR_BODY_BOUND - len(_OVERLOADED))
        want = {
            "the call ended as": "CloudAdapterError",
            "its text": "openai HTTP 503: " + body[:500].decode(),
            "requests opened": 1,
            "bytes read": _ERROR_BODY_BOUND,
            "the reply closed": True,
        }
        self.assertEqual({
            "no stop": self._error_case(body, None),
            "a turn that does not end": self._error_case(
                body, threading.Event().is_set),
        }, {"no stop": want, "a turn that does not end": want})

    STOPPED = {
        "the call ended as": "CloudCallStopped",
        "its text": "openai: the turn this call belonged to has ended",
        "requests opened": 1,
        "the reply closed": True,
    }

    def test_a_turn_ending_in_the_read_that_finds_the_end_stops_the_call(
            self):
        """The turn ends while the read that finds the end of the error's
        body is in flight. The stop was checked only before each read, so the
        body was handed back and the call came back as a provider error after
        its turn had ended; it now stops, the reply closed."""
        import threading
        stop = threading.Event()

        def ends_in_the_last_read(reply, piece):
            if not piece:
                stop.set()

        body = _OVERLOADED + b'rate limited"}}'
        self.assertEqual(
            self._error_case(body, stop.is_set, ends_in_the_last_read),
            {**self.STOPPED, "bytes read": len(body)})

    def test_control_a_turn_ending_in_the_read_at_the_bound_stops_the_call(
            self):
        """The turn ends while the read that reaches the bound is in flight:
        the call stops, the reply closed, as at the end of a body. (How much
        is read is the other cases' to pin: before the bound there was none,
        and the turn's stop was read before the next read.)"""
        import threading
        stop = threading.Event()

        def ends_past_the_bound(reply, piece):
            if reply.sent > _ERROR_BODY_BOUND:
                stop.set()

        body, _ = self._past_the_bound()
        seen = self._error_case(body, stop.is_set, ends_past_the_bound)
        del seen["bytes read"]
        self.assertEqual(seen, self.STOPPED)

    def test_control_a_successful_answer_past_the_bound_is_read_whole(self):
        import json
        import threading
        from intergen.interfaces.types import Message, MessageRole
        answer = "Clear skies tomorrow, with a light breeze. " * 30000
        body = json.dumps({
            "choices": [{"message": {"content": answer}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 9},
        }).encode()
        replies: list[_LongReply] = []

        def urlopen(req, timeout=None):
            replies.append(_LongReply(body))
            return replies[-1]

        seen = {}
        with mock.patch("urllib.request.urlopen", urlopen):
            for label, stop in (("no stop", None), ("a turn that does not end",
                                                    threading.Event().is_set)):
                reply = self._provider().send(
                    [Message(role=MessageRole.USER, content="hi")],
                    should_stop=stop)
                seen[label] = {
                    "the answer whole": reply.text == answer,
                    "bytes read": replies[-1].sent,
                    "the reply closed": replies[-1].closed,
                }
        want = {"the answer whole": True, "bytes read": len(body),
                "the reply closed": True}
        self.assertEqual(seen, {"no stop": want,
                                "a turn that does not end": want})


class _ChatServerThatStopsListening(_ChatServer):
    """The chat server answers the turn's draft and then stops listening:
    every later request is refused, so a regeneration's chat() escalates to
    the registered cloud provider."""

    def urlopen(self, req, timeout=None):
        import json
        if self.requests:
            self.requests.append(json.loads(req.data)["messages"])
            raise _refused()
        return super().urlopen(req, timeout)


class AnEndedTurnsCloudRegenerationStopsWithinOneItem(unittest.TestCase):
    """The same stop through the web turn: the claim screen asks the model to
    write the answer again, the chat server has stopped listening, and the
    regeneration's chat() escalates to the registered provider on the worker
    thread the turn handed its context. Each door ends the turn while the
    provider is reading its reply. The control: a turn that is not ended gets
    the cloud's regenerated answer on the page."""

    DRAFT = "Done! I have installed the package."
    maxDiff = None

    def _turn(self):
        from intergen.interfaces.types import RouteResult
        from intergen.llm import LLMRouter
        chat = _ChatServerThatStopsListening(self.DRAFT, REGENERATED)
        patcher = mock.patch("urllib.request.urlopen", chat.urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(chat.finish)
        cloud = _PacedCloud(REGENERATED)
        self.addCleanup(cloud.finish.set)
        llm = LLMRouter({"endpoint": _LOCAL, "escalation_mode": "auto",
                         "request_timeout": 5})
        llm.register_cloud_provider("controlled", cloud)
        router = _model_router(llm)
        server = WebServer(router=router, llm=llm, tools=None, governance=None)
        server._sessions = mock.MagicMock()
        server._send_session_list = mock.AsyncMock()
        ctx = ConnectionContext(client_id="case", source_interface="web",
                                ws=_RecordingWS(),
                                conversation=router.new_conversation())

        async def streamed_turn(c, data):
            await server._stream_llm_response(
                c, "old-turn", data["content"],
                RouteResult(text="", source="llm_freeform", handled=False))
        server._handle_client_message = streamed_turn

        def start() -> asyncio.Task:
            task = asyncio.create_task(server._run_turn(
                ctx, {"type": "message", "content": "is the package installed?"}))
            ctx.turn_task = task
            return task
        return chat, cloud, server, ctx, start

    async def _escalated(self, cloud: _PacedCloud) -> None:
        """Wait until the regeneration's cloud call has read one item."""
        for _ in range(500):
            if cloud.entered.is_set():
                break
            await asyncio.sleep(0.01)
        else:
            self.fail("the regeneration never reached the cloud provider")
        cloud.allow.release()
        for _ in range(500):
            if cloud.produced >= 1:
                return
            await asyncio.sleep(0.01)
        self.fail("the cloud provider never read its first item")

    def test_control_the_clouds_regeneration_reaches_the_page(self):
        chat, cloud, server, ctx, start = self._turn()

        async def scenario():
            try:
                task = start()
                await self._escalated(cloud)
                for _ in cloud.words[1:]:
                    cloud.allow.release()
                await asyncio.wait_for(task, 10)
            finally:
                chat.finish()
                cloud.finish.set()
        asyncio.run(scenario())
        self.assertIn("You did NOT run", chat.requests[1][-1]["content"])
        self.assertTrue(cloud.answered)
        ends = [f for f in ctx.ws.sent if f.get("type") == "stream_end"]
        self.assertEqual(len(ends), 1, _frames(ctx.ws))
        self.assertEqual(ends[0].get("full_response", "").strip(), REGENERATED)

    def _ended_by(self, door: str) -> None:
        chat, cloud, server, ctx, start = self._turn()

        async def scenario():
            try:
                task = start()
                await self._escalated(cloud)
                at_end = cloud.produced
                opened = (len(chat.requests), cloud.calls)
                if door == "/clear":
                    await server._handle_slash_command(
                        ctx, {"command": "/clear"})
                else:
                    await server._handle_new_session(ctx, {})
                await asyncio.gather(task, return_exceptions=True)
                self.assertTrue(task.cancelled(), "the turn was not ended")
                for _ in cloud.words[1:]:
                    cloud.allow.release()
                done = await asyncio.to_thread(cloud.done.wait, 2.0)
                await asyncio.sleep(0.3)
                return (cloud.produced - at_end, done,
                        (len(chat.requests), cloud.calls), opened)
            finally:
                chat.finish()
                cloud.finish.set()
        after, done, now, opened = asyncio.run(scenario())
        self.assertEqual({
            "items read after the turn ended, at most one": after <= 1,
            "the provider saw the turn's stop": cloud.stopped,
            "the provider handed an answer back": cloud.answered,
            "the call over within 2 s": done,
            "requests opened after the end (local, cloud)": tuple(
                n - o for n, o in zip(now, opened)),
        }, {
            "items read after the turn ended, at most one": True,
            "the provider saw the turn's stop": True,
            "the provider handed an answer back": False,
            "the call over within 2 s": True,
            "requests opened after the end (local, cloud)": (0, 0),
        }, f"{door}: {after} item(s) were read after the turn ended")

    # One case per door, never subtests: each verdict is its own line.
    def test_clear_stops_the_ended_turns_cloud_regeneration_within_one_item(
            self):
        self._ended_by("/clear")

    def test_a_new_session_stops_the_ended_turns_cloud_regeneration(self):
        self._ended_by("/new")


# ---- the frontier request ----------------------------------------------------

FRONTIER_QUESTION = "what will the weather be tomorrow?"


def _text(frame: dict):
    """One frame from the page, as the receive loop reads it."""
    import json
    import types
    from aiohttp import WSMsgType
    return types.SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps(frame))


class _FrontierPage(_RecordingWS):
    """The page as the real receive loop (_dispatch_loop) reads it: it asks
    the frontier model, then sends one more frame (a door, or a chat message)
    once the case lets it. The page stays until the case lets it leave."""

    def __init__(self, then: dict | None, first: dict | None = None) -> None:
        super().__init__()
        self.first = first or {"type": "frontier_escalate",
                               "content": FRONTIER_QUESTION}
        self.then = then
        self.send_then = asyncio.Event()
        self.leave = asyncio.Event()
        self._step = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        self._step += 1
        if self._step == 1:
            return _text(self.first)
        if self._step == 2 and self.then is not None:
            await self.send_then.wait()
            return _text(self.then)
        await self.leave.wait()
        raise StopAsyncIteration


def _watched_manager(cloud):
    """The real escalation manager over ``cloud``, recording what each
    escalate() returned and when it had returned."""
    import threading
    from intergen.escalation import EscalationManager
    from intergen.interfaces.cloud import ProviderConfig

    class _Watched(EscalationManager):
        def __init__(self) -> None:
            super().__init__(
                providers=[ProviderConfig(
                    name="frontier", adapter="custom", model="example-large",
                    api_key_keyring_id="none")],
                adapter_factory=lambda config: cloud)
            self.returned = threading.Event()
            self.results: list = []

        def escalate(self, *args, **kwargs):
            try:
                result = super().escalate(*args, **kwargs)
                self.results.append(result)
                return result
            finally:
                self.returned.set()

    return _Watched()


class AConsentedFrontierRequestStopsWithItsConversation(unittest.TestCase):
    """The person asks their frontier model from the page and approves the
    send; while the provider is reading its reply, the conversation is
    cleared, or a new one is started. The request was awaited inside the
    receive loop, so neither door could even be read until the call had run
    to its end. It now runs in the turn's slot with a stop bound for its
    frame: each door ends it, the stop reaches the provider through the
    cloud-provider contract and the call stops within one item, and its
    answer reaches neither the page nor any conversation. The frames come
    through the real receive loop. The control: a request nobody ends is
    answered on the page and kept in the conversation."""

    maxDiff = None

    def _server(self, cloud, consent=None):
        from intergen.llm import LLMRouter
        if consent is None:
            def consent(*_args, **_kwargs):
                return True
        patcher = mock.patch("intergen.consent_modal.prompt_send_consent",
                             side_effect=consent)
        patcher.start()
        self.addCleanup(patcher.stop)
        llm = LLMRouter({"endpoint": _LOCAL, "request_timeout": 1})
        router = _model_router(llm)
        router._escalation = _watched_manager(cloud)
        server = WebServer(router=router, llm=llm, tools=None, governance=None)
        server._sessions = mock.MagicMock()
        self.saved: list[list[str]] = []
        server._sessions.save.side_effect = (
            lambda _sid, history, **_kw: self.saved.append(
                [m.content for m in history]))
        server._send_session_list = mock.AsyncMock()
        return server, router

    async def _until(self, predicate, what: str, seconds: float = 5.0) -> None:
        for _ in range(int(seconds * 100)):
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail(what)

    @staticmethod
    async def _sent_within(page, predicate, seconds: float) -> bool:
        """Whether the server sent the page a frame ``predicate`` accepts
        within ``seconds``."""
        for _ in range(int(seconds * 100)):
            if any(predicate(frame) for frame in page.sent):
                return True
            await asyncio.sleep(0.01)
        return False

    def _kept(self, ctx) -> bool:
        """The answer in the live conversation, or in a transcript saved."""
        return (any(m.content == CLOUD_ANSWER for m in ctx.session_history)
                or any(CLOUD_ANSWER in saved for saved in self.saved))

    @staticmethod
    def _answered_on_page(page) -> bool:
        return any(f.get("type") == "frontier_response" and f.get("answered")
                   for f in page.sent)

    def _run(self, page_then, scenario_body, cloud, consent=None, first=None):
        server, router = self._server(cloud, consent)

        async def scenario():
            page = _FrontierPage(page_then, first)
            ctx = ConnectionContext(client_id="case", source_interface="web",
                                    ws=page,
                                    conversation=router.new_conversation())
            loop_task = asyncio.create_task(server._dispatch_loop(ctx))
            try:
                return await scenario_body(server, router, ctx, page,
                                           loop_task)
            finally:
                cloud.finish.set()
                page.send_then.set()
                page.leave.set()
                await asyncio.gather(loop_task, return_exceptions=True)
                if ctx.turn_task is not None:
                    await asyncio.gather(ctx.turn_task,
                                         return_exceptions=True)
        return asyncio.run(scenario())

    def test_control_a_request_nobody_ends_is_answered_and_kept(self):
        cloud = _PacedCloud(CLOUD_ANSWER)

        async def body(server, router, ctx, page, loop_task):
            await self._until(cloud.entered.is_set,
                              "the frontier provider was never asked")
            for _ in cloud.words:
                cloud.allow.release()
            await self._until(lambda: self._answered_on_page(page),
                              "no answer reached the page")
            return {
                "the provider handed an answer back": cloud.answered,
                "the provider saw a stop": cloud.stopped,
                "an answer reached the page": self._answered_on_page(page),
                "the answer kept in the conversation": self._kept(ctx),
            }

        self.assertEqual(self._run(None, body, cloud), {
            "the provider handed an answer back": True,
            "the provider saw a stop": False,
            "an answer reached the page": True,
            "the answer kept in the conversation": True,
        })

    def _ended_by(self, door: dict, door_frame: str) -> dict:
        cloud = _PacedCloud(CLOUD_ANSWER)

        async def body(server, router, ctx, page, loop_task):
            manager = router._escalation
            await self._until(cloud.entered.is_set,
                              "the frontier provider was never asked")
            cloud.allow.release()
            await self._until(lambda: cloud.produced >= 1,
                              "the frontier provider never read an item")
            at_door = cloud.produced
            page.send_then.set()
            took_effect = (await self._sent_within(
                page, lambda f: f.get("type") == door_frame, 1.0)
                and not cloud.done.is_set())
            for _ in cloud.words[1:]:
                cloud.allow.release()
            await asyncio.to_thread(manager.returned.wait, 2.0)
            await self._until(lambda: door_frame in _frames(page),
                              f"the door's {door_frame} never came")
            return {
                "the door took effect while the call ran": took_effect,
                "items read after the door, at most one":
                    cloud.produced - at_door <= 1,
                "the provider saw the stop": cloud.stopped,
                "the provider handed an answer back": cloud.answered,
                "the escalation came back as not sent": [
                    type(r).__name__ for r in manager.results],
                "an answer reached the page": self._answered_on_page(page),
                "the answer kept in a conversation": self._kept(ctx),
            }

        return self._run(door, body, cloud)

    ENDED = {
        "the door took effect while the call ran": True,
        "items read after the door, at most one": True,
        "the provider saw the stop": True,
        "the provider handed an answer back": False,
        "the escalation came back as not sent": ["EscalationNotSent"],
        "an answer reached the page": False,
        "the answer kept in a conversation": False,
    }

    # One case per door, never subtests: each verdict is its own line.
    def test_clear_stops_the_frontier_call_within_one_item(self):
        self.assertEqual(self._ended_by(
            {"type": "slash_command", "command": "/clear"}, "buffer_cleared"),
            self.ENDED)

    def test_a_new_conversation_stops_the_frontier_call_within_one_item(self):
        self.assertEqual(self._ended_by(
            {"type": "new_session"}, "session_created"), self.ENDED)

    def test_a_door_while_the_consent_dialog_is_open_sends_nothing(self):
        """The door comes while the person is still reading the consent
        dialog; they press Send after it. Nothing reaches the provider."""
        import threading
        cloud = _PacedCloud(CLOUD_ANSWER)
        asked = threading.Event()
        send_pressed = threading.Event()

        def consent(*_args, **_kwargs):
            asked.set()
            return send_pressed.wait(5)

        async def body(server, router, ctx, page, loop_task):
            manager = router._escalation
            await self._until(asked.is_set, "the consent dialog never opened")
            page.send_then.set()
            took_effect = await self._sent_within(
                page, lambda f: f.get("type") == "buffer_cleared", 1.0)
            send_pressed.set()
            for _ in cloud.words:
                cloud.allow.release()
            await asyncio.to_thread(manager.returned.wait, 2.0)
            await self._until(lambda: "buffer_cleared" in _frames(page),
                              "the door's buffer_cleared never came")
            return {
                "the door took effect while the dialog was open":
                    took_effect,
                "the provider was asked": cloud.calls,
                "the escalation came back as not sent": [
                    type(r).__name__ for r in manager.results],
                "an answer reached the page": self._answered_on_page(page),
            }

        self.assertEqual(self._run(
            {"type": "slash_command", "command": "/clear"}, body, cloud,
            consent), {
            "the door took effect while the dialog was open": True,
            "the provider was asked": 0,
            "the escalation came back as not sent": ["EscalationNotSent"],
            "an answer reached the page": False,
        })

    def test_a_message_while_the_frontier_call_runs_is_told_busy(self):
        """The request holds the turn's slot, as a turn does: a chat message
        sent while it runs is told the assistant is busy and starts nothing
        beside it, and the request itself goes on to its answer."""
        cloud = _PacedCloud(CLOUD_ANSWER)
        turns: list = []

        async def body(server, router, ctx, page, loop_task):
            async def recorded_turn(c, data):
                turns.append(data.get("content"))
            server._handle_client_message = recorded_turn
            await self._until(cloud.entered.is_set,
                              "the frontier provider was never asked")
            page.send_then.set()
            told_busy = (await self._sent_within(
                page, lambda f: f.get("type") == "error"
                and f.get("code") == "busy", 1.0)
                and not cloud.done.is_set())
            for _ in cloud.words:
                cloud.allow.release()
            await self._until(lambda: self._answered_on_page(page),
                              "no answer reached the page")
            await asyncio.sleep(0.2)
            return {
                "the message was told busy while the call ran": told_busy,
                "a turn ran for the message": turns,
                "an answer reached the page": self._answered_on_page(page),
                "the answer kept in the conversation": self._kept(ctx),
            }

        self.assertEqual(self._run(
            {"type": "message", "content": "and the day after?"}, body,
            cloud), {
            "the message was told busy while the call ran": True,
            "a turn ran for the message": [],
            "an answer reached the page": True,
            "the answer kept in the conversation": True,
        })


    def test_a_frontier_request_while_a_turn_runs_is_told_busy(self):
        """The other side of the same slot: a frontier request that arrives
        while a turn runs is told the assistant is busy, asks no provider,
        and leaves the turn as the work every door ends."""
        cloud = _PacedCloud(CLOUD_ANSWER)

        async def body(server, router, ctx, page, loop_task):
            started = asyncio.Event()
            release = asyncio.Event()

            async def held_turn(c, data):
                started.set()
                await release.wait()
            server._handle_client_message = held_turn
            try:
                await self._until(started.is_set, "the turn never started")
                turn = ctx.turn_task
                page.send_then.set()
                told_busy = await self._sent_within(
                    page, lambda f: f.get("type") == "error"
                    and f.get("code") == "busy", 1.0)
                for _ in cloud.words:
                    cloud.allow.release()
                if cloud.entered.is_set():
                    await asyncio.to_thread(cloud.done.wait, 2.0)
                return {
                    "the frontier request was told busy": told_busy,
                    "the frontier provider was asked": cloud.calls,
                    "the turn still holds the slot": ctx.turn_task is turn,
                }
            finally:
                release.set()

        self.assertEqual(self._run(
            {"type": "frontier_escalate", "content": FRONTIER_QUESTION},
            body, cloud, first={"type": "message", "content": "hello"}), {
            "the frontier request was told busy": True,
            "the frontier provider was asked": 0,
            "the turn still holds the slot": True,
        })

    def test_control_a_frontier_request_that_fails_is_reported_to_the_page(
            self):
        """Off the receive loop the request owns its errors, as the loop did
        for it: a failure is logged and the page is told, and the page is
        still served afterwards."""
        cloud = _PacedCloud(CLOUD_ANSWER)

        async def body(server, router, ctx, page, loop_task):
            async def failing(c, data):
                raise RuntimeError("the frontier request failed")
            server._handle_frontier_escalate = failing
            reported = await self._sent_within(
                page, lambda f: f.get("type") == "error"
                and f.get("code") == "internal_error", 2.0)
            page.send_then.set()
            served = await self._sent_within(
                page, lambda f: f.get("type") == "buffer_cleared", 2.0)
            return {
                "the failure reported to the page": reported,
                "the page served afterwards": served,
                "the provider was asked": cloud.calls,
            }

        self.assertEqual(self._run(
            {"type": "slash_command", "command": "/clear"}, body, cloud), {
            "the failure reported to the page": True,
            "the page served afterwards": True,
            "the provider was asked": 0,
        })


class TheEscalationHandsTheStopToTheProvider(unittest.TestCase):
    """EscalationManager.escalate under a bound stop that is set while the
    provider reads its reply: the provider is handed the stop through the
    cloud-provider contract and stops within one item, and the call comes
    back as not sent, in words that say why, and is not logged as a failure.
    Run on a worker thread whose context binds the stop, as the web
    surface's frontier request runs it."""

    maxDiff = None

    def test_a_call_the_stop_ended_comes_back_as_not_sent(self):
        import contextvars
        import logging
        import threading
        import time
        from intergen.interfaces.types import Message, MessageRole
        from intergen.llm import bind_turn_stop
        cloud = _PacedCloud(CLOUD_ANSWER)
        self.addCleanup(cloud.finish.set)
        manager = _watched_manager(cloud)
        stop = threading.Event()
        context = contextvars.copy_context()
        context.run(bind_turn_stop, stop)
        errors: list[str] = []
        handler = logging.Handler(level=logging.ERROR)
        handler.emit = lambda record: errors.append(record.getMessage())
        log = logging.getLogger("intergen.escalation")
        log.addHandler(handler)
        self.addCleanup(log.removeHandler, handler)
        worker = threading.Thread(target=lambda: context.run(
            manager.escalate,
            [Message(role=MessageRole.USER, content=FRONTIER_QUESTION)],
            reason="case", user_consented=True), daemon=True)
        worker.start()
        self.assertTrue(cloud.entered.wait(5),
                        "the frontier provider was never asked")
        cloud.allow.release()
        for _ in range(500):
            if cloud.produced >= 1:
                break
            time.sleep(0.01)
        self.assertEqual(cloud.produced, 1,
                         "the frontier provider never read an item")
        at_stop = cloud.produced
        stop.set()
        for _ in cloud.words[1:]:
            cloud.allow.release()
        worker.join(5)
        result = manager.results[0] if manager.results else None
        self.assertEqual({
            "items read after the stop, at most one":
                cloud.produced - at_stop <= 1,
            "the provider saw the stop": cloud.stopped,
            "came back as": type(result).__name__,
            "its words say the conversation ended": (
                "the conversation it was asked from ended"
                in getattr(result, "text", "")),
            "an ERROR logged": errors,
        }, {
            "items read after the stop, at most one": True,
            "the provider saw the stop": True,
            "came back as": "EscalationNotSent",
            "its words say the conversation ended": True,
            "an ERROR logged": [],
        })


# The consent dialog's program, of the case's own: it records its pid, reads
# what it was handed, and then (as its mode says) tells its launcher it is on
# screen, or never does, or exits before it can be. It answers when the case
# presses Send or Cancel and exits at once when the case ends, and in any case
# within a minute, so no case leaves it behind. It never draws a window.
_DIALOG_PROGRAM = """import os, sys, time
pids, answer, over, mode = sys.argv[1:5]
with open(pids, "a") as f:
    f.write(str(os.getpid()) + chr(10))
if mode == "cannot-render":
    sys.exit(70)
sys.stdin.read()
if mode == "renders":
    sys.stdout.write(%r + chr(10))
    sys.stdout.flush()
    open(pids + ".rendered", "w").close()
for _ in range(600):
    if os.path.exists(over):
        sys.exit(1)
    if os.path.exists(answer):
        with open(answer) as f:
            sys.exit(int(f.read()))
    time.sleep(0.1)
sys.exit(1)
"""


def _running(pid: int) -> bool:
    """Whether the process is still running: one that has exited, reaped or
    not, is not. One reaped between the open and the read of its stat is
    gone too: that read fails with ProcessLookupError."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


class _Dialogs:
    """The two dialog programs of one case: the branded one, run by the
    real launcher (consent_dialog._run_dialog) in ``mode``, and the fallback
    in zenity's place. Both record their pids in one file."""

    def __init__(self, case: unittest.TestCase, mode: str) -> None:
        import shlex
        import sys
        import tempfile
        from pathlib import Path
        from intergen import consent_dialog_proto
        tmp = tempfile.TemporaryDirectory(prefix="consent-withdrawn-")
        case.addCleanup(tmp.cleanup)
        here = Path(tmp.name)
        self.pids = here / "pids"
        self.answer = here / "answer"
        self.over = here / "over"
        program = here / "dialog.py"
        program.write_text(
            _DIALOG_PROGRAM % consent_dialog_proto.RENDERED_MARKER)
        args = [str(program), str(self.pids), str(self.answer), str(self.over)]
        self.branded = [sys.executable, *args, mode]
        self.fallback = str(here / "zenity")
        Path(self.fallback).write_text("#!/bin/sh\nexec " + shlex.join(
            [sys.executable, *args, "fallback"]) + "\n")
        Path(self.fallback).chmod(0o755)
        case.addCleanup(self.end)

    def started(self) -> list[int]:
        if not self.pids.exists():
            return []
        return [int(line) for line in self.pids.read_text().split()]

    def running(self) -> list[int]:
        return [pid for pid in self.started() if _running(pid)]

    def rendered(self) -> bool:
        return self.pids.with_name("pids.rendered").exists()

    def press(self, code: int) -> None:
        """The person answers: Send or Cancel, as the dialog's exit status."""
        import os
        pending = self.answer.with_name("answer.pending")
        pending.write_text(str(code))
        os.replace(pending, self.answer)

    def end(self) -> None:
        """Every dialog program exits; one still running after two seconds
        is killed."""
        import os
        import signal
        import time
        self.over.touch()
        for _ in range(200):
            if not self.running():
                return
            time.sleep(0.01)
        for pid in self.running():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


class AConsentDialogIsWithdrawnWithItsRequest(unittest.TestCase):
    """The person asks their frontier model from the page and the consent
    dialog opens; before they answer it, the conversation is cleared, or a
    new one is started. The door ends the request, and whatever the person
    answers nothing reaches the provider, but the dialog stayed open: a
    question its conversation no longer has, asked until the person
    answered it or its deadline passed an hour later. The dialog is now
    handed the frame's stop and withdrawn when it is set: its program is
    killed, the consent is counted as none and recorded as withdrawn, and no
    fallback dialog opens in its place.

    The consent step is the real one (consent_modal.prompt_send_consent, the
    branded dialog's launcher consent_dialog._run_dialog, and the zenity
    fallback), and the frames come through the real receive loop; only the
    two dialog PROGRAMS are the case's own (_Dialogs). The controls: a
    dialog answered Send is the request's consent, and one answered Cancel
    is the person's decline."""

    maxDiff = None

    def _server(self, cloud, dialogs: _Dialogs):
        from intergen import consent_dialog, consent_modal
        from intergen.llm import LLMRouter
        real_prompt = consent_modal.prompt_send_consent
        real_launcher = consent_dialog._run_dialog
        self.asked: list[dict] = []

        def recorded(*args, **kwargs):
            entry = {"answered": "<still asking>",
                     "outcome": kwargs.get("outcome")}
            self.asked.append(entry)
            entry["answered"] = real_prompt(*args, **kwargs)
            return entry["answered"]

        def launcher(spec, cmd=None, **kwargs):
            return real_launcher(spec, cmd=dialogs.branded, **kwargs)

        for patcher in (
                mock.patch("intergen.consent_modal.prompt_send_consent",
                           side_effect=recorded),
                mock.patch.object(consent_modal, "_session_active",
                                  return_value=True),
                mock.patch.object(consent_dialog, "_run_dialog",
                                  side_effect=launcher),
                mock.patch.object(consent_modal.shutil, "which",
                                  side_effect=lambda name: dialogs.fallback
                                  if name == "zenity" else None),
                mock.patch.object(consent_modal, "_prompt_consent_libnotify",
                                  return_value=False)):
            patcher.start()
            self.addCleanup(patcher.stop)
        llm = LLMRouter({"endpoint": _LOCAL, "request_timeout": 1})
        router = _model_router(llm)
        router._escalation = _watched_manager(cloud)
        server = WebServer(router=router, llm=llm, tools=None, governance=None)
        server._sessions = mock.MagicMock()
        self.saved: list[list[str]] = []
        server._sessions.save.side_effect = (
            lambda _sid, history, **_kw: self.saved.append(
                [m.content for m in history]))
        server._send_session_list = mock.AsyncMock()
        return server, router

    async def _within(self, predicate, seconds: float) -> bool:
        for _ in range(int(seconds * 100)):
            if predicate():
                return True
            await asyncio.sleep(0.01)
        return bool(predicate())

    async def _until(self, predicate, what: str, seconds: float = 5.0) -> None:
        if not await self._within(predicate, seconds):
            self.fail(what)

    def _run(self, page_then, scenario_body, cloud, dialogs: _Dialogs):
        server, router = self._server(cloud, dialogs)

        async def scenario():
            page = _FrontierPage(page_then)
            ctx = ConnectionContext(client_id="case", source_interface="web",
                                    ws=page,
                                    conversation=router.new_conversation())
            loop_task = asyncio.create_task(server._dispatch_loop(ctx))
            try:
                return await scenario_body(ctx, page)
            finally:
                dialogs.end()
                cloud.finish.set()
                page.send_then.set()
                page.leave.set()
                await asyncio.gather(loop_task, return_exceptions=True)
                if ctx.turn_task is not None:
                    await asyncio.gather(ctx.turn_task,
                                         return_exceptions=True)
        return asyncio.run(scenario())

    def _consent(self) -> dict:
        asked = self.asked[0] if self.asked else {}
        return {
            "the consent answered": asked.get("answered", "<never asked>"),
            "the consent recorded": list(asked.get("outcome") or []),
        }

    def _ended_by(self, door: dict, door_frame: str, mode: str,
                  open_programs: int) -> dict:
        cloud = _PacedCloud(CLOUD_ANSWER)
        dialogs = _Dialogs(self, mode)

        async def body(ctx, page):
            if mode == "renders":
                await self._until(dialogs.rendered,
                                  "the consent dialog never came on screen")
            await self._until(lambda: len(dialogs.running()) == 1
                              and len(dialogs.started()) == open_programs,
                              "the consent dialog never opened")
            # The launcher has read what it was waiting for.
            await asyncio.sleep(0.3)
            open_at_the_door = (len(dialogs.running()) == 1
                                and self.asked[0]["answered"]
                                == "<still asking>")
            page.send_then.set()
            took_effect = await self._within(
                lambda: door_frame in _frames(page), 1.0)
            withdrawn = await self._within(
                lambda: not dialogs.running(), 2.0)
            # Time for a fallback to open in its place, if one would.
            await asyncio.sleep(0.5)
            return {
                "the dialog open when the door came": open_at_the_door,
                "the door took effect": took_effect,
                "the dialog withdrawn within 2 s of the door": withdrawn,
                "dialog programs started": len(dialogs.started()),
                **self._consent(),
                "the provider was asked": cloud.calls,
                "an answer reached the page": any(
                    f.get("type") == "frontier_response" for f in page.sent),
            }

        return self._run(door, body, cloud, dialogs)

    @staticmethod
    def _withdrawn(programs: int) -> dict:
        return {
            "the dialog open when the door came": True,
            "the door took effect": True,
            "the dialog withdrawn within 2 s of the door": True,
            "dialog programs started": programs,
            "the consent answered": False,
            "the consent recorded": ["withdrawn"],
            "the provider was asked": 0,
            "an answer reached the page": False,
        }

    # One case per door and dialog, never subtests: each verdict is its own
    # line.
    def test_clear_withdraws_the_consent_dialog_on_screen(self):
        self.assertEqual(self._ended_by(
            {"type": "slash_command", "command": "/clear"}, "buffer_cleared",
            "renders", 1), self._withdrawn(1))

    def test_a_new_conversation_withdraws_the_consent_dialog_on_screen(self):
        self.assertEqual(self._ended_by(
            {"type": "new_session"}, "session_created", "renders", 1),
            self._withdrawn(1))

    def test_a_dialog_still_coming_on_screen_is_withdrawn_and_not_replaced(
            self):
        """The branded dialog has not yet said it is on screen when the door
        comes. It is withdrawn too, and the fallback does not open in its
        place: the request it would ask about has ended."""
        self.assertEqual(self._ended_by(
            {"type": "new_session"}, "session_created", "never-renders", 1),
            self._withdrawn(1))

    def test_clear_withdraws_the_fallback_dialog(self):
        """The branded dialog could not open, so the fallback asks; the door
        withdraws it in the same way."""
        self.assertEqual(self._ended_by(
            {"type": "slash_command", "command": "/clear"}, "buffer_cleared",
            "cannot-render", 2), self._withdrawn(2))

    def _answered(self, code: int) -> dict:
        cloud = _PacedCloud(CLOUD_ANSWER)
        dialogs = _Dialogs(self, "renders")

        async def body(ctx, page):
            await self._until(dialogs.rendered,
                              "the consent dialog never came on screen")
            dialogs.press(code)
            await self._until(
                lambda: self.asked[0]["answered"] != "<still asking>",
                "the consent step never took the answer")
            if self.asked[0]["answered"] is True:
                await self._until(cloud.entered.is_set,
                                  "the frontier provider was never asked")
                for _ in cloud.words:
                    cloud.allow.release()
            await self._until(lambda: any(
                f.get("type") == "frontier_response" for f in page.sent),
                "the page was never answered")
            told = [f.get("content") for f in page.sent
                    if f.get("type") == "frontier_response"]
            return {
                "dialog programs started": len(dialogs.started()),
                **self._consent(),
                "the provider was asked": cloud.calls,
                "the page was told": told,
                "the answer kept in the conversation": (
                    any(m.content == CLOUD_ANSWER
                        for m in ctx.session_history)
                    or any(CLOUD_ANSWER in saved for saved in self.saved)),
            }

        return self._run(None, body, cloud, dialogs)

    def test_control_a_send_is_the_requests_consent(self):
        from intergen.consent_dialog_proto import EXIT_CONSENT_SEND
        from intergen.consent_modal import SEND
        self.assertEqual(self._answered(EXIT_CONSENT_SEND), {
            "dialog programs started": 1,
            "the consent answered": True,
            "the consent recorded": [SEND],
            "the provider was asked": 1,
            "the page was told": [CLOUD_ANSWER],
            "the answer kept in the conversation": True,
        })

    def test_control_a_cancel_is_the_persons_decline(self):
        from intergen.consent_dialog_proto import EXIT_DENY
        from intergen.consent_modal import DECLINED, DECLINED_SENTENCE
        self.assertEqual(self._answered(EXIT_DENY), {
            "dialog programs started": 1,
            "the consent answered": False,
            "the consent recorded": [DECLINED],
            "the provider was asked": 0,
            "the page was told": [DECLINED_SENTENCE],
            "the answer kept in the conversation": False,
        })


class TheConsentStepTakesItsCallersStop(unittest.TestCase):
    """prompt_send_consent handed a caller's stop, called as the web
    surface's frontier request calls it: a stop already set opens no dialog,
    and an answer that comes back as the stop is set is not taken. Each
    counts as no consent and is recorded as withdrawn."""

    maxDiff = None

    def _ask(self, should_stop, dialog=None) -> dict:
        """The consent step asked once; what it answered and recorded, and
        which dialogs it opened, counted where each is launched (a program
        killed at once never gets as far as recording its pid)."""
        from intergen import consent_dialog, consent_modal
        dialogs = _Dialogs(self, "renders")
        real_launcher = consent_dialog._run_dialog
        opened: list[str] = []

        def launcher(spec, cmd=None, **kwargs):
            opened.append("branded")
            return real_launcher(spec, cmd=dialogs.branded, **kwargs)

        def which(name):
            if name != "zenity":
                return None
            opened.append("fallback")
            return dialogs.fallback

        patches = [
            mock.patch.object(consent_modal, "_session_active",
                              return_value=True),
            mock.patch.object(consent_dialog, "_run_dialog",
                              side_effect=launcher),
            mock.patch.object(consent_modal.shutil, "which",
                              side_effect=which),
            mock.patch.object(consent_modal, "_prompt_consent_libnotify",
                              return_value=False),
        ]
        if dialog is not None:
            patches.append(mock.patch.object(
                consent_dialog, "run_consent_dialog", side_effect=dialog))
        outcome: list = []
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        try:
            answered = consent_modal.prompt_send_consent(
                FRONTIER_QUESTION, "frontier", outcome=outcome,
                should_stop=should_stop)
        except TypeError as exc:  # read by the case: a caller's stop refused
            answered = f"<raised {type(exc).__name__}>"
        return {
            "the consent answered": answered,
            "the consent recorded": outcome,
            "dialogs opened": opened,
        }

    def test_a_stop_already_set_opens_no_dialog(self):
        self.assertEqual(self._ask(lambda: True), {
            "the consent answered": False,
            "the consent recorded": ["withdrawn"],
            "dialogs opened": [],
        })

    def test_a_send_given_as_the_stop_is_set_is_not_taken(self):
        import threading
        stop = threading.Event()

        def send_pressed_as_the_door_came(*_args, **_kwargs):
            stop.set()
            return True

        self.assertEqual(self._ask(stop.is_set, send_pressed_as_the_door_came),
                         {
            "the consent answered": False,
            "the consent recorded": ["withdrawn"],
            "dialogs opened": [],
        })

    def test_control_a_caller_that_hands_no_stop_calls_as_before(self):
        """The command line, the bus and the review dialog hand no stop: the
        consent step makes exactly the calls it made before the stop, so a
        launcher or a fallback written for those calls still answers."""
        from intergen import consent_dialog, consent_modal
        from intergen.consent_dialog_proto import EXIT_CONSENT_SEND

        def ask(branded_answer) -> dict:
            calls: list[str] = []

            def the_launcher_before_the_stop(spec):
                calls.append("branded")
                return branded_answer

            def the_fallback_before_the_stop(content, provider, reason):
                calls.append("fallback")
                return True

            outcome: list = []
            with mock.patch.object(consent_modal, "_session_active",
                                   return_value=True), \
                    mock.patch.object(consent_dialog, "_run_dialog",
                                      side_effect=the_launcher_before_the_stop), \
                    mock.patch.object(consent_modal, "_prompt_consent_zenity",
                                      side_effect=the_fallback_before_the_stop):
                try:
                    answered = consent_modal.prompt_send_consent(
                        FRONTIER_QUESTION, "frontier", outcome=outcome)
                except TypeError as exc:  # read by the case
                    answered = f"<raised {type(exc).__name__}>"
            return {"answered": answered, "recorded": outcome,
                    "asked": calls}

        self.assertEqual({
            "through the branded dialog": ask(EXIT_CONSENT_SEND),
            "through the fallback": ask(None),
        }, {
            "through the branded dialog": {
                "answered": True, "recorded": ["send"], "asked": ["branded"]},
            "through the fallback": {
                "answered": True, "recorded": ["send"],
                "asked": ["branded", "fallback"]},
        })



# The dialog programs a teardown cannot end, for the cases below: each is the
# case's own program run as before, with only its Popen's kill made to fail.
def _kill_refused_popen():
    import subprocess

    class KillRefused(subprocess.Popen):
        """A dialog program whose kill is refused: kill() raises and the
        program keeps running."""

        def kill(self):
            raise PermissionError(1, "the kill was refused")

    return KillRefused


def _never_reaped_popen():
    import subprocess

    class NeverReaped(subprocess.Popen):
        """A dialog program the kill does not end (as one stuck in the
        kernel): kill() is taken, the program keeps running, and the reap
        that follows times out."""

        def kill(self):
            self.kill_taken = True

    return NeverReaped


class ADialogThatCannotBeTakenDownIsNotRecordedClosed(unittest.TestCase):
    """A consent dialog the consent step has to take down - withdrawn when its
    request ends, unanswered at its deadline, or never on screen - was killed
    with every failure swallowed: a kill that raised, or a reap that timed out,
    was recorded as a clean withdrawal (or a deadline, or a fallback opened
    beside it) with the dialog still on screen. Each teardown is now verified
    (a bounded reap and a poll); one that fails is logged with the program's
    pid and the reason and recorded as NOT_TAKEN_DOWN: no consent, nothing
    sent, no fallback opened beside it. The same for the fallback dialog's
    teardowns, which waited on the program without a bound.

    The consent step is the real one (prompt_send_consent, the branded
    launcher, the fallback's runner); the dialog programs are the case's own
    (_Dialogs), and only their Popen's kill is made to fail. Each case reads
    what the consent step answered and recorded, which dialogs it opened,
    whether the program it had to take down still runs, and whether an ERROR
    names that program's pid. The review gate's dialog, which shares the
    launcher, is a failed review when its teardown fails, never a timed-out
    one. A caller's stop that raised while the branded dialog was on screen
    left it untried: the error went on to the caller and the dialog stayed on
    screen. It is now taken down first, and a teardown that fails is
    not-taken-down, the error its cause. The control: a kill refused for a
    program that had already ended is no failure."""

    maxDiff = None

    def _ask(self, mode: str, branded_popen=None, fallback_popen=None,
             stop_after_render: bool = False, stop_after_fallback: bool = False,
             with_stop: bool = True, deadline: float | None = None,
             render_timeout: float | None = None,
             stop_raises_after_render: bool = False) -> dict:
        import logging
        import subprocess
        import threading
        import time
        import types
        from intergen import consent_dialog, consent_dialog_proto, consent_modal
        dialogs = _Dialogs(self, mode)
        real_launcher = consent_dialog._run_dialog
        opened: list[str] = []

        def launcher(spec, cmd=None, **kwargs):
            opened.append("branded")
            return real_launcher(spec, cmd=dialogs.branded, **kwargs)

        def which(name):
            if name != "zenity":
                return None
            opened.append("fallback")
            return dialogs.fallback

        def with_popen(popen):
            return types.SimpleNamespace(
                Popen=popen, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL,
                TimeoutExpired=subprocess.TimeoutExpired,
                CompletedProcess=subprocess.CompletedProcess,
                run=subprocess.run)

        patches = [
            mock.patch.object(consent_modal, "_session_active",
                              return_value=True),
            mock.patch.object(consent_dialog, "_run_dialog",
                              side_effect=launcher),
            mock.patch.object(consent_modal.shutil, "which", side_effect=which),
            mock.patch.object(consent_modal, "_prompt_consent_libnotify",
                              return_value=False),
            mock.patch.object(consent_dialog, "KILL_REAP_SECONDS", 0.3,
                              create=True),
        ]
        if branded_popen is not None:
            patches.append(mock.patch.object(
                consent_dialog, "subprocess", with_popen(branded_popen)))
        if fallback_popen is not None:
            # The fallback's runner is consent_dialog.run_bounded; before it
            # moved there it was consent_modal's own, so both are given the
            # Popen (the branded program, which cannot render here, has ended
            # before its teardown, which is then no failure).
            patches.append(mock.patch.object(
                consent_modal, "subprocess", with_popen(fallback_popen)))
            patches.append(mock.patch.object(
                consent_dialog, "subprocess", with_popen(fallback_popen)))
        if deadline is not None:
            patches.append(mock.patch.object(
                consent_dialog_proto, "POST_RENDER_DEADLINE_SECONDS", deadline))
        if render_timeout is not None:
            patches.append(mock.patch.object(
                consent_dialog_proto, "PRE_RENDER_TIMEOUT_SECONDS",
                render_timeout))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        errors: list[str] = []
        handler = logging.Handler(level=logging.ERROR)
        handler.emit = lambda record: errors.append(record.getMessage())
        log = logging.getLogger("intergen.consent_dialog")
        log.addHandler(handler)
        self.addCleanup(log.removeHandler, handler)
        stop = threading.Event()
        outcome: list = []
        out: dict = {}

        def raising_stop() -> bool:
            if dialogs.rendered():
                raise RuntimeError("the stop could not be read")
            return False

        def ask() -> None:
            try:
                out["answered"] = consent_modal.prompt_send_consent(
                    FRONTIER_QUESTION, "frontier", outcome=outcome,
                    should_stop=(raising_stop if stop_raises_after_render
                                 else stop.is_set) if with_stop else None)
            except BaseException as exc:  # read by the case
                out["answered"] = f"<raised {type(exc).__name__}>"

        thread = threading.Thread(target=ask, name="consent", daemon=True)
        thread.start()
        # Cleanups run last-registered first: the dialog programs end and the
        # consent thread is joined BEFORE the stand-ins above are removed, so
        # a thread still waiting on a program (the red runs at the base) can
        # never reach the real notification or a real dialog after its cell.
        self.addCleanup(thread.join, 10)
        self.addCleanup(dialogs.end)
        if stop_after_render:
            for _ in range(500):
                if dialogs.rendered():
                    break
                time.sleep(0.01)
            stop.set()
        if stop_after_fallback:
            for _ in range(500):
                if len(dialogs.running()) == 1 and len(dialogs.started()) == 2:
                    break
                time.sleep(0.01)
            time.sleep(0.3)
            stop.set()
        thread.join(6)
        pids = dialogs.started()
        last = pids[-1] if pids else None
        return {
            "the consent answered": out.get("answered", "<still asking>"),
            "the consent recorded": list(outcome),
            "dialogs opened": opened,
            "the program to take down still running": bool(
                last is not None and _running(last)),
            "an ERROR names its pid": any(
                f"could not take down the dialog (pid {last})" in m
                for m in errors),
        }

    @staticmethod
    def _not_taken_down(opened: list[str]) -> dict:
        return {
            "the consent answered": False,
            "the consent recorded": ["not-taken-down"],
            "dialogs opened": opened,
            "the program to take down still running": True,
            "an ERROR names its pid": True,
        }

    def test_a_withdrawn_dialog_whose_kill_is_refused_is_not_recorded_withdrawn(
            self):
        self.assertEqual(self._ask(
            "renders", branded_popen=_kill_refused_popen(),
            stop_after_render=True), self._not_taken_down(["branded"]))

    def test_a_withdrawn_dialog_that_is_never_reaped_is_not_recorded_withdrawn(
            self):
        self.assertEqual(self._ask(
            "renders", branded_popen=_never_reaped_popen(),
            stop_after_render=True), self._not_taken_down(["branded"]))

    def test_a_stop_that_raises_when_the_branded_dialogs_teardown_fails(self):
        """The caller's stop raises while the branded dialog is on screen and
        its kill is refused: not verified closed, so recorded not taken down,
        nothing sent - never the stop's error alone with the dialog left on
        screen untried."""
        self.assertEqual(self._ask(
            "renders", branded_popen=_kill_refused_popen(),
            stop_raises_after_render=True), self._not_taken_down(["branded"]))

    def test_a_stop_that_raises_takes_the_branded_dialog_down(self):
        """The caller's stop raises while the branded dialog is on screen: the
        error goes on to the caller, and the dialog is taken down first, as
        the fallback's runner takes its program down on any error."""
        self.assertEqual(self._ask("renders", stop_raises_after_render=True), {
            "the consent answered": "<raised RuntimeError>",
            "the consent recorded": [],
            "dialogs opened": ["branded"],
            "the program to take down still running": False,
            "an ERROR names its pid": False,
        })

    def test_a_dialog_never_on_screen_that_cannot_be_taken_down_opens_no_fallback(
            self):
        """The branded dialog never says it is on screen and its kill is
        refused: no fallback opens beside it."""
        self.assertEqual(self._ask(
            "never-renders", branded_popen=_kill_refused_popen(),
            with_stop=False, render_timeout=0.5, deadline=1.0),
            self._not_taken_down(["branded"]))

    def test_a_dialog_unanswered_at_its_deadline_that_cannot_be_taken_down(
            self):
        """Nobody answers before the deadline and the kill is refused: not
        recorded as the person's decline."""
        self.assertEqual(self._ask(
            "renders", branded_popen=_kill_refused_popen(), with_stop=False,
            deadline=1.0), self._not_taken_down(["branded"]))

    def test_a_withdrawn_fallback_dialog_whose_kill_is_refused(self):
        """The branded dialog cannot open, so the fallback asks; its request
        ends and its kill is refused."""
        self.assertEqual(self._ask(
            "cannot-render", fallback_popen=_kill_refused_popen(),
            stop_after_fallback=True),
            self._not_taken_down(["branded", "fallback"]))

    def test_a_withdrawn_fallback_dialog_that_is_never_reaped(self):
        self.assertEqual(self._ask(
            "cannot-render", fallback_popen=_never_reaped_popen(),
            stop_after_fallback=True),
            self._not_taken_down(["branded", "fallback"]))

    def test_a_fallback_dialog_unanswered_at_its_deadline_that_cannot_be_taken_down(
            self):
        self.assertEqual(self._ask(
            "cannot-render", fallback_popen=_kill_refused_popen(),
            deadline=1.0), self._not_taken_down(["branded", "fallback"]))

    def test_the_reply_for_a_dialog_not_taken_down_says_so(self):
        """The reply the page and the bus are given for that outcome: nothing
        was sent, the dialog could not be closed, and answering it now sends
        nothing - never the person's Cancel."""
        from intergen.consent_modal import refusal_sentence
        self.assertEqual(
            refusal_sentence(["not-taken-down"]),
            "Not sent — the review dialog could not be closed, so nothing was "
            "sent to the frontier model; if it is still on screen, answering "
            "it sends nothing.")

    def test_control_a_refused_kill_of_a_program_that_has_ended_is_no_failure(
            self):
        """A program that has ended is verified ended though its kill raised:
        no failure, no ERROR."""
        import logging
        import sys
        from intergen import consent_dialog
        errors: list[str] = []
        handler = logging.Handler(level=logging.ERROR)
        handler.emit = lambda record: errors.append(record.getMessage())
        log = logging.getLogger("intergen.consent_dialog")
        log.addHandler(handler)
        self.addCleanup(log.removeHandler, handler)
        proc = _kill_refused_popen()([sys.executable, "-c", "pass"])
        proc.wait(5)
        self.assertEqual({
            "the teardown's failure": consent_dialog._kill(proc),
            "ERRORs": errors,
        }, {
            "the teardown's failure": None,
            "ERRORs": [],
        })

    def test_a_review_dialog_that_cannot_be_taken_down_is_a_failed_review(self):
        """The review gate's dialog shares the launcher: unanswered at its
        deadline with its kill refused, it is a failed review - never a
        timed-out one, and never a fallback beside it."""
        import subprocess
        import types
        from intergen import consent_dialog, consent_dialog_proto
        from intergen.tool_registry import REVIEW_FAILED
        dialogs = _Dialogs(self, "renders")
        real_launcher = consent_dialog._run_dialog
        for patcher in (
                mock.patch.object(consent_dialog, "_run_dialog",
                                  side_effect=lambda spec, cmd=None, **kw:
                                  real_launcher(spec, cmd=dialogs.branded,
                                                **kw)),
                mock.patch.object(consent_dialog, "subprocess",
                                  types.SimpleNamespace(
                                      Popen=_kill_refused_popen(),
                                      PIPE=subprocess.PIPE,
                                      DEVNULL=subprocess.DEVNULL,
                                      TimeoutExpired=subprocess.TimeoutExpired)),
                mock.patch.object(consent_dialog, "KILL_REAP_SECONDS", 0.3,
                                  create=True),
                mock.patch.object(consent_dialog_proto,
                                  "POST_RENDER_DEADLINE_SECONDS", 1.0)):
            patcher.start()
            self.addCleanup(patcher.stop)
        call = types.SimpleNamespace(name="read_file", arguments='{"path": "/etc/hostname"}')
        decision = types.SimpleNamespace(
            effective_provenance=Provenance.USER_DIRECT, reason="", needs_pkexec=False,
            risk_tier=None)
        self.assertEqual(consent_dialog.run_review_dialog(call, decision),
                         REVIEW_FAILED)


# The program of the cases below, in a dialog's, a notification's or a probe's
# place: it records its pid and runs until the case ends it (within a minute
# in any case), reading nothing and drawing nothing.
_HOLDING_PROGRAM = """import os, sys, time
pids, over = sys.argv[1:3]
with open(pids, "a") as f:
    f.write(str(os.getpid()) + chr(10))
for _ in range(600):
    if os.path.exists(over):
        sys.exit(0)
    time.sleep(0.1)
"""


class _Held:
    """One case's program, found under ``name`` (zenity, notify-send,
    systemctl, gdbus) by the module that runs it."""

    def __init__(self, case: unittest.TestCase, name: str) -> None:
        import shlex
        import sys
        import tempfile
        from pathlib import Path
        tmp = tempfile.TemporaryDirectory(prefix="bounded-teardown-")
        case.addCleanup(tmp.cleanup)
        here = Path(tmp.name)
        self.name = name
        self.pids = here / "pids"
        self.over = here / "over"
        program = here / "program.py"
        program.write_text(_HOLDING_PROGRAM)
        self.path = str(here / name)
        Path(self.path).write_text("#!/bin/sh\nexec " + shlex.join(
            [sys.executable, str(program), str(self.pids), str(self.over)])
            + "\n")
        Path(self.path).chmod(0o755)

    def which(self, name: str) -> str | None:
        """shutil.which as the module sees it: only this program exists."""
        return self.path if name == self.name else None

    def pid(self) -> int | None:
        if not self.pids.exists():
            return None
        lines = self.pids.read_text().split()
        return int(lines[-1]) if lines else None

    def end(self) -> None:
        """The program exits; one still running after two seconds is
        killed."""
        import os
        import signal
        import time
        self.over.touch()
        for _ in range(200):
            pid = self.pid()
            if pid is None or not _running(pid):
                return
            time.sleep(0.01)
        try:
            os.kill(self.pid(), signal.SIGKILL)
        except ProcessLookupError:
            pass


class EveryProgramTheModalsRunIsTornDownWithinItsBound(unittest.TestCase):
    """The consent and review modals ran their programs through subprocess.run
    with a timeout: the fallback consent dialog when its caller hands no stop,
    the review dialog, the review gate's notification and its two session
    probes - and the consent step's notification with no timeout at all. At
    its timeout subprocess.run kills the program and then waits for it without
    a bound, so a program the kill does not end (as one stuck in the kernel)
    held the call for as long as it lived, and the consent step or the review
    never came back. Every one now runs through the branded dialog's bounded
    runner (consent_dialog.run_bounded): a kill, a reap bounded by
    KILL_REAP_SECONDS and a poll. A program not verified ended is logged with
    its pid and the reason and answered as the dialog's own failure (the
    consent step's not-taken-down, the review's review_failed), or, for a
    notification or a probe, as that step's failure was answered before. When
    an error stops the wait (the caller's stop raising) and the teardown that
    follows fails, the runner raises DialogNotTakenDown naming the program's
    pid, the error its cause: it re-raised the error alone, and the caller
    never learned the program was still running.

    Each case runs the real modal code with its program replaced by the case's
    own (_Held) and every Popen's kill made a no-op, its bound shortened where
    a constant holds it, and reads whether the call came back within its bound
    and the reap, what it answered, whether the program still runs, and
    whether an ERROR names the program's pid. The control: a review dialog the
    kill does end at its bound is a timed-out review, as before."""

    maxDiff = None

    def _held(self, program: _Held, call, bound: float, patches=(),
              what: str = "the dialog", kill_ends_it: bool = False) -> dict:
        import logging
        import subprocess
        import threading
        from intergen import consent_dialog
        if not kill_ends_it:
            patches = (mock.patch.object(subprocess, "Popen",
                                         _never_reaped_popen()), *patches)
        for patcher in (mock.patch.object(consent_dialog, "KILL_REAP_SECONDS",
                                          0.3, create=True), *patches):
            patcher.start()
            self.addCleanup(patcher.stop)
        errors: list[str] = []
        handler = logging.Handler(level=logging.ERROR)
        handler.emit = lambda record: errors.append(record.getMessage())
        log = logging.getLogger("intergen.consent_dialog")
        log.addHandler(handler)
        self.addCleanup(log.removeHandler, handler)
        out: dict = {}

        def run() -> None:
            try:
                out["answered"] = call()
            except BaseException as exc:  # read by the case
                out["answered"] = f"<raised {type(exc).__name__}>"

        thread = threading.Thread(target=run, name="held", daemon=True)
        thread.start()
        # Cleanups run last-registered first: the program ends and the call's
        # thread is joined BEFORE the stand-ins above are removed, so a call
        # still waiting on the program (the red runs at the base) never
        # reaches a real program after its case.
        self.addCleanup(thread.join, 10)
        self.addCleanup(program.end)
        thread.join(bound)
        pid = program.pid()
        return {
            "came back within its bound and the reap": not thread.is_alive(),
            "answered": out.get("answered", "<still waiting>"),
            "the program still running": bool(
                pid is not None and _running(pid)),
            "an ERROR names its pid": any(
                f"could not take down {what} (pid {pid})" in m
                for m in errors),
        }

    @staticmethod
    def _reported(answered) -> dict:
        return {
            "came back within its bound and the reap": True,
            "answered": answered,
            "the program still running": True,
            "an ERROR names its pid": True,
        }

    @staticmethod
    def _review():
        import types
        call = types.SimpleNamespace(name="read_file",
                                     arguments='{"path": "/etc/hostname"}')
        decision = types.SimpleNamespace(
            effective_provenance=Provenance.USER_DIRECT, reason="",
            needs_pkexec=False, risk_tier=None)
        return call, decision, "", "", ""

    def test_the_fallback_consent_dialog_with_no_stop(self):
        """The branded dialog cannot open, the caller hands no stop, and the
        fallback dialog is unanswered at its deadline: not verified closed,
        so recorded not taken down."""
        from intergen import consent_dialog, consent_dialog_proto, consent_modal
        program = _Held(self, "zenity")
        outcome: list = []
        self.assertEqual(self._held(
            program,
            lambda: (consent_modal.prompt_send_consent(
                FRONTIER_QUESTION, "frontier", outcome=outcome), outcome),
            bound=4.0, patches=(
                mock.patch.object(consent_modal, "_session_active",
                                  return_value=True),
                mock.patch.object(consent_dialog, "run_consent_dialog",
                                  return_value=None),
                mock.patch.object(consent_modal.shutil, "which",
                                  side_effect=program.which),
                mock.patch.object(consent_modal, "_prompt_consent_libnotify",
                                  return_value=False),
                mock.patch.object(consent_dialog_proto,
                                  "POST_RENDER_DEADLINE_SECONDS", 1.0))),
            self._reported((False, ["not-taken-down"])))

    def test_the_review_dialog(self):
        """The review dialog outlives its bound (one hour and a minute;
        one second here, as the case's program ignores its --timeout): not
        verified closed, so a failed review, never a timed-out one."""
        from intergen import review_modal
        program = _Held(self, "zenity")
        self.assertEqual(self._held(
            program, lambda: review_modal._prompt_review_zenity(
                *self._review()),
            bound=4.0, patches=(
                mock.patch.object(review_modal.shutil, "which",
                                  side_effect=program.which),
                mock.patch.object(review_modal, "FALLBACK_TIMEOUT_SECONDS",
                                  -59))),
            self._reported("review_failed"))

    def test_the_review_gates_notification(self):
        from intergen import review_modal
        program = _Held(self, "notify-send")
        self.assertEqual(self._held(
            program, lambda: review_modal._prompt_review_libnotify(
                *self._review()),
            bound=8.0, what="notify-send", patches=(
                mock.patch.object(review_modal.shutil, "which",
                                  side_effect=program.which),)),
            self._reported("review_failed"))

    def test_the_consent_steps_notification(self):
        """No session, so the consent step only notifies: its notification
        had no bound at all. Nobody was asked, and the record says so."""
        from intergen import consent_modal
        program = _Held(self, "notify-send")
        outcome: list = []
        self.assertEqual(self._held(
            program,
            lambda: (consent_modal.prompt_send_consent(
                FRONTIER_QUESTION, "frontier", outcome=outcome), outcome),
            bound=8.0, what="notify-send", patches=(
                mock.patch.object(consent_modal, "_session_active",
                                  return_value=False),
                mock.patch.object(consent_modal.shutil, "which",
                                  side_effect=program.which))),
            self._reported((False, ["not-shown"])))

    def test_the_display_probe(self):
        """The probe that reads the display from the user manager: the
        environment is left as it was."""
        import os
        from intergen import review_modal
        program = _Held(self, "systemctl")
        environment = mock.patch.dict(os.environ, {}, clear=False)
        environment.start()
        self.addCleanup(environment.stop)
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"):
            os.environ.pop(name, None)
        self.assertEqual(self._held(
            program, lambda: (review_modal._ensure_display_env(),
                              os.environ.get("DISPLAY")),
            bound=5.0, what="systemctl", patches=(
                mock.patch.object(review_modal.shutil, "which",
                                  side_effect=program.which),)),
            self._reported((None, None)))

    def test_the_screen_lock_probe(self):
        """The probe that asks whether the screen is locked: the session is
        taken as active, as for a probe that fails."""
        import os
        from intergen import review_modal
        program = _Held(self, "gdbus")
        self.assertEqual(self._held(
            program, review_modal._session_active,
            bound=5.0, what="gdbus", patches=(
                mock.patch.dict(os.environ, {"DISPLAY": ":0"}),
                mock.patch.object(review_modal.shutil, "which",
                                  side_effect=program.which))),
            self._reported(True))

    def test_a_stop_that_raises_takes_the_fallback_dialog_down(self):
        """The caller's stop raises while the fallback dialog is open: the
        error goes on to the caller, as before, and the program is taken
        down first, as subprocess.run takes a program down on any error."""
        from intergen import consent_dialog, consent_modal
        program = _Held(self, "zenity")
        asked = {"times": 0}

        def stop() -> bool:
            asked["times"] += 1
            if asked["times"] > 5 and program.pid() is not None:
                raise RuntimeError("the stop could not be read")
            return False

        self.assertEqual(self._held(
            program, lambda: consent_modal.prompt_send_consent(
                FRONTIER_QUESTION, "frontier", should_stop=stop),
            bound=4.0, kill_ends_it=True, patches=(
                mock.patch.object(consent_modal, "_session_active",
                                  return_value=True),
                mock.patch.object(consent_dialog, "run_consent_dialog",
                                  return_value=None),
                mock.patch.object(consent_modal.shutil, "which",
                                  side_effect=program.which),
                mock.patch.object(consent_modal, "_prompt_consent_libnotify",
                                  return_value=False))), {
            "came back within its bound and the reap": True,
            "answered": "<raised RuntimeError>",
            "the program still running": False,
            "an ERROR names its pid": False,
        })

    def test_a_stop_that_raises_when_the_teardown_fails(self):
        """The caller's stop raises while the fallback dialog is open, and
        the dialog's kill is refused: the program may still be on screen, so
        the runner raises DialogNotTakenDown naming its pid, the stop's error
        its cause - never the stop's error alone, which told the caller
        nothing of a program left running."""
        import subprocess
        from intergen import consent_dialog, consent_modal
        program = _Held(self, "zenity")
        asked = {"times": 0}

        def stop() -> bool:
            asked["times"] += 1
            if asked["times"] > 5 and program.pid() is not None:
                raise RuntimeError("the stop could not be read")
            return False

        def ask():
            try:
                consent_modal._prompt_consent_zenity(
                    FRONTIER_QUESTION, "frontier", "", should_stop=stop)
            except consent_dialog.DialogNotTakenDown as exc:
                return {
                    "raised": type(exc).__name__,
                    "it names the pid": f"(pid {program.pid()})" in str(exc),
                    "its cause": type(exc.__cause__).__name__,
                }
            return "<returned>"

        self.assertEqual(self._held(
            program, ask, bound=4.0, kill_ends_it=True, patches=(
                mock.patch.object(subprocess, "Popen", _kill_refused_popen()),
                mock.patch.object(consent_modal.shutil, "which",
                                  side_effect=program.which))),
            self._reported({
                "raised": "DialogNotTakenDown",
                "it names the pid": True,
                "its cause": "RuntimeError",
            }))

    def test_control_a_review_dialog_the_kill_ends_is_a_timed_out_review(self):
        from intergen import review_modal
        program = _Held(self, "zenity")
        self.assertEqual(self._held(
            program, lambda: review_modal._prompt_review_zenity(
                *self._review()),
            bound=4.0, kill_ends_it=True, patches=(
                mock.patch.object(review_modal.shutil, "which",
                                  side_effect=program.which),
                mock.patch.object(review_modal, "FALLBACK_TIMEOUT_SECONDS",
                                  -59))), {
            "came back within its bound and the reap": True,
            "answered": "review_timed_out",
            "the program still running": False,
            "an ERROR names its pid": False,
        })


class _DripEndpoint:
    """A loopback endpoint that sends slowly: it answers one request with
    ``status`` and ``body``, which it sends one byte every ``drip`` seconds
    (all at once for none) until all of it is sent or the case ends it. A
    receive brings a byte long before the socket's timeout, so no wait on it
    ever times out."""

    def __init__(self, status: int, body: bytes = b"x" * 65536,
                 drip: float = 0.002) -> None:
        import socket
        import threading
        self.status = status
        self.body = body
        self.drip = drip
        self.done = threading.Event()
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.srv.settimeout(5)
        self.port = self.srv.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, name="drip-endpoint",
                                       daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self.srv.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5)
            try:
                data = b""
                while b"\r\n\r\n" not in data:
                    more = conn.recv(65536)
                    if not more:
                        return
                    data += more
                head, _, rest = data.partition(b"\r\n\r\n")
                length = next(
                    int(line.split(b":", 1)[1]) for line in head.split(b"\r\n")
                    if line.lower().startswith(b"content-length:"))
                while len(rest) < length:
                    more = conn.recv(65536)
                    if not more:
                        return
                    rest += more
                self._answer(conn)
            except OSError:
                pass

    def _answer(self, conn) -> None:
        import time
        conn.sendall(b"HTTP/1.1 %d Controlled\r\nContent-Type: "
                     b"application/json\r\nContent-Length: %d\r\n"
                     b"Connection: close\r\n\r\n"
                     % (self.status, len(self.body)))
        if not self.drip:
            conn.sendall(self.body)
            return
        for i in range(len(self.body)):
            if self.done.is_set():
                return
            conn.sendall(self.body[i:i + 1])
            time.sleep(self.drip)

    def close(self) -> None:
        self.done.set()
        self.thread.join(10)
        self.srv.close()


class _StallingEndpoint(_DripEndpoint):
    """The same endpoint, answering with the head of a 1000-byte reply and,
    ``first_byte_after`` seconds later, the body's first byte, then nothing
    more until the case ends it: the receive after that byte waits on the
    socket for as long as it is let."""

    def __init__(self, status: int, first_byte_after: float) -> None:
        self.first_byte_after = first_byte_after
        super().__init__(status, b"x" * 1000)

    def _answer(self, conn) -> None:
        conn.sendall(b"HTTP/1.1 %d Controlled\r\nContent-Type: "
                     b"application/json\r\nContent-Length: %d\r\n"
                     b"Connection: close\r\n\r\n"
                     % (self.status, len(self.body)))
        if self.done.wait(self.first_byte_after):
            return
        conn.sendall(self.body[:1])
        self.done.wait(30)


class _ChunkFramingDripEndpoint(_DripEndpoint):
    """The same endpoint, answering with a chunked reply whose first
    chunk-size line never ends: after ``1;`` its chunk extension arrives a
    byte every ``drip`` seconds until the case ends it. Each of those
    receives is made inside one read1, which reads the whole chunk-size line
    before it returns, and each brings a byte long before the socket's
    timeout."""

    def _answer(self, conn) -> None:
        conn.sendall(b"HTTP/1.1 %d Controlled\r\nContent-Type: "
                     b"application/json\r\nTransfer-Encoding: chunked\r\n"
                     b"Connection: close\r\n\r\n1;" % self.status)
        while not self.done.wait(self.drip):
            conn.sendall(b"a")


class AReplyThatArrivesSlowlyHoldsNeitherTheStopNorTheCall(unittest.TestCase):
    """The adapter read a reply's body 4096 bytes at a time, each read waiting
    for its whole chunk, every receive inside it within the socket's timeout,
    and checked the caller's stop only between reads. An endpoint that sends
    slowly held the call: measured on a loopback endpoint sending a byte
    every 2 ms with a 0.5 s timeout, a stop set 0.3 s in was seen 8.56 s
    later; at the shipped 60 s timeout one read could last about 68 hours,
    and a caller with no stop was held for as long as the endpoint kept
    sending. Each read is now one receive (read1), so
    the stop is seen within one wait on the socket, and the whole read has
    the request's timeout, past which the call ends as a provider error that
    names it.

    That timeout was checked only before each read, and every receive still
    waited on the socket for the request's whole timeout: a reply whose
    first byte came just inside the timeout and then stalled ran to nearly
    twice the timeout and ended as a raw TimeoutError, not the provider
    error. A chunked reply's framing - the chunk-size line and its
    extension, a chunk's closing CRLF, the trailers - is received inside one
    read1, so an endpoint that sent its framing a byte at a time was never
    stopped: measured with the 0.5 s timeout and a byte every 50 ms, the
    call was still reading 30 s in, and a stop set 0.3 s in was not seen
    until the endpoint stopped sending. Every receive is now bound to what
    is left of the whole read's timeout and checks the stop first: a stall
    after the first byte ends at the timeout as the provider error naming
    it, framing that arrives a byte at a time is stopped within one wait,
    or ends at the timeout, and a reply whose socket cannot be reached is
    not read, since its receives could not be bound.

    Each case calls the shipped adapter's _request on a worker thread, with
    the 0.5 s timeout, against one of those endpoints - a server the case
    starts itself, on an ephemeral loopback port: an error reply and a
    successful one, sent slowly with a stop set 0.3 s in and with no stop,
    stalled after its first byte, and chunked with its framing sent slowly,
    with the stop and without; and a reply given a stream that is not its
    socket's. The control: a reply sent at once is read whole."""

    TIMEOUT = 0.5
    STOP_AFTER = 0.3
    FIRST_BYTE_AFTER = 0.45

    maxDiff = None

    def _call(self, endpoint: _DripEndpoint, with_stop: bool,
              at_the_timeout: bool = False) -> dict:
        import threading
        import time
        from intergen.cloud import http_adapter
        from intergen.interfaces.cloud import ProviderConfig
        adapter = http_adapter.HTTPCloudAdapter(ProviderConfig(
            name="drip", adapter="drip", model="m",
            api_key_keyring_id="unused",
            base_url=f"http://127.0.0.1:{endpoint.port}/v1"))
        patcher = mock.patch.object(adapter, "_api_key", return_value="K")
        patcher.start()
        self.addCleanup(patcher.stop)
        stop = threading.Event()
        out: dict = {}

        def call() -> None:
            try:
                out["answer"] = adapter._request(
                    {"messages": []}, timeout=self.TIMEOUT,
                    should_stop=stop.is_set if with_stop else None)
            except BaseException as exc:  # read by the case
                out["raised"] = exc
            out["returned"] = time.monotonic()

        began = time.monotonic()
        thread = threading.Thread(target=call, name="drip-call", daemon=True)
        thread.start()
        # Cleanups run last-registered first: the endpoint stops sending
        # before the call's thread is joined, so a call still reading (the
        # red runs at the base) ends with its case.
        self.addCleanup(thread.join, 10)
        self.addCleanup(endpoint.close)
        if with_stop:
            time.sleep(self.STOP_AFTER)
            stop.set()
            began = time.monotonic()
        thread.join(self.TIMEOUT + 2.0)
        returned = out.get("returned")
        raised = out.get("raised")
        self.answer = out.get("answer")
        self.raised = raised
        if with_stop:
            within, bound = ("it ended within one wait on the socket",
                             self.TIMEOUT)
        elif at_the_timeout:
            within, bound = ("it ended within half a wait of the timeout",
                             1.5 * self.TIMEOUT)
        else:
            within, bound = ("it ended within the timeout and one more wait",
                             2 * self.TIMEOUT)
        return {
            "the call ended as": (
                "<still reading>" if returned is None
                else type(raised).__name__ if raised is not None
                else "its answer"),
            within: returned is not None and returned - began < bound,
            "its words name the timeout": (
                f"was not read within {self.TIMEOUT:g}s, the request's timeout"
                in str(raised)),
        }

    @pytest.mark.reaches_a_server_it_starts_itself
    def test_an_error_reply_sent_slowly_is_stopped_within_one_wait(self):
        self.assertEqual(self._call(_DripEndpoint(500), with_stop=True), {
            "the call ended as": "CloudCallStopped",
            "it ended within one wait on the socket": True,
            "its words name the timeout": False,
        })

    @pytest.mark.reaches_a_server_it_starts_itself
    def test_an_error_reply_sent_slowly_ends_at_the_timeout(self):
        self.assertEqual(self._call(_DripEndpoint(500), with_stop=False), {
            "the call ended as": "CloudAdapterError",
            "it ended within the timeout and one more wait": True,
            "its words name the timeout": True,
        })

    @pytest.mark.reaches_a_server_it_starts_itself
    def test_an_answer_sent_slowly_is_stopped_within_one_wait(self):
        self.assertEqual(self._call(_DripEndpoint(200), with_stop=True), {
            "the call ended as": "CloudCallStopped",
            "it ended within one wait on the socket": True,
            "its words name the timeout": False,
        })

    @pytest.mark.reaches_a_server_it_starts_itself
    def test_an_answer_sent_slowly_ends_at_the_timeout(self):
        self.assertEqual(self._call(_DripEndpoint(200), with_stop=False), {
            "the call ended as": "CloudAdapterError",
            "it ended within the timeout and one more wait": True,
            "its words name the timeout": True,
        })

    @pytest.mark.reaches_a_server_it_starts_itself
    def test_an_answer_that_stalls_after_its_first_byte_ends_at_the_timeout(
            self):
        self.assertEqual(self._call(
            _StallingEndpoint(200, self.FIRST_BYTE_AFTER), with_stop=False,
            at_the_timeout=True), {
            "the call ended as": "CloudAdapterError",
            "it ended within half a wait of the timeout": True,
            "its words name the timeout": True,
        })

    @pytest.mark.reaches_a_server_it_starts_itself
    def test_an_error_reply_that_stalls_after_its_first_byte_ends_at_the_timeout(
            self):
        self.assertEqual(self._call(
            _StallingEndpoint(500, self.FIRST_BYTE_AFTER), with_stop=False,
            at_the_timeout=True), {
            "the call ended as": "CloudAdapterError",
            "it ended within half a wait of the timeout": True,
            "its words name the timeout": True,
        })

    @pytest.mark.reaches_a_server_it_starts_itself
    def test_an_answer_whose_framing_arrives_slowly_is_stopped_within_one_wait(
            self):
        self.assertEqual(self._call(
            _ChunkFramingDripEndpoint(200), with_stop=True), {
            "the call ended as": "CloudCallStopped",
            "it ended within one wait on the socket": True,
            "its words name the timeout": False,
        })

    @pytest.mark.reaches_a_server_it_starts_itself
    def test_an_answer_whose_framing_arrives_slowly_ends_at_the_timeout(self):
        self.assertEqual(self._call(
            _ChunkFramingDripEndpoint(200), with_stop=False,
            at_the_timeout=True), {
            "the call ended as": "CloudAdapterError",
            "it ended within half a wait of the timeout": True,
            "its words name the timeout": True,
        })

    @pytest.mark.reaches_a_server_it_starts_itself
    def test_an_error_reply_whose_framing_arrives_slowly_is_stopped_within_one_wait(
            self):
        self.assertEqual(self._call(
            _ChunkFramingDripEndpoint(500), with_stop=True), {
            "the call ended as": "CloudCallStopped",
            "it ended within one wait on the socket": True,
            "its words name the timeout": False,
        })

    @pytest.mark.reaches_a_server_it_starts_itself
    def test_an_error_reply_whose_framing_arrives_slowly_ends_at_the_timeout(
            self):
        self.assertEqual(self._call(
            _ChunkFramingDripEndpoint(500), with_stop=False,
            at_the_timeout=True), {
            "the call ended as": "CloudAdapterError",
            "it ended within half a wait of the timeout": True,
            "its words name the timeout": True,
        })

    @pytest.mark.reaches_a_server_it_starts_itself
    def test_a_reply_whose_socket_cannot_be_reached_is_not_read(self):
        """A reply whose stream is not its socket's own, as a change in the
        standard library could leave it, cannot have its receives bound: it
        is not read, and the call ends as the provider error saying so -
        never a read without the bound. urlopen is the real one; the case
        gives the reply it opened a stream over the bytes it received."""
        import io
        import json
        import urllib.request
        answer = {"choices": [{"message": {"content": CLOUD_ANSWER}}]}
        endpoint = _DripEndpoint(200, json.dumps(answer).encode(), drip=0)
        opened = urllib.request.urlopen

        def urlopen(req, timeout=None):
            resp = opened(req, timeout=timeout)
            resp.fp = io.BufferedReader(io.BytesIO(resp.fp.read()))
            return resp

        with mock.patch("urllib.request.urlopen", urlopen):
            outcome = self._call(endpoint, with_stop=False)
        outcome["its words say why"] = (
            "its socket could not be reached to bound each receive to the "
            "request's timeout" in str(self.raised))
        self.assertEqual(outcome, {
            "the call ended as": "CloudAdapterError",
            "it ended within the timeout and one more wait": True,
            "its words name the timeout": False,
            "its words say why": True,
        })

    @pytest.mark.reaches_a_server_it_starts_itself
    def test_control_an_answer_sent_at_once_is_read_whole(self):
        import json
        answer = {"choices": [{"message": {"content": CLOUD_ANSWER}}],
                  "pad": "y" * 70000}
        endpoint = _DripEndpoint(200, json.dumps(answer).encode(), drip=0)
        self.assertEqual(self._call(endpoint, with_stop=False), {
            "the call ended as": "its answer",
            "it ended within the timeout and one more wait": True,
            "its words name the timeout": False,
        })
        self.assertEqual(self.answer, answer)


if __name__ == "__main__":
    unittest.main()
