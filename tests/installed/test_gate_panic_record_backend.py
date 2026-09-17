# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""GATE — on the INSTALLED system a kernel panic has somewhere to write.

WHAT THIS CATCHES THAT THE SOURCE-TREE TESTS CANNOT. tests/preflight proves the
shipped files state the designed parameters, and that is all a source tree can
prove. Whether a real machine ends up with a working recorder depends on things
that exist only after an install: whether those parameters reached the signed
image's command line, whether the kernel could place the reserved region, and
whether the module that owns the region actually registered as the pstore
backend. Every one of those can fail quietly, and the failure is invisible until
the machine dies and cannot say why — which is exactly what happened on
2026-09-16, when a panic on a machine whose configuration "looked applied" wrote
nothing at all.

THE MEASUREMENTS ARE THE KERNEL'S OWN ANSWERS. The backend name is read from
/sys/module/pstore/parameters/backend, the registration and the placed region
from the boot log, the parameters from /proc/cmdline. None of them is read from
the shipped configuration files: "the file says so" is what the September
failure already satisfied.

THIS GATE IS RED ON EVERY R001.2-03 MACHINE UNTIL THE R001.3 INSTALL. That is
the gate working. The configuration reaches a machine inside the signed kernel
image built at install time, so a machine installed before it has no reservation,
no registration and no recorder, and every arm here says so by name. It goes
green on an R001.3 install.

RUNNING STATE AND NEXT BOOT ARE MEASURED SEPARATELY, and the reason is a
measurement. On 2026-09-16 a machine used for the panic-record proof was
restored — the temporary image carrying the reservation was removed from its
disk — and it kept running the kernel that image had booted. Every running-state
arm here read green on it while its next boot would have had no recorder at all.
A machine's current ability to record a panic and its configured ability to keep
recording one are different facts, so the last arm reads the shipped
configuration from disk and says which of the two is missing.

CONTROLS. Each reader is fed a boot log that does not satisfy it and must return
the negative — including the log of a machine whose pstore registered the OTHER
backend, because "some backend registered" is not what this gate is about.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

BACKEND_PARAM = Path("/sys/module/pstore/parameters/backend")
REQUIRED_BACKEND = "ramoops"
ARCHIVER = "systemd-pstore.service"

# The kernel's own words, taken from a boot log of a machine where this was
# proved working (2026-09-16), never from memory:
#   pstore: Registered ramoops as persistent store backend
#   ramoops: using 0x100000@0x711d00000, ecc: 0
_REGISTERED = re.compile(r"pstore: Registered (\S+) as persistent store backend")
_REGION = re.compile(r"ramoops: using 0x[0-9a-f]+@0x[0-9a-f]+")

# The two command-line parameters without which nothing above can happen: the
# reservation the kernel makes, and the backend named explicitly rather than
# left to a load-order race with the firmware backend.
_CMDLINE_REQUIRED = ("reserve_mem=", "pstore.backend=ramoops", "ramoops.mem_name=")

# The two shipped files that make the running state above survive a reboot: the
# command-line fragment merged into the next signed image, and the module-load
# file that loads the recorder at every boot.
_SHIPPED_CONFIGURATION = (
    Path("/etc/kernel/cmdline.d/30-panic-record.conf"),
    Path("/etc/modules-load.d/panic-record-ramoops.conf"),
)


