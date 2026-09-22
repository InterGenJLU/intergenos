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


def mixer_stub(bin_dir: Path, log: Path, controls: list[str],
               scontrols_status: int = 0, sset_status: int = 0) -> None:
    """An amixer stand-in that answers both questions the helper asks.

    The real program is asked twice: `scontrols`, which lists the simple
    controls a card has, and `sset`, which sets one of them. The helper tells
    an element this codec does not have from an element it has that will not
    take 0 dB by reading the first answer, so a stand-in that answers only the
    second would not exercise the distinction at all.
    """
    listing = "\n".join(
        f"Simple mixer control '{name}',0" for name in controls
    )
    stub = bin_dir / "amixer"
    stub.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {shlex.quote(str(log))}\n'
        "case \"$*\" in\n"
        "  *scontrols*)\n"
        f"    cat <<'LISTING'\n{listing}\nLISTING\n"
        f"    exit {scontrols_status} ;;\n"
        "esac\n"
        f"exit {sset_status}\n"
    )
    stub.chmod(0o755)


def fake_control_devices(root: Path, cards: list[str]) -> str:
    """A directory of control-device names, and the glob that finds them.

    The helper takes its device glob from the environment so that this can be
    driven on a build host with no sound card at all. A test that read the
    host's real /dev/snd would say nothing on such a host, and a check that
    cannot fail is not a check.
    """
    snd = root / "snd"
    snd.mkdir(parents=True, exist_ok=True)
    for card in cards:
        (snd / f"controlC{card}").write_text("")
    return f"{snd}/controlC*"


def run_helper(helper: Path, bin_dir: Path, tmp_path: Path,
               glob_pattern: str | None = None) -> subprocess.CompletedProcess:
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    if glob_pattern is not None:
        env["PIPEWIRE_BOOST_CONTROL_GLOB"] = glob_pattern
    return subprocess.run(
        [str(helper)], capture_output=True, text=True, env=env
    )


def test_the_helper_writes_every_listed_element_the_card_actually_has(
        tmp_path):
    """Behaviour, with a stand-in mixer program that records what it is asked.

    Two control devices are placed in a fixture directory, both reporting
    every listed element, so the expectation is the same on a machine with
    sound cards and on a build host with none.
    """
    root = staged(tmp_path)
    helper = root / HELPER
    elements = [name for name in helper_elements(helper) if name]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "amixer.log"
    mixer_stub(bin_dir, log, elements)
    glob_pattern = fake_control_devices(tmp_path, ["1", "7"])

    result = run_helper(helper, bin_dir, tmp_path, glob_pattern)

    assert result.returncode == 0, (
        f"the helper failed: {result.stdout}{result.stderr}"
    )
    calls = log.read_text().splitlines() if log.exists() else []
    for card in ("1", "7"):
        assert f"-c {card} scontrols" in calls, (
            f"the helper did not ask card {card} which controls it has: {calls}"
        )
        for element in elements:
            assert f"-c {card} -q sset {element} 0dB" in calls, (
                f"no write of {element} to 0 dB on card {card}: {calls}"
            )
    assert len(calls) == 2 * (1 + len(elements)), (
        f"{len(calls)} mixer calls for {len(elements)} elements on two "
        f"control devices: {calls}"
    )
    assert f"{2 * len(elements)} element(s) set to 0 dB" in result.stdout, (
        f"the helper does not say what it did: {result.stdout}"
    )


def test_the_helper_passes_over_an_element_the_card_does_not_have(tmp_path):
    """An element this codec has not got is an ordinary fact, not a failure.

    The element list names every boost the shipped mixer path files mark, not
    the ones any one codec carries, so most machines have most of them absent.
    The helper must exit 0 and write nothing for them.
    """
    root = staged(tmp_path)
    helper = root / HELPER
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "amixer.log"
    mixer_stub(bin_dir, log, ["Master", "PCM"])
    glob_pattern = fake_control_devices(tmp_path, ["3"])

    result = run_helper(helper, bin_dir, tmp_path, glob_pattern)

    assert result.returncode == 0, (
        "the helper failed on a card that simply has no boost element: "
        f"{result.stdout}{result.stderr}"
    )
    calls = log.read_text().splitlines() if log.exists() else []
    assert [c for c in calls if "sset" in c] == [], (
        f"the helper wrote an element the card does not have: {calls}"
    )
    elements = [name for name in helper_elements(helper) if name]
    assert f"{len(elements)} not present on this machine" in result.stdout, (
        f"the helper does not say the elements were absent: {result.stdout}"
    )


