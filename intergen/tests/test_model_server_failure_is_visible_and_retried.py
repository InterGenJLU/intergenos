# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
r"""A model server that fails to start says WHY, is tried again, and is admitted
to the person if it stays down.

THE EVENT THAT PRODUCED THIS FILE, measured on a dual-GPU workstation on
2026-08-26. The assistant daemon started at 15:49:34 and its chat model server
exited two seconds later. The daemon logged a warning, dropped the manager, and
carried on reporting the unit ACTIVE. For the next two and a half hours every
question a person asked was answered "I didn't manage to put together a response
that time. Could you rephrase or give me a bit more detail about what you need?"
— advice that cannot help, because rephrasing does not start a server. Three
separate defects had to line up for that, and each one is closed here.

1. THE REASON WAS THROWN AWAY. `_wait_for_healthy` recorded the dead child's
   stderr as `...read().decode(errors="replace")[:500]`. The llama.cpp startup
   banner — a device list, build_info, and system_info — is longer than that on
   its own, so the 500 characters that survive are always banner and the reason
   is always in the part that is discarded. The journal line from 15:49:47 is
   exactly 500 characters long and ends mid-token at "8 | CPU : SS". Reproduced
   here against a REAL failing launch: a bad --model path produces 2165 bytes of
   stderr, of which every line naming the cause ("llama_model_load: error loading
   model", "main: exiting due to model loading error") sits in the 1665 bytes the
   slice dropped.

2. IT WAS NEVER TRIED AGAIN. The daemon retains the manager — and therefore
   creates the watchdog that could recover it, which is built under `if
   self._llama:` — for exactly one failure, PORT_IN_USE, on the reasoning that a
   held port is the only transient case. Every other failure sets the manager to
   None and there is no second attempt for the life of the process. The 15:49
   failure recorded UNHEALTHY, so it was dropped. That reasoning is refuted by
   the event itself: the identical command, run by hand 33 minutes later, loaded
   and served normally, and a plain service restart brought the model up first
   try. A device that is momentarily busy is exactly the kind of transient a
   single attempt cannot survive.

3. THE PERSON WAS NOT TOLD. `_EMPTY_RESPONSE_FALLBACK` is the last-resort text
   for a model that GENERATED something unservable, and it is honest for that.
   A model server that is not running reaches the same string through the same
   path, because an unreachable endpoint yields no tokens and an empty
   generation is what the ladder sees. The two are not the same situation and
   must not read the same: one is worth rephrasing for, the other is not. The
   status surface has the same gap — it carries a field for a model server that
   refused to start over a declared capability, and none for a chat model that
   is simply down.

WHAT IS STUBBED. No llama-server is started by any test here except the one
marked as a real failing launch, which starts the real binary with a path that
does not exist and asserts on its real stderr; it skips, naming the reason, when
the binary is absent. No chat model is used anywhere.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest

from intergen import llama_manager
from intergen.interfaces.types import StartFailure
from intergen.llama_manager import LlamaManager
from intergen.llm import LLMRouter


# The real banner a ROCm llama-server prints before it says anything useful.
# Reproduced from the 2026-08-26 journal; its length is the whole point.
_REAL_BANNER = (
    "ggml_cuda_init: found 3 ROCm devices (Total VRAM: 128400 MiB):\n"
    "  Device 0: AMD Radeon AI PRO R9700, gfx1201 (0x1201), VMM: no, Wave Size: 32, VRAM: 32624 MiB\n"
    "  Device 1: AMD Radeon AI PRO R9700, gfx1201 (0x1201), VMM: no, Wave Size: 32, VRAM: 32624 MiB\n"
    "  Device 2: AMD Ryzen 9 9950X 16-Core Processor, gfx1036 (0x1036), VMM: no, Wave Size: 32, VRAM: 63152 MiB\n"
    "build_info: b8796-unknown\n"
    "system_info: n_threads = 16 (n_threads_batch = 16) / 32 | ROCm : NO_VMM = 1 | "
    "PEER_MAX_BATCH_SIZE = 128 | CPU : SSE3 = 1 | SSSE3 = 1 | AVX = 1 | AVX2 = 1 | "
    "F16C = 1 | FMA = 1 | BMI2 = 1 | LLAMAFILE = 1 | OPENMP = 1 | REPACK = 1 |\n"
)
_REAL_REASON = "main: exiting due to model loading error"


class _DeadChild:
    """A subprocess that has already exited, carrying a real-length stderr."""

    class _Stderr:
        def __init__(self, text: str) -> None:
            self._text = text

        def read(self) -> bytes:
            return self._text.encode()

    def __init__(self, text: str, code: int = 1) -> None:
        self.stderr = self._Stderr(text)
        self.returncode = code
        self.pid = -1

    def poll(self):
        return self.returncode


class TheBannerIsLongerThanTheOldCap(unittest.TestCase):
    """The premise, asserted rather than asserted-in-prose: the reason can never
    fit inside 500 characters once the banner is in front of it."""

    def test_the_startup_banner_alone_exceeds_five_hundred_characters(self) -> None:
        self.assertGreater(
            len(_REAL_BANNER), 500,
            "if the banner were shorter than the old cap the truncation would "
            "have been harmless and this file would have no subject")


class AFailedStartRecordsTheWholeReason(unittest.TestCase):
    """Defect 1. RED at base: the recorded error stops after 500 characters."""

    def _record(self, text: str) -> str:
        mgr = LlamaManager()
        mgr._process = _DeadChild(text)
        mgr._startup_budget = 1
        self.assertFalse(mgr._wait_for_healthy(65535))
        return mgr.last_error

    def test_the_reason_after_the_banner_survives(self) -> None:
        recorded = self._record(_REAL_BANNER + _REAL_REASON + "\n")
        self.assertIn(
            _REAL_REASON, recorded,
            "the line that says WHY the server exited was dropped — this is the "
            "defect: the banner alone fills the old 500-character slice")

    def test_the_record_is_not_cut_off_mid_token(self) -> None:
        """The 2026-08-26 journal line ended at "8 | CPU : SS". A record that
        stops mid-word is not evidence anybody can act on."""
        text = _REAL_BANNER + _REAL_REASON + "\n"
        recorded = self._record(text)
        self.assertTrue(
            recorded.rstrip().endswith(_REAL_REASON),
            f"the record ends mid-stream: ...{recorded[-40:]!r}")

    def test_an_enormous_stderr_is_still_bounded(self) -> None:
        """Whole does not mean unbounded — a runaway child must not put megabytes
        into the journal. The bound has to be far above a real failure's size."""
        huge = _REAL_BANNER + ("x" * 400_000) + "\n" + _REAL_REASON + "\n"
        recorded = self._record(huge)
        self.assertLess(len(recorded), 200_000,
                        "an unbounded capture would let a runaway child flood "
                        "the journal")
        self.assertIn(_REAL_REASON, recorded,
                      "the bound must keep the END, where the reason is")


