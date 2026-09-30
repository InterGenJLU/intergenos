# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""THE WINDOW'S OWN TWO SENTENCES ABOUT A SHORT VERSION.

WHAT WAS STILL WRONG AFTER THE PREVIOUS COMMIT. That commit gave the timeline
row the count of paths a capture could not read, and it left two places in the
same window saying the opposite:

  * The toast a capture ends in. A person who presses Capture now is told
    "Captured user-data <id>" — the handler rendered that inline and threw the
    rest of the result away, so the one sentence a person gets about the
    capture they just drove could not say it had been short, while the row
    beside it and the command line both said so.
  * The verdict line at the top of the window. It is the first thing a person
    reads about their backup. With the newest version short of its source it
    read "Protected — last capture today at HH:MM" and nothing more, because
    the status verb carried no count for a caller to render.

WHAT THIS COMMIT DOES, AND WHAT IT DELIBERATELY DOES NOT. The verdict STAYS
PROTECTED: versions exist and the captures are reaching their target, and a
person's remedy is not to treat the machine as unprotected. What changes is
that it says in the same breath that the newest version of a layer could not
read everything, so the top line and the timeline below it no longer disagree.
Both sentences are rendered by named functions beside `timeline_subtitle`, so
these cases state what the window says without opening a display — the window
itself is drawn by a toolkit these cases do not start.

