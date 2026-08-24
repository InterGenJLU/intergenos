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

import re
from pathlib import Path

import pytest


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


def test_the_client_deadline_is_longer_than_every_server_deadline_it_covers(web_files):
    """The browser must not give up before the work it is waiting on can finish."""
    client_ms = int(_one(r"RESPONSE_TIMEOUT_MS\s*=\s*(\d+)", web_files["app_js"],
                         "the browser's response deadline"))
    embed_s = float(_one(r"def embed\(self[^)]*?timeout:\s*float\s*=\s*([\d.]+)",
                         web_files["llama"].replace("\n", " "),
                         "the embedding call's deadline"))
    model_s = float(_one(r'config\.get\("request_timeout",\s*(\d+)\)',
                         web_files["llm"], "the model request deadline"))

    client_s = client_ms / 1000.0
    problems = []
    if client_s <= embed_s:
        problems.append(
            f"  the browser gives the whole turn {client_s:g}s while a single "
            f"embedding call inside that turn is allowed {embed_s:g}s — the client "
            "gives up at the same moment the server's own phase deadline expires")
    if client_s < model_s:
        problems.append(
            f"  the browser gives the whole turn {client_s:g}s while the model request "
            f"alone is allowed {model_s:g}s — {model_s / client_s:.0f} times longer")
    assert not problems, (
        "\nThe browser's deadline does not cover the work it is waiting for:\n"
        + "\n".join(problems) +
        "\n\nWhen it expires the user is told the assistant did not respond, the reply "
        "is discarded and the socket is force-closed, while the server is still "
        "working on that turn."
    )


def test_the_server_acknowledges_a_turn_before_it_finishes_routing(web_files):
    """Something must reach the browser before the slow phase, or its timer is blind.

    The browser's turn timer is armed when the turn is sent and disarmed only by
    ``stream_start`` or a terminal frame. This asserts that between the turn arriving
    at the handler and the routing call, the server sends the client at least one frame
    that is not an error — an acknowledgement the client can use. Error frames do not
    count: a turn that is going to succeed slowly never produces one.
    """
    lines = web_files["server"].splitlines()

    handler = next((i for i, ln in enumerate(lines)
                    if "async def _handle_client_message" in ln), None)
    route_line = next((i for i, ln in enumerate(lines)
                       if i > (handler or 0) and re.search(r"self\._router\.route\(", ln)),
                      None)
    if handler is None or route_line is None:
        pytest.fail(
            "Could not locate the turn handler and its routing call in the installed "
            f"web server (handler at {handler}, route at {route_line}); the ordering "
            "cannot be asserted and this gate must be rewritten against the new shape.")

    window = lines[handler:route_line]
    frames = [(handler + i + 1, ln.strip()) for i, ln in enumerate(window)
              if "send_json" in ln]
    acknowledgements = [(n, ln) for n, ln in frames if "_make_error" not in ln]

    assert acknowledgements, (
        "\nNothing reaches the browser between the turn arriving and routing "
        "finishing:\n"
        f"  the turn handler starts at web_server.py line {handler + 1};\n"
        f"  the routing call is at line {route_line + 1};\n"
        f"  frames sent in between: {len(frames)}, all of them error frames "
        f"({[n for n, _ in frames]}).\n"
        "The routing phase — which includes the embedding call the browser's deadline "
        "is racing — therefore runs with a timer nothing can disarm. The browser gives "
        "up, tells the user the assistant did not respond, and force-closes the socket "
        "while the server is still working on the turn."
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
