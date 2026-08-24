# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The privileged-boundary gate: real unit, real policy, no prompt reachable.

This is the gate the recovery plan requires for the privileged execution
boundary — one that runs against the REAL transient unit and the REAL PolicyKit
path and is STRUCTURALLY unable to reach a human authentication prompt. Fixtures
would not satisfy it: the whole class of defect being corrected here is a
composition property that only appears when the real pieces meet.

THE LEGS, and why each one cannot summon a dialog.

  LEG 1 — the transition itself.
    A transient unit started through this account's own systemd user manager
    reports no_new_privs 0, which is what makes the kernel honour a setuid
    binary. The NEGATIVE CONTROL is the shipped defect: the same unit shape
    carrying NoNewPrivileges=yes must both report no_new_privs 1 AND make the
    real setuid pkexec refuse with "pkexec must be setuid root". A gate that
    cannot produce that failure on demand has not shown it can detect it.
    No prompt is reachable: in the failing half pkexec refuses on its own
    effective uid before it consults PolicyKit at all, and the passing half
    reads a file in /proc and runs nothing setuid.

  LEG 2 — the real PolicyKit path.
    PolicyKit is queried directly for the action, for a real subject, WITHOUT
    permitting interaction. The installed pkcheck documents this exactly: with
    no --allow-user-interaction it does not block for authentication, and when
    authentication would be required it exits 2 with a diagnostic. That is a
    definite, assertable result which proves the action is registered and
    resolves to the administrator requirement the policy states — the real
    policy path, exercised, with no dialog reachable.

  LEG 3 — the /proc canary.
    While a process carrying the real dispatch argv is alive, /proc is scanned
    for the approval token and the tool arguments. Neither may appear. The
    POSITIVE CONTROL puts a canary on a process's own command line and requires
    the same scanner to find it: an instrument never shown to detect a true
    positive cannot certify a zero.

  LEG 4 — one transition per approved action.
    Lives in test_one_privilege_transition_per_action.py, which needs nothing
    to run.

DELIBERATELY NOT AUTOMATED. No leg here runs a privileged action end to end.
That needs a person to authenticate, and it belongs to release validation: an
automated test that can summon an authentication dialog is one that will
eventually summon it on somebody's desktop at three in the morning. What these
legs prove is everything up to, and not through, the authentication step.

ALSO NOT PROVEN HERE. Leg 1's passing half asserts the kernel flag, not a live
setuid transition — demonstrating the transition itself would mean running
pkexec somewhere it is honoured, which is the one thing that can reach a prompt.
The flag is the property the correction turns on, and the negative control
shows the flag's absence is what the failure was made of.

