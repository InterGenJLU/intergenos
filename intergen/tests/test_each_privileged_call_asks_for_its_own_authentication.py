# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Each privileged call asks for its own administrator authentication.

THE PROPERTY (decided 2026-10-22: one approval card is one call). The
administrator authentication that lets a privileged tool run as root covers that
one run and nothing after it, and the prompt says which kind of action it is
for.

WHY A KEEP WINDOW IS WRONG HERE. pkexec asks PolicyKit about its PARENT process
(polkit 127, src/programs/pkexec.c: the subject is built from getppid()). Every
privileged runner is started as a transient unit of the person's systemd user
manager, so every runner has the same parent: the manager. Under auth_admin_keep
PolicyKit stores a successful authentication for that subject for five minutes,
and any process of the account can ask the same manager to start another runner
inside that window; it would run as root with no prompt. auth_admin stores
nothing, so each runner is its own authentication.

THE TEST AGENT. Nothing here talks to the PolicyKit daemon, runs pkexec or can
raise a dialog. The daemon's decision for one pkexec of the runner is modelled
from polkit 127's source, the version this tree builds, and each step names
where it comes from:

  * the action: pkexec.c find_action_for_path — the first action whose
    org.freedesktop.policykit.exec.path equals the program and, when the action
    carries org.freedesktop.policykit.exec.argv1, whose argv1 equals the first
    argument; no match falls back to org.freedesktop.policykit.exec, which
    polkit's own policy makes auth_admin;
  * the subject: pkexec's parent. For a runner started as `systemd-run --user
    ... -- pkexec ...` pkexec is the unit's main process, so its parent is the
    user manager; Leg 4 below OBSERVES that on this machine with two real
    transient units (no setuid program, nothing that can prompt);
  * the answer for an active local session: the action's allow_active default;
  * a kept authorization: polkitbackendinteractiveauthority.c stores one only
    after a successful challenge under auth_admin_keep or auth_self_keep (767-792),
    for that subject and action, for expiration-seconds, 5 * 60 by default (511),
    and grants from it, before any challenge, when the action id and the subject
    match (1290-1301);
  * the challenge: the agent is asked, answers, and the prompt is counted, with
    the message the action declares.

The argv each dispatch would run is captured from the real code path
(ToolRegistry._dispatch_via_pkexec with subprocess.run replaced by a recorder,
and the user manager's unit search path, which the launcher reads before every
start, stood in by a directory that does not exist, so no drop-in applies and
the read does not reach the recorder), and the policy is the one this tree ships. Proven by the instrument's own
controls below: under a keep policy the agent counts ONE prompt for two calls
inside the window and two when the second call comes after it.

NOT PROVEN HERE: that the real daemon and the real desktop agent count the same.
That is the release firing on an installed machine with this policy installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
import importlib.util
from pathlib import Path
from unittest import mock

from intergen import tool_process
from intergen import tool_registry as tr
from intergen.interfaces.provenance import Provenance
from intergen.interfaces.types import ToolCall
from intergen.tests.escalation_posture import (
    escalation_allowed_for_a_stubbed_dispatch,
)
from intergen.tool_registry import ToolRegistry, _PRIVILEGED_TOOLS

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY = REPO_ROOT / "intergen" / "data" / "org.intergenos.intergen.policy"
RUNNER_SCRIPT = REPO_ROOT / "intergen" / "data" / "intergen-privileged-runner"

#: The D-008 gates' reader of the policy (scripts/lib/check-privileged-runner-policy.py),
#: which reads it as polkit 127 does: intergen has one reader of its policy.
_READER_SPEC = importlib.util.spec_from_file_location(
    "check_privileged_runner_policy", REPO_ROOT / "scripts" / "lib" / "check-privileged-runner-policy.py")
policy_reader = importlib.util.module_from_spec(_READER_SPEC)
_READER_SPEC.loader.exec_module(policy_reader)

EXEC_PATH = "org.freedesktop.policykit.exec.path"
EXEC_ARGV1 = "org.freedesktop.policykit.exec.argv1"

#: polkit's generic pkexec action, the fallback for a program and first
#: argument no action claims: auth_admin (polkit 127,
#: actions/org.freedesktop.policykit.policy.in), with the message pkexec itself
#: supplies for it when the target user is root (pkexec.c 887).
FALLBACK = {
    "id": "org.freedesktop.policykit.exec",
    "allow_active": "auth_admin",
    "message": "Authentication is needed to run `$(cmdline_short)' as the super user",
}

#: polkitbackendinteractiveauthority.c: expiration-seconds defaults to 5 * 60.
KEEP_SECONDS = 5 * 60

