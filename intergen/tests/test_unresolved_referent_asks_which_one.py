# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A clause whose referent nothing resolved asks which one, in our own words.

THE MEASURED CASE, 2026-08-27, on the 2B at intergen r241. "find a pdf
editor and install it" split correctly into two clauses. Clause 1 searched and
found NOTHING — "No packages matching 'pdf editor'". Clause 2, "install it",
therefore had no referent to resolve: _resolved_referent() returned "",
_extract_arguments took its documented decline, and nothing was dispatched.

That decline is right. What happened next is not. The router's own comment at
that decline says the point of declining is that "the turn asks which package".
NOTHING ASKS. The clause falls to the model, and on this run the model answered:
"To find a PDF editor, you can install the `pdfelement` package using `pkm`."
It named a package no search had found, and presented an install command for it.
So a promised clarify that has no implementation is filled in by an invented
package name — a recovery path with no caller, and the gap taken by fabrication.

This is the sibling of the refused-dispatch fix in r241, one decline reason
over. There, a tool RAN
and refused and its refusal was replaced by model prose; here, nothing ran and
the clarify that should have taken its place was replaced by model prose. The
principle is the same in both: when the code has decided what the honest answer
is, the model must not be asked to invent one instead.

WHY THE CLARIFY IS CODE-OWNED. The wording is a product decision and it must be
identical every time, because it is the sentence that tells a person the machine
found nothing and needs them to name something. A model-generated version can
name a package, and on this evidence does.

