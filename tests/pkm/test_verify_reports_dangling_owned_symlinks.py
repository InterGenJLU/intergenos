# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An owned symbolic link must resolve: verify used to certify the link alone.

verify_package probes every owned path with lstat, which answers for a link
itself, so a link whose target does not exist counted as present and the
package verified ok. Measured on installed systems: 72 owned links in one
icon theme and 12 in a GTK theme point at files those packages never
shipped, and `pkm verify` reported both packages ok.

The rule pinned here is RESOLVE, not ownership. An owned link whose target
resolves is fine whether or not any package owns the target. An owned link
whose target does not resolve is reported under its own name, `dangling`,
beside missing and modified, and counts as a failure (EXIT_MODIFIED; the
command exits 1) in strict and fast mode alike, because resolving is part of
existence, not of content.

Resolution follows the kernel's rules inside the package root, so a scratch
root is judged the way a process chrooted into it would see it:
  - an absolute target is looked up under the root, never on the machine
    running the check;
  - ".." never climbs above the root;
  - ".." after a link starts from where the link led, not from its text;
  - more path after something that is not a directory ("file/..",
    "file/.", "file/") does not resolve (ENOTDIR);
  - more than 40 links in one resolution does not resolve (ELOOP).
For every shape built from relative targets inside the root, the kernel's
own answer (os.stat on the same path) is asserted beside the verdict, so the
tests measure agreement with the kernel instead of restating the rules.

Two outcomes are neither ok nor dangling and report `undeterminable`: a
directory on the way to the target that this user may not search, and a
target under /proc, /sys, /dev or /run while that directory is not a mount
point under the root (an offline root, where the filesystem the target lives
on does not exist yet). Where it is mounted, the link is judged like any
other.

The permission case cannot be produced as root, which bypasses the bits; it
skips as root and says why.
"""
from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pkm.database import PackageDB
from pkm.verifier import (
    EXIT_MODIFIED,
    EXIT_OK,
    EXIT_UNDETERMINED,
    PackageVerifier,
)

RUNNING_AS_ROOT = (os.geteuid() == 0)
ROOT_SKIP = ("root bypasses the permission bits this test relies on; the "
             "condition under test is a NON-root user who may not search a "
             "directory on the way to a link's target")

ICON = "usr/share/icons/Theme/status/22/weather-clear-night-000.svg"


class _ScratchRoot(unittest.TestCase):
    """A package root in a temporary directory, with its own database."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.top = Path(self.tmp.name)
        self.root = self.top / "root"
        self.root.mkdir()
        self.db = PackageDB(self.top / "t.db", root=str(self.root))
        self.verifier = PackageVerifier(self.db)
        self._locked = []

    def tearDown(self):
        self.db.close()
        for d in self._locked:
            d.chmod(0o755)
        self.tmp.cleanup()

    def _file(self, rel, data=b"payload"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def _link(self, rel, target):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, p)
        return p

    def _own(self, name, relpaths):
        pkg_id = self.db.add_installed(name, "1.0", release=1, tier="desktop")
        self.db.add_files(pkg_id, list(relpaths))
        return pkg_id

    def _lock(self, rel):
        d = self.root / rel
        d.chmod(0o000)
        self._locked.append(d)

    def _kernel_resolves(self, rel):
        """The kernel's own answer for a path inside the scratch root. Valid
        only for shapes whose targets are relative and stay inside the root,
        where the host's "/" never enters the resolution."""
        try:
            os.stat(self.root / rel)
        except OSError:
            return False
        return True

    def _verify(self, name, mode="strict"):
        return self.verifier.verify(name, mode=mode)


