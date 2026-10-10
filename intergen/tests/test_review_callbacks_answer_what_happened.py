# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every review callback answers the registry with what happened.

The registry records a review answer of "deny" as the person's own refusal
(``user_decision`` "denied", "User denied via review modal") and records the
other non-approvals as what happened (review_timed_out, review_failed,
refused_without_review). The web review bridge answers that way. The other
review callbacks answered "deny" for refusals no person gave:

* consent_dialog.py, the branded dialog review_modal renders first on a
  desktop: its own post-render deadline, and a crash, a signal or an unmapped
  exit;
* review_modal.py, the desktop review: zenity's own one-hour timeout (exit 5),
  zenity outliving its subprocess bound, zenity failing to start, zenity
  exiting with an error code, no notify-send to show the held action, a
  notify-send that failed, no zenity to answer it with, and the one-hour
  expiry of the locked-session fallback;
* the daemon's launch-time review autopilot, which refuses by policy (its deny
  mode, and a privileged call in its allow mode);
* the evaluation responder (eval_consent.py), which refuses every review by
  policy.

Each now answers the registry's word for what happened, so the record never
names the person for a refusal they did not give. Nothing runs on any of them:
the registry runs a held call only on an approval. The person's own Deny, and
the approvals, are answered as before — those are the controls.

Hermetic: zenity and notify-send are never started (the bounded runner,
consent_dialog.run_bounded, and the executable lookup are replaced), and
the registry cases replace the root dispatch, the dispatch token and the
audit writer.
"""

from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from intergen import eval_consent, review_modal
from intergen import tool_registry as registry_module
from intergen.dbus_daemon import InterGenDaemon
from intergen.interfaces.provenance import DispatchDecision, Provenance
from intergen.interfaces.types import ToolCall, ToolResult
from intergen.tool_registry import (
    REFUSED_WITHOUT_REVIEW, REVIEW_FAILED, REVIEW_TIMED_OUT, ToolRegistry,
)

_ARGS = {"path": "/tmp/review-callback-case-never-written.txt", "content": "x"}


def _call(call_id: str = "c1") -> ToolCall:
    return ToolCall(name="write_file", arguments=dict(_ARGS), call_id=call_id,
                    source_of_request=Provenance.USER_DIRECT)


def _decision(needs_pkexec: bool = False) -> DispatchDecision:
    return DispatchDecision(action="hold_for_review",
                            effective_provenance=Provenance.USER_DIRECT,
                            needs_pkexec=needs_pkexec, reason="case")


def _which(**present):
    """shutil.which as review_modal sees it: only the named tools exist."""
    return lambda name: present.get(name)


def _zenity(*, returncode: int = 0, stdout: str = "", raises=None) -> str:
    """Run review_modal's zenity prompt with zenity's result chosen here."""
    def _run(argv, *a, **k):
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout,
                                           stderr="")
    with mock.patch.object(review_modal.shutil, "which",
                           _which(zenity="/usr/bin/zenity")), \
            mock.patch.object(review_modal.consent_dialog, "run_bounded", _run):
        return review_modal._prompt_review_zenity(_call(), _decision(),
                                                  "", "", "")


def _fallback(*, notify_send=True, zenity=True, notify_raises=None,
              expire=False) -> str:
    """Run review_modal's locked-session fallback with its tools chosen here."""
    present = {}
    if notify_send:
        present["notify-send"] = "/usr/bin/notify-send"
    if zenity:
        present["zenity"] = "/usr/bin/zenity"

    def _run(argv, *a, **k):
        if notify_raises is not None:
            raise notify_raises
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    patches = [
        mock.patch.object(review_modal.shutil, "which", _which(**present)),
        mock.patch.object(review_modal.consent_dialog, "run_bounded", _run),
        mock.patch.object(review_modal, "_session_active", return_value=False),
        mock.patch.object(review_modal.time, "sleep", lambda s: None),
    ]
    if expire:
        patches.append(mock.patch.object(review_modal,
                                         "FALLBACK_TIMEOUT_SECONDS", 0))
    for p in patches:
        p.start()
    try:
        return review_modal._prompt_review_libnotify(_call(), _decision(),
                                                     "", "", "")
    finally:
        for p in reversed(patches):
            p.stop()


class TheDesktopReviewAnswersWhatHappened(unittest.TestCase):
    """intergen/review_modal.py — every path that answered "deny" with no
    person's answer behind it."""

    def test_zenity_timing_out_is_review_timed_out(self):
        self.assertEqual(_zenity(returncode=5), REVIEW_TIMED_OUT)

    def test_zenity_outliving_its_bound_is_review_timed_out(self):
        self.assertEqual(
            _zenity(raises=subprocess.TimeoutExpired(["zenity"], 3660)),
            REVIEW_TIMED_OUT)

    def test_zenity_failing_to_start_is_review_failed(self):
        self.assertEqual(_zenity(raises=OSError("exec format error")),
                         REVIEW_FAILED)

    def test_zenity_exiting_with_an_error_is_review_failed(self):
        self.assertEqual(_zenity(returncode=255), REVIEW_FAILED)

    def test_no_notify_send_is_review_failed(self):
        self.assertEqual(_fallback(notify_send=False), REVIEW_FAILED)

    def test_a_notify_send_that_failed_is_review_failed(self):
        self.assertEqual(_fallback(notify_raises=OSError("no bus")),
                         REVIEW_FAILED)

    def test_no_zenity_to_answer_with_is_review_failed(self):
        self.assertEqual(_fallback(zenity=False), REVIEW_FAILED)

    def test_the_fallbacks_hour_expiring_is_review_timed_out(self):
        self.assertEqual(_fallback(expire=True), REVIEW_TIMED_OUT)

    # -- controls: the person's own answers are answered as before ----------

    def test_control_the_persons_deny_is_deny(self):
        self.assertEqual(_zenity(returncode=1), "deny")

    def test_control_the_persons_approvals_are_approvals(self):
        self.assertEqual(_zenity(returncode=0), "allow_once")
        self.assertEqual(
            _zenity(returncode=1, stdout="Allow for this conversation\n"),
            "allow_conversation")


