# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The lane-proof runner must say which tier it drove.

THE DEFECT, read in the tree. ``lane_proof.main`` never passed a posture to
``run_scenario``, so every run graded with ``posture=None``. A scenario turn can
carry assertions written for different tiers that are MUTUALLY EXCLUSIVE — the
same sentence routing freeform on the locked tier and through tools on the
native one — and grading with no posture evaluated all of them, so one of each
such pair had to fail whatever the product did.

WHAT THAT PRODUCED. A whole-corpus run on a locked 2B box counted 31 failing
assertions that were written for a tier that box is not, and 20 scenarios failed
on nothing else.

WHAT THIS FILE PINS. ``--posture`` is REQUIRED, it accepts only a real posture,
and the value reaches ``run_scenario``. Required rather than defaulted: a
default would be a guess about the box, and a run that guesses its own tier is
the thing being fixed.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen.tests.scenario import lane_proof, runner
from intergen.tests.scenario import transport as transport_module
from intergen.tests.scenario.transport import MockTransport, TurnResult

_SCENARIO = {
    "id": "POSTURE-PLUMB-01",
    "name": "a one-turn scenario, so the run has something to drive",
    "category": "conversation",
    "axis": ["routing"],
    "postures": ["2B-locked"],
    "turns": [{"user": "hello",
               "assertions": [["contains", "hello", "the reply says hello"]]}],
}


def _corpus(tmp: Path) -> Path:
    path = tmp / "corpus.json"
    path.write_text(json.dumps([_SCENARIO]), encoding="utf-8")
    return path


def _argv(tmp: Path, *extra: str) -> list[str]:
    return ["--run-id", "posture-plumb", "--out", str(tmp / "runs"),
            "--corpus", str(_corpus(tmp)), "--allow-installed", *extra]


class PostureIsRequired(unittest.TestCase):

    def test_a_run_without_a_posture_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="posture-") as tmp:
            with self.assertRaises(SystemExit) as raised:
                lane_proof.main(_argv(Path(tmp)))
            self.assertEqual(raised.exception.code, 2)

    def test_a_posture_that_is_not_a_posture_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="posture-") as tmp:
            with self.assertRaises(SystemExit) as raised:
                lane_proof.main(_argv(Path(tmp), "--posture", "7B-imaginary"))
            self.assertEqual(raised.exception.code, 2)


class ThePostureReachesTheGrader(unittest.TestCase):
    """The real run, with the real runner, driven over a mock transport.

    The runner is not replaced — it is WRAPPED, so the real call still happens
    and the real artifacts are still written. Replacing it would test that the
    argument was passed to a stand-in, which is not the same as the run working.
    """

    def test_run_scenario_is_told_which_tier_was_driven(self) -> None:
        seen: dict[str, object] = {}
        real_run_scenario = runner.run_scenario

        def _spy(scenario, transport, **kwargs):
            seen.update(kwargs)
            return real_run_scenario(scenario, transport, **kwargs)

        with tempfile.TemporaryDirectory(prefix="posture-") as tmp:
            with mock.patch.object(runner, "run_scenario", _spy), \
                 mock.patch.object(
                     transport_module, "ClientTransport",
                     lambda **kw: MockTransport(
                         default=TurnResult(text="hello there",
                                            source="llm_freeform"))):
                rc = lane_proof.main(_argv(Path(tmp), "--posture",
                                           "2B-locked"))
        self.assertEqual(
            seen.get("posture"), "2B-locked",
            "the run did not tell the grader which tier it drove, so "
            "tier-specific assertions were graded against a tier nobody named")
        self.assertEqual(rc, 0, "the run itself did not complete")


if __name__ == "__main__":
    unittest.main()