class DanglingOwnedLinkIsAFailure(_ScratchRoot):
    """The measured defect: an owned link to nothing verified ok."""

    def test_link_to_a_file_never_shipped_is_dangling_in_strict_mode(self):
        self._link(ICON, "weather-clear-night.svg")
        self._own("theme", [ICON])
        result = self._verify("theme", mode="strict")
        self.assertEqual(result.get("dangling"), [ICON])
        self.assertEqual(result["missing"], [],
                         "the link itself is present; it is not missing")
        self.assertEqual(result["modified"], [])
        self.assertEqual(result["undeterminable"], [])
        self.assertEqual(result["exit_code"], EXIT_MODIFIED)

    def test_link_to_a_file_never_shipped_is_dangling_in_fast_mode(self):
        # fast mode skips content, not existence, and a link that reaches
        # nothing does not establish that anything exists.
        self._link(ICON, "weather-clear-night.svg")
        self._own("theme", [ICON])
        result = self._verify("theme", mode="fast")
        self.assertEqual(result.get("dangling"), [ICON])
        self.assertEqual(result["exit_code"], EXIT_MODIFIED)

    def test_target_removed_after_install_is_dangling_not_undeterminable(self):
        # Registered while it resolved, so a content hash was recorded
        # through the link; the target is then removed. The content check
        # used to fail to read it and file the link as "could not be
        # checked" — a healthy-machine answer for a broken link.
        self._file("usr/lib/libdemo.so.1.2", b"\x7fELF")
        self._link("usr/lib/libdemo.so.1", "libdemo.so.1.2")
        self._own("demo", ["usr/lib/libdemo.so.1"])
        (self.root / "usr/lib/libdemo.so.1.2").unlink()
        result = self._verify("demo", mode="strict")
        self.assertEqual(result.get("dangling"), ["usr/lib/libdemo.so.1"])
        self.assertEqual(result["undeterminable"], [])
        self.assertEqual(result["exit_code"], EXIT_MODIFIED)

    def test_target_removed_after_install_is_dangling_in_fast_mode(self):
        self._file("usr/lib/libdemo.so.1.2", b"\x7fELF")
        self._link("usr/lib/libdemo.so.1", "libdemo.so.1.2")
        self._own("demo", ["usr/lib/libdemo.so.1"])
        (self.root / "usr/lib/libdemo.so.1.2").unlink()
        result = self._verify("demo", mode="fast")
        self.assertEqual(result.get("dangling"), ["usr/lib/libdemo.so.1"])
        self.assertEqual(result["exit_code"], EXIT_MODIFIED)

    def test_the_report_carries_the_link_text_and_the_reason(self):
        self._link(ICON, "weather-clear-night.svg")
        self._own("theme", [ICON])
        result = self._verify("theme")
        self.assertEqual(result.get("link_targets"),
                         {ICON: "weather-clear-night.svg"})
        self.assertIn("nothing exists", (result.get("link_reasons") or {})
                      .get(ICON, ""))

    def test_a_configuration_link_that_dangles_is_dangling(self):
        # Configuration files are exempt from the CONTENT check because
        # people edit them; a link under etc/ that reaches nothing is an
        # existence failure, which no exemption covers.
        self._link("etc/demo/active.conf", "../../usr/share/demo/none.conf")
        self._own("demo", ["etc/demo/active.conf"])
        result = self._verify("demo")
        self.assertEqual(result.get("dangling"), ["etc/demo/active.conf"])
        self.assertEqual(result["exit_code"], EXIT_MODIFIED)

    def test_a_link_to_a_missing_owned_file_reports_each_under_its_own_name(self):
        self._file("usr/lib/libx.so.1", b"\x7fELF")
        self._link("usr/lib/libx.so", "libx.so.1")
        self._own("x", ["usr/lib/libx.so", "usr/lib/libx.so.1"])
        (self.root / "usr/lib/libx.so.1").unlink()
        result = self._verify("x")
        self.assertEqual(result["missing"], ["usr/lib/libx.so.1"])
        self.assertEqual(result.get("dangling"), ["usr/lib/libx.so"])
        self.assertEqual(result["exit_code"], EXIT_MODIFIED)

    def test_an_absent_owned_path_under_a_dangling_directory_link_is_missing(self):
        # The boundary between the two names: when the owned path itself
        # cannot be reached it is missing (as before); dangling is for a
        # link that IS there and leads nowhere.
        self._link("usr/share/alias", "../share/nowhere")
        self._own("alias", ["usr/share/alias/inner.txt"])
        result = self._verify("alias")
        self.assertEqual(result["missing"], ["usr/share/alias/inner.txt"])
        self.assertEqual(result.get("dangling", []), [])