#: pkexec's parent for every runner the dispatcher starts: the person's user
#: manager (observed by Leg 4).
USER_MANAGER = "the person's systemd user manager"

#: The kind of action each tool's prompt must name.
CLASS_WORDS = {
    "manage_services": "system service",
    "manage_packages": "software",
    "run_command": "command",
    "write_file": "system file",
}

ARGUMENTS = {
    "manage_services": {"action": "restart", "unit": "sshd"},
    "manage_packages": {"action": "install", "package": "htop"},
    "run_command": {"command": "true"},
    "write_file": {"path": "/etc/motd", "content": "hello\n"},
}


def _actions(policy_path):
    """The policy's actions as polkit 127 reads them (the gates' reader): id,
    the allow_active default, annotations, message (the untranslated
    <message>). A file PolicyKit would not read as written (it stops reading
    it, or keeps the last of a repeated element) is refused here, named."""
    reading = policy_reader.read_policy(policy_path)
    departures = policy_reader.reading_violations(reading)
    if departures:
        raise AssertionError(f"PolicyKit does not read {policy_path} as written: {departures}")
    return [{
        "id": action.id,
        "allow_active": action.defaults["allow_active"],
        "defaults": dict(action.defaults),
        "annotations": dict(action.annotations),
        "message": action.message,
    } for action in reading.actions.values()]


class _PolkitAgent:
    """polkit 127's decision for each pkexec of the runner, and an
    authentication agent that answers every challenge and counts it."""

    def __init__(self, policy_path):
        self.actions = _actions(policy_path)
        self.kept = []      # (action id, subject, expires at)
        self.prompts = []   # (action id, message)

    def action_for(self, exec_argv):
        """pkexec.c find_action_for_path, over this policy."""
        path = exec_argv[0]
        argv1 = exec_argv[1] if len(exec_argv) > 1 else None
        for action in self.actions:
            if action["annotations"].get(EXEC_PATH) != path:
                continue
            wanted = action["annotations"].get(EXEC_ARGV1)
            if wanted is not None and wanted != argv1:
                continue
            return action
        return FALLBACK

    def pkexec(self, exec_argv, *, subject, now):
        action = self.action_for(exec_argv)
        implicit = action["allow_active"]
        if implicit == "yes":
            return "authorized"
        if implicit == "no":
            return "not authorized"
        self.kept = [k for k in self.kept if k[2] > now]
        if any(k[0] == action["id"] and k[1] == subject for k in self.kept):
            return "authorized by a kept authorization"
        self.prompts.append((action["id"], action["message"]))
        if implicit in ("auth_admin_keep", "auth_self_keep"):
            self.kept.append((action["id"], subject, now + KEEP_SECONDS))
        return "authorized after a prompt"


def _dispatch_argv(tool_name):
    """The argv the real dispatch path builds for one call of `tool_name`."""
    captured = []

    def _record(argv, **kwargs):
        captured.append(list(argv))

        class _Completed:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return _Completed()

    with tempfile.TemporaryDirectory(prefix="each-call-auth-") as runtime:
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}, clear=False), \
                mock.patch.object(tr.subprocess, "run", side_effect=_record), \
                mock.patch.object(tool_process, "unit_search_path",
                                  return_value=[os.path.join(runtime, "no-unit-path")]), \
                escalation_allowed_for_a_stubbed_dispatch():
            ToolRegistry._dispatch_via_pkexec(
                ToolCall(
                    name=tool_name,
                    arguments=ARGUMENTS[tool_name],
                    call_id=f"each-call-{tool_name}",
                    source_of_request=list(Provenance)[0],
                ),
                tool_name,
                ARGUMENTS[tool_name],
                "a-token-the-recorder-never-reads",
            )
    dispatches = [a for a in captured if "pkexec" in a]
    assert len(dispatches) == 1, f"expected one dispatch, recorded {captured}"
    return dispatches[0]


def _pkexec_argv(dispatch_argv):
    """What pkexec runs, from the dispatcher's systemd-run command line.

    pkexec must be the unit's main process (the first word after --): that is
    what makes the user manager its parent."""
    assert dispatch_argv[0].endswith("systemd-run"), dispatch_argv
    assert "--user" in dispatch_argv, dispatch_argv
    command = dispatch_argv[dispatch_argv.index("--") + 1:]
    assert command[0] == "pkexec", dispatch_argv
    return command[1:]


