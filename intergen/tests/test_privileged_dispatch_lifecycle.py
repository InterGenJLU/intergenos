# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The approval is spent when the action is taken, and the attempt leaves a record.

An independent review of this branch found that root consumed the human's
single-use approval-nonce BEFORE it decided whether it would act at all: the
consume happened immediately after the token verified, and tool discovery, tool
resolution, argument validation and the BLOCKED check all came afterwards. Two
things follow from that ordering, and both are real.

  * A dispatch REFUSED for any of those four reasons has already burned the
    approval. The person fixes an argument, asks again, and is made to
    authenticate a second time for an action that never ran once.

  * An outcome that is not known — the unit killed at its ceiling, the daemon
    dying, a wait cut short — leaves the approval spent and NOTHING recorded
    about whether the tool ran. "Did it happen?" has no answer, and a fresh
    approval does not make a retry safe: a token proves a person authorized the
    action, not that the previous attempt failed to perform it.

These tests pin both halves of the correction.

  1. CONSUMPTION MARKS ACTION, NOT INTENT. Every refusal path leaves the nonce
     unspent, provable by the same token succeeding immediately afterwards.
     The single-use guarantee is unchanged: once an execute is reached the
     nonce is consumed, and a replay of that token is refused.

  2. THE ATTEMPT IS RECORDED. A root-side store carries the transitions —
     claimed, consumed, executing, and a terminal state — so an interrupted
     dispatch is a state a reader can consult instead of a silence. The
     executing record must be on disk BEFORE the tool runs, which is the only
     way it can survive the process that was running it; a test proves that by
     reading the store from inside the tool.

