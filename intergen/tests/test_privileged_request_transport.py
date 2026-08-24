# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The privileged request must travel in an owner-only file, never on argv.

Background. A privileged dispatch has to carry three things across the
privilege boundary: which tool to run, the arguments to run it with, and the
approval token that proves a person authorized this exact action. `pkexec`
scrubs the environment as it crosses that boundary, so the released code put
all three on the command line — and a command line is world-readable through
/proc for as long as the process lives. On this image /proc carries no
`hidepid`, so any local account could read the approval token and the tool
arguments out of the process listing while a dispatch was in flight.

The correction is to put the request in a file that only its owner can read and
to pass only that file's PATH on the command line. The path is not a secret;
the contents are, and they are protected by file permissions rather than by
hoping nobody looks.

That transport is the one part of the design that can be got wrong quietly, so
these tests are the hardest ones in the change. They pin:

  1. RESTRICTIVE AT CREATION. The file is created 0600 from the outset, with
     O_EXCL, inside a 0700 directory. A file created wide and narrowed
     afterwards has a window; there must be no window.
  2. WHAT WENT IN COMES OUT. Tool name, arguments and token round-trip exactly.
  3. READING REMOVES IT. The request does not outlive the read that consumes it.
  4. REMOVED ON EVERY FAILURE PATH. A malformed file, a refused file, a
     validation error — none of them may leave a request behind on disk.
  5. TWO OVERLAPPING DISPATCHES DO NOT COLLIDE. Two requests in flight at once
     get distinct paths, read back their own contents, and removing one leaves
     the other untouched.
  6. THE ROOT SIDE REFUSES WHAT IT CANNOT TRUST. Wrong owner, wrong mode, a
     symlink, a hard-linked file, a directory, an unknown format version — each
     is a refusal, not a best-effort read.

Nothing here runs pkexec, the runner, systemd-run, or any tool. Every path used
is inside a temporary directory created by the test.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from unittest import mock

from intergen import privileged_request as pr


TOOL = "manage_packages"
ARGS = {"action": "install", "package": "cowsay"}
TOKEN = "v1.deadbeef.cafef00d.signature-material"


