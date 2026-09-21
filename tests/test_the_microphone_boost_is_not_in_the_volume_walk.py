#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The microphone boost is not part of the audio server's volume range.

WHAT THIS COVERS. The audio server picks a mixer path file per input port and
drives every element that file marks `volume = merge`. The shipped
documentation for that format, in `analog-output.conf.common`, states the
rule: the server walks the merge elements in the order the file lists them,
drives the first to the top of its range, and puts the remainder on the next
one. The input path files list the capture element first and a microphone
boost element second, so a source volume of 100% is the capture element at its
maximum with the boost stacked on top of it. Measured on 2026-09-21 on a
Realtek ALC285: capture +30.00 dB and boost +30 dB, sixty decibels of analog
gain, which saturates the microphone; the same shape was read on a Realtek
ALC236. A boost is an amplifier of last resort, not part of a linear range.

The recipe therefore rewrites the staged copies of those files after the
upstream install, setting the boost elements to the format's own
`volume = zero` (documented as "always set it to 0 dB"). The files belong to
the upstream tarball and are not carried in this repository, so the rewrite
runs at install time on every build of every version, and it fails the build
unless it changed exactly the stanzas it lists.

WHAT THESE TESTS PROVE. That the recipe lists exactly the twelve boost stanzas
found in the shipped path set; that `do_install` runs the step against the
staged mixer path directory; that the step changes each listed boost element
and nothing else, including leaving a `volume = merge` on a capture element and
in a stanza it does not name; and that the step HALTS — non-zero, with the
file named — when a listed stanza no longer carries the line, when a listed
file is absent, and when the list does not hold twelve entries.