Nothing here executes a privileged action, contacts PolicyKit or runs pkexec.
The registry is mocked, the signing key is injected, and both stores are
redirected into a tempdir.
"""

from __future__ import annotations

import os
import pwd
import tempfile
import time
import unittest
from unittest import mock

from intergen import dispatch_token as dt
from intergen import privileged_dispatch as pd
from intergen import privileged_request as pr
from intergen.interfaces.types import SafetyTier, ToolResult


_KEY = "ab" * dt.KEY_BYTES
_TOOL = "manage_services"
_ARGS = {"action": "restart", "unit": "sshd"}


class _Tool:
    """A discovered privileged tool whose every behaviour a test can choose."""

    def __init__(self, sink, *, validation_error=None,
                 safety=SafetyTier.CONFIRM, raises=None, success=True,
                 on_execute=None):
        self._sink = sink
        self._validation_error = validation_error
        self._safety = safety
        self._raises = raises
        self._success = success
        self._on_execute = on_execute

    def validate_arguments(self, arguments):
        return self._validation_error

    def classify_safety(self, arguments):
        return self._safety

    def execute(self, arguments):
        self._sink["executed"] = self._sink.get("executed", 0) + 1
        if self._on_execute is not None:
            self._on_execute()
        if self._raises is not None:
            raise self._raises
        return ToolResult(call_id="c", name=_TOOL, content="did it",
                          success=self._success)


class _LifecycleTestCase(unittest.TestCase):
    def setUp(self):
        self.uid = os.getuid()
        self.user = pwd.getpwuid(self.uid).pw_name
        self.sink = {}

        self._store = tempfile.TemporaryDirectory(prefix="privlifecycle-")
        self.addCleanup(self._store.cleanup)
        for name, value in (
            ("_CONSUMED_NONCE_DIR", self._store.name),
            ("_CONSUMED_NONCE_PATH",
             os.path.join(self._store.name, "consumed-nonces")),
            ("_DISPATCH_STATE_PATH",
             os.path.join(self._store.name, "dispatch-states")),
        ):
            patcher = mock.patch.object(pd, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        # One runtime tree of the production shape: the unprivileged side reads
        # $XDG_RUNTIME_DIR, the privileged side derives <RUNTIME_ROOT>/<uid>.
        self._runtime = tempfile.TemporaryDirectory(prefix="privlifecycle-rt-")
        self.addCleanup(self._runtime.cleanup)
        self._uid_dir = os.path.join(self._runtime.name, str(self.uid))
        os.makedirs(self._uid_dir, mode=0o700, exist_ok=True)
        root_patch = mock.patch.object(pr, "RUNTIME_ROOT", self._runtime.name)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        env = mock.patch.dict(os.environ, {
            "XDG_RUNTIME_DIR": self._uid_dir,
            "PKEXEC_UID": str(self.uid),
            "PKEXEC_USER": self.user,
        }, clear=False)
        env.start()
        self.addCleanup(env.stop)

        key_patch = mock.patch.object(dt, "load_dispatch_key", return_value=_KEY)
        key_patch.start()
        self.addCleanup(key_patch.stop)

        self._reg_patch = mock.patch.object(pd, "ToolRegistry")
        reg_cls = self._reg_patch.start()
        self.addCleanup(self._reg_patch.stop)
        self.registry = reg_cls.return_value
        self.registry.discover_tools.return_value = 1
        self.set_tool(_Tool(self.sink))

    def set_tool(self, tool):
        self.registry.get_tool.return_value = tool

    def mint(self, *, tool=_TOOL, args=None, ttl=120):
        return dt.mint_token(tool, args if args is not None else _ARGS,
                             self.uid, key=_KEY, ttl_seconds=ttl)

    def dispatch(self, token, *, tool=_TOOL, args=None):
        """Write a request the way the unprivileged side does and dispatch it.

        Returns (exit_code, raised) — a refusal exits, an execute returns.
        """
        path = pr.write_request(tool, args if args is not None else _ARGS,
                                token)
        try:
            return pd.main(["--request-id", pr.request_id_for(path)]), None
        except SystemExit as exc:
            return exc.code, exc

    def states(self, nonce):
        """Every state recorded for `nonce`, oldest first."""
        return pd.recorded_states(nonce)

    def nonce_of(self, token):
        body = token.rsplit(".", 1)[0]
        return dt.verify_token(token, _TOOL, _ARGS, self.uid,
                               username=self.user).nonce


class TheApprovalIsSpentOnlyWhenTheActionIsTakenTests(_LifecycleTestCase):

    def test_an_undiscovered_tool_does_not_burn_the_approval(self):
        token = self.mint()
        self.registry.get_tool.return_value = None
        code, _ = self.dispatch(token)
        self.assertNotEqual(code, 0)
        self.assertNotIn("executed", self.sink)

        # The same approval must still work: it was never acted on.
        self.registry.get_tool.return_value = _Tool(self.sink)
        code, _ = self.dispatch(token)
        self.assertEqual(code, 0, "the refusal above burned the approval")
        self.assertEqual(self.sink.get("executed"), 1)

    def test_an_empty_registry_does_not_burn_the_approval(self):
        token = self.mint()
        self.registry.discover_tools.return_value = 0
        code, _ = self.dispatch(token)
        self.assertNotEqual(code, 0)

        self.registry.discover_tools.return_value = 1
        code, _ = self.dispatch(token)
        self.assertEqual(code, 0, "the refusal above burned the approval")

    def test_a_validation_refusal_does_not_burn_the_approval(self):
        token = self.mint()
        self.set_tool(_Tool(self.sink, validation_error="unit is not a unit"))
        code, _ = self.dispatch(token)
        self.assertNotEqual(code, 0)
        self.assertNotIn("executed", self.sink)

        self.set_tool(_Tool(self.sink))
        code, _ = self.dispatch(token)
        self.assertEqual(code, 0, "a rejected argument cost a real approval")

    def test_a_blocked_classification_does_not_burn_the_approval(self):
        token = self.mint()
        self.set_tool(_Tool(self.sink, safety=SafetyTier.BLOCKED))
        code, _ = self.dispatch(token)
        self.assertNotEqual(code, 0)
        self.assertNotIn("executed", self.sink)

        self.set_tool(_Tool(self.sink))
        code, _ = self.dispatch(token)
        self.assertEqual(code, 0, "the refusal above burned the approval")

    def test_single_use_still_holds_once_the_action_is_taken(self):
        """The reordering must not weaken replay defense, which is the reason
        the store exists at all."""
        token = self.mint()
        code, _ = self.dispatch(token)
        self.assertEqual(code, 0)
        self.assertEqual(self.sink.get("executed"), 1)

        code, _ = self.dispatch(token)
        self.assertNotEqual(code, 0, "a spent approval was accepted twice")
        self.assertEqual(self.sink.get("executed"), 1,
                         "the tool ran a second time on one approval")


class TheAttemptLeavesAReadableRecordTests(_LifecycleTestCase):

    def test_a_completed_dispatch_records_every_transition_in_order(self):
        token = self.mint()
        nonce = self.nonce_of(token)
        code, _ = self.dispatch(token)
        self.assertEqual(code, 0)
        self.assertEqual(
            self.states(nonce),
            [pd.STATE_CLAIMED, pd.STATE_CONSUMED, pd.STATE_EXECUTING,
             pd.STATE_TERMINAL_OK],
        )

    def test_a_refusal_records_a_terminal_state_and_no_consumption(self):
        token = self.mint()
        nonce = self.nonce_of(token)
        self.set_tool(_Tool(self.sink, safety=SafetyTier.BLOCKED))
        self.dispatch(token)
        recorded = self.states(nonce)
        self.assertEqual(recorded[0], pd.STATE_CLAIMED)
        self.assertEqual(recorded[-1], pd.STATE_TERMINAL_REFUSED)
        self.assertNotIn(pd.STATE_CONSUMED, recorded)
        self.assertNotIn(pd.STATE_EXECUTING, recorded)

    def test_a_tool_that_raises_records_a_terminal_state_not_a_silence(self):
        token = self.mint()
        nonce = self.nonce_of(token)
        self.set_tool(_Tool(self.sink, raises=RuntimeError("boom")))
        self.dispatch(token)
        recorded = self.states(nonce)
        self.assertIn(pd.STATE_EXECUTING, recorded)
        self.assertEqual(recorded[-1], pd.STATE_TERMINAL_RAISED)

    def test_a_failing_tool_is_a_terminal_state_of_its_own(self):
        token = self.mint()
        nonce = self.nonce_of(token)
        self.set_tool(_Tool(self.sink, success=False))
        code, _ = self.dispatch(token)
        self.assertEqual(code, 1)
        self.assertEqual(self.states(nonce)[-1], pd.STATE_TERMINAL_FAILED)

    def test_the_executing_record_is_on_disk_before_the_tool_runs(self):
        """This is the record's whole purpose. If it were written afterwards it
        could not survive the process it exists to describe, and an interrupted
        dispatch would be exactly as silent as before."""
        token = self.mint()
        nonce = self.nonce_of(token)
        seen = {}

        def _look():
            seen["states"] = self.states(nonce)

        self.set_tool(_Tool(self.sink, on_execute=_look))
        code, _ = self.dispatch(token)
        self.assertEqual(code, 0)
        self.assertIn("states", seen, "the tool never ran")
        self.assertEqual(
            seen["states"][-1], pd.STATE_EXECUTING,
            "at the moment the tool was running, the store did not say so; an "
            "interrupted dispatch would leave no evidence it had begun",
        )

    def test_an_interrupted_dispatch_leaves_executing_as_the_last_word(self):
        """The ambiguous outcome, modelled: the process dies inside the tool.
        What survives must say the action had begun."""
        token = self.mint()
        nonce = self.nonce_of(token)
        self.set_tool(_Tool(self.sink, raises=KeyboardInterrupt()))
        with self.assertRaises(KeyboardInterrupt):
            self.dispatch(token)
        self.assertEqual(self.states(nonce)[-1], pd.STATE_EXECUTING)

    def test_the_record_is_pruned_the_way_the_nonce_store_is(self):
        """A store that only grows is a store that eventually fails to write,
        and this one is consulted at the privilege boundary."""
        token = self.mint(ttl=1)
        nonce = self.nonce_of(token)
        self.dispatch(token)
        self.assertTrue(self.states(nonce))
        pd._prune_states(now=int(time.time()) + 3600)
        self.assertEqual(self.states(nonce), [])


if __name__ == "__main__":
    unittest.main()