class _RuntimeDirTestCase(unittest.TestCase):
    """Base: every test runs against a throwaway XDG_RUNTIME_DIR.

    The live runtime directory is never touched — a test that wrote a real
    request file would be leaving privileged-dispatch state on the operator's
    running system.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="privreq-")
        self.addCleanup(self._tmp.cleanup)
        self.runtime_dir = self._tmp.name
        patcher = mock.patch.dict(
            os.environ, {"XDG_RUNTIME_DIR": self.runtime_dir}, clear=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, tool=TOOL, arguments=None, token=TOKEN):
        return pr.write_request(
            tool, ARGS if arguments is None else arguments, token,
        )


class CreationIsRestrictiveTests(_RuntimeDirTestCase):

    def test_file_is_created_owner_only(self):
        path = self._write()
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(
            mode, 0o600,
            f"the request file is mode {mode:o}; the approval token and the "
            f"tool arguments are readable by someone other than the owner",
        )

    def test_directory_is_owner_only(self):
        path = self._write()
        mode = stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode)
        self.assertEqual(
            mode, 0o700,
            f"the request directory is mode {mode:o}; another account can "
            f"list, and so learn, which privileged actions are in flight",
        )

    def test_creation_never_widens_an_existing_file(self):
        """O_EXCL: an already-present path is an error, never an overwrite.

        Without O_EXCL a pre-planted file — one the attacker created wide, or
        made a symlink — would be opened and written through. The refusal is
        what makes the 0600 above meaningful.
        """
        path = self._write()
        with self.assertRaises(FileExistsError):
            pr._create_request_file(path)

    def test_owner_is_the_calling_user(self):
        path = self._write()
        self.assertEqual(os.stat(path).st_uid, os.getuid())


class RoundTripTests(_RuntimeDirTestCase):

    def test_contents_survive_exactly(self):
        path = self._write()
        tool, arguments, token = pr.read_request(path, expected_uid=os.getuid())
        self.assertEqual(tool, TOOL)
        self.assertEqual(arguments, ARGS)
        self.assertEqual(token, TOKEN)

    def test_nested_argument_structures_survive(self):
        nested = {"action": "write", "path": "/etc/x", "opts": {"mode": 420,
                  "backup": True, "tags": ["a", "b"]}}
        path = self._write(arguments=nested)
        _, arguments, _ = pr.read_request(path, expected_uid=os.getuid())
        self.assertEqual(arguments, nested)

    def test_reading_removes_the_request(self):
        path = self._write()
        pr.read_request(path, expected_uid=os.getuid())
        self.assertFalse(
            os.path.exists(path),
            "the request outlived the read that consumed it; a request left on "
            "disk is an approval token left on disk",
        )

    def test_the_path_carries_no_secret(self):
        """The path is the only thing that goes on the command line, so the
        path itself must not contain the token or the arguments."""
        path = self._write()
        self.assertNotIn(TOKEN, path)
        for value in ("install", "cowsay"):
            self.assertNotIn(value, path)


class RemovalOnEveryFailurePathTests(_RuntimeDirTestCase):

    def test_unparseable_body_is_refused_and_removed(self):
        path = self._write()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("this is not json")
        with self.assertRaises(pr.RequestError):
            pr.read_request(path, expected_uid=os.getuid())
        self.assertFalse(os.path.exists(path), "a refused request was left behind")

    def test_missing_field_is_refused_and_removed(self):
        path = self._write()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"version": pr.FORMAT_VERSION, "tool": TOOL}, fh)
        with self.assertRaises(pr.RequestError):
            pr.read_request(path, expected_uid=os.getuid())
        self.assertFalse(os.path.exists(path), "a refused request was left behind")

    def test_unknown_format_version_is_refused_and_removed(self):
        path = self._write()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"version": pr.FORMAT_VERSION + 99, "tool": TOOL,
                       "arguments": ARGS, "token": TOKEN}, fh)
        with self.assertRaises(pr.RequestError):
            pr.read_request(path, expected_uid=os.getuid())
        self.assertFalse(os.path.exists(path), "a refused request was left behind")

    def test_wrong_argument_type_is_refused_and_removed(self):
        path = self._write()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"version": pr.FORMAT_VERSION, "tool": TOOL,
                       "arguments": ["not", "a", "mapping"], "token": TOKEN}, fh)
        with self.assertRaises(pr.RequestError):
            pr.read_request(path, expected_uid=os.getuid())
        self.assertFalse(os.path.exists(path), "a refused request was left behind")

    def test_discard_is_idempotent(self):
        """The caller discards in a finally block, so discarding a request the
        runner already consumed must not itself raise."""
        path = self._write()
        pr.discard_request(path)
        pr.discard_request(path)  # already gone — must be silent
        self.assertFalse(os.path.exists(path))

    def test_discard_of_a_never_created_path_is_silent(self):
        pr.discard_request(os.path.join(self.runtime_dir, "no-such-request"))


class OverlappingDispatchTests(_RuntimeDirTestCase):

    def test_two_requests_in_flight_get_distinct_paths(self):
        first = self._write(arguments={"action": "install", "package": "one"})
        second = self._write(arguments={"action": "install", "package": "two"})
        self.assertNotEqual(first, second)
        self.assertTrue(os.path.exists(first))
        self.assertTrue(os.path.exists(second))

    def test_each_overlapping_request_reads_back_its_own_contents(self):
        first = self._write(arguments={"action": "install", "package": "one"},
                            token="token-one")
        second = self._write(arguments={"action": "install", "package": "two"},
                             token="token-two")
        # Consumed out of order, on purpose: nothing may depend on ordering.
        _, args2, tok2 = pr.read_request(second, expected_uid=os.getuid())
        _, args1, tok1 = pr.read_request(first, expected_uid=os.getuid())
        self.assertEqual(args1["package"], "one")
        self.assertEqual(tok1, "token-one")
        self.assertEqual(args2["package"], "two")
        self.assertEqual(tok2, "token-two")

    def test_consuming_one_leaves_the_other_intact(self):
        first = self._write(arguments={"action": "install", "package": "one"})
        second = self._write(arguments={"action": "install", "package": "two"})
        pr.read_request(first, expected_uid=os.getuid())
        self.assertFalse(os.path.exists(first))
        self.assertTrue(
            os.path.exists(second),
            "consuming one in-flight request removed another; two overlapping "
            "dispatches would destroy each other",
        )

    def test_many_concurrent_requests_are_all_distinct(self):
        paths = {self._write(arguments={"action": "install", "package": f"p{i}"})
                 for i in range(50)}
        self.assertEqual(len(paths), 50, "request paths collided")


class RootSideRefusalTests(_RuntimeDirTestCase):
    """The root side reads a path chosen by the unprivileged side. It checks
    what it is about to read rather than trusting it."""

    def test_wrong_owner_is_refused(self):
        path = self._write()
        # Claim to expect a different uid than the one that owns the file.
        with self.assertRaises(pr.RequestError):
            pr.read_request(path, expected_uid=os.getuid() + 1)

    def test_a_refused_foreign_file_is_not_removed(self):
        """A file we refuse because it is not ours is not ours to delete.

        Removing it would make a refusal into a deletion primitive against a
        path the caller chose — the opposite of a boundary check.
        """
        path = self._write()
        with self.assertRaises(pr.RequestError):
            pr.read_request(path, expected_uid=os.getuid() + 1)
        self.assertTrue(
            os.path.exists(path),
            "a file refused for wrong ownership was deleted anyway",
        )

    def test_group_or_world_readable_mode_is_refused(self):
        path = self._write()
        os.chmod(path, 0o644)
        with self.assertRaises(pr.RequestError):
            pr.read_request(path, expected_uid=os.getuid())

    def test_symlink_is_refused(self):
        target = os.path.join(self.runtime_dir, "target-of-the-link")
        with open(target, "w", encoding="utf-8") as fh:
            json.dump({"version": pr.FORMAT_VERSION, "tool": TOOL,
                       "arguments": ARGS, "token": TOKEN}, fh)
        os.chmod(target, 0o600)
        link = os.path.join(self.runtime_dir, "request-that-is-a-link")
        os.symlink(target, link)
        with self.assertRaises(pr.RequestError):
            pr.read_request(link, expected_uid=os.getuid())
        self.assertTrue(
            os.path.exists(target),
            "refusing a symlinked request deleted the file it pointed at",
        )

    def test_hard_linked_request_is_refused(self):
        """A second name for the same inode means someone else can still read
        the contents after we unlink our name."""
        path = self._write()
        os.link(path, os.path.join(self.runtime_dir, "second-name"))
        with self.assertRaises(pr.RequestError):
            pr.read_request(path, expected_uid=os.getuid())

    def test_directory_is_refused(self):
        path = os.path.join(self.runtime_dir, "a-directory-not-a-request")
        os.mkdir(path, 0o700)
        with self.assertRaises(pr.RequestError):
            pr.read_request(path, expected_uid=os.getuid())

    def test_absent_request_is_refused(self):
        with self.assertRaises(pr.RequestError):
            pr.read_request(
                os.path.join(self.runtime_dir, "never-written"),
                expected_uid=os.getuid(),
            )


class RuntimeDirectoryResolutionTests(_RuntimeDirTestCase):

    def test_uses_xdg_runtime_dir_when_set(self):
        path = self._write()
        self.assertTrue(
            path.startswith(self.runtime_dir + os.sep),
            f"{path} is not under the runtime directory {self.runtime_dir}",
        )

    def test_falls_back_to_the_per_uid_run_directory(self):
        """With XDG_RUNTIME_DIR unset the location is derived, not guessed at
        in /tmp — /tmp is shared, and a shared directory is the wrong home for
        an approval token even at 0600."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XDG_RUNTIME_DIR", None)
            resolved = pr.request_dir()
        self.assertEqual(
            resolved,
            os.path.join(f"/run/user/{os.getuid()}", pr.RUNTIME_SUBDIR),
        )


