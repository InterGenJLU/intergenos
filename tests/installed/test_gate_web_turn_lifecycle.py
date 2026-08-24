"""GATE 4 — browser/WebSocket turn lifecycle against a slow server (section 9 line 3).

WHAT COMPOSITION PROPERTY THIS CATCHES. The browser arms a single deadline for the
whole turn and the server arms its own deadline for one phase of that turn. Nothing
tells the browser that its turn has been received before routing finishes. When
routing takes longer than the browser's deadline — which happens whenever the turn
arrives during the startup embedding backlog — the browser reports that the assistant
did not respond, drops the reply and force-closes the socket while the server is still
working. The two deadlines are in different languages in different files, so no
source-tree test compares them.

WHAT THIS GATE MEASURES AND WHAT IT DOES NOT. It reads the shipped constants and the
shipped ordering out of the INSTALLED files and asserts the relationship between them.
It does NOT drive a real browser against a deliberately slowed server; that leg needs a
headless browser and a controllable embed endpoint and is named as unproven residue in
the delivery rather than silently omitted.

EXPECTED TO FAIL ON R001.1 AS SHIPPED.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "_wtl_shape", Path(__file__).resolve().parent / "_shape_detectors.py")
shape = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shape)


@pytest.fixture(scope="module")
def web_files(installed_intergen_dir):
    app_js = installed_intergen_dir / "web" / "app.js"
    server = installed_intergen_dir / "web_server.py"
    llama = installed_intergen_dir / "llama_manager.py"
    llm = installed_intergen_dir / "llm.py"
    for p in (app_js, server, llama, llm):
        if not p.is_file():
            pytest.fail(f"{p} is absent from the installed package; the turn "
                        "lifecycle cannot be characterised.")
    return {
        "app_js": app_js.read_text(encoding="utf-8"),
        "server": server.read_text(encoding="utf-8"),
        "llama": llama.read_text(encoding="utf-8"),
        "llm": llm.read_text(encoding="utf-8"),
    }


def _one(pattern: str, text: str, what: str) -> str:
    found = re.findall(pattern, text)
    if not found:
        pytest.fail(f"Could not read {what} out of the installed file; the gate "
                    "cannot report a number it did not measure.")
    return found[0]


def test_the_server_declares_a_routing_deadline_inside_the_client_failsafe(web_files):
    """Routing must be bounded, and bounded shorter than the browser's failsafe.

    RE-KEYED 2026-08-24, and the reason matters more than the change. The earlier
    form compared the browser's ``RESPONSE_TIMEOUT_MS`` against the embedding
    call's deadline and against the model request's deadline, on the premise that
    the browser arms ONE deadline covering the whole turn. That premise has moved.
    The shipped client now arms that constant only until the server acknowledges
    the turn, then re-arms a longer one (``POST_ACK_TIMEOUT_MS``); a streaming
    model request no longer sits inside it at all, because the first content frame
    disarms it. Comparing it to the model request deadline therefore reported a
    defect the shape had been changed to remove, while saying nothing about what
    now actually bounds routing.

    What bounds routing is the server's own declared deadline. The property is the
    one the shipped design states in ``intergen/web/app.js`` and enforces in
    ``intergen/tests/test_turn_lifecycle_contract.py``: the server declares a
    routing deadline, and it is strictly shorter than the client's failsafe, so
    the server gives up and says so before the browser concludes it has died.

    This is NOT a weakening. On R001.1 as shipped there is no declared routing
    deadline at all — routing is unbounded — so this fails, and it fails naming
    the real cause instead of an arithmetic comparison against a deadline that no
    longer covers the same work.
    """
    client_ms = int(_one(r"RESPONSE_TIMEOUT_MS\s*=\s*(\d+)", web_files["app_js"],
                         "the browser's pre-acknowledgement failsafe"))
    client_s = client_ms / 1000.0

    declared = re.findall(r"^SERVER_ROUTE_DEADLINE_S\s*=\s*([\d.]+)",
                          web_files["server"], re.MULTILINE)
    assert declared, (
        "\nThe shipped web server declares no routing deadline.\n"
        f"  the browser's pre-acknowledgement failsafe: {client_s:g}s\n"
        "  the server's routing deadline             : none declared\n"
        "Routing runs unbounded. When it outlasts the browser's failsafe the user "
        "is told the assistant did not respond, the reply is discarded and the "
        "socket is force-closed while the server is still working on that turn. "
        "Nothing on the server ever gives up and says so, because nothing on the "
        "server is counting."
    )

    route_s = float(declared[0])
    assert route_s < client_s, (
        "\nThe server's routing deadline does not fit inside the browser's "
        "failsafe:\n"
        f"  the browser's pre-acknowledgement failsafe: {client_s:g}s\n"
        f"  the server's routing deadline             : {route_s:g}s\n"
        "The browser gives up first, so the user is told the assistant did not "
        "respond by a timer that fires before the server's own timer can report "
        "what went wrong."
    )


def test_the_server_acknowledges_a_turn_before_it_finishes_routing(web_files):
    """Something the client can use must reach it before the slow phase begins.

    RE-KEYED 2026-08-24. The earlier form found the routing call with the text
    pattern ``self._router.route(`` and then scanned the source lines between the
    handler and that call for a ``send_json`` that was not an error frame.
    MEASURED against the current tree: the routing call is now handed to an
    executor, so the router's ``route`` never appears in callee position, the
    pattern matched nothing, and the gate took its own "must be rewritten against
    the new shape" branch — on a tree that sends exactly the acknowledgement this
    gate exists to require. It would have reported a corrected defect as still
    present, and blamed the ordering rather than its own reader.

    Two things also had to stop being assumed. The acknowledgement need not live
    in the function that routes — the shipped one is sent by the caller, above the
    call into it. And a frame's line is the line of the SEND, not of whatever
    ``try:`` or ``with`` encloses it; measuring by the enclosing statement's line
    counted the final response frame as though it preceded routing.

    Both halves are asserted. A frame the browser has no arm for would leave its
    failsafe running exactly as if nothing had been sent.
    """
    try:
        acknowledgements = shape.acknowledgement_before_routing(web_files["server"])
    except shape.ShapeNotRecognised as exc:
        pytest.fail(
            f"This gate could not characterise the shipped turn path ({exc}). "
            "That is a statement about this reader, not about the installed "
            "system: it must be rewritten against the new shape rather than "
            "reporting a verdict it did not measure.")

    assert acknowledgements, (
        "\nNothing reaches the browser between the turn arriving and routing "
        "beginning.\n"
        f"  routing is entered at: {shape.routing_call_sites(web_files['server'])}\n"
        "  non-error frames sent before that point: none\n"
        "The routing phase — which includes the embedding call the browser's "
        "failsafe is racing — therefore runs with a timer nothing can disarm. The "
        "browser gives up, tells the user the assistant did not respond, and "
        "force-closes the socket while the server is still working on the turn."
    )

    unhandled = [t for t in acknowledgements
                 if not shape.client_dispatches(web_files["app_js"], t)]
    assert len(unhandled) < len(acknowledgements), (
        "\nThe server sends an acknowledgement the shipped browser does not read:\n"
        f"  frame types sent before routing: {acknowledgements}\n"
        f"  types the browser has no dispatch arm for: {unhandled}\n"
        "An acknowledgement the client ignores leaves its failsafe armed exactly "
        "as if the frame had never been sent, so the turn still ends in a reported "
        "failure while the server is still working."
    )


def test_the_browser_timer_has_a_terminal_frame_on_every_path(web_files):
    """A turn that ends without a terminal frame leaves the browser waiting.

    Reported as its own assertion rather than folded into the two above, because it is
    the property a fix has to preserve: whatever the deadlines become, every turn must
    end with a frame the client recognises as terminal.
    """
    app_js = web_files["app_js"]
    if "RESPONSE_TIMEOUT_MS" not in app_js:
        pytest.fail("The browser has no response deadline constant; this gate's "
                    "premise no longer holds and it must be rewritten.")
    disarm_sites = len(re.findall(r"clearTimeout\(", app_js))
    assert disarm_sites >= 1, (
        "The browser arms a response deadline and never clears it anywhere in the "
        "shipped app.js; every turn would report a failure."
    )
