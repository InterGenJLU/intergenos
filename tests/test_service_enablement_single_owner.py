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

AGREEMENT IS NOT ALLOWED EITHER, and that is the second thing this file
measures. It originally accepted a recipe that enabled a unit the preset also
enabled, on the reasoning that duplication is not disagreement. That reasoning
was wrong, and the reason is the moment the two artifacts are applied. The
preset pass runs once, at image build and at the end of an install. A recipe's
post_install runs then AND on every subsequent upgrade of that package, because
pkm fires the sealed post_install hook on an upgrade too. So a redundant enable
is not a harmless duplicate of the preset's decision — it is a re-application of
it, months later, over whatever the user has since chosen. Four recipes were in
that state: pkm's update timer, bluez, forge-tui and the backup engine. Each
would silently turn its unit back on for a user who had turned it off, and this
test could not see any of them, because agreement is the shape of that defect.

The rule is therefore the simple one: enablement of a system unit is decided in
the preset files and nowhere else, so a recipe or the image script making any
such call at all is the finding — whether it agrees with the policy or not. The
99- catch-all `disable *` means the policy resolves EVERY system unit, so there
is no system unit whose enablement a recipe could own.

`systemctl --global` calls act on USER units, which system presets do not
govern; they are outside this rule and are excluded deliberately, which the
harness below proves rather than assumes.

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


def _splice_continuations(text):
    r"""Join backslash-continued shell lines into one logical line.

    Found 2026-08-19 while red-firing the agreement rule: the backup engine's
    recipe called `systemctl enable chronicled.service \` with three timers on
    the continuation lines, and this scanner reported ONLY chronicled.service.
    Units two onwards were invisible, so a contradiction hidden on a
    continuation line would have been waved through by a gate that looked like
    it had read the call. A scanner that reads part of a statement does not
    report less; it reports the wrong thing.
    """
    out, buf = [], ""
    for line in text.splitlines():
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
            continue
        out.append(buf + line)
        buf = ""
    if buf:
        out.append(buf)
    return "\n".join(out)


def _units_named_by(text):
    """The (verb, unit) pairs a shell fragment calls systemctl with."""
    out = []
    for line in _splice_continuations(text).splitlines():
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

    def test_the_scanner_reads_the_real_recipes(self):
        """The invariant below now expects ZERO calls, so a scanner that read
        nothing at all would pass it while proving nothing.

        This control was originally `assert calls` — it asserted the tree still
        CONTAINED the defect, so it stopped working the moment the defect was
        fixed. What it should have pinned is that the scanner reaches the real
        files. It asserts the parser found real post_install bodies, and that a
        call injected into a real recipe's real body is seen.
        """
        bodies = {
            f"{b.parent.parent.name}/{b.parent.name}": _post_install_body(b)
            for b in sorted(PACKAGES.glob("*/*/build.sh"))
        }
        nonempty = {k: v for k, v in bodies.items() if v.strip()}
        assert len(nonempty) > 20, (
            f"only {len(nonempty)} post_install bodies parsed; the scanner is "
            f"not reaching the recipes")

        real = next(iter(nonempty.values()))
        injected = real + "\n    systemctl enable injected-probe.service\n"
        assert ("enable", "injected-probe.service") in _units_named_by(injected), (
            "the scanner did not see a call injected into a real recipe body")

    def test_the_global_exclusion_is_deliberate_and_still_load_bearing(self):
        """`systemctl --global` acts on USER units; system presets do not
        govern them, so they are excluded from this rule.

        The exclusion is asserted against the tree rather than trusted: the
        wireplumber recipe really does make three such calls, a plain text
        search finds them, and the scanner really does drop them. Without this,
        a regex that had quietly stopped matching anything would read exactly
        like a clean tree.
        """
        wp = PACKAGES / "desktop" / "wireplumber" / "build.sh"
        body = _post_install_body(wp)
        raw = [l.strip() for l in body.splitlines()
               if l.strip().startswith("systemctl enable")]
        assert len(raw) == 3, f"expected wireplumber's three --global calls, saw {raw}"
        assert all("--global" in l for l in raw), raw
        assert _units_named_by(body) == [], (
            f"the scanner should exclude --global user-unit calls, but returned "
            f"{_units_named_by(body)}")

    def test_the_scanner_reads_a_call_it_is_shown(self):
        found = _units_named_by(
            "post_install() {\n    systemctl enable example.service\n}\n"
        )
        assert ("enable", "example.service") in found, found

    def test_the_scanner_sees_every_unit_on_a_continued_line(self):
        """A backslash continuation used to hide every unit after the first.

        The real case that exposed it: the backup engine enabled its service
        and three timers across four lines, and this scanner reported one unit.
        Both directions are pinned — the continued form and the single-line
        form must yield the same set — so the splice cannot regress quietly.
        """
        continued = _units_named_by(
            "post_install() {\n"
            "    systemctl enable a.service \\\n"
            "        b.timer \\\n"
            "        c.timer\n"
            "}\n"
        )
        assert continued == [("enable", "a.service"), ("enable", "b.timer"),
                             ("enable", "c.timer")], continued
        single = _units_named_by("    systemctl enable a.service b.timer c.timer\n")
        assert continued == single, (continued, single)

    def test_the_scanner_ignores_a_commented_call(self):
        found = _units_named_by("    # systemctl enable example.service\n")
        assert found == [], found


# --------------------------------------------------------------------------
# The invariant.
# --------------------------------------------------------------------------

def test_no_recipe_or_image_script_decides_system_unit_enablement(tmp_path):
    """The invariant: the preset files decide enablement, and nothing else
    makes the call — agreeing or disagreeing.

    Disagreement was the original finding: an artifact turning a service on
    while the policy turned it off, whichever ran last winning, the tree saying
    both. Agreement turned out to be the same defect on a different timescale.
    The preset pass runs once per image build and once per install; a recipe's
    post_install runs on every upgrade as well, so a call that merely repeats
    the policy re-applies it over a choice the user has made since. That is why
    a redundant enable is reported here and not waved through.

    Each finding still carries what the policy resolves the unit to, because
    that is what tells a reader whether they are looking at a stale
    disagreement or a redundant repetition.
    """
    calls = _collect_calls()
    if not calls:
        return

    units = sorted({unit for _, _, unit in calls})
    resolved = _resolve(units, tmp_path)
    want = {"enable": "enabled", "disable": "disabled"}

    findings = []
    for src, verb, unit in calls:
        kind = ("REDUNDANT — the preset policy already resolves it this way, and "
                "this call re-applies that on every upgrade"
                if resolved[unit] == want[verb] else
                "CONTRADICTION — the preset policy resolves it the other way")
        findings.append(
            f"{src}: `systemctl {verb} {unit}` -> policy says {resolved[unit]} "
            f"({kind})")

    assert not findings, (
        "enablement of a system unit is decided in the preset files and nowhere "
        "else; these artifacts decide it too:\n  " + "\n  ".join(findings)
    )


@pytest.mark.parametrize("unit,expected", [
    ("cups.service", "disabled"),
    ("cups.socket", "disabled"),
    ("cups.path", "disabled"),
    ("avahi-daemon.service", "disabled"),
    ("avahi-daemon.socket", "disabled"),
    ("rtkit-daemon.service", "enabled"),
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
