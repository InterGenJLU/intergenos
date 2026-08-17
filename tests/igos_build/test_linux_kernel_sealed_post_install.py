#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The kernel recipe's post_install must not depend on its own build preamble.

WHAT WENT WRONG. `packages/core/linux-kernel/build.sh` defined

    post_install() {
        set -e
        depmod "${KVER}"
    }

and KVER is computed at the TOP of that recipe, from the recipe's own
package.yml or from a staged module tree. When the recipe is sourced, KVER is
set and the call is correct. When hookseal seals the FUNCTION BODY into
`.scripts/post_install.sh` — which is the path an archive install takes — the
preamble does not come with it, KVER is unset, and the call becomes

    $ depmod ""
    depmod: ERROR: Bad version passed
    rc=1

which is the exact error the package was marked degraded with. Reproduced
2026-08-06 against the real sealer.

WHAT WAS DECIDED, 2026-08-06: derive the release ON THE MACHINE rather than drop
the call. Both were on the table, and dropping it is defensible — pkm carries a
canonical depmod hook (pkm/hooks.py `_depmod_cmd`) that derives the release from
the installed module paths and fires whenever a package ships
`lib/modules/<kver>/`. It was not chosen for two reasons. The recipe is invoked
in three contexts and pkm's hook covers only one of them, so dropping the call
would leave the build-chroot invocation with no depmod at all. And a recipe that
silently relies on another component's path-matching to do the thing the recipe
says it does is a dependency nobody reviewing the recipe can see; if that
pattern ever stops matching, no depmod runs and the failure is a stale
modules.dep discovered at the next boot. Deriving in the recipe keeps the stated
intent true in every context, and depmod is idempotent, so the overlap with
pkm's canonical hook costs nothing.

SAFETY. These tests execute the kernel recipe's post_install body. That is only
acceptable because the body is confined here: `depmod` is stubbed onto PATH so
the real one cannot run, the module tree is fabricated in a temporary directory,
and PKM_PACKAGE_ROOT points at that directory. A gate below asserts the body
invokes no external command other than depmod, so if the recipe ever grows one
the test refuses to run it rather than executing it against this machine.
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "igos-build"))

import hookseal  # noqa: E402

_RECIPE = _REPO_ROOT / "packages" / "core" / "linux-kernel" / "build.sh"

# Shell builtins and control words the body may use freely; anything else that
# looks like a command at the start of a line is an external invocation.
_ALLOWED_WORDS = {
    "if", "then", "elif", "else", "fi", "for", "do", "done", "while", "until",
    "case", "esac", "local", "return", "echo", "set", "unset", "shift", "true",
    "false", "printf", "read", "continue", "break", "declare", "export",
    "depmod",
}


class BodySafetyGateTest(unittest.TestCase):
    """Refuse to execute a body that does more than this test sandboxes."""

    def test_the_body_invokes_nothing_but_depmod(self):
        body = hookseal.extract_function(_RECIPE.read_text(), "post_install")
        self.assertIsNotNone(body, "the recipe declares no post_install()")
        offenders = []
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            first = re.split(r"[\s;|&(]", stripped, maxsplit=1)[0]
            first = first.lstrip("!")
            if not first or first.startswith(("[", "]", "}", "{", '"', "'",
                                              "$", "-")):
                continue
            if "=" in first and not first.startswith("="):
                continue  # a variable assignment
            if first in _ALLOWED_WORDS:
                continue
            offenders.append(stripped)
        self.assertEqual(
            offenders, [],
            "the kernel post_install now runs commands this test does not "
            "sandbox; it will not be executed here. Extend the sandbox "
            "deliberately or cover the new behaviour structurally:\n  "
            + "\n  ".join(offenders))


class _SealedRun:
    """Run the sealed kernel post_install against a fabricated root."""

    def __init__(self, testcase):
        self.tc = testcase

    def run(self, module_dirs, kver_env=None, package_root=True):
        body = hookseal.extract_function(_RECIPE.read_text(), "post_install")
        script_text = hookseal.render_script(
            "post_install", body, "linux-kernel", "6.18.10-10")
        tmp = tempfile.mkdtemp(prefix="kernel-postinstall-")
        self.tc.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        root = Path(tmp) / "root"
        for name in module_dirs:
            (root / "usr" / "lib" / "modules" / name).mkdir(parents=True)
        root.mkdir(parents=True, exist_ok=True)

        # A depmod stub that records its argv and always succeeds. The real
        # depmod is never reachable: PATH is replaced, not prepended to.
        bindir = Path(tmp) / "bin"
        bindir.mkdir()
        record = Path(tmp) / "depmod.argv"
        stub = bindir / "depmod"
        stub.write_text(
            "#!/bin/bash\n"
            f'printf "%s\\n" "$*" >> {record}\n'
            "exit 0\n")
        stub.chmod(0o755)

        script = Path(tmp) / "post_install.sh"
        script.write_text(script_text)
        script.chmod(0o755)

        env = {
            "PATH": f"{bindir}:/usr/bin:/bin",
            "HOME": tmp,
        }
        if package_root:
            env["PKM_PACKAGE_ROOT"] = str(root)
        if kver_env is not None:
            env["KVER"] = kver_env
        proc = subprocess.run(["bash", "-e", str(script)],
                              capture_output=True, text=True, env=env)
        argv = record.read_text().splitlines() if record.exists() else []
        return proc, argv


