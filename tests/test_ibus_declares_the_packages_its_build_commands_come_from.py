# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""ibus names the packages its build commands come from among its build
dependencies.

packages/desktop/ibus/build.sh runs unzip twice: configure() unpacks the
Unicode Character Database onto the live tree for ibus's configure to read,
and do_install() stages the same zip into DESTDIR. Its package.yml named
libarchive, and not unzip, among its build dependencies; while libarchive
installed a /usr/bin/unzip link to its bsdunzip, that link was what put an
unzip command in the build root. libarchive no longer installs it, and the
command comes only from the unzip package, so ibus names unzip beside
libarchive, which it still links against.

The test reads the two files. Every command the recipe's build.sh runs, in
every function it defines (as bash itself prints them, declare -f), must
come from a package the recipe names among its build dependencies, unless
it is a shell builtin or keyword, a script of the source tree itself
(./configure), or a program the build environment always provides (the
list the undeclared-dependency preflight keeps,
scripts/preflight-undeclared-deps.py). A package ships a command when its
package.yml lists /usr/bin/<command> among its verify_paths. A command no
package lists fails the test, and so does a shell construct the reader does
not follow, so nothing passes unread.
"""
import importlib.util
import os
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
RECIPE = REPO / "packages" / "desktop" / "ibus"

_spec = importlib.util.spec_from_file_location(
    "preflight_undeclared_deps",
    REPO / "scripts" / "preflight-undeclared-deps.py")
_preflight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_preflight)
BUILD_ENVIRONMENT = _preflight.TOOLCHAIN_PROVIDED_PROGRAMS

# After one of these the next word is a command.
_LIST_OPERATORS = {";", "&&", "||", "|", "&", "(", ")", "{", "}"}
_COMMAND_FOLLOWS = {"if", "then", "else", "elif", "do", "while", "until",
                    "!", "time"}
# Constructs whose words are not commands and arguments alone.
_UNFOLLOWED = {"for", "case", "select", "coproc", "[[", "function"}


def _bash_reading(build_sh):
    """The functions build_sh defines, as bash prints them, and bash's own
    builtins and keywords."""
    script = ('. "$1" >/dev/null; declare -f; echo @@builtins; compgen -b; '
              'echo @@keywords; compgen -k')
    out = subprocess.run(
        ["bash", "--norc", "--noprofile", "-c", script, "bash", str(build_sh)],
        env={"PATH": os.environ["PATH"]}, capture_output=True, text=True,
        timeout=30, check=True).stdout
    functions, rest = out.split("@@builtins\n")
    builtins, keywords = rest.split("@@keywords\n")
    return functions, set(builtins.split()), set(keywords.split())


def _command_words(functions, keywords):
    """The word in command position of every simple command, in order."""
    found = []
    for line in functions.splitlines():
        text = line.strip()
        if not text or text in ("{", "}") or text.endswith("()"):
            continue
        if "$(" in text or "`" in text or "<<" in text:
            raise AssertionError(f"a construct this reader does not follow: {text}")
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        expect_command = True
        for token in lexer:
            if token in _LIST_OPERATORS:
                expect_command = True
            elif not expect_command:
                continue
            elif token in _UNFOLLOWED:
                raise AssertionError(f"a construct this reader does not follow: {text}")
            elif token in keywords:
                expect_command = token in _COMMAND_FOLLOWS
            elif "=" in token and token.split("=", 1)[0].isidentifier():
                continue
            else:
                if "$" in token:
                    raise AssertionError(f"a command named by a variable: {text}")
                found.append(token)
                expect_command = False
    return found


def _from_other_packages(words, builtins):
    return [w for w in dict.fromkeys(words)
            if w not in builtins and w not in BUILD_ENVIRONMENT
            and not w.startswith("./")]


def _shippers():
    """Each path a package.yml lists among its verify_paths -> the packages
    that list it."""
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    by_path = {}
    for yml in sorted((REPO / "packages").glob("*/*/package.yml")):
        data = yaml.load(yml.read_text(), Loader=loader)
        for path in data.get("verify_paths") or []:
            by_path.setdefault(path, set()).add(data["name"])
    return by_path


def test_every_build_command_from_another_package_is_a_build_dependency():
    recipe = yaml.safe_load((RECIPE / "package.yml").read_text())
    declared = set(recipe["dependencies"]["build"])
    functions, builtins, keywords = _bash_reading(RECIPE / "build.sh")
    shippers = _shippers()
    wrong = []
    for word in _from_other_packages(_command_words(functions, keywords),
                                     builtins):
        path = word if word.startswith("/") else f"/usr/bin/{word}"
        owners = shippers.get(path, set()) - {recipe["name"]}
        if not owners:
            wrong.append(f"{word}: no package lists {path} among its verify_paths")
        elif not owners & declared:
            wrong.append(f"{word}: comes from {', '.join(sorted(owners))}, "
                         f"which the build dependencies do not name")
    assert not wrong, "\n".join(wrong)


def test_the_reader_finds_the_commands_the_recipe_runs():
    """The reader is shown to find what build.sh runs, so a pass above is
    not a pass over nothing: unzip in configure and do_install, and
    glib-compile-schemas in post_install."""
    functions, builtins, keywords = _bash_reading(RECIPE / "build.sh")
    words = _command_words(functions, keywords)
    assert words.count("unzip") == 2
    assert {"unzip", "glib-compile-schemas"} <= set(
        _from_other_packages(words, builtins))
    assert {"./autogen.sh", "./configure", "make", "sed"} <= set(words)


def test_a_construct_the_reader_does_not_follow_fails_loudly(tmp_path):
    build_sh = tmp_path / "build.sh"
    build_sh.write_text("build() {\n    make -j$(nproc)\n}\n")
    functions, _builtins, keywords = _bash_reading(build_sh)
    with pytest.raises(AssertionError, match="does not follow"):
        _command_words(functions, keywords)
