#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A download helper records an extension it did NOT install.

The VS Code extension step of the claude-code and codex helpers can end five
ways that leave the pinned extension absent: the gallery download fails, the
downloaded bytes fail the pinned sha256 check, the editor refuses the install,
the editor refuses because the install ran as root with no invoking user, or no
editor is present at all.  Each of those prints a message, and before this
change none of them left anything in the manifest, so the package manager's
operation log recorded a plain success for an install that had silently dropped
a pinned, sha256-verified component.

These tests drive the REAL helper body out of each recipe with a stand-in npm
and a stand-in gallery, and read the manifest the helper commits — the same
file pkm copies its operation log from.  They assert the record is present with
the right reason class, that the successful path still records exactly the line
it always recorded, and that the helper's exit status is unchanged in every
case: a gallery outage must not fail an otherwise good install.
"""

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
HELPER_LIB = REPO / "packages" / "core" / "intergenos-helper-lib" / "helper-lib.sh"

# The helper reads the installed executable's name out of the module's own
# package.json and only requires that exactly one is declared, so the stand-in
# registry below declares a neutral name rather than a vendor one.
RECIPES = {
    "claude-code": {
        "dir": REPO / "packages" / "extra" / "claude-code",
        "helper": "igos-install-claude-code",
        "npm_package": "@anthropic-ai/claude-code",
        "bin": "cli",
        "extension": "anthropic.claude-code",
        "version_var": "CLAUDE_CODE_VSIX_VERSION",
        "sha_var": "CLAUDE_CODE_VSIX_SHA256",
        "acceptance": "claude-code-1.0-accepted.json",
        "functions": "claude-code-helper-functions.sh",
    },
    "codex": {
        "dir": REPO / "packages" / "extra" / "codex",
        "helper": "igos-install-codex",
        "npm_package": "@openai/codex",
        "bin": "cli",
        "extension": "openai.chatgpt (Codex)",
        "version_var": "CODEX_VSIX_VERSION",
        "sha_var": "CODEX_VSIX_SHA256",
        "acceptance": "codex-1.0-accepted.json",
        "functions": None,
    },
}

REASON_CLASSES = (
    "download-failed",
    "integrity-refused",
    "install-failed",
    "root-refused",
    "vscode-absent",
)


def _helper_body(build_sh: Path, helper_name: str) -> str:
    source = build_sh.read_text(encoding="utf-8")
    match = re.search(
        r"cat > \"\$\{DESTDIR\}/usr/bin/" + re.escape(helper_name) + r"\" "
        r"<< 'HELPEREOF'\n(?P<body>.*?)\nHELPEREOF",
        source,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"{build_sh} does not contain the {helper_name} heredoc")
    return match.group("body") + "\n"


def _pinned_version(build_sh: Path, var: str) -> str:
    match = re.search(rf'^{re.escape(var)}="([^"]+)"', build_sh.read_text(encoding="utf-8"), re.M)
    if match is None:
        raise AssertionError(f"{build_sh} does not pin {var}")
    return match.group(1)


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class _HelperHarness:
    """Runs a recipe's real helper body offline, once, and keeps its manifest."""

    def __init__(self, root: Path, recipe_key: str, case: str):
        self.root = root
        self.recipe = RECIPES[recipe_key]
        self.case = case
        self.bin_dir = root / "bin"
        self.bin_dir.mkdir()
        self.prefix = root / "npmprefix"
        self.manifests = root / "manifests"
        self.manifests.mkdir()
        self.legal = root / "legal"
        self.legal.mkdir()
        (self.legal / self.recipe["acceptance"]).write_text("{}\n", encoding="utf-8")
        self.vsix_source = root / "gallery.vsix"
        self.vsix_source.write_bytes(b"pinned gallery bytes\n")
        self.vsix_sha256 = hashlib.sha256(self.vsix_source.read_bytes()).hexdigest()

        self._write_npm()
        self._write_gallery()
        self._write_editor()
        self._write_helper()

    # -- stand-ins ---------------------------------------------------------
    def _write_npm(self) -> None:
        pkg = self.recipe["npm_package"]
        binary = self.recipe["bin"]
        _executable(
            self.bin_dir / "npm",
            "#!/bin/sh\n"
            'case "$1 $2" in\n'
            '  "prefix -g")\n'
            f'    printf "%s\\n" "{self.prefix}"\n'
            "    exit 0 ;;\n"
            "esac\n"
            'case "$1" in\n'
            "  audit) exit 0 ;;\n"
            "  view)\n"
            "    printf '%s\\n' \"$IGOS_TEST_NPM_LATEST\"\n"
            "    exit 0 ;;\n"
            "  install)\n"
            '    for a in "$@"; do\n'
            '      case "$a" in\n'
            f"        {pkg}@*)\n"
            '          v=${a##*@}\n'
            f'          mod="{self.prefix}/lib/node_modules/{pkg}"\n'
            '          mkdir -p "$mod"\n'
            f'          printf \'{{"name":"{pkg}","version":"%s","bin":{{"{binary}":"cli.js"}}}}\\n\' "$v" > "$mod/package.json"\n'
            '          printf \'#!/bin/sh\\n\' > "$mod/cli.js"\n'
            f'          mkdir -p "{self.prefix}/bin"\n'
            f'          printf \'#!/bin/sh\\nprintf "%%s (stand-in)\\\\n" "\'"$v"\'"\\n\' > "{self.prefix}/bin/{binary}"\n'
            f'          chmod 0755 "{self.prefix}/bin/{binary}" "$mod/cli.js"\n'
            "          ;;\n"
            "      esac\n"
            "    done\n"
            "    exit 0 ;;\n"
            "esac\n"
            "exit 0\n",
        )

    def _write_gallery(self) -> None:
        """Stands in for curl against the gallery, per case."""
        if self.case == "download-failed":
            body = (
                "#!/bin/sh\n"
                "# the failure seen on a real run: reset at zero bytes\n"
                'echo "curl: (35) Recv failure: Connection reset by peer" >&2\n'
                "exit 35\n"
            )
        elif self.case == "integrity-refused":
            body = (
                "#!/bin/sh\n"
                'out=""; prev=""\n'
                'for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done\n'
                '[ -n "$out" ] && printf \'not the pinned bytes\\n\' > "$out"\n'
                "exit 0\n"
            )
        else:
            body = (
                "#!/bin/sh\n"
                'out=""; prev=""\n'
                'for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done\n'
                f'[ -n "$out" ] && cp "{self.vsix_source}" "$out"\n'
                "exit 0\n"
            )
        _executable(self.bin_dir / "curl", body)

    def _write_editor(self) -> None:
        if self.case == "vscode-absent":
            # A PATH that is the real /usr/bin with exactly one name missing:
            # the editor. Linking every other name keeps the run failing for
            # the case under test rather than for a tool the list forgot.
            self.nocode = self.root / "nocode"
            self.nocode.mkdir()
            for entry in Path("/usr/bin").iterdir():
                if entry.name in ("code", "code-oss"):
                    continue
                try:
                    (self.nocode / entry.name).symlink_to(entry)
                except OSError:
                    pass
            return
        if self.case == "install-failed":
            body = (
                "#!/bin/sh\n"
                'echo "Unable to install extension" >&2\n'
                "exit 7\n"
            )
        elif self.case == "root-refused":
            # VS Code's own super-user guard: a nonzero exit when run as root.
            body = (
                "#!/bin/sh\n"
                'echo "You are trying to start Visual Studio Code as a super user" >&2\n'
                "exit 1\n"
            )
        else:
            body = "#!/bin/sh\necho \"Extension installed\"\nexit 0\n"
        _executable(self.bin_dir / "code", body)
        # the helper drops privilege with `sudo -u <user> -H`; stand in for
        # that so the test needs no privilege of its own.
        _executable(
            self.bin_dir / "sudo",
            "#!/bin/sh\n"
            'while [ $# -gt 0 ]; do\n'
            '  case "$1" in\n'
            '    -u) shift 2 ;;\n'
            '    -H|-E) shift ;;\n'
            "    *) break ;;\n"
            "  esac\n"
            "done\n"
            'exec "$@"\n',
        )

    def _write_helper(self) -> None:
        body = _helper_body(self.recipe["dir"] / "build.sh", self.recipe["helper"])
        if self.case != "integrity-refused":
            # The stand-in gallery cannot serve bytes that hash to the real
            # pin, so for every case that must get PAST the integrity gate the
            # pin is set to the stand-in's own digest. The gate itself is
            # proven by the integrity-refused case, which leaves the real pin
            # in place and serves bytes that do not match it.
            body = re.sub(
                rf'^{re.escape(self.recipe["sha_var"])}="[0-9a-f]+"',
                f'{self.recipe["sha_var"]}="{self.vsix_sha256}"',
                body,
                count=1,
                flags=re.M,
            )
        body = body.replace(
            "source /usr/share/igos/helpers/helper-lib.sh",
            f"source {HELPER_LIB}",
        )
        if self.recipe["functions"]:
            body = body.replace(
                "source /usr/libexec/igos-install-claude-code-functions.sh",
                f"source {self.recipe['dir'] / self.recipe['functions']}",
            )
        # The helper refuses a non-root run because the real install needs
        # root; the test has no privilege, so the guard reads a stand-in.
        body = body.replace('if [ "$(id -u)" -ne 0 ]; then', "if false; then")
        body = body.replace(
            'ACCEPTANCE_DIR="/var/lib/intergen/legal"',
            f'ACCEPTANCE_DIR="{self.legal}"',
        )
        body = body.replace(
            'ACCEPTANCE_FILE="$ACCEPTANCE_DIR/', 'ACCEPTANCE_FILE="$ACCEPTANCE_DIR/'
        )
        # `id -u` is also how the helper tells the root-refused branch from a
        # plain install failure; make it answer the case under test.
        uid = "0" if self.case == "root-refused" else "1000"
        _executable(self.bin_dir / "id", f'#!/bin/sh\n[ "$1" = "-u" ] && {{ echo {uid}; exit 0; }}\nexec /usr/bin/id "$@"\n')
        self.helper = self.root / self.recipe["helper"]
        self.helper.write_text(body, encoding="utf-8")
        self.helper.chmod(0o755)

    # -- the run -----------------------------------------------------------
    def run(self) -> subprocess.CompletedProcess:
        path = f"{self.bin_dir}:/usr/bin:/bin"
        if self.case == "vscode-absent":
            path = f"{self.bin_dir}:{self.nocode}"
        env = {
            "PATH": path,
            "HOME": str(self.root),
            "IGOS_HELPER_MANIFEST_DIR": str(self.manifests),
            "IGOS_TEST_NPM_LATEST": "0.0.0",
        }
        if self.case != "root-refused":
            env["SUDO_USER"] = "someone"
        return subprocess.run(
            ["/bin/bash", str(self.helper)],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )

    def actions(self) -> list:
        manifests = sorted(self.manifests.glob("*.manifest"))
        if not manifests:
            return []
        data = json.loads(manifests[0].read_text(encoding="utf-8"))
        return list(data.get("post_install_actions_log", []))


