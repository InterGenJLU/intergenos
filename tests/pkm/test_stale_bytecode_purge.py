#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A deploy that replaces a Python module drops that module's cached bytecode.

THE MEASURED FAILURE. On an installed system on 2026-08-06, a package upgrade
deployed new Python sources at 11:45:17 and the very next invocation, twelve
seconds later, executed a code path that had already been REMOVED from the file
on disk. The source was new, the compiled copy beside it was old, and the
interpreter used the old one; it did not recompile until 11:48.

WHY IT CAN HAPPEN AT ALL. CPython decides whether a cached `.pyc` is still
valid by comparing the modification time and size the cache recorded against
the source file's current pair. A deployed file's timestamp comes out of the
archive rather than from the moment it landed, so a replaced module can present
the very pair the stale cache was built against — and the interpreter then has
no reason to look at the new bytes.

WHY IT IS A SECURITY DEFECT AND NOT A PAPERCUT. An upgrade that installs a fix
and then keeps running the unfixed code is a machine whose owner has been told
they are patched and is not. It is the same class as any other silent failure:
the system reports success and the change has not taken effect.

THE FIX ASSERTED HERE. The deploy removes the cached bytecode for every Python
module it just replaced. With no cache entry the interpreter must read the file
that is actually on disk — the one mechanism whose correctness does not depend
on getting cache invalidation right. Nothing is lost: CPython regenerates the
cache on the next import.