class BoundedReadTests(_RuntimeDirTestCase):
    """The root side must not read an unbounded file into memory.

    Added 2026-08-24 after an independent review listed a size check among the
    facts the root side establishes about a caller-supplied path and found it
    was the one that was missing. Everything else about the file is verified
    before its contents are believed — that it is a regular file, owned by the
    calling user, mode 0600, with a single link — and then the whole of it is
    read with no ceiling.

    The request is a small JSON object: a tool name, an arguments object, and a
    token. There is no legitimate request anywhere near the bound below. What a
    bound buys is that the unprivileged side cannot decide how much memory the
    privileged side allocates, which is a property worth having on its own and
    costs one comparison against a number `fstat` already returned.
    """

    def test_an_oversized_request_is_refused(self):
        path = pr.write_request("manage_packages", {"action": "list"}, "tok")
        # Grow the file past the bound while keeping every other property the
        # root side checks intact: same inode, same owner, same mode, one link.
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(" " * (pr.MAX_REQUEST_BYTES + 1))
        with self.assertRaises(pr.RequestError) as caught:
            pr.read_request(path, expected_uid=os.getuid())
        self.assertIn("too large", str(caught.exception).lower())

    def test_the_refusal_removes_the_file(self):
        """An oversized request is a CONTENT refusal: the file is ours, we
        established that before looking at its size, so it does not stay on
        disk carrying an approval token."""
        path = pr.write_request("manage_packages", {"action": "list"}, "tok")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(" " * (pr.MAX_REQUEST_BYTES + 1))
        with self.assertRaises(pr.RequestError):
            pr.read_request(path, expected_uid=os.getuid())
        self.assertFalse(os.path.exists(path), "the oversized request survived")

    def test_a_request_at_the_bound_is_still_accepted(self):
        """Negative control: the bound must not reject ordinary requests.

        Without this, a bound of zero would pass the test above and break every
        real dispatch.
        """
        padding = "x" * 2048
        path = pr.write_request(
            "manage_packages", {"action": "install", "package": padding}, "tok")
        self.assertLess(os.stat(path).st_size, pr.MAX_REQUEST_BYTES)
        tool, arguments, token = pr.read_request(path, expected_uid=os.getuid())
        self.assertEqual(tool, "manage_packages")
        self.assertEqual(arguments["package"], padding)
        self.assertEqual(token, "tok")


