#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The audio server's ALSA configuration reaches the directory the library reads.

WHAT THIS COVERS. The audio server installs two ALSA configuration files —
`50-pipewire.conf`, which defines a `pipewire` device, and
`99-pipewire-default.conf`, which points `pcm.!default` at it — into
`/usr/share/alsa/alsa.conf.d`. The sound library does not read that directory.
Its shipped `alsa.conf` loads `/var/lib/alsa/conf.d`, `$sysconfdir/alsa/conf.d`
and `$sysconfdir/asound.conf`, and the library is configured `--prefix=/usr`
with no `--sysconfdir`, so that directory is `/usr/etc/alsa/conf.d`. The ALSA
plugin collection bridges the same gap for its own eleven files by installing
one symlink per file there; the audio server installed none, so both of its
files were installed and never read.

On the machine this was measured on, the ALSA default therefore stayed the
library's built-in dmix/dsnoop pair: `arecord -D default` exited 1 with
dsnoop's "unable to open slave" whenever the audio server held the capture
device, `arecord -D pipewire` reported "Unknown PCM pipewire", and neither
`aplay -L` nor `arecord -L` listed a `pipewire` device — while `aplay -D
default` still worked, because dmix can share an output card and dsnoop cannot
open a capture device another process owns.

The properties held here are that the recipe installs a link for each of the
two files, that the links point into the directory the files are installed to,
that their targets are relative and resolve inside a packaging root rather than
against the building machine, that they carry the same shape as the plugin
collection's links, and that the two paths are declared among the paths the
build verifies so they cannot silently stop being installed again.

WHAT THEY DO NOT PROVE: that recording through the ALSA default works on an
installed machine. That needs the archive built and installed, which needs a
build substrate; it is the unproven residue named in this change's delivery.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RECIPE = REPO / "packages/desktop/pipewire"
BUILD_SH = RECIPE / "build.sh"
PACKAGE_YML = RECIPE / "package.yml"

# The two files the audio server's own install places in the shared directory.
CONF_FILES = ("50-pipewire.conf", "99-pipewire-default.conf")
# The directory the sound library reads, given --prefix=/usr and no --sysconfdir.
READ_DIR = "usr/etc/alsa/conf.d"
# The directory the files are installed into.
INSTALL_DIR = "usr/share/alsa/alsa.conf.d"


def install_body() -> str:
    """The do_install function's text, comments stripped.

    Stripping comments matters: this file's own explanation names every path
    it checks for, so a probe that matched the comments would pass on a recipe
    that installs nothing.
    """
    text = BUILD_SH.read_text()
    match = re.search(r"^do_install\(\)\s*\{(.*?)^\}", text, re.S | re.M)
    assert match, "build.sh has no do_install() function"
    body = match.group(1)
    return "\n".join(
        line.split("#", 1)[0] for line in body.splitlines()
    )


def test_the_recipe_installs_a_link_for_each_configuration_file():
    body = install_body()
    for name in CONF_FILES:
        assert f"{READ_DIR}/{name}" in body, (
            f"do_install installs no link at /{READ_DIR}/{name}, so the sound "
            f"library never reads {name} and the ALSA default is not the audio "
            f"server"
        )
    assert re.search(r"\bln\s+-s", body), (
        "do_install names the link paths but runs no `ln -s`"
    )
    assert f'install -dm755 "${{DESTDIR}}/{READ_DIR}"' in body, (
        f"do_install does not create /{READ_DIR} before linking into it; a "
        f"packaging root has no such directory until something makes it"
    )


def test_every_link_target_is_relative_and_points_at_the_installed_file():
    """An absolute target would name a path on the building machine.

    The package manager rewrites an absolute symlink target to its relative
    equivalent at extraction, so an absolute target would still land correctly
    — but that makes the result depend on the rewrite running. Relative targets
    are what the plugin collection uses and what this recipe states directly.
    """
    body = install_body()
    targets = re.findall(r"ln\s+-sfn\s+(\S+)", body)
    assert len(targets) == len(CONF_FILES), (
        f"expected {len(CONF_FILES)} `ln -sfn` targets in do_install, found "
        f"{len(targets)}: {targets}"
    )
    for target in targets:
        assert not target.startswith("/"), (
            f"link target {target!r} is absolute; it names a path on the "
            f"machine that built the package"
        )
        assert target.endswith(CONF_FILES), (
            f"link target {target!r} does not point at one of {CONF_FILES}"
        )
        assert INSTALL_DIR.split("/", 1)[1] in target, (
            f"link target {target!r} does not point into {INSTALL_DIR}, where "
            f"the files are installed"
        )