THE LIVE DAEMON IS NOT TOUCHED. Every unit started here is transient, is
collected on exit, and belongs to this account's manager — not to the assistant
service.
"""

from __future__ import annotations

import ast
import os
import shutil
import signal
import subprocess
import sys
import time
import unittest
from xml.etree import ElementTree

from intergen import tool_registry as tr
from intergen.tool_registry import ToolRegistry
from intergen.interfaces.types import ToolCall
from intergen.interfaces.provenance import Provenance


#: The action the shipped policy binds to the privileged runner's exec.path.
POLICY_ACTION = "org.intergenos.intergen.privileged-tool"

#: pkexec's refusal when the kernel declined to apply its setuid bit. This exact
#: string is the shipped defect's signature.
SETUID_REFUSAL = "pkexec must be setuid root"

#: How long a probe unit may take before the gate calls it a failure rather than
#: waiting on it. Generous: these are sub-second in practice.
PROBE_TIMEOUT = 60

#: The one pkcheck flag that lets it block for authentication — the only thing
#: in this file's reach that could raise a dialog.
#:
#: Built by concatenation on purpose. A test below reads THIS FILE's syntax tree
#: and refuses any string constant equal to the flag; if the needle were written
#: out as a literal here, the checker would match its own comparison operand and
#: report itself. Assembling it at runtime means the file genuinely contains no
#: such constant, so the check measures the invocations rather than itself.
INTERACTION_FLAG = "--allow-user-" + "interaction"


def _systemd_run_available() -> tuple[bool, str]:
    """Is a systemd user manager reachable for this account?

    Returns (available, reason-if-not). The reason is carried into the skip so
    a skipped gate says exactly which measurement did not happen, rather than
    reading as a pass.
    """
    if shutil.which("systemd-run") is None:
        return False, "systemd-run is not installed"
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        return False, "XDG_RUNTIME_DIR is unset, so no user manager is addressable"
    socket = os.path.join(runtime, "systemd", "private")
    if not os.path.exists(socket):
        return False, f"no systemd user manager is running ({socket} absent)"
    return True, ""


def _run_in_transient_unit(command, *, properties=()):
    """Run `command` in a transient unit of this account's user manager.

    Deliberately mirrors the shape the dispatcher uses: --user, quiet,
    collected, waited on, piped. `properties` lets the negative control impose
    NoNewPrivileges=yes, which is the ONLY reason this helper takes properties
    at all — the real dispatch imposes none.
    """
    argv = ["systemd-run", "--user", "--quiet", "--collect", "--wait", "--pipe"]
    for prop in properties:
        argv += ["--property", prop]
    argv += ["--"] + list(command)
    return subprocess.run(
        argv, capture_output=True, text=True, check=False, timeout=PROBE_TIMEOUT,
    )


AVAILABLE, UNAVAILABLE_REASON = _systemd_run_available()


@unittest.skipUnless(
    AVAILABLE,
    f"the privilege-transition leg was NOT measured: {UNAVAILABLE_REASON}",
)
class Leg1TransitionInATransientUnitTests(unittest.TestCase):
    """Leg 1 — the transition, with its failing negative control."""

    def test_a_transient_unit_does_not_carry_no_new_privs(self):
        """The positive half: this is the property the correction turns on."""
        completed = _run_in_transient_unit(
            ["/bin/grep", "NoNewPrivs", "/proc/self/status"],
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "NoNewPrivs:\t0", completed.stdout,
            f"a unit started by the user manager carries no_new_privs, so the "
            f"kernel would still refuse to honour a setuid binary in it: "
            f"{completed.stdout!r}",
        )

    def test_the_flagged_unit_still_carries_it(self):
        """Control on the instrument: the harness can tell the two apart."""
        completed = _run_in_transient_unit(
            ["/bin/grep", "NoNewPrivs", "/proc/self/status"],
            properties=("NoNewPrivileges=yes",),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("NoNewPrivs:\t1", completed.stdout, completed.stdout)

    def test_the_caller_of_this_gate_is_not_what_is_being_measured(self):
        """Second control: the measurement is of the UNIT, not of this process.

        Without this, a gate run from an unflagged shell could report a pass
        that says nothing about what the manager does.
        """
        with open("/proc/self/status", encoding="utf-8") as fh:
            own = [line for line in fh if line.startswith("NoNewPrivs")]
        self.assertTrue(own, "/proc/self/status carries no NoNewPrivs line")
        unit = _run_in_transient_unit(
            ["/bin/grep", "NoNewPrivs", "/proc/self/status"],
        ).stdout.strip()
        self.assertIn("NoNewPrivs:\t0", unit)

    @unittest.skipUnless(
        os.path.exists("/usr/bin/pkexec"),
        "the negative control was NOT measured: /usr/bin/pkexec is absent",
    )
    def test_negative_control_the_flagged_unit_reproduces_the_shipped_failure(self):
        """THE NEGATIVE CONTROL. The shipped defect, on demand.

        This runs the real setuid pkexec — and it is safe to do so precisely
        because of what is being proven: under NoNewPrivileges=yes the kernel
        does not apply the setuid bit, so pkexec starts as this user, sees its
        own effective uid is not root, and refuses. It never contacts PolicyKit,
        so no authentication agent is ever asked for anything. The command it is
        given is /bin/true, which is never reached.

        SELF-GUARDING (2026-08-24). That safety argument rests on one premise —
        the flag really applied — and the premise used to be proven by a
        DIFFERENT test in this class, which unittest orders alphabetically and
        therefore runs AFTER this one. The risky invocation happened first and
        its premise was checked afterwards. If a future systemd, a manager
        configuration or a drop-in ever declined that property, the first thing
        to discover it would have been a real authentication dialog on somebody's
        desktop — and this file SHIPS, so that somebody need not be a developer.
        An independent review also measured that the file cannot be reliably
        excluded from outside: its pytest node ID changes with the layout it is
        run from, so a path-shaped --deselect silently matched nothing and the
        test ran anyway.

        So the premise is measured HERE, in the same unit shape, immediately
        before the pkexec invocation, and the test skips loudly rather than
        proceeding if it does not hold. Same call, no added risk, and the order
        is no longer something an alphabet decides.
        """
        premise = _run_in_transient_unit(
            ["/bin/grep", "NoNewPrivs", "/proc/self/status"],
            properties=("NoNewPrivileges=yes",),
        )
        if premise.returncode != 0 or "NoNewPrivs:\t1" not in premise.stdout:
            self.skipTest(
                "REFUSING TO RUN THE NEGATIVE CONTROL: a unit asked to carry "
                "NoNewPrivileges=yes did not come back carrying it "
                f"(rc={premise.returncode}, stdout={premise.stdout!r}, "
                f"stderr={premise.stderr!r}). Under that condition invoking "
                "setuid pkexec could reach a real authentication prompt, which "
                "this gate must never be able to do."
            )

        completed = _run_in_transient_unit(
            ["/usr/bin/pkexec", "/bin/true"],
            properties=("NoNewPrivileges=yes",),
        )
        self.assertEqual(
            completed.returncode, 127,
            f"expected pkexec's setuid refusal (127); got "
            f"{completed.returncode} with stdout={completed.stdout!r} "
            f"stderr={completed.stderr!r}",
        )
        self.assertIn(
            SETUID_REFUSAL, completed.stderr,
            f"the negative control did not reproduce the shipped failure, so "
            f"this gate has not shown it can detect it: {completed.stderr!r}",
        )

    @unittest.skipUnless(
        os.path.exists("/usr/bin/pkexec"),
        "the setuid-mode check was NOT measured: /usr/bin/pkexec is absent",
    )
    def test_pkexec_is_actually_setuid_root(self):
        """The premise the negative control rests on. If pkexec were not setuid
        the refusal above would be true for an entirely different reason."""
        info = os.stat("/usr/bin/pkexec")
        self.assertEqual(info.st_uid, 0, "pkexec is not owned by root")
        self.assertTrue(
            info.st_mode & 0o4000,
            f"pkexec is not setuid (mode {info.st_mode & 0o7777:o})",
        )


@unittest.skipUnless(
    shutil.which("pkcheck") is not None,
    "the PolicyKit leg was NOT measured: pkcheck is not installed",
)
class Leg2RealPolicyKitPathTests(unittest.TestCase):
    """Leg 2 — the real policy path, queried with interaction withheld.

    Every invocation here omits --allow-user-interaction. The installed man page
    states that without it pkcheck does not block for authentication and exits 2
    with a diagnostic when authentication would be required. No dialog is
    reachable from any of these.
    """

    def _pkcheck(self, action):
        return subprocess.run(
            ["pkcheck", "--action-id", action, "--process", str(os.getpid())],
            capture_output=True, text=True, check=False, timeout=PROBE_TIMEOUT,
        )

    def test_no_invocation_here_permits_interaction(self):
        """A structural assertion about this file itself: the one flag that
        could raise a dialog is not passed by any code in it.

        Read from the syntax tree, matching a string constant EXACTLY. Prose
        that names the flag — this file explains at length why it is withheld —
        is a substring of a docstring, never an argument, and the difference is
        the whole point of checking structurally rather than by grep.
        """
        with open(__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        offenders = [
            getattr(node, "lineno", "?")
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and node.value == INTERACTION_FLAG
        ]
        self.assertEqual(
            offenders, [],
            f"this gate passes the flag that lets pkcheck block for "
            f"authentication, at line(s) {offenders}; it must be structurally "
            f"unable to reach a prompt",
        )

    def test_the_structural_check_can_see_a_planted_flag(self):
        """Positive control on the check above.

        The planted source is assembled from INTERACTION_FLAG rather than typed
        out, for the same reason the needle is: a literal here would be a
        constant in this file and the checker would flag it.
        """
        planted = ast.parse(
            'subprocess.run(["pkcheck", "%s"])' % INTERACTION_FLAG
        )
        found = [
            node.value for node in ast.walk(planted)
            if isinstance(node, ast.Constant)
            and node.value == INTERACTION_FLAG
        ]
        self.assertEqual(
            found, [INTERACTION_FLAG],
            "the structural check cannot see a planted interaction flag, so "
            "its clean result over this file proves nothing",
        )

    @unittest.skipUnless(
        os.path.exists(
            f"/usr/share/polkit-1/actions/{POLICY_ACTION.rsplit('.', 1)[0]}.policy"
        ),
        f"the action-resolution leg was NOT measured: the installed policy file "
        f"for {POLICY_ACTION} is absent",
    )
    def test_the_action_is_registered_and_requires_an_administrator(self):
        completed = self._pkcheck(POLICY_ACTION)
        combined = completed.stdout + completed.stderr
        self.assertNotEqual(
            combined.strip(), "",
            "pkcheck said nothing about the action at all",
        )
        self.assertIn(
            "auth_admin", combined,
            f"the action does not resolve to an administrator requirement: "
            f"{combined!r}",
        )
        self.assertEqual(
            completed.returncode, 2,
            f"expected exit 2 (authentication required, interaction withheld); "
            f"got {completed.returncode}: {combined!r}",
        )

    def test_an_unregistered_action_is_reported_differently(self):
        """Control on the instrument: pkcheck distinguishes 'this action needs
        authentication' from 'there is no such action'. Without this, the case
        above could be passing on a generic failure."""
        completed = self._pkcheck("org.intergenos.intergen.no-such-action-exists")
        combined = completed.stdout + completed.stderr
        self.assertNotIn(
            "auth_admin", combined,
            f"an action that does not exist resolved to an administrator "
            f"requirement, so the check above proves nothing: {combined!r}",
        )

    #: Where polkit records which program an action is allowed to run. The
    #: annotation is the binding; the rest of the file is prose around it.
    EXEC_PATH_ANNOTATION = "org.freedesktop.policykit.exec.path"

    def _policy_action_element(self):
        """Return the <action> element for THIS action, or skip if absent."""
        policy = f"/usr/share/polkit-1/actions/{POLICY_ACTION.rsplit('.', 1)[0]}.policy"
        if not os.path.exists(policy):
            self.skipTest(
                f"the policy-binding leg was NOT measured: {policy} is absent"
            )
        tree = ElementTree.parse(policy)
        for action in tree.getroot().iter("action"):
            if action.get("id") == POLICY_ACTION:
                return action
        self.fail(
            f"{policy} contains no <action> whose id is {POLICY_ACTION}"
        )

    def test_the_policy_binds_the_action_to_the_runner_path(self):
        """What the user authenticates against is the narrow, purpose-built
        action bound to this one executable — not a general 'run anything as
        root' action. That narrowness is the design's whole argument.

        PARSED, NOT SEARCHED (2026-08-24). This used to assert that the action
        id and the runner path each appeared SOMEWHERE in the file. An
        independent review pointed out what that actually establishes: an XML
        comment satisfies it, a different <action> block satisfies it, and the
        runner path annotated onto some other action satisfies it. None of
        those is the binding, and this is the leg that certifies the binding.

        So the element is selected by id and its own exec.path annotation is
        compared for EQUALITY with the runner path.
        """
        action = self._policy_action_element()
        annotations = {
            a.get("key"): (a.text or "").strip()
            for a in action.iter("annotate")
        }
        self.assertIn(
            self.EXEC_PATH_ANNOTATION, annotations,
            f"the action carries no {self.EXEC_PATH_ANNOTATION} annotation, so "
            f"it is not bound to any particular program: {annotations}",
        )
        self.assertEqual(
            annotations[self.EXEC_PATH_ANNOTATION], tr._PKEXEC_RUNNER_PATH,
            "the action is bound to a different program than the one this "
            "code dispatches through",
        )

    def test_the_action_requires_an_administrator_for_an_active_session(self):
        """The other half of the binding: what a person has to do to satisfy it.

        Also parsed rather than searched, and asserted on THIS action's own
        <defaults>, because a permissive default on the action that matters is
        not made safe by a strict one elsewhere in the file.
        """
        action = self._policy_action_element()
        defaults = action.find("defaults")
        self.assertIsNotNone(defaults, "the action declares no <defaults>")
        allow_active = defaults.find("allow_active")
        self.assertIsNotNone(
            allow_active, "the action declares no allow_active default")
        self.assertEqual(
            (allow_active.text or "").strip(), "auth_admin_keep",
            "an active session does not have to authenticate as an "
            "administrator for this action",
        )


def _long_lived_process_carrying(words):
    """Start a process whose command line carries `words`, and keep it alive.

    MEASURED, not assumed: `/bin/sleep 30 <word>` does NOT work for this —
    sleep rejects a non-numeric second argument and exits immediately, leaving
    a zombie whose /proc/<pid>/cmdline reads empty. A scan against it finds
    nothing and looks like a clean result, which is precisely the false zero
    this gate exists to avoid. The first version of this helper had exactly
    that defect and its own positive control caught it.

    Python is used instead: it sleeps without execing away (a shell can
    exec-optimise its last command and drop the extra argv), and any additional
    arguments land in sys.argv untouched, so every word stays visible in /proc
    for the life of the process.
    """
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"] + list(words),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait_until_visible(needle, timeout=10):
    """Poll /proc until `needle` shows up, or the timeout expires.

    Returns whether it became visible. Polling rather than assuming: a freshly
    started process is not necessarily scheduled and readable the instant
    Popen returns, and a scan run too early would report a zero that means
    nothing.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if needle in _scan_proc_for([needle]):
            return True
        time.sleep(0.05)
    return False