class RootConstructsThePathTests(_RuntimeDirTestCase):
    """The unprivileged side names an ID; the privileged side builds the path.

    Added 2026-08-24. Until now the caller handed root a complete filesystem
    path and root defended itself by inspecting what it found there — a regular
    file, right owner, right mode, one link, opened without following symlinks.
    Those checks are correct and they stay. But they are a defence applied to
    an input class that does not need to exist: there is no reason for the
    unprivileged side to be able to NAME an arbitrary path to a root process at
    all.

    So the argument across the boundary becomes an opaque request ID — hex, of
    a fixed length, nothing else accepted — and root constructs the path
    itself, beneath a runtime directory it has verified belongs to the calling
    user. Path traversal, absolute paths, symlinked parents and every other
    naming trick stop being things to defend against and become things that
    cannot be expressed.
    """

    def setUp(self):
        """Line the two views of the directory up on a temporary root.

        The unprivileged side reads $XDG_RUNTIME_DIR; the privileged side
        DERIVES <RUNTIME_ROOT>/<uid>. In production those name the same place.
        Here both are pointed at a temporary tree of the real shape, so the
        derivation is exercised rather than bypassed.
        """
        super().setUp()
        self._root = tempfile.TemporaryDirectory(prefix="runtime-root-")
        self.addCleanup(self._root.cleanup)
        uid_dir = os.path.join(self._root.name, str(os.getuid()))
        os.makedirs(uid_dir, mode=0o700, exist_ok=True)
        patcher = mock.patch.object(pr, "RUNTIME_ROOT", self._root.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": uid_dir},
                              clear=False)
        env.start()
        self.addCleanup(env.stop)

    def test_a_written_request_yields_an_id_that_resolves_back(self):
        path = pr.write_request("manage_packages", {"action": "list"}, "tok")
        request_id = pr.request_id_for(path)
        self.assertRegex(request_id, r"\A[0-9a-f]{32}\Z")
        resolved = pr.resolve_request(request_id, os.getuid())
        self.assertEqual(os.path.realpath(resolved), os.path.realpath(path))

    def test_ids_that_are_not_plain_hex_are_refused(self):
        for bad in (
            "../../etc/shadow",
            "/etc/shadow",
            "..",
            "",
            "g" * 32,
            "0" * 31,
            "0" * 33,
            "0123456789abcdef0123456789abcde/",
            "0123456789abcdef0123456789abcd\n",
            "0123456789ABCDEF0123456789ABCDEF",
        ):
            with self.subTest(request_id=bad):
                with self.assertRaises(pr.RequestError):
                    pr.resolve_request(bad, os.getuid())

    def test_a_resolved_path_never_leaves_the_request_directory(self):
        path = pr.write_request("manage_packages", {"action": "list"}, "tok")
        resolved = pr.resolve_request(pr.request_id_for(path), os.getuid())
        self.assertEqual(os.path.dirname(resolved), pr.request_dir())

    def test_a_world_writable_request_directory_is_refused(self):
        """The directory is part of what root is trusting; it is checked too."""
        pr.write_request("manage_packages", {"action": "list"}, "tok")
        directory = pr.request_dir()
        os.chmod(directory, 0o777)
        self.addCleanup(os.chmod, directory, 0o700)
        with self.assertRaises(pr.RequestError) as caught:
            pr.resolve_request("0" * 32, os.getuid())
        self.assertIn("mode", str(caught.exception).lower())

    def test_a_request_directory_owned_by_someone_else_is_refused(self):
        """Root must not read out of a directory the calling user does not own.

        The OWNERSHIP branch has to be the one that fires, so the directory for
        the other uid is really created first — this test used to name a uid
        whose directory did not exist and was satisfied by an ENOENT refusal,
        which proves only that a missing directory is refused. The directory
        below exists, is mode 0700, and is owned by THIS account while the
        caller is claimed to be another: exactly one fact is wrong, and it is
        the fact under test.
        """
        other_uid = os.getuid() + 1
        other_dir = os.path.join(pr.RUNTIME_ROOT, str(other_uid), "intergen")
        os.makedirs(other_dir, mode=0o700, exist_ok=True)
        self.assertEqual(os.stat(other_dir).st_uid, os.getuid(),
                         "fixture precondition: the directory is ours")
        with self.assertRaises(pr.RequestError) as caught:
            pr.resolve_request("0" * 32, other_uid)
        self.assertIn("owned", str(caught.exception).lower())


if __name__ == "__main__":
    unittest.main()
