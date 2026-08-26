# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The direct-mode conversation reset must name the conversation it is ending.

WHAT WENT WRONG, MEASURED. The scenario harness resets the conversation between
scenarios so one scenario's staged offer and trust posture cannot leak into the
next. In direct (in-process) mode that reset reached past the daemon and called
``router.reset_conversation_state()`` with NO conversation named. Once the
daemon began serving several frontends over one router it detaches its own
conversation at wiring time, so a router asked to act with nothing bound refuses
— by design, because the alternative is one conversation's decisions applied to
another's turn.

The refusal landed on the harness. A live run of the 64-scenario field_shapes
class on 2026-08-26 produced 64 ERROR results in 0.0s each, every one of them
``ConversationUnbound: This router is serving no conversation`` raised before the
scenario's first turn. Nothing was measured, and the failure looked like 64
product failures rather than one harness defect.

The daemon already has the correct call: ``InterGenDaemon.reset_conversation()``
names ``self._conversation`` — the conversation the direct-mode turns are
actually routed in — and it is the SAME method the D-Bus ResetConversation
surface runs. Going through it is the both-modes parity this reset promises, and
it removes a reach past the daemon into a private router attribute.

These tests drive the REAL ConversationRouter and the REAL daemon reset method,
with no model, no bus and no llama-server, so they measure the wiring rather than
a description of it.
"""

from __future__ import annotations

import unittest

from intergen.conversation_state import new_conversation_state
from intergen.router import ConversationRouter, ConversationUnbound
from intergen.tests.client import InterGenTestClient


def _detached_router() -> ConversationRouter:
    """A real router in the state the daemon leaves it in: serving nobody.

    Built with ``__new__`` so no model, matcher or tool registry is required —
    the reset path under test touches only the conversation and the optional
    memory store. ``detach_conversation`` is the daemon's own wiring-time call.
    """
    router = ConversationRouter.__new__(ConversationRouter)
    router.detach_conversation()
    return router


class _DaemonStandIn:
    """A daemon holding a real router and a real conversation.

    ``reset_conversation`` is the SHIPPED method, taken unbound from
    InterGenDaemon so this test cannot pass against a re-implementation of it.
    """

    from intergen.dbus_daemon import InterGenDaemon as _Real
    reset_conversation = _Real.reset_conversation

    def __init__(self) -> None:
        self._router = _detached_router()
        self._conversation = new_conversation_state()


class TestDirectResetNamesTheConversation(unittest.TestCase):

    def test_router_refuses_an_unnamed_reset(self) -> None:
        """The product behaviour this test is built on, asserted directly.

        If the router ever stops refusing, this test's premise is gone and the
        rest of the file is measuring nothing — so the premise is checked here
        rather than assumed.
        """
        router = _detached_router()
        with self.assertRaises(ConversationUnbound):
            router.reset_conversation_state()

    def test_direct_reset_clears_the_daemon_conversation(self) -> None:
        """The reset must clear the conversation the direct turns run in."""
        daemon = _DaemonStandIn()
        daemon._conversation.history.append({"role": "user", "content": "hello"})

        client = InterGenTestClient.__new__(InterGenTestClient)
        client._mode = "direct"
        client._daemon = daemon

        client.reset_conversation()

        self.assertEqual(
            list(daemon._conversation.history), [],
            "the direct-mode reset left the daemon's conversation history in "
            "place — the next scenario would start inside the previous one")

    def test_direct_reset_does_not_raise_on_a_detached_router(self) -> None:
        """The regression itself: 64 scenarios died here, before turn one."""
        daemon = _DaemonStandIn()
        client = InterGenTestClient.__new__(InterGenTestClient)
        client._mode = "direct"
        client._daemon = daemon
        try:
            client.reset_conversation()
        except ConversationUnbound as exc:
            self.fail(
                "the direct-mode reset reached the router without naming a "
                f"conversation and was refused: {exc}")

    def test_a_partly_built_client_resets_without_raising(self) -> None:
        """No daemon yet (the partial-construction path) is a no-op, not a crash."""
        client = InterGenTestClient.__new__(InterGenTestClient)
        client._mode = "direct"
        client._daemon = None
        client.reset_conversation()


if __name__ == "__main__":
    unittest.main()