def test_the_links_resolve_inside_a_packaging_root(tmp_path):
    """Build the links exactly as the recipe does and follow them.

    A relative target with the wrong number of `../` steps resolves outside
    the packaging root, which means it would resolve against the BUILDING
    machine's filesystem at package time and be broken on the target. This
    creates the two directories, drops a marker file where the real
    configuration lands, creates the links with the recipe's own targets, and
    reads through them.
    """
    body = install_body()
    targets = re.findall(r"ln\s+-sfn\s+(\S+)", body)
    # Without this, the loop below runs zero times on a recipe that creates no
    # links and the test passes having asserted nothing — which is exactly what
    # it did when first run against the unchanged recipe.
    assert len(targets) == len(CONF_FILES), (
        f"do_install creates {len(targets)} links, not {len(CONF_FILES)}; "
        f"there is nothing here to resolve"
    )
    root = tmp_path / "root"
    (root / READ_DIR).mkdir(parents=True)
    (root / INSTALL_DIR).mkdir(parents=True)

    for target, name in zip(targets, CONF_FILES):
        marker = f"# {name} as installed\n"
        (root / INSTALL_DIR / name).write_text(marker)
        link = root / READ_DIR / name
        os.symlink(target, link)

        resolved = Path(os.path.realpath(link))
        assert resolved.is_file(), (
            f"the link /{READ_DIR}/{name} with target {target!r} does not "
            f"resolve to a file; it resolves to {resolved}"
        )
        assert root in resolved.parents, (
            f"the link /{READ_DIR}/{name} escapes the packaging root: it "
            f"resolves to {resolved}, outside {root}. Its target {target!r} "
            f"has the wrong number of parent steps."
        )
        assert resolved.read_text() == marker, (
            f"the link /{READ_DIR}/{name} resolves to {resolved}, which is "
            f"not the configuration file installed beside it"
        )


def test_the_two_paths_are_declared_among_the_paths_the_build_verifies():
    """This is what stops the links going missing unnoticed a second time.

    The pre-squashfs audit checks that every declared path exists in the built
    root, so a declared link that stops being installed halts the build
    instead of shipping an audio stack that cannot record.
    """
    text = PACKAGE_YML.read_text()
    match = re.search(r"^verify_paths:\s*$(.*?)(?=^\S|\Z)", text, re.S | re.M)
    assert match, "package.yml declares no verify_paths"
    declared = [
        line.split("#", 1)[0].strip().lstrip("-").strip()
        for line in match.group(1).splitlines()
        if line.strip().startswith("-")
    ]
    for name in CONF_FILES:
        want = f"/{READ_DIR}/{name}"
        assert want in declared, (
            f"{want} is not declared in verify_paths, so the build would not "
            f"notice if it stopped being installed. Declared: {declared}"
        )


def test_the_release_was_bumped_and_its_note_says_why():
    """A recipe pinned to an upstream tarball is not watched by the release
    bump tool, so the number and its note are written by hand and must be."""
    first_lines = PACKAGE_YML.read_text().splitlines()
    release_line = next(
        (l for l in first_lines if l.startswith("release:")), None
    )
    assert release_line, "package.yml has no release: line"
    number = int(re.match(r"release:\s*(\d+)", release_line).group(1))
    assert number >= 4, (
        f"release is {number}; the change to do_install needs a bump so the "
        f"package manager and the mirror see a new build"
    )
    assert "alsa" in release_line.lower(), (
        "the release note does not mention the ALSA change it stands for"
    )


@pytest.mark.skipif(
    shutil.which("arecord") is None,
    reason="alsa-utils is not installed on this machine",
)
def test_the_sound_library_finds_the_device_once_the_directory_is_read(tmp_path):
    """The real consumer: alsa-lib's own configuration loader.

    This is the causal claim the whole change rests on — that a configuration
    file placed in a directory alsa.conf loads makes the `pipewire` device
    known, where the same file in /usr/share/alsa/alsa.conf.d does not.

    The tool under test stays alsa-lib: its own loader, its own hook, its own
    parser, driven through the real `arecord -L`. What this moves is the
    DESTINATION — which directory the load list names — because that directory
    is precisely the variable the change alters. The shipped alsa.conf is
    copied and only its load list is repointed; nothing about the mechanism is
    replaced or reimplemented.
    """
    shipped = Path("/usr/share/alsa/alsa.conf")
    if not shipped.is_file():
        pytest.skip("the system's alsa.conf is not present")
    real_conf_d = Path("/usr/share/alsa/alsa.conf.d")
    present = [n for n in CONF_FILES if (real_conf_d / n).is_file()]
    if len(present) != len(CONF_FILES):
        pytest.skip(
            f"this machine does not carry both configuration files in "
            f"{real_conf_d} (found {present})"
        )

    conf_d = tmp_path / "conf.d"
    conf_d.mkdir()
    for name in CONF_FILES:
        shutil.copy(real_conf_d / name, conf_d / name)

    text = shipped.read_text()
    # Repoint the FIRST directory in alsa.conf's load list at our copy. The
    # list, the hook, the loader and the parser are all the shipped ones.
    text, count = re.subn(
        r'"/var/lib/alsa/conf\.d"', f'"{conf_d}"', text, count=1
    )
    assert count == 1, (
        "could not find the load-list entry to repoint in the shipped "
        "alsa.conf; its shape has changed and this test must be re-read"
    )
    patched = tmp_path / "alsa.conf"
    patched.write_text(text)

    def devices(config: Path) -> str:
        env = dict(os.environ, ALSA_CONFIG_PATH=str(config))
        proc = subprocess.run(
            ["arecord", "-L"], capture_output=True, text=True,
            timeout=60, env=env,
        )
        return proc.stdout

    with_links = devices(patched)
    without_links = devices(shipped)

    assert "pipewire" not in without_links, (
        "the control failed: the shipped configuration already lists a "
        "pipewire device, so this test cannot show that the directory is what "
        "makes the difference\n" + without_links
    )
    assert "pipewire" in with_links, (
        "alsa-lib still does not know the pipewire device when the "
        "configuration files are in a directory its load list names:\n"
        + with_links
    )