def _scan_proc_for(needles):
    """Return the needles that appear in ANY readable process's command line.

    Read-only throughout: opens /proc/<pid>/cmdline and nothing else. Processes
    that vanish or that this account may not read are skipped, which is the
    honest limit of the instrument and is stated in the gate's own controls.
    """
    found = set()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as fh:
                raw = fh.read()
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            continue
        text = raw.replace(b"\0", b" ").decode("utf-8", "replace")
        for needle in needles:
            if needle in text:
                found.add(needle)
    return found


class Leg3ProcCanaryTests(unittest.TestCase):
    """Leg 3 — nothing protected is visible in any process's command line."""

    CANARY_TOKEN = "boundary-canary-token-3f9d2a71c4e8"
    CANARY_ARG = "boundary-canary-argument-8b17e5cd0a2f"
    LIVENESS_MARKER = "boundary-liveness-marker-6d4c8e02b9f1"

    def test_positive_control_the_scanner_can_see_a_value_that_is_there(self):
        """The instrument proves itself before it is trusted to report a zero."""
        proc = _long_lived_process_carrying([self.CANARY_TOKEN])
        try:
            self.assertTrue(
                _wait_until_visible(self.CANARY_TOKEN),
                "the scanner cannot see a value that is demonstrably on a "
                "process's command line, so a clean scan proves nothing",
            )
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)

    def test_negative_control_the_scanner_does_not_invent_a_value(self):
        """The other half of the instrument: a value nowhere on the machine is
        not reported. Without this, a scanner that returned every needle it was
        handed would pass the positive control."""
        self.assertEqual(
            _scan_proc_for(["value-that-exists-nowhere-4a7f13d6"]),
            set(),
        )

    def test_the_real_dispatch_argv_carries_nothing_protected(self):
        """The real argv, alive in /proc, scanned for the real secrets.

        The dispatcher's argv is captured from the real code path, then a
        process is started carrying those exact words so the scan measures what
        /proc would actually show during a dispatch. sleep stands in for the
        program; the words are what this leg is about.
        """
        captured = {}

        def _capture(argv, **kwargs):
            captured["argv"] = list(argv)

            class _R:
                returncode = 0
                stdout = "ok"
                stderr = ""
            return _R()

        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory(prefix="privboundary-canary-") as runtime:
            with mock.patch.dict(
                    os.environ, {"XDG_RUNTIME_DIR": runtime}, clear=False), \
                    mock.patch.object(tr.subprocess, "run", side_effect=_capture):
                ToolRegistry._dispatch_via_pkexec(
                    ToolCall(
                        name="manage_packages",
                        arguments={"action": "install",
                                   "package": self.CANARY_ARG},
                        call_id="proc-canary",
                        source_of_request=list(Provenance)[0],
                    ),
                    "manage_packages",
                    {"action": "install", "package": self.CANARY_ARG},
                    self.CANARY_TOKEN,
                )

        argv = captured.get("argv")
        self.assertIsNotNone(argv, "the dispatcher built no command at all")

        # A marker travels alongside the real argv so liveness is proven by
        # something this test placed, not by a word that might coincidentally
        # appear elsewhere on the machine.
        started = _long_lived_process_carrying([self.LIVENESS_MARKER] + argv)
        try:
            self.assertTrue(
                _wait_until_visible(self.LIVENESS_MARKER),
                "the process carrying the dispatch argv never became visible "
                "in /proc, so this scan measured nothing",
            )
            visible = _scan_proc_for([self.CANARY_TOKEN, self.CANARY_ARG])
            self.assertNotIn(
                self.CANARY_TOKEN, visible,
                f"the approval token is visible in a process command line; "
                f"the dispatch argv was {argv}",
            )
            self.assertNotIn(
                self.CANARY_ARG, visible,
                f"the tool arguments are visible in a process command line; "
                f"the dispatch argv was {argv}",
            )
        finally:
            started.send_signal(signal.SIGTERM)
            started.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
