"""GATE 7 — conversation-scoped consent reset (section 9 line 6).

WHAT COMPOSITION PROPERTY THIS CATCHES. When a user approves a tool "for this
conversation", the approval is recorded so the review prompt is not shown again for
the same tool and source. The mechanism that records it works and the mechanism that
clears it works. Nothing on the browser path connects the second one to the end of a
conversation, so a grant scoped to one conversation stays in force for every
conversation after it, for the life of the daemon.

This is the consent half of the shared-router defect, and it is failed separately from
the context half because it is a different severity: on a shared machine the person
whose approval is still in force may not be the person now typing.

THE READERS ARE SHARED WITH GATE 6 AND PROVED BOTH WAYS. They live in
_shape_detectors.py beside this file, and the ordinary source-tree suite proves that
each one still reports the defect against a stand-in built to the R001.1 shape and
reports it absent against a fixed tree.

THE GATE PROVES THE MECHANISM WORKS FIRST. A gate that only showed a missing call
could not tell a real gap from a mechanism that never worked. The first test drives
the shipped consent record end to end — record a grant, see the prompt skipped, reset,
see the prompt required again — so the second test's finding is about the wiring and
not about the part being broken.

EXPECTED TO FAIL ON R001.1 AS SHIPPED.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "_igos_shape_detectors", Path(__file__).resolve().parent / "_shape_detectors.py")
shape = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shape)

RESET_ROUTER = shape.RESET
_names_used = shape.names_used


def test_the_consent_record_itself_remembers_and_forgets_correctly(installed_intergen_dir):
    """The control: the shipped consent record behaves as designed when it is used."""
    from intergen.interfaces.provenance import ConversationTrustState

    state = ConversationTrustState()
    assert state.check("run_command", "user") is None, (
        "A fresh consent record already held a decision; the control is invalid.")

    state.remember_decision("run_command", "user", "allow")
    assert state.check("run_command", "user") == "allow", (
        "The shipped consent record did not remember a grant it was given. This gate's "
        "premise is that the mechanism works and the wiring does not; if the mechanism "
        "itself is broken that is a different and larger finding.")

    state.reset()
    assert state.check("run_command", "user") is None, (
        "The shipped consent record did not clear on reset. The remaining tests in "
        "this file assume it does.")


def test_a_conversation_scoped_grant_does_not_survive_a_new_session(installed_intergen_dir):
    """The browser's new-session path must clear the conversation's consent decisions."""
    from intergen import web_server
    from intergen.router import ConversationRouter

    reset_source = inspect.getsource(getattr(ConversationRouter, RESET_ROUTER))
    if not shape.reset_clears_the_consent_record(reset_source):
        pytest.fail(
            f"The router's {RESET_ROUTER} no longer clears the conversation trust "
            "state, so calling it would not settle this property. This gate must be "
            "rewritten against the new shape.")

    cls = next((o for _n, o in vars(web_server).items()
                if inspect.isclass(o) and hasattr(o, "_handle_new_session")), None)
    if cls is None:
        pytest.fail("Could not find the shipped web server class.")

    handler = getattr(cls, "_handle_new_session")
    names = _names_used(handler)
    clears_directly = any(n in names for n in ("reset", "_trust_state",
                                               "ConversationTrustState"))

    assert shape.handler_resets_the_conversation(handler) or clears_directly, (
        "\nA consent grant scoped to one conversation outlives that conversation on the "
        "browser path.\n"
        f"  the router's {RESET_ROUTER} does clear the conversation trust state;\n"
        f"  the browser's new-session handler references: {sorted(names)};\n"
        "  it calls neither that reset nor the trust state directly.\n"
        "The dispatcher skips the review prompt for any tool and source the record "
        "still holds, so an approval given in a discarded conversation silently "
        "authorises the next one — including a conversation started by a different "
        "person at the same machine."
    )


def test_the_only_reset_caller_is_not_a_surface_the_user_reaches(installed_intergen_dir):
    """Name where the reset IS called from, so the gap is legible rather than implied.

    Reported as its own assertion because "the web path does not call it" is only half
    the fact; the other half is that exactly one surface does, and it is not the one the
    release ships as its main interface.
    """
    from intergen import router as router_module
    import pkgutil

    package_dir = installed_intergen_dir
    callers = []
    for path in sorted(package_dir.rglob("*.py")):
        if path.name == "router.py" or "/tests/" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if RESET_ROUTER + "(" in text:
            callers.append(path.relative_to(package_dir).as_posix())

    web_callers = [c for c in callers if c.startswith("web")]
    assert web_callers, (
        "\nThe conversation reset is reachable from exactly these shipped modules:\n"
        + ("\n".join(f"  {c}" for c in callers) if callers
           else "  (none outside router.py itself)") +
        "\nNone of them is the browser interface, which is the surface this release "
        "ships as its main way to use the assistant."
    )
