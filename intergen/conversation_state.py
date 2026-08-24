# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Everything that belongs to ONE conversation, in one object.

WHY THIS MODULE EXISTS. The assistant serves every browser tab, the console and
the desktop bus from a single router object. Until this module existed, that
router also HELD the conversation: one history, one consent record, one ingress
watermark, one set of pending offers, one grounding window, one handed-off set,
one turn index, one first-interaction flag. So there was exactly one
conversation on the machine no matter how many people or tabs were talking, and
the surfaces that say "new session" or "switch session" could not end one
without ending them all.

WHAT IS HERE. A plain container holding exactly the state a conversation reset
replaces, and a factory that builds a fresh one. Nothing in this module makes
policy: the router still decides what a turn does, and the frontends still
decide which conversation a turn belongs to. The container's only rule is that
two conversations never share a mutable object — every list, set and record is
built per conversation, so a decision taken in one cannot appear in another.

WHO OWNS ONE. The browser server keeps one per connected client, replaced when
that client starts a new conversation and cleared when it switches to another.
The desktop bus keeps one for its own conversation. A router constructed
directly — by a test, or by any single-frontend caller — is given one at
construction, because a router that is not shared is not multiplexed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from intergen.interfaces.types import Message

__all__ = ["ConversationState", "new_conversation_state"]


@dataclass
class ConversationState:
    """The state of one conversation.

    The fields are exactly what ``ConversationRouter.reset_conversation_state``
    replaces, under the names the router used when it owned them:

    ``history``               the model-facing buffer the prompt is built from
    ``trust_state``           allow/deny decisions the user made for this
                              conversation (D-008 RFC section 5.3)
    ``ingress_tracker``       the per-turn and per-conversation ingress
                              watermarks (D-008 RFC section 5.1)
    ``pending_action_offer``  an offered command awaiting a yes or no
    ``pending_ipv6_offer``    the follow-up IPv6 offer awaiting a yes or no
    ``pending_memory_offer``  an offered preference awaiting a yes or no
    ``action_offer_ttl``      turns the preventive-grounding window stays open
    ``offer_in_recent_history`` whether that window is open for THIS turn
    ``offer_topic_terms``     the offered command's content words
    ``handed_off_commands``   commands already declined or handed off here
    ``turn_index``            the relevance index over this conversation's turns
    ``first_interaction``     whether nothing has been said yet
    ``memory_session_id``     the long-term memory session this conversation
                              records into, or None when memory is not wired
    """

    history: list[Message] = field(default_factory=list)
    trust_state: Any = None
    ingress_tracker: Any = None
    pending_action_offer: tuple[str, str, str] | None = None
    pending_ipv6_offer: str | None = None
    pending_memory_offer: tuple[str, str, str, str] | None = None
    action_offer_ttl: int = 0
    offer_in_recent_history: bool = False
    offer_topic_terms: frozenset[str] = frozenset()
    handed_off_commands: set[str] = field(default_factory=set)
    turn_index: Any = None
    first_interaction: bool = True
    memory_session_id: str | None = None

    def __post_init__(self) -> None:
        # Built here rather than defaulted in the annotation so that two
        # conversations can never be handed the same record by accident — the
        # defect this whole object exists to close.
        from intergen.interfaces.provenance import (
            ConversationTrustState, IngressTracker,
        )
        if self.trust_state is None:
            self.trust_state = ConversationTrustState()
        if self.ingress_tracker is None:
            self.ingress_tracker = IngressTracker()

    def clear(self) -> None:
        """Return this conversation to the state a fresh one starts in.

        In place, not by replacement: the frontends and the router hold
        references to this object, and handing back a new one would leave a
        caller writing into the conversation the user just ended.

        The turn index and the memory session are NOT cleared here — both are
        built from components the router owns, so the router replaces them (see
        ``ConversationRouter.reset_conversation_state``). Everything else a
        conversation carries is cleared here, in one place, so a slot added
        later is cleared by the same code that clears the rest.
        """
        self.history.clear()
        self.trust_state.reset()
        self.ingress_tracker.reset_conversation()
        # F3 (offer/accept fix, 2026-07-01): ALL offer slots, not just memory —
        # a "yes" in a fresh conversation must never bind to an action or ipv6
        # offer staged in a discarded one (the stale-bind-across-reset hole).
        self.pending_action_offer = None
        self.pending_ipv6_offer = None
        self.pending_memory_offer = None
        # M3(ii) option B + PI-Z29: the preventive-grounding window is
        # per-conversation — a discarded conversation's offer must not inject a
        # no-dispatch note into a fresh one. Clear the TTL AND the topic terms.
        self.action_offer_ttl = 0
        self.offer_in_recent_history = False
        self.offer_topic_terms = frozenset()
        # Loop-killer set is per-conversation: a fresh conversation may re-offer
        # an action a prior (discarded) one declined.
        self.handed_off_commands.clear()
        self.first_interaction = True


def new_conversation_state(embedder: Any = None,
                           window_turns: int = 10) -> ConversationState:
    """Build a conversation with its own record of everything.

    ``embedder`` is the shared embedding client the router was given. When one
    is present the conversation gets its OWN relevance index over its own turns,
    so a retrieval in one conversation cannot surface another's exchanges. A
    failure to build the index degrades this conversation to its raw history
    window and is never allowed to take a caller down — the same guarded
    construction the router used when it owned the index.
    """
    state = ConversationState()
    if embedder is not None:
        try:
            from intergen.memory import SessionTurnIndex
            state.turn_index = SessionTurnIndex(
                embedder, window_turns=max(1, window_turns))
        except Exception:  # noqa: BLE001 — memory must never be a startup risk
            import logging
            logging.getLogger(__name__).warning(
                "M2b session memory disabled for this conversation: "
                "SessionTurnIndex failed to construct; the raw history window "
                "is the only context.", exc_info=True)
            state.turn_index = None
    return state
