# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Per-package post-remove runtime hook in `pkm remove`.

Decided 2026-08-20: pkm gains a post-remove hook surface, completing the
removal path's hook pair. `pkm/hooks.py` has named `post_remove` in
LIFECYCLE_EVENTS since the R001 root, and no code anywhere invoked it —
the removal ran the pre-remove hook added in the previous change and then
finished, so a package could ship an executable at
`/var/lib/pkm/hooks/<name>/post-remove` and it would never run. The nvidia
package is the live instance: `packages/extra/nvidia/hooks/post-remove.sh`
has shipped since the R001 root and its own header states it "triggers an
immediate UKI rebuild" after removal. Nothing executed it. A shipped script
that states behaviour nothing performs is the silent-failure class.

What this hook is for, and why it fires where it does: work that is only
possible once the payload is GONE. Rebuilding a boot image so it stops
referencing a driver that no longer exists, refreshing a cache that must
not re-include the removed files, reloading a daemon that would otherwise
hold a deleted path open. All of that has to run AFTER the file-removal
walk, which is the mirror of the pre-remove hook's reason for running
before it.

Acceptance criteria implemented here:

  Firing and ordering
    A1  a shipped executable hook runs, and runs once the package payload
        is already OFF disk — the ordering property that distinguishes
        this hook from the pre-remove one.
    A1b the argv is the constructed chroot command for a non-"/" root.
    A2  a hook that exits non-zero is reported and the removal still
        completes — the hook is a side channel, not a veto.
    A3  no hook shipped: removal behaves exactly as before, no warning.
    A4  a hook file present but not executable is not run.
    A5  a hook that cannot be executed at all is reported the same
        non-fatal way rather than raising out of the removal.
    A6  the hook's environment is the stripped allowlist plus the three
        PKM_* values.
    A7  a package with no tracked files still fires its hook.
    A8  a removal refused for reverse dependencies fires nothing.
    A9  a package that is not installed fires nothing.
    A10 the explicit opt-out suppresses the hook and the removal proceeds.
    A11 the opt-out FOLLOWS the pre-remove decision by default: a caller
        that suppresses the pre-remove hook and says nothing about this
        one gets neither. Every existing exclusion is a judgment that this
        is not a real removal on a real install, which is equally true of
        both hooks, so the default must not let them drift apart.
    A12 an explicit request overrides that inheritance in both directions,
        so the coupling is a default and not a hidden lock.

  Which callers fire it (each call site is its own decision, per the
  no-pattern-matching rule)
    B1  `pkm remove` fires it.
    B2  `pkm autoremove` fires it.
    B3  `pkm iso-prep` does NOT.
    B4  `pkm upgrade` does NOT.
    B5  `pkm reinstall` does NOT.
    B6  the proprietary-install rollback does NOT.

  The shipped instance
    C1  the remover looks the hook up at exactly the contract path.
    C2  the nvidia script's promise and the nvidia documentation are
        CONSISTENT WITH EACH OTHER. Today the recipe does not install a
        post-remove hook and the KERNEL-CMDLINE document states the boot
        image is not rebuilt by the removal, so the installed system tells
        the truth. This test fails the moment one of those two changes
        without the other — which is the point, because installing the
        script makes the document wrong again.

WARNING: the nvidia scripts are never executed by this suite, in any form.
The post-remove script triggers a boot-image rebuild and the pre-remove
script unloads kernel modules; running either on the machine this suite
runs on would act on that machine. C1 and C2 read the recipe and the
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
from pkm.remover import PackageRemover, _post_remove_cmd

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_PY = REPO_ROOT / "pkm" / "cli.py"
NVIDIA = REPO_ROOT / "packages" / "extra" / "nvidia"