Nine cases below are red at this lane's parent: the two rendering functions
and the engine helper do not exist there, so the clean-run controls for the
toast and verdict note are red there too. The control that the verdict stays
PROTECTED passes on both.
"""

import os
import unittest
from unittest import mock

import pytest

from chronicle import engine as _engine
from chronicle import gui as _gui
from chronicle import manifest as _manifest
from chronicle import paths as _paths
from chronicle import protection as _protection

pytestmark = pytest.mark.skipif(
    os.geteuid() == 0,
    reason="the store below is written as this user; root would read it anyway")

_SHORT = {"version_id": "0000000002-2be713ba6d4f",
          "unreadable": ["/home/someone/.cache/one-locked-directory"]}
_CLEAN = {"version_id": "0000000003-9c1f0a2b77de", "unreadable": []}


class TheCaptureToast(unittest.TestCase):
    """The sentence a person gets about the capture they just drove."""

    def test_a_short_capture_says_so(self):
        toast = getattr(_gui, "capture_toast", None)
        self.assertIsNotNone(toast, (
            "the handler renders the toast inline, so there is nowhere for the "
            "count to come from and no way to state what the toast says"))
        text = toast("user-data", _SHORT)
        self.assertIn("Captured user-data", text)
        self.assertIn(_SHORT["version_id"], text)
        self.assertIn("1 path(s) could not be read", text, (
            f"the toast reports a short version as a clean one: {text!r}"))

    def test_a_complete_capture_says_nothing_about_omissions(self):
        """CONTROL."""
        toast = getattr(_gui, "capture_toast", None)
        self.assertIsNotNone(toast)
        text = toast("config", _CLEAN)
        self.assertIn("Captured config", text)
        self.assertNotIn("could not be read", text)

    def test_the_two_toasts_differ(self):
        """CONTROL: the count is what makes them different, not the layer."""
        toast = getattr(_gui, "capture_toast", None)
        self.assertIsNotNone(toast)
        self.assertNotEqual(toast("user-data", _SHORT),
                            toast("user-data", dict(_CLEAN)))


class TheVerdictLine(unittest.TestCase):
    """The first line a person reads about their backup."""

    def _status(self, **newest):
        return {"target": None, "target_present": True,
                "last_capture": {"user-data": 1759250000},
                "newest_unreadable": newest}

    def test_a_short_newest_version_is_named_in_the_verdict(self):
        note = getattr(_protection, "short_version_note", None)
        self.assertIsNotNone(note, (
            "nothing renders the verdict's clause about a short version, so "
            "the verdict cannot say it and no case can state what it says"))
        text = note(self._status(**{"user-data": {
            "version_id": _SHORT["version_id"], "unreadable": 1}}))
        self.assertIn("user-data", text)
        self.assertIn("1 path(s)", text)
        self.assertIn("could not read", text)

    def test_the_verdict_stays_protected(self):
        """The verdict itself does not move: this is a disclosure, not a state
        change, and a person's remedy is not to treat the machine as
        unprotected."""
        status = self._status(**{"user-data": {
            "version_id": _SHORT["version_id"], "unreadable": 1}})
        status["last_capture"] = {"user-data": 1759250000}
        self.assertEqual(_protection.classify(status), _protection.PROTECTED)

    def test_a_complete_newest_version_adds_nothing(self):
        """CONTROL."""
        note = getattr(_protection, "short_version_note", None)
        self.assertIsNotNone(note)
        self.assertEqual(note(self._status(**{"user-data": {
            "version_id": _CLEAN["version_id"], "unreadable": 0}})), "")
        self.assertEqual(note(self._status()), "")

    def test_every_short_layer_is_named(self):
        note = getattr(_protection, "short_version_note", None)
        self.assertIsNotNone(note)
        text = note(self._status(**{
            "user-data": {"version_id": "0000000002-2be713ba6d4f",
                          "unreadable": 1},
            "config": {"version_id": "0000000004-6b2dc07a701c",
                       "unreadable": 3}}))
        self.assertIn("user-data", text)
        self.assertIn("config", text)
        self.assertIn("3 path(s)", text)


class TheStatusVerbCarriesTheCount(unittest.TestCase):
    """Where the verdict's count comes from. The window asks the engine for
    status and nothing else, so the count has to be in that payload."""

    def test_status_names_the_newest_versions_omission_count(self):
        eng = _engine.Engine.__new__(_engine.Engine)
        seen = {}

        def store_root_for(layer):
            return f"/store/{layer}" if layer == _paths.LAYERS[0] else None

        def list_versions(root, layer):
            seen["asked"] = (root, layer)
            return [
                {"version_id": "0000000001-aaaaaaaaaaaa", "sequence": 1,
                 "wall_clock": 10, "unreadable": []},
                {"version_id": _SHORT["version_id"], "sequence": 2,
                 "wall_clock": 20, "unreadable": _SHORT["unreadable"]},
            ]

        eng._store_root_for = store_root_for
        with mock.patch.object(_manifest, "list_versions", list_versions):
            out = _engine.Engine._newest_unreadable_by_layer(eng)
        layer = _paths.LAYERS[0]
        self.assertIn(layer, out, (
            "the status payload carries no count for the newest version, so "
            "the verdict has nothing to say it with"))
        self.assertEqual(out[layer]["version_id"], _SHORT["version_id"],
                         "the NEWEST version is the one a person is told about")
        self.assertEqual(out[layer]["unreadable"], 1)

    def test_a_layer_whose_newest_version_is_clean_reports_zero(self):
        """CONTROL."""
        eng = _engine.Engine.__new__(_engine.Engine)
        eng._store_root_for = lambda layer: f"/store/{layer}"

        def list_versions(root, layer):
            return [{"version_id": _CLEAN["version_id"], "sequence": 1,
                     "wall_clock": 10, "unreadable": []}]

        with mock.patch.object(_manifest, "list_versions", list_versions):
            out = _engine.Engine._newest_unreadable_by_layer(eng)
        for layer in _paths.LAYERS:
            self.assertEqual(out[layer]["unreadable"], 0)

    def test_the_count_never_carries_the_paths(self):
        """The policy keeps paths out of the read tier; status is read-tier."""
        eng = _engine.Engine.__new__(_engine.Engine)
        eng._store_root_for = lambda layer: f"/store/{layer}"

        def list_versions(root, layer):
            return [{"version_id": _SHORT["version_id"], "sequence": 1,
                     "wall_clock": 10, "unreadable": _SHORT["unreadable"]}]

        with mock.patch.object(_manifest, "list_versions", list_versions):
            out = _engine.Engine._newest_unreadable_by_layer(eng)
        rendered = repr(out)
        self.assertNotIn("one-locked-directory", rendered,
                         f"a path reached the read tier: {rendered!r}")


if __name__ == "__main__":
    unittest.main()
