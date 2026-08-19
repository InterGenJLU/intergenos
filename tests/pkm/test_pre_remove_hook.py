# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Per-package pre-remove runtime hook in `pkm remove`.

Decided 2026-08-19: pkm gains a pre-remove hook surface. Before this change
the package system shipped a hook path that nothing ever ran — a package
could install an executable at
`/var/lib/pkm/hooks/<name>/pre-remove`, its own documentation could tell the
user that script fires at remove time, and the removal walked straight past
it. The nvidia package is the live instance: its recipe installs
`pre-remove`, the script's own header and the package's KERNEL-CMDLINE
document both state it runs at `pkm remove` time, and the removal path
invoked no external command at all. A documented behaviour that does not
happen is the silent-failure class the project exists to remove.

What the hook is for: work that must happen while the package's payload is
still on disk — stopping services the payload provides, unloading kernel
modules built from it, deleting artefacts the package created after its
manifest was sealed (so no manifest records them and the file-removal walk
cannot see them). All of that has to run BEFORE the files go away, which is
why the firing point is ahead of the removal walk rather than after it.

Acceptance criteria implemented here:

  Firing and ordering
    A1  a shipped executable hook runs, and runs while the package payload
        is still present on disk.
    A2  a hook that exits non-zero is reported and the removal still
        completes — the hook is a side channel, not a veto.
    A3  no hook shipped: removal behaves exactly as before, no warning.
    A4  a hook file present but not executable is not run.
    A5  a hook that cannot be executed at all is reported the same
        non-fatal way rather than raising out of the removal.
    A6  the hook's environment is the stripped allowlist plus the three
        PKM_* values; a variable the parent process carries that is not on
        the allowlist does not reach the hook.
    A7  a package with no tracked files still fires its hook — that path
        removes the package too.
    A8  a removal refused for reverse dependencies fires nothing.
    A9  a package that is not installed fires nothing.
    A10 the explicit opt-out suppresses the hook and the removal proceeds.

  Which callers fire it (each call site is its own decision, per the
  no-pattern-matching rule — these assert the argument at the call, so a
  later edit that changes one cannot pass unnoticed)
    B1  `pkm remove` fires it.
    B2  `pkm autoremove` fires it.
    B3  `pkm iso-prep` does NOT: that is the build-time prune of packages
        the image never ships, running inside the mint chroot whose /run
        and /proc belong to the build machine.
    B4  `pkm upgrade` does NOT: the package is not going away, and its
        remove-then-install shape is an implementation detail.
    B5  `pkm reinstall` does NOT, for the same reason.
    B6  the proprietary-install rollback does NOT: it undoes an install
        that never completed.

  The shipped instance
    C1  the nvidia recipe installs its script at exactly the path the
        remover looks up, executable.
    C2  the nvidia documentation describes only behaviour that is wired.

⚠️ The nvidia script is never executed by this suite, in any form. It stops
services, unloads kernel modules and `rm -rf`s
`/lib/modules/*/extra/nvidia` — running it on the machine this suite runs on
would destroy that machine's live driver. C1/C2 read the recipe and the
document as text.
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB
from pkm.remover import PackageRemover, _pre_remove_cmd

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_PY = REPO_ROOT / "pkm" / "cli.py"
NVIDIA = REPO_ROOT / "packages" / "extra" / "nvidia"