class ResolvingOwnedLinksStayOk(_ScratchRoot):
    """Controls: every one of these is ok before the change and after it."""

    def _assert_ok(self, name, rel):
        for mode in ("strict", "fast"):
            with self.subTest(mode=mode):
                result = self._verify(name, mode=mode)
                self.assertEqual(result.get("dangling", []), [])
                self.assertEqual(result["missing"], [])
                self.assertEqual(result["undeterminable"], [])
                self.assertEqual(result["exit_code"], EXIT_OK)
        if rel is not None:
            self.assertTrue(self._kernel_resolves(rel),
                            "precondition: the kernel resolves this shape")

    def test_link_to_a_shipped_file_is_ok(self):
        self._file("usr/share/icons/Theme/status/22/weather-clear-night.svg")
        self._link(ICON, "weather-clear-night.svg")
        self._own("theme", [
            ICON, "usr/share/icons/Theme/status/22/weather-clear-night.svg"])
        self._assert_ok("theme", ICON)

    def test_link_to_a_file_no_package_owns_is_ok(self):
        # RESOLVE, not ownership.
        self._file("usr/share/common/shared.svg")
        self._link("usr/share/theme/shared.svg", "../common/shared.svg")
        self._own("theme", ["usr/share/theme/shared.svg"])
        self._assert_ok("theme", "usr/share/theme/shared.svg")

    def test_link_to_a_directory_is_ok(self):
        (self.root / "usr/share/data/v2").mkdir(parents=True)
        self._link("usr/share/data/current", "v2")
        self._own("data", ["usr/share/data/current"])
        self._assert_ok("data", "usr/share/data/current")

    def test_a_chain_of_links_ending_in_a_file_is_ok(self):
        self._file("usr/lib/libc.so.9.9")
        self._link("usr/lib/libc.so.9", "libc.so.9.9")
        self._link("usr/lib/libc.so", "libc.so.9")
        self._own("c", ["usr/lib/libc.so", "usr/lib/libc.so.9"])
        self._assert_ok("c", "usr/lib/libc.so")

    def test_forty_links_in_a_row_resolve(self):
        # Linux follows at most 40 links in one resolution; the 40th still
        # resolves. The kernel's answer is asserted beside the verdict.
        self._file("usr/share/chain/end")
        for i in range(40):
            nxt = f"l{i + 1}" if i + 1 < 40 else "end"
            self._link(f"usr/share/chain/l{i}", nxt)
        self._own("chain", ["usr/share/chain/l0"])
        self._assert_ok("chain", "usr/share/chain/l0")

    def test_absolute_target_is_looked_up_under_the_root(self):
        # The file exists only inside the root. Following the absolute text
        # on the machine running the check would find nothing and call a
        # sound package broken.
        rel = "usr/share/igos-verify-probe-7f3c/real.svg"
        self._file(rel)
        self.assertFalse(os.path.lexists("/" + rel),
                         "precondition: the host does not have this path")
        self._link("usr/share/theme/abs.svg", "/" + rel)
        self._own("theme", ["usr/share/theme/abs.svg"])
        self._assert_ok("theme", None)

    def test_dotdot_after_a_directory_link_starts_where_the_link_led(self):
        # lib -> usr/lib, and lib/x -> ../share/data/y. The directory
        # holding x is usr/lib, so ".." is usr: usr/share/data/y, which
        # exists. Text arithmetic from "lib" would look for share/data/y
        # at the top of the root, which does not.
        (self.root / "usr/lib").mkdir(parents=True)
        self._file("usr/share/data/y")
        self._link("lib", "usr/lib")
        self._link("usr/lib/x", "../share/data/y")
        self._own("merged", ["lib/x"])
        self._assert_ok("merged", "lib/x")