def _boot_log() -> str:
    """The running kernel's own log for THIS boot.

    journalctl is used rather than dmesg because dmesg's buffer is not readable
    by an unprivileged account on this system, and a gate that needs privilege
    to answer is a gate that gets skipped.
    """
    result = subprocess.run(
        ["journalctl", "-k", "-b", "0", "--no-pager"],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        pytest.fail(
            "the boot log could not be read, so nothing about the panic-record "
            f"path was measured: journalctl exited {result.returncode}; "
            f"{result.stderr.strip()[:300]}"
        )
    return result.stdout


def registered_backend(log_text: str):
    """Which backend pstore registered on this boot, or None. Pure, so the
    controls can feed it a log that must not satisfy it."""
    match = _REGISTERED.search(log_text)
    return match.group(1) if match else None


def region_was_placed(log_text: str) -> bool:
    """True when the recorder reported the region it actually got. Pure."""
    return bool(_REGION.search(log_text))


def test_the_recording_backend_is_ramoops():
    assert BACKEND_PARAM.is_file(), (
        f"{BACKEND_PARAM} does not exist, so pstore has no backend at all and a "
        "panic on this machine leaves no record"
    )
    backend = BACKEND_PARAM.read_text().strip()
    assert backend == REQUIRED_BACKEND, (
        f"the panic recorder is {backend!r}, not {REQUIRED_BACKEND!r}. The "
        "RAM-backed recorder is the one this system configures, because the "
        "firmware backend was measured writing nothing in panic context on "
        "2026-09-16"
    )


def test_the_boot_log_carries_the_recorders_registration():
    backend = registered_backend(_boot_log())
    assert backend == REQUIRED_BACKEND, (
        f"the boot log records {backend!r} as the registered pstore backend. "
        "A parameter that was accepted is not a recorder that started: this "
        "line is the kernel saying the recorder is live"
    )


def test_the_reserved_region_was_actually_placed():
    assert region_was_placed(_boot_log()), (
        "the recorder never reported the memory region it took. reserve_mem is "
        "best effort: when the kernel cannot place the region the recorder does "
        "not start, and a panic has nowhere to write"
    )


def test_the_command_line_carries_the_parameters():
    cmdline = Path("/proc/cmdline").read_text()
    missing = [p for p in _CMDLINE_REQUIRED if p not in cmdline]
    assert not missing, (
        "the running kernel's command line does not carry: "
        + ", ".join(missing)
        + ". On this system the command line lives inside the signed kernel "
        "image, so a configuration file that was installed but never built into "
        "an image reaches nothing"
    )


def test_the_archiver_that_keeps_the_record_is_enabled():
    """The record is in RAM until something moves it to disk."""
    result = subprocess.run(
        ["systemctl", "is-enabled", ARCHIVER],
        capture_output=True, text=True, timeout=60,
    )
    state = result.stdout.strip()
    assert state in ("enabled", "enabled-runtime", "static", "indirect"), (
        f"{ARCHIVER} is {state!r}: the panic record would sit in the reserved "
        "region until the next panic overwrote it, and never reach "
        "/var/lib/systemd/pstore"
    )


def test_the_configuration_that_produces_this_is_on_disk():
    """The next boot, as distinct from this one.

    Every arm above reads the RUNNING kernel. A machine can run a kernel whose
    image carried the reservation and no longer have the configuration that
    would put it in the next image — measured on exactly that machine on
    2026-09-16 — and it would then lose the recorder at its next reboot with
    nothing about the running system saying so.
    """
    missing = [str(p) for p in _SHIPPED_CONFIGURATION if not p.is_file()]
    assert not missing, (
        "this machine is missing the configuration that puts the panic recorder "
        "in place at boot: " + ", ".join(missing) + ". Whatever the running "
        "kernel does today, the next boot has no recorder"
    )


# --- controls: a reader that cannot fail is not a reader --------------------

def test_the_registration_reader_fails_on_a_log_without_the_line():
    assert registered_backend("nothing here about persistent storage\n") is None


def test_a_different_backend_does_not_satisfy_the_registration_reader():
    efi_log = (
        "Sep 16 14:30:26 host kernel: pstore: Registered efi_pstore as "
        "persistent store backend\n"
    )
    assert registered_backend(efi_log) == "efi_pstore"
    assert registered_backend(efi_log) != REQUIRED_BACKEND


def test_the_region_reader_fails_when_no_region_was_reported():
    assert not region_was_placed(
        "ramoops: using module parameters\n"
        "pstore: Using crash dump compression: deflate\n"
    )


def test_the_region_reader_accepts_the_line_a_working_machine_printed():
    assert region_was_placed("ramoops: using 0x100000@0x711d00000, ecc: 0\n")
