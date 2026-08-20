# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Template selection must key on the user's WORDS, not on substrings of them.

Reported class: `search for a pdf editor` was answered "Disk usage is available."
The package-search keyword intent matched by design, the package tool executed,
and then template selection tested `"df" in lower` — which is true inside
"p-df" — so a package listing was rendered through the disk summariser. The
turn's trace still named the package tool the delivered answer never came from.

That is one instance of a class: every bare-substring token in the selection
table can be captured by an unrelated word. These fixtures pin the class in both
directions — the false captures must not fire, and every legitimate diagnostic
recall must still answer. A fix that quietened the leaks by narrowing the
templates would fail the second half.
"""
import unittest

from intergen.router import ConversationRouter


DF_OUTPUT = (
    "Filesystem      Size  Used Avail Use% Mounted on\n"
    "/dev/sda1       100G   50G   50G  50% /\n"
)
FREE_OUTPUT = "              total        used        free\nMem:           32G         8G         24G\n"
PKG_OUTPUT = (
    "pdfarranger  1.11.0   available\n"
    "okular       24.08.1  available\n"
    "xournalpp    1.2.3    available\n"
)


class SubstringCaptureTests(unittest.TestCase):
    """The reported defect and its siblings — none may select a template."""

    def _assert_no_template(self, query, output=DF_OUTPUT):
        got = ConversationRouter._template_synthesis(query, output)
        self.assertIsNone(
            got,
            f"{query!r} must not select a diagnostic template (got {got!r}); "
            "selecting one renders an unrelated result through the wrong "
            "summariser and leaves the trace naming a tool the answer never "
            "came from")

    def test_the_reported_turn_pdf_editor_search(self):
        """PKG-dispatch-04: `search for a pdf editor` answered as disk usage."""
        self._assert_no_template("search for a pdf editor", PKG_OUTPUT)

    def test_df_inside_pdf(self):
        for q in ("convert this pdf", "open the pdf", "email me the pdf"):
            with self.subTest(q=q):
                self._assert_no_template(q)

    def test_ram_inside_ordinary_words(self):
        for q in ("what program should I use", "draw a diagram",
                  "install telegram"):
            with self.subTest(q=q):
                self._assert_no_template(q, FREE_OUTPUT)

    def test_free_inside_freedesktop(self):
        self._assert_no_template("is freedesktop running", FREE_OUTPUT)

    def test_space_inside_namespace_workspace_whitespace(self):
        for q in ("what is a namespace", "switch workspace",
                  "strip the whitespace"):
            with self.subTest(q=q):
                self._assert_no_template(q)

    def test_ip_inside_script_zip_clipboard_recipe(self):
        for q in ("write a script", "unzip this", "copy to clipboard",
                  "give me a recipe"):
            with self.subTest(q=q):
                self._assert_no_template(q, "203.0.113.10")

    def test_core_inside_hardcore(self):
        self._assert_no_template("is this hardcore mode", "32")

    def test_time_inside_sometimes_timeout_runtime(self):
        for q in ("sometimes it fails", "what is a timeout",
                  "what is the runtime"):
            with self.subTest(q=q):
                self._assert_no_template(q, "12:04:11")

    def test_host_inside_ghost(self):
        self._assert_no_template("what is a ghost process", "box-01")

    def test_block_inside_blockchain(self):
        for q in ("what is blockchain", "unblock this user"):
            with self.subTest(q=q):
                self._assert_no_template(q)

    def test_active_inside_inactive_does_not_invert_a_status_answer(self):
        """The nastiest sibling: "active" matches inside "inactive"."""
        got = ConversationRouter._template_synthesis(
            "why is it inactive", "inactive (dead)")
        self.assertNotEqual(
            got, "Yes, it's running. inactive (dead)",
            "a question containing 'inactive' must never be answered "
            "'Yes, it's running'")


class PrecisionControlTests(unittest.TestCase):
    """Recall must not regress — the half that fails if the leaks are fixed by
    narrowing the templates rather than by matching whole words."""

    def test_disk_recall_survives(self):
        for q in ("what is my disk usage", "how much free space do I have",
                  "how much storage is left", "what does df say"):
            with self.subTest(q=q):
                got = ConversationRouter._template_synthesis(q, DF_OUTPUT)
                self.assertIsNotNone(got, f"{q!r} must still reach the disk summary")
                self.assertIn("Disk", got)

    def test_memory_recall_survives(self):
        for q in ("how much memory do I have", "show me ram usage",
                  "how much free memory is there"):
            with self.subTest(q=q):
                got = ConversationRouter._template_synthesis(q, FREE_OUTPUT)
                self.assertIsNotNone(got, f"{q!r} must still reach the memory summary")

    def test_single_value_recall_survives(self):
        cases = [
            ("what kernel am I running", "6.18.10-igos-8", "kernel"),
            ("what is my hostname", "box-01", "hostname"),
            ("what is my uptime", "2 days", "uptime"),
            ("how many cores do I have", "32", "core"),
        ]
        for q, out, marker in cases:
            with self.subTest(q=q):
                got = ConversationRouter._template_synthesis(q, out)
                self.assertIsNotNone(got, f"{q!r} must still be answered")
                self.assertIn(out, got)

    def test_possessive_and_quantity_forms_survive(self):
        """The forms named explicitly as must-not-regress."""
        for q, out in (("what is MY disk usage", DF_OUTPUT),
                       ("how much memory", FREE_OUTPUT),
                       ("what kernel am I running", "6.18.10-igos-8")):
            with self.subTest(q=q):
                self.assertIsNotNone(
                    ConversationRouter._template_synthesis(q, out))

    def test_punctuation_does_not_break_whole_word_matching(self):
        """Word boundaries, not .split() — 'disk?' and 'df,' must still match."""
        for q in ("what about disk?", "run df, please"):
            with self.subTest(q=q):
                self.assertIsNotNone(
                    ConversationRouter._template_synthesis(q, DF_OUTPUT))


if __name__ == "__main__":
    unittest.main()