# ----------------------------------------------------------------------
# A — behaviour, driven through the real PackageRemover
# ----------------------------------------------------------------------
class PreRemoveHookBehaviourTest(unittest.TestCase):
    """Every case here drives `PackageRemover.remove` itself.

    The fixture root is a temporary directory, so the remover takes its
    chroot branch and really execs `chroot <root> /var/lib/pkm/hooks/...`.
    A stub `chroot` placed first on PATH records the exact argv it was
    given and then runs the hook, which is what makes the argv, the
    environment and the ordering all observable without the test needing
    the privilege a real chroot(2) requires.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pkm-preremove-test-")
        self.root = Path(self._tmp)
        self.db = PackageDB(db_path=str(self.root / "pkm.db"),
                            root=str(self.root))
        self.witness = self.root / "witness.txt"
        self.envfile = self.root / "hook-env.txt"
        self.argvfile = self.root / "chroot-argv.txt"
        self.stubdir = self.root / "stubbin"
        self.stubdir.mkdir()
        self._write_chroot_stub()

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        shutil.rmtree(self._tmp, ignore_errors=True)

    # -- fixture helpers ------------------------------------------------
    def _write_chroot_stub(self):
        """A `chroot` that records its argv and then runs the program.

        It does not change root — it resolves the program path under the
        given root and execs it, which is the observable part. What is
        being proven here is that the remover builds and runs the right
        command; the kernel's own chroot(2) is not under test.
        """
        stub = self.stubdir / "chroot"
        stub.write_text(
            "#!/bin/bash\n"
            f"printf '%s\\n' \"$@\" > {self.argvfile}\n"
            "root=\"$1\"; shift\n"
            "prog=\"$1\"; shift\n"
            "exec \"${root%/}$prog\" \"$@\"\n"
        )
        stub.chmod(0o755)

    def _install(self, name, entries, version="1.0"):
        for rel in entries:
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(rel.encode())
        pid = self.db.add_installed(name=name, version=version,
                                    install_method="archive")
        if entries:
            self.db.add_files(pid, entries)
        return pid

    def _ship_hook(self, name, body, executable=True):
        hook = self.root / "var/lib/pkm/hooks" / name / "pre-remove"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(body)
        hook.chmod(0o755 if executable else 0o644)
        return hook

    def _witness_hook(self, watched_payload, exit_code=0):
        """A hook that records the PKM_* values, its whole environment, and
        whether the watched payload file was still on disk when it ran."""
        return (
            "#!/bin/bash\n"
            f"{{ echo \"name=$PKM_PACKAGE_NAME\";"
            f" echo \"version=$PKM_PACKAGE_VERSION\";"
            f" echo \"root=$PKM_PACKAGE_ROOT\"; }} > {self.witness}\n"
            f"if [ -e {watched_payload} ]; then\n"
            f"  echo 'payload=present' >> {self.witness}\n"
            "else\n"
            f"  echo 'payload=gone' >> {self.witness}\n"
            "fi\n"
            f"env > {self.envfile}\n"
            f"exit {exit_code}\n"
        )

    def _remove(self, name, extra_path=True, **kwargs):
        """Run the removal with the stub `chroot` first on PATH.

        `extra_path=False` runs it with a PATH that has no `chroot` at all,
        which is how the could-not-execute case is reached honestly.
        """
        payload_path = os.environ.get("PATH", "")
        new_path = (f"{self.stubdir}:{payload_path}" if extra_path
                    else str(self.stubdir / "empty"))
        prev = os.environ.get("PATH")
        os.environ["PATH"] = new_path
        try:
            return PackageRemover(self.db, root=str(self.root)).remove(
                name, **kwargs)
        finally:
            if prev is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = prev

    def _witness(self):
        return (self.witness.read_text() if self.witness.exists() else "")

    # -- A1 -------------------------------------------------------------
    def test_a1_hook_runs_before_the_file_removal_walk(self):
        payload = self.root / "usr/lib/demo/payload.so"
        self._install("demo", ["usr/lib/demo/payload.so", "usr/bin/demo"])
        self._ship_hook("demo", self._witness_hook(payload))

        ok, msg = self._remove("demo", force=True)
        self.assertTrue(ok, msg)

        w = self._witness()
        self.assertIn("name=demo", w)
        self.assertIn("version=1.0", w)
        # PKM_PACKAGE_ROOT is "/" from the hook's own perspective: inside the
        # chroot the target IS the root.
        self.assertIn("root=/", w)
        self.assertIn("payload=present", w,
                      "the hook must run while the payload is still on disk")
        # and the removal itself still happened
        self.assertFalse(payload.exists())

    def test_a1b_argv_is_the_constructed_chroot_command(self):
        self._install("demo", ["usr/bin/demo"])
        hook = self._ship_hook("demo", "#!/bin/bash\nexit 0\n")
        self._remove("demo", force=True)

        recorded = self.argvfile.read_text().splitlines()
        expected, _ = _pre_remove_cmd(self.root, hook)
        self.assertEqual(recorded, expected[1:],
                         "the argv the remover passed to chroot must be the "
                         "one its command constructor produces")

    # -- A2 -------------------------------------------------------------
    def test_a2_failing_hook_is_reported_and_removal_completes(self):
        payload = self.root / "usr/bin/demo"
        self._install("demo", ["usr/bin/demo"])
        self._ship_hook("demo", self._witness_hook(payload, exit_code=3))

        proc = self._run_in_child("demo", ["usr/bin/demo"], exit_code=3)
        self.assertIn("pre-remove hook", proc.stderr)
        self.assertIn("demo", proc.stderr)
        self.assertIn("3", proc.stderr)
        self.assertIn("REMOVE_OK=True", proc.stdout)
        self.assertIn("PAYLOAD_GONE=True", proc.stdout)

    def _run_in_child(self, name, entries, exit_code=0):
        """Drive one removal in a child interpreter so the warning stream is
        captured as the user would see it, rather than through a redirect
        this test installed itself."""
        script = self.root / "drive.py"
        payload = self.root / entries[0]
        script.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
            "from pkm.database import PackageDB\n"
            "from pkm.remover import PackageRemover\n"
            f"db = PackageDB(db_path={str(self.root / 'pkm.db')!r},"
            f" root={str(self.root)!r})\n"
            f"ok, msg = PackageRemover(db, root={str(self.root)!r})"
            f".remove({name!r}, force=True)\n"
            "print('REMOVE_OK=%s' % ok)\n"
            f"import os; print('PAYLOAD_GONE=%s' % (not os.path.exists("
            f"{str(payload)!r})))\n"
        )
        env = dict(os.environ)
        env["PATH"] = f"{self.stubdir}:{env.get('PATH', '')}"
        return subprocess.run(
            ["python3", str(script)], capture_output=True, text=True,
            env=env,
        )

    # -- A3 -------------------------------------------------------------
    def test_a3_no_hook_shipped_is_unchanged_behaviour(self):
        self._install("demo", ["usr/bin/demo"])
        proc = self._run_in_child("demo", ["usr/bin/demo"])
        self.assertIn("REMOVE_OK=True", proc.stdout)
        self.assertIn("PAYLOAD_GONE=True", proc.stdout)
        self.assertNotIn("pre-remove", proc.stderr)
        self.assertFalse(self.argvfile.exists(),
                         "no hook shipped must mean no command was run")

    # -- A4 -------------------------------------------------------------
    def test_a4_non_executable_hook_is_not_run(self):
        payload = self.root / "usr/bin/demo"
        self._install("demo", ["usr/bin/demo"])
        self._ship_hook("demo", self._witness_hook(payload),
                        executable=False)

        ok, msg = self._remove("demo", force=True)
        self.assertTrue(ok, msg)
        self.assertEqual(self._witness(), "",
                         "a non-executable hook file must not be run")
        # The witness alone does not prove the guard: without it the exec is
        # attempted and the kernel refuses the non-executable file, which
        # leaves the witness empty for a completely different reason. What
        # separates the two is whether a command was attempted AT ALL — the
        # stub records its argv before it tries to exec anything.
        self.assertFalse(self.argvfile.exists(),
                         "no command may be attempted for a hook the "
                         "executable-bit check should have rejected")

    # -- A5 -------------------------------------------------------------
    def test_a5_unrunnable_hook_is_reported_non_fatally(self):
        self._install("demo", ["usr/bin/demo"])
        self._ship_hook("demo", "#!/bin/bash\nexit 0\n")
        # PATH with no `chroot` on it at all: the exec raises, and the
        # removal must survive it.
        ok, msg = self._remove("demo", force=True, extra_path=False)
        self.assertTrue(ok, msg)
        self.assertFalse((self.root / "usr/bin/demo").exists())

    # -- A6 -------------------------------------------------------------
    def test_a6_hook_environment_is_stripped_to_the_allowlist(self):
        payload = self.root / "usr/bin/demo"
        self._install("demo", ["usr/bin/demo"])
        self._ship_hook("demo", self._witness_hook(payload))

        os.environ["PKM_TEST_SMUGGLED"] = "must-not-reach-the-hook"
        os.environ["PYTHONPATH"] = "/attacker/controlled"
        try:
            ok, msg = self._remove("demo", force=True)
        finally:
            os.environ.pop("PKM_TEST_SMUGGLED", None)
            os.environ.pop("PYTHONPATH", None)
        self.assertTrue(ok, msg)

        env_seen = self.envfile.read_text()
        self.assertIn("PKM_PACKAGE_NAME=demo", env_seen)
        self.assertIn("PKM_PACKAGE_VERSION=1.0", env_seen)
        self.assertIn("PKM_PACKAGE_ROOT=/", env_seen)
        self.assertNotIn("PKM_TEST_SMUGGLED", env_seen)
        self.assertNotIn("/attacker/controlled", env_seen)

    # -- A7 -------------------------------------------------------------
    def test_a7_package_with_no_tracked_files_still_fires(self):
        self._install("bare", [])
        self._ship_hook("bare", self._witness_hook(self.root / "nothing"))

        ok, msg = self._remove("bare", force=True)
        self.assertTrue(ok, msg)
        self.assertIn("name=bare", self._witness(),
                      "a package removed with no tracked files is still "
                      "being removed, so its hook still fires")

    # -- A8 -------------------------------------------------------------
    def test_a8_refused_removal_fires_nothing(self):
        self._install("lib", ["usr/lib/lib.so"])
        pid = self._install("app", ["usr/bin/app"])
        self.db.add_depends(pid, [("lib", "runtime")])
        self._ship_hook("lib", self._witness_hook(self.root / "usr/lib/lib.so"))

        ok, msg = self._remove("lib", force=False)
        self.assertFalse(ok, msg)
        self.assertEqual(self._witness(), "",
                         "a removal that was refused must not have run the "
                         "package's pre-remove hook")

    # -- A9 -------------------------------------------------------------
    def test_a9_absent_package_fires_nothing(self):
        self._ship_hook("ghost", self._witness_hook(self.root / "nothing"))
        ok, msg = self._remove("ghost", force=True)
        self.assertFalse(ok, msg)
        self.assertEqual(self._witness(), "")

    # -- A10 ------------------------------------------------------------
    def test_a10_explicit_opt_out_suppresses_the_hook(self):
        payload = self.root / "usr/bin/demo"
        self._install("demo", ["usr/bin/demo"])
        self._ship_hook("demo", self._witness_hook(payload))

        ok, msg = self._remove("demo", force=True,
                               run_pre_remove_hook=False)
        self.assertTrue(ok, msg)
        self.assertEqual(self._witness(), "",
                         "the opt-out must suppress the hook")
        self.assertFalse(payload.exists(),
                         "the removal itself still happens")

    def test_a10b_direct_command_when_the_root_is_the_live_system(self):
        """The other branch of the command constructor: with root "/" the
        hook is run directly rather than through chroot. Asserted on the
        constructor because the only way to exercise it end to end is to
        remove a package from the machine running the suite."""
        hook = Path("/var/lib/pkm/hooks/demo/pre-remove")
        cmd, hook_root = _pre_remove_cmd(Path("/"), hook)
        self.assertEqual(cmd, [str(hook)])
        self.assertEqual(hook_root, "/")


# ----------------------------------------------------------------------
# B — which callers fire it
# ----------------------------------------------------------------------
class CallSiteDecisionTest(unittest.TestCase):
    """One decision per call site, asserted at the call.

    `remove()` fires the hook by default, because "remove" means the package
    is going away and a caller that forgets an argument should get the
    documented behaviour. The callers that are NOT that — the build-time
    prune, the two remove-then-install flows, and the failed-install
    rollback — say so explicitly, and this test reads the argument they
    actually pass.
    """

    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(CLI_PY.read_text(), filename=str(CLI_PY))

    def _calls_in(self, func_name):
        """Every `<something>.remove(...)` call inside the named function."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                return [
                    c for c in ast.walk(node)
                    if isinstance(c, ast.Call)
                    and isinstance(c.func, ast.Attribute)
                    and c.func.attr == "remove"
                ]
        self.fail(f"{func_name} not found in {CLI_PY}")

    def _hook_kwarg(self, call):
        for kw in call.keywords:
            if kw.arg == "run_pre_remove_hook":
                self.assertIsInstance(
                    kw.value, ast.Constant,
                    "the opt-out must be a literal at the call site")
                return kw.value.value
        return "absent"

    def _assert_single(self, func_name, expected):
        calls = self._calls_in(func_name)
        self.assertEqual(len(calls), 1,
                         f"{func_name} should hold exactly one remove() call")
        self.assertEqual(self._hook_kwarg(calls[0]), expected)

    def test_b1_pkm_remove_fires_the_hook(self):
        # default (argument absent) == fires
        self._assert_single("cmd_remove", "absent")

    def test_b2_autoremove_fires_the_hook(self):
        self._assert_single("cmd_autoremove", "absent")

    def test_b3_iso_prep_does_not_fire_the_hook(self):
        self._assert_single("cmd_iso_prep", False)

    def test_b4_upgrade_does_not_fire_the_hook(self):
        calls = self._calls_in("cmd_upgrade")
        self.assertTrue(calls, "cmd_upgrade should hold a remove() call")
        for c in calls:
            self.assertIs(self._hook_kwarg(c), False)

    def test_b5_reinstall_does_not_fire_the_hook(self):
        calls = self._calls_in("cmd_reinstall")
        self.assertTrue(calls, "cmd_reinstall should hold a remove() call")
        for c in calls:
            self.assertIs(self._hook_kwarg(c), False)

    def test_b6_proprietary_rollback_does_not_fire_the_hook(self):
        self._assert_single("_rollback_proprietary", False)