class SkippedExtensionIsRecorded(unittest.TestCase):
    """Every path that leaves the extension absent records that it did."""

    def _run(self, recipe_key: str, case: str):
        with tempfile.TemporaryDirectory() as td:
            harness = _HelperHarness(Path(td), recipe_key, case)
            result = harness.run()
            return result, harness.actions()

    def _assert_records_reason(self, recipe_key: str, case: str):
        recipe = RECIPES[recipe_key]
        version = _pinned_version(recipe["dir"] / "build.sh", recipe["version_var"])
        result, actions = self._run(recipe_key, case)
        self.assertEqual(
            result.returncode,
            0,
            f"{recipe_key}/{case} changed the helper's exit status:\n"
            f"{result.stdout}\n{result.stderr}",
        )
        skipped = [a for a in actions if "NOT installed" in a]
        self.assertEqual(
            len(skipped),
            1,
            f"{recipe_key}/{case} recorded {len(skipped)} skip lines: {actions}",
        )
        line = skipped[0]
        self.assertIn(recipe["extension"], line)
        self.assertIn(version, line)
        self.assertIn(f"reason: {case}", line)
        self.assertFalse(
            [a for a in actions if a.endswith("installed (per-user; not pkm-tracked)")],
            f"{recipe_key}/{case} also claimed the extension was installed: {actions}",
        )

    def test_claude_code_records_every_reason_class(self):
        for case in REASON_CLASSES:
            with self.subTest(case=case):
                self._assert_records_reason("claude-code", case)

    def test_codex_records_every_reason_class(self):
        for case in REASON_CLASSES:
            with self.subTest(case=case):
                self._assert_records_reason("codex", case)

    def test_a_successful_install_still_records_exactly_the_old_line(self):
        for recipe_key in RECIPES:
            with self.subTest(recipe=recipe_key):
                result, actions = self._run(recipe_key, "success")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                installed = [a for a in actions if "installed (per-user" in a]
                self.assertEqual(
                    installed,
                    [
                        f"VS Code extension {RECIPES[recipe_key]['extension']} "
                        "installed (per-user; not pkm-tracked)"
                    ],
                )
                self.assertFalse([a for a in actions if "NOT installed" in a])

    def test_the_reason_vocabulary_is_fixed_and_complete_in_both_recipes(self):
        for recipe_key, recipe in RECIPES.items():
            with self.subTest(recipe=recipe_key):
                source = (recipe["dir"] / "build.sh").read_text(encoding="utf-8")
                assigned = set(re.findall(r'ext_skip_reason="([a-z-]+)"', source))
                assigned.discard("")
                self.assertEqual(assigned, set(REASON_CLASSES))


if __name__ == "__main__":
    unittest.main()
