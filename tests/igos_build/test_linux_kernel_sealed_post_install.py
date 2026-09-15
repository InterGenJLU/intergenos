#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Both kernel recipes seal a post_install independent of the build preamble.

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
The complete pass-2 recipe is also sourced from a temporary installer bundle.
Those cases expose only its read-only parsers and the depmod stub, with grep
guarded against reading recipes outside the fixture.
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
_PASS2_RECIPE = _RECIPE.parent.parent / "linux-kernel-pass2" / "build.sh"

# Shell builtins and control words the body may use freely; anything else that
# looks like a command at the start of a line is an external invocation.
_ALLOWED_WORDS = {
    "if", "then", "elif", "else", "fi", "for", "do", "done", "while", "until",
    "case", "esac", "local", "return", "echo", "set", "unset", "shift", "true",
    "false", "printf", "read", "continue", "break", "declare", "export",
    "depmod",
}


class _RecipeTest(unittest.TestCase):
    recipe = _RECIPE


class BodySafetyGateTest(_RecipeTest):
    """Refuse to execute a body that does more than this test sandboxes."""

    def test_the_body_invokes_nothing_but_depmod(self):
        body = hookseal.extract_function(self.recipe.read_text(), "post_install")
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

    def run(self, module_dirs, kver_env=None, package_root=True, depmod_rc=0,
            source_recipe=False, recipe_release=10, recipe_version="6.18.10",
            package_version="6.18.10"):
        body = hookseal.extract_function(self.tc.recipe.read_text(), "post_install")
        self.tc.assertIsNotNone(body, "the recipe declares no post_install()")
        tmp = tempfile.mkdtemp(prefix="kernel-postinstall-")
        self.tc.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        root = Path(tmp) / "root"
        self.root = root
        for name in module_dirs:
            (root / "usr" / "lib" / "modules" / name).mkdir(parents=True)
        root.mkdir(parents=True, exist_ok=True)

        # Record depmod's arguments and return the selected status. The real
        # depmod is never reachable: PATH is replaced, not prepended to.
        bindir = Path(tmp) / "bin"
        bindir.mkdir()
        record = Path(tmp) / "depmod.argv"
        stub = bindir / "depmod"
        stub.write_text(
            "#!/bin/bash\n"
            f'printf "%s\\n" "$*" >> {record}\n'
            f"exit {depmod_rc}\n")
        stub.chmod(0o755)

        if source_recipe:
            recipe_dir = Path(tmp) / "core" / self.tc.recipe.parent.name
            recipe_dir.mkdir(parents=True)
            recipe_copy = recipe_dir / "build.sh"
            recipe_copy.write_text(self.tc.recipe.read_text())
            sibling = recipe_dir.parent / "linux-kernel"
            sibling.mkdir(exist_ok=True)
            metadata = f'version: "{recipe_version}"\n'
            if recipe_release is not None:
                metadata += f"release: {recipe_release}\n"
            (sibling / "package.yml").write_text(metadata)
            # Source the actual recipe with only its read-only parsers available.
            # The grep guard refuses any attempt to consult a host recipe.
            for command in ("dirname", "readlink", "awk", "tr"):
                (bindir / command).symlink_to(f"/usr/bin/{command}")
            grep = bindir / "grep"
            grep.write_text(
                '#!/bin/bash\nfor arg in "$@"; do\n'
                '  case "$arg" in /*) [[ "$arg" == "$TEST_FIXTURE_ROOT/"* ]] '
                '|| { echo "outside fixture: $arg" >&2; exit 91; };; esac\n'
                'done\nexec /usr/bin/grep "$@"\n')
            grep.chmod(0o755)
            script_text = f'source "{recipe_copy}"\npost_install\n'

        if source_recipe:
            script = Path(tmp) / "post_install.sh"
            script.write_text(script_text)
        else:
            sealed_root = Path(tmp) / "sealed"
            events = hookseal.seal_into_staging(
                sealed_root, self.tc.recipe, self.tc.recipe.parent.name,
                "6.18.10-10", events=("post_install",))
            self.tc.assertEqual(events, ["post_install"])
            script = sealed_root / ".scripts" / "post_install.sh"

        env = {
            "PATH": str(bindir),
            "HOME": tmp,
            "TEST_FIXTURE_ROOT": tmp,
        }
        if source_recipe:
            env["PKG_VERSION"] = package_version
        if package_root:
            env["PKM_PACKAGE_ROOT"] = str(root)
        if kver_env is not None:
            env["KVER"] = kver_env
        proc = subprocess.run(["/bin/bash", "-e", str(script)],
                              capture_output=True, text=True, env=env)
        argv = record.read_text().splitlines() if record.exists() else []
        return proc, argv