def _real_failing_launch_stderr(test: unittest.TestCase) -> str:
    """Start the REAL llama-server with a path that does not exist and return its
    whole stderr. Skips, naming the reason, when no binary is present."""
    server = shutil.which("llama-server") or "/opt/rocm/bin/llama-server"
    if not os.path.exists(server):
        test.skipTest(
            f"no llama-server binary at {server} — the real-launch proofs cannot "
            f"run here; the stubbed proofs still apply")
    proc = subprocess.run(
        [server, "--model", "/nonexistent/NO-SUCH-MODEL.gguf",
         "--port", "65534", "--ctx-size", "512", "--n-gpu-layers", "0",
         "--device", "none"],
        capture_output=True, timeout=120)
    test.assertNotEqual(proc.returncode, 0, "the launch was supposed to fail")
    return (proc.stderr or b"").decode(errors="replace")


class ARealFailingLaunchPutsItsReasonPastTheOldCap(unittest.TestCase):
    """THE PREMISE, against the real binary rather than a stub. This test does
    not touch the product's recording code and so cannot go red at base — it
    establishes the fact the recording tests rest on: on a real machine the
    reason a launch failed is never inside the first 500 characters."""

    def test_a_bad_model_path_puts_its_reason_past_the_old_cap(self) -> None:
        err = _real_failing_launch_stderr(self)
        self.assertGreater(
            len(err), 500,
            "a real failing launch prints more than the old cap kept")
        self.assertNotIn(
            "failed to load model", err[:500],
            "if the reason were inside the first 500 characters the old slice "
            "would have been adequate — it is not, which is the defect")
        self.assertIn("failed to load model", err,
                      "the reason is in the part the old slice discarded")


class TheProductRecordsARealFailingLaunchsRealReason(unittest.TestCase):
    """Defect 1 THROUGH THE PRODUCT, on real bytes. The stderr comes from a real
    failing launch of the real binary; the recording is the product's own. RED at
    base, because the recorder keeps the banner and drops the reason."""

    def test_the_recorded_error_carries_what_the_server_actually_said(self) -> None:
        err = _real_failing_launch_stderr(self)
        mgr = LlamaManager()
        mgr._process = _DeadChild(err)
        mgr._startup_budget = 1
        self.assertFalse(mgr._wait_for_healthy(65535))
        self.assertIn(
            "failed to load model", mgr.last_error,
            "the product recorded the banner and dropped the line that says why "
            "the real server exited — this is the defect, on real bytes")