WHAT THEY DO NOT PROVE: that the audio server then keeps the boost at 0 dB on
a running machine. That is not provable from a source tree; it is measured on
installed hardware by re-reading the mixer at each source volume.
"""
import re
import shlex
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RECIPE = REPO / "packages/desktop/pipewire"
BUILD_SH = RECIPE / "build.sh"

# The boost stanzas the shipped path set marks `volume = merge`, read from
# /usr/share/alsa-card-profile/mixer/paths of pipewire 1.6.0 on 2026-09-21:
# twelve stanzas across nine files.
EXPECTED_STANZAS = (
    ("analog-input-internal-mic.conf", "Internal Mic Boost"),
    ("analog-input-internal-mic.conf", "Int Mic Boost"),
    ("analog-input-internal-mic-always.conf", "Internal Mic Boost"),
    ("analog-input-internal-mic-always.conf", "Int Mic Boost"),
    ("analog-input-mic.conf", "Mic Boost"),
    ("analog-input-mic.conf", "Mic Boost (+20dB)"),
    ("analog-input-front-mic.conf", "Front Mic Boost"),
    ("analog-input-rear-mic.conf", "Rear Mic Boost"),
    ("analog-input-dock-mic.conf", "Dock Mic Boost"),
    ("analog-input-headphone-mic.conf", "Headphone Mic Boost"),
    ("analog-input-headset-mic.conf", "Headset Mic Boost"),
    ("analog-input-linein.conf", "Line Boost"),
)


def run_step(paths_dir: Path, preamble: str = "") -> subprocess.CompletedProcess:
    """Run the recipe's own function against `paths_dir`.

    The recipe is sourced, not copied: sourcing it defines its functions and
    runs nothing, so what is exercised here is the text that ships. `preamble`
    is shell that runs after the source and before the call, which is how a
    test replaces the stanza list to reach the count check.
    """
    script = (
        "set -e\n"
        f"source {shlex.quote(str(BUILD_SH))}\n"
        f"{preamble}\n"
        'zero_boost_volume_elements "$1"\n'
    )
    return subprocess.run(
        ["bash", "-c", script, "_", str(paths_dir)],
        capture_output=True,
        text=True,
    )


def path_file_text(elements) -> str:
    """A mixer path file in the shipped shape.

    `elements` is a sequence of (name, volume). Every file also carries a
    capture element on `merge` and an unnamed boost on `off`, so a rewrite that
    matched by keyword instead of by stanza would change a line it must not.
    """
    out = [
        "# a mixer path file",
        "",
        "[General]",
        "priority = 89",
        "",
        "[Element Capture]",
        "switch = mute",
        "volume = merge",
        "override-map.1 = all",
        "",
    ]
    for name, volume in elements:
        out += [
            f"[Element {name}]",
            "required-any = any",
            "switch = select",
            f"volume = {volume}",
            "override-map.1 = all",
            "",
            f"[Option {name}:on]",
            "name = input-boost-on",
            "",
        ]
    out += [
        "[Element Some Other Boost]",
        "switch = off",
        "volume = off",
        "",
    ]
    return "\n".join(out)


def build_paths_dir(root: Path) -> Path:
    """Write a path directory holding every listed stanza on `merge`."""
    paths = root / "paths"
    paths.mkdir(parents=True)
    by_file: dict[str, list[str]] = {}
    for conf, element in EXPECTED_STANZAS:
        by_file.setdefault(conf, []).append(element)
    for conf, elements in by_file.items():
        (paths / conf).write_text(
            path_file_text([(name, "merge") for name in elements])
        )
    # A file the list does not name: nothing in it may change.
    (paths / "analog-input-aux.conf").write_text(
        path_file_text([("Aux Boost", "merge")])
    )
    return paths


def stanza_volume(text: str, element: str) -> str:
    """The volume value inside one stanza, or "" if the stanza has none."""
    inside = False
    for line in text.splitlines():
        if line.startswith("["):
            inside = line == f"[Element {element}]"
            continue
        if inside:
            match = re.fullmatch(r"volume[ \t]*=[ \t]*(\S+)", line)
            if match:
                return match.group(1)
    return ""


def test_the_recipe_lists_exactly_the_twelve_boost_stanzas():
    result = subprocess.run(
        ["bash", "-c", f"source {shlex.quote(str(BUILD_SH))}; boost_volume_stanzas"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    listed = tuple(
        tuple(line.split("|", 1))
        for line in result.stdout.splitlines()
        if line.strip()
    )
    assert listed == EXPECTED_STANZAS, (
        "the recipe's stanza list is not the twelve boost stanzas the shipped "
        f"path set carries; it lists {listed}"
    )
    assert len({conf for conf, _ in listed}) == 9, (
        "the twelve stanzas span nine path files"
    )


def test_do_install_runs_the_step_on_the_staged_mixer_paths():
    text = BUILD_SH.read_text()
    match = re.search(r"^do_install\(\)\s*\{(.*?)^\}", text, re.S | re.M)
    assert match, "build.sh has no do_install() function"
    # Comments are stripped: this file names the function in its own prose, and
    # so does the recipe, so a probe that matched a comment would pass on a
    # recipe that never calls it.
    body = "\n".join(line.split("#", 1)[0] for line in match.group(1).splitlines())
    assert "zero_boost_volume_elements" in body, (
        "do_install never calls zero_boost_volume_elements, so the boost stays "
        "in the volume walk however the function is written"
    )
    assert "usr/share/alsa-card-profile/mixer/paths" in body, (
        "do_install does not name the staged mixer path directory"
    )
    assert "${DESTDIR}" in body, (
        "the step must run on the staged copies under DESTDIR, not on the "
        "building machine's own files"
    )


def test_the_step_zeroes_every_listed_boost_and_changes_nothing_else(tmp_path):
    paths = build_paths_dir(tmp_path)
    before = {p.name: p.read_text() for p in sorted(paths.iterdir())}

    result = run_step(paths)

    assert result.returncode == 0, f"stdout={result.stdout} stderr={result.stderr}"
    for conf, element in EXPECTED_STANZAS:
        text = (paths / conf).read_text()
        assert stanza_volume(text, element) == "zero", (
            f"[Element {element}] in {conf} is not on volume = zero after the "
            f"step, so the boost is still driven by the volume walk"
        )
    # Everything that is not a listed stanza's volume line is untouched.
    for name, old in before.items():
        new = (paths / name).read_text()
        listed = [e for c, e in EXPECTED_STANZAS if c == name]
        expected = old
        for element in listed:
            expected = expected.replace(
                f"[Element {element}]\nrequired-any = any\nswitch = select\n"
                f"volume = merge\n",
                f"[Element {element}]\nrequired-any = any\nswitch = select\n"
                f"volume = zero\n",
            )
        assert new == expected, f"{name} changed somewhere other than its listed stanzas"
        assert stanza_volume(new, "Capture") == "merge", (
            f"the capture element in {name} was changed; it carries the range "
            f"and must stay on merge"
        )
    assert stanza_volume(
        (paths / "analog-input-aux.conf").read_text(), "Aux Boost"
    ) == "merge", "a stanza the list does not name was changed"
    assert list(paths.glob("*.zero-boost")) == [], "a temporary file was left behind"


def test_the_step_halts_when_a_listed_stanza_no_longer_carries_the_merge_line(tmp_path):
    """An upstream rewrite of one stanza must stop the build, not pass quietly."""
    paths = build_paths_dir(tmp_path)
    target = paths / "analog-input-rear-mic.conf"
    target.write_text(
        target.read_text().replace(
            "[Element Rear Mic Boost]\nrequired-any = any\nswitch = select\n"
            "volume = merge\n",
            "[Element Rear Mic Boost]\nrequired-any = any\nswitch = select\n",
        )
    )

    result = run_step(paths)

    assert result.returncode != 0, (
        "the step passed although one listed stanza no longer carries "
        "volume = merge; an upstream change would ship silently"
    )
    assert "Rear Mic Boost" in result.stderr and "analog-input-rear-mic.conf" in result.stderr, (
        f"the refusal does not name the stanza it failed on: {result.stderr}"
    )
    assert list(paths.glob("*.zero-boost")) == [], "a temporary file was left behind"


def test_the_step_halts_when_a_listed_path_file_is_absent(tmp_path):
    paths = build_paths_dir(tmp_path)
    (paths / "analog-input-linein.conf").unlink()

    result = run_step(paths)

    assert result.returncode != 0, (
        "the step passed although a listed path file is not installed"
    )
    assert "analog-input-linein.conf" in result.stderr, result.stderr


def test_the_step_halts_when_the_list_does_not_hold_twelve_stanzas(tmp_path):
    """The count check, exercised by replacing the list with a shorter one."""
    paths = build_paths_dir(tmp_path)
    short = "\n".join(f"{c}|{e}" for c, e in EXPECTED_STANZAS[:-1])
    preamble = (
        "boost_volume_stanzas() { cat <<'SHORT'\n" + short + "\nSHORT\n}"
    )

    result = run_step(paths, preamble=preamble)

    assert result.returncode != 0, (
        "the step accepted eleven changed stanzas where it expects twelve"
    )
    assert "11" in result.stderr and "12" in result.stderr, (
        f"the refusal does not state the counts: {result.stderr}"
    )


def test_the_step_refuses_a_directory_that_is_not_there(tmp_path):
    """The refusal must be the step's own, not a shell error.

    `"not found" in stderr` would also match bash's own "command not found"
    when the function does not exist at all, which is how a test can pass
    against a recipe that has no step in it. The whole phrase and the
    directory are required instead.
    """
    missing = tmp_path / "no-such-directory"
    result = run_step(missing)
    assert result.returncode != 0
    assert "mixer path directory not found" in result.stderr, result.stderr
    assert str(missing) in result.stderr, result.stderr
