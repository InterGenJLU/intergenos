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

from intergen.tests.web_page_harness import NEEDS_NODE, run_page


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


if __name__ == "__main__":
    unittest.main()
