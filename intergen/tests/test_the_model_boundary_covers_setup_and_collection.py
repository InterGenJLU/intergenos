# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""THE BOUNDARY'S OWN RULES, PINNED.

WHY THESE CASES EXIST. The boundary in this directory's conftest is what keeps a
unit case from reaching whatever model server the machine happens to have up. Its
first version was a per-case fixture, and two things about that were wrong in the
same way: code that runs OUTSIDE a case — a unittest class-level setup, module
code at collection — was not covered, and the directory's own opt-in embedding
gates, which are supposed to reach a real server when someone asks for them by
name, had no way to say so and could no longer measure anything. Both were
measured, not supposed. The rules below are therefore asserted directly rather
than inferred from whether some other run happened to be green.

WHAT IS ASSERTED, AND WHY IN THIS SHAPE. The permission rule is a pure function
of a case's markers and the environment, so it is read directly; the boundary's
refusal is exercised through the real `urllib.request.urlopen` name, because
that name IS the claim; and the two gates' markers are read off the classes
themselves, so a gate cannot drift away from the variable that opts it in. None
of it contacts anything: the only request these cases make is one the boundary
refuses before a socket exists, and the single case that proves a request PASSES
the boundary replaces the real opener with a recorder first.
"""

from __future__ import annotations

import os
import sys
import types
import unittest
import urllib.request

import pytest


def _the_live_boundary():
    """The conftest module object this run is actually using, not a fresh copy.

    A second import would carry its own state and prove nothing about the
    boundary that is installed, so the module is found by its file.
    """
    target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conftest.py")
    for module in list(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if filename and os.path.abspath(filename) == target:
            return module
    raise AssertionError(f"the live conftest module for {target} is not in sys.modules")


class _Mark:
    def __init__(self, *args):
        self.args = args


class _Item:
    """The smallest thing the permission rule reads: markers and a node id."""

    def __init__(self, **marks):
        self._marks = marks
        self.nodeid = "a case"

    def get_closest_marker(self, name):
        return self._marks.get(name)


class ThePermissionRule(unittest.TestCase):
    def setUp(self):
        self.boundary = _the_live_boundary()

    def test_a_case_with_no_marker_has_no_permission(self):
        self.assertIsNone(self.boundary.permission_for(_Item()))

    def test_the_own_server_marker_is_honoured_unconditionally(self):
        item = _Item(**{self.boundary.OPT_OUT_MARKER: _Mark()})
        permission = self.boundary.permission_for(item)
        self.assertIsNotNone(permission)
        self.assertIn(self.boundary.OPT_OUT_MARKER, permission)

    def test_the_embedding_gate_marker_is_honoured_only_while_its_variable_is_set(self):
        variable = "INTERGEN_A_GATE_THAT_DOES_NOT_EXIST"
        item = _Item(**{self.boundary.EMBEDDING_GATE_MARKER: _Mark(variable)})
        previous = os.environ.pop(variable, None)
        try:
            self.assertIsNone(self.boundary.permission_for(item),
                              "the marker alone must not open the boundary")
            os.environ[variable] = "0"
            self.assertIsNone(self.boundary.permission_for(item),
                              "only the value 1 opts a gate in")
            os.environ[variable] = "1"
            permission = self.boundary.permission_for(item)
            self.assertIsNotNone(permission)
            self.assertIn(variable, permission)
        finally:
            os.environ.pop(variable, None)
            if previous is not None:
                os.environ[variable] = previous

    def test_the_embedding_gate_marker_without_a_variable_opens_nothing(self):
        item = _Item(**{self.boundary.EMBEDDING_GATE_MARKER: _Mark()})
        self.assertIsNone(self.boundary.permission_for(item))


class TheBoundaryIsInstalledOnTheNameItClaims(unittest.TestCase):
    def setUp(self):
        self.boundary = _the_live_boundary()

    def test_a_request_from_a_case_is_refused_by_name(self):
        with self.assertRaises(self.boundary.ModelServerReachedInAUnitTest) as caught:
            urllib.request.urlopen("http://127.0.0.1:1/v1/chat/completions")
        message = str(caught.exception)
        self.assertIn("test_a_request_from_a_case_is_refused_by_name", message)
        self.assertIn("http://127.0.0.1:1/v1/chat/completions", message)

    def test_the_refusal_is_not_an_Exception(self):
        # The product wraps every one of these calls in `except Exception`; a
        # refusal it could swallow would report a clean run for a request that
        # already happened.
        self.assertTrue(issubclass(self.boundary.ModelServerReachedInAUnitTest,
                                   BaseException))
        self.assertFalse(issubclass(self.boundary.ModelServerReachedInAUnitTest,
                                    Exception))

    def test_this_directorys_code_is_refused_outside_a_case_as_well(self):
        # What a class-level setup or a module at collection looks like to the
        # boundary: no case is running, and the request comes from this
        # directory. The state is put back whatever happens.
        boundary = self.boundary
        running = boundary._RUNNING
        item, running["item"] = running["item"], None
        try:
            with self.assertRaises(boundary.ModelServerReachedInAUnitTest) as caught:
                urllib.request.urlopen("http://127.0.0.1:1/v1/embeddings")
            self.assertIn("outside any case", str(caught.exception))
        finally:
            running["item"] = item

    def test_a_frame_from_elsewhere_is_not_this_directorys(self):
        # The boundary belongs to this directory: a request made outside a case
        # by code that lives somewhere else passes, or arming this one name
        # would quietly govern every other test directory too. The rule is
        # judged on a frame handed to it, because the stack of a case in THIS
        # directory always contains this directory and so could never stand in
        # for another one.
        boundary = self.boundary
        source = "import sys\ndef frame_from_elsewhere():\n    return sys._getframe(0)\n"
        namespace = {"__file__": "/somewhere/else/not_this_directory.py"}
        exec(compile(source, namespace["__file__"], "exec"), namespace)
        foreign = namespace["frame_from_elsewhere"]()

        self.assertFalse(boundary.request_is_this_directory_s(foreign, depth=1),
                         "a frame whose file lives elsewhere is not this directory's")
        self.assertTrue(boundary.request_is_this_directory_s(sys._getframe(0), depth=1),
                        "this case's own frame is this directory's")

    def test_the_walk_looks_past_the_immediate_caller(self):
        # Module-level code here can reach a server through the product, several
        # frames down; the immediate caller would then be the product's file and
        # the request would pass. The walk is what covers that, so its reach is
        # asserted rather than assumed.
        boundary = self.boundary
        source = ("import sys\n"
                  "def through_a_middle_layer(sink):\n"
                  "    return sink()\n")
        namespace = {"__file__": "/somewhere/else/not_this_directory.py"}
        exec(compile(source, namespace["__file__"], "exec"), namespace)

        def sink():
            return boundary.request_is_this_directory_s(sys._getframe(1), depth=4)

        # the frame handed over is the foreign middle layer's; this directory's
        # frame is the one below it, and the walk must still find it.
        self.assertTrue(namespace["through_a_middle_layer"](sink))

class TheTwoOptInGatesCarryTheirMarker(unittest.TestCase):
    """The marker and the variable that opts the gate in stay together.

    Read off the classes rather than trusted to a list somewhere else: a gate
    that loses its marker would be refused on the very run someone opted into,
    and a gate whose marker named the wrong variable would reach the machine's
    server on every routine run.
    """

    def _marker_of(self, cls):
        marks = [m for m in getattr(cls, "pytestmark", [])
                 if m.name == _the_live_boundary().EMBEDDING_GATE_MARKER]
        self.assertEqual(len(marks), 1, f"{cls.__name__} carries {len(marks)} of them")
        return marks[0]

    def test_the_precision_recall_harness(self):
        from intergen.tests import test_matcher_precision_recall as harness
        mark = self._marker_of(harness.MatcherPrecisionRecallTests)
        self.assertEqual(mark.args, ("INTERGEN_PR_HARNESS",))
        self.assertEqual(harness._ENABLED,
                         os.environ.get("INTERGEN_PR_HARNESS") == "1")

    def test_the_field_sentence_router_gate(self):
        from intergen.tests import test_router_admission_is_the_per_intent_bar as gate
        mark = self._marker_of(gate.FieldSentencesThroughTheRealRouter)
        self.assertEqual(mark.args, (gate._LIVE_ENV,))
        self.assertEqual(gate._LIVE_ENV, "INTERGEN_FIELD_ROUTER_GATE")

    def test_neither_gate_reaches_out_while_it_is_not_opted_in(self):
        # Importing the two modules is what collection does. With the boundary
        # armed and no variable set, that import must make no request at all —
        # the reachability probe belongs inside the case, under the marker.
        for name in ("test_matcher_precision_recall",
                     "test_router_admission_is_the_per_intent_bar"):
            with self.subTest(module=name):
                module = __import__(f"intergen.tests.{name}", fromlist=["*"])
                self.assertTrue(hasattr(module, "_EMBED_URL"))


if __name__ == "__main__":
    unittest.main()