class DerivationTest(unittest.TestCase):
    """With no KVER in the environment — the sealed-archive shape."""

    def test_it_derives_the_release_from_the_staged_module_tree(self):
        proc, argv = _SealedRun(self).run(["6.18.10-igos-10"])
        self.assertEqual(proc.returncode, 0,
                         f"exited {proc.returncode}\nstderr: {proc.stderr}")
        self.assertEqual(len(argv), 1,
                         f"expected exactly one depmod call, got {argv}")
        self.assertIn("6.18.10-igos-10", argv[0],
                      f"depmod was not given the staged release: {argv[0]!r}")

    def test_it_never_calls_depmod_with_an_empty_version(self):
        """The precise defect: `depmod ""` -> 'Bad version passed'."""
        proc, argv = _SealedRun(self).run(["6.18.10-igos-10"])
        for call in argv:
            self.assertNotEqual(
                call.strip(), "",
                "depmod was called with an empty version — this is the "
                "'Bad version passed' failure the fix exists for")

    def test_an_unresolvable_release_fails_loudly_instead_of_calling_depmod(self):
        """No module tree at all. Silence here would be the worse outcome."""
        proc, argv = _SealedRun(self).run([])
        self.assertNotEqual(proc.returncode, 0,
                            "an unresolvable kernel release exited 0")
        self.assertEqual(argv, [],
                         f"depmod was called anyway with {argv}")
        self.assertTrue(proc.stderr.strip(),
                        "it failed without saying why")

    def test_two_staged_trees_refuse_rather_than_guess(self):
        """Mirrors the recipe preamble's own rule for the same ambiguity."""
        proc, argv = _SealedRun(self).run(
            ["6.18.10-igos-9", "6.18.10-igos-10"])
        self.assertNotEqual(proc.returncode, 0,
                            "two staged module trees did not refuse")
        self.assertEqual(argv, [],
                         f"depmod was called on a guess: {argv}")

    def test_it_scopes_depmod_to_the_package_root(self):
        """A non-'/' root must reach depmod, or the host's tree is rebuilt."""
        proc, argv = _SealedRun(self).run(["6.18.10-igos-10"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("-b", argv[0],
                      f"depmod was not scoped to the package root: {argv[0]!r}")


class PreambleContextTest(unittest.TestCase):
    """With KVER set — the sourced-recipe shape, which must not regress."""

    def test_an_explicit_kver_is_honoured(self):
        proc, argv = _SealedRun(self).run(["6.18.10-igos-10"],
                                          kver_env="6.18.10-igos-10")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("6.18.10-igos-10", argv[0])

    def test_an_explicit_kver_wins_without_needing_a_staged_tree(self):
        """The build chroot calls this with KVER set and its own module tree.

        The derivation must not become a hard requirement in the context that
        already has the answer.
        """
        proc, argv = _SealedRun(self).run([], kver_env="6.18.10-igos-10")
        self.assertEqual(proc.returncode, 0,
                         f"exited {proc.returncode}\nstderr: {proc.stderr}")
        self.assertIn("6.18.10-igos-10", argv[0])


class RecipeTextTest(unittest.TestCase):
    """Pins read off the recipe, to catch a regression at review time."""

    def test_the_body_no_longer_hard_depends_on_the_build_variable(self):
        body = hookseal.extract_function(_RECIPE.read_text(), "post_install")
        code = "\n".join(ln for ln in body.splitlines()
                         if not ln.strip().startswith("#"))
        self.assertNotRegex(
            code, r'depmod\s+"\$\{KVER\}"',
            'post_install still calls depmod "${KVER}" — an unset KVER makes '
            'that `depmod ""`')
        # Referencing KVER with a default is fine and is how the sourced
        # context is honoured; depending on it without one is not.
        for match in re.finditer(r"\$\{KVER([^}]*)\}", code):
            self.assertTrue(
                match.group(1).startswith(":-")
                or match.group(1).startswith("-"),
                f"KVER is referenced without a default: {match.group(0)}")


if __name__ == "__main__":
    sys.exit(unittest.main())
