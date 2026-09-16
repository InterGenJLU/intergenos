"""The NVIDIA package's installed policy excludes competing framebuffer drivers."""

import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def test_installed_nvidia_modprobe_policy(tmp_path):
    recipe = REPO / "packages/extra/nvidia/build.sh"
    subprocess.run(
        ["/usr/bin/bash", "-c",
         'source "$1"; DESTDIR="$2"; install_modprobe_policy',
         "nvidia-policy-test", str(recipe), str(tmp_path)],
        check=True, capture_output=True, text=True,
    )
    config = tmp_path / "etc/modprobe.d/nvidia-nouveau-blacklist.conf"
    result = subprocess.run(
        ["/usr/sbin/modprobe", "--config", str(config), "--showconfig"],
        check=True, capture_output=True, text=True,
    )
    lines = result.stdout.splitlines()
    assert "blacklist nvidiafb" in lines
    assert "blacklist nouveau" in lines
    assert "options nouveau modeset=0" in lines
