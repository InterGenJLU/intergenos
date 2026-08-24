# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""GATE — the two promised keyboard shortcuts are bound on the INSTALLED system.

WHAT COMPOSITION PROPERTY THIS CATCHES, AND WHY THE SOURCE-TREE GATE IS NOT
ENOUGH. tests/preflight/test_desktop_keybindings_are_shipped.py reads the two
files in the repository and proves they SAY the right thing. It cannot prove
that the gschema override was compiled into /usr/share/glib-2.0/schemas, that
the dconf fragment was installed into /etc/dconf/db/local.d, or that
``dconf update`` ever ran to compile /etc/dconf/db/local from it. Every one of
those is a build- and install-time step outside the two files, and if any of
them is missed the image ships with the same defect the source gate was written
to catch — two keys that do nothing, documented as working. That gap is exactly
what this tier exists for.

WHAT "THE BINDING IS PRESENT" MEANS HERE — READ THIS BEFORE CHANGING THE GATE.
It means A FRESH ACCOUNT ON THIS IMAGE GETS BOTH KEYS. It deliberately does NOT
mean "the account running the test currently has both keys". The shipped
fragment says in its own text that the binding is NOT LOCKED: a user who wants
Ctrl+Alt+T for something else rebinds it and their user db wins, which is a
supported thing to do. A gate that read the invoking account's effective value
would fail on a machine whose owner exercised that freedom, and would pass on a
machine where the image shipped nothing but the user had bound the keys by hand.
Both verdicts would be about the wrong thing.

So every read below is taken through a dconf profile this test writes, naming
the real system database and NO user layer:

    system-db:local        -> /etc/dconf/db/local, the compiled image default

CAUTION — DO NOT "FIX" THAT BY POINTING IT AT A SCRATCH DATABASE. During the authoring
of this change a scratch profile that named ``system-db:local`` silently
answered from the real /etc/dconf/db/local and looked like a successful
measurement of a not-yet-installed fragment. That is a hazard when the intent is
to measure a build artifact. Here the intent is the opposite: the REAL installed
database is the subject, and naming it is correct.

HOW THIS GATE PROVES IT MEASURED SOMETHING (the controls are not decoration).
A reader that always answers, or that answers from a schema default, would
report green on a machine that ships nothing. Two controls are therefore run
against the same instrument, in the same way, in the same session:

  * an UNSHIPPED PATH control — the same relocatable schema is read at a
    custom-keybinding path the image does not ship, and must come back empty. An
    instrument that answered there is not resolving per path.
  * a NO-SYSTEM-DB control — the shipped binding is read again through a profile
    with no system database in it at all, and must come back empty. This is what
    proves the real answer came out of /etc/dconf/db/local, i.e. out of the
    image, rather than from a schema default or a leftover user db.

EXPECTED RED ON AN R001.1 SYSTEM AS SHIPPED. R001.1 binds neither key; that
absence is the defect this change closes. The gate turns green on an image built
from the branch that adds them.

Nothing here writes outside a temporary directory, reads the network, or needs
privilege.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

MEDIA_KEYS_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_SCHEMA = f"{MEDIA_KEYS_SCHEMA}.custom-keybinding"
WM_KEYBINDINGS_SCHEMA = "org.gnome.desktop.wm.keybindings"

# The image's own default layer, compiled from /etc/dconf/db/local.d.
SYSTEM_DB_LINE = "system-db:local"

CUSTOM0_PATH = f"/{MEDIA_KEYS_SCHEMA.replace('.', '/')}/custom-keybindings/custom0/"
UNSHIPPED_PATH = f"/{MEDIA_KEYS_SCHEMA.replace('.', '/')}/custom-keybindings/custom99/"

EXPECTED_TERMINAL_BINDING = "<Control><Alt>t"
EXPECTED_SHOW_DESKTOP_BINDING = "<Super>d"


