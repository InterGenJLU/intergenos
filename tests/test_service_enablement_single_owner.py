# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Default service enablement is decided in the preset files and nowhere else.

WHY THIS EXISTS. Three kinds of artifact in this tree can turn a service on or
off: a package recipe's post_install, the disk-image script, and the systemd
preset files. Only the last of those is consulted at the moment that decides an
installed system, because both the image build and the installer finish by
running `systemctl preset-all`, which re-applies the preset policy over
whatever anything else did. When a recipe and the preset policy disagreed, the
tree therefore stated one default and shipped another, and which one a reader
believed depended on which file they happened to open.

Four units were in exactly that state before this test existed: cups.service,
avahi-daemon.service, rtkit-daemon.service and
NetworkManager-wait-online.service were enabled by their recipes and resolved
to disabled by the preset policy's catch-all. The disk-image script was a third
voice, disabling one of them and enabling two others after the preset pass had
already run and against a different filesystem root.

WHAT THIS MEASURES. Every `systemctl enable`/`systemctl disable` of a system
unit that survives in a recipe post_install or in the disk-image script is
resolved against the REAL preset engine — `systemctl --root <root> preset-all`,
the same command the installer runs — using the tree's own preset files. A call
whose verb disagrees with what the preset policy resolves that unit to is a
contradiction and fails here.

Agreement is allowed. A recipe that enables a unit the preset also enables is
redundant but not a contradiction, and this test does not object to it; the
defect being kept out is disagreement, not duplication.

The preset engine is never reimplemented. Glob matching, first-match-wins
across lexically sorted files and the `disable *` catch-all are all evaluated
by systemd itself against a temporary root, so this test cannot drift from the
resolver the build and the installer actually use.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGES = REPO_ROOT / "packages"
CREATE_IMAGE = REPO_ROOT / "scripts" / "create-image.sh"

# `systemctl <verb> <unit>`, with or without the masking suffixes that used to
# wrap these calls. --global calls act on user units, which system presets do
# not govern, so they are not resolvable here and are excluded by the pattern.
CALL_RE = re.compile(
    r"^\s*systemctl\s+(enable|disable)\s+((?:[A-Za-z0-9@_.\-]+(?:\s+|$))+)"
)


def _preset_files():
    """Every *.preset file the tree ships, by the basename systemd sorts on.

    Searched tree-wide, not under packages/ alone: a preset can be authored
    anywhere a recipe can reach. The backup engine's 90-chronicle.preset lives
    under assets/ and is installed into /usr/lib/systemd/system-preset/ by its
    recipe, and a packages/-only search silently missed it — which made this
    test report a contradiction for a unit whose preset it simply had not
    read. A search that cannot see a file it is meant to judge does not report
    nothing; it reports the wrong thing.
    """
    found = {}
    for p in sorted(REPO_ROOT.rglob("*.preset")):
        if ".git" in p.parts:
            continue
        found.setdefault(p.name, p)
    return found


