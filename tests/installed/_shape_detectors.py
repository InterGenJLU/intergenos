# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""How gates 6 and 7 read the shape of the shipped modules.

Both gates decide whether an installed system still keeps one conversation for
every client, or one conversation per conversation. They decide it by reading
what the shipped code IS — which names a handler's compiled code references,
which fields a connection carries, how many consent records reach the
dispatcher — because the alternative, standing up a daemon and two browsers,
is not something a post-install checklist can do.

The readers live here, in one file, for two reasons.

  * A gate whose reader is a private copy inside the gate cannot be proved.
    These are proved both ways by intergen/tests/test_installed_gate_shape_
    detectors.py in the ordinary suite: against a stand-in built to the shape
    R001.1 shipped, every reader must still report the defect; against the
    current tree, every reader must report it absent. A reader that cannot fail
    is not a reader, and that is caught in the source tree rather than silently
    on a machine where this tier is gated off.

  * When the shape moves, one file moves with it. The gates keep asking the
    same question either way.

Compiled code objects, not source text: an earlier gate in this tier went green
by reading source text and matching the wrong expression. A code object carries
the names a function really uses, and comments and strings cannot fake it.
"""

from __future__ import annotations

import re

# The call that ends one conversation. Its name is part of the shipped
# interface: a session handler that does not reference it is not ending the
# conversation it just replaced.
RESET = "reset_conversation_state"

# A per-connection field naming the conversation the model is prompted from.
# "session_history" is deliberately NOT here: in the shape this gate was written
# against, that field was the display and persistence copy while the model read
# a buffer held on the shared router.
_OWNED_CONVERSATION_FIELDS = (
    "conversation", "conversation_state", "conversation_history",
    "model_history", "router", "_router",
)


def names_used(func) -> set[str]:
    """Every name the function's code object references, including nested code."""
    seen: set[str] = set()
    stack = [func.__code__]
    while stack:
        code = stack.pop()
        seen.update(code.co_names)
        for const in code.co_consts:
            if hasattr(const, "co_names"):
                stack.append(const)
    return seen


def handler_resets_the_conversation(handler) -> bool:
    """True when this session handler ends the conversation it is replacing."""
    return RESET in names_used(handler)


def connection_owns_the_model_conversation(ctx_cls) -> bool:
    """True when a connection carries the conversation the model is prompted from."""
    fields = set(getattr(ctx_cls, "__annotations__", {}))
    return any(f in fields for f in _OWNED_CONVERSATION_FIELDS)


def connection_fields(ctx_cls) -> list[str]:
    """The per-connection field names, for a failure message that shows its work."""
    return sorted(getattr(ctx_cls, "__annotations__", {}))


def reset_clears_the_consent_record(reset_source: str) -> bool:
    """True when the conversation reset still clears the consent record.

    A premise check, not a finding: both gates rest on the reset actually
    clearing consent, so if that stops being true the gates must be rewritten
    rather than reporting a pass or a fail about it. Matches the record whether
    the reset reaches it as an attribute of the router (`self._trust_state`, the
    shape R001.1 shipped) or of the conversation (`state.trust_state`), and
    whether it is replaced or cleared in place.
    """
    if re.search(r"trust_state\b", reset_source):
        return True
    # Cleared through the conversation's own clear() rather than field by field.
    return bool(re.search(r"\.clear\(\)", reset_source))


def consent_record_sites(web_text: str, router_text: str) -> tuple[int, int]:
    """(per-connection sites, shared-router sites) handing consent to the dispatcher.

    Two non-zero counts mean two records with different lifetimes: a decision
    taken on one path is invisible on the other.
    """
    per_connection = web_text.count("trust_state=ctx.conversation_trust_state")
    shared = router_text.count("trust_state=self._trust_state")
    return per_connection, shared
