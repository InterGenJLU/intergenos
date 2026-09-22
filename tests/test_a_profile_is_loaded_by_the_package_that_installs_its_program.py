#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A profile is in /etc/apparmor.d only when the package that installs its
program puts it there.

WHAT THIS COVERS. The apparmor unit loads every file in /etc/apparmor.d at every
boot. Measured on an ordinary install on 2026-09-22, the security package put
55 top-level profiles there and 39 of them named a program that machine did not
have: each was parsed, loaded and counted in every summary of the policy while
confining nothing. Only the package that installs a program knows whether the
program is on the machine, so the decision is made there. The security package
stages every top-level profile in /usr/share/apparmor/extra-profiles, which
nothing loads, keeping only the two named profiles that other profiles switch
to; each owning package links its program's profile back into /etc/apparmor.d
and declares apparmor as a runtime dependency so the link cannot dangle.

WHAT THESE TESTS PROVE: that the staging step moves every top-level profile
except the two named targets, leaves links and directories alone, and refuses
rather than overwrites; that the security package's install runs it after both
profile sets are in place and before its own profile is installed; that each
of the six owning packages links exactly its own profiles, with a relative link
into the staged directory, and depends on apparmor at runtime; that no other
recipe links a staged profile, and no profile is linked twice; and that the set
the unit loads on a machine carrying all six packages is exactly the sixteen
named below.

The build-time half: the security package declares the names other packages
link and its own build refuses, naming them, when any is not staged as a
regular file; the tests prove that refusal, its passing case, and that the
declared list and the names the owning recipes link are the same set, so no
link can escape the check.

WHAT THEY DO NOT PROVE: that each linked profile's program is what the owning
package installs (measured on an installed machine and in the cut's evidence,
not assertable from a source tree), that the parser loads the linked profiles
(proven against the pinned upstream source in the cut's evidence), or that a
real package-manager upgrade crosses from the old placement to the new one.
"""
import re
import subprocess
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
APPARMOR_BUILD = REPO / "packages/core/apparmor/build.sh"
STAGED = "../../usr/share/apparmor/extra-profiles/"

# The owning package of every upstream profile whose program the image installs.
OWNERS = {
    "packages/core/inetutils": {"bin.ping"},
    "packages/desktop/samba": {
        "samba-bgqd", "samba-dcerpcd", "samba-rpcd", "samba-rpcd-classic",
        "samba-rpcd-spoolss", "usr.sbin.nmbd", "usr.sbin.smbd",
        "usr.sbin.winbindd",
    },
    "packages/desktop/avahi": {"usr.sbin.avahi-daemon"},
    "packages/extra/dnsmasq": {"usr.sbin.dnsmasq"},
    "packages/base/traceroute": {"usr.sbin.traceroute"},
    "packages/core/gzip-core": {"zgrep"},
}
NAMED_TARGETS = {"lsb_release", "nvidia_modprobe"}
FIRST_PARTY = {"usr.bin.pkm"}


def do_install_body(path: Path) -> str:
    text = path.read_text()
    match = re.search(r"^do_install\(\) \{\n(.*?)^\}\n", text, re.S | re.M)
    assert match, f"{path} has no do_install()"
    return "\n".join(
        line for line in match.group(1).splitlines()
        if not line.strip().startswith("#")
    )


def _run_snippet(snippet: str) -> dict[str, str]:
    """Run a recipe's own profile-link loop, taken from the recipe text, in a
    scratch DESTDIR and read back what it made."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["bash", "-euc", snippet], check=True,
            env={"DESTDIR": tmp, "PATH": "/usr/bin:/bin"},
        )
        loaded = Path(tmp) / "etc/apparmor.d"
        return {p.name: str(p.readlink()) for p in loaded.iterdir()}


