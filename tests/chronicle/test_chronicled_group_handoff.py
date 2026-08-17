#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The engine's group handoff must not call chown when nothing needs changing.

WHAT WENT WRONG. chronicled.service denies the privileged syscall set
(SystemCallFilter=~@privileged). @chown is a member of that set, so the
kernel's seccomp filter answers a chown() from the daemon by killing the whole
process with SIGSYS. The daemon called chown unconditionally on its runtime
directory and on its socket while handing both to the `chronicle` group, so it
was killed at every start, restarted by Restart=on-failure, and killed again.
Measured on 2026-08-06: 165 restarts and 166 SIGSYS coredumps on one machine,
every one of them frame #0 chown, frame #1 os_chown_impl.

The Python-level error handling could not help. SIGSYS from a seccomp filter in
kill mode is not an errno delivered to the caller, so `except PermissionError`
and `except OSError` never run — the process is gone before the call returns.
A test that mocks chown to raise cannot see this defect; only a test of whether
chown is CALLED can.

WHAT FIXES IT, in two halves that have to hold together:

  1. The unit runs with Group=chronicle, so the directory systemd creates and
     the socket bind() creates are already owned by the target group. There is
     nothing left for a chown to do.
  2. The code asks before it acts: it reads the current group off the inode and
     calls chown only when the group is actually wrong.

Half 2 alone would leave the daemon killed whenever the group really was wrong.
Half 1 alone would leave a chown in the path for anything that reached it. Both
halves together mean the production path never issues the syscall, so these
tests pin BOTH, and they also pin that the chown is still there and still runs
when the group is genuinely wrong — the fix is "ask first", not "delete it".

Everything here runs unprivileged and headless in a temporary directory, using
the running user's own gid so the real ownership path executes.
"""

import os
import re
import socket
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from chronicle import api as _api

_REPO_ROOT = Path(__file__).resolve().parents[2]
_UNIT = (_REPO_ROOT / "assets" / "intergenos-backup" / "systemd"
         / "chronicled.service")


class _ChownWatcher:
    """Records every chown the code under test issues, and passes it through.

    Not a mock that swallows the call: the real chown still runs, so the
    resulting inode is real and the assertions about ownership stay honest.
    Counting the calls is the only way to observe the syscall that the seccomp
    filter reacts to, because the reaction is a signal and not a return value.
    """

    def __init__(self):
        self.calls = []
        self._real = os.chown

    def __enter__(self):
        watcher = self

        def _counting_chown(path, uid, gid, **kw):
            watcher.calls.append((str(path), uid, gid))
            return watcher._real(path, uid, gid, **kw)

        _api.os.chown = _counting_chown
        return self

    def __exit__(self, *exc):
        _api.os.chown = self._real
        return False


class RuntimeDirectoryHandoffTest(unittest.TestCase):
    """prepare_runtime_dir: chown only when the group is wrong."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chronicle-grouphandoff-")
        self.gid = os.getgid()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_chown_when_the_directory_already_has_the_group(self):
        """The production shape under Group=chronicle.

        systemd creates RuntimeDirectory=chronicle owned by the unit's User and
        Group, so the directory arrives already correct. Before the fix this
        issued a chown anyway and the daemon was killed by SIGSYS right here.
        """
        d = Path(self.tmp) / "already-right"
        d.mkdir()
        os.chown(d, -1, self.gid)
        self.assertEqual(os.stat(d).st_gid, self.gid, "test setup is wrong")
        with _ChownWatcher() as watcher:
            _api.prepare_runtime_dir(d, self.gid)
        self.assertEqual(
            watcher.calls, [],
            f"chown was called on a directory that already had the right "
            f"group: {watcher.calls}. Under chronicled.service's seccomp "
            f"filter that call is SIGSYS and the daemon dies.")
        # And the mode is still applied — skipping the chown must not skip the
        # narrowing that makes the directory group-traversable-only.
        self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o750)

    def test_the_chown_still_happens_when_the_group_is_wrong(self):
        """The fix is "ask first", not "delete the call".

        Deleting the chown outright would pass the test above and silently stop
        handing the directory to the group on any system where it arrived
        wrong. This is the mutation guard against that.
        """
        d = Path(self.tmp) / "needs-fixing"
        d.mkdir()
        other = self._a_different_gid()
        if other is None:
            self.skipTest("this account belongs to only one group, so a "
                          "wrong-group directory cannot be constructed here")
        os.chown(d, -1, other)
        self.assertNotEqual(os.stat(d).st_gid, self.gid, "test setup is wrong")
        with _ChownWatcher() as watcher:
            _api.prepare_runtime_dir(d, self.gid)
        self.assertEqual(len(watcher.calls), 1,
                         f"expected exactly one chown, got {watcher.calls}")
        self.assertEqual(os.stat(d).st_gid, self.gid)
        self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o750)

    def test_a_missing_group_still_stays_owner_only_and_never_chowns(self):
        d = Path(self.tmp) / "no-group"
        d.mkdir()
        with _ChownWatcher() as watcher:
            _api.prepare_runtime_dir(d, None)
        self.assertEqual(watcher.calls, [])
        self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)

    def _a_different_gid(self):
        """Some gid this process may hand a file to that is not its own.

        Returns None when the account has no second group, in which case the
        wrong-group case cannot be built unprivileged and the test says so
        rather than pretending to have covered it.
        """
        for gid in os.getgroups():
            if gid != self.gid:
                return gid
        return None


