# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every web turn that delivers bytes to the user records those bytes.

The M1 mandate is that every byte of processing is reconstructible from the
trace alone — including "the final bytes delivered to chat". The web surface
disposes of a turn in three places:

  * the FAST path, gated on an explicit `result.source` allowlist — emits
    ``delivery/final`` and runs the M8-2 unconsumed-dispatch invariant;
  * the STREAMED path (`llm_tools` / `llm_freeform`) — emits its own
    ``delivery/final`` with a self-declared linkage;
  * the FALLBACK, which catches every OTHER source.

The fallback is the one that delivers to the user and historically recorded
nothing: a turn routed via e.g. ``direct_answer`` sent a real answer over the
socket and left only a ``route/turn_start`` behind. Measured on a live box
(2026-07-25): a ``direct_answer`` web turn returned "You have 187G free on the
root filesystem" to the client and produced exactly one glass record. The
delivered bytes were unreconstructible, and ``find_unconsumed_dispatches`` —
the substituted-result check — never ran for that whole class.

These tests hold the invariant at the DISPOSITION level rather than for one
route name, so a future route that lands in the fallback cannot silently
reintroduce the gap.
"""

from __future__ import annotations

import asyncio
import unittest

from intergen import web_server
from intergen.web_server import ConnectionContext, WebServer
from intergen.interfaces.types import AnswerLinkage, RouteResult


class _FakeWS:
    """Minimal WS double: records what the server sent to the client."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


class _FakeRouter:
    """Returns one canned RouteResult; records history appends."""

    def __init__(self, result: RouteResult) -> None:
        self._result = result
        self.appended: list[tuple[str, str]] = []

    def route(self, user_msg, decide_only=False, review_callback=None, **kw):
        return self._result

    def last_route_confidence(self):
        return None

    def _append_history(self, user_msg: str, delivered: str, *,
                        state=None) -> None:
        # `state` names the conversation the exchange belongs to: the real
        # server writes back after the route lock is released.
        self.appended.append((user_msg, delivered))


