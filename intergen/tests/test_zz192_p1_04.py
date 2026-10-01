# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""ZIGOSC-192 P1-04 — "/new" as a SLASH COMMAND must end the conversation.

THE RULE, quoted verbatim from the code it is written above
(intergen/web_server.py lines 1877-1881, inside ``_handle_new_session``):

    The old conversation ENDS. Emptying the pane is not enough: the model
    buffer, the consent decisions taken in it, the ingress watermark, the
    offers left awaiting a yes and the turn index all belong to the
    conversation that just finished, and a "yes" typed in the new one must
    not be able to reach back into it.

``_handle_new_session`` honours that rule by calling
``router.reset_conversation_state(ctx.conversation)`` (web_server.py:1882-1884),
and intergen/tests/test_conversation_isolation.py pins that path.

THERE IS A SECOND DOOR TO THE SAME PROMISE, AND NOTHING PINS IT. The WebSocket
protocol (docs/architecture/intergen-web-ui/websocket-protocol.md §12) documents
``{"type": "slash_command", "command": "/new"}`` as "Start a new session (sends
``session_created``)", and the dispatch table wires ``slash_command`` to
``WebServer._handle_slash_command`` (web_server.py:956). Its ``/new`` branch
(web_server.py:1609-1614) does ``ctx.session_history.clear()`` and then sends the
very same ``session_created`` frame ``_handle_new_session`` sends, so the person
is told, in the same words, that the conversation is new.

``session_history`` is a property over ``conversation.history``
(web_server.py:205-214). Clearing it therefore empties the MODEL BUFFER AND
NOTHING ELSE: the staged offers, the allow-conversation consent record, the
cross-turn ingress watermark, the handed-off set, the verbatim transcript and the
first-interaction flag all survive into the conversation the person was just told
was new. ``_resolve_pending_action_offer`` (router.py:4418-4466) runs at the very
top of the next turn with no history, age or freshness precondition, so a bare
"yes" typed in the "new" conversation dispatches the command staged in the ended
one, and a privileged call is escalated — or not escalated — on the strength of
the ended conversation's consent and watermark.

SCOPE. Only ``/new`` is asserted on here. §12 documents ``/clear`` as "Clear the
in-memory conversation buffer", which is what it does, so ``/clear`` keeping the
conversation is documented behaviour and is not claimed as a defect by this file.

WHAT IS PROVED HERE. The real web-server handler against a recording socket, the
real ``ConversationState``, the real consent record and ingress tracker from
``intergen.interfaces.provenance``, and the real router offer resolver on a
partially constructed router (the idiom of test_offer_accept.py and
test_conversation_isolation.py). No live model, no daemon, no browser, no socket,
no display.

WHAT IS NOT PROVED HERE, STATED PLAINLY. No real WebSocket client was connected
to a running daemon and driven through a real model turn. That the message shape
is reachable is read from the handler map and the protocol document, not from a
live connection; that leg belongs to the installed-system gate tier.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from intergen.interfaces.types import Message, MessageRole
from intergen.web_server import WebServer, ConnectionContext


class _RecordingWS:
    """Stands in for the aiohttp WebSocketResponse; records every send."""

    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, payload):
        self.sent.append(payload)

    def of_type(self, type_name):
        return [m for m in self.sent if m.get("type") == type_name]


def _bare_router():
    """A router with no __init__ run — the idiom from test_offer_accept.py.

    Only the attributes the exercised helpers touch are placed, so the reset and
    the offer resolver run without standing up an embedder, a model client or a
    daemon.
    """
    from intergen.router import ConversationRouter
    r = ConversationRouter.__new__(ConversationRouter)
    r._max_history = 20
    r._record = lambda *a, **k: None
    r._current_query_type = "general"
    r._memory = None
    r._embedder = None
    return r


def _server(**kw):
    defaults = dict(router=None, llm=None, tools=None, governance=None)
    defaults.update(kw)
    return WebServer(**defaults)


def _ctx(client_id="c1"):
    return ConnectionContext(client_id=client_id, source_interface="web",
                             ws=_RecordingWS())


# The exact captured shape from the finding: a person asks how to update the
# system, the assistant explains and OFFERS to run it, and the offer is left
# standing. Every other slot a conversation reset is supposed to replace is
# marked alongside it, so a reset that clears one and misses another is visible.
STAGED = ("pkm upgrade", "run_command", "how do I update my system")


def _dirty(state):
    """Put a mark in every slot the quoted rule says belongs to the old
    conversation."""
    state.history.append(Message(role=MessageRole.USER,
                                 content="how do I update my system"))
    state.trust_state.remember_decision("run_command", "user", "allow")
    state.ingress_tracker.record_tool_call("web_search")
    state.pending_action_offer = STAGED
    state.pending_ipv6_offer = "what is my ip"
    state.pending_memory_offer = ("pref", "editor", "vim", "my editor is vim")
    state.pending_search_offer = "what is the latest kernel release"
    state.action_offer_ttl = 3
    state.offer_in_recent_history = True
    state.offer_topic_terms = frozenset({"upgrade"})
    state.handed_off_commands.add("pkm upgrade")
    state.transcript.append(("how do I update my system",
                             "you would run pkm upgrade"))
    state.first_interaction = False
    return state