class Leg1TheShippedPolicyAndTheRealArgvTests(unittest.TestCase):
    """Two calls through the real dispatch argv against the shipped policy."""

    def _agent(self):
        return _PolkitAgent(POLICY)

    def test_two_calls_of_one_tool_inside_the_window_take_two_prompts(self):
        agent = self._agent()
        exec_argv = _pkexec_argv(_dispatch_argv("manage_packages"))
        agent.pkexec(exec_argv, subject=USER_MANAGER, now=0)
        agent.pkexec(exec_argv, subject=USER_MANAGER, now=60)
        self.assertEqual(
            len(agent.prompts), 2,
            f"two privileged calls 60 s apart took {len(agent.prompts)} "
            f"administrator prompt(s): the second ran on the first's "
            f"authentication ({agent.prompts})",
        )

    def test_a_different_tool_inside_the_window_takes_its_own_prompt(self):
        agent = self._agent()
        agent.pkexec(_pkexec_argv(_dispatch_argv("manage_services")),
                     subject=USER_MANAGER, now=0)
        agent.pkexec(_pkexec_argv(_dispatch_argv("write_file")),
                     subject=USER_MANAGER, now=30)
        self.assertEqual(
            len(agent.prompts), 2,
            f"a write_file call 30 s after a manage_services call took "
            f"{len(agent.prompts)} prompt(s) in all ({agent.prompts})",
        )

    def test_every_tool_reaches_an_action_of_its_own_that_names_its_kind(self):
        agent = self._agent()
        messages = {}
        for tool_name in sorted(_PRIVILEGED_TOOLS):
            with self.subTest(tool=tool_name):
                action = agent.action_for(_pkexec_argv(_dispatch_argv(tool_name)))
                self.assertNotEqual(
                    action["id"], FALLBACK["id"],
                    f"{tool_name} matched no InterGen action, so the prompt "
                    f"would be pkexec's generic one",
                )
                self.assertIn(
                    CLASS_WORDS[tool_name], action["message"] or "",
                    f"the prompt for {tool_name} does not name its kind of "
                    f"action: {action['message']!r}",
                )
                messages[tool_name] = action["message"]
        self.assertEqual(
            len(set(messages.values())), len(_PRIVILEGED_TOOLS),
            f"tools share one prompt, so it cannot say which kind of action "
            f"it is for: {messages}",
        )


class Leg2ThePolicyItselfTests(unittest.TestCase):
    """Every action the policy binds to the runner, read element by element."""

    def _runner_actions(self):
        return [a for a in _actions(POLICY)
                if a["annotations"].get(EXEC_PATH) == tr._PKEXEC_RUNNER_PATH]

    def test_no_action_bound_to_the_runner_keeps_an_authentication(self):
        bound = self._runner_actions()
        for action in bound:
            for key in ("allow_any", "allow_inactive", "allow_active"):
                with self.subTest(action=action["id"], default=key):
                    self.assertEqual(
                        action["defaults"][key], "auth_admin",
                        f"{action['id']} {key} is not auth_admin",
                    )
        self.assertGreater(len(bound), 0, "no action is bound to the runner")

    def test_each_runner_action_names_one_privileged_tool_and_each_tool_has_one(self):
        names = [a["annotations"].get(EXEC_ARGV1) for a in self._runner_actions()]
        self.assertNotIn(
            None, names,
            "an action bound to the runner carries no exec.argv1, so it "
            "matches any first argument",
        )
        self.assertEqual(sorted(names), sorted(_PRIVILEGED_TOOLS))