class ATransientStartFailureIsRetried(unittest.TestCase):
    """Defect 2. RED at base: there is no transient class at all, and the daemon
    keeps the manager for PORT_IN_USE alone."""

    def test_the_failure_taxonomy_names_which_failures_a_retry_can_survive(
            self) -> None:
        for failure in (StartFailure.UNHEALTHY, StartFailure.PORT_IN_USE,
                        StartFailure.SPAWN_ERROR):
            with self.subTest(failure=failure.name):
                self.assertTrue(
                    failure.is_transient,
                    f"{failure.name} is exactly the shape the 2026-08-26 event "
                    f"took — the same command succeeded minutes later")

    def test_a_missing_file_or_binary_is_never_retried(self) -> None:
        for failure in (StartFailure.MODEL_FILE_ABSENT,
                        StartFailure.BINARY_ABSENT):
            with self.subTest(failure=failure.name):
                self.assertFalse(
                    failure.is_transient,
                    f"{failure.name} does not become true by waiting; retrying "
                    f"it spends the budget and delays the honest degrade")

    def test_an_integrity_failure_is_never_retried(self) -> None:
        """A declared capability the running server did not honor reads as tamper
        or corruption. Retrying tamper is not recovery."""
        for failure in (StartFailure.MMPROJ_MISSING,
                        StartFailure.CHAT_TEMPLATE_MISSING,
                        StartFailure.TOOLS_NOT_ADVERTISED,
                        StartFailure.VISION_NOT_ADVERTISED):
            with self.subTest(failure=failure.name):
                self.assertTrue(failure.is_integrity)
                self.assertFalse(failure.is_transient,
                                 "an integrity failure must never be retried")

    def test_the_two_classes_do_not_overlap(self) -> None:
        for failure in StartFailure:
            with self.subTest(failure=failure.name):
                self.assertFalse(failure.is_integrity and failure.is_transient)


class AStartThatFailsOnceThenSucceeds(unittest.TestCase):
    """Defect 2's behaviour: one transient failure does not end the attempt."""

    def _manager(self, outcomes: list[bool], failure: StartFailure):
        mgr = LlamaManager()
        mgr._config = {"model_path": "/stub.gguf"}   # a saved config to retry with
        seen = {"n": 0}

        def _start_saved():
            seen["n"] += 1
            ok = outcomes[min(seen["n"] - 1, len(outcomes) - 1)]
            mgr._last_failure = StartFailure.NONE if ok else failure
            mgr._last_error = "" if ok else f"stubbed {failure.name}"
            return ok

        mgr.start_saved_config = _start_saved
        return mgr, seen

    def test_a_transient_failure_is_tried_again_and_can_succeed(self) -> None:
        mgr, seen = self._manager([False, True], StartFailure.UNHEALTHY)
        slept: list[float] = []
        self.assertTrue(
            mgr.retry_transient_start(attempts=3, sleep=slept.append),
            "a transient failure followed by a success must end up started")
        self.assertEqual(seen["n"], 2, "it must stop as soon as it succeeds")
        self.assertEqual(len(slept), 1, "one back-off between the two attempts")

    def test_the_retry_is_bounded(self) -> None:
        mgr, seen = self._manager([False], StartFailure.UNHEALTHY)
        slept: list[float] = []
        self.assertFalse(mgr.retry_transient_start(attempts=3, sleep=slept.append))
        self.assertEqual(seen["n"], 3, "exactly the attempts asked for, no more")
        self.assertLessEqual(sum(slept), 30.0,
                             "the whole back-off must fit inside ~30 seconds so "
                             "a doomed start does not hold the daemon open")

    def test_a_non_transient_failure_is_not_retried_at_all(self) -> None:
        mgr, seen = self._manager([False], StartFailure.MODEL_FILE_ABSENT)
        self.assertFalse(mgr.retry_transient_start(attempts=3, sleep=lambda _s: None))
        self.assertEqual(seen["n"], 1,
                         "a missing model file must degrade immediately")


class AnUnreachableModelServerIsSaidSo(unittest.TestCase):
    """Defect 3. RED at base: a down server and an unservable generation produce
    the same sentence."""

    def test_the_two_situations_do_not_share_a_sentence(self) -> None:
        llm = LLMRouter(config=None)
        unservable = llm._servable_text("some garbage", "repetition")
        llm.note_transport_failure("Connection refused")
        unreachable = llm._servable_text("", "empty")
        self.assertNotEqual(
            unservable, unreachable,
            "a model that produced something unservable and a model server that "
            "is not running must not read the same to the person")

    def test_the_unreachable_text_does_not_ask_for_a_rephrasing(self) -> None:
        llm = LLMRouter(config=None)
        llm.note_transport_failure("Connection refused")
        text = llm._servable_text("", "empty").lower()
        self.assertNotIn("rephrase", text,
                         "rephrasing cannot start a server; asking for it wastes "
                         "the person's time and hides the real state")
        self.assertTrue(
            any(w in text for w in ("model server", "not running", "engine")),
            f"the reply must name what is wrong; it said: {text!r}")

    def test_a_recovered_transport_stops_saying_it(self) -> None:
        """The notice is about the CURRENT state, so a later successful request
        must clear it — otherwise one blip marks the session forever."""
        llm = LLMRouter(config=None)
        llm.note_transport_failure("Connection refused")
        llm.note_transport_ok()
        self.assertEqual(llm._servable_text("some garbage", "repetition"),
                         llm._EMPTY_RESPONSE_FALLBACK)