class DanglingShapes(_ScratchRoot):
    """Every way a path can fail to resolve, each judged as the kernel does."""

    def _assert_dangling(self, name, rel, kernel_oracle=True):
        result = self._verify(name)
        self.assertEqual(result.get("dangling"), [rel])
        self.assertEqual(result["exit_code"], EXIT_MODIFIED)
        if kernel_oracle:
            self.assertFalse(self._kernel_resolves(rel),
                             "precondition: the kernel refuses this shape")
        return result

    # /etc/passwd exists on every Linux machine that runs these tests, and
    # the scratch root has no etc/ at all: a path the machine running the
    # check has and the root being verified does not.
    HOST_ONLY = "/etc/passwd"

    def test_absolute_target_that_exists_only_on_this_machine_is_dangling(self):
        self.assertTrue(os.path.exists(self.HOST_ONLY),
                        "precondition: the machine running the test has it")
        self._link("usr/share/theme/host", self.HOST_ONLY)
        self._own("theme", ["usr/share/theme/host"])
        # The host's kernel follows the absolute text to the host's own
        # file; inside the root there is nothing at that path.
        self.assertTrue(os.path.exists(self.root / "usr/share/theme/host"))
        self._assert_dangling("theme", "usr/share/theme/host",
                              kernel_oracle=False)

    def test_dotdot_never_climbs_above_the_root(self):
        link_dir = self.root / "usr/share/theme"
        climb = "../" * (len(link_dir.parts) + 2)
        self._link("usr/share/theme/up", climb + self.HOST_ONLY.lstrip("/"))
        self._own("theme", ["usr/share/theme/up"])
        self.assertTrue(os.path.exists(self.root / "usr/share/theme/up"),
                        "precondition: on the host this climbs out and hits")
        self._assert_dangling("theme", "usr/share/theme/up",
                              kernel_oracle=False)

    def test_dotdot_after_a_directory_link_is_physical_not_textual(self):
        # The reverse of the control: share/data/y exists at the top of the
        # root, where text arithmetic would land, but not under usr/share,
        # where the kernel lands.
        (self.root / "usr/lib").mkdir(parents=True)
        self._file("share/data/y")
        self._link("lib", "usr/lib")
        self._link("usr/lib/z", "../share/data/y")
        self._own("merged", ["lib/z"])
        self._assert_dangling("merged", "lib/z")

    def test_more_path_after_a_regular_file_is_dangling(self):
        self._file("usr/share/demo/file")
        shapes = {"dotdot": "file/..", "dot": "file/.", "slash": "file/",
                  "child": "file/child"}
        for tag, target in shapes.items():
            with self.subTest(target=target):
                rel = f"usr/share/demo/{tag}"
                self._link(rel, target)
                self._own(f"shape-{tag}", [rel])
                self._assert_dangling(f"shape-{tag}", rel)

    def test_a_loop_of_links_is_dangling(self):
        self._link("usr/share/loop/a", "b")
        self._link("usr/share/loop/b", "a")
        self._link("usr/share/loop/self", "self")
        self._own("loop", ["usr/share/loop/a", "usr/share/loop/b"])
        self._own("selfloop", ["usr/share/loop/self"])
        result = self._verify("loop")
        self.assertEqual(sorted(result.get("dangling") or []),
                         ["usr/share/loop/a", "usr/share/loop/b"])
        self.assertFalse(self._kernel_resolves("usr/share/loop/a"))
        self._assert_dangling("selfloop", "usr/share/loop/self")

    def test_forty_one_links_in_a_row_are_dangling(self):
        self._file("usr/share/chain/end")
        for i in range(41):
            nxt = f"l{i + 1}" if i + 1 < 41 else "end"
            self._link(f"usr/share/chain/l{i}", nxt)
        self._own("chain", ["usr/share/chain/l0"])
        result = self._assert_dangling("chain", "usr/share/chain/l0")
        self.assertIn("40", result["link_reasons"]["usr/share/chain/l0"])

    def test_a_chain_whose_last_link_reaches_nothing_is_dangling_at_every_link(self):
        self._link("usr/lib/libm.so", "libm.so.6")
        self._link("usr/lib/libm.so.6", "libm.so.6.0")
        self._own("m", ["usr/lib/libm.so", "usr/lib/libm.so.6"])
        result = self._verify("m")
        self.assertEqual(sorted(result.get("dangling") or []),
                         ["usr/lib/libm.so", "usr/lib/libm.so.6"])

    def test_an_empty_link_text_is_dangling(self):
        # Linux refuses to create one (symlink(2) returns ENOENT), but a
        # filesystem made elsewhere can carry it, and splitting "" would
        # read as "this directory". The kernel answers ENOENT.
        link = self._link("usr/share/demo/empty", "placeholder")
        self._own("empty", ["usr/share/demo/empty"])
        real_readlink = os.readlink

        def readlink(path, *args, **kwargs):
            if os.fspath(path) == str(link):
                return ""
            return real_readlink(path, *args, **kwargs)

        with mock.patch("os.readlink", side_effect=readlink):
            result = self._verify("empty")
        self.assertEqual(result.get("dangling"), ["usr/share/demo/empty"])
        self.assertEqual(result["exit_code"], EXIT_MODIFIED)


