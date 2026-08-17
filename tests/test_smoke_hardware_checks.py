# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Tests for installer/smoke/checks/hardware.sh — the unclaimed-hardware category.

WHY THESE TESTS LOOK THE WAY THEY DO
------------------------------------
The check under test asks questions about the machine it runs on. If these tests
asked the same questions of the machine running the suite, they would assert this
build host's hardware and fail on the next host that has a different one — a
host-specific condition promoted into a suite failure. So every test here builds
a FAKE machine in a temporary directory and points the check at it through the
module's injection points. Nothing below reads the real /sys, /proc or /dev.

Each condition is tested in BOTH directions. A check that reports FAIL for a dead
card reader is worth nothing unless it also reports PASS for a live one — a test
that cannot fail is worse than no test, and a check never shown to detect a true
positive cannot certify a zero.
"""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_DIR = REPO_ROOT / "installer" / "smoke"
HARDWARE_SH = SMOKE_DIR / "checks" / "hardware.sh"
SMOKE_TEST_SH = SMOKE_DIR / "smoke-test.sh"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def run_check(func, root, *, lspci_output="", aplay_output="", force_virt="0"):
    """Source lib.sh + hardware.sh against a fake root and run one check.

    Returns the list of "STATUS|id|message" entries the check emitted.
    """
    stub_dir = root / "_stubs"
    stub_dir.mkdir(parents=True, exist_ok=True)

    lspci = stub_dir / "lspci"
    lspci.write_text("#!/usr/bin/env bash\ncat <<'STUBEOF'\n" + lspci_output + "\nSTUBEOF\n")
    lspci.chmod(0o755)

    aplay = stub_dir / "aplay"
    aplay.write_text("#!/usr/bin/env bash\ncat <<'STUBEOF'\n" + aplay_output + "\nSTUBEOF\n")
    aplay.chmod(0o755)

    script = textwrap.dedent(f"""
        set -u
        SMOKE_JSON=1
        . "{SMOKE_DIR}/lib.sh"
        . "{HARDWARE_SH}"
        {func}
        for r in "${{SMOKE_RESULTS[@]}}"; do printf '%s\\n' "$r"; done
    """)
    env = dict(os.environ)
    env.update({
        "SMOKE_HW_ROOT": str(root),
        "SMOKE_HW_LSPCI": str(lspci),
        "SMOKE_HW_APLAY": str(aplay),
        "SMOKE_HW_FORCE_VIRT": force_virt,
        "SMOKE_JSON": "1",
    })
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60
    )
    assert proc.returncode == 0, f"harness failed: {proc.stderr}"
    return [line for line in proc.stdout.splitlines() if "|" in line]


def status_of(results, check_id):
    for entry in results:
        status, ident, _msg = entry.split("|", 2)
        if ident == check_id:
            return status
    raise AssertionError(f"check {check_id!r} emitted nothing; got {results!r}")


def message_of(results, check_id):
    for entry in results:
        status, ident, msg = entry.split("|", 2)
        if ident == check_id:
            return msg
    raise AssertionError(f"check {check_id!r} emitted nothing; got {results!r}")


# ---------------------------------------------------------------------------
# The deferred-probe list — the highest-signal check, and the one that would
# have caught the dead touchpad.
# ---------------------------------------------------------------------------

def test_deferred_probe_fails_when_a_device_was_never_brought_up(tmp_path):
    node = tmp_path / "sys/kernel/debug/devices_deferred"
    node.parent.mkdir(parents=True)
    # The exact line the real machine produced.
    node.write_text("i2c-ELAN0788:00\ti2c_hid_acpi: can't get irq\n")

    results = run_check("check_hardware_deferred_probe", tmp_path)
    assert status_of(results, "hw/deferred") == "FAIL"
    # The device and its reason must both survive into the message — a bare
    # "something failed" is not actionable on an install record.
    msg = message_of(results, "hw/deferred")
    assert "i2c-ELAN0788:00" in msg
    assert "can't get irq" in msg


def test_deferred_probe_passes_on_an_empty_list(tmp_path):
    """The negative control for the test above."""
    node = tmp_path / "sys/kernel/debug/devices_deferred"
    node.parent.mkdir(parents=True)
    node.write_text("")

    results = run_check("check_hardware_deferred_probe", tmp_path)
    assert status_of(results, "hw/deferred") == "PASS"


def test_deferred_probe_reports_multiple_devices(tmp_path):
    node = tmp_path / "sys/kernel/debug/devices_deferred"
    node.parent.mkdir(parents=True)
    node.write_text("dev-a\tdrv_a: reason one\ndev-b\tdrv_b: reason two\n")

    results = run_check("check_hardware_deferred_probe", tmp_path)
    assert status_of(results, "hw/deferred") == "FAIL"
    msg = message_of(results, "hw/deferred")
    assert "2 device(s)" in msg
    assert "dev-a" in msg and "dev-b" in msg


def test_deferred_probe_skips_rather_than_passes_when_unreadable(tmp_path):
    """An unread list is not an empty one.

    This is the trap the check must not fall into: reporting a clean result for
    a file it never opened. Absence of the node must never read as PASS.
    """
    results = run_check("check_hardware_deferred_probe", tmp_path)
    assert status_of(results, "hw/deferred") == "SKIP"


# ---------------------------------------------------------------------------
# Card reader — present in hardware, no MMC host.
# ---------------------------------------------------------------------------

CARD_READER_LSPCI = "00:14.3 SD Host controller: Realtek RTS5227 PCI Express Card Reader"


def test_card_reader_present_without_mmc_host_fails(tmp_path):
    results = run_check("check_hardware_card_reader", tmp_path,
                        lspci_output=CARD_READER_LSPCI)
    assert status_of(results, "hw/card-reader") == "FAIL"
    assert "NO MMC host" in message_of(results, "hw/card-reader")


def test_card_reader_present_with_mmc_host_passes(tmp_path):
    """Negative control: the same hardware, working."""
    (tmp_path / "sys/class/mmc_host/mmc0").mkdir(parents=True)
    results = run_check("check_hardware_card_reader", tmp_path,
                        lspci_output=CARD_READER_LSPCI)
    assert status_of(results, "hw/card-reader") == "PASS"


def test_card_reader_absent_from_hardware_skips(tmp_path):
    """A machine with no reader is not a defect."""
    results = run_check("check_hardware_card_reader", tmp_path,
                        lspci_output="00:1f.0 ISA bridge: Intel Corporation")
    assert status_of(results, "hw/card-reader") == "SKIP"


# ---------------------------------------------------------------------------
# Audio — a controller with no card, and a card that is HDMI-only.
# ---------------------------------------------------------------------------

AUDIO_LSPCI = "00:1f.3 Audio device: Intel Corporation Alder Lake PCH-P High Definition Audio"


def _write_cards(root, text):
    cards = root / "proc/asound/cards"
    cards.parent.mkdir(parents=True, exist_ok=True)
    cards.write_text(text)


def test_audio_controller_with_no_card_registered_fails(tmp_path):
    _write_cards(tmp_path, "--- no soundcards ---\n")
    results = run_check("check_hardware_audio", tmp_path, lspci_output=AUDIO_LSPCI)
    assert status_of(results, "hw/audio") == "FAIL"
    assert "NO sound card" in message_of(results, "hw/audio")


def test_audio_hdmi_only_card_fails(tmp_path):
    """The half-working state: sound through a monitor, none from the speakers."""
    _write_cards(tmp_path, " 0 [NVidia ]: HDA-Intel - HDA NVidia\n")
    results = run_check(
        "check_hardware_audio", tmp_path,
        lspci_output=AUDIO_LSPCI,
        aplay_output="card 0: NVidia [HDA NVidia], device 3: HDMI 0 [HDMI 0]",
    )
    assert status_of(results, "hw/audio") == "FAIL"
    assert "NO analog playback" in message_of(results, "hw/audio")


def test_audio_with_analog_playback_passes(tmp_path):
    """Negative control for both audio failures above."""
    _write_cards(tmp_path, " 0 [sofhdadsp ]: sof-hda-dsp - sof-hda-dsp\n")
    results = run_check(
        "check_hardware_audio", tmp_path,
        lspci_output=AUDIO_LSPCI,
        aplay_output="card 0: sofhdadsp [sof-hda-dsp], device 0: Speaker [Speaker]",
    )
    assert status_of(results, "hw/audio") == "PASS"


def test_audio_absent_controller_skips(tmp_path):
    results = run_check("check_hardware_audio", tmp_path,
                        lspci_output="00:00.0 Host bridge: Intel Corporation")
    assert status_of(results, "hw/audio") == "SKIP"


# ---------------------------------------------------------------------------
# Pointer — a laptop with no touchpad.
# ---------------------------------------------------------------------------

INTERNAL_KB = 'N: Name="AT Translated Set 2 keyboard"\n'
TOUCHPAD = 'N: Name="ELAN0788:00 04F3:321A Touchpad"\n'


def _write_input_devices(root, text):
    devices = root / "proc/bus/input/devices"
    devices.parent.mkdir(parents=True, exist_ok=True)
    devices.write_text(text)


def test_laptop_without_touchpad_fails(tmp_path):
    _write_input_devices(tmp_path, INTERNAL_KB)
    results = run_check("check_hardware_pointer", tmp_path)
    assert status_of(results, "hw/pointer") == "FAIL"


def test_laptop_with_touchpad_passes(tmp_path):
    """Negative control: the same machine after the pin controller lands."""
    _write_input_devices(tmp_path, INTERNAL_KB + TOUCHPAD)
    results = run_check("check_hardware_pointer", tmp_path)
    assert status_of(results, "hw/pointer") == "PASS"


def test_desktop_without_touchpad_skips(tmp_path):
    """No internal keyboard means no touchpad is expected — not a defect."""
    _write_input_devices(tmp_path, 'N: Name="Dell KB216 Wired Keyboard"\n')
    results = run_check("check_hardware_pointer", tmp_path)
    assert status_of(results, "hw/pointer") == "SKIP"


# ---------------------------------------------------------------------------
# Unclaimed PCI.
# ---------------------------------------------------------------------------

def _make_pci(root, addr, cls, bound):
    dev = root / "sys/bus/pci/devices" / addr
    dev.mkdir(parents=True)
    (dev / "class").write_text(cls + "\n")
    if bound:
        (dev / "driver").mkdir()


def test_unclaimed_pci_ignores_bridges_but_reports_real_devices(tmp_path):
    _make_pci(tmp_path, "0000:00:00.0", "0x060000", bound=False)   # host bridge, expected
    _make_pci(tmp_path, "0000:00:1f.0", "0x060100", bound=False)   # ISA bridge, expected
    _make_pci(tmp_path, "0000:00:1f.3", "0x040300", bound=False)   # audio, NOT expected
    results = run_check("check_hardware_unclaimed_pci", tmp_path)
    assert status_of(results, "hw/unclaimed-pci") == "WARN"
    msg = message_of(results, "hw/unclaimed-pci")
    assert "0000:00:1f.3" in msg
    assert "0000:00:00.0" not in msg, "a host bridge with no driver is normal, not a finding"


def test_unclaimed_pci_passes_when_everything_is_bound(tmp_path):
    """Negative control."""
    _make_pci(tmp_path, "0000:00:00.0", "0x060000", bound=False)   # bridge, excepted
    _make_pci(tmp_path, "0000:00:1f.3", "0x040300", bound=True)
    results = run_check("check_hardware_unclaimed_pci", tmp_path)
    assert status_of(results, "hw/unclaimed-pci") == "PASS"


# ---------------------------------------------------------------------------
# Day-one devices, including the wireless-detection regression guard.
# ---------------------------------------------------------------------------

def test_wireless_detected_via_phy80211(tmp_path):
    """Regression guard for a counting bug that reported 'no Wi-Fi' on a machine
    whose radio worked.

    The original prototype counted with `ls <dir> | wc -l`, which returns 0 for
    an EMPTY directory — so an interface that had a wireless/ directory with no
    entries in it was counted as absent. Detection must test for the interface's
    wireless attributes, never for the contents of a directory.
    """
    wlan = tmp_path / "sys/class/net/wlp4s0"
    wlan.mkdir(parents=True)
    (wlan / "phy80211").mkdir()
    (tmp_path / "sys/class/net/lo").mkdir(parents=True)

    results = run_check("check_hardware_day_one", tmp_path)
    assert "wifi" not in message_of(results, "hw/day-one").split("| present")[0]


def test_wireless_absent_is_reported(tmp_path):
    """Negative control for the guard above."""
    (tmp_path / "sys/class/net/eno1").mkdir(parents=True)
    results = run_check("check_hardware_day_one", tmp_path)
    assert "wifi" in message_of(results, "hw/day-one")


def test_day_one_all_present_passes(tmp_path):
    for path in ("sys/class/power_supply/BAT0", "sys/class/backlight/intel_backlight",
                 "sys/class/bluetooth/hci0", "sys/class/typec/port0",
                 "sys/class/net/wlp4s0/phy80211", "dev"):
        (tmp_path / path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "dev/video0").write_text("")
    results = run_check("check_hardware_day_one", tmp_path)
    assert status_of(results, "hw/day-one") == "PASS"


# ---------------------------------------------------------------------------
# Virtual machines.
# ---------------------------------------------------------------------------

def test_physical_checks_are_skipped_in_a_virtual_machine(tmp_path):
    """A guest has no card reader or touchpad to lose; reporting their absence
    as a defect would make the category cry wolf on every VM evaluation."""
    node = tmp_path / "sys/kernel/debug/devices_deferred"
    node.parent.mkdir(parents=True)
    node.write_text("")

    results = run_check("run_hardware_checks", tmp_path, force_virt="1")
    ids = [entry.split("|", 2)[1] for entry in results]
    assert "hw/physical" in ids
    assert status_of(results, "hw/physical") == "SKIP"
    assert "hw/card-reader" not in ids
    # The deferred list still runs — a guest device the kernel could not bind
    # is a real defect wherever it happens.
    assert "hw/deferred" in ids


def test_physical_checks_run_on_real_hardware(tmp_path):
    """Negative control for the VM gate."""
    node = tmp_path / "sys/kernel/debug/devices_deferred"
    node.parent.mkdir(parents=True)
    node.write_text("")

    results = run_check("run_hardware_checks", tmp_path, force_virt="0")
    ids = [entry.split("|", 2)[1] for entry in results]
    assert "hw/physical" not in ids
    assert "hw/card-reader" in ids
    assert "hw/pointer" in ids


# ---------------------------------------------------------------------------
# Wiring — a check module that exists but is never called is a silent skip.
# ---------------------------------------------------------------------------

def test_hardware_module_is_sourced_and_invoked_by_the_orchestrator():
    text = SMOKE_TEST_SH.read_text()
    assert "checks/hardware.sh" in text, "hardware.sh is never sourced"
    assert "run_hardware_checks" in text, "run_hardware_checks is never called"


def test_hardware_module_is_executable_and_syntactically_valid():
    proc = subprocess.run(["bash", "-n", str(HARDWARE_SH)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_orchestrator_still_parses_with_the_new_module_wired():
    proc = subprocess.run(["bash", "-n", str(SMOKE_TEST_SH)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_hardware_module_ships_in_the_forge_package():
    """The install glob must cover it, or the check exists only in the repo."""
    build_sh = (REPO_ROOT / "packages" / "desktop" / "forge" / "build.sh").read_text()
    assert "installer/smoke/checks/*.sh" in build_sh, (
        "forge/build.sh must install the checks directory by glob; a hand-listed "
        "set would silently omit this module"
    )
