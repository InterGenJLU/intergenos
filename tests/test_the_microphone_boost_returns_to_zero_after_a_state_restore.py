#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A microphone boost written straight to the mixer is put back to 0 dB.

WHAT THIS COVERS. The recipe marks the microphone boost elements
`volume = zero` in the mixer path files, and the audio server honours that
when it APPLIES a route — at session start, on a port or profile change, on a
session-manager restart — and at no other moment. Anything that writes those
controls directly in between holds the boost up until the next apply.

The writer this system ships is alsa-utils: `alsactl restore`, run by
alsa-restore.service from the saved state in /var/lib/alsa/asound.state at
boot, and again from the udev rule 90-alsa-restore.rules whenever a sound
control device appears. The saved state is written by the same unit's ExecStop
at shutdown, so a machine whose boost was raised once comes back up with it
raised; and a card that appears after the session is running is restored after
the last route apply, where nothing takes it down again. Measured on this
project's hardware 2026-09-22: restoring a saved state that carries the boost
at its maximum leaves the control at +30.00 dB and it stays there.

The recipe therefore installs a small helper and a drop-in on alsa-utils' own
unit, so the boost elements are put back to 0 dB immediately after the restore
has written them. The drop-in can only ever run where alsa-utils is installed,
which is why this needs no new dependency: without it there is no
alsa-restore.service and no restore to correct.

WHAT THESE TESTS PROVE. That do_install installs both files, at the paths and
modes they must have; that the helper is valid shell; that its element list is
GENERATED from the same list the path-file rewrite uses, so the two cannot
drift apart; that it names no capture element; that the drop-in runs the
helper after the restore and cannot fail it; and that the helper issues one
mixer write per listed element per control device present, and exits 0 when a
device or an element is not there.

WHAT THEY DO NOT PROVE: that the drop-in is picked up by the system manager
from /usr/lib/systemd/system/alsa-restore.service.d on an installed machine.
That needs the package installed and is measured there, not from a source
tree.
"""
import glob
import os
import shlex
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUILD_SH = REPO / "packages/desktop/pipewire/build.sh"

HELPER = "usr/libexec/pipewire-zero-microphone-boost"
DROP_IN = ("usr/lib/systemd/system/alsa-restore.service.d/"
           "10-microphone-boost-to-zero.conf")


def recipe(command: str, *args: str, env: dict | None = None
           ) -> subprocess.CompletedProcess:
    """Run `command` with the recipe sourced, so what runs is what ships."""
    script = f"set -e\nsource {shlex.quote(str(BUILD_SH))}\n{command}\n"
    run_env = None
    if env is not None:
        run_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env}
    return subprocess.run(
        ["bash", "-c", script, "_", *args],
        capture_output=True,
        text=True,
        env=run_env,
    )


def helper_elements(helper: Path) -> list[str]:
    """The element names the generated helper carries, read from its text.

    Read rather than sourced: the helper ends in `exit 0`, so a shell that
    sourced it would leave before it could be asked anything.
    """
    names, inside = [], False
    for line in helper.read_text().splitlines():
        if "<<'ELEMENTS'" in line:
            inside = True
            continue
        if inside and line.strip() == "ELEMENTS":
            break
        if inside:
            names.append(line)
    return names


def staged(root: Path, env: dict | None = None) -> Path:
    """Run the recipe's install step for these two files against `root`."""
    result = recipe('install_boost_zeroing_helper "$1"', str(root), env=env)
    assert result.returncode == 0, (
        f"the install step failed: {result.stdout}{result.stderr}"
    )
    return root


def test_do_install_installs_the_helper_and_its_drop_in(tmp_path):
    """The step is reached from do_install, not only defined beside it."""
    body = recipe("declare -f do_install").stdout
    stripped = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("#")
    )
    assert "install_boost_zeroing_helper" in stripped, (
        "do_install does not call the step that installs the helper"
    )


def test_both_files_are_installed_at_their_paths_and_modes(tmp_path):
    root = staged(tmp_path)

    helper = root / HELPER
    drop_in = root / DROP_IN
    assert helper.is_file(), f"the helper was not installed at {HELPER}"
    assert drop_in.is_file(), f"the drop-in was not installed at {DROP_IN}"
    assert stat.S_IMODE(helper.stat().st_mode) == 0o755, (
        "the helper is not executable; the unit would not be able to run it"
    )
    assert stat.S_IMODE(drop_in.stat().st_mode) == 0o644, (
        "the drop-in has the wrong mode"
    )