def _unquote(raw: str) -> str:
    """Turn a GVariant string literal as gsettings prints it into its text."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    return raw


class _Reader:
    """Reads gsettings through a dconf profile written for this test.

    ``db_line`` is the single database line that profile contains.
    ``SYSTEM_DB_LINE`` names the real installed database and is what the gate
    itself measures; ``None`` writes an empty profile and gives the
    no-system-db control — the same command, the same schemas, the same
    session, with only the image's own layer removed. The parameter exists so
    the instrument can also be pointed at a database built from a branch's
    artifacts for a positive control, which is how it is shown to DETECT a
    binding rather than only to report its absence. It is a constructor
    argument and never an environment variable: nothing outside this file can
    redirect what the gate reads.
    """

    def __init__(self, root: Path, *, db_line: str | None) -> None:
        self.root = root
        self.db_line = db_line
        self.profile = root / "dconf-profile"
        self.profile.write_text(f"{db_line}\n" if db_line else "", encoding="utf-8")
        self.home = root / "home"
        (self.home / ".config").mkdir(parents=True, exist_ok=True)

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update({
            "DCONF_PROFILE": str(self.profile),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_RUNTIME_DIR": str(self.root / "runtime"),
            "LC_ALL": "C",
        })
        # A session bus address inherited from the invoking desktop would let
        # dconf talk to the running session's writer. Reads must come from the
        # databases this profile names and from nothing else.
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)
        (self.root / "runtime").mkdir(exist_ok=True)
        return env

    def get(self, schema: str, key: str, path: str | None = None) -> str:
        target = f"{schema}:{path}" if path else schema
        proc = subprocess.run(
            ["gsettings", "get", target, key],
            env=self._env(), capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            pytest.fail(
                f"`gsettings get {target} {key}` exited {proc.returncode} on this "
                "installed system, so this gate measured nothing. That is a "
                "failure and not a skip: the tier cannot report on a machine it "
                f"cannot read.\n  stdout: {proc.stdout!r}\n  stderr: {proc.stderr!r}"
            )
        return proc.stdout.strip()


@pytest.fixture(scope="module")
def gsettings_binary() -> str:
    found = shutil.which("gsettings")
    if not found:
        pytest.fail(
            "gsettings is not on PATH on this installed system. Every desktop "
            "default in this image is carried by GLib settings, so a machine "
            "without it cannot be evaluated — reported as a failure rather than "
            "a skip, because 'I could not check' must never read as 'I checked'."
        )
    return found


@pytest.fixture(scope="module")
def image_reader(gsettings_binary, tmp_path_factory) -> _Reader:
    """Reads the image's own defaults: system database, no user layer."""
    return _Reader(tmp_path_factory.mktemp("keybindings-image"), db_line=SYSTEM_DB_LINE)


@pytest.fixture(scope="module")
def no_system_db_reader(gsettings_binary, tmp_path_factory) -> _Reader:
    """The control instrument: identical, with the image's layer removed."""
    return _Reader(tmp_path_factory.mktemp("keybindings-control"), db_line=None)


def test_the_system_dconf_database_is_compiled_and_present():
    """The image's default layer exists at all.

    /etc/dconf/db/local is produced by `dconf update` from local.d at build or
    install time. If the fragment was installed but the database was never
    recompiled, every read below would answer with the pre-existing database and
    the miss would be invisible.
    """
    database = Path("/etc/dconf/db/local")
    fragment_dir = Path("/etc/dconf/db/local.d")
    assert fragment_dir.is_dir(), (
        f"{fragment_dir} does not exist, so the image ships no system-wide dconf "
        "defaults at all.")
    assert database.is_file(), (
        f"{database} does not exist. The fragments in {fragment_dir} are source "
        "text; until `dconf update` compiles them into this file nothing reads "
        "them, and every default they carry is absent from the running system.")
    fragments = sorted(p for p in fragment_dir.iterdir() if p.is_file())
    assert fragments, f"{fragment_dir} is empty"
    stale = [p.name for p in fragments if p.stat().st_mtime > database.stat().st_mtime]
    assert not stale, (
        "the compiled database is OLDER than these fragments, so their contents "
        f"are not in it: {', '.join(stale)}. `dconf update` did not run after "
        "they were installed.")


