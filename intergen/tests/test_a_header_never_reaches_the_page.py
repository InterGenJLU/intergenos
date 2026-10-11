# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A request's Authorization header never reaches the panel's page.

The index handler used to write the Authorization header's value into an
inline script that set window.__INTERGEN_TOKEN__: with no nonce, so the page's
own content security policy (script-src 'self') refused it in a browser, and
with no check of the value and no escaping, so a value carrying markup closed
the script element and opened another, the policy being all that kept it from
running. The handler now writes no script for a header at all: the panel
window's token reaches the page by its own document-start user script, and a
browser the item signed in holds a session of its own, kept by the page's
script in its sessionStorage, so the page is served no inline script and never
carries the token.

These cells pin that removal. Each serves a real WebServer on a free loopback
port with a token file of its own; nothing here touches the panel's own
address or the account's token.
"""
from __future__ import annotations

import re
import unittest

from intergen.tests import test_the_browser_item_opens_a_signed_in_panel as item

TOKEN = item.TOKEN
OTHER_TOKEN = item.OTHER_TOKEN
MARKUP = "</script><script>window.__header_case_ran__ = 1;</script>"


def _inline_scripts(html: str) -> list[str]:
    """Every script element of the page that is not a file the server serves."""
    return [tag for tag in re.findall(r"<script\b[^>]*>", html, re.I)
            if not re.search(r"\bsrc\s*=", tag, re.I)]


class AHeaderIsServedNoToken(unittest.TestCase):

    def page_for(self, value: str) -> tuple[int, str]:
        panel = item._Panel(self)
        status, _, html, _ = panel.run(lambda session: panel.get(
            session, "/", headers={"Authorization": f"Bearer {value}"}))
        return status, html

    def test_the_accounts_own_token_in_a_header_is_served_no_token(self):
        status, html = self.page_for(TOKEN)
        self.assertEqual(status, 200)
        self.assertNotIn(TOKEN, html)
        self.assertNotIn("__INTERGEN_TOKEN__", html)
        self.assertEqual(_inline_scripts(html), [])

    def test_a_wrong_token_in_a_header_is_served_none_of_it(self):
        status, html = self.page_for(OTHER_TOKEN)
        self.assertEqual(status, 200)
        self.assertNotIn(OTHER_TOKEN, html)
        self.assertEqual(_inline_scripts(html), [])

    def test_markup_in_a_header_is_not_written_into_the_page(self):
        status, html = self.page_for(MARKUP)
        self.assertEqual(status, 200)
        self.assertNotIn("__header_case_ran__", html)
        self.assertEqual(_inline_scripts(html), [])


class WithTheBrowserItemsSessionAHeaderStillReachesNothing(unittest.TestCase):

    def signed_in_page(self, header_value: str | None,
                       account_token: str = TOKEN):
        """The page served, with an Authorization header, to a browser the
        item signed in, and the session that browser was given; a header value
        of None presents that session itself."""
        panel = item._Panel(self, token=account_token)

        async def scenario(session):
            browser_session = await panel.sign_in(session, account_token)
            value = browser_session if header_value is None else header_value
            return browser_session, await panel.get(
                session, "/", headers={"Authorization": f"Bearer {value}"})

        browser_session, (status, _, html, _) = panel.run(scenario)
        return browser_session, status, html

    def test_the_page_carries_no_script_and_none_of_the_header(self):
        _, status, html = self.signed_in_page(MARKUP)
        self.assertEqual(status, 200)
        self.assertNotIn("__header_case_ran__", html)
        self.assertEqual(_inline_scripts(html), [])
        self.assertNotIn(TOKEN, html)

    def test_a_wrong_token_in_the_header_is_served_none_of_it(self):
        _, status, html = self.signed_in_page(OTHER_TOKEN)
        self.assertEqual(status, 200)
        self.assertNotIn(OTHER_TOKEN, html)
        self.assertNotIn(TOKEN, html)
        self.assertEqual(_inline_scripts(html), [])

    def test_the_session_in_a_header_is_served_no_credential(self):
        browser_session, status, html = self.signed_in_page(None)
        self.assertEqual(status, 200)
        self.assertNotIn(browser_session, html)
        self.assertNotIn(TOKEN, html)
        self.assertEqual(_inline_scripts(html), [])

    def test_an_account_token_carrying_markup_never_reaches_the_page(self):
        # An account token carrying markup would close a script element the
        # page carried it in; the page carries no token at all.
        hostile = "0123456789abcdef</script><script>window.__token_case_ran__=1</script>"
        _, status, html = self.signed_in_page(TOKEN, account_token=hostile)
        self.assertEqual(status, 200)
        self.assertNotIn("__token_case_ran__", html)
        self.assertNotIn(hostile, html)
        self.assertEqual(_inline_scripts(html), [])


if __name__ == "__main__":
    unittest.main()