def test_each_owning_package_links_exactly_its_profiles_into_the_staged_copy():
    for recipe, expected in OWNERS.items():
        body = do_install_body(REPO / recipe / "build.sh")
        match = re.search(
            r'(install -dm755 "\$\{DESTDIR\}/etc/apparmor\.d"\n'
            r'\s*for profile in [^\n]*; do\n.*?\n\s*done)',
            body, re.S,
        )
        assert match, f"{recipe} links no AppArmor profile into /etc/apparmor.d"
        links = _run_snippet(match.group(1))
        assert set(links) == expected, (
            f"{recipe} links {sorted(links)}, expected {sorted(expected)}"
        )
        for name, target in links.items():
            assert target == STAGED + name, (
                f"{recipe}: /etc/apparmor.d/{name} points at {target}, not at "
                f"the staged copy {STAGED}{name}"
            )


def test_each_owning_package_depends_on_apparmor_at_runtime():
    for recipe in OWNERS:
        meta = yaml.safe_load((REPO / recipe / "package.yml").read_text())
        runtime = meta["dependencies"]["runtime"] or []
        assert "apparmor" in runtime, (
            f"{recipe} links a profile staged by apparmor but does not depend "
            f"on it at runtime, so the link can dangle and the boot-time load "
            f"of /etc/apparmor.d then reports a failure"
        )