class DerivationTest(_RecipeTest):
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
        runner = _SealedRun(self)
        proc, argv = runner.run(["6.18.10-igos-10"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(argv, [f"-b {runner.root} 6.18.10-igos-10"])

    def test_depmod_failure_reaches_the_sealed_hook_caller(self):
        proc, argv = _SealedRun(self).run(["6.18.10-igos-10"], depmod_rc=23)
        self.assertEqual(proc.returncode, 23, proc.stderr)
        self.assertEqual(len(argv), 1)


class PreambleContextTest(_RecipeTest):
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

    def test_the_default_root_does_not_add_a_staging_prefix(self):
        proc, argv = _SealedRun(self).run(
            [], kver_env="6.18.10-igos-10", package_root=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(argv, ["6.18.10-igos-10"])


class RecipeTextTest(_RecipeTest):
    """Pins read off the recipe, to catch a regression at review time."""

    def test_the_body_no_longer_hard_depends_on_the_build_variable(self):
        body = hookseal.extract_function(self.recipe.read_text(), "post_install")
        self.assertIsNotNone(body, "the recipe declares no post_install()")
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


class Pass2BodySafetyGateTest(BodySafetyGateTest):
    recipe = _PASS2_RECIPE


class Pass2DerivationTest(DerivationTest):
    recipe = _PASS2_RECIPE


class Pass2PreambleContextTest(PreambleContextTest):
    recipe = _PASS2_RECIPE


class Pass2RecipeTextTest(RecipeTextTest):
    recipe = _PASS2_RECIPE


class Pass2SourcedRecipeTest(_RecipeTest):
    """Exercise the complete recipe as Forge does, without a build-tree path."""

    recipe = _PASS2_RECIPE

    def test_sibling_release_is_used_in_the_build_context(self):
        runner = _SealedRun(self)
        proc, argv = runner.run([], source_recipe=True, recipe_release=37)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(argv, [f"-b {runner.root} 6.18.10-igos-37"])

    def test_forge_metadata_without_release_uses_the_installed_tree(self):
        runner = _SealedRun(self)
        proc, argv = runner.run(["6.18.10-igos-37"], source_recipe=True,
                                recipe_release=None)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(argv, [f"-b {runner.root} 6.18.10-igos-37"])

    def test_forge_refuses_absent_ambiguous_or_wrong_version_trees(self):
        for trees in ([], ["6.18.10-igos-36", "6.18.10-igos-37"],
                      ["6.18.11-igos-37"]):
            with self.subTest(trees=trees):
                proc, argv = _SealedRun(self).run(
                    trees, source_recipe=True, recipe_release=None)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(argv, [])
                self.assertIn("FATAL:", proc.stderr)

    def test_sourcing_refuses_missing_or_mismatched_package_version(self):
        for version in ("", "6.18.11"):
            with self.subTest(version=version):
                proc, argv = _SealedRun(self).run(
                    [], source_recipe=True, package_version=version)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(argv, [])
                self.assertIn("FATAL:", proc.stderr)


if __name__ == "__main__":
    sys.exit(unittest.main())