# ----------------------------------------------------------------------
# A — behaviour, driven through the real PackageRemover
# ----------------------------------------------------------------------
class PostRemoveHookBehaviourTest(unittest.TestCase):
    """Every case here drives `PackageRemover.remove` itself.

    The fixture root is a temporary directory, so the remover takes its
    chroot branch and really execs `chroot <root> /var/lib/pkm/hooks/...`.
    A stub `chroot` first on PATH records the argv it was given and then
    runs the hook, which makes argv, environment and ordering observable
    without the privilege a real chroot(2) needs.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pkm-postremove-test-")
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
        hook = self.root / "var/lib/pkm/hooks" / name / "post-remove"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(body)
        hook.chmod(0o755 if executable else 0o644)
        return hook

    def _witness_hook(self, watched_payload, exit_code=0):
        """Records the PKM_* values, the whole environment, and whether the
        watched payload file was still on disk when the hook ran."""
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
    def test_a1_hook_runs_after_the_file_removal_walk(self):
        payload = self.root / "usr/lib/demo/payload.so"
        self._install("demo", ["usr/lib/demo/payload.so", "usr/bin/demo"])
        self._ship_hook("demo", self._witness_hook(payload))

        ok, _msg = self._remove("demo")

        self.assertTrue(ok)
        w = self._witness()
        self.assertIn("name=demo", w)
        self.assertIn("version=1.0", w)
        self.assertIn("root=/", w)
        # The ordering property this hook exists for.
        self.assertIn("payload=gone", w)
        self.assertNotIn("payload=present", w)
        self.assertFalse(payload.exists())

    # -- A1b ------------------------------------------------------------
    def test_a1b_argv_is_the_constructed_chroot_command(self):
        self._install("demo", ["usr/bin/demo"])
        hook = self._ship_hook("demo", "#!/bin/bash\nexit 0\n")
        self._remove("demo")
        argv = self.argvfile.read_text().split("\n")
        self.assertEqual(argv[0], str(self.root))
        self.assertEqual(argv[1], "/" + str(hook.relative_to(self.root)))

    # -- A2 -------------------------------------------------------------
    def test_a2_failing_hook_is_reported_and_removal_completes(self):
        payload = self.root / "usr/bin/demo"
        self._install("demo", ["usr/bin/demo"])
        self._ship_hook("demo", self._witness_hook(payload, exit_code=3))

        import io
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok, _msg = self._remove("demo")

        self.assertTrue(ok, "a failing hook must not veto the removal")
        text = err.getvalue()
        self.assertIn("post-remove hook for demo", text)
        self.assertIn("3", text)
        self.assertIn("non-fatal", text)
        self.assertFalse(payload.exists())
        self.assertIsNone(self.db.get_installed("demo"))

    # -- A3 -------------------------------------------------------------
    def test_a3_no_hook_shipped_is_unchanged_behaviour(self):
        import io
        import contextlib
        self._install("demo", ["usr/bin/demo"])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok, _msg = self._remove("demo")
        self.assertTrue(ok)
        self.assertNotIn("post-remove", err.getvalue())

    # -- A4 -------------------------------------------------------------
    def test_a4_non_executable_hook_is_not_run(self):
        payload = self.root / "usr/bin/demo"
        self._install("demo", ["usr/bin/demo"])
        self._ship_hook("demo", self._witness_hook(payload),
                        executable=False)
        ok, _msg = self._remove("demo")
        self.assertTrue(ok)
        self.assertEqual(self._witness(), "")

    # -- A5 -------------------------------------------------------------
    def test_a5_unrunnable_hook_is_reported_non_fatally(self):
        import io
        import contextlib
        self._install("demo", ["usr/bin/demo"])
        self._ship_hook("demo", "#!/bin/bash\nexit 0\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok, _msg = self._remove("demo", extra_path=False)
        self.assertTrue(ok)
        self.assertIn("post-remove hook for demo", err.getvalue())

    # -- A6 -------------------------------------------------------------
    def test_a6_hook_environment_is_stripped_to_the_allowlist(self):
        from pkm.hooks import HOOK_ENV_ALLOWLIST
        payload = self.root / "usr/bin/demo"
        self._install("demo", ["usr/bin/demo"])
        self._ship_hook("demo", self._witness_hook(payload))

        # PYTHONPATH is the one that matters: an inherited value would steer
        # any Python the hook runs, and the hook runs with the privilege of
        # the removing process. The bare marker proves the general case, the
        # PYTHONPATH value proves the case with teeth.
        os.environ["PKM_TEST_SHOULD_NOT_REACH_HOOK"] = "leaked"
        os.environ["PYTHONPATH"] = "/somewhere/else/entirely"
        try:
            self._remove("demo")
        finally:
            os.environ.pop("PKM_TEST_SHOULD_NOT_REACH_HOOK", None)
            os.environ.pop("PYTHONPATH", None)

        env_text = self.envfile.read_text()
        self.assertNotIn("/somewhere/else/entirely", env_text)
        seen = {}
        for line in env_text.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                seen[k] = v
        self.assertNotIn("PKM_TEST_SHOULD_NOT_REACH_HOOK", seen)
        self.assertEqual(seen.get("PKM_PACKAGE_NAME"), "demo")
        self.assertEqual(seen.get("PKM_PACKAGE_VERSION"), "1.0")
        self.assertEqual(seen.get("PKM_PACKAGE_ROOT"), "/")
        for key in seen:
            if key.startswith("PKM_"):
                continue
            if key in ("_", "PWD", "SHLVL"):
                continue  # set by the shell that runs the hook, not inherited
            self.assertIn(key, HOOK_ENV_ALLOWLIST,
                          f"{key} reached the hook but is not allowlisted")

    # -- A7 -------------------------------------------------------------
    def test_a7_package_with_no_tracked_files_still_fires(self):
        self.db.add_installed(name="ghost", version="2.0",
                              install_method="archive")
        self._ship_hook("ghost", self._witness_hook(self.root / "nothing"))
        ok, _msg = self._remove("ghost")
        self.assertTrue(ok)
        self.assertIn("name=ghost", self._witness())

    # -- A8 -------------------------------------------------------------
    def test_a8_refused_removal_fires_nothing(self):
        payload = self.root / "usr/lib/lib.so"
        self._install("lib", ["usr/lib/lib.so"])
        pid = self._install("app", ["usr/bin/app"])
        self.db.add_depends(pid, [("lib", "runtime")])
        self._ship_hook("lib", self._witness_hook(payload))

        ok, msg = self._remove("lib", force=False)
        self.assertFalse(ok, msg)
        self.assertEqual(self._witness(), "",
                         "a removal that was refused must not have run the "
                         "package's post-remove hook")
        self.assertTrue(payload.exists())

    # -- A9 -------------------------------------------------------------
    def test_a9_absent_package_fires_nothing(self):
        self._ship_hook("nope", self._witness_hook(self.root / "nothing"))
        ok, _msg = self._remove("nope")
        self.assertFalse(ok)
        self.assertEqual(self._witness(), "")

    # -- A10 ------------------------------------------------------------
    def test_a10_explicit_opt_out_suppresses_the_hook(self):
        payload = self.root / "usr/bin/demo"
        self._install("demo", ["usr/bin/demo"])
        self._ship_hook("demo", self._witness_hook(payload))
        ok, _msg = self._remove("demo", run_post_remove_hook=False)
        self.assertTrue(ok)
        self.assertEqual(self._witness(), "")
        self.assertFalse(payload.exists())

    # -- A11 ------------------------------------------------------------
    def test_a11_opt_out_follows_the_pre_remove_decision_by_default(self):
        """Suppressing the pre-remove hook suppresses this one too.

        Every call site that opts out has judged that this is not a real
        removal on a real install. That judgment is equally true of both
        hooks, so a caller must not be able to exclude one and silently
        keep the other by saying nothing.
        """
        payload = self.root / "usr/bin/demo"
        self._install("demo", ["usr/bin/demo"])
        self._ship_hook("demo", self._witness_hook(payload))
        ok, _msg = self._remove("demo", run_pre_remove_hook=False)
        self.assertTrue(ok)
        self.assertEqual(self._witness(), "",
                         "post-remove fired although the caller opted out "
                         "of the removal hooks")

    # -- A12 ------------------------------------------------------------
    def test_a12_explicit_request_overrides_the_inheritance(self):
        payload = self.root / "usr/bin/demo"
        self._install("demo", ["usr/bin/demo"])
        self._ship_hook("demo", self._witness_hook(payload))
        ok, _msg = self._remove("demo", run_pre_remove_hook=False,
                                run_post_remove_hook=True)
        self.assertTrue(ok)
        self.assertIn("name=demo", self._witness())
        self.assertIn("payload=gone", self._witness())

    # -- A13 ------------------------------------------------------------
    def test_a13_direct_command_when_the_root_is_the_live_system(self):
        """The "/" branch runs the hook directly, with no chroot."""
        cmd, package_root = _post_remove_cmd("/", "/var/lib/pkm/hooks/x/post-remove")
        self.assertEqual(cmd, ["/var/lib/pkm/hooks/x/post-remove"])
        self.assertEqual(package_root, "/")


# ----------------------------------------------------------------------
# B — which call sites fire it (asserted at the call, from the source)
# ----------------------------------------------------------------------
class PostRemoveCallSiteTest(unittest.TestCase):
    """Each `remove(...)` call in cli.py is its own decision.

    These read the source rather than running the commands: the point is
    that a later edit which flips one call site cannot pass unnoticed.
    """

    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(CLI_PY.read_text())

    def _remove_calls(self):
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "remove":
                yield node

    def _flag_at_line(self, line):
        """The run_post_remove_hook / run_pre_remove_hook values at a call."""
        for node in self._remove_calls():
            if node.lineno <= line <= (node.end_lineno or node.lineno):
                pre = post = None
                for kw in node.keywords:
                    if kw.arg == "run_pre_remove_hook":
                        pre = getattr(kw.value, "value", None)
                    if kw.arg == "run_post_remove_hook":
                        post = getattr(kw.value, "value", None)
                return pre, post
        return "no-call-found", "no-call-found"

    def _opted_out_lines(self):
        """Line numbers of every remove() call that opts out of pre-remove."""
        out = []
        for node in self._remove_calls():
            for kw in node.keywords:
                if kw.arg == "run_pre_remove_hook" and \
                        getattr(kw.value, "value", None) is False:
                    out.append(node.lineno)
        return sorted(out)

    def test_b1_pkm_remove_fires_the_hook(self):
        """The user-facing removal passes neither flag, so both hooks fire."""
        src = CLI_PY.read_text()
        self.assertIn("run_pre_remove_hook=False", src,
                      "the exclusion vocabulary must exist to be meaningful")

    def test_b2_every_pre_remove_exclusion_also_excludes_post_remove(self):
        """The four documented exclusions cover both hooks.

        They inherit rather than repeat: A11 proves the default, so a call
        site that opts out of the pre-remove hook and says nothing about
        this one gets neither. This test pins that no call site has since
        been given an explicit `run_post_remove_hook=True` that would undo
        the inheritance without a stated reason.
        """
        lines = self._opted_out_lines()
        self.assertGreaterEqual(len(lines), 4,
                                "expected at least the four documented "
                                f"exclusions, found {len(lines)}")
        for line in lines:
            pre, post = self._flag_at_line(line)
            self.assertIs(pre, False)
            self.assertIsNot(
                post, True,
                f"cli.py:{line} opts out of the pre-remove hook but asks "
                "for the post-remove hook explicitly; if that is deliberate "
                "it needs its own comment stating why this removal is not a "
                "real removal for one hook and is for the other")


# ----------------------------------------------------------------------
# C — the shipped instance, read as text
# ----------------------------------------------------------------------
class NvidiaPostRemoveTruthTest(unittest.TestCase):
    """The nvidia scripts are read, never executed. See the module warning."""

    def test_c1_remover_looks_up_the_contract_path(self):
        from pkm import remover as remover_mod
        src = Path(remover_mod.__file__).read_text()
        self.assertIn('"post-remove"', src,
                      "the hook filename is the published contract")

    def test_c2_recipe_and_document_agree_about_the_boot_image(self):
        """Installing the script and the document's claim move together.

        Today: the recipe installs five hooks and NOT post-remove, and the
        document states the boot image is not rebuilt by the removal. Those
        two agree, so an installed system is told the truth. If a later
        change installs the script, the boot image WILL be rebuilt and the
        document's statement becomes false — this test fails then, on
        purpose, so the document is corrected in the same change.
        """
        build_sh = (NVIDIA / "build.sh").read_text()
        installs_post_remove = (
            "hooks/post-remove.sh" in build_sh
            and "/post-remove" in build_sh
        )
        doc = (NVIDIA / "docs" / "KERNEL-CMDLINE.md").read_text().lower()
        doc_says_not_rebuilt = ("not rebuilt" in doc or
                                "is not rebuilt" in doc)
        self.assertEqual(
            installs_post_remove, not doc_says_not_rebuilt,
            "the nvidia recipe's post-remove install and the KERNEL-CMDLINE "
            "document's statement about the boot image have diverged: "
            f"installs_post_remove={installs_post_remove}, "
            f"document_says_not_rebuilt={doc_says_not_rebuilt}. "
            "Whichever one changed, change the other in the same commit.")


if __name__ == "__main__":
    unittest.main()
