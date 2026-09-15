"""Keep hardware conditions scoped to services that require that hardware."""

import configparser
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
UNITS = Path("usr/lib/systemd/system")


@pytest.mark.parametrize("package,unit,key,value", [
    ("core/wpa_supplicant", "wpa_supplicant-nl80211@.service",
     "ConditionPathExistsGlob", "/sys/class/net/*/wireless"),
    ("desktop/switcheroo-control", "switcheroo-control.service",
     "ConditionPathExists", "/sys/kernel/debug/vgaswitcheroo/switch"),
])
def test_hardware_dropin(package, unit, key, value):
    path = REPO / "packages" / package / "files" / UNITS / (unit + ".d/10-hardware.conf")
    assert path.is_file()
    config = configparser.ConfigParser(interpolation=None)
    config.read(path)
    assert config["Unit"][key] == value


def test_generic_and_wired_supplicant_remain_available():
    overlay = REPO / "packages/core/wpa_supplicant/files" / UNITS
    assert sorted(p.name for p in overlay.iterdir()) == ["wpa_supplicant-nl80211@.service.d"]
    recipe = (REPO / "packages/core/wpa_supplicant/build.sh").read_text()
    assert "CONFIG_DRIVER_WIRED=y" in recipe


def test_vmtoolsd_installs_existing_vmware_condition(tmp_path):
    recipe = REPO / "packages/desktop/open-vm-tools"
    subprocess.run(
        ["/usr/bin/bash", "-c", 'source "$1"; DESTDIR="$2"; make() { :; }; do_install',
         "vmtoolsd-package-test", str(recipe / "build.sh"), str(tmp_path)],
        check=True, capture_output=True, text=True,
    )
    unit = configparser.ConfigParser(interpolation=None)
    unit.read(tmp_path / UNITS / "vmtoolsd.service")
    assert unit["Unit"]["ConditionVirtualization"] == "vmware"
    assert unit["Service"]["ExecStart"] == "/usr/bin/vmtoolsd"
