"""The live account's bus policy is created only in the live boot overlay."""

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


def test_live_overlay_writes_separate_policy(tmp_path):
    source = (REPO / "installer/init/init.sh").read_text()
    live_start = source.index('if [ "$MODE" = "live" ] || [ "$MODE" = "install-gui" ]; then')
    start = source.index("    mkdir -p /newroot/etc/dbus-1/system.d", live_start)
    end = source.index("    # Home dir + tmpfiles", start)
    scaffold = source[start:end].replace("/newroot", str(tmp_path))
    subprocess.run(["/usr/bin/bash", "-eu", "-c", scaffold], check=True)
    path = tmp_path / "etc/dbus-1/system.d/org.intergenos.ForgeInstaller1.Live.conf"
    policy = ET.parse(path)
    assert len(policy.findall(".//policy")) == 1
    assert policy.find(".//policy").attrib == {"user": "intergenos"}
    assert len(policy.findall(".//allow")) == 2
    assert path.stat().st_mode & 0o777 == 0o644
