# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""NO CASE IN THIS DIRECTORY REACHES A LIVE MODEL SERVER UNLESS IT SAYS SO.

THE DEFECT THIS CLOSES. Cases here build a real router around a real model
client and replace the model by hand, one entry point at a time. The ladder has
more than one: the free-form rung calls the client's completion method, the
native tier's tools rung calls its streaming method, and the step that turns a
tool result into a sentence calls the completion method again from a third
place. A case that replaced one of them left the others pointing at whatever
model server the machine running the suite happened to have up. Measured on a
workstation with a server listening: sixteen requests left this directory during
one run of it, from three case files that all believed they had stubbed the
model, every one of them from the synthesis step after a dispatch. A fourth file
had already been corrected by hand, and its single escaping request carried the
real tool schemas, so a live answer could have chosen a tool during a unit test.

WHY A DIRECTORY GUARD AND NOT FOUR MORE HAND STUBS. Each of those files was
written in good faith and each was wrong in the same way, which makes it a
property of the directory rather than of any author. The stubs stay — they are
what makes a case deterministic — and this is the floor under them, set once so
a case written next year cannot quietly reach a server either. It follows the
pattern the project-root conftest already uses for privileged escalation: the
local discipline still applies, and the floor catches what the discipline misses.

WHY urllib.request.urlopen AND NOTHING ELSE. Every HTTP path in the model client
goes through that one function: the streaming completion, the tool-calling
stream, the follow-up after a tool call and the embedding request all build a
request and hand it there. Arming that single name therefore states the whole
claim, and states it against the client as it is rather than against a list of
method names that a refactor would age out of.

WHY IT REFUSES WITH SOMETHING THAT IS NOT AN Exception. Every one of those call
sites wraps its request in `except Exception`, logs the failure and returns an
empty stream; the caller reads an empty stream as an unanswered rung and walks
on. A guard that raised an ordinary exception would be swallowed, the case would
carry on down the ladder, and it would PASS with the connection already made.
Measured: a first version of this guard, written that way, reported a clean run
for a case that had already opened a socket. The class below inherits from
BaseException for that reason and for that reason only.

THE OPT-OUT, AND WHAT IT IS NOT. A case that starts its OWN server and then
polls it is not reaching for a shared model; it is talking to a process it owns
and will stop. Those cases carry the `reaches_a_server_it_starts_itself` marker
(registered in pytest.ini), which is a visible, greppable, reviewable act. The
marker is not a way to keep a case that wants the machine's model server: such a
case is not a unit test, and the tier for live surfaces is tests/installed.
"""

from __future__ import annotations

import urllib.request

import pytest

OPT_OUT_MARKER = "reaches_a_server_it_starts_itself"


class ModelServerReachedInAUnitTest(BaseException):
    """Deliberately not an Exception — see the module docstring.

    Raised at the moment a case opens a connection, naming the case and the
    address, so the run fails where the defect is rather than minutes later on
    a socket timeout or, worse, silently on a machine with nothing listening.
    """


@pytest.fixture(autouse=True)
def no_live_model_server(request):
    """Arm the model client's one HTTP boundary for the duration of each case."""
    if request.node.get_closest_marker(OPT_OUT_MARKER) is not None:
        yield
        return

    real_urlopen = urllib.request.urlopen

    def refuse(req, *args, **kwargs):
        address = getattr(req, "full_url", None) or repr(req)
        raise ModelServerReachedInAUnitTest(
            f"{request.node.nodeid} opened {address}. A case in this directory "
            f"must not reach a running server: replace the model entry point "
            f"the case takes — the completion method, the streaming method, or "
            f"both — or, if the case starts the server itself, mark it "
            f"{OPT_OUT_MARKER}. See intergen/tests/conftest.py."
        )

    urllib.request.urlopen = refuse
    try:
        yield
    finally:
        urllib.request.urlopen = real_urlopen
