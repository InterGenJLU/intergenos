# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A question asked in another script is routed as its English form is.

THE DEFECT, read on the tree in intergen/howto.py. When the embedding server is
unavailable, the curated how-to corpus scores a question by word overlap, and
its reader took words as runs of a-z and 0-9 only. Every word written in
another script was dropped before scoring, so the one Latin word such a
question usually carries was all that was left of it: "我用的是什么 shell？"
(which shell am I using) read as "shell", the trigger "how do I run a shell
script" covered the whole of that, and the condition that refuses a question
the corpus has never heard of read the question as fully known. The explain
route then served the run-a-script snippet as the answer. Measured on the
shipped corpus: 10 of 12 such questions (Chinese, Japanese, Korean, Russian,
Greek, Arabic and Hindi, each carrying one Latin word) were served a curated
answer on this path; the embedding path served none of them, and none of their
English forms.

THE RULE these cells hold the corpus and the router to: a letter the reader's
alphabet does not cover is read as part of a word the corpus does not know. A
run of such letters is one word and an ideograph is a word on its own, since
the scripts that use ideographs put no spaces between words. A question
written mostly in another script is then mostly unknown to the corpus, so the
keyword path refuses it as the embedding path does, and the router hands it
to the model's turn as it hands the English form.

THE CONTROLS beside the cells are green at both ends: an English question is
read exactly as before (every shipped trigger and every English form here
yields the same words under the reader as under the a-z reader), the English
forms are routed as before, and a how-to question still gets its curated
answer on the keyword path.

The corpus is the tree's own (intergen/data/howto), never a copy installed on
the machine running the cells. Each router case builds the router the way the
daemon builds it with no embedding server; the model is stubbed at both entry
points and counted with the request it was asked, the tool registry's dispatch
is stood in and recorded, and every way a turn can leave the process (a child
process, a socket, urllib) is recorded and refused for the turn.
"""
from __future__ import annotations

import os
import re
import socket
import unittest
from unittest import mock

from intergen import howto
from intergen.howto import HowtoCorpus
from intergen.interfaces.types import HardwareTierLevel, ToolResult
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry

MODEL_ANSWER = "(a model's answer)"
CAPTURED = "我用的是什么 shell？"
# Each question of the class beside its English form.
CLASS = (
    ("我用的是什么 shell？", "What shell am I using?"),
    ("我的 firefox 是什么版本？", "What version is my firefox?"),
    ("我的 wifi 连上了吗？", "Is my wifi connected?"),
    ("bluetooth 开着吗？", "Is bluetooth on?"),
    ("我装的 python 是哪个版本？", "Which python version do I have installed?"),
    ("我的 firewall 开着吗？", "Is my firewall on?"),
    ("私の shell は何ですか？", "What is my shell?"),
    ("내 shell 은 뭐야?", "What's my shell?"),
    ("какой у меня shell?", "Which shell do I have?"),
    ("ποιο shell χρησιμοποιώ;", "Which shell am I using?"),
    ("ما هو shell الذي أستخدمه؟", "What is the shell I am using?"),
    ("मैं कौन सा shell उपयोग कर रहा हूँ?", "Which shell am I using?"),
)
_AZ = re.compile(r"[a-z0-9]+")


def _corpus() -> HowtoCorpus:
    """The tree's own corpus with no embedder: the keyword path."""
    return HowtoCorpus(embedder=None, data_dir=howto._REPO_DIR)


class _BoundaryReached(BaseException):
    """Deliberately NOT an Exception: the model client's ``except Exception``
    paths would swallow an ordinary one and the case would walk on with the
    request already made."""