class UndeterminableLinks(_ScratchRoot):
    """A link verify could not follow is neither ok nor damaged."""

    @unittest.skipIf(RUNNING_AS_ROOT, ROOT_SKIP)
    def test_link_through_an_unsearchable_directory_is_undeterminable(self):
        self._file("usr/share/private/data.bin")
        self._link("usr/share/pub/data.bin", "../private/data.bin")
        self._lock("usr/share/private")
        self._own("priv", ["usr/share/pub/data.bin"])
        for mode in ("strict", "fast"):
            with self.subTest(mode=mode):
                result = self._verify("priv", mode=mode)
                self.assertEqual(result["undeterminable"],
                                 ["usr/share/pub/data.bin"])
                self.assertEqual(result.get("dangling"), [],
                                 "a link this user may not follow is not "
                                 "a broken link")
                self.assertEqual(result["missing"], [])
                self.assertEqual(result["exit_code"], EXIT_UNDETERMINED)

    def test_link_into_an_unmounted_run_is_undeterminable(self):
        # An offline root: run/ is a plain directory, and /run/lock is made
        # by the running system, so it does not exist here yet.
        (self.root / "run").mkdir()
        self.assertFalse(os.path.ismount(self.root / "run"))
        self._link("var/lock", "../run/lock")
        self._own("base-files", ["var/lock"])
        for mode in ("strict", "fast"):
            with self.subTest(mode=mode):
                result = self._verify("base-files", mode=mode)
                self.assertEqual(result["undeterminable"], ["var/lock"])
                self.assertEqual(result.get("dangling"), [])
                self.assertEqual(result["exit_code"], EXIT_UNDETERMINED)
        self.assertIn("/run", result["link_reasons"]["var/lock"])
        self.assertEqual(result["link_targets"]["var/lock"], "../run/lock")

    def test_absolute_link_into_an_unmounted_proc_is_undeterminable(self):
        self._link("etc/mtab", "/proc/self/mounts")
        self._own("base-files", ["etc/mtab"])
        result = self._verify("base-files")
        self.assertEqual(result["undeterminable"], ["etc/mtab"])
        self.assertEqual(result.get("dangling"), [])
        self.assertEqual(result["exit_code"], EXIT_UNDETERMINED)

    def test_link_into_run_is_ok_when_its_target_is_there(self):
        (self.root / "run/lock").mkdir(parents=True)
        self._link("var/lock", "../run/lock")
        self._own("base-files", ["var/lock"])
        result = self._verify("base-files")
        self.assertEqual(result.get("dangling", []), [])
        self.assertEqual(result["undeterminable"], [])
        self.assertEqual(result["exit_code"], EXIT_OK)

    def test_link_into_a_mounted_runtime_filesystem_is_judged(self):
        # Where /run IS a mount point the runtime filesystem exists, so a
        # target missing from it is simply missing. A real mount needs
        # root; the mount test is replaced for the root's run/ only.
        (self.root / "run").mkdir()
        self._link("var/lock", "../run/lock")
        self._own("base-files", ["var/lock"])
        run_dir = str(self.root / "run")
        real_ismount = os.path.ismount

        def ismount(path):
            if os.fspath(path) == run_dir:
                return True
            return real_ismount(path)

        with mock.patch("os.path.ismount", side_effect=ismount):
            result = self._verify("base-files")
        self.assertEqual(result.get("dangling"), ["var/lock"])
        self.assertEqual(result["undeterminable"], [])
        self.assertEqual(result["exit_code"], EXIT_MODIFIED)


