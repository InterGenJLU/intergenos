# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The answer the web page keeps is the text it streamed, or the final frame
says why it is not.

The defect, recorded on 2026-09-20 in a web conversation on an installed
machine: the page showed one answer while it typed and kept a different one.
The streamed tokens read "Yes. I am currently running on a smaller model ..."
and the turn's terminal frame carried an unrelated sentence about the memory
in the machine. Nothing in the frames said that the answer had changed, or
why, and the page replaces what it showed with the terminal frame's text.

The cause is not a bug in one screen. After the tokens are sent, several
screens may rewrite the delivered answer: the one that carries a command's
result into an answer that dropped it, the one that removes a claim of an
action nobody performed, the one that removes an offer to act that nothing can
carry out, and the ones that handle a command named in the answer that does
not exist or cannot be checked. Each is a correct thing to do; doing it
silently is what leaves a person reading an answer that was exchanged for
another in front of them.

What is pinned here, driven through the real message handler with a real
router whose route() returns a fixed verdict and whose model client is faked:

  * a turn nothing rewrites keeps exactly what it streamed, and says so;
  * a turn whose draft is rewritten carries a plain-language reason;
  * a rewrite that cannot be made by the model still carries its reason;
  * the blank-reply nudge is streamed, so it is not a replacement;
  * every terminal frame satisfies one contract: the kept answer equals the
    concatenated stream, or the frame states why it does not;
  * the page reads the reason and shows it as text.