def test_no_other_recipe_links_a_staged_profile_and_none_is_linked_twice():
    result = subprocess.run(
        ["git", "grep", "-l", "--fixed-strings", "usr/share/apparmor/extra-profiles",
         "--", "packages/*/*/build.sh"],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    linkers = {
        str(Path(line).parent) for line in result.stdout.splitlines()
        if line and line != "packages/core/apparmor/build.sh"
    }
    assert linkers == set(OWNERS), (
        f"recipes linking a staged profile: {sorted(linkers)}; the owner table "
        f"is {sorted(OWNERS)}"
    )
    seen: dict[str, str] = {}
    for recipe, names in OWNERS.items():
        for name in names:
            assert name not in seen, f"{name} is linked by {seen[name]} and {recipe}"
            seen[name] = recipe


def test_the_loaded_set_on_a_machine_with_all_six_is_exactly_sixteen():
    linked = set().union(*OWNERS.values())
    assert not linked & NAMED_TARGETS
    loaded = linked | NAMED_TARGETS | FIRST_PARTY
    assert len(loaded) == 16, sorted(loaded)
    kept = set(re.search(
        r'^APPARMOR_NAMED_TARGETS="([^"]*)"$', APPARMOR_BUILD.read_text(), re.M,
    ).group(1).split())
    assert kept == NAMED_TARGETS, kept


def _stage(tmp: Path, entries: dict[str, str], staged: dict[str, str] = None):
    loaded = tmp / "etc/apparmor.d"
    loaded.mkdir(parents=True)
    for name, kind in entries.items():
        p = loaded / name
        if kind == "file":
            p.write_text(f"profile {name} {{}}\n")
        elif kind == "dir":
            p.mkdir()
        elif kind.startswith("link:"):
            p.symlink_to(kind[5:])
    for name in (staged or {}):
        d = tmp / "usr/share/apparmor/extra-profiles"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text("already here\n")
    return subprocess.run(
        ["bash", "-c", f'source "{APPARMOR_BUILD}" && stage_top_level_profiles "$1"',
         "stage", str(tmp)],
        capture_output=True, text=True,
    )


def test_staging_moves_every_top_level_profile_except_the_named_targets(tmp_path):
    result = _stage(tmp_path, {
        "usr.sbin.smbd": "file", "usr.bin.irssi": "file",
        "lsb_release": "file", "nvidia_modprobe": "file",
        "abstractions": "dir", "linked-by-a-package": "link:elsewhere",
    })
    assert result.returncode == 0, result.stderr
    loaded = {p.name for p in (tmp_path / "etc/apparmor.d").iterdir()}
    staged = {p.name for p in (tmp_path / "usr/share/apparmor/extra-profiles").iterdir()}
    assert loaded == {"lsb_release", "nvidia_modprobe", "abstractions",
                      "linked-by-a-package"}
    assert staged == {"usr.sbin.smbd", "usr.bin.irssi"}
    assert "2 staged" in result.stdout and "2 named targets kept" in result.stdout


def test_staging_refuses_rather_than_overwrites_a_staged_file(tmp_path):
    result = _stage(tmp_path, {"usr.sbin.smbd": "file"}, staged={"usr.sbin.smbd": 1})
    assert result.returncode == 1
    assert "refusing to overwrite" in result.stderr
    assert (tmp_path / "etc/apparmor.d/usr.sbin.smbd").is_file()
    assert (tmp_path / "usr/share/apparmor/extra-profiles/usr.sbin.smbd").read_text() == "already here\n"


def test_the_security_package_stages_after_both_sets_and_before_its_own_profile():
    body = do_install_body(APPARMOR_BUILD).splitlines()

    def first(fragment):
        hits = [i for i, line in enumerate(body) if fragment in line]
        assert hits, f"do_install has no line containing {fragment!r}"
        return hits[0]

    profiles = first("make -C profiles install")
    extras = first("profiles-extra/work/profiles -maxdepth 1")
    stage = first('stage_top_level_profiles "${DESTDIR}"')
    verify = first('verify_linked_profiles_are_staged "${DESTDIR}"')
    own = first("profiles/usr.bin.pkm")
    assert profiles < stage and extras < stage and stage < own, (
        "the staging step must run after the upstream and Debian-derived "
        "profiles are installed and before the package's own profile is"
    )
    assert stage < verify < own, (
        "the check that every linked profile is staged must run right after "
        "the staging step"
    )


def declared_linked_names() -> set[str]:
    match = re.search(
        r'^APPARMOR_LINKED_BY_OWNING_PACKAGES="([^"]*)"$',
        APPARMOR_BUILD.read_text(), re.M,
    )
    assert match, "the security package declares no list of linked profiles"
    return set(match.group(1).split())


def test_every_linked_name_is_declared_and_every_declared_name_is_linked():
    """The source-tree half of the build-time check: the security package's
    build refuses when a DECLARED name is not staged, so a name an owning
    recipe links without it being declared would escape that check."""
    linked = set()
    for recipe in OWNERS:
        body = do_install_body(REPO / recipe / "build.sh")
        match = re.search(
            r'(install -dm755 "\$\{DESTDIR\}/etc/apparmor\.d"\n'
            r'\s*for profile in [^\n]*; do\n.*?\n\s*done)',
            body, re.S,
        )
        linked |= set(_run_snippet(match.group(1)))
    declared = declared_linked_names()
    assert linked - declared == set(), (
        f"linked by an owning recipe but not declared by the security "
        f"package, so its build never checks they are staged: "
        f"{sorted(linked - declared)}"
    )
    assert declared - linked == set(), (
        f"declared by the security package but linked by no recipe: "
        f"{sorted(declared - linked)}"
    )


def _verify(tmp: Path, staged_files: set[str], staged_links: set[str] = ()):
    staged = tmp / "usr/share/apparmor/extra-profiles"
    staged.mkdir(parents=True)
    for name in staged_files:
        (staged / name).write_text(f"profile {name} {{}}\n")
    for name in staged_links:
        (staged / name).symlink_to("elsewhere")
    return subprocess.run(
        ["bash", "-c",
         f'source "{APPARMOR_BUILD}" && verify_linked_profiles_are_staged "$1"',
         "verify", str(tmp)],
        capture_output=True, text=True,
    )


def test_the_build_refuses_when_a_linked_profile_is_not_staged(tmp_path):
    names = declared_linked_names()
    result = _verify(tmp_path, names - {"usr.sbin.smbd"})
    assert result.returncode == 1, result.stdout
    assert "usr.sbin.smbd" in result.stderr, result.stderr
    assert "would dangle" in result.stderr, result.stderr


def test_the_build_refuses_a_linked_profile_staged_only_as_a_link(tmp_path):
    names = declared_linked_names()
    result = _verify(tmp_path, names - {"zgrep"}, staged_links={"zgrep"})
    assert result.returncode == 1, result.stdout
    assert "zgrep" in result.stderr, result.stderr


def test_the_check_passes_when_every_linked_profile_is_staged(tmp_path):
    names = declared_linked_names()
    result = _verify(tmp_path, names)
    assert result.returncode == 0, result.stderr
    assert f"all {len(names)} profiles other packages link are staged" in result.stdout