def _branded(body: str, deadline: float | None = None):
    """Run the branded review dialog's launcher against a stand-in binary that
    signals it rendered and then does what ``body`` says, through the real
    pre-render watchdog and post-render wait (consent_dialog._run_dialog)."""
    import sys

    from intergen import consent_dialog
    from intergen import consent_dialog_proto as proto
    real = consent_dialog._run_dialog
    script = ("import os, sys, time; sys.stdin.read(); "
              f"sys.stdout.write({proto.RENDERED_MARKER!r} + chr(10)); "
              "sys.stdout.flush(); " + body)
    patches = [mock.patch.object(
        consent_dialog, "_run_dialog",
        lambda spec: real(spec, cmd=[sys.executable, "-c", script]))]
    if deadline is not None:
        patches.append(mock.patch.object(proto, "POST_RENDER_DEADLINE_SECONDS",
                                         deadline))
    for p in patches:
        p.start()
    try:
        return (consent_dialog.run_review_dialog(_call(), _decision()),
                consent_dialog.run_consent_dialog("x", "provider"))
    finally:
        for p in reversed(patches):
            p.stop()


class TheBrandedDialogAnswersWhatHappened(unittest.TestCase):
    """intergen/consent_dialog.py — the dialog review_modal renders first on a
    desktop: its deadline and a crash answered "deny"."""

    def test_the_dialogs_deadline_is_review_timed_out(self):
        review, consent = _branded("time.sleep(30)", deadline=1.0)
        self.assertEqual(review, REVIEW_TIMED_OUT)
        self.assertIs(consent, False, "an unanswered consent must not send")

    def test_a_dialog_that_crashed_is_review_failed(self):
        review, consent = _branded("os.abort()")
        self.assertEqual(review, REVIEW_FAILED)
        self.assertIs(consent, False, "a crashed consent must not send")

    def test_control_the_persons_answers_in_the_dialog(self):
        self.assertEqual(_branded("sys.exit(1)")[0], "deny")
        self.assertEqual(_branded("sys.exit(10)")[0], "allow_once")


class ThePolicyResponderAnswerRefusedWithoutReview(unittest.TestCase):
    """intergen/dbus_daemon.py (the review autopilot) and
    intergen/eval_consent.py — refusals by policy, not by a person."""

    def test_the_autopilots_deny_mode_is_refused_without_review(self):
        cb = InterGenDaemon._make_autopilot_review_callback("deny")
        self.assertEqual(cb(_call(), _decision()), REFUSED_WITHOUT_REVIEW)

    def test_the_autopilot_refusing_a_privileged_call_is_refused_without_review(
            self):
        cb = InterGenDaemon._make_autopilot_review_callback("allow")
        self.assertEqual(cb(_call(), _decision(needs_pkexec=True)),
                         REFUSED_WITHOUT_REVIEW)

    def test_the_evaluation_responder_is_refused_without_review(self):
        with mock.patch.object(eval_consent, "_emit"):
            self.assertEqual(eval_consent.review_verdict(_call(), _decision()),
                             REFUSED_WITHOUT_REVIEW)

    def test_control_the_autopilot_still_approves_an_ordinary_call(self):
        cb = InterGenDaemon._make_autopilot_review_callback("allow")
        self.assertEqual(cb(_call(), _decision(needs_pkexec=False)),
                         "allow_once")


class TheRecordNamesWhatHappened(unittest.TestCase):
    """The answers above, through the real registry: nothing runs, and the
    audit record does not name the person for a refusal they did not give."""

    def setUp(self) -> None:
        self.records: list = []
        self.dispatched: list = []
        self.registry = ToolRegistry()
        self.registry.discover_tools()
        for patcher in (
            mock.patch.object(registry_module, "write_record",
                              side_effect=lambda r, *a, **k:
                              self.records.append(r) or True),
            mock.patch.object(registry_module, "mint_token",
                              return_value="case-token"),
            mock.patch.object(self.registry, "_dispatch_via_pkexec",
                              side_effect=self._dispatch),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _dispatch(self, call, *a, **k) -> ToolResult:
        self.dispatched.append(call.arguments)
        return ToolResult(call_id=call.call_id, name=call.name, content="ok",
                          success=True)

    def _through(self, callback, outcome: str) -> None:
        result = self.registry.execute(_call(), review_callback=callback)
        self.assertEqual(self.dispatched, [], "the held call ran")
        self.assertFalse(result.executed)
        self.assertFalse(result.denied_by_user)
        self.assertEqual(len(self.records), 1, self.records)
        record = self.records[0]
        self.assertEqual(record.user_decision, outcome,
                         f"recorded as {record.user_decision!r}: "
                         f"{record.result_summary!r}")
        self.assertNotIn("User denied", record.result_summary)

    def test_a_desktop_review_that_timed_out(self):
        self._through(lambda call, decision: _zenity(returncode=5),
                      REVIEW_TIMED_OUT)

    def test_the_autopilots_refusal(self):
        self._through(InterGenDaemon._make_autopilot_review_callback("deny"),
                      REFUSED_WITHOUT_REVIEW)

    def test_the_evaluation_responders_refusal(self):
        with mock.patch.object(eval_consent, "_emit"):
            self._through(eval_consent.make_review_callback(),
                          REFUSED_WITHOUT_REVIEW)


if __name__ == "__main__":
    unittest.main()