"""

from __future__ import annotations

import asyncio
import pathlib
import unittest

from intergen.interfaces.types import Message, MessageRole, RouteResult
from intergen.router import ConversationRouter
from intergen.web_server import ConnectionContext, WebServer

_WEB = pathlib.Path(__file__).resolve().parents[1] / "web"


class _RecordingWS:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, payload):
        self.sent.append(payload)

    def of_type(self, type_name):
        return [m for m in self.sent if m.get("type") == type_name]


class _ChatReply:
    def __init__(self, text: str):
        self.text = text


class _FakeLLM:
    _endpoint = "http://127.0.0.1:8080/v1/chat/completions"

    def __init__(self, tokens, chat_text=""):
        self._tokens = list(tokens)
        self._chat_text = chat_text
        self.prompts: list[list[Message]] = []

    def build_system_messages(self, query_type="general", with_tools=True):
        return [Message(role=MessageRole.SYSTEM, content="system prompt")]

    def stream_with_tools(self, messages, tools=None, image_data=None,
                          max_tokens=None):
        self.prompts.append(list(messages))
        yield from self._tokens

    def chat(self, messages, max_tokens=None):
        return _ChatReply(self._chat_text)


def _router_with_verdict(llm, verdict: RouteResult) -> ConversationRouter:
    r = ConversationRouter.__new__(ConversationRouter)
    r._max_history = 20
    r._record = lambda *a, **k: None
    r._current_query_type = "general"
    r._memory = None
    r._embedder = None
    r._llm = llm
    r._last_semantic_score = None
    r.detach_conversation()
    r.route = lambda user_input, **kw: verdict
    return r


def _turn(tokens, chat_text="", question="What should I do about it?"):
    """Run one streamed turn and return (ws, streamed_text)."""
    llm = _FakeLLM(tokens, chat_text=chat_text)
    router = _router_with_verdict(
        llm, RouteResult(text="", source="llm_freeform", handled=False))
    server = WebServer(router=router, llm=llm, tools=None, governance=None)
    ctx = ConnectionContext(client_id="c1", source_interface="web",
                            ws=_RecordingWS(),
                            conversation=router.new_conversation())
    asyncio.run(server._handle_client_message(
        ctx, {"type": "message", "content": question}))
    streamed = "".join(m.get("token", "") for m in ctx.ws.of_type("stream_token"))
    return ctx.ws, streamed


def _terminal(ws) -> dict:
    ends = ws.of_type("stream_end")
    assert len(ends) == 1, ws.sent
    return ends[0]


def _assert_frame_explains_itself(case, ws, streamed):
    """The one contract: what is kept is what was streamed, or the frame says
    why it is not — and it never claims a replacement with no reason."""
    end = _terminal(ws)
    case.assertIn("replaced_streamed_text", end)
    case.assertIn("replacement_reason", end)
    if end["full_response"] == streamed:
        case.assertFalse(end["replaced_streamed_text"], end)
        case.assertIsNone(end["replacement_reason"], end)
    else:
        case.assertTrue(end["replaced_streamed_text"], end)
        case.assertTrue((end["replacement_reason"] or "").strip(), end)


# A draft that offers to perform an action nothing on a toolless turn can
# carry out — the screen that removes it is the one the recorded conversation
# tripped.
_SELF_OFFER = ["I can ", "set that up for you. ",
               "Just let me know and I'll take care of it."]
_CLEAN_REWRITE = ("You can change that yourself in the settings panel. "
                  "I am not able to change it from here.")


class NothingRewrittenTests(unittest.TestCase):

    def test_the_kept_answer_is_the_streamed_answer(self):
        ws, streamed = _turn(["The system logs ", "are under /var/log."])
        end = _terminal(ws)
        self.assertEqual(end["full_response"], streamed)
        self.assertEqual(end["replaced_streamed_text"], False)
        self.assertIsNone(end["replacement_reason"])
        _assert_frame_explains_itself(self, ws, streamed)

    def test_the_blank_reply_nudge_is_streamed_not_substituted(self):
        ws, streamed = _turn([])
        end = _terminal(ws)
        self.assertTrue(streamed.strip(), ws.sent)
        self.assertEqual(end["full_response"], streamed)
        self.assertEqual(end["replaced_streamed_text"], False)
        _assert_frame_explains_itself(self, ws, streamed)


class RewrittenAnswerTests(unittest.TestCase):

    def test_a_rewritten_answer_carries_its_reason(self):
        ws, streamed = _turn(_SELF_OFFER, chat_text=_CLEAN_REWRITE)
        end = _terminal(ws)
        self.assertEqual(streamed, "".join(_SELF_OFFER))
        self.assertEqual(end["full_response"], _CLEAN_REWRITE)
        self.assertNotEqual(end["full_response"], streamed)
        self.assertEqual(end["replaced_streamed_text"], True)
        self.assertIn("offered to perform an action",
                      end["replacement_reason"])
        _assert_frame_explains_itself(self, ws, streamed)

    def test_a_rewrite_the_model_cannot_make_still_carries_its_reason(self):
        # The regeneration comes back offering again, so the deterministic
        # honest line is delivered instead — a replacement all the same.
        ws, streamed = _turn(_SELF_OFFER,
                             chat_text="Want me to install it for you?")
        end = _terminal(ws)
        self.assertNotEqual(end["full_response"], streamed)
        self.assertEqual(end["replaced_streamed_text"], True)
        self.assertIn("could not be written again", end["replacement_reason"])
        _assert_frame_explains_itself(self, ws, streamed)

    def test_the_reason_is_plain_language_not_an_internal_marker(self):
        ws, _streamed = _turn(_SELF_OFFER, chat_text=_CLEAN_REWRITE)
        reason = _terminal(ws)["replacement_reason"]
        for shorthand in ("M7", "M3", "M4", "M8-2", "violation",
                          "screen_model_text_offer"):
            self.assertNotIn(shorthand, reason)


class ThePageShowsTheReasonTests(unittest.TestCase):
    """The frame is only half the fix: the page that replaces the text is what
    a person is looking at."""

    def test_the_page_reads_the_flag_and_shows_the_reason_as_text(self):
        app = (_WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("msg.replaced_streamed_text", app)
        self.assertIn("addReplacementNote(msg.replacement_reason)", app)
        note = app.split("function addReplacementNote(")[1].split("\n  }")[0]
        self.assertIn("textContent", note)
        self.assertNotIn("innerHTML", note)

    def test_the_note_has_a_style_of_its_own(self):
        css = (_WEB / "style.css").read_text(encoding="utf-8")
        self.assertIn(".replacement-note", css)


if __name__ == "__main__":
    unittest.main()
