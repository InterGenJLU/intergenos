# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A page without a token says both ways it can end up without one.

The page's token is set by the panel's document-start user script and by
nothing else, so a browser opened on the page directly never has one. The page
then said "InterGen is not set up on this account yet" — on a machine where
InterGen may be fully set up, sending the person to re-run setup for a problem
setup does not have.

The banner now names both causes: a page opened in a browser directly, and an
account not set up yet. The page still opens no socket without a token.

The case runs the shipped app.js whole under node (web_page_harness.js).
"""

from __future__ import annotations

import shutil
import unittest

from intergen.tests.web_page_harness import DASHBOARD_JS, NEEDS_NODE, run_page


@unittest.skipUnless(shutil.which("node"), NEEDS_NODE)
class TheNoTokenBannerNamesBothCauses(unittest.TestCase):

    def test_a_page_without_a_token_names_both_causes(self):
        record = run_page([], token=None)
        self.assertEqual(len(record["banners"]), 1, record["banners"])
        banner = record["banners"][0]
        self.assertIn("error", banner["kind"])
        self.assertIn("browser opened on it directly", banner["text"],
                      "the banner does not name a page opened in a browser "
                      "directly, the usual way it has no token")
        self.assertIn("intergen setup", banner["text"])
        self.assertEqual(record["sockets"], 0,
                         "a page without a token opened a socket")

    def test_control_a_page_with_a_token_shows_no_banner(self):
        record = run_page([])
        self.assertEqual(record["banners"], [])
        self.assertEqual(record["sockets"], 1)


@unittest.skipUnless(shutil.which("node"), NEEDS_NODE)
class TheDashboardWithoutATokenSaysSo(unittest.TestCase):
    """The operations dashboard, with no token from the panel and none handed
    over by the chat page, made one up and connected with it: the server
    refused it, and the dashboard showed nothing and said nothing. It now says
    why, as the chat page does, and opens no socket. dashboard.js runs whole
    under node, as app.js does."""

    def test_a_dashboard_without_a_token_says_why_and_opens_no_socket(self):
        record = run_page([], token=None, script=DASHBOARD_JS, by_id=True)
        self.assertEqual(record["sockets"], 0,
                         "the dashboard connected with a token it made up")
        self.assertEqual(len(record["banners"]), 1, record["banners"])
        self.assertIn("browser opened on it directly",
                      record["banners"][0]["text"])
        self.assertIn("intergen setup", record["banners"][0]["text"])

    def test_control_a_dashboard_with_a_token_connects(self):
        record = run_page([], script=DASHBOARD_JS, by_id=True)
        self.assertEqual(record["sockets"], 1)
        self.assertEqual(record["banners"], [])


if __name__ == "__main__":
    unittest.main()
