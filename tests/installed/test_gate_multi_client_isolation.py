"""GATE 6 — multi-session and multi-client isolation (section 9 line 5).

WHAT COMPOSITION PROPERTY THIS CATCHES. The browser interface presents one
conversation per session and one session per tab, but every connection is served by a
single shared conversation router. Starting a new session in the browser clears the
pane and the stored transcript; it does not clear the history the model is actually
shown, the offers waiting to be accepted, or the trust decisions taken in the
discarded conversation. Two tabs share one model context.

WHY THE SOURCE-TREE TESTS CANNOT SEE IT. The harness client maps its ``new_session``
call onto the router's reset method — the very call the browser path never makes. The
test therefore proves the router resets correctly and says nothing about whether the
shipped web path asks it to.

HOW THIS GATE MEASURES IT. It reads the names the SHIPPED session handlers actually
reference, from their compiled code objects rather than from the source text. An
earlier gate in this tier went green by reading source text and matching the wrong
expression; a code object carries the names the function really uses, and comments and
strings cannot fake it.

WHAT IS NOT MEASURED HERE, STATED PLAINLY: no two real WebSocket clients were
connected and driven. That leg needs a running daemon under a test-owned instance and
is named as unproven residue rather than implied.

EXPECTED TO FAIL ON R001.1 AS SHIPPED.
"""

from __future__ import annotations

import inspect

import pytest

RESET = "reset_conversation_state"
HANDLERS = ["_handle_new_session", "_handle_switch_session"]


def _names_used(func) -> set[str]:
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


@pytest.fixture(scope="module")
def web_server_class(installed_intergen_dir):
    from intergen import web_server
    for _name, obj in vars(web_server).items():
        if inspect.isclass(obj) and all(hasattr(obj, h) for h in HANDLERS):
            return obj
    pytest.fail("Could not find the shipped web server class carrying the session "
                "handlers; this gate must be rewritten against the new shape.")


def test_starting_a_new_session_clears_the_context_the_model_is_shown(web_server_class):
    from intergen.router import ConversationRouter

    if not hasattr(ConversationRouter, RESET):
        pytest.fail(f"The shipped router has no {RESET}; this gate's premise no longer "
                    "holds and it must be rewritten.")

    handler = getattr(web_server_class, "_handle_new_session")
    names = _names_used(handler)

    assert RESET in names, (
        "\nThe browser's new-session handler never asks the router to reset the "
        "conversation.\n"
        f"  names the shipped handler actually references: {sorted(names)}\n"
        f"  the reset it does not call clears: model-facing history, the trust state, "
        "the ingress tracker, and every pending offer.\n"
        "The pane empties and the stored transcript is replaced, so the user sees a "
        "fresh conversation. The model still sees the previous one, and an offer staged "
        "in the discarded conversation can still be accepted by a 'yes' typed in the "
        "new one."
    )


def test_switching_sessions_clears_the_context_the_model_is_shown(web_server_class):
    handler = getattr(web_server_class, "_handle_switch_session")
    names = _names_used(handler)
    assert RESET in names, (
        "\nThe browser's switch-session handler never asks the router to reset the "
        "conversation.\n"
        f"  names the shipped handler actually references: {sorted(names)}\n"
        "Switching to another session loads that session's stored transcript into the "
        "pane while leaving the model's own history untouched, so the model is shown "
        "one conversation and the user is shown another."
    )


def test_each_connection_owns_the_conversation_the_model_is_shown(web_server_class,
                                                                  installed_intergen_dir):
    """The history the model reads must belong to the connection, not to the server.

    Stated narrowly on purpose. The per-connection record DOES carry its own consent
    record and its own ingress tracker — measured, not assumed. What it does not carry
    is the conversation history the model is actually shown, which lives on one router
    instance shared by every connected client.
    """
    from intergen import web_server

    ctx_cls = getattr(web_server, "ConnectionContext", None)
    if ctx_cls is None:
        pytest.fail("The shipped web server has no per-connection context class; this "
                    "gate must be rewritten against the new shape.")

    fields = set(getattr(ctx_cls, "__annotations__", {}))
    owns_model_context = any(
        f in fields for f in ("router", "_router", "conversation_history",
                              "model_history"))

    assert owns_model_context, (
        "\nA connection does not own the conversation the model reads.\n"
        f"  per-connection fields: {sorted(fields)}\n"
        "  'session_history' is the display and persistence copy; the history built "
        "into the model's prompt is held on the shared router.\n"
        "Two browser tabs are one conversation as far as the model is concerned: each "
        "delivered answer is written into the shared history that the prompt builder "
        "reads, so one tab's turns appear in the other tab's context."
    )


def test_the_consent_record_the_routed_path_uses_is_the_per_connection_one(
        installed_intergen_dir):
    """There must be one consent record per conversation, not two with different scopes.

    This release carries two. The per-connection record is passed to the tool
    dispatcher on the user-invoked-tool path. A second record lives on the shared
    router and is the one passed to the dispatcher on every path reached through
    ``route()`` — which is how an ordinary typed turn is served. A grant recorded on
    one is invisible to the other, and only one of them is per-connection.
    """
    from intergen import web_server, router as router_module

    web_text = (installed_intergen_dir / "web_server.py").read_text(
        encoding="utf-8", errors="replace")
    router_text = (installed_intergen_dir / "router.py").read_text(
        encoding="utf-8", errors="replace")

    per_connection_uses = web_text.count("trust_state=ctx.conversation_trust_state")
    shared_uses = router_text.count("trust_state=self._trust_state")

    assert not (per_connection_uses and shared_uses), (
        "\nThe conversation's consent decisions are kept in two separate records with "
        "different lifetimes.\n"
        f"  per-connection record passed to the dispatcher: {per_connection_uses} "
        "call site(s) in the web server\n"
        f"  shared router record passed to the dispatcher : {shared_uses} call site(s) "
        "in the router\n"
        "An ordinary typed turn is served through the router, so its approvals land on "
        "the shared record — the one no browser action resets and every connection "
        "shares. A user who approves a tool 'for this conversation' in one tab has "
        "approved it in every tab, and in every conversation after it."
    )
