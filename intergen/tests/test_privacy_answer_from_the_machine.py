# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
""""does this assistant send my data anywhere?" answered from the machine.

The phrasing matched no privacy alias, so the question reached the model, which
answered in generalities. The privacy answer the aliases did reach was a fixed
text saying that no query, response or system data is ever sent anywhere, and
"do you run locally" said that no data leaves the machine unless cloud
escalation is configured. A web search the person asks for sends its search
words out, so both were false on every machine with web search.

The direct-answer route now answers every privacy phrasing from the machine's
own facts: the model serving the router, whether web search is registered, which
Phone-A-Friend providers are configured; and the two fixed texts no longer say
that nothing is ever sent.
"""

from __future__ import annotations

import unittest

from intergen.router import ConversationRouter
from intergen.tests.test_security_posture_answers import _ask, _router


class PrivacyAnswer(unittest.TestCase):

    def test_the_privacy_alias(self):
        res = _ask(_router(), "does this assistant send my data anywhere?")
        self.assertIsNotNone(res, "the privacy alias was not answered")
        self.assertEqual(res.source, "direct_answer")
        self.assertIn("TestModel-2B", res.text)
        self.assertIn("no telemetry", res.text)
        self.assertIn("web search", res.text)
        self.assertIn("No Phone-A-Friend provider is set up", res.text)

    def test_the_answer_names_the_machine_facts(self):
        text = _router()._privacy_answer()
        self.assertIn("TestModel-2B", text)
        self.assertIn("no telemetry", text)
        self.assertIn("web search", text)
        self.assertIn("No Phone-A-Friend provider is set up", text)

    def test_a_configured_provider_is_named(self):
        text = _router(providers=["example-cloud"])._privacy_answer()
        self.assertIn("example-cloud", text)
        self.assertIn("only after you approve", text)
        self.assertNotIn("No Phone-A-Friend provider is set up", text)

    def test_without_web_search_none_is_promised(self):
        text = _router(web=False)._privacy_answer()
        self.assertNotIn("web search sends", text)

    def test_an_unknown_model_is_not_named(self):
        text = _router(model=None)._privacy_answer()
        self.assertIn("the model that answers you runs here", text)

    def test_every_privacy_alias_reaches_it(self):
        for text in ("does this assistant send my data anywhere?",
                     "do you send my data", "is my data sent anywhere",
                     "is my data private", "where does my data go",
                     "what about privacy", "does intergen have telemetry?"):
            with self.subTest(text=text):
                res = _ask(_router(), text)
                self.assertIsNotNone(res)
                self.assertIn("no telemetry", res.text)

    def test_an_instruction_to_send_is_not_answered_with_the_privacy_facts(self):
        self.assertIsNone(_ask(_router(), "can you send my data to my email"))

    def test_the_identity_texts_no_longer_say_nothing_is_ever_sent(self):
        for key in ("what about privacy", "do you run locally"):
            with self.subTest(key=key):
                text = ConversationRouter._try_self_awareness(key)
                self.assertNotIn("no queries", text.lower())
                self.assertNotIn("unless you explicitly configure", text.lower())
                self.assertIn("web search", text)


if __name__ == "__main__":
    unittest.main()