class HookGeneratedDirectoryRows(_ScratchRoot):
    """A hook-generated directory row meets the resolve rule like every owned path.

    verify selects a directory row only when the package's own hook created
    it, and passes a present one without a line of its own: a directory holds
    no content to check. The link probe runs BEFORE that pass, so a row whose
    path is now a link to nothing is reported dangling instead of passing as a
    present directory. These two cases hold that order; with the pass moved
    ahead of the probe, every other test in tests/pkm still passes.
    """

    def _generated_dir(self, name, rel):
        pkg_id = self._own(name, [rel + "/"])
        self.db.mark_files_generated(pkg_id, [rel + "/"])
        rows = self.db.conn.execute(
            "SELECT path, is_dir, is_generated FROM files WHERE package_id = ?",
            (pkg_id,)).fetchall()
        self.assertEqual([(rel, 1, 1)], [tuple(r) for r in rows],
                         "precondition: one hook-generated directory row")

    def _cli(self, package):
        from pkm import cli

        class _Args:
            verify_mode = "strict"
            verify_all = False
            verify_detail = False

        args = _Args()
        args.package = package
        out = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                code = cli.cmd_verify(self.db, args) or 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue()

    def test_a_generated_directory_row_that_is_a_link_to_nothing_is_dangling(self):
        self._link("usr/share/gd2", "no-such-directory")
        self._generated_dir("gd2", "usr/share/gd2")
        for mode in ("strict", "fast"):
            with self.subTest(mode=mode):
                result = self._verify("gd2", mode=mode)
                self.assertEqual(result.get("dangling"), ["usr/share/gd2"])
                self.assertEqual(result["missing"], [])
                self.assertEqual(result["exit_code"], EXIT_MODIFIED)
        code, text = self._cli("gd2")
        self.assertEqual(code, 1)
        self.assertIn("/usr/share/gd2 -> no-such-directory", text)

    def test_a_real_generated_directory_gets_no_line(self):
        (self.root / "usr/share/gd1").mkdir(parents=True)
        self._generated_dir("gd1", "usr/share/gd1")
        for mode in ("strict", "fast"):
            with self.subTest(mode=mode):
                result = self._verify("gd1", mode=mode)
                for key in ("dangling", "missing", "modified", "undeterminable",
                            "generated", "generated_absent"):
                    self.assertEqual(result.get(key, []), [], key)
                self.assertEqual(result["exit_code"], EXIT_OK)
        code, text = self._cli("gd1")
        self.assertEqual(code, 0)
        self.assertIn("gd1: ok", text)
        self.assertNotIn("usr/share/gd1", text)