def test_show_desktop_is_bound_on_the_installed_system(image_reader):
    value = image_reader.get(WM_KEYBINDINGS_SCHEMA, "show-desktop")
    assert EXPECTED_SHOW_DESKTOP_BINDING in value, (
        f"show-desktop is {value} on this image, not a list containing "
        f"{EXPECTED_SHOW_DESKTOP_BINDING!r}.\n"
        "GNOME leaves show-desktop unbound in its own defaults, so this reads as "
        "the stock behaviour: the key does nothing, while the user documentation "
        "and the first-run page both say it shows the desktop. The binding is "
        "shipped in config/gsettings/92_intergenos-desktop.gschema.override, "
        "which has to be installed into /usr/share/glib-2.0/schemas and compiled "
        "by glib-compile-schemas for this read to see it.")


def test_the_terminal_binding_is_listed_on_the_installed_system(image_reader):
    listed = image_reader.get(MEDIA_KEYS_SCHEMA, "custom-keybindings")
    assert CUSTOM0_PATH in listed, (
        f"the image's custom-keybindings list is {listed}, which does not contain "
        f"{CUSTOM0_PATH!r}. A binding section that nothing points at is never "
        "read, so the terminal key does nothing. The list is shipped in "
        "/etc/dconf/db/local.d/03-intergenos-keybindings.")


def test_the_terminal_binding_is_bound_named_and_runs_a_command(image_reader):
    binding = _unquote(image_reader.get(CUSTOM_SCHEMA, "binding", CUSTOM0_PATH))
    name = _unquote(image_reader.get(CUSTOM_SCHEMA, "name", CUSTOM0_PATH))
    command = _unquote(image_reader.get(CUSTOM_SCHEMA, "command", CUSTOM0_PATH))
    assert binding == EXPECTED_TERMINAL_BINDING, (
        f"the shipped binding at {CUSTOM0_PATH} is {binding!r}, not "
        f"{EXPECTED_TERMINAL_BINDING!r}")
    assert name, (
        f"the binding at {CUSTOM0_PATH} has no name, so it appears unnamed in "
        "the Settings keyboard panel and a user cannot tell what it is")
    assert command, (
        f"the binding at {CUSTOM0_PATH} runs no command, so the key is listed "
        "and still does nothing")


def test_the_bound_command_is_an_executable_this_image_carries(image_reader):
    command = _unquote(image_reader.get(CUSTOM_SCHEMA, "command", CUSTOM0_PATH))
    assert command, "no command to check; see the binding test above"
    binary = command.split()[0]
    assert binary.startswith("/"), (
        f"the bound command {command!r} is not an absolute path, so what the key "
        "runs depends on the session's PATH rather than on the image")
    path = Path(binary)
    assert path.is_file(), (
        f"the terminal key runs {binary}, which is not present on this installed "
        "system. A key bound to a binary the image does not carry is the same "
        "defect as a key bound to nothing.")
    assert os.access(binary, os.X_OK), f"{binary} is present but not executable"


def test_control_the_reader_returns_empty_for_a_path_the_image_does_not_ship(
        image_reader):
    """Control: the instrument resolves per path instead of always answering."""
    command = _unquote(image_reader.get(CUSTOM_SCHEMA, "command", UNSHIPPED_PATH))
    assert command == "", (
        f"reading {UNSHIPPED_PATH}, which the image does not ship, produced "
        f"{command!r}. The reader is not resolving the path it was given, so "
        "none of its other answers are evidence about a particular binding.")


def test_control_the_binding_disappears_when_the_image_layer_is_removed(
        no_system_db_reader):
    """Control: the real answer comes from the image's database, not elsewhere.

    Same command, same schemas, same machine, with only ``system-db:local``
    removed from the profile. If the terminal binding still answered here it
    would be coming from a schema default or a user database, and the green
    above would say nothing about what this image ships.
    """
    command = _unquote(
        no_system_db_reader.get(CUSTOM_SCHEMA, "command", CUSTOM0_PATH))
    assert command == "", (
        f"with the image's system database removed from the profile, "
        f"{CUSTOM0_PATH} still answers with {command!r}. The value is therefore "
        "not coming from /etc/dconf/db/local, and a green result above would not "
        "have been evidence that the image ships the binding.")
