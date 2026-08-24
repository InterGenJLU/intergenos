# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The installed-system gates for session isolation, proved against both shapes.

WHY THIS FILE EXISTS. Gates 6 and 7 of the installed-system tier decide whether a
real install still carries the shared-conversation defect. They decide it by
reading the shape of the shipped modules — which names a handler's compiled code
references, which fields a connection carries, whether one consent record is used
or two. The fix in this change moves that shape. A gate whose reader is not moved
with it reports on a shape that no longer exists, and a gate whose reader is moved
without proof can report a pass because it stopped being able to see anything.

So the readers live in one module, tests/installed/_shape_detectors.py, and this
file proves each of them BOTH ways:

  * against a stand-in built to the R001.1 shape, every detector must still say
    the defect is present (the true-positive control — a reader that cannot fail
    is not a reader);
  * against this tree's real modules, every detector must say it is absent.

A detector that cannot tell the two apart fails here, in the ordinary suite, and
not silently on an installed machine where the tier is env-gated.
"""

from __future__ import annotations

import importlib.util
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _detectors():
    """Load the gate tier's shared readers by path.

    By path rather than by import, because tests/installed/ is a gate tier with
    its own conftest, not an importable package, and because the file the gates
    actually use is the file this test must measure.
    """
    here = Path(__file__).resolve()
    root = here.parents[2]
    path = root / "tests" / "installed" / "_shape_detectors.py"
    if not path.is_file():
        raise AssertionError(
            f"{path} does not exist. Gates 6 and 7 each carry their own private "
            "copy of the shape readers, so a change to the shape has to be "
            "mirrored into two files by hand and neither copy is proved against "
            "a known-defective input.")
    spec = importlib.util.spec_from_file_location("_igos_shape_detectors", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Stand-ins built to the R001.1 shape (the true-positive controls) ───────

@dataclass
class _R0011ConnectionContext:
    """The shipped R001.1 connection record, field for field as it mattered."""

    client_id: str = ""
    source_interface: str = "web"
    session_history: list = field(default_factory=list)
    current_session_id: str = "default"
    ingress_tracker: Any = None
    conversation_trust_state: Any = None


class _R0011WebServer:
    """The two shipped session handlers as R001.1 wrote them."""

    def _handle_new_session(self, ctx, data):
        if ctx.session_history:
            self._sessions.save(ctx.current_session_id, ctx.session_history,
                                category=data.get("category", ""))
        ctx.current_session_id = "session_new"
        ctx.session_history = []
        self._sessions.create(session_id=ctx.current_session_id)

    def _handle_switch_session(self, ctx, data):
        if ctx.session_history:
            self._sessions.save(ctx.current_session_id, ctx.session_history)
        ctx.current_session_id = data.get("session_id")
        loaded = self._sessions.load(ctx.current_session_id)
        ctx.session_history = loaded.get("messages") or []


def _r0011_reset_source() -> str:
    return (
        "    def reset_conversation_state(self) -> None:\n"
        "        self._trust_state = ConversationTrustState()\n"
        "        self._ingress_tracker.reset_conversation()\n"
        "        self._conversation_history.clear()\n"
    )


_R0011_WEB_TEXT = (
    "                    trust_state=ctx.conversation_trust_state,\n"
    "                            trust_state=ctx.conversation_trust_state,\n"
)
_R0011_ROUTER_TEXT = (
    "                trust_state=self._trust_state,\n"
    "                trust_state=self._trust_state, review_callback=None)\n"
)


def _tree_root() -> Path:
    return Path(__file__).resolve().parents[2]


class TruePositiveControlTests(unittest.TestCase):
    """Every reader must still fail the shape the release actually shipped."""

    def test_the_reset_reader_finds_no_reset_in_the_shipped_handlers(self):
        d = _detectors()
        srv = _R0011WebServer
        self.assertFalse(d.handler_resets_the_conversation(
            getattr(srv, "_handle_new_session")))
        self.assertFalse(d.handler_resets_the_conversation(
            getattr(srv, "_handle_switch_session")))

    def test_the_connection_reader_finds_no_owned_conversation(self):
        d = _detectors()
        self.assertFalse(
            d.connection_owns_the_model_conversation(_R0011ConnectionContext))

    def test_the_consent_reader_finds_two_records(self):
        d = _detectors()
        per_conn, shared = d.consent_record_sites(_R0011_WEB_TEXT,
                                                  _R0011_ROUTER_TEXT)
        self.assertTrue(per_conn and shared,
                        "the reader could not see the two separate records")

    def test_the_premise_reader_accepts_the_shipped_reset(self):
        d = _detectors()
        self.assertTrue(d.reset_clears_the_consent_record(_r0011_reset_source()))


class ThisTreeTests(unittest.TestCase):
    """The same readers, against the modules this change ships."""

    def test_both_session_handlers_reset_the_conversation(self):
        d = _detectors()
        from intergen.web_server import WebServer
        for handler in ("_handle_new_session", "_handle_switch_session"):
            self.assertTrue(
                d.handler_resets_the_conversation(getattr(WebServer, handler)),
                f"{handler} does not ask for the conversation to be reset")

    def test_a_connection_owns_the_conversation_the_model_is_shown(self):
        d = _detectors()
        from intergen.web_server import ConnectionContext
        self.assertTrue(
            d.connection_owns_the_model_conversation(ConnectionContext),
            "the connection still does not carry the model's conversation")

    def test_there_is_one_consent_record(self):
        d = _detectors()
        root = _tree_root()
        web = (root / "intergen" / "web_server.py").read_text(encoding="utf-8")
        router = (root / "intergen" / "router.py").read_text(encoding="utf-8")
        per_conn, shared = d.consent_record_sites(web, router)
        self.assertFalse(per_conn and shared, (
            f"two consent records are still in use: {per_conn} per-connection "
            f"site(s) in the web server and {shared} shared-router site(s)"))

    def test_the_reset_still_clears_the_consent_record(self):
        d = _detectors()
        import inspect
        from intergen.router import ConversationRouter
        src = inspect.getsource(ConversationRouter.reset_conversation_state)
        self.assertTrue(d.reset_clears_the_consent_record(src),
                        "the conversation reset no longer clears the consent "
                        "record, so gates 6 and 7 rest on a false premise")


if __name__ == "__main__":
    unittest.main()
