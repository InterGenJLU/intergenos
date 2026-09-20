# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The kernel console reaches the serial port without putting a login prompt on it.

WHY THIS FILE EXISTS.

On 2026-09-20 the remote-login surface was measured on an installed machine
(CS-12): a login prompt was running on the serial port while the kernel wrote
its messages to the screen only, so a serial session could log in and see none
of them. It was decided the same day that the shipped command line names the
serial port ahead of the screen. The kernel takes console= more than once, sends
its messages to every console named, and makes the LAST one /dev/console — so
"ttyS0 first, tty0 last" adds the serial port without moving anything away from
the screen.

THE SIDE-EFFECT THAT DECIDED THE THIRD PARAMETER. systemd's getty generator
starts a login prompt (serial-getty@ttyS0) on every serial kernel console it
finds active, on its own, at every boot. Naming ttyS0 would therefore put a
login prompt on the serial port of every installed machine — the class the
installer stopped on 2026-09-14 (R001.3 row 13: a serial login prompt only when
the installer itself ran over a serial console). systemd.getty_auto=0 switches
that automatic instantiation off. The installer's decision stays the only source
of a serial login prompt; the screen's prompt, getty@tty1, comes from systemd's
own preset and not from the generator.

WHAT THESE TESTS PIN.

1. Exactly one shipped fragment names a kernel console, so the order below is
   the order the kernel sees.
2. The serial port is named, the screen is named, and the screen is named last.
3. Whenever a serial console is named, the getty generator's switch is off.
4. The fragment is declared by its package, so a build that failed to ship it
   would not pass verification.

Nothing here reaches a serial port: the real proof is a boot with a cable
attached, which is the CS-12 re-measure on installed hardware.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BASE_FILES = REPO / "packages" / "core" / "intergenos-base-files"
CMDLINE_D = BASE_FILES / "files" / "etc" / "kernel" / "cmdline.d"
RECIPE = BASE_FILES / "package.yml"
FRAGMENT = CMDLINE_D / "20-console.conf"


def parameters_of(conf: Path) -> list[str]:
    """The parameters one fragment contributes, comments and blank lines stripped —
    the same reading the linux-kernel post-install hook makes when it builds the image."""
    parts = []
    for raw in conf.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts.extend(line.split())
    return parts


def shipped_parameters() -> list[str]:
    parts = []
    for conf in sorted(CMDLINE_D.glob("*.conf")):
        parts.extend(parameters_of(conf))
    return parts


def consoles(params: list[str]) -> list[str]:
    """Every console= value, in command-line order. Pure, so the controls can feed
    it a command line that must fail."""
    return [p.split("=", 1)[1] for p in params if p.startswith("console=")]


def getty_auto(params: list[str]):
    hits = [p.split("=", 1)[1] for p in params if p.startswith("systemd.getty_auto=")]
    return hits[-1] if hits else None


# --- the invariant ---------------------------------------------------------

def test_exactly_one_fragment_names_a_kernel_console():
    naming = [c.name for c in sorted(CMDLINE_D.glob("*.conf")) if consoles(parameters_of(c))]
    assert naming == [FRAGMENT.name], (
        f"fragments naming console=: {naming}. The kernel takes the LAST console= as "
        "/dev/console, so two fragments naming consoles would make the winner a matter "
        "of filename sort order that nothing states."
    )


def test_the_serial_port_is_named_ahead_of_the_screen():
    order = consoles(shipped_parameters())
    assert any(c.startswith("ttyS0,115200") for c in order), (
        f"no serial console on the shipped command line: {order}. A serial session sees "
        "no kernel messages — the state CS-12 measured on 2026-09-20."
    )
    assert order and order[-1] == "tty0", (
        f"the LAST console named is {order[-1] if order else None!r}, not tty0. The last "
        "console= becomes /dev/console; naming the serial port last would move init's and "
        "the panic path's output off the screen."
    )


def test_a_serial_console_never_ships_without_the_getty_generator_switched_off():
    params = shipped_parameters()
    if not any(c.startswith("ttyS") for c in consoles(params)):
        return
    assert getty_auto(params) == "0", (
        f"systemd.getty_auto is {getty_auto(params)!r} while a serial console is named. "
        "systemd's getty generator would start serial-getty@ttyS0 on every machine at "
        "boot, outside the installer's row-13 decision and invisible to the installed gate."
    )


def test_the_fragment_is_declared_by_its_package():
    recipe = RECIPE.read_text(encoding="utf-8")
    assert re.search(r"^\s*-\s*/etc/kernel/cmdline\.d/20-console\.conf\b", recipe, re.M), (
        "20-console.conf is not in intergenos-base-files' verify_paths: a build that "
        "dropped it would pass verification and the serial console would vanish silently."
    )


# --- controls: the checks can fail --------------------------------------------

def test_control_the_order_check_rejects_the_screen_named_first():
    order = consoles(["console=tty0", "console=ttyS0,115200"])
    assert order[-1] != "tty0"


def test_control_the_switch_check_sees_an_absent_switch():
    assert getty_auto(["console=ttyS0,115200", "console=tty0"]) is None
