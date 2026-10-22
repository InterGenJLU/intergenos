"""The live account's bus policy, and its polkit rule for the installer
backend, are created only in the live boot overlay."""

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def test_installed_forge_policy_has_no_live_account():
    policy = ET.parse(REPO / "installer/data/org.intergenos.ForgeInstaller1.conf")
    assert policy.findall(".//policy[@user='intergenos']") == []
    assert policy.findall(".//policy[@user='root']")
    assert policy.findall(".//policy[@group='wheel']")
    assert policy.findall(".//policy[@context='default']/deny")


RULE = "49-intergenos-forge-live-install.rules"


def _run_live_scaffold(newroot):
    """Runs init.sh's live-overlay scaffold against `newroot`, with the
    script's own info() defined as init.sh defines it."""
    source = (REPO / "installer/init/init.sh").read_text()
    info_start = source.index("info() {")
    info = source[info_start:source.index("\n}\n", info_start) + 3]
    live_start = source.index('if [ "$MODE" = "live" ] || [ "$MODE" = "install-gui" ]; then')
    start = source.index("    mkdir -p /newroot/etc/dbus-1/system.d", live_start)
    end = source.index("    # Home dir + tmpfiles", start)
    scaffold = info + source[start:end].replace("/newroot", str(newroot))
    return subprocess.run(["/usr/bin/bash", "-eu", "-c", scaffold], check=True,
                          capture_output=True, text=True)


def test_live_overlay_writes_separate_policy(tmp_path):
    _run_live_scaffold(tmp_path)
    path = tmp_path / "etc/dbus-1/system.d/org.intergenos.ForgeInstaller1.Live.conf"
    policy = ET.parse(path)
    assert len(policy.findall(".//policy")) == 1
    assert policy.find(".//policy").attrib == {"user": "intergenos"}
    assert len(policy.findall(".//allow")) == 2
    assert path.stat().st_mode & 0o777 == 0o644


def test_live_overlay_makes_the_packages_inert_rule_a_rule(tmp_path):
    shipped = REPO / "packages/desktop/forge/polkit" / RULE
    inert = tmp_path / "usr/share/intergenos/forge" / RULE
    inert.parent.mkdir(parents=True)
    inert.write_bytes(shipped.read_bytes())
    _run_live_scaffold(tmp_path)
    rule = tmp_path / "etc/polkit-1/rules.d" / RULE
    assert rule.read_bytes() == shipped.read_bytes()
    assert rule.stat().st_mode & 0o777 == 0o644


def test_live_overlay_says_so_when_the_rule_is_absent(tmp_path):
    done = _run_live_scaffold(tmp_path)
    assert not (tmp_path / "etc/polkit-1/rules.d" / RULE).exists()
    assert f"[init] no Forge live polkit rule at {tmp_path}/usr/share/intergenos/forge/{RULE}" in done.stdout
