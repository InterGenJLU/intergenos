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
import threading
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


def _code_compiled_as(filename: str):
    """A caller and a frame that name ``filename``, for the boundary to classify.

    The file itself need not exist: what the boundary reads is a frame's
    ``__file__``, so compiling the caller under a chosen name is the only way to
    hand the PRODUCTION path a caller from a chosen directory. The cases above
    hand the rule a frame directly, which proves the rule; these prove the call
    that uses it.
    """
    namespace = {"__file__": filename}
    source = ("import sys\n"
              "def frame_here():\n"
              "    return sys._getframe(0)\n"
              "def open_from_here(opener, address):\n"
              "    return opener(address)\n"
              # Records the outcome and then hands it back, rather than letting
              # a thread swallow it: the refusal is a BaseException by design,
              # and a control that lost it would report a clean run for a
              # request that happened. This runs in the compiled file too, so
              # that a thread started on it carries no frame of the case's own
              # module up its stack.
              "def record_opening_from_here(opener, address, outcome):\n"
              "    try:\n"
              "        outcome.append(('returned', opener(address)))\n"
              "    except BaseException as caught:\n"
              "        outcome.append(('raised', caught))\n")
    exec(compile(source, filename, "exec"), namespace)
    return namespace


class TheBoundaryJudgesTheCallerThatMadeTheRequest(unittest.TestCase):
    """Where a request came from, decided on the PRODUCTION path.

    The cases above exercise the rule with a frame handed to it. That leaves the
    call the boundary actually makes unproven, and the two went different ways:
    the installed opener asked the rule about its OWN frame, which lives in this
    directory, so every request made outside a case was refused — this
    directory's, the directory below it, and another test directory's alike
    (measured 2026-09-30). These cases make the request through the real
    `urllib.request.urlopen` with no case running, which is what collection, a
    module import and a class-level setup look like, from code compiled as a file
    in a chosen directory, and read what the boundary did with it.

    The real opener is replaced with a recorder first, so a request that PASSES
    the boundary is counted here rather than sent anywhere.

    Two of the cases open from a NEW THREAD, and that is not decoration: a case
    of this directory has this directory's frames all the way up its own stack,
    so from here a foreign caller could never be seen as foreign. A thread's
    stack begins at its own target, and the walk ends in the threading module,
    which is how a caller outside this directory is put in front of the
    installed boundary at all.
    """

    ADDRESS = "http://127.0.0.1:1/v1/embeddings"
    PASSED = "passed the boundary"

    def setUp(self):
        self.boundary = _the_live_boundary()
        self.calls = []
        self._real_opener = self.boundary._REAL_URLOPEN
        self._running_item = self.boundary._RUNNING["item"]

        def recorder(request, *args, **kwargs):
            self.calls.append(getattr(request, "full_url", None) or str(request))
            return self.PASSED

        self.boundary._REAL_URLOPEN = recorder
        self.boundary._RUNNING["item"] = None

    def tearDown(self):
        self.boundary._REAL_URLOPEN = self._real_opener
        self.boundary._RUNNING["item"] = self._running_item

    def _here(self, *parts):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), *parts)

    def _in_the_tree(self, *parts):
        """A path under the source tree's root, outside this directory.

        Spelled from this file rather than from a working directory, so the case
        means the same thing wherever the suite is run from: this file sits in
        ``<root>/intergen/tests``, so the root is three levels up.
        """
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        return os.path.join(root, *parts)

    def _open_from(self, filename):
        code = _code_compiled_as(filename)
        return code["open_from_here"](urllib.request.urlopen, self.ADDRESS)

    def _open_from_on_its_own_thread(self, filename):
        """What the boundary does with that caller, off this case's stack.

        Everything the thread runs is compiled as ``filename``, this method's
        own frame included by its absence: a helper of this module on the
        thread's stack would put this directory back in the walk's way and the
        case would measure nothing.
        """
        code = _code_compiled_as(filename)
        outcome = []
        thread = threading.Thread(
            target=code["record_opening_from_here"],
            args=(urllib.request.urlopen, self.ADDRESS, outcome),
            name="boundary-caller")
        thread.start()
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive(), "the caller thread did not finish")
        self.assertEqual(len(outcome), 1, f"the thread recorded {outcome!r}")
        return outcome[0]

    def test_a_request_from_this_directory_is_refused(self):
        with self.assertRaises(self.boundary.ModelServerReachedInAUnitTest) as caught:
            self._open_from(self._here("a_root_caller_of_this_directory.py"))
        self.assertIn("outside any case", str(caught.exception))
        self.assertEqual(self.calls, [], "a refused request must not reach the opener")

    def test_a_request_from_a_directory_below_this_one_is_refused(self):
        # scenario/ is one of this directory's own subtrees; a request made while
        # it is imported or collected is this directory's request.
        nested = self._here("scenario", "a_nested_caller_of_this_directory.py")
        with self.assertRaises(self.boundary.ModelServerReachedInAUnitTest) as caught:
            self._open_from(nested)
        self.assertIn("outside any case", str(caught.exception))
        self.assertEqual(self.calls, [])

        # The same claim one level down, where the walk cannot stand in for it:
        # the rule itself must call a file below this directory this
        # directory's, or correcting the walk alone would let collection and
        # import code in the subtrees reach a server.
        self.assertTrue(
            self.boundary.request_is_this_directory_s(
                _code_compiled_as(nested)["frame_here"](), depth=1),
            "a frame from a directory below this one is this directory's")

    def test_a_request_from_another_test_directory_passes_untouched(self):
        # The suite's other test root. This boundary is this directory's and
        # says so: another directory's request goes through unchanged.
        how, what = self._open_from_on_its_own_thread(
            self._in_the_tree("tests", "a_caller_in_the_other_test_root.py"))
        self.assertEqual((how, what), ("returned", self.PASSED),
                         f"another test directory was governed by this boundary: {what!r}")
        self.assertEqual(self.calls, [self.ADDRESS])

    def test_a_directory_whose_name_only_begins_like_this_one_is_outside(self):
        # The subtree rule is about path components, not about the text of a
        # path: a sibling that merely starts the same way is not below this
        # directory and must stay outside the boundary.
        beside = os.path.dirname(os.path.abspath(__file__)) + "-and-then-some"
        how, what = self._open_from_on_its_own_thread(
            os.path.join(beside, "a_caller_in_a_similarly_named_directory.py"))
        self.assertEqual((how, what), ("returned", self.PASSED),
                         f"a sibling directory was governed by this boundary: {what!r}")
        self.assertEqual(self.calls, [self.ADDRESS])

    def test_the_rule_will_not_guess_which_frame_to_start_from(self):
        # The frame is required, and this is the case that keeps it required: a
        # default of "my own caller" is what made the installed boundary ask
        # about its own frame and refuse everything. A caller with no frame to
        # name fails here, loudly, instead of being answered wrongly.
        with self.assertRaises(TypeError):
            self.boundary.request_is_this_directory_s()


if __name__ == "__main__":
    unittest.main()