class SocketHandoffTest(unittest.TestCase):
    """secure_socket: chown only when the group is wrong."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chronicle-sockhandoff-")
        self.gid = os.getgid()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _bound_socket(self, name="s.sock"):
        path = os.path.join(self.tmp, name)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            srv.bind(path)
        finally:
            os.umask(old)
        self.addCleanup(srv.close)
        return path

    def test_no_chown_when_the_socket_already_has_the_group(self):
        """The production shape under Group=chronicle.

        bind() creates the socket owned by the process's effective group, which
        under Group=chronicle IS the target group. Before the fix this issued a
        chown anyway; that is the second of the two calls that killed the
        daemon.
        """
        path = self._bound_socket()
        self.assertEqual(os.stat(path).st_gid, self.gid, "test setup is wrong")
        with _ChownWatcher() as watcher:
            result = _api.secure_socket(path, self.gid)
        self.assertEqual(
            watcher.calls, [],
            f"chown was called on a socket that already had the right group: "
            f"{watcher.calls}. That call is SIGSYS under the shipped unit.")
        # The verified-true return and the final mode both still hold: the
        # socket is group-reachable and nothing outside the group sees a bit.
        self.assertTrue(result)
        st = os.stat(path)
        self.assertEqual(st.st_gid, self.gid)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o660)
        self.assertEqual(stat.S_IMODE(st.st_mode) & 0o007, 0)

    def test_the_chown_still_happens_when_the_socket_group_is_wrong(self):
        path = self._bound_socket("wrong.sock")
        other = None
        for gid in os.getgroups():
            if gid != self.gid:
                other = gid
                break
        if other is None:
            self.skipTest("this account belongs to only one group, so a "
                          "wrong-group socket cannot be constructed here")
        os.chown(path, -1, other)
        with _ChownWatcher() as watcher:
            result = _api.secure_socket(path, self.gid)
        self.assertEqual(len(watcher.calls), 1,
                         f"expected exactly one chown, got {watcher.calls}")
        self.assertTrue(result)
        self.assertEqual(os.stat(path).st_gid, self.gid)

    def test_a_missing_group_still_stays_owner_only_and_never_chowns(self):
        path = self._bound_socket("nogroup.sock")
        with _ChownWatcher() as watcher:
            result = _api.secure_socket(path, None)
        self.assertEqual(watcher.calls, [])
        self.assertFalse(result)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


class UnitGroupTest(unittest.TestCase):
    """The other half of the fix: the shipped unit declares the group.

    Without Group=chronicle the directory and the socket arrive owned by root's
    primary group, the conditional finds the group wrong, and the chown fires
    after all — straight back into SIGSYS. The code half and the unit half are
    one fix and neither is optional.
    """

    def setUp(self):
        self.text = _UNIT.read_text()
        # Comments are stripped before any assertion. The header explains the
        # hardening at length and mentions the group by name; a test that a
        # comment can satisfy proves nothing.
        self.code = "\n".join(ln for ln in self.text.splitlines()
                              if not ln.strip().startswith("#"))

    def test_the_unit_runs_under_the_engine_group(self):
        self.assertIn(f"Group={_api.ENGINE_SOCKET_GROUP}", self.code,
                      "chronicled.service does not declare the engine group, "
                      "so the runtime directory and socket arrive owned by "
                      "root's primary group and the chown fires")

    def test_the_group_directive_is_in_the_service_section(self):
        """Group= outside [Service] is silently inert.

        systemd does not reject an unknown key in [Unit]; it logs and carries
        on, so a misplaced directive would leave the daemon dying exactly as
        before while the file appeared to carry the fix.
        """
        sections = {}
        current = None
        for line in self.code.splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                current = line
                sections.setdefault(current, [])
            elif current and line:
                sections[current].append(line)
        self.assertIn("[Service]", sections)
        self.assertTrue(
            any(ln.startswith("Group=") for ln in sections["[Service]"]),
            "Group= is not in the [Service] section")

    def test_the_privileged_denial_is_still_in_place(self):
        """The fix must not have been a seccomp widening.

        The decided direction was explicit: hand the group over correctly, do
        not relax the filter. If these lines ever disappear, the crash loop is
        "fixed" by giving the backup daemon the privileged syscall set back,
        which is a different and much worse change.
        """
        self.assertIn("SystemCallFilter=~@privileged", self.code)
        self.assertIn("SystemCallFilter=@system-service", self.code)
        self.assertNotIn("@chown", self.code,
                         "the filter was widened to re-allow chown")

    def test_the_runtime_directory_mode_is_unchanged(self):
        self.assertIn("RuntimeDirectoryMode=0750", self.code)

    def test_the_capability_set_was_not_widened(self):
        """CAP_CHOWN would be the other way to paper over this.

        It would not even work — seccomp is checked before capabilities — but
        it would look like a fix, so it is pinned out.
        """
        caps = [ln for ln in self.code.splitlines()
                if ln.startswith(("CapabilityBoundingSet=",
                                  "AmbientCapabilities="))]
        self.assertTrue(caps, "the unit lost its capability bounding set")
        for line in caps:
            self.assertNotIn("CAP_CHOWN", line)
            self.assertNotIn("CAP_FOWNER", line)


class SysusersParityTest(unittest.TestCase):
    """The unit names a group; something has to create it."""

    def test_the_group_the_unit_uses_is_the_group_sysusers_declares(self):
        frag = (_REPO_ROOT / "assets" / "intergenos-backup" / "sysusers"
                / "chronicle.conf")
        declared = [ln.split()[1] for ln in frag.read_text().splitlines()
                    if ln.strip().startswith("g ")]
        code = "\n".join(ln for ln in _UNIT.read_text().splitlines()
                         if not ln.strip().startswith("#"))
        match = re.search(r"^Group=(\S+)$", code, re.MULTILINE)
        self.assertIsNotNone(match, "the unit declares no Group=")
        self.assertIn(match.group(1), declared,
                      "the unit runs as a group nothing creates, so the "
                      "service fails to start at all")

    def test_the_unit_is_ordered_after_the_group_is_created(self):
        """Group= is resolved at start; the group must exist by then.

        With Group=chronicle the failure mode of a missing group changes from
        "socket stays owner-only" to "the unit does not start", so the existing
        After= ordering stops being a statement of a dependency and becomes
        load-bearing.
        """
        code = "\n".join(ln for ln in _UNIT.read_text().splitlines()
                         if not ln.strip().startswith("#"))
        self.assertIn("After=systemd-sysusers.service", code)


if __name__ == "__main__":
    sys.exit(unittest.main())