THE LAST CASE IN THIS FILE IS THE ONE THAT MATTERS MOST. It does not inspect
the fix at all — it reproduces the ORIGINAL FAILURE against a real interpreter,
with a real stale cache and a real timestamp collision, and shows the old code
running; then it applies the purge and shows the new code running. A case that
only checked "the .pyc was deleted" would pass just as happily against a
mechanism that deletes the wrong file.
"""

import compileall
import importlib.util
import os
import py_compile
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# The purge helper is imported INSIDE each case, not here. A module-level
# import of a function this change introduces would make the whole file
# fail to COLLECT on a tree that predates it, which proves only that the
# code is new. Kept local, the reproduction case below still collects
# against the old tree and fails where it matters: it shows the stale
# bytecode being executed and then has nothing to fix it with.


class PurgeMechanismTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _module(self, rel, body="VALUE = 1\n"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        py_compile.compile(str(p), doraise=True)
        return p

    def _cache_files(self, rel):
        p = self.root / rel
        cache = p.parent / "__pycache__"
        if not cache.is_dir():
            return []
        return sorted(cache.glob(f"{p.stem}.*.pyc"))

    def test_removes_the_cached_bytecode_of_a_deployed_module(self):
        self._module("usr/lib/app/mod.py")
        self.assertTrue(self._cache_files("usr/lib/app/mod.py"))
        from pkm.installer import _purge_stale_bytecode
        removed = _purge_stale_bytecode(self.root, ["usr/lib/app/mod.py"])
        self.assertTrue(removed)
        self.assertEqual(self._cache_files("usr/lib/app/mod.py"), [])

    def test_leaves_the_source_file_alone(self):
        p = self._module("usr/lib/app/mod.py")
        from pkm.installer import _purge_stale_bytecode
        _purge_stale_bytecode(self.root, ["usr/lib/app/mod.py"])
        self.assertTrue(p.is_file())
        self.assertEqual(p.read_text(), "VALUE = 1\n")

    def test_touches_only_the_modules_the_deploy_replaced(self):
        """A neighbouring module in the SAME cache directory that this deploy
        did not touch keeps its compiled copy. Purging a whole __pycache__
        directory would be easier and would throw away work that was never
        stale."""
        self._module("usr/lib/app/mine.py")
        self._module("usr/lib/app/theirs.py")
        from pkm.installer import _purge_stale_bytecode
        _purge_stale_bytecode(self.root, ["usr/lib/app/mine.py"])
        self.assertEqual(self._cache_files("usr/lib/app/mine.py"), [])
        self.assertTrue(self._cache_files("usr/lib/app/theirs.py"))

    def test_removes_every_optimisation_level(self):
        """CPython writes `mod.cpython-NNN.pyc` and, under -O/-OO, additional
        `.opt-1`/`.opt-2` variants. Leaving one behind leaves the defect."""
        p = self._module("usr/lib/app/mod.py")
        cache = p.parent / "__pycache__"
        for opt in (1, 2):
            py_compile.compile(str(p), optimize=opt, doraise=True)
        self.assertGreaterEqual(len(list(cache.glob("mod.*.pyc"))), 2)
        from pkm.installer import _purge_stale_bytecode
        _purge_stale_bytecode(self.root, ["usr/lib/app/mod.py"])
        self.assertEqual(list(cache.glob("mod.*.pyc")), [])

    def test_ignores_non_python_files_and_directories(self):
        (self.root / "usr" / "bin").mkdir(parents=True)
        (self.root / "usr" / "bin" / "tool").write_text("#!/bin/sh\n")
        from pkm.installer import _purge_stale_bytecode
        removed = _purge_stale_bytecode(
            self.root, ["usr/bin/tool", "usr/lib/", "usr/share/data.txt"])
        self.assertEqual(removed, [])

    def test_a_module_with_no_cache_directory_is_not_an_error(self):
        p = self.root / "usr" / "lib" / "fresh.py"
        p.parent.mkdir(parents=True)
        p.write_text("X = 1\n")
        from pkm.installer import _purge_stale_bytecode
        self.assertEqual(
            _purge_stale_bytecode(self.root, ["usr/lib/fresh.py"]), [])

    def test_an_unremovable_cache_file_is_reported_and_not_fatal(self):
        """A cache file that cannot be removed must not fail an install: the
        package's own files are correct. The condition worth surfacing is that
        a stale compiled copy may still run."""
        p = self._module("usr/lib/app/mod.py")
        cache = p.parent / "__pycache__"
        os.chmod(cache, 0o500)          # readable, not writable
        from pkm.installer import _purge_stale_bytecode
        try:
            removed = _purge_stale_bytecode(self.root, ["usr/lib/app/mod.py"])
        finally:
            os.chmod(cache, 0o700)
        self.assertEqual(removed, [])
        self.assertTrue(list(cache.glob("mod.*.pyc")))


class RealStaleBytecodeReproductionTest(unittest.TestCase):
    """Reproduce the actual failure with a real interpreter, then close it.

    The timestamp collision is manufactured deliberately rather than waited
    for: the replaced source is given the SAME modification time and the SAME
    size as the file it replaced, which is exactly the condition a deployed
    archive can produce and is the condition under which CPython accepts a
    stale cache. Without forcing it the failure is a race, and a race that
    only sometimes reproduces is not a proof.
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.pkgdir = self.root / "usr" / "lib" / "app"
        self.pkgdir.mkdir(parents=True)
        self.mod = self.pkgdir / "greet.py"
        self.driver = self.root / "run.py"
        self.driver.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(self.pkgdir)!r})\n"
            "import greet\n"
            "print(greet.answer())\n"
        )

    def tearDown(self):
        self._td.cleanup()

    def _run(self):
        res = subprocess.run(
            [sys.executable, str(self.driver)],
            capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.root)},
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        return res.stdout.strip()

    def _write_old_and_compile(self):
        # Same LENGTH as the replacement below, so the size half of CPython's
        # validity check also matches after the swap.
        self.mod.write_text("def answer():\n    return 'OLD'\n")
        compileall.compile_dir(str(self.pkgdir), quiet=2, force=True)
        return os.stat(self.mod)

    def _replace_preserving_stat(self, st):
        self.mod.write_text("def answer():\n    return 'NEW'\n")
        os.utime(self.mod, (st.st_atime, st.st_mtime))
        self.assertEqual(os.stat(self.mod).st_size, st.st_size,
                         "the reproduction requires an identical size")

    def test_the_failure_reproduces_and_the_purge_closes_it(self):
        st = self._write_old_and_compile()
        self.assertEqual(self._run(), "OLD")

        # A deploy replaces the module. Timestamp and size match what the
        # cache recorded, so the interpreter accepts the stale compiled copy.
        self._replace_preserving_stat(st)
        stale_answer = self._run()
        self.assertEqual(
            stale_answer, "OLD",
            "the stale-bytecode condition did not reproduce on this "
            "interpreter, so the case below would prove nothing")

        # The fix: drop the cached bytecode for the module that was replaced.
        from pkm.installer import _purge_stale_bytecode
        removed = _purge_stale_bytecode(self.root, ["usr/lib/app/greet.py"])
        self.assertTrue(removed, "nothing was purged")
        self.assertEqual(
            self._run(), "NEW",
            "after the purge the interpreter must read the source that is "
            "actually on disk")

    def test_the_deploy_path_calls_the_purge(self):
        """The mechanism above is only worth anything if the install path
        actually runs it. Located in the source rather than by mocking, so
        this stays true of the deploy step itself.

        BEFORE the extract, not after it. This case used to require the call
        after the extract, and there it deleted the compiled files the archive
        had just deployed — every fresh installation then failed `pkm verify`
        with thousands of the packages' own .pyc files missing (2026-09-02).
        tests/pkm/test_stale_bytecode_purge_keeps_the_archives_own.py holds
        the order and proves it against a real install."""
        from pkm import installer as installer_mod
        src = Path(installer_mod.__file__).read_text(encoding="utf-8")
        deploy_marker = "ok, err = _safe_extract_tar("
        self.assertIn(deploy_marker, src)
        # The LAST extract in the installer is the deploy (the first is the
        # staging extract the file list is built from).
        before_deploy = src.rsplit(deploy_marker, 1)[0]
        window = before_deploy.rsplit("file_list = []", 1)[1]
        self.assertIn("_purge_stale_bytecode(self.root, file_list)", window)


if __name__ == "__main__":
    unittest.main()
