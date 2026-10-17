# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The web battery's local form runs ON the installed machine, with no ssh.

The shipped sshd hardening refuses TCP forwarding, so the battery's tunnel form
(``--target``) cannot reach an installed R001.3 machine from outside. The local
form (``--local``) drives the machine's own panel at 127.0.0.1:8089 and takes
every reading on the machine itself. These cases pin what that form must keep:
the refusals the tunnel form has (a checkout first of all), the refusal of a
machine whose pkm names no installed assistant, the tunnel form's command text
for every reading, no ssh and no tunnel anywhere, and a record whose members
and summary keys are the tunnel form's. No daemon, no socket and no model run
here: every reading is a recorded double.
"""

from __future__ import annotations

import io
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from intergen.tests import web_battery as wb
from intergen.tests import ws_harness

IDENTITY = (
    "box\n6.18.51-igos-6\nNAME=\"InterGenOS\"\nIMAGE_VERSION=\"r001.3-02\"\n"
    "--- pkm info intergen ---\n  ==================\n  intergen 0.1.0-337\n"
    "  ==================\n")
NO_ASSISTANT = (
    "box\n6.18.51-igos-6\nNAME=\"InterGenOS\"\n--- pkm info intergen ---\n"
    "error: package 'intergen' is not installed\n")
FREE_H = (
    "               total        used        free      shared  buff/cache   available\n"
    "Mem:            23Gi       4.2Gi        11Gi       888Mi       9.1Gi        18Gi\n"
    "Swap:          4.0Gi          0B       4.0Gi\n")


class _Machine:
    """A recorded double for every reading the battery takes. It answers the
    way an installed machine does and keeps every argv it was asked to run, so
    a case can say which readings went over ssh and which ran here."""

    def __init__(self, identity=IDENTITY, identity_rc=0):
        self.identity, self.identity_rc = identity, identity_rc
        self.calls: list[tuple[list[str], dict]] = []

    def run(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append((argv, kwargs))
        cmd = argv[-1]
        if cmd.startswith("hostname;"):
            out, rc = self.identity, self.identity_rc
        elif cmd == f"cat ~/{wb.TOKEN_PATH}":
            out, rc = "token-for-the-test\n", 0
        elif cmd == "free -h":
            out, rc = FREE_H, 0
        elif cmd.startswith("grep -F "):
            out, rc = "", 1
        else:
            raise AssertionError(f"a reading the battery does not take: {argv!r}")
        return subprocess.CompletedProcess(argv, rc, stdout=out, stderr="")

    def programs(self) -> set[str]:
        return {argv[0] for argv, _kw in self.calls}


def _no_popen(*args, **kwargs):
    raise AssertionError(f"the local form started a process: {args!r}")


class _Tunnel:
    """The tunnel form's ssh -N -L, alive until terminated."""

    def __init__(self, argv, **kwargs):
        self.argv, self.stderr = list(argv), None

    def poll(self):
        return None

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def _turn(key: str, text: str):
    turn_id = f"turn-{key}"
    frames = [{"type": "turn_ack", "turn_id": turn_id},
              {"type": "response", "turn_id": turn_id, "source": "identity",
               "text": text}]
    return SimpleNamespace(messages=frames, text=text, terminal=True,
                           closed_by="client", elapsed_s=0.5, terminal_at=0.4,
                           late_frames=0,
                           events=[(0.0, m["type"]) for m in frames])


