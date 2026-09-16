"""Installed AMD UKIs omit Intel firmware; unknown hardware retains it."""

import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("vendors,intel", [
    (["AuthenticAMD", "AuthenticAMD"], False),
    (["GenuineIntel"], True),
    (["AuthenticAMD", "GenuineIntel"], True),
    ([], True),
])
def test_installed_uki_cpu_vendor_selection(tmp_path, vendors, intel):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("".join(f"vendor_id : {v}\n" for v in vendors))
    for name in ["intel", "amd"]:
        (tmp_path / name).touch()
    source = (REPO / "packages/core/linux-kernel/hooks/post-install.sh").read_text()
    start = source.index("CPU_VENDORS=")
    end = source.index("# D-005 Phase D:", start)
    selection = source[start:end].replace("/proc/cpuinfo", str(cpuinfo))
    script = ('UKIFY_ARGS=(); UCODE_INTEL="$1"; UCODE_AMD="$2"; log() { :; };\n'
              + selection + '\nprintf "%s\\n" "${UKIFY_ARGS[@]}"')
    result = subprocess.run(["/usr/bin/bash", "-u", "-c", script, "microcode-test",
                             str(tmp_path / "intel"), str(tmp_path / "amd")],
                            check=True, capture_output=True, text=True)
    assert (f"--initrd={tmp_path}/intel" in result.stdout) is intel
    assert f"--initrd={tmp_path}/amd" in result.stdout