def _assert_conversation_ended(case, conv, door):
    """Every slot the quoted rule names, checked in one place."""
    case.assertEqual(list(conv.history), [],
                     f"{door}: the model buffer survived")
    case.assertIsNone(
        conv.pending_action_offer,
        f"{door}: the ended conversation's staged command {STAGED[0]!r} is "
        "still awaiting a yes. A bare 'yes' typed in what the person was told "
        "is a new conversation reaches back into the one that finished and "
        "dispatches it.")
    case.assertIsNone(conv.pending_ipv6_offer,
                      f"{door}: an ipv6 offer survived")
    case.assertIsNone(conv.pending_memory_offer,
                      f"{door}: a memory offer survived")
    case.assertIsNone(conv.pending_search_offer,
                      f"{door}: a web-search offer survived")
    case.assertIsNone(
        conv.trust_state.check("run_command", "user"),
        f"{door}: an allow-conversation consent granted in the ended "
        "conversation is still in force. The next privileged call is waved "
        "through on a decision the person made in a conversation they were "
        "told had ended.")
    case.assertFalse(
        conv.ingress_tracker.ingress_fired_this_conversation(),
        f"{door}: the cross-turn ingress watermark survived. The provenance "
        "posture of the new conversation is the old one's.")
    case.assertEqual(conv.action_offer_ttl, 0,
                     f"{door}: the grounding window survived")
    case.assertFalse(conv.offer_in_recent_history,
                     f"{door}: the grounding window flag survived")
    case.assertEqual(conv.offer_topic_terms, frozenset(),
                     f"{door}: the grounding topic terms survived")
    case.assertEqual(conv.handed_off_commands, set(),
                     f"{door}: the handed-off set survived")
    case.assertEqual(list(conv.transcript), [],
                     f"{door}: the verbatim transcript survived")
    case.assertTrue(conv.first_interaction,
                    f"{door}: the new conversation does not believe it is new")


class NewSlashCommandEndsTheConversationTests(unittest.TestCase):
    """``/new`` over ``slash_command`` promises a new session in the same frame
    ``new_session`` sends; it must deliver the same ending."""

    @staticmethod
    def _srv():
        srv = _server(router=_bare_router())
        srv._sessions = mock.MagicMock()
        srv._send_session_list = mock.AsyncMock()
        return srv

    def _slash_new(self):
        srv = self._srv()
        ctx = _ctx()
        conv = _dirty(ctx.conversation)
        asyncio.run(srv._handle_slash_command(ctx, {"command": "/new"}))
        return ctx, conv

    # ── The promise the frame makes ────────────────────────────────────────
    def test_slash_new_tells_the_person_the_session_is_new(self):
        """Precondition, not the defect: the reply is the new-session frame."""
        ctx, _conv = self._slash_new()
        self.assertEqual(ctx.ws.of_type("error"), [],
                         "/new is a documented slash command (protocol §12)")
        self.assertTrue(
            ctx.ws.of_type("session_created"),
            "/new did not announce a new session at all, so §12 no longer "
            "describes the handler")

    # ── The defect ─────────────────────────────────────────────────────────
    def test_the_new_slash_command_ends_the_conversation_not_just_the_pane(self):
        _ctx_, conv = self._slash_new()
        _assert_conversation_ended(self, conv, "slash_command /new")

    def test_the_consent_of_the_ended_conversation_does_not_carry(self):
        """Named on its own so the capture shows this limb of the rule even
        when the offer limb fails first."""
        _ctx_, conv = self._slash_new()
        self.assertIsNone(
            conv.trust_state.check("run_command", "user"),
            "An allow-conversation consent granted in the conversation the "
            "server announced as ended is still in force in the one it "
            "announced as new. The next privileged call is waved through on a "
            "decision taken in a conversation that had finished.")

    def test_the_ingress_watermark_of_the_ended_conversation_does_not_carry(self):
        """Named on its own for the same reason."""
        _ctx_, conv = self._slash_new()
        self.assertFalse(
            conv.ingress_tracker.ingress_fired_this_conversation(),
            "The cross-turn ingress watermark of the ended conversation is "
            "still raised in the new one, so a privileged call is escalated on "
            "an ingress read the new conversation never made.")

    def test_a_yes_after_slash_new_cannot_reach_the_old_offer(self):
        """The consequence, through the real resolver: a bare yes in the "new"
        conversation must not dispatch the ended one's staged command."""
        from intergen.router import RouteResult

        ctx, conv = self._slash_new()
        dispatched: list[str] = []

        def _fake_run(cmd):
            dispatched.append(cmd)
            return RouteResult(text="...", source="explain_offer_run",
                               handled=True)

        router = _bare_router()
        router._run_staged_command = _fake_run
        with router.bind_conversation(conv):
            router._resolve_pending_action_offer("yes", 0.0)

        self.assertEqual(
            dispatched, [],
            "A bare 'yes' typed in the conversation the server announced as "
            f"new dispatched {dispatched!r} — the command staged in the "
            "conversation that had already ended.")

    # ── The control: the other door to the same promise honours it ─────────
    def test_the_new_session_message_ends_the_conversation(self):
        """Same server, same router double, same dirty conversation — through
        ``new_session`` instead. This passes, so a failure above is the handler
        and not the fixture."""
        srv = self._srv()
        ctx = _ctx()
        conv = _dirty(ctx.conversation)
        asyncio.run(srv._handle_new_session(ctx, {}))
        _assert_conversation_ended(self, conv, "new_session")


if __name__ == "__main__":
    unittest.main()
