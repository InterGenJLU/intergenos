# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""manage_packages argument extraction — specific patterns outrank loose ones.

Pins the ordering fix for a live-captured defect: "what version of the
intergen package is currently installed?" contains both "installed" and
"package", the loose LIST co-occurrence test matched first, pkm listed the
entire corpus, and the model narrated a package COUNT as the answer to a
VERSION question — a true answer to a question nobody asked, plausible and
unfalsifiable from the transcript. The version regex now runs before the
LIST heuristic; these tests are the discriminating pair that must never
regress in either direction.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from intergen.router import ConversationRouter  # noqa: E402


def _extract(text):
    router = ConversationRouter.__new__(ConversationRouter)
    return router._extract_arguments("manage_packages", text)


class TestVersionQuestionOutranksListHeuristic:
    def test_version_question_with_installed_and_package_is_info(self):
        """The captured defect sentence, verbatim."""
        args = _extract(
            "what version of the intergen package is currently installed?")
        assert args == {"action": "info", "package": "intergen"}

    def test_version_question_variant_without_currently(self):
        args = _extract("what version of the intergen package is installed")
        assert args == {"action": "info", "package": "intergen"}

    def test_version_question_without_list_keywords_still_info(self):
        """The phrasing that always worked must keep working."""
        args = _extract("what version of the kernel package")
        assert args == {"action": "info", "package": "kernel"}

    def test_plain_list_question_still_lists(self):
        """The other direction: a genuine list ask must NOT become info."""
        args = _extract("what packages are installed")
        assert args == {"action": "list"}

    def test_list_phrasings_unaffected(self):
        for phrase in ("list installed packages",
                       "what packages do I have"):
            assert _extract(phrase) == {"action": "list"}, phrase

    def test_info_on_still_works(self):
        args = _extract("info on intergen")
        assert args == {"action": "info", "package": "intergen"}
