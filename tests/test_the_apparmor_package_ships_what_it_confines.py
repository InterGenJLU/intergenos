#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The security package ships a profile only for a program the tree installs,
and it ships the tools that can read the policy back.

WHAT THIS COVERS. Two facts measured on an ordinary installed machine on
2026-09-22, both of which this file turns into checks that fail in the tree.

First, a profile was shipped for a program nothing installs. The file
`usr.bin.intergen-mcp` named `/usr/bin/intergen-mcp`, and no recipe anywhere in
this repository puts a binary of that name on a machine. A profile with no
program to attach to is loaded into the kernel at every install, is counted in
every summary of the policy, and confines nothing: it makes the policy look
wider than it is, which is the one thing a mandatory access control policy must
never do.

Second, of the AppArmor userspace tools only the parser was installed, so an
installed machine could not report its own enforce/complain split at all. The
same machine read 169 profiles loaded, 68 in enforce and 101 in complain with
the tool built from the same upstream tarball the recipe already uses, and
could read nothing without it. A policy nobody can read back is a policy nobody
can check.

WHAT THESE TESTS PROVE: that every profile file the package ships names a
program some recipe in this tree installs; that the recipe compiles and
installs the upstream component carrying the status tools; that the package
declares the status tool among the paths its build is verified against; and
that the package's own documentation names no profile file the package does not
ship.

WHAT THEY DO NOT PROVE: that the tools work on an installed machine. That was
measured separately by building the component and running it against this
machine's live policy, and it is evidence in the cut, not something a source
tree can assert.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RECIPE = REPO / "packages/core/apparmor"
BUILD_SH = RECIPE / "build.sh"
PACKAGE_YML = RECIPE / "package.yml"
PROFILES = RECIPE / "profiles"
# The package manager's launcher and the interpreter it runs are decided in two
# other recipes, so the check below reads them there rather than repeating them.
PKM_BUILD = REPO / "packages/core/pkm/build.sh"
PYTHON_YML = REPO / "packages/core/python/package.yml"

# The parser is in /usr/sbin, which a non-login shell's PATH need not carry, so
# it is looked for by name and then at its own path. Compiling a profile also
# needs the policy substrate this package installs, because every profile here
# includes abstractions from it.
PARSER = shutil.which("apparmor_parser") or "/usr/sbin/apparmor_parser"
ABSTRACTIONS = Path("/etc/apparmor.d/abstractions/base")
PARSER_USABLE = Path(PARSER).is_file() and ABSTRACTIONS.is_file()

# The one warning class the parser prints on these profiles that no change to
# them can remove, with what it costs and what removes it.
#
# The rule that produces it is in abstractions/base, which every profile here
# includes: four extended unix socket rules. apparmor_parser 3.1.7 enforces such
# a rule only when the feature "network/af_unix" is in BOTH the kernel's feature
# set and the abi the policy declares (parser/parser_main.c, features_intersect
# of "network/af_unix"). The abi this package installs carries it; kernel 6.18
# publishes af_unix under network_v9 and has no "network" node at all, so the
# intersection is empty and the parser downgrades each of the four to a generic
# network rule. Measured: the rule is still compiled into the policy - the
# downgrade is a weaker form, not a dropped rule - and compiling the same
# profile against a feature set that carries network/af_unix produces no warning
# at all. So what removes these four is a parser version that reads network_v9,
# not an edit to any profile.
KNOWN_DOWNGRADE = "downgrading extended network unix socket rule to generic network rule"

# The attachment line of an AppArmor profile: an optional profile NAME, then
# the program path, then flags or the opening brace. Both forms are read,
# because naming a profile by a file path is deprecated (apparmor_parser 3.1.7
# warns on it) and the profiles this package ships give the profile a name of
# its own; the group this returns is always the program path, whichever form
# the line uses.
ATTACHMENT = re.compile(
    r"^(?:profile\s+\S+\s+)?(/\S+)\s+(?:flags=\([^)]*\)\s*)?\{",
    re.MULTILINE)

# The include form apparmor_parser 3.1.7 warns about, and the one it wants.
DEPRECATED_INCLUDE = re.compile(r"^\s*#include\s+<", re.MULTILINE)
CURRENT_INCLUDE = re.compile(r"^\s*include\s+<", re.MULTILINE)

# A profile's feature abi declaration. The version matters: the abstractions
# this package installs declare abi/3.0, and a profile that declares a
# different one makes the parser say so and use the profile's, for every
# abstraction it includes.
ABI = re.compile(r"^abi\s+<abi/([0-9.]+)>\s*,\s*$", re.MULTILINE)
ABSTRACTION_ABI = "3.0"


def profile_files() -> list[Path]:
    return sorted(p for p in PROFILES.iterdir() if p.is_file())


def attached_program(profile: Path) -> str:
    match = ATTACHMENT.search(profile.read_text())
    assert match, f"{profile.name} has no profile attachment line"
    return match.group(1)