class Leg3TheAgentIsAnInstrumentTests(unittest.TestCase):
    """Controls on the agent: it must see a keep window when one is there."""

    def _keep_policy(self, directory):
        path = Path(directory) / "keep.policy"
        path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<policyconfig>\n'
            '  <action id="org.example.keep">\n'
            '    <message>kept</message>\n'
            '    <defaults><allow_any>auth_admin_keep</allow_any>'
            '<allow_inactive>auth_admin_keep</allow_inactive>'
            '<allow_active>auth_admin_keep</allow_active></defaults>\n'
            f'    <annotate key="{EXEC_PATH}">{tr._PKEXEC_RUNNER_PATH}</annotate>\n'
            '  </action>\n</policyconfig>\n'
        )
        return path

    def test_positive_control_a_keep_policy_takes_one_prompt_for_two_calls(self):
        with tempfile.TemporaryDirectory(prefix="each-call-auth-") as d:
            agent = _PolkitAgent(self._keep_policy(d))
            exec_argv = _pkexec_argv(_dispatch_argv("manage_packages"))
            agent.pkexec(exec_argv, subject=USER_MANAGER, now=0)
            agent.pkexec(exec_argv, subject=USER_MANAGER, now=60)
            self.assertEqual(len(agent.prompts), 1)

    def test_control_the_kept_authorization_ends_with_its_window(self):
        with tempfile.TemporaryDirectory(prefix="each-call-auth-") as d:
            agent = _PolkitAgent(self._keep_policy(d))
            exec_argv = _pkexec_argv(_dispatch_argv("manage_packages"))
            agent.pkexec(exec_argv, subject=USER_MANAGER, now=0)
            agent.pkexec(exec_argv, subject=USER_MANAGER, now=KEEP_SECONDS + 1)
            self.assertEqual(len(agent.prompts), 2)

    def test_control_a_kept_authorization_is_for_its_own_subject(self):
        with tempfile.TemporaryDirectory(prefix="each-call-auth-") as d:
            agent = _PolkitAgent(self._keep_policy(d))
            exec_argv = _pkexec_argv(_dispatch_argv("manage_packages"))
            agent.pkexec(exec_argv, subject=USER_MANAGER, now=0)
            agent.pkexec(exec_argv, subject="some other process", now=60)
            self.assertEqual(len(agent.prompts), 2)

    def test_control_the_model_reads_a_repeated_default_as_polkit_does(self):
        """A repeated allow_active whose second value is a keep is PolicyKit's
        keep: the model refuses the file, naming the element, and never reads it
        as its first value (which would count a prompt PolicyKit never asks)."""
        marker = "<allow_active>auth_admin</allow_active>"
        text = POLICY.read_text().replace(
            marker, marker + "<allow_active>auth_admin_keep</allow_active>", 1)
        with tempfile.TemporaryDirectory(prefix="each-call-auth-") as d:
            path = Path(d) / "repeated.policy"
            path.write_text(text)
            with self.assertRaises(AssertionError) as caught:
                _actions(path)
        self.assertIn("<allow_active> appears 2 times", str(caught.exception))


def _user_manager_reachable():
    if shutil.which("systemd-run") is None:
        return False, "systemd-run is not installed"
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime or not os.path.exists(os.path.join(runtime, "systemd", "private")):
        return False, "no systemd user manager is addressable for this account"
    return True, ""


_REACHABLE, _UNREACHABLE_REASON = _user_manager_reachable()

#: Prints the parent's pid, its start time (field 22 of /proc/<pid>/stat) and its
#: command line: the facts polkit identifies a process subject by.
_PARENT_PROBE = (
    "import os\n"
    "p = os.getppid()\n"
    "stat = open(f'/proc/{p}/stat').read()\n"
    "start = stat[stat.rindex(')') + 2:].split()[19]\n"
    "cmd = open(f'/proc/{p}/cmdline', 'rb').read().replace(b'\\0', b' ').decode().strip()\n"
    "print(p, start, cmd)\n"
)


@unittest.skipUnless(
    _REACHABLE, f"the shared-parent leg was NOT measured: {_UNREACHABLE_REASON}",
)
class Leg4TheParentIsObservedTests(unittest.TestCase):
    """Two real transient units, started the way the dispatcher starts the
    runner, report the same parent: the user manager. The probe is a plain
    interpreter; nothing setuid runs and nothing can prompt."""

    def _parent(self):
        completed = subprocess.run(
            ["systemd-run", "--user", "--quiet", "--collect", "--wait", "--pipe",
             "--", "/usr/bin/python3", "-I", "-c", _PARENT_PROBE],
            capture_output=True, text=True, check=False, timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        pid, start, command = completed.stdout.strip().split(" ", 2)
        return int(pid), start, command

    def test_two_runners_have_one_parent_and_it_is_the_user_manager(self):
        first = self._parent()
        second = self._parent()
        self.assertEqual(
            first[:2], second[:2],
            f"two transient units had different parents: {first} and {second}",
        )
        self.assertIn("systemd", first[2])
        self.assertIn("--user", first[2])


class Leg5TheRunnerRefusesWhatThePolicyDoesNotBindTests(unittest.TestCase):
    """The real runner script, run without pkexec: every case below exits
    before the dispatcher, so nothing runs as root and nothing is read."""

    def _run(self, *args):
        return subprocess.run(
            ["/bin/sh", str(RUNNER_SCRIPT), *args],
            capture_output=True, text=True, check=False, timeout=30,
            env={"PATH": "/usr/bin:/bin", "PKEXEC_UID": str(os.getuid())},
        )

    def test_a_tool_outside_the_four_is_refused(self):
        completed = self._run("rm_everything", "0" * 32)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("not one of the privileged tools", completed.stderr)

    def test_the_request_identifier_alone_is_the_old_contract_and_is_refused(self):
        completed = self._run("0" * 32)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("<tool> <request_id>", completed.stderr)

    def test_a_request_identifier_that_is_not_hex_is_still_refused(self):
        completed = self._run("manage_packages", "../../etc/shadow")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("not hex", completed.stderr)


if __name__ == "__main__":
    unittest.main()