def test_the_helper_is_valid_shell(tmp_path):
    root = staged(tmp_path)
    result = subprocess.run(
        ["sh", "-n", str(root / HELPER)], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"the generated helper is not valid shell: {result.stderr}"
    )
    assert (root / HELPER).read_text().startswith("#!/bin/sh"), (
        "the helper has no interpreter line"
    )


def test_the_helper_list_is_generated_from_the_stanza_list(tmp_path):
    """The two lists cannot drift: one is computed from the other.

    A helper carrying its own copy of the element names would keep zeroing
    the elements of an older path set after the stanza list moved, and
    nothing would say so.
    """
    root = staged(tmp_path)
    stanzas = recipe("boost_volume_stanzas").stdout.splitlines()
    wanted = sorted({line.split("|", 1)[1] for line in stanzas if line})

    listed = helper_elements(root / HELPER)
    assert [name for name in listed if name] == wanted, (
        f"the helper's element list is not the recipe's: {listed} != {wanted}"
    )


def test_the_helper_names_no_capture_element(tmp_path):
    """The capture element carries the usable range and must not be touched."""
    root = staged(tmp_path)
    listed = "\n".join(helper_elements(root / HELPER))
    assert "Capture" not in listed, (
        f"the helper would write the capture element: {listed}"
    )


def test_the_drop_in_runs_the_helper_after_the_restore_and_cannot_fail_it(
        tmp_path):
    root = staged(tmp_path)
    text = (root / DROP_IN).read_text()

    assert "[Service]" in text, "the drop-in has no [Service] section"
    line = next(
        (l for l in text.splitlines() if l.startswith("ExecStartPost=")), ""
    )
    assert line, f"the drop-in sets no ExecStartPost: {text}"
    assert line == "ExecStartPost=-/usr/libexec/pipewire-zero-microphone-boost", (
        "the drop-in does not run the helper after the restore with a leading "
        f"'-', so a failure here would fail the restore itself: {line}"
    )


def test_the_helper_writes_every_listed_element_on_every_control_device(
        tmp_path):
    """Behaviour, with a stub mixer program that records what it is asked.

    The expectation is derived from the control devices this host actually
    has, so the test says something true on a machine with sound cards and on
    a build host with none: with none, the helper must still exit 0 and write
    nothing.
    """
    root = staged(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "amixer.log"
    stub = bin_dir / "amixer"
    stub.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {shlex.quote(str(log))}\n'
        "exit 0\n"
    )
    stub.chmod(0o755)

    result = subprocess.run(
        [str(root / HELPER)],
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)},
    )

    assert result.returncode == 0, (
        f"the helper failed: {result.stdout}{result.stderr}"
    )
    elements = [name for name in helper_elements(root / HELPER) if name]
    cards = sorted(glob.glob("/dev/snd/controlC*"))
    calls = log.read_text().splitlines() if log.exists() else []
    assert len(calls) == len(elements) * len(cards), (
        f"{len(calls)} mixer writes for {len(elements)} elements on "
        f"{len(cards)} control devices: {calls}"
    )
    for card in cards:
        index = card[len("/dev/snd/controlC"):]
        for element in elements:
            assert f'-c {index} -q sset {element} 0dB' in calls, (
                f"no write of {element} to 0 dB on card {index}: {calls}"
            )


def test_the_helper_survives_a_mixer_program_that_refuses(tmp_path):
    """An element a codec does not have must not fail the restore."""
    root = staged(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "amixer"
    stub.write_text("#!/bin/sh\necho 'no such control' >&2\nexit 1\n")
    stub.chmod(0o755)

    result = subprocess.run(
        [str(root / HELPER)],
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)},
    )

    assert result.returncode == 0, (
        "the helper failed when the mixer program refused; with the drop-in's "
        "leading '-' removed that would fail a boot-time restore"
    )


def test_the_helper_is_the_same_file_whatever_the_build_host_locale(tmp_path):
    """`sort` collates by locale, so an unpinned sort ships two files.

    Found by running this file's tests under a systemd unit, whose locale is
    not the interactive one: "Int Mic Boost" and "Internal Mic Boost" swap
    places between a C locale and a UTF-8 one. Two builds of the same source
    would then install different helpers, and nothing would say which one a
    machine has.
    """
    first = staged(tmp_path / "c", env={"LC_ALL": "C"})
    second = staged(tmp_path / "utf8", env={"LC_ALL": "en_US.UTF-8"})

    assert (first / HELPER).read_text() == (second / HELPER).read_text(), (
        "the generated helper differs between two build-host locales"
    )
