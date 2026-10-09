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


class _CloudWire:
    """The cloud provider's side of an HTTP reply as the shipped adapter reads
    it (intergen/cloud/http_adapter.py): the body arrives in pieces, one each
    time the case allows one; read(n) returns the next piece and read() with no
    size every piece left. The case counts the pieces sent and whether the
    reply was closed."""

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


if __name__ == "__main__":
    unittest.main()
