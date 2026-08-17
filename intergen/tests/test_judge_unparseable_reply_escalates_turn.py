"""An unparseable judge reply escalates THE TURN — it never aborts the run.

The judge model is nondeterministic. One malformed JSON reply out of a long
pull used to raise straight out of judge_turn, through apply_judge_grading,
and kill the whole run's grading before results were written — a completed
147-turn baseline measurement died exactly this way (2026-08-07, the T-7
leg-1 first firing). The contract these cases pin: retry once, then fold a
'flag' verdict that names the parse failure and keeps the reply head, with
the Layer-1 deterministic floor still applied — loud at the turn, never
fatal to the run, and never a silent clean grade.
"""

from __future__ import annotations

import unittest

from intergen.tests.quality_judge import (
    JudgeInputs,
    apply_judge_grading,
    judge_turn,
)

MALFORMED = '```json\n{"reasoning": "an "unescaped" quote broke this", "dimensions": {}}\n```'


def _valid_reply() -> str:
    """A schema-complete reply built from the module's own rubric, so these
    cases cannot drift when dimensions change."""
    import json

    from intergen.tests.quality_judge import RUBRIC_DIMENSIONS
    dims = {d.id: {"verdict": "pass", "evidence": "quoted span"}
            for d in RUBRIC_DIMENSIONS}
    return json.dumps({"reasoning": "clean", "dimensions": dims})


def _inputs() -> JudgeInputs:
    return JudgeInputs(
        user_input="is the disk healthy?",
        assembled_prompt="is the disk healthy?",
        model_output="The disk reports no SMART errors.",
        delivered="The disk reports no SMART errors.",
        source="llm_freeform")


class CountingClient:
    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls = 0

    def __call__(self, prompt: str) -> str:
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return reply


class UnparseableReplyEscalatesTheTurn(unittest.TestCase):
    def test_a_twice_malformed_reply_flags_the_turn_instead_of_raising(self):
        client = CountingClient([MALFORMED, MALFORMED])
        v = judge_turn(_inputs(), judge_client=client)
        self.assertEqual(client.calls, 2, "exactly one retry")
        self.assertEqual(v.overall, "flag")
        self.assertIn("unparseable", v.reasoning)
        self.assertIn("reply head", v.reasoning)

    def test_a_retry_that_parses_is_judged_normally(self):
        client = CountingClient([MALFORMED, _valid_reply()])
        v = judge_turn(_inputs(), judge_client=client)
        self.assertEqual(client.calls, 2)
        self.assertEqual(v.overall, "pass")
        self.assertNotIn("unparseable", v.reasoning)
        self.assertTrue(v.dimensions, "the parsed retry's dimensions folded")

    def test_a_clean_first_reply_is_not_retried(self):
        client = CountingClient([_valid_reply()])
        v = judge_turn(_inputs(), judge_client=client)
        self.assertEqual(client.calls, 1, "no retry on a parseable reply")
        self.assertEqual(v.overall, "pass")

    def test_the_run_completes_and_counts_the_escalations(self):
        run_data = {"conversations": [
            {"turn_details": [
                {"turn_id": "t1", "user": "u1", "response_text": "r1"},
                {"turn_id": "t2", "user": "u2", "response_text": "r2"},
            ]},
        ]}
        client = CountingClient([MALFORMED])  # malformed forever
        escalated = apply_judge_grading(run_data, judge_client=client)
        self.assertEqual(escalated, 2, "both turns escalated, none fatal")
        for turn in run_data["conversations"][0]["turn_details"]:
            self.assertEqual(turn["judge_overall"], "flag")
            overall_rows = [a for a in turn["assertions"]
                            if a.get("type") == "judge:overall"]
            self.assertEqual(len(overall_rows), 1)
            self.assertFalse(overall_rows[0]["passed"],
                             "an unjudged turn is never a silent pass")


class RaisingClient:
    """A judge client whose call fails in transport (what the real client does
    when the judge server refuses a request — e.g. HTTP 400 on a judge prompt
    exceeding the server's context window)."""

    def __init__(self, errors: list[Exception], then: str | None = None) -> None:
        self.errors = errors
        self.then = then
        self.calls = 0

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        if self.calls <= len(self.errors):
            raise self.errors[self.calls - 1]
        assert self.then is not None
        return self.then


def _http_400() -> Exception:
    import io
    import urllib.error
    return urllib.error.HTTPError(
        "http://127.0.0.1:8090/v1/chat/completions", 400, "Bad Request",
        {}, io.BytesIO(b""))


class TransportFailureEscalatesTheTurn(unittest.TestCase):
    """The transport twin of the unparseable-reply contract (2026-08-12: an
    over-long judge prompt drew HTTP 400 from the judge server and the raised
    HTTPError destroyed a completed 204-conversation baseline, three times)."""

    def test_a_twice_refused_call_flags_the_turn_instead_of_raising(self):
        client = RaisingClient([_http_400(), _http_400()])
        v = judge_turn(_inputs(), judge_client=client)
        self.assertEqual(client.calls, 2, "exactly one retry")
        self.assertEqual(v.overall, "flag")
        self.assertIn("transport", v.reasoning)
        self.assertIn("400", v.reasoning)
        self.assertIn("no reply reached the parser", v.reasoning)

    def test_a_transient_fault_that_clears_on_retry_is_judged_normally(self):
        client = RaisingClient([_http_400()], then=_valid_reply())
        v = judge_turn(_inputs(), judge_client=client)
        self.assertEqual(client.calls, 2)
        self.assertEqual(v.overall, "pass")
        self.assertNotIn("transport", v.reasoning)

    def test_a_timeout_is_the_same_lane(self):
        client = RaisingClient([TimeoutError("timed out"),
                                TimeoutError("timed out")])
        v = judge_turn(_inputs(), judge_client=client)
        self.assertEqual(v.overall, "flag")
        self.assertIn("transport", v.reasoning)

    def test_the_run_survives_a_refusing_judge_and_counts_the_escalations(self):
        run_data = {"conversations": [
            {"turn_details": [
                {"turn_id": "t1", "user": "u1", "response_text": "r1"},
                {"turn_id": "t2", "user": "u2", "response_text": "r2"},
            ]},
        ]}
        client = RaisingClient([_http_400()] * 4)  # refused forever
        escalated = apply_judge_grading(run_data, judge_client=client)
        self.assertEqual(escalated, 2, "both turns escalated, none fatal")
        for turn in run_data["conversations"][0]["turn_details"]:
            self.assertEqual(turn["judge_overall"], "flag")
            overall_rows = [a for a in turn["assertions"]
                            if a.get("type") == "judge:overall"]
            self.assertEqual(len(overall_rows), 1)
            self.assertFalse(overall_rows[0]["passed"],
                             "an unjudged turn is never a silent pass")


if __name__ == "__main__":
    unittest.main()