def test_the_helper_reports_a_write_that_fails_on_an_element_that_is_there(
        tmp_path):
    """An element the card HAS that will not take 0 dB is a failure.

    Measured by the independent read of the first form of this change: the
    helper discarded every diagnostic and every status, so a simulated mixer
    I/O error produced the same empty, successful run as a machine with no
    boost at all. A boost that was never zeroed then looks exactly like a
    boost there was nothing to zero, which is the reading the whole change
    exists to prevent. The drop-in keeps its leading "-", so the restore is
    still not failed by this; what changes is that the helper says so.
    """
    root = staged(tmp_path)
    helper = root / HELPER
    elements = [name for name in helper_elements(helper) if name]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "amixer.log"
    mixer_stub(bin_dir, log, elements, sset_status=1)
    glob_pattern = fake_control_devices(tmp_path, ["2"])

    result = run_helper(helper, bin_dir, tmp_path, glob_pattern)

    assert result.returncode != 0, (
        "the helper reported success although every mixer write failed: "
        f"{result.stdout}{result.stderr}"
    )
    assert elements[0] in result.stderr and "card 2" in result.stderr, (
        f"the failure names neither the element nor the card: {result.stderr}"
    )
    assert f"{len(elements)} failed" in result.stdout, (
        f"the helper does not count the failures: {result.stdout}"
    )


def test_the_helper_reports_a_card_whose_controls_cannot_be_read(tmp_path):
    """If the card cannot be asked at all, nothing about it is known."""
    root = staged(tmp_path)
    helper = root / HELPER
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "amixer.log"
    mixer_stub(bin_dir, log, [], scontrols_status=1)
    glob_pattern = fake_control_devices(tmp_path, ["5"])

    result = run_helper(helper, bin_dir, tmp_path, glob_pattern)

    assert result.returncode != 0, (
        "the helper passed on a card it could not read at all: "
        f"{result.stdout}{result.stderr}"
    )
    assert "card 5" in result.stderr, (
        f"the failure does not name the card: {result.stderr}"
    )


def test_the_helper_writes_nothing_where_there_is_no_control_device(tmp_path):
    """A build host with no sound card: no calls, exit 0."""
    root = staged(tmp_path)
    helper = root / HELPER
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "amixer.log"
    mixer_stub(bin_dir, log, [])
    empty = tmp_path / "no-cards"
    empty.mkdir()

    result = run_helper(helper, bin_dir, tmp_path, f"{empty}/controlC*")

    assert result.returncode == 0, (
        f"the helper failed with no control device: {result.stderr}"
    )
    assert not log.exists() or log.read_text() == "", (
        f"the helper called the mixer with no control device: {log.read_text()}"
    )


def test_the_helper_is_not_written_through_a_link_at_its_destination(tmp_path):
    """A link standing where the helper goes must be refused, not followed.

    Measured by the independent read: the step redirected straight into
    usr/libexec/pipewire-zero-microphone-boost, so a link there was followed,
    the file it pointed at was overwritten with the helper's text, and the
    chmod that followed changed that outside file's mode from 0600 to 0755.
    """
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    outside.mkdir(parents=True)
    sentinel = outside / "helper-sentinel"
    sentinel.write_text("this file is not the staging root\n")
    sentinel.chmod(0o600)
    (root / "usr/libexec").mkdir(parents=True)
    (root / HELPER).symlink_to(sentinel)

    result = recipe('install_boost_zeroing_helper "$1"', str(root))

    assert result.returncode != 0, (
        "the step passed with a symbolic link where the helper goes: "
        f"{result.stdout}{result.stderr}"
    )
    assert (root / HELPER).is_symlink(), "the step replaced the link"
    assert sentinel.read_text() == "this file is not the staging root\n", (
        "the file outside the staging root was overwritten"
    )
    assert stat.S_IMODE(sentinel.stat().st_mode) == 0o600, (
        "the mode of the file outside the staging root was changed"
    )


def test_the_drop_in_is_not_written_through_a_link_at_its_destination(
        tmp_path):
    """The same rule at the second generated file."""
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    outside.mkdir(parents=True)
    sentinel = outside / "drop-in-sentinel"
    sentinel.write_text("this file is not the staging root\n")
    sentinel.chmod(0o600)
    (root / DROP_IN).parent.mkdir(parents=True)
    (root / DROP_IN).symlink_to(sentinel)

    result = recipe('install_boost_zeroing_helper "$1"', str(root))

    assert result.returncode != 0, (
        "the step passed with a symbolic link where the drop-in goes: "
        f"{result.stdout}{result.stderr}"
    )
    assert (root / DROP_IN).is_symlink(), "the step replaced the link"
    assert sentinel.read_text() == "this file is not the staging root\n", (
        "the file outside the staging root was overwritten"
    )
    assert stat.S_IMODE(sentinel.stat().st_mode) == 0o600, (
        "the mode of the file outside the staging root was changed"
    )


def test_no_staging_temporary_is_left_behind(tmp_path):
    """The temporaries the step creates are published, never left."""
    root = staged(tmp_path)
    leftovers = sorted(
        str(p.relative_to(root)) for p in root.rglob(".pipewire-staging.*")
    )
    assert leftovers == [], f"temporaries were left behind: {leftovers}"


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
