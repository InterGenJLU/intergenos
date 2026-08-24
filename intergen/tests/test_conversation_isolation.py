# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""One conversation's state belongs to that conversation and to nothing else.

THE DEFECT THESE TESTS EXIST FOR. The assistant serves every browser tab, the
console and the desktop bus from a single router object, and that object holds
one conversation history, one consent record, one ingress watermark, one set of
pending offers and one turn counter. Two tabs are therefore one conversation as
far as the model is concerned: each delivered answer is written into the shared
buffer the prompt builder reads, a "yes" typed in one tab can accept an offer
staged in another, and an approval a person grants "for this conversation" stays
in force for every conversation afterwards, including one started by somebody
else at the same machine. Starting a new session or switching sessions empties
the pane and replaces the stored transcript without touching any of it.

A SECOND, SEPARATE RECORD MAKES THE CONSENT HALF WORSE. The browser connection
carries its own consent record and its own ingress watermark, and those are
handed to the dispatcher on exactly one path — a tool the user invokes directly.
An ordinary typed turn is served through the router, which hands the dispatcher
its own shared record instead. There are two records with different lifetimes
and different scopes, so a decision taken on one path is invisible on the other
and a fix that resets one of them leaves the defect standing on the other.

WHAT IS PROVED HERE. Each test drives shipped code: the real web-server session
handlers against a recording socket, the real router helpers on a partially
constructed router (the established idiom in test_offer_accept.py), and the real
consent record from intergen.interfaces.provenance. No live model, no daemon, no
browser, no display.