def _run_main(argv: list[str]) -> tuple[int | None, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code: int | None
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = wb.main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class LocalFormRefusals(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="web-battery-local-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.record = self.tmp / "record"
        self.machine = _Machine()
        for name, double in (("run", self.machine.run), ("Popen", _no_popen)):
            patcher = mock.patch.object(wb.subprocess, name, double)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_local_form_refuses_to_run_from_a_plain_checkout(self):
        # This file lives in a checkout, so the battery's own file does too:
        # the local form refuses it before anything is read or written, as
        # the tunnel form does.
        self.assertIsNotNone(wb._checkout_above(Path(wb.__file__).parent))
        code, _out, err = _run_main(["--local", "--output", str(self.record),
                                     "--tree-sha", "0123456789abcdef"])
        self.assertEqual(code, wb.EXIT_REFUSED)
        self.assertIn("source checkout", err)
        self.assertFalse(self.record.exists())
        self.assertEqual(self.machine.calls, [])

    def test_target_and_local_are_one_or_the_other(self):
        code, _out, err = _run_main(["--local", "--target", "u@h", "--output",
                                     str(self.record), "--tree-sha", "abc1234"])
        self.assertEqual(code, wb.EXIT_REFUSED)
        self.assertIn("not allowed with", err)
        code, _out, err = _run_main(["--output", str(self.record),
                                     "--tree-sha", "abc1234"])
        self.assertEqual(code, wb.EXIT_REFUSED)
        self.assertIn("one of the arguments --target --local is required", err)
        self.assertFalse(self.record.exists())
        self.assertEqual(self.machine.calls, [])

    def test_the_local_form_refuses_the_tunnels_flags_rather_than_ignore_them(self):
        for extra, named in ((["--ssh-port", "2222"], "--ssh-port"),
                             (["--local-port", "0"], "--local-port"),
                             (["--ssh-port", "22", "--local-port", "9000"],
                              "--ssh-port and --local-port")):
            with self.subTest(extra=extra):
                code, _out, err = _run_main(
                    ["--local", *extra, "--output", str(self.record),
                     "--tree-sha", "abc1234"])
                self.assertEqual(code, wb.EXIT_REFUSED)
                self.assertIn(f"{named} would be ignored", err)
        self.assertFalse(self.record.exists())
        self.assertEqual(self.machine.calls, [])

    def test_the_local_form_refuses_a_machine_whose_pkm_names_no_installed_assistant(self):
        # A pinned copy (no checkout above it) on a machine where the
        # assistant is not an installed package: the identity names no
        # release, so nothing is recorded.
        self.machine.identity = NO_ASSISTANT
        with mock.patch.object(wb, "_checkout_above", lambda start: None):
            code, _out, err = _run_main(["--local", "--output",
                                         str(self.record), "--tree-sha",
                                         "abc1234"])
        self.assertEqual(code, wb.EXIT_REFUSED)
        self.assertIn("on this machine did not name an installed release", err)
        self.assertFalse(self.record.exists())
        self.assertEqual(self.machine.programs(), {wb.LOCAL_SHELL})

    def test_the_local_form_refuses_an_identity_it_cannot_read(self):
        self.machine.identity_rc = 127
        with mock.patch.object(wb, "_checkout_above", lambda start: None):
            code, _out, err = _run_main(["--local", "--output",
                                         str(self.record), "--tree-sha",
                                         "abc1234"])
        self.assertEqual(code, wb.EXIT_REFUSED)
        self.assertIn("could not be read on this machine (rc=127)", err)
        self.assertFalse(self.record.exists())
        self.assertEqual(self.machine.programs(), {wb.LOCAL_SHELL})


class EveryReadingIsTheTunnelFormsCommand(unittest.TestCase):

    def test_a_local_reading_runs_here_with_the_passwd_home(self):
        machine = _Machine()
        with mock.patch.object(wb.subprocess, "run", machine.run), \
                mock.patch.dict(os.environ, {"HOME": "/nonexistent/home"}):
            wb._read(None, 22, "free -h")
        (argv, kwargs), = machine.calls
        self.assertEqual(argv, [wb.LOCAL_SHELL, "-c", "free -h"])
        self.assertEqual(kwargs["env"]["HOME"],
                         pwd.getpwuid(os.getuid()).pw_dir)

    def test_a_tunnel_reading_is_unchanged(self):
        machine = _Machine()
        with mock.patch.object(wb.subprocess, "run", machine.run):
            wb._read("u@h", 2222, "free -h")
        (argv, _kwargs), = machine.calls
        self.assertEqual(argv, ["ssh", "-o", "BatchMode=yes", "-o",
                                "ConnectTimeout=15", "-p", "2222", "u@h",
                                "free -h"])

    def test_identity_token_and_trace_send_the_same_command_text(self):
        readings = (
            ("identity", lambda t: wb._target_identity(t, 22)),
            ("token", lambda t: wb._read_token(t, 22)),
            ("trace", lambda t: wb._read_glass(t, 22, ["turn-1", "turn 2"])),
        )
        for name, take in readings:
            with self.subTest(reading=name):
                over_ssh, here = _Machine(), _Machine()
                with mock.patch.object(wb.subprocess, "run", over_ssh.run):
                    by_ssh = take("u@h")
                with mock.patch.object(wb.subprocess, "run", here.run):
                    by_local = take(None)
                (ssh_argv, _), = over_ssh.calls
                (local_argv, _), = here.calls
                self.assertEqual(ssh_argv[0], "ssh")
                self.assertEqual(local_argv[:2], [wb.LOCAL_SHELL, "-c"])
                self.assertEqual(local_argv[-1], ssh_argv[-1])
                self.assertEqual(by_local, by_ssh)


class TheLocalRecordIsTheTunnelRecordMemberForMember(unittest.TestCase):

    def _record(self, reach: list[str], popen) -> tuple[Path, _Machine, dict]:
        tmp = Path(tempfile.mkdtemp(prefix="web-battery-record-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        record = tmp / "record"
        machine = _Machine()
        seen: dict = {}

        async def drive(tree_root, local_port, token, deadline_s, frames_out,
                        target, ssh_port, truth_reads):
            # The real _drive's I/O, without a socket: the frames file, and
            # the truth probe taken through the battery's own reader right
            # after the question that carries it.
            seen.update(local_port=local_port, token=token, target=target)
            results = []
            with frames_out.open("w", encoding="utf-8") as fh:
                for q in wb.QUESTIONS:
                    r = _turn(q.key, "an answer")
                    for m in r.messages:
                        fh.write(json.dumps({"question": q.key, "frame": m})
                                 + "\n")
                    probe = wb.TRUTH_PROBES.get(q.truth or "")
                    if probe:
                        gt = wb._read(target, ssh_port, probe, timeout=60)
                        truth_reads[q.key] = (probe, gt.returncode, gt.stdout)
                    results.append((q, r))
            return results

        # main() puts the tree it runs from on sys.path; keep that here.
        with mock.patch.object(sys, "path", list(sys.path)), \
                mock.patch.object(wb.subprocess, "run", machine.run), \
                mock.patch.object(wb.subprocess, "Popen", popen), \
                mock.patch.object(wb, "_checkout_above", lambda start: None), \
                mock.patch.object(wb, "_drive", drive), \
                mock.patch.object(ws_harness, "daemon_reachable",
                                  lambda host, port: True):
            code, _out, _err = _run_main([*reach, "--output", str(record),
                                          "--tree-sha", "abc1234"])
        self.assertIn(code, (wb.EXIT_OK, wb.EXIT_FAIL))
        return record, machine, seen

    def test_members_summary_keys_and_readings_match_the_tunnel_form(self):
        tunnels: list[_Tunnel] = []

        def tunnel_popen(argv, **kwargs):
            tunnels.append(_Tunnel(argv, **kwargs))
            return tunnels[-1]

        by_tunnel, over_ssh, tunnel_seen = self._record(
            ["--target", "u@h", "--local-port", "40000"], tunnel_popen)
        by_local, here, local_seen = self._record(["--local"], _no_popen)

        def members(record: Path) -> list[str]:
            return sorted(p.name for p in record.iterdir())

        self.assertEqual(members(by_local), members(by_tunnel))
        self.assertEqual(members(by_local),
                         ["SHA256SUMS", "frames.jsonl", "glass-excerpt.jsonl",
                          "ground-truth.txt", "summary.json",
                          "target-identity.txt", "verdicts.tsv"])
        local_summary = json.loads((by_local / "summary.json").read_text())
        tunnel_summary = json.loads((by_tunnel / "summary.json").read_text())
        self.assertEqual(sorted(local_summary), sorted(tunnel_summary))
        self.assertEqual(
            [v["verdict"] for v in local_summary["per_question"]],
            [v["verdict"] for v in tunnel_summary["per_question"]])
        for name in ("target-identity.txt", "verdicts.tsv"):
            self.assertEqual((by_local / name).read_text(),
                             (by_tunnel / name).read_text(), name)
        self.assertEqual(
            (by_local / "ground-truth.txt").read_text(),
            (by_tunnel / "ground-truth.txt").read_text().replace(
                "ssh u@h free -h", "local free -h"))

        # The tunnel form: one ssh -N -L, every reading over ssh.
        self.assertEqual(len(tunnels), 1)
        self.assertEqual(tunnels[0].argv[0], "ssh")
        self.assertIn("-N", tunnels[0].argv)
        self.assertEqual(over_ssh.programs(), {"ssh"})
        self.assertEqual(tunnel_summary["target_ssh"], "u@h")
        self.assertEqual(tunnel_summary["tunnel"],
                         "127.0.0.1:40000 -> 127.0.0.1:8089")
        self.assertEqual(tunnel_seen["local_port"], 40000)
        # The local form: no process started, every reading taken here, the
        # panel driven at its own port, and the record says so.
        self.assertEqual(here.programs(), {wb.LOCAL_SHELL})
        self.assertEqual(len(here.calls), len(over_ssh.calls))
        self.assertEqual([argv[-1] for argv, _ in here.calls],
                         [argv[-1] for argv, _ in over_ssh.calls])
        self.assertEqual(local_seen["local_port"], wb.TARGET_PORT)
        self.assertEqual(local_seen["token"], tunnel_seen["token"])
        self.assertIsNone(local_summary["target_ssh"])
        self.assertTrue(local_summary["tunnel"].startswith("none:"))

    def test_an_unanswering_panel_is_recorded_as_such_with_no_tunnel_member(self):
        tmp = Path(tempfile.mkdtemp(prefix="web-battery-record-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        record = tmp / "record"
        machine = _Machine()
        with mock.patch.object(sys, "path", list(sys.path)), \
                mock.patch.object(wb.subprocess, "run", machine.run), \
                mock.patch.object(wb.subprocess, "Popen", _no_popen), \
                mock.patch.object(wb, "_checkout_above", lambda start: None), \
                mock.patch.object(wb.time, "sleep", lambda s: None), \
                mock.patch.object(ws_harness, "daemon_reachable",
                                  lambda host, port: False):
            code, _out, err = _run_main(["--local", "--output", str(record),
                                         "--tree-sha", "abc1234"])
        self.assertEqual(code, wb.EXIT_FAIL)
        self.assertIn("did not answer on this machine (127.0.0.1:8089)", err)
        self.assertEqual(sorted(p.name for p in record.iterdir()),
                         ["SHA256SUMS", "summary.json", "target-identity.txt"])
        self.assertEqual(
            json.loads((record / "summary.json").read_text()),
            {"refused_after_record_opened":
             "panel unreachable on this machine at 127.0.0.1:8089"})
        self.assertEqual(machine.programs(), {wb.LOCAL_SHELL})


if __name__ == "__main__":
    unittest.main()