def some_recipe_installs(program: str) -> bool:
    """True when a recipe's build script puts this path on a machine.

    The search is over the build scripts because that is where a file becomes
    a file on a machine; a mention in a comment, a document or a profile is not
    an installation. The security package's own script is excluded: it installs
    the profiles, not the programs they confine, so leaving it in would let a
    profile justify itself.
    """
    result = subprocess.run(
        ["git", "grep", "-l", "--fixed-strings", program.lstrip("/"),
         "--", "packages/*/*/build.sh"],
        cwd=REPO, capture_output=True, text=True,
    )
    providers = [
        line for line in result.stdout.splitlines()
        if line and line != "packages/core/apparmor/build.sh"
    ]
    return bool(providers)


def test_every_shipped_profile_names_a_program_this_tree_installs():
    orphans = []
    for profile in profile_files():
        program = attached_program(profile)
        if not some_recipe_installs(program):
            orphans.append(f"{profile.name} -> {program}")
    assert orphans == [], (
        "these profiles confine a program no recipe in this tree installs, so "
        "they load into the kernel on every machine and confine nothing: "
        f"{orphans}"
    )


def test_the_recipe_compiles_the_component_that_carries_the_status_tools():
    text = BUILD_SH.read_text()
    body = "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )
    assert "make -C binutils" in body, (
        "the recipe does not compile the upstream component that carries "
        "aa-status, aa-enabled, aa-exec and aa-features-abi"
    )
    assert "make -C binutils install" in body, (
        "the recipe compiles the status tools and never installs them"
    )


def test_the_package_verifies_the_status_tool_landed():
    text = PACKAGE_YML.read_text()
    assert "/usr/sbin/aa-status" in text, (
        "the package does not name the status tool among its verify_paths, so "
        "a build that produced no tool would pass the same gate that was "
        "strengthened in 2026-05 for exactly this class"
    )


def test_the_package_documentation_names_no_profile_it_does_not_ship():
    shipped = {p.name for p in profile_files()}
    named = set()
    named |= set(re.findall(r"usr\.bin\.[A-Za-z0-9._-]+",
                            (RECIPE / "README.md").read_text()))
    # Comment lines of the build script are excluded on purpose: the script
    # explains there why a profile it once shipped was removed, and an
    # explanation of a removal is not a claim that the file is still there.
    code = "\n".join(
        line for line in BUILD_SH.read_text().splitlines()
        if not line.strip().startswith("#")
    )
    named |= set(re.findall(r"usr\.bin\.[A-Za-z0-9._-]+", code))
    missing = sorted(named - shipped)
    assert missing == [], (
        "the package's own files name profile files it does not ship: "
        f"{missing}"
    )


def test_the_attachment_reader_reads_both_forms():
    """The reader above is the thing every check here depends on, so it is
    pinned itself: a line that names the profile and a line that does not both
    yield the program path, and a line that is neither yields nothing."""
    named = "profile pkm /usr/bin/pkm flags=(complain) {\n"
    bare = "/usr/bin/pkm flags=(complain) {\n"
    no_flags = "profile pkm /usr/bin/pkm {\n"
    for text in (named, bare, no_flags):
        match = ATTACHMENT.search(text)
        assert match, f"the attachment reader read nothing in {text!r}"
        assert match.group(1) == "/usr/bin/pkm", match.group(1)
    assert ATTACHMENT.search("include <tunables/global>\n") is None


def test_every_shipped_profile_uses_the_current_include_form():
    """apparmor_parser 3.1.7 warns once per line on the '#include' form, and a
    warning on every load is noise a person learns to read past - which is how
    a real warning goes unread. The current form is 'include <...>', which the
    53 upstream profiles on an installed machine already use."""
    offenders = {}
    for profile in profile_files():
        text = profile.read_text()
        hits = len(DEPRECATED_INCLUDE.findall(text))
        if hits:
            offenders[profile.name] = hits
    assert offenders == {}, (
        "these profiles use the include form the parser warns about, once per "
        f"line: {offenders}"
    )


def test_every_shipped_profile_names_its_profile_rather_than_a_file_path():
    """A profile whose name IS the program path is the deprecated form (the
    parser says so, once per profile) and the name is what the kernel puts in
    every audit record, so it is also the name a person reads. The shipped
    documentation already calls this one 'pkm'."""
    unnamed = []
    for profile in profile_files():
        text = profile.read_text()
        match = ATTACHMENT.search(text)
        assert match, f"{profile.name} has no profile attachment line"
        if not text[match.start():].startswith("profile "):
            unnamed.append(f"{profile.name} -> {match.group(1)}")
    assert unnamed == [], (
        "these profiles are named by a file path, the form the parser warns "
        f"about: {unnamed}"
    )