WHAT IS NOT PROVED HERE, STATED PLAINLY: no two real WebSocket clients were
connected to a running daemon and driven through a real model turn. That leg
belongs to the installed-system gate tier (gates 6 and 7) on a real install.
"""

from __future__ import annotations

import asyncio
import logging
import unittest
from unittest import mock

from intergen.interfaces.types import Message, MessageRole, ToolCall
from intergen import web_server as ws_mod
from intergen.web_server import WebServer, ConnectionContext


# ── Call-time imports ──────────────────────────────────────────────────────
# Imported inside the tests rather than at module level: a module-level import
# of a name that does not exist yet breaks collection for the whole file and
# hides every other case behind one error.

def _state_module():
    try:
        import intergen.conversation_state as mod
    except ImportError as exc:                        # pragma: no cover - red path
        raise AssertionError(
            "intergen/conversation_state.py does not exist. The conversation's "
            "history, consent record, ingress watermark, pending offers, "
            "grounding window, handed-off set, turn index and first-interaction "
            "flag are still attributes of the single shared router object, so "
            "every connection shares one conversation."
        ) from exc
    return mod


def _new_state(**kw):
    mod = _state_module()
    factory = getattr(mod, "new_conversation_state", None)
    if factory is None:
        raise AssertionError(
            "intergen.conversation_state has no new_conversation_state() "
            "factory; a caller cannot make a conversation of its own.")
    return factory(**kw)


def _bare_router():
    """A router with no __init__ run — the idiom from test_offer_accept.py.

    Only the attributes a test touches are placed, so the offer/history helpers
    can be exercised without standing up an embedder, a model client or a daemon.
    """
    from intergen.router import ConversationRouter
    r = ConversationRouter.__new__(ConversationRouter)
    r._max_history = 20
    r._record = lambda *a, **k: None
    r._current_query_type = "general"
    r._memory = None
    r._embedder = None
    return r


def _bind(router, state):
    binder = getattr(router, "bind_conversation", None)
    if binder is None:
        raise AssertionError(
            "ConversationRouter has no bind_conversation(); there is no way for "
            "a caller to say WHICH conversation a turn belongs to, so every "
            "turn lands on the router's single shared state.")
    return binder(state)


class _RecordingWS:
    """Stands in for the aiohttp WebSocketResponse; records every send."""

    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, payload):
        self.sent.append(payload)

    def of_type(self, type_name):
        return [m for m in self.sent if m.get("type") == type_name]


def _ctx(client_id="c1"):
    return ConnectionContext(client_id=client_id, source_interface="web",
                             ws=_RecordingWS())


def _conversation_of(ctx):
    conv = getattr(ctx, "conversation", None)
    if conv is None:
        raise AssertionError(
            "A browser connection does not carry a conversation of its own. "
            f"Its fields are: {sorted(getattr(type(ctx), '__annotations__', {}))}. "
            "'session_history' is the display and persistence copy; the history "
            "the model is actually prompted with lives on the shared router.")
    return conv


def _server(**kw):
    defaults = dict(router=None, llm=None, tools=None, governance=None)
    defaults.update(kw)
    return WebServer(**defaults)


# ── The state object itself ────────────────────────────────────────────────
class ConversationStateShapeTests(unittest.TestCase):
    """The object must hold exactly what a conversation reset replaces."""

    # The attributes reset_conversation_state() replaces today, as measured on
    # dev 6e8aa572f (router.py:6355-6393), in the order it replaces them.
    REPLACED = (
        "trust_state", "ingress_tracker", "history",
        "pending_action_offer", "pending_ipv6_offer", "pending_memory_offer",
        "action_offer_ttl", "offer_in_recent_history", "offer_topic_terms",
        "handed_off_commands", "turn_index", "first_interaction",
    )

    def test_the_state_holds_every_attribute_the_reset_replaces(self):
        state = _new_state()
        missing = [n for n in self.REPLACED if not hasattr(state, n)]
        self.assertEqual(missing, [], (
            "A conversation's state object is missing "
            f"{missing}. Anything a conversation reset replaces that the state "
            "does not hold stays on the shared router and is shared by every "
            "connection."))

    def test_two_states_share_no_mutable_container(self):
        a, b = _new_state(), _new_state()
        self.assertIsNot(a.history, b.history)
        self.assertIsNot(a.trust_state, b.trust_state)
        self.assertIsNot(a.ingress_tracker, b.ingress_tracker)
        self.assertIsNot(a.handed_off_commands, b.handed_off_commands)


# ── Proof 1: two clients alternating turns ─────────────────────────────────
class TwoClientsAlternatingTurnsTests(unittest.TestCase):

    def test_each_connection_owns_its_own_conversation(self):
        a, b = _ctx("tab-a"), _ctx("tab-b")
        self.assertIsNot(_conversation_of(a), _conversation_of(b), (
            "Two browser connections were handed the same conversation object."))

    def test_the_prompt_one_client_gets_carries_only_its_own_turns(self):
        """The history the prompt builder reads, measured through the builder."""
        router = _bare_router()
        router._turn_index = None
        router._llm = mock.Mock()
        # side_effect, not return_value: a Mock returns the SAME list on every
        # call and _build_messages appends to it, so a shared list would make a
        # second call look like it carried the first's turns.
        router._llm.build_system_messages.side_effect = lambda **kw: []
        router._wiki_retrieval = None
        state_a, state_b = _new_state(), _new_state()

        # The conversation is named ON THE CALL, because the browser server
        # writes the delivered exchange back after the route lock is released,
        # by which time another connection may be the one bound.
        append = getattr(router, "_append_history")
        with _bind(router, state_a):
            append("what is my disk usage", "83% of 1 TB is in use",
                   state=state_a)
        with _bind(router, state_b):
            append("who wrote the kernel", "many people did", state=state_b)
            built = router._build_messages("and how big is it",
                                           with_tools=False)

        text = "\n".join(m.content for m in built)
        self.assertIn("who wrote the kernel", text,
                      "the second client's own turn is missing from its prompt")
        self.assertNotIn("what is my disk usage", text, (
            "One client's turn appeared in another client's prompt. The model "
            "is being shown a conversation the person in front of it never had."))

    def test_a_consent_granted_in_one_conversation_is_refused_in_another(self):
        state_a, state_b = _new_state(), _new_state()
        state_a.trust_state.remember_decision("run_command", "user", "allow")
        self.assertEqual(state_a.trust_state.check("run_command", "user"),
                         "allow")
        self.assertIsNone(state_b.trust_state.check("run_command", "user"), (
            "An approval granted in one conversation was already in force in "
            "another. On a shared machine the person whose approval is standing "
            "may not be the person now typing."))


# ── Proof 2: one consent record, not two (N-01) ────────────────────────────
class OneConsentRecordTests(unittest.TestCase):
    """The routed path and the user-invoked-tool path must read one record."""

    def _capture_trust_state_from_user_invoked_path(self, ctx):
        captured = {}

        class _Registry:
            def get_tool(self, name):
                return object()

            def execute(self, call, **kw):
                captured["trust_state"] = kw.get("trust_state")
                captured["ingress_tracker"] = kw.get("ingress_tracker")
                return mock.Mock(name="read_file", success=True, content="",
                                 model_summary=None)

        srv = _server(tools=_Registry())
        srv._evaluate_tool_with_gate = mock.AsyncMock(return_value="approved")
        srv._make_web_review_callback = lambda *a, **k: None
        asyncio.run(srv._run_user_invoked_tool(
            ctx, "read_file", {"path": "/etc/os-release"}))
        return captured

    def _capture_trust_state_from_routed_path(self, router):
        captured = {}

        class _Registry:
            def get_tool(self, name):
                return object()

            def execute(self, call, **kw):
                captured["trust_state"] = kw.get("trust_state")
                captured["ingress_tracker"] = kw.get("ingress_tracker")
                return mock.Mock(success=True, content="ok", name="read_file")

        router._tools = _Registry()
        router._review_callback = None
        router._extract_arguments = lambda *a, **k: {"path": "/etc/os-release"}
        router._execute_tool_for_intent("read_file", "read /etc/os-release")
        return captured

    def test_both_paths_read_the_same_consent_record(self):
        ctx = _ctx()
        conv = _conversation_of(ctx)
        router = _bare_router()
        with _bind(router, conv):
            routed = self._capture_trust_state_from_routed_path(router)
        invoked = self._capture_trust_state_from_user_invoked_path(ctx)

        self.assertIsNotNone(routed.get("trust_state"),
                             "the routed path passed no consent record at all")
        self.assertIs(routed["trust_state"], invoked["trust_state"], (
            "The conversation's consent decisions are kept in two separate "
            "records. An ordinary typed turn is served through the router, so "
            "its approvals land on the router's record; a tool the user invokes "
            "directly reads the connection's record. A grant on one is invisible "
            "to the other, and a reset of one leaves the other standing."))

    def test_both_paths_read_the_same_ingress_watermark(self):
        ctx = _ctx()
        conv = _conversation_of(ctx)
        router = _bare_router()
        with _bind(router, conv):
            routed = self._capture_trust_state_from_routed_path(router)
        invoked = self._capture_trust_state_from_user_invoked_path(ctx)
        self.assertIs(routed["ingress_tracker"], invoked["ingress_tracker"], (
            "The two paths carry two ingress watermarks, so an ingress tool "
            "fired on one path does not raise the provenance of a privileged "
            "call made on the other."))


# ── Proof 3: new session clears everything ─────────────────────────────────
def _dirty(state):
    """Put a mark in every slot a conversation reset is supposed to replace."""
    state.history.append(Message(role=MessageRole.USER, content="old turn"))
    state.trust_state.remember_decision("run_command", "user", "allow")
    state.ingress_tracker.record_tool_call("web_search")
    state.pending_action_offer = ("sudo pkm upgrade", "run_command", "upgrade?")
    state.pending_ipv6_offer = "what is my ip"
    state.pending_memory_offer = ("pref", "editor", "vim", "my editor is vim")
    state.action_offer_ttl = 3
    state.offer_in_recent_history = True
    state.offer_topic_terms = frozenset({"upgrade"})
    state.handed_off_commands.add("sudo pkm upgrade")
    state.first_interaction = False
    return state


class NewSessionClearsTheConversationTests(unittest.TestCase):

    def _new_session(self):
        srv = _server(router=_bare_router())
        srv._sessions = mock.MagicMock()
        srv._send_session_list = mock.AsyncMock()
        ctx = _ctx()
        conv = _dirty(_conversation_of(ctx))
        asyncio.run(srv._handle_new_session(ctx, {}))
        return ctx, conv

    def test_new_session_clears_every_slot(self):
        ctx, old = self._new_session()
        conv = _conversation_of(ctx)
        self.assertEqual(list(conv.history), [], "history survived a new session")
        self.assertIsNone(conv.trust_state.check("run_command", "user"),
                          "a consent grant survived a new session")
        self.assertIsNone(conv.pending_action_offer)
        self.assertIsNone(conv.pending_ipv6_offer)
        self.assertIsNone(conv.pending_memory_offer)
        self.assertEqual(conv.action_offer_ttl, 0)
        self.assertFalse(conv.offer_in_recent_history)
        self.assertEqual(conv.offer_topic_terms, frozenset())
        self.assertEqual(conv.handed_off_commands, set())
        self.assertTrue(conv.first_interaction)

    def test_a_yes_in_the_new_session_cannot_accept_the_old_offer(self):
        ctx, _old = self._new_session()
        conv = _conversation_of(ctx)
        router = _bare_router()
        router._pending_memory_offer = None
        with _bind(router, conv):
            res = router._try_bare_affirmative_guard("yes", 0.0)
        self.assertIsNotNone(res, (
            "A bare 'yes' in a brand-new conversation was not caught by the "
            "nothing-staged guard, which means an offer from the discarded "
            "conversation was still live."))
        self.assertEqual(res.source, "affirmative_no_offer")


# ── Proof 4: switch restores that session's buffer ─────────────────────────
class SwitchSessionRestoresThatSessionTests(unittest.TestCase):

    def _switch(self, loaded):
        srv = _server(router=_bare_router())
        sessions = mock.MagicMock()
        sessions.load.return_value = {"messages": loaded}
        srv._sessions = sessions
        srv._send_session_list = mock.AsyncMock()
        ctx = _ctx()
        conv = _conversation_of(ctx)
        conv.history.append(Message(role=MessageRole.USER,
                                    content="a turn from the session I left"))
        conv.trust_state.remember_decision("run_command", "user", "allow")
        with mock.patch.object(ws_mod.SessionManager,
                               "_validate_session_id", return_value=None):
            asyncio.run(srv._handle_switch_session(
                ctx, {"session_id": "session_abc12345"}))
        return ctx

    def test_the_prompt_after_a_switch_is_built_from_the_restored_transcript(self):
        ctx = self._switch([
            Message(role=MessageRole.USER, content="why is the sky blue"),
            Message(role=MessageRole.ASSISTANT, content="rayleigh scattering"),
        ])
        conv = _conversation_of(ctx)
        router = _bare_router()
        router._turn_index = None
        router._llm = mock.Mock()
        # side_effect, not return_value: a Mock returns the SAME list on every
        # call and _build_messages appends to it, so a shared list would make a
        # second call look like it carried the first's turns.
        router._llm.build_system_messages.side_effect = lambda **kw: []
        router._wiki_retrieval = None
        with _bind(router, conv):
            built = router._build_messages("and what about sunsets",
                                           with_tools=False)
        text = "\n".join(m.content for m in built)
        self.assertIn("rayleigh scattering", text, (
            "After a switch the model is prompted without the transcript the "
            "pane is showing: the user sees one conversation and the model is "
            "given another."))
        self.assertNotIn("a turn from the session I left", text, (
            "The conversation the user switched AWAY from is still in the "
            "model's context."))

    def test_a_consent_does_not_cross_a_switch(self):
        ctx = self._switch([])
        conv = _conversation_of(ctx)
        self.assertIsNone(conv.trust_state.check("run_command", "user"), (
            "An approval granted in the conversation the user switched away "
            "from is still in force in the one they switched to."))


# ── Proof 5: the desktop bus and the browser do not reset each other ───────
class DbusAndWebDoNotCrossTests(unittest.TestCase):

    def test_a_bus_reset_leaves_every_browser_conversation_untouched(self):
        router = _bare_router()
        bus_state, web_state = _dirty(_new_state()), _dirty(_new_state())
        with _bind(router, bus_state):
            router.reset_conversation_state()
        self.assertEqual(list(bus_state.history), [])
        self.assertEqual(len(web_state.history), 1, (
            "A ResetConversation on the desktop bus wiped a browser "
            "conversation that nobody asked to end."))
        self.assertEqual(web_state.trust_state.check("run_command", "user"),
                         "allow")

    def test_a_browser_new_session_leaves_the_bus_conversation_untouched(self):
        srv = _server(router=_bare_router())
        srv._sessions = mock.MagicMock()
        srv._send_session_list = mock.AsyncMock()
        bus_state = _dirty(_new_state())
        ctx = _ctx()
        _dirty(_conversation_of(ctx))
        asyncio.run(srv._handle_new_session(ctx, {}))
        self.assertEqual(len(bus_state.history), 1, (
            "Starting a new conversation in the browser ended the desktop "
            "bus conversation as well."))


# ── Proof 6: an unbound conversation refuses to route ──────────────────────
class UnboundConversationRefusesTests(unittest.TestCase):

    def test_route_refuses_when_no_conversation_is_bound(self):
        router = _bare_router()
        detach = getattr(router, "detach_conversation", None)
        if detach is None:
            raise AssertionError(
                "ConversationRouter has no detach_conversation(); a router "
                "shared by several frontends cannot be put into the state "
                "where a caller that forgot to name its conversation is "
                "refused rather than silently served from a shared default.")
        detach()
        with self.assertLogs("intergen.router", level=logging.ERROR) as logs:
            result = router.route("what is my ip")
        self.assertFalse(result.handled)
        self.assertEqual(result.source, "conversation_unbound")
        self.assertTrue(
            any("conversation" in r.getMessage().lower() for r in logs.records),
            "the refusal was not logged with a reason")


if __name__ == "__main__":
    unittest.main()