def _units_named_by(text):
    """The (verb, unit) pairs a shell fragment calls systemctl with."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        m = CALL_RE.match(line)
        if not m:
            continue
        verb, rest = m.group(1), m.group(2)
        for unit in rest.split():
            if unit.startswith("-"):
                break
            if "." not in unit:
                unit += ".service"
            out.append((verb, unit))
    return out


def _post_install_body(build_sh):
    text = build_sh.read_text()
    m = re.search(r"(?m)^post_install\(\) \{\n(.*?)^\}\n", text, re.S)
    return m.group(1) if m else ""


def _collect_calls():
    """Every enablement call the tree still makes outside the preset files."""
    calls = []
    for build_sh in sorted(PACKAGES.glob("*/*/build.sh")):
        pkg = f"{build_sh.parent.parent.name}/{build_sh.parent.name}"
        for verb, unit in _units_named_by(_post_install_body(build_sh)):
            calls.append((pkg, verb, unit))
    for verb, unit in _units_named_by(CREATE_IMAGE.read_text()):
        calls.append(("scripts/create-image.sh", verb, unit))
    return calls


def _resolve(units, tmp_path):
    """Ask the real preset engine what the tree's preset files say about each
    unit. Returns {unit: "enabled"|"disabled"}."""
    root = tmp_path / "root"
    (root / "usr/lib/systemd/system").mkdir(parents=True)
    (root / "usr/lib/systemd/system-preset").mkdir(parents=True)
    (root / "etc/systemd/system").mkdir(parents=True)

    for name, src in _preset_files().items():
        shutil.copy(src, root / "usr/lib/systemd/system-preset" / name)

    # A stand-in unit per name. The preset verb for a unit depends on its NAME,
    # not its contents; an [Install] section is required for enable/disable to
    # mean anything at all.
    for unit in units:
        (root / "usr/lib/systemd/system" / unit).write_text(
            "[Unit]\nDescription=preset resolution stand-in\n"
            "[Service]\nExecStart=/bin/true\n"
            "[Install]\nWantedBy=multi-user.target\n"
        )

    subprocess.run(
        ["systemctl", "--root", str(root), "preset-all"],
        capture_output=True, text=True, check=True,
    )

    resolved = {}
    for unit in units:
        r = subprocess.run(
            ["systemctl", "--root", str(root), "is-enabled", unit],
            capture_output=True, text=True,
        )
        resolved[unit] = r.stdout.strip()
    return resolved


# --------------------------------------------------------------------------
# The harness, proven in both directions before anything relies on it.
# --------------------------------------------------------------------------

class TestTheHarness:

    def test_systemctl_root_preset_all_is_available(self):
        r = subprocess.run(["systemctl", "--version"],
                           capture_output=True, text=True)
        assert r.returncode == 0, "no systemctl on this host; this test cannot run"

    def test_the_preset_files_are_found(self):
        names = _preset_files()
        assert "80-intergenos-enable.preset" in names, names
        assert "99-intergenos-default-disable.preset" in names, names

    def test_a_preset_authored_outside_the_packages_tree_is_found(self):
        """The search once covered packages/ only and missed this one, then
        reported the unit it whitelists as a contradiction. Pinned so the
        blind spot cannot come back quietly."""
        names = _preset_files()
        assert "90-chronicle.preset" in names, sorted(names)
        assert "assets" in names["90-chronicle.preset"].parts, \
            names["90-chronicle.preset"]

    def test_a_whitelisted_unit_resolves_enabled(self, tmp_path):
        """True positive for the enabled direction."""
        assert _resolve(["bluetooth.service"], tmp_path)["bluetooth.service"] \
            == "enabled"

    def test_an_unlisted_unit_resolves_disabled(self, tmp_path):
        """True positive for the disabled direction — the catch-all works, so a
        `disabled` verdict below is a measurement and not a silent failure."""
        assert _resolve(["no-such-invented-unit.service"], tmp_path)[
            "no-such-invented-unit.service"] == "disabled"

    def test_calls_are_actually_found_in_the_tree(self):
        """An empty call list would make the contradiction test pass by
        finding nothing. Assert the scanner still sees real calls."""
        calls = _collect_calls()
        assert calls, "the scanner found no systemctl calls at all"

    def test_the_scanner_reads_a_call_it_is_shown(self):
        found = _units_named_by(
            "post_install() {\n    systemctl enable example.service\n}\n"
        )
        assert ("enable", "example.service") in found, found

    def test_the_scanner_ignores_a_commented_call(self):
        found = _units_named_by("    # systemctl enable example.service\n")
        assert found == [], found


# --------------------------------------------------------------------------
# The invariant.
# --------------------------------------------------------------------------

def test_no_recipe_or_image_script_contradicts_the_preset_policy(tmp_path):
    """The defect this whole change set exists to remove: an artifact that
    turns a service on while the preset policy turns it off, or the reverse.
    Whichever ran last won, and the tree said both."""
    calls = _collect_calls()
    units = sorted({unit for _, _, unit in calls})
    resolved = _resolve(units, tmp_path)

    want = {"enable": "enabled", "disable": "disabled"}
    contradictions = [
        f"{src}: `systemctl {verb} {unit}` but the preset policy resolves "
        f"{unit} to {resolved[unit]}"
        for src, verb, unit in calls
        if resolved[unit] != want[verb]
    ]
    assert not contradictions, (
        "these artifacts state a default the preset policy does not:\n  "
        + "\n  ".join(contradictions)
    )


@pytest.mark.parametrize("unit,expected", [
    ("cups.service", "disabled"),
    ("cups.socket", "disabled"),
    ("cups.path", "disabled"),
    ("avahi-daemon.service", "disabled"),
    ("avahi-daemon.socket", "disabled"),
    ("rtkit-daemon.service", "disabled"),
    ("NetworkManager.service", "enabled"),
    ("NetworkManager-wait-online.service", "enabled"),
    ("bluetooth.service", "enabled"),
    ("gdm.service", "enabled"),
])
def test_each_decided_unit_resolves_the_way_its_entry_says(unit, expected,
                                                           tmp_path):
    """Every unit the preset files carry a written decision for resolves to
    what that decision says. Pins the four this change set decided, plus the
    two whose enablement was proven preset-owned when a duplicate elsewhere was
    removed."""
    assert _resolve([unit], tmp_path)[unit] == expected


def test_the_disk_image_script_makes_no_enablement_decision():
    """The image script populates a different filesystem root AFTER the preset
    pass has run against the chroot, so anything it enables or disables there
    silently diverges from the ISO and from every installed system built from
    the same tree. It states no default at all now."""
    calls = _units_named_by(CREATE_IMAGE.read_text())
    assert calls == [], f"scripts/create-image.sh still decides: {calls}"