def test_every_shipped_profile_declares_the_feature_abi_its_abstractions_use():
    """A profile with no abi line is compiled against the parser's built-in
    default feature set, and that set's network feature is a form this kernel
    does not publish, so the parser says 'network rules not enforced' and the
    profile's network rules are dead text: the domain mediates no network at
    all. Declaring the abi the shipped abstractions declare makes those rules
    take effect and removes the two warnings the fallback prints.
    """
    wrong = {}
    for profile in profile_files():
        match = ABI.search(profile.read_text())
        if match is None:
            wrong[profile.name] = "no abi declaration"
        elif match.group(1) != ABSTRACTION_ABI:
            wrong[profile.name] = (
                f"declares abi/{match.group(1)}, while the abstractions this "
                f"package installs declare abi/{ABSTRACTION_ABI}")
    assert wrong == {}, (
        "these profiles are compiled against a feature set nobody chose: "
        f"{wrong}"
    )


def test_the_profile_covers_the_interpreter_its_own_launcher_runs():
    """The confined program is a shell script that execs an interpreter, and a
    profile that grants the interpreter no execute permission does not confine
    the program that does the work.

    Two facts measured on this kernel (parser 3.1.7, kernel 6.18.10, complain
    mode, records read back from the kernel log):

      - with no execute rule for the interpreter, the exec is recorded as
        entering a learning child of the profile
        (target="<profile>//null-/usr/bin/python3.14"), and inside that child
        the network, unix-socket, signal, mount and user-namespace classes are
        not mediated at all, so every transition rule written in the profile is
        unreachable for the program that does the work; under enforce the same
        missing rule denies the exec and the package manager cannot start.

      - a rule naming the SYMLINK the launcher writes (/usr/bin/python3) does
        not match: the kernel matches the file the symlink resolves to, so the
        rule must name /usr/bin/python3.14. The launcher's own interpreter needs
        no rule, and has none: no record in that measurement named it, because
        the profile attaches at the script and the interpreter runs inside it.

    The expected path is derived here from the two recipes that decide it - the
    launcher line in the package manager's recipe and the interpreter version in
    python's - so an interpreter version move fails this case in the tree
    instead of silently putting the program back in a learning child.
    """
    launcher = re.search(r"^exec (/usr/bin/python3)\b", PKM_BUILD.read_text(),
                         re.MULTILINE)
    assert launcher, "the package manager's recipe no longer writes the launcher line this reads"
    version = re.search(r'^version:\s*"(\d+)\.(\d+)\.', PYTHON_YML.read_text(),
                        re.MULTILINE)
    assert version, "python's recipe no longer states a version this can read"
    resolved = f"/usr/bin/python{version.group(1)}.{version.group(2)}"
    rule = re.compile(r"^\s*" + re.escape(resolved) + r"\s+([a-zA-Z]*x)\s*,\s*$",
                      re.MULTILINE)
    text = (PROFILES / "usr.bin.pkm").read_text()
    match = rule.search(text)
    assert match, (
        f"the profile grants no execute permission to {resolved}, the file the "
        f"launcher's {launcher.group(1)} resolves to, so the program that does "
        "the work runs outside this profile's rules"
    )
    assert "i" in match.group(1), (
        f"the rule for {resolved} is '{match.group(1)}', which sends the "
        "interpreter somewhere other than this profile's own domain; the "
        "measurement above is of an inherited execution (ix)"
    )


@pytest.mark.skipif(
    not PARSER_USABLE,
    reason="apparmor_parser or the installed policy substrate is absent on this "
           "machine, so the shipped profiles cannot be compiled here")
def test_the_parser_reads_every_shipped_profile_with_no_unexpected_warning():
    """The real consumer, over the shipped bytes: the program that compiles
    these profiles at every install and every boot, asked to print every warning
    it has.

    A profile that compiles with warnings compiles with warnings on every
    machine that installs it, at every boot, and a warning that always prints is
    one a person learns to read past. The budget is exactly one class, the one
    no edit to these files can remove (see KNOWN_DOWNGRADE above); anything else
    fails here, in the tree, rather than in a log nobody reads.
    """
    unexpected = {}
    for profile in profile_files():
        # No -S: that writes the compiled binary policy to stdout, which is
        # not text. The warnings come from the compile either way.
        run = subprocess.run(
            [PARSER, "-Q", "-K", "--warn=all", str(profile)],
            capture_output=True, text=True,
        )
        assert run.returncode == 0, (
            f"{profile.name} does not compile: exit {run.returncode}\n{run.stderr}")
        lines = [line for line in run.stderr.splitlines() if line.strip()]
        others = [line for line in lines if KNOWN_DOWNGRADE not in line]
        if others:
            unexpected[profile.name] = others
    assert unexpected == {}, (
        "the parser prints warnings on these shipped profiles that are not the "
        f"one known class: {unexpected}"
    )
