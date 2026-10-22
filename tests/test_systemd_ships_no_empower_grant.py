# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""systemd ships no polkit grant for the empower group, and no empower group.

systemd 259 installs src/run/empower.rules whenever polkit support is built: a
rule that answers YES to every polkit action, without a prompt, for any member
of the group "empower". sysusers.d/basic.conf declares the group, and run0
--empower is the only thing that puts a process in it (it skips the group when
the name does not resolve). The recipe's drop_empower_grant takes both out of
the staged payload, and do_install calls it after ninja install.

The function under test is the recipe's own, extracted from build.sh and run on
a scratch root seeded with systemd 259.1's own text for the three files it
touches or must leave alone: the empower rule, the systemd-networkd rule the
package keeps, and the rendered basic.conf.

The positive control is the rule the package keeps: the same read that finds no
empower rule afterwards finds the networkd rule, byte for byte, so an empty
result is a measurement rather than a directory the read never looked in.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_SH = REPO_ROOT / "packages" / "core" / "systemd" / "build.sh"

# systemd 259.1, src/run/empower.rules, verbatim.
EMPOWER_RULES = """\
// Allow all actions for users who are in the "empower" group. Users are added to the
// "empower" group by running run0 --empower.

polkit.addRule(function(action, subject) {
    if (subject.isInGroup("empower")) {
        return polkit.Result.YES;
    }
});
"""

# systemd 259.1, rules.d/systemd-networkd.rules, verbatim: a rule this package
# keeps.
NETWORKD_RULES = """\
// This file is part of systemd.
// See systemd-networkd.service(8) and polkit(8) for more information.

// Allow systemd-networkd to set timezone, get product UUID,
// and transient hostname
polkit.addRule(function(action, subject) {
    if ((action.id == "org.freedesktop.hostname1.set-hostname" ||
         action.id == "org.freedesktop.hostname1.get-product-uuid" ||
         action.id == "org.freedesktop.timedate1.set-timezone") &&
        subject.user == "systemd-network") {
        return polkit.Result.YES;
    }
});
"""

# The group declarations of systemd 259.1's sysusers.d/basic.conf as this
# recipe renders them (the soft-static gids unset), around the empower line.
BASIC_HEAD = """\
# The superuser
g root    0       -            -
u root    0:0     "Super User" /root

# Administrator group: can *see* more than normal users
g adm     -     -            -

# Administrator groups: can *do* more than normal users
g wheel   -     -            -
"""
EMPOWER_LINE = "g empower -     -            -\n"
BASIC_TAIL = """\

# Access to shared database of users on the system
g utmp    -     -            -
"""


def _function_text():
    text = BUILD_SH.read_text()
    start = text.index("drop_empower_grant() {")
    return text[start:text.index("\n}\n", start) + 3]


def _seed(root: Path, *, rule_name="empower.rules"):
    rules = root / "usr/share/polkit-1/rules.d"
    rules.mkdir(parents=True)
    (rules / rule_name).write_text(EMPOWER_RULES)
    (rules / "systemd-networkd.rules").write_text(NETWORKD_RULES)
    sysusers = root / "usr/lib/sysusers.d"
    sysusers.mkdir(parents=True)
    (sysusers / "basic.conf").write_text(BASIC_HEAD + EMPOWER_LINE + BASIC_TAIL)
    return rules, sysusers / "basic.conf"


def _drop(root: Path):
    return subprocess.run(
        ["bash", "-c", "set -e\n" + _function_text() + '\ndrop_empower_grant "$1"\n', "_", str(root)],
        capture_output=True, text=True, check=False, timeout=30,
    )


def test_the_rule_and_the_group_line_are_gone_and_nothing_else_changed(tmp_path):
    rules, basic = _seed(tmp_path)
    result = _drop(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(p.name for p in rules.iterdir()) == ["systemd-networkd.rules"]
    assert (rules / "systemd-networkd.rules").read_text() == NETWORKD_RULES
    assert basic.read_text() == BASIC_HEAD + BASIC_TAIL


def test_positive_control_the_read_that_finds_no_empower_rule_finds_the_kept_rule(tmp_path):
    rules, _ = _seed(tmp_path)
    assert _drop(tmp_path).returncode == 0
    naming_empower = subprocess.run(
        ["grep", "-rlwi", "empower", str(rules)], capture_output=True, text=True, check=False)
    naming_a_rule = subprocess.run(
        ["grep", "-rl", "polkit.addRule", str(rules)], capture_output=True, text=True, check=False)
    assert naming_empower.returncode == 1 and naming_empower.stdout == ""
    assert naming_a_rule.stdout.strip() == str(rules / "systemd-networkd.rules")


def test_a_renamed_empower_rule_fails_the_build_loudly(tmp_path):
    """Upstream renaming the file must not ship it: the strip names the file,
    and a rule still naming the group afterwards is an error."""
    _seed(tmp_path, rule_name="60-empower.rules")
    result = _drop(tmp_path)
    assert result.returncode != 0
    assert "still names the empower group" in result.stderr


def test_a_group_line_with_other_spacing_and_a_gid_is_still_removed(tmp_path):
    _, basic = _seed(tmp_path)
    basic.write_text(BASIC_HEAD + "g  empower\t998 - -\n" + BASIC_TAIL)
    result = _drop(tmp_path)
    assert result.returncode == 0, result.stderr
    assert basic.read_text() == BASIC_HEAD + BASIC_TAIL


def test_a_group_line_the_strip_does_not_recognise_fails_the_build_loudly(tmp_path):
    """A declaration the strip cannot remove must not ship: the check after the
    strip reads the group name at the end of the line too."""
    _, basic = _seed(tmp_path)
    basic.write_text(BASIC_HEAD + "g empower\n" + BASIC_TAIL)
    result = _drop(tmp_path)
    assert result.returncode != 0
    assert "still declares the empower group" in result.stderr


def test_do_install_calls_it_after_the_payload_is_staged():
    text = BUILD_SH.read_text()
    start = text.index("do_install() {")
    body = text[start:text.index("\n}\n", start)]
    assert 'DESTDIR="$DESTDIR" ninja install' in body
    assert 'drop_empower_grant "$DESTDIR"' in body
    assert body.index('drop_empower_grant "$DESTDIR"') > body.index('DESTDIR="$DESTDIR" ninja install')