class _FakeManager:
    """Just enough LlamaManager for the daemon's status read."""

    def __init__(self, running: bool, last_error: str = "") -> None:
        self._running = running
        self.last_error = last_error

    def is_running(self) -> bool:
        return self._running


def _daemon(**attrs):
    """A daemon object with only the attributes the status read touches. The
    real class opens a bus, a model and a router; none of that decides this."""
    from intergen.dbus_daemon import InterGenDaemon
    d = InterGenDaemon.__new__(InterGenDaemon)
    d._paused = False
    d._llama = None
    d._model_server_down = None
    for k, v in attrs.items():
        setattr(d, k, v)
    return d


class TheStatusSurfaceSaysTheModelIsDown(unittest.TestCase):
    """Defect 3's other half. RED at base: there is no such reader, and a unit
    reporting itself active was the only signal a person got."""

    def test_a_healthy_server_reports_nothing(self) -> None:
        d = _daemon(_llama=_FakeManager(running=True))
        self.assertIsNone(d._model_server_down_now())

    def test_a_dropped_manager_is_reported_with_its_recorded_reason(self) -> None:
        d = _daemon(_llama=None, _model_server_down="UNHEALTHY: exited code 1")
        self.assertIn("UNHEALTHY", d._model_server_down_now())

    def test_a_server_that_died_after_a_healthy_start_is_reported_too(self) -> None:
        """The startup record alone would say "up" for the rest of the session."""
        d = _daemon(_llama=_FakeManager(running=False, last_error="killed"),
                    _model_server_down=None)
        reported = d._model_server_down_now()
        self.assertIsNotNone(reported)
        self.assertIn("killed", reported)

    def test_a_deliberate_pause_is_not_called_a_failure(self) -> None:
        """Paused for a game is down ON PURPOSE; naming that a failure would be a
        lie in the other direction."""
        d = _daemon(_paused=True, _llama=None,
                    _model_server_down="UNHEALTHY: exited code 1")
        self.assertIsNone(d._model_server_down_now())

    def test_a_partially_constructed_daemon_is_still_queryable(self) -> None:
        """status() is called on daemons that were built with __new__ and given
        only the fields a caller cared about, and on one whose start_service
        failed part-way. A status read that raises turns a degraded daemon into
        an unqueryable one, which is the opposite of the point. (This is the
        regression this lane actually caused and had to fix: reading the new
        fields directly made the existing daemon status tests raise.)"""
        from intergen.dbus_daemon import InterGenDaemon
        bare = InterGenDaemon.__new__(InterGenDaemon)   # no attributes at all
        self.assertIsNone(bare._model_server_down_now())

    def test_a_status_read_never_raises(self) -> None:
        class _Exploding:
            last_error = "boom"

            def is_running(self):
                raise RuntimeError("the manager blew up")

        d = _daemon(_llama=_Exploding())
        self.assertIsNotNone(d._model_server_down_now())


class TheManagerIsKeptForEveryTransientFailure(unittest.TestCase):
    """Defect 2's other half, as a statement about the RULE the daemon applies.

    The daemon kept the manager — and therefore the watchdog, which is created
    under `if self._llama:` — for PORT_IN_USE alone. The rule is now the failure
    taxonomy's own transient class, so the measured UNHEALTHY case is covered by
    the same code that already covered the port collision.
    """

    def test_every_transient_failure_keeps_the_manager(self) -> None:
        for failure in (StartFailure.PORT_IN_USE, StartFailure.UNHEALTHY,
                        StartFailure.SPAWN_ERROR):
            with self.subTest(failure=failure.name):
                self.assertTrue(
                    failure.is_transient,
                    f"{failure.name} must keep the manager so the watchdog exists")

    def test_the_old_rule_would_have_dropped_the_measured_failure(self) -> None:
        """The regression this file exists to prevent, stated as an assertion."""
        self.assertIsNot(StartFailure.UNHEALTHY, StartFailure.PORT_IN_USE)
        self.assertTrue(
            StartFailure.UNHEALTHY.is_transient,
            "the 2026-08-26 failure recorded UNHEALTHY; a rule that names only "
            "PORT_IN_USE drops it, which is what left the assistant dead")


if __name__ == "__main__":
    unittest.main()