class ResultShapeAndTrace(_ScratchRoot):

    def test_trace_event_carries_the_dangling_count(self):
        self._link(ICON, "weather-clear-night.svg")
        self._own("theme", [ICON])
        trace = mock.MagicMock()
        with mock.patch("pkm.verifier._TRACE_AVAILABLE", True), \
                mock.patch("pkm.verifier._trace", trace):
            self._verify("theme")
        kwargs = trace.trace_event.call_args.kwargs
        self.assertEqual(trace.trace_event.call_args.args[0],
                         "pkm_verify_result")
        self.assertEqual(kwargs.get("dangling"), 1)
        self.assertEqual(kwargs.get("exit_code"), EXIT_MODIFIED)

    def test_superseded_payload_carries_an_empty_dangling_list(self):
        self._own("old", [])
        self._own("new", [])
        self.db.mark_superseded("old", "new")
        self.db.conn.commit()
        result = self._verify("old")
        self.assertEqual(result.get("dangling"), [])
        self.assertEqual(result.get("link_targets"), {})

    def test_verify_all_reports_the_dangling_package(self):
        self._link(ICON, "weather-clear-night.svg")
        self._own("theme", [ICON])
        self._file("usr/bin/tool")
        self._own("tool", ["usr/bin/tool"])
        by_name = {n: r for n, _v, r in self.verifier.verify_all()}
        self.assertEqual(by_name["theme"].get("dangling"), [ICON])
        self.assertEqual(by_name["theme"]["exit_code"], EXIT_MODIFIED)
        self.assertEqual(by_name["tool"]["exit_code"], EXIT_OK)


class CliReporting(_ScratchRoot):
    """The command's exit status and the lines a person reads."""

    def _run(self, package=None, verify_all=False, detail=False):
        from pkm import cli

        class _Args:
            verify_mode = "strict"

        args = _Args()
        args.package = package
        args.verify_all = verify_all
        args.verify_detail = detail
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.cmd_verify(self.db, args)
                code = rc or 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue() + err.getvalue()

    def test_single_package_exits_one_and_names_the_link_and_its_target(self):
        self._link(ICON, "weather-clear-night.svg")
        self._own("theme", [ICON])
        code, text = self._run("theme")
        self.assertEqual(code, 1)
        self.assertIn(f"/{ICON} -> weather-clear-night.svg", text)
        self.assertNotIn("ok (", text)

    def test_all_counts_the_package_as_failed_and_says_why(self):
        self._link(ICON, "weather-clear-night.svg")
        self._own("theme", [ICON])
        self._file("usr/bin/tool")
        self._own("tool", ["usr/bin/tool"])
        code, text = self._run(verify_all=True)
        self.assertEqual(code, 1)
        self.assertIn("theme 1.0 — 0 missing, 0 modified, 1 dangling link",
                      text)
        self.assertIn("1 ok, 1 with issues", text)

    def test_all_with_detail_lists_each_dangling_link(self):
        self._link(ICON, "weather-clear-night.svg")
        self._own("theme", [ICON])
        code, text = self._run(verify_all=True, detail=True)
        self.assertEqual(code, 1)
        self.assertIn(f"/{ICON} -> weather-clear-night.svg", text)

    def test_single_package_names_why_a_link_could_not_be_checked(self):
        (self.root / "run").mkdir()
        self._link("var/lock", "../run/lock")
        self._own("base-files", ["var/lock"])
        code, text = self._run("base-files")
        self.assertEqual(code, 3)
        self.assertIn("/var/lock -> ../run/lock", text)
        self.assertIn("not mounted", text)

    def test_a_package_whose_links_all_resolve_still_exits_zero(self):
        self._file("usr/share/icons/Theme/status/22/weather-clear-night.svg")
        self._link(ICON, "weather-clear-night.svg")
        self._own("theme", [ICON])
        code, text = self._run("theme")
        self.assertEqual(code, 0)
        self.assertIn("theme: ok", text)


if __name__ == "__main__":
    unittest.main()