WHAT IS DELIBERATELY UNCHANGED. Every other route to arguments_indeterminate
still falls through exactly as it did. This is not "declines now answer"; it is
"a clause that declined BECAUSE ITS REFERENT COULD NOT BE RESOLVED answers",
which is a state the code already identifies and already acts on.
"""

from __future__ import annotations

import unittest


class TheGapIsRecordedWhenItHappens(unittest.TestCase):
    """The decline already knows why; nothing wrote it down."""

    def test_the_router_exposes_the_referent_gap(self):
        from intergen.router import ConversationRouter

        self.assertTrue(
            hasattr(ConversationRouter, "_referent_gap")
            or "_referent_gap" in getattr(ConversationRouter, "__annotations__", {})
            or hasattr(ConversationRouter, "_take_referent_gap"),
            "nothing on the router records that a decline was caused by an "
            "unresolvable referent, so the rung cannot tell that case apart "
            "from any other indeterminate argument")

    def test_every_unresolved_referent_site_records_the_gap(self):
        """All three, not just the install one.

        The install branch is the measured case, but "is it installed" and
        "restart it" decline for exactly the same reason and their comments make
        exactly the same promise. Fixing one and leaving two is how a rule
        becomes a special case.
        """
        import ast
        import inspect
        import textwrap

        from intergen.router import ConversationRouter

        src = textwrap.dedent(
            inspect.getsource(ConversationRouter._extract_arguments))
        tree = ast.parse(src)

        # A referent-gap site is precisely: `if not X:` returning None, where
        # X was assigned from a referent resolution (_resolved_referent or
        # _scan_service_name). Naming the resolution rather than pattern-matching
        # `if not …` keeps this off the write_file fail-safe a few lines away,
        # which declines for a DIFFERENT reason (no path or no content) and has
        # its own documented remedy — asking what to write, not which package.
        resolved_names = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            calls = [n for n in ast.walk(node.value) if isinstance(n, ast.Call)]
            if not any(isinstance(c.func, ast.Attribute)
                       and c.func.attr in ("_resolved_referent",
                                           "_scan_service_name")
                       for c in calls):
                continue
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    resolved_names.add(tgt.id)

        self.assertTrue(
            resolved_names,
            "no variable in _extract_arguments is assigned from a referent "
            "resolution; re-point this test")

        gap_sites = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            body = node.body
            if not body or not isinstance(body[-1], ast.Return):
                continue
            v = body[-1].value
            if v is not None and not (isinstance(v, ast.Constant)
                                      and v.value is None):
                continue
            test = node.test
            if not (isinstance(test, ast.UnaryOp)
                    and isinstance(test.op, ast.Not)
                    and isinstance(test.operand, ast.Name)
                    and test.operand.id in resolved_names):
                continue
            gap_sites.append((ast.unparse(test), body))

        self.assertGreaterEqual(
            len(gap_sites), 3,
            f"expected at least three referent-gap declines in "
            f"_extract_arguments, found {len(gap_sites)}; re-point this test")

        missing = [t for t, body in gap_sites
                   if not any("_referent_gap" in ast.unparse(st) for st in body)]
        self.assertEqual(
            missing, [],
            f"{len(missing)} referent-gap decline(s) return None without "
            f"recording why: {missing}. The rung then cannot tell an "
            f"unresolvable referent from any other indeterminate argument, and "
            f"the clause falls to the model")


class TheClarifyIsCodeOwned(unittest.TestCase):

    def test_a_recorded_gap_produces_a_clarify(self):
        from intergen.router import _clarify_for_referent_gap

        rr = _clarify_for_referent_gap("package")
        self.assertIsNotNone(
            rr, "an unresolvable package referent produced no clarify, so the "
                "clause still falls to the model")
        self.assertTrue(rr.handled)
        self.assertFalse(rr.used_llm,
                         "the clarify must be our words, not the model's")
        self.assertTrue(rr.text.strip())

    def test_the_clarify_asks_rather_than_asserting(self):
        from intergen.router import _clarify_for_referent_gap

        text = _clarify_for_referent_gap("package").text
        self.assertIn("?", text,
                      "a clarify that does not ask anything is not a clarify")

    def test_the_clarify_names_no_package(self):
        """The whole point. The measured failure NAMED a package nobody found."""
        from intergen.router import _clarify_for_referent_gap

        text = _clarify_for_referent_gap("package").text.lower()
        for invented in ("pdfelement", "pkm install ", "apt install", "`pkm`"):
            self.assertNotIn(
                invented, text,
                f"the clarify contains {invented!r} — it must ask which one, "
                f"never supply one or the command to install it")

    def test_the_service_gap_asks_about_a_service(self):
        from intergen.router import _clarify_for_referent_gap

        text = _clarify_for_referent_gap("service").text.lower()
        self.assertIn("service", text)
        self.assertNotIn("package", text,
                         "the service clarify asks about a package")

    def test_an_unknown_gap_falls_through(self):
        """Fail closed: an unnamed gap is not answered with a guess."""
        from intergen.router import _clarify_for_referent_gap

        self.assertIsNone(_clarify_for_referent_gap(""))
        self.assertIsNone(_clarify_for_referent_gap("something_else"))

    def test_the_clarify_declares_its_linkage_as_code_owned(self):
        from intergen.router import _clarify_for_referent_gap

        rr = _clarify_for_referent_gap("package")
        self.assertIsNotNone(
            rr.answer_linkage,
            "a composed answer with no declared linkage is recorded as an "
            "uninstrumented path")


class TheRungUsesIt(unittest.TestCase):

    def test_the_decline_path_consults_the_gap(self):
        import ast
        import inspect
        import textwrap

        from intergen.router import ConversationRouter

        src = textwrap.dedent(
            inspect.getsource(ConversationRouter._try_keyword_match))
        self.assertIn(
            "_clarify_for_referent_gap", src,
            "the keyword rung never consults the referent gap, so a clause that "
            "declined for want of a referent still falls to the model")
        # And it must be reached on the indeterminate path, not only the failed
        # one — the measured case never dispatched at all.
        tree = ast.parse(src)
        self.assertTrue(
            any(isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "_clarify_for_referent_gap"
                for n in ast.walk(tree)),
            "the clarify helper is named in the rung but never called")


if __name__ == "__main__":
    unittest.main()