# ----------------------------------------------------------------------
# C — the shipped instance (read as text; never executed)
# ----------------------------------------------------------------------
class NvidiaShippedHookContractTest(unittest.TestCase):
    """The nvidia recipe and document, read as text.

    ⚠️ Nothing here runs the script. It stops services, unloads modules and
    deletes `/lib/modules/*/extra/nvidia`; executing it would take the
    driver off the machine running this suite.
    """

    def test_c1_recipe_installs_the_hook_at_the_looked_up_path(self):
        build_sh = (NVIDIA / "build.sh").read_text()
        self.assertIn(
            'install -m 755 "$BUILD_DIR/hooks/pre-remove.sh" \\\n'
            '        "$DESTDIR/var/lib/pkm/hooks/nvidia/pre-remove"',
            build_sh,
            "the recipe must install the script executable at the path the "
            "remover looks up",
        )
        self.assertTrue((NVIDIA / "hooks" / "pre-remove.sh").is_file())

    def test_c2_documentation_describes_only_wired_behaviour(self):
        doc = (NVIDIA / "docs" / "KERNEL-CMDLINE.md").read_text()
        # The document names the INSTALLED path, which is what a reader can
        # look at and run, not the filename inside the recipe directory.
        self.assertIn("/var/lib/pkm/hooks/nvidia/pre-remove", doc)
        # The companion post-remove script is in the recipe directory but the
        # recipe does not install it and no caller runs it, so the document
        # must not tell a user it fires.
        build_sh = (NVIDIA / "build.sh").read_text()
        self.assertNotIn("post-remove", build_sh,
                         "if the recipe starts shipping post-remove, this "
                         "test and the document both need revisiting")
        self.assertNotIn(
            "`post-remove.sh` hook triggers", doc,
            "the document must not claim a hook that nothing installs or "
            "runs",
        )


if __name__ == "__main__":
    unittest.main()
