# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""NO CODE IN THIS DIRECTORY REACHES A LIVE MODEL SERVER UNLESS IT SAYS SO.

THE DEFECT THIS CLOSES. Cases here build a real router around a real model
client and replace the model by hand, one entry point at a time. The ladder has
more than one: the free-form rung calls the client's completion method, the
native tier's tools rung calls its streaming method, and the step that turns a
tool result into a sentence calls the completion method again from a third
place. A case that replaced one of them left the others pointing at whatever
model server the machine running the suite happened to have up. Measured with
the boundary armed to record and refuse: sixteen cases and sub-cases in three
files that all believed they had stubbed the model reached it during one run of
this directory, twelve from the synthesis step after a dispatch and four from
the free-form rung, all through the completion method. A fourth file had already
been corrected by hand, and its single escaping request carried the real tool
schemas, so a live answer could have chosen a tool during a unit test.

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

WHAT THE BOUNDARY COVERS, AND WHY IT IS NOT A PER-CASE FIXTURE. A case is not
the only thing that runs here. A unittest `setUpClass` runs before any of its
cases' own fixtures, and module-level code runs at collection, before any case
exists at all; a boundary armed only for the duration of each case leaves both
of those reaching whatever the machine has up. Measured on the first version of
this guard, which was a function-scoped fixture: a request from `setUpClass`
was neither refused nor named, and reached the socket layer. So the boundary is
installed when THIS FILE is imported — which happens before any module in this
directory is imported — and it stays installed: what changes per case is only
whether it is armed. A request made while one of this directory's cases is
running is judged by that case's permissions; a request made outside a case by
code that lives in this directory, at collection or at import, is refused with
the same refusal; a request from anywhere else in the suite is not this
directory's business and passes untouched.

THE TWO PERMISSIONS, AND WHAT THEY ARE NOT. Both are registered markers
(pytest.ini) and both are visible, greppable, reviewable acts.

`reaches_a_server_it_starts_itself` — the case starts its OWN server and polls
it. That is not reaching for a shared model; it is talking to a process it owns
and will stop.

`reaches_the_machines_embedding_server(<VARIABLE>)` — the case is one of this
directory's own opt-in gates, which measure the SOURCE tree against a real
embedding server the installed tier does not carry. It is honoured ONLY while
that gate's own opt-in variable is set to 1, so the gate reaches the machine's
embedding server exactly when someone asks for it by name on the command line,
and is refused like anything else on every routine run. The variable belongs in
the marker, beside the class it applies to, rather than in a list here that
would age out of step with the gates.

Neither marker is a way to keep a case that wants the machine's model server for
an ordinary unit test: such a case is not a unit test, and the tier for live
surfaces is tests/installed.
"""

from __future__ import annotations

import os
import sys
import urllib.request

OPT_OUT_MARKER = "reaches_a_server_it_starts_itself"
EMBEDDING_GATE_MARKER = "reaches_the_machines_embedding_server"

_THIS_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
_REAL_URLOPEN = urllib.request.urlopen

# The case this directory is running, or None between cases, during collection,
# and while the rest of the suite runs.
_RUNNING = {"item": None}


class ModelServerReachedInAUnitTest(BaseException):
    """Deliberately not an Exception — see the module docstring.

    Raised at the moment the connection is opened, naming what opened it and
    the address, so the run fails where the defect is rather than minutes later
    on a socket timeout or, worse, silently on a machine with nothing listening.
    """


def permission_for(item) -> str | None:
    """What lets this case through the boundary, named, or None if nothing does.

    Pure apart from reading the environment, so a case can assert the rule
    directly instead of inferring it from a run.
    """
    if item.get_closest_marker(OPT_OUT_MARKER) is not None:
        return f"marked {OPT_OUT_MARKER}"
    mark = item.get_closest_marker(EMBEDDING_GATE_MARKER)
    if mark is not None:
        variable = mark.args[0] if mark.args else None
        if variable and os.environ.get(variable) == "1":
            return f"marked {EMBEDDING_GATE_MARKER} with {variable}=1"
    return None


def request_is_this_directory_s(frame=None, depth: int = 40) -> bool:
    """True when a frame of the code making this request lives in this directory.

    Used only for a request made while no case of this directory is running: at
    collection, at module import, or while another directory's tests run. The
    frame walk is what tells this directory's own code apart from the rest of
    the suite, which this boundary leaves alone; it looks through the chain
    rather than at the immediate caller alone, because module-level code here
    can reach a server through the product, several frames down.

    The bound is stated rather than hidden: the walk gives up after ``depth``
    frames, so a request made by this directory's code deeper than that, with no
    case running, would pass. Nothing in this directory is built that way today.
    The frame is a parameter so a case can judge a known one instead of
    inferring the rule from its own stack, which always contains this directory.
    """
    frame = sys._getframe(1) if frame is None else frame
    seen = 0
    while frame is not None and seen < depth:
        filename = frame.f_globals.get("__file__") or ""
        if filename and os.path.dirname(os.path.abspath(filename)) == _THIS_DIRECTORY:
            return True
        frame = frame.f_back
        seen += 1
    return False


def _refuse(request, *args, **kwargs):
    item = _RUNNING["item"]
    if item is not None:
        permission = permission_for(item)
        if permission is not None:
            return _REAL_URLOPEN(request, *args, **kwargs)
        opened_by = item.nodeid
    else:
        if not request_is_this_directory_s():
            return _REAL_URLOPEN(request, *args, **kwargs)
        opened_by = (f"code in {_THIS_DIRECTORY} running outside any case "
                     f"(class-level setup, module import or collection)")
    address = getattr(request, "full_url", None) or repr(request)
    raise ModelServerReachedInAUnitTest(
        f"{opened_by} opened {address}. Nothing in this directory may reach a "
        f"running server: replace the model entry point the code takes — the "
        f"completion method, the streaming method, or both — or, if the case "
        f"starts the server itself, mark it {OPT_OUT_MARKER}, or, if it is one "
        f"of this directory's opt-in embedding gates, mark it "
        f"{EMBEDDING_GATE_MARKER} with its own variable. Code that runs outside "
        f"a case has no marker to carry and must not reach out at all. See "
        f"intergen/tests/conftest.py."
    )


urllib.request.urlopen = _refuse


def pytest_runtest_setup(item):
    """Arm the boundary for this case before anything of it is set up.

    This hook runs before the item's own setup, class-level fixtures included,
    which is why a `setUpClass` request is now judged by its class's markers
    rather than escaping the way it did while this was a per-case fixture.
    """
    _RUNNING["item"] = item


def pytest_runtest_logfinish(nodeid, location):
    """The case is over, teardown included: nothing of this directory is running."""
    _RUNNING["item"] = None
