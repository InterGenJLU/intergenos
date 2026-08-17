#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A sealed hook body must keep the function context the recipe wrote it in.

WHAT WENT WRONG. hookseal extracts a recipe's `post_install()` body and emits
it as a standalone script under `set -e`. The body was pasted at TOP LEVEL, so
every construct that is only legal inside a function stopped being legal. The
common one is a bare `return`: bash refuses it outside a function or a sourced
script and exits 2.

  $ bash -e post_install.sh
  post_install.sh: line 12: return: can only `return' from a function or
                                     sourced script
  rc=2

That is the shape `packages/core/intergenos-base-files` is written in — a
documented stub whose whole body is `return 0` — so its sealed hook failed on
every archive install, and pkm reported the package's post-install step as
failed for a recipe that does nothing at all.

WHY THE SEALER'S OWN VALIDATION MISSED IT, which is the more important half.
hookseal syntax-checks what it emits with `bash -n`, and `bash -n` ACCEPTS a
top-level `return` — the refusal is a run-time error, not a parse error. So the
check passed, the script shipped, and the failure appeared on user machines.
`bash -n` cannot see this class at all, which is why the tests below EXECUTE the
sealed script rather than parsing it.

THE FIX. render_script wraps the body in a function and invokes it, so the body
runs in exactly the context the recipe author wrote it for. `return` returns,
`local` declares, and `set -e` still governs. Nothing about the body is
rewritten — the wrapper is the only change, and that keeps the seal a purely
textual operation.
"""

import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "igos-build"))

import hookseal  # noqa: E402


def _run(script_text, args=(), env=None):
    """Write the script and RUN it the way pkm does. Returns the completed run.

    Execution, not parsing. The defect this file exists for is invisible to
    `bash -n`, so a test that only parsed would pass against the bug.
    """
    with tempfile.TemporaryDirectory(prefix="hookseal-ctx-") as tmp:
        p = Path(tmp) / "hook.sh"
        p.write_text(script_text)
        p.chmod(0o755)
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        return subprocess.run(["bash", "-e", str(p), *args],
                              capture_output=True, text=True, env=run_env)


class BareReturnTest(unittest.TestCase):
    """The measured production shape."""

    def test_a_bare_return_body_runs_clean(self):
        script = hookseal.render_script(
            "post_install", "    return 0", "somepkg", "1.0-1")
        r = _run(script)
        self.assertEqual(
            r.returncode, 0,
            f"a sealed `return 0` body exited {r.returncode}.\n"
            f"stderr: {r.stderr}")
        self.assertNotIn("can only `return'", r.stderr)

    def test_the_real_base_files_recipe_seals_into_a_hook_that_runs(self):
        """Against the recipe in the tree, not a fixture.

        This is the package that actually failed, and it is the ONE real recipe
        body this file executes. That is only safe because the body is a
        documented no-op stub, so the safety is enforced below as a checked
        gate rather than assumed: if the recipe ever grows a real command, the
        test refuses to run it instead of quietly executing whatever was added.
        Recipe bodies do real work on the machine running the suite — see the
        note on CorpusSweepTest for what happened when a sweep ran them.
        """
        build_sh = (_REPO_ROOT / "packages" / "core"
                    / "intergenos-base-files" / "build.sh")
        body = hookseal.extract_function(build_sh.read_text(), "post_install")
        self.assertIsNotNone(body, "the recipe declares no post_install()")
        self.assertRegex(
            body, r"(?m)^\s*return\b",
            "this recipe no longer contains a bare `return`, so it no longer "
            "covers the shape this test exists for — point the test at a "
            "recipe that does, or keep a fixture that does")
        # The gate: every non-comment, non-blank line must be a bare return.
        # Anything else means the body now DOES something, and this test stops
        # rather than running it.
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            self.assertRegex(
                stripped, r"^return\b",
                f"this recipe's post_install is no longer an inert stub — it "
                f"now runs {stripped!r}. Refusing to execute a real recipe "
                f"body in the test suite; cover it structurally instead.")
        script = hookseal.render_script(
            "post_install", body, "intergenos-base-files", "1.0-1")
        r = _run(script)
        self.assertEqual(r.returncode, 0,
                         f"the sealed real recipe exited {r.returncode}.\n"
                         f"stderr: {r.stderr}")

    def test_a_nonzero_return_is_still_a_failure(self):
        """The wrapper must not swallow the body's exit status.

        A hook that returns 2 has to reach pkm as 2. Wrapping in a function and
        forgetting to propagate the invocation's status would turn every failing
        hook into a silent success, which is far worse than the bug being
        fixed.
        """
        script = hookseal.render_script(
            "post_install", "    return 3", "somepkg", "1.0-1")
        r = _run(script)
        self.assertEqual(r.returncode, 3,
                         f"expected the body's own status 3, got "
                         f"{r.returncode}; stderr: {r.stderr}")

    def test_an_early_return_stops_the_body(self):
        """`return` must actually RETURN, not merely be tolerated.

        A wrapper that made `return` legal but ran the rest of the body anyway
        would pass every test above while changing what recipes do.
        """
        body = ('    echo BEFORE\n'
                '    return 0\n'
                '    echo AFTER')
        script = hookseal.render_script("post_install", body, "p", "1")
        r = _run(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("BEFORE", r.stdout)
        self.assertNotIn("AFTER", r.stdout,
                         "the body kept running after `return`")


class LocalDeclarationTest(unittest.TestCase):
    """`local` is the same class of construct and the same failure."""

    def test_a_local_declaration_runs_clean(self):
        body = ('    local v="x"\n'
                '    echo "got:$v"')
        r = _run(hookseal.render_script("post_install", body, "p", "1"))
        self.assertEqual(r.returncode, 0,
                         f"a sealed `local` body exited {r.returncode}.\n"
                         f"stderr: {r.stderr}")
        self.assertIn("got:x", r.stdout)
        self.assertNotIn("can only be used in a function", r.stderr)


class SetEStillGovernsTest(unittest.TestCase):
    """Wrapping must not weaken the error handling pkm relies on."""

    def test_a_failing_command_still_aborts_the_hook(self):
        body = ('    echo FIRST\n'
                '    false\n'
                '    echo SECOND')
        r = _run(hookseal.render_script("post_install", body, "p", "1"))
        self.assertNotEqual(r.returncode, 0,
                            "a failing command inside the body did not fail "
                            "the hook — set -e stopped governing")
        self.assertIn("FIRST", r.stdout)
        self.assertNotIn("SECOND", r.stdout)

    def test_the_emitted_script_still_declares_set_e(self):
        script = hookseal.render_script("post_install", "    true", "p", "1")
        self.assertRegex(script, r"(?m)^set -e$")


class ArgumentsAndProvenanceTest(unittest.TestCase):
    """Details a wrapper can quietly break."""

    def test_positional_arguments_reach_the_body(self):
        """pkm passes no arguments today, but the installer's hook layer may.

        A wrapper that invoked the function bare would silently drop them.
        """
        body = '    echo "args:$*"'
        r = _run(hookseal.render_script("post_install", body, "p", "1"),
                 args=["alpha", "beta"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("args:alpha beta", r.stdout)

    def test_the_environment_still_reaches_the_body(self):
        body = '    echo "root:${PKM_PACKAGE_ROOT:-unset}"'
        r = _run(hookseal.render_script("post_install", body, "p", "1"),
                 env={"PKM_PACKAGE_ROOT": "/target"})
        self.assertIn("root:/target", r.stdout)

    def test_the_provenance_header_survives(self):
        script = hookseal.render_script("post_install", "    true",
                                        "intergenos-base-files", "1.0-1")
        self.assertIn("Generated by igos-build/hookseal.py", script)
        self.assertIn("package: intergenos-base-files-1.0-1", script)

    def test_the_body_text_is_not_rewritten(self):
        """The seal stays a textual operation.

        The wrapper is allowed to add lines around the body. It is not allowed
        to edit the body, because then the sealed hook would stop being what
        the recipe says and reviewing the recipe would stop being enough.
        """
        body = ('    # a comment with a }brace and a $VAR\n'
                '    echo "untouched"')
        script = hookseal.render_script("post_install", body, "p", "1")
        self.assertIn(body, script)


class EveryEventIsWrappedTest(unittest.TestCase):
    """All six lifecycle events go through the same emitter."""

    def test_each_lifecycle_event_survives_a_bare_return(self):
        for event in hookseal.LIFECYCLE_EVENTS:
            with self.subTest(event=event):
                r = _run(hookseal.render_script(event, "    return 0",
                                                "p", "1"))
                self.assertEqual(r.returncode, 0,
                                 f"{event} sealed body exited "
                                 f"{r.returncode}: {r.stderr}")


class SealedOnDiskRunsTest(unittest.TestCase):
    """End to end through seal_into_staging, executing what lands on disk."""

    def test_the_file_written_into_the_archive_runs(self):
        with tempfile.TemporaryDirectory(prefix="hookseal-staging-") as tmp:
            tmp = Path(tmp)
            build_sh = tmp / "build.sh"
            build_sh.write_text(
                "#!/bin/bash\n"
                "post_install() {\n"
                "    return 0\n"
                "}\n")
            staging = tmp / "staging"
            staging.mkdir()
            sealed = hookseal.seal_into_staging(
                staging, build_sh, "p", "1")
            self.assertEqual(sealed, ["post_install"])
            script_path = staging / hookseal.SCRIPTS_DIR / "post_install.sh"
            self.assertTrue(script_path.is_file())
            self.assertTrue(os.access(script_path, os.X_OK))
            self.assertTrue(
                stat.S_IMODE(script_path.stat().st_mode) & 0o111)
            r = subprocess.run(["bash", "-e", str(script_path)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0,
                             f"the sealed file on disk exited {r.returncode}: "
                             f"{r.stderr}")


class CorpusSweepTest(unittest.TestCase):
    """Every lifecycle function in the tree, sealed and checked STRUCTURALLY.

    THE SWEEP DOES NOT EXECUTE RECIPE BODIES, AND THAT IS A HARD RULE.
    The first version of this sweep did exactly that — rendered each real
    recipe's lifecycle body and ran it — on the reasoning that only execution
    can see a run-time-only error. Running it proved why that is wrong: recipe
    bodies do real work on the machine running the tests. Within two minutes it
    had reached recipes that call `systemctl` and raised four polkit
    authentication prompts in the live desktop session (measured 2026-08-06
    13:47:16 through 13:48:20; all four timed out unauthenticated, and nothing
    was written, because the suite runs unprivileged). Had it run as root it
    would have been enabling units and installing files on the test machine.
    A test that can demand a human, or that mutates the system it is measuring,
    is a defect in the test.

    So the property is checked the way it can be checked safely: STRUCTURALLY.
    "Is `return` legal here" is not a question about what the body does, it is a
    question about where the body sits. A body inside a function may use
    `return` and `local`; a body at top level may not. Proving the emitted
    script puts every body inside a function settles it for every recipe at
    once, without running a single line of anyone's hook.

    Execution of the wrapper's BEHAVIOUR is covered above, against small
    synthetic bodies this file owns — those are safe to run because this file
    wrote them.
    """

    _SENTINEL = "__IGOS_HOOKSEAL_BODY_SENTINEL__"

    def _recipes(self):
        for build_sh in sorted((_REPO_ROOT / "packages").rglob("build.sh")):
            yield build_sh

    def _function_span(self, event):
        """The (start, end) character offsets of the wrapper's function body.

        Located by rendering a sentinel body and finding where it lands, so the
        span is derived from the emitter itself rather than from a hard-coded
        assumption about how the wrapper is written. If render_script's shape
        changes, this follows it.
        """
        script = hookseal.render_script(event, self._SENTINEL, "probe", "0")
        idx = script.index(self._SENTINEL)
        opener = script.rfind("{", 0, idx)
        closer = script.find("\n}", idx)
        self.assertNotEqual(opener, -1,
                            f"the sealed {event} script has no function opener "
                            f"before the body — the body is at top level")
        self.assertNotEqual(closer, -1,
                            f"the sealed {event} script has no function closer "
                            f"after the body")
        return opener, closer

    def test_the_emitted_script_puts_the_body_inside_a_function(self):
        for event in hookseal.LIFECYCLE_EVENTS:
            with self.subTest(event=event):
                self._function_span(event)

    def test_the_emitted_script_invokes_that_function(self):
        """A defined-but-never-called function is a silently skipped hook.

        Wrapping without invoking would make every sealed hook exit 0 having
        done nothing, which no other test in this file distinguishes from
        success as clearly as this one does.
        """
        script = hookseal.render_script("post_install", "    echo RAN",
                                        "probe", "0")
        r = _run(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("RAN", r.stdout,
                      "the wrapper defined the function but never called it")

    def test_every_real_lifecycle_body_lands_inside_the_function(self):
        """The corpus, checked without running any of it."""
        checked = 0
        recipes_with_hooks = 0
        offenders = []
        for build_sh in self._recipes():
            try:
                text = build_sh.read_text(errors="surrogateescape")
            except OSError:
                continue
            had_hook = False
            for event in hookseal.LIFECYCLE_EVENTS:
                try:
                    body = hookseal.extract_function(text, event)
                except hookseal.SealError:
                    # A recipe the sealer refuses never reaches an archive, so
                    # it cannot fail this way on a user machine. The refusal
                    # itself is covered by the extraction tests.
                    continue
                if body is None:
                    continue
                checked += 1
                had_hook = True
                script = hookseal.render_script(event, body, "probe", "0")
                where = script.find(body)
                if where == -1:
                    offenders.append(
                        f"{build_sh.relative_to(_REPO_ROOT)}::{event}: the "
                        f"emitted script does not contain the body verbatim")
                    continue
                start, end = self._function_span(event)
                if not (start < where and where + len(body) < end + len(body)):
                    offenders.append(
                        f"{build_sh.relative_to(_REPO_ROOT)}::{event}: body "
                        f"is not inside the wrapper function")
            if had_hook:
                recipes_with_hooks += 1
        self.assertGreater(checked, 0,
                           "the sweep found no lifecycle functions at all — "
                           "it is not measuring the population it claims to")
        self.assertEqual(offenders, [],
                         "sealed bodies not inside a function:\n  "
                         + "\n  ".join(offenders))
        print(f"\n[corpus sweep] {checked} lifecycle bodies across "
              f"{recipes_with_hooks} recipes, all inside the wrapper function")

    def test_the_recipes_that_actually_use_the_construct_are_named(self):
        """Name the population rather than assert a bare zero.

        A sweep that only ever reports "no offenders" cannot be distinguished
        from a sweep that is looking at nothing. This one lists the recipes
        whose bodies really do contain a function-context construct, so the
        count is visible and a future change that empties it is noticeable.
        """
        users = []
        for build_sh in self._recipes():
            try:
                text = build_sh.read_text(errors="surrogateescape")
            except OSError:
                continue
            for event in hookseal.LIFECYCLE_EVENTS:
                try:
                    body = hookseal.extract_function(text, event)
                except hookseal.SealError:
                    continue
                if body is None:
                    continue
                for line in body.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if re.match(r"^(return|local)\b", stripped):
                        users.append(
                            f"{build_sh.relative_to(_REPO_ROOT)}::{event}: "
                            f"{stripped}")
                        break
        print(f"\n[corpus sweep] {len(users)} lifecycle bodies use a "
              f"function-context construct:")
        for u in users:
            print(f"  {u}")
        self.assertGreater(
            len(users), 0,
            "no lifecycle body in the tree uses `return` or `local` any more, "
            "so this sweep no longer covers a live shape — either the fix is "
            "unnecessary or the sweep is looking in the wrong place")


if __name__ == "__main__":
    sys.exit(unittest.main())
