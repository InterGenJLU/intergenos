# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The terminal console's consent prompt names only keys the console acts on.

When the provenance gate holds a tool dispatch and the terminal console is the
surface, the server sends a `gate_prompt` frame and the console renders its own
card. That card tells the person, in the imperative, which keys decide:

    Press 'a' to allow, 'y' to allow conversation, 'n' to deny

A consent surface is a trust surface: an instruction it prints about how to
authorize has to be an instruction the program can carry out. This case pins
exactly that property, and nothing narrower — for every key the shipped card
names, the console must ACT on that key, either

  * as a binding in `_build_keybindings()`, or
  * along the fallback path an unbound character actually takes: the character
    lands in the input buffer and Enter hands it to `_on_user_input`, so a
    console that decided the gate there would also satisfy the promise.

Because the requirement is stated over the card's OWN text, it holds under
either honest repair: bind the keys the card names, or reword the card to the
one control the console really has (Esc, deny). It fails only while the card
names a key that decides nothing — the state in which a person who presses the
key the program told them to press does not allow the action, and the single
character is handed to the assistant as the next thing they said.
"""

from __future__ import annotations

import asyncio
import io
import re
import unittest
from contextlib import redirect_stderr
from unittest import mock

# A gate_prompt as intergen/web_server.py builds one (_render_review_card).
GATE_PROMPT = {
    "type": "gate_prompt",
    "turn_id": "turn-1",
    "tool_call_id": "tc-1",
    "action": '{"command": "systemctl restart some-unit"}',
    "tool_name": "run_command",
    "provenance": {"classification": "user_direct"},
    "governance_check": None,
    "risk_tier": "privileged_state_changing",
    "blocked_by": "provenance_gate",
}

# Every "'<key>' to allow/deny" the card prints, in the card's own words.
_KEY_NAMED = re.compile(r"'([^']+)'\s+to\s+(?:allow|deny)")


def _no_models():
    """Keep the shell's startup attribution off the model manifest."""
    from intergen.model_manager import ModelManager
    return mock.patch.object(ModelManager, "list_downloaded", return_value=[])


class _RecordingClient:
    """Stands in for ConsoleClient. Records instead of touching a socket."""

    connected = True

    def __init__(self, log: list) -> None:
        self._log = log

    async def send_message(self, content: str) -> None:
        self._log.append(("chat_to_the_assistant", content))

    async def send_gate_decision(self, tool_call_id: str,
                                 decision: str) -> None:
        self._log.append(("gate_decision", tool_call_id, decision))

    async def send(self, data: dict) -> None:
        self._log.append(("frame", data))


def _new_shell():
    """A ConsoleShell built off-terminal. prompt_toolkit writes 'Input is not a
    terminal' to stderr when built under pytest; it is captured so it cannot
    pollute a suite capture and says nothing about this behaviour."""
    from intergen.console import shell
    with _no_models(), redirect_stderr(io.StringIO()):
        return shell.ConsoleShell()


def _bound_keys(sh) -> set[str]:
    """The keys `_build_keybindings()` actually binds, as the strings a person
    presses ('a'), with prompt_toolkit's named keys spelled out ('escape')."""
    keys: set[str] = set()
    for binding in sh._build_keybindings().bindings:
        for key in binding.keys:
            keys.add(getattr(key, "value", None) or str(key))
    return keys


def _card_text(sh) -> str:
    """The gate card the console renders for the frame above."""
    asyncio.run(sh._process_server_message(dict(GATE_PROMPT)))
    gate = [m for m in sh._messages if m.get("source") == "gate"]
    assert gate, f"the console rendered no gate card: {sh._messages!r}"
    return gate[-1]["content"]


def _where_the_key_goes(sh, key: str) -> tuple[bool, str]:
    """Press `key` at the console while the gate is pending.

    Returns (did it reach the gate, what happened instead).
    """
    bound = _bound_keys(sh)
    if key in bound:
        return True, f"{key!r} is bound in _build_keybindings()"

    # Unbound: BufferControl's default insert binding puts the character in the
    # input buffer, and the Enter binding hands the buffer to _on_user_input.
    # Drive that real path and record where the keystroke ends up.
    log: list = []
    sh._client = _RecordingClient(log)
    sh._gate_pending = dict(GATE_PROMPT)
    asyncio.run(sh._on_user_input(key))
    if any(entry[0] == "gate_decision" for entry in log):
        return True, f"{key!r} decided the gate through _on_user_input: {log!r}"
    return False, (f"{key!r} is bound to nothing, and pressing it then Enter "
                   f"produced {log!r} — no decision reached the server")


class TheConsoleGateCardTests(unittest.TestCase):
    """intergen/console/shell.py — the card at line 614 and the keys it names."""

    def test_the_gate_prompt_names_only_keys_the_console_binds(self):
        sh = _new_shell()
        card = _card_text(sh)
        named = _KEY_NAMED.findall(card)

        unusable: list[str] = []
        detail: list[str] = []
        for key in named:
            acted, how = _where_the_key_goes(_new_shell(), key)
            detail.append(how)
            if not acted:
                unusable.append(key)

        self.assertEqual(
            unusable, [],
            "the console's consent card instructs the person to press keys the "
            "console does not act on.\n"
            f"  card:  {card!r}\n"
            f"  named: {named!r}\n"
            f"  bound: {sorted(_bound_keys(sh))!r}\n"
            "  " + "\n  ".join(detail),
        )


if __name__ == "__main__":
    unittest.main()