class _Resp:
    """A completion carrying only what the free-form path reads."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.quality_passed = True
        self.escalated = False
        self.local = True
        self.model = "stub"
        self.tokens_prompt = 0
        self.tokens_completion = 0
        self.semantic_flags = []


def _last_user(messages) -> str:
    """The text of the last user message a model request carried."""
    for m in reversed(list(messages or [])):
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
        if getattr(role, "value", role) == "user":
            content = (m.get("content") if isinstance(m, dict)
                       else getattr(m, "content", ""))
            return content if isinstance(content, str) else repr(content)
    return ""


class _RouterCase(unittest.TestCase):
    """The router with no embedding server, and one turn."""

    def _router(self) -> ConversationRouter:
        from intergen.intents import register_all_intents
        reg = ToolRegistry()
        reg.discover_tools()
        self.dispatched: list[tuple[str, dict]] = []

        def _execute(call, *a, **kw):
            self.dispatched.append((call.name, dict(call.arguments or {})))
            return ToolResult(call_id=call.call_id, name=call.name,
                              content="(the stand-in's output)", success=True)

        reg.execute = _execute
        matcher = SemanticMatcher(embedder=None)
        register_all_intents(matcher)
        with mock.patch.dict(os.environ, {"INTERGEN_HOWTO_DIR": str(howto._REPO_DIR)}):
            r = ConversationRouter(
                tool_registry=reg, semantic_matcher=matcher, llm=LLMRouter(config=None),
                lock_dispatch=False, hardware_tier=HardwareTierLevel.TIER_2)
        self.assertIsNotNone(r._howto, "control: the router built no how-to corpus")
        self.assertIsNone(r._howto._embeddings,
                          "control: the corpus built an embedding index, so this "
                          "case would not measure the keyword path")
        self.asked: list[tuple[str, str]] = []

        def _chat(messages, **kw):
            self.asked.append(("chat", _last_user(messages)))
            return _Resp(MODEL_ANSWER)

        def _stream_with_tools(messages, *, tools=(), **kw):
            self.asked.append(("stream_with_tools", _last_user(messages)))
            yield MODEL_ANSWER

        r._llm.chat = _chat
        r._llm.stream_with_tools = _stream_with_tools
        return r

    def _turn(self, r: ConversationRouter, request: str):
        self.processes: list[str] = []
        self.connections: list[str] = []
        self.urls: list[str] = []

        def _popen(args, *a, **kw):
            self.processes.append(args if isinstance(args, str)
                                  else " ".join(map(str, args)))
            raise OSError("a child process, refused by this case")

        def _connect(sock, address, *a, **kw):
            self.connections.append(repr(address))
            raise OSError("a connection, refused by this case")

        def _urlopen(req, *a, **kw):
            self.urls.append(getattr(req, "full_url", repr(req)))
            raise _BoundaryReached(self.urls[-1])

        with mock.patch("subprocess.Popen", _popen), \
                mock.patch.object(socket.socket, "connect", _connect), \
                mock.patch("urllib.request.urlopen", _urlopen):
            return r.route(request)


# ── the cells: red before the correction ────────────────────────────────────

class OnTheKeywordPathTheCorpusRefusesAQuestionItCannotRead(unittest.TestCase):

    def setUp(self):
        self.corpus = _corpus()

    def assertRefused(self, question: str) -> None:
        for strong in (True, False):
            entry, score = self.corpus.retrieve(question, strong=strong)
            self.assertIsNone(
                entry, "%r was served %r at score %.3f (strong=%s)"
                % (question, getattr(entry, "id", None), score, strong))

    def test_the_captured_question(self) -> None:
        self.assertRefused(CAPTURED)

    def test_every_question_of_the_class(self) -> None:
        for question, _english in CLASS[1:]:
            with self.subTest(question=question):
                self.assertRefused(question)


class TheRouterHandsTheCapturedQuestionToTheModel(_RouterCase):

    def test_with_no_embedding_server(self) -> None:
        r = self._router()
        result = self._turn(r, CAPTURED)
        self.assertNotEqual(result.source, "explain",
                            "%r was answered by the explain route: %r"
                            % (CAPTURED, result.text))
        self.assertEqual(self.asked, [("stream_with_tools", CAPTURED)])
        self.assertEqual(self.dispatched, [])
        self.assertEqual((self.processes, self.connections, self.urls), ([], [], []))


# ── the controls: green at both ends ────────────────────────────────────────

class AnEnglishQuestionIsReadAsBefore(unittest.TestCase):

    def test_every_shipped_trigger_and_every_english_form(self) -> None:
        corpus = _corpus()
        texts = list(corpus._trigger_texts) + [english for _q, english in CLASS]
        self.assertGreater(len(corpus._trigger_texts), 400,
                           "control: the shipped corpus did not load")
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual(
                    HowtoCorpus._content_words(text),
                    {w for w in _AZ.findall(text.lower()) if w not in howto._STOPWORDS})

    def test_a_how_to_question_still_gets_its_curated_answer(self) -> None:
        entry, _score = _corpus().retrieve("how do I run a shell script")
        self.assertEqual(getattr(entry, "id", None), "user-how-do-i-run-a-shell-script")


class TheRouterStillTeachesOnTheKeywordPath(_RouterCase):

    def test_how_do_i_run_a_shell_script(self) -> None:
        r = self._router()
        result = self._turn(r, "how do I run a shell script")
        self.assertEqual(result.source, "explain")
        self.assertEqual(self.asked, [])
        self.assertEqual(self.dispatched, [])

    def test_the_english_form_of_the_captured_question(self) -> None:
        r = self._router()
        result = self._turn(r, "What shell am I using?")
        self.assertNotEqual(result.source, "explain")
        self.assertEqual(self.asked, [("stream_with_tools", "What shell am I using?")])


if __name__ == "__main__":
    unittest.main()