class _GlassRecorder:
    """Captures glass.emit calls without touching the real trace file."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, phase, event, detail=None, dur_ms=None, **kw):
        self.calls.append((phase, event, dict(detail or {})))

    def finals(self) -> list[dict]:
        return [d for phase, event, d in self.calls
                if phase == "delivery" and event == "final"]


class _WebDeliveryHarness(unittest.TestCase):
    """Drives one real _handle_client_message turn against doubles."""

    def _run_turn(self, result: RouteResult,
                  content: str = "How much disk space is free?"):
        server = WebServer()
        router = _FakeRouter(result)
        server._router = router
        # _persist_and_list needs a session manager; the turn under test is the
        # delivery disposition, not persistence.
        async def _noop_persist(ctx):
            return None
        server._persist_and_list = _noop_persist

        ws = _FakeWS()
        ctx = ConnectionContext(client_id="t", source_interface="web", ws=ws)

        recorder = _GlassRecorder()
        real_emit = web_server.glass.emit
        web_server.glass.emit = recorder
        try:
            asyncio.run(server._handle_client_message(ctx, {"content": content}))
        finally:
            web_server.glass.emit = real_emit
        return ws, recorder, router


class FallbackDispositionRecordsDelivery(_WebDeliveryHarness):
    """The fallback delivers to the user, so it must record what it delivered."""

    def test_fallback_source_emits_delivery_final(self):
        # `direct_answer` is in NEITHER the fast-path allowlist nor the streamed
        # pair, so it lands in the fallback — the measured real-world case.
        ws, recorder, _ = self._run_turn(
            RouteResult(text="You have 187G free on the root filesystem.",
                        source="direct_answer", handled=True))

        sent = [m for m in ws.sent if m.get("type") == "response"]
        self.assertTrue(sent, "the fallback did not answer the client at all")
        self.assertIn("187G", sent[0]["content"])

        finals = recorder.finals()
        self.assertEqual(
            len(finals), 1,
            "a web turn delivered bytes to the user and recorded no "
            "delivery/final — those bytes are unreconstructible from the trace")
        self.assertEqual(finals[0]["iface"], "web")
        self.assertEqual(finals[0]["source"], "direct_answer")
        self.assertIn("187G", finals[0]["text"])

    def test_fallback_records_delivered_text_not_raw_route_text(self):
        # A re-offer reminder is folded into the DELIVERED text; the trace has to
        # carry what the user actually received, not the pre-fold route text.
        ws, recorder, _ = self._run_turn(
            RouteResult(text="Firefox is installed.", source="direct_answer",
                        handled=True,
                        reoffer_reminder="That upgrade offer is still standing."))
        finals = recorder.finals()
        self.assertEqual(len(finals), 1)
        self.assertIn("still standing", finals[0]["text"],
                      "the trace recorded the route text, not the delivered text")
        sent = [m for m in ws.sent if m.get("type") == "response"][0]
        self.assertEqual(sent["content"], finals[0]["text"],
                         "what the client got and what the trace holds diverged")

    def test_fallback_declares_undeclared_rather_than_defaulting_to_code(self):
        # An absent linkage is recorded AS absent. Defaulting it to `code` would
        # put an unverified claim on the trace.
        _, recorder, _ = self._run_turn(
            RouteResult(text="ok", source="capability_question", handled=True))
        link = recorder.finals()[0]["answer_linkage"]
        self.assertEqual(link, {"kind": "undeclared"})

    def test_fallback_carries_a_real_linkage_when_the_route_declares_one(self):
        _, recorder, _ = self._run_turn(
            RouteResult(text="ok", source="ip_answer", handled=True,
                        answer_linkage=AnswerLinkage(kind="code",
                                                     renderer="ip_selector")))
        link = recorder.finals()[0]["answer_linkage"]
        self.assertEqual(link["kind"], "code")
        self.assertEqual(link["renderer"], "ip_selector")

    def test_foreign_object_in_the_linkage_slot_does_not_speak(self):
        # Parity with the fast path and the D-Bus surface: only a genuine
        # AnswerLinkage may speak for an answer.
        bogus = RouteResult(text="ok", source="explain", handled=True)
        object.__setattr__(bogus, "answer_linkage", {"kind": "dispatch"})
        _, recorder, _ = self._run_turn(bogus)
        self.assertEqual(recorder.finals()[0]["answer_linkage"],
                         {"kind": "undeclared"})


class FastAndStreamedDispositionsUnchanged(_WebDeliveryHarness):
    """The two paths that already recorded must keep recording exactly once."""

    def test_fast_path_still_emits_exactly_one_final(self):
        _, recorder, _ = self._run_turn(
            RouteResult(text="It is 7pm.", source="keyword", handled=True))
        finals = recorder.finals()
        self.assertEqual(len(finals), 1)
        self.assertTrue(finals[0]["fast_path"])

    def test_fallback_final_is_marked_not_fast_path(self):
        # The two dispositions stay distinguishable in the trace.
        _, recorder, _ = self._run_turn(
            RouteResult(text="ok", source="safety_decline", handled=True))
        self.assertFalse(recorder.finals()[0]["fast_path"])


class DeliveryDetailKeysSurviveRedaction(unittest.TestCase):
    """A trace field the M1 mandate needs must not be credential-shaped.

    ``glass._SECRET_KEY_RE`` matches "token" as a SUBSTRING. A delivery detail
    keyed ``tokens`` — a plain streamed-chunk COUNT — was therefore written as
    ``"<redacted:tokens>"``, so the count could not be reconstructed from the
    trace. Observed live on 2026-07-26 in a real web streamed turn. The fix is
    the key name, never a weakened redactor.
    """

    def test_secret_key_rule_still_catches_real_credential_shapes(self):
        from intergen import glass
        for key in ("password", "api_key", "auth_token", "bearer",
                    "credential", "private_key", "passphrase", "keyring",
                    "authorization"):
            self.assertTrue(glass._SECRET_KEY_RE.search(key),
                            f"the credential rule stopped catching {key!r}")

    def test_streamed_delivery_detail_keys_are_not_credential_shaped(self):
        from intergen import glass
        # The exact detail keys the streamed delivery record emits.
        for key in ("iface", "text", "source", "streamed", "stream_chunks",
                    "tool_calls", "answer_linkage"):
            self.assertIsNone(
                glass._SECRET_KEY_RE.search(key),
                f"delivery detail key {key!r} is credential-shaped and will be "
                f"redacted out of the trace")

    def test_the_old_key_name_would_have_been_redacted(self):
        # Guards the reason for the rename: this is why it must not go back.
        from intergen import glass
        self.assertIsNotNone(glass._SECRET_KEY_RE.search("tokens"))
        self.assertEqual(glass._redact(39, "tokens"), "<redacted:tokens>")
        self.assertEqual(glass._redact(39, "stream_chunks"), 39)


if __name__ == "__main__":
    unittest.main()
