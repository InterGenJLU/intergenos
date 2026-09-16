# SPDX-License-Identifier: GPL-3.0-or-later
"""GATE — required file capabilities exist on installed payload files."""

import stat
import subprocess
from pathlib import Path

import pytest


GETCAP = Path("/usr/sbin/getcap")
REQUIRED = {
    "/usr/bin/gnome-keyring-daemon": "cap_ipc_lock=ep",
    "/usr/bin/ping6": "cap_net_raw=ep",
}


def _parse(output: str) -> dict[str, str]:
    found = {}
    for raw in output.splitlines():
        path, separator, capability = raw.partition(" ")
        assert separator and path not in found, f"unparseable or duplicate getcap line: {raw!r}"
        found[path] = capability.strip()
    return found


@pytest.mark.usefixtures("require_installed_intergenos")
class TestInstalledFileCapabilities:
    def test_control_rejects_an_empty_or_wrong_capability_read(self):
        assert _parse("") != REQUIRED
        assert _parse("/usr/bin/ping6 cap_net_raw=p\n") != REQUIRED

    def test_capability_reader_is_installed(self):
        assert GETCAP.is_file() and GETCAP.stat().st_mode & stat.S_IXUSR, (
            f"{GETCAP} is absent or not executable: libcap did not ship the required reader"
        )

    def test_installed_payloads_carry_the_exact_capabilities(self):
        missing = [path for path in REQUIRED if not Path(path).is_file()]
        assert not missing, f"required installed payload files are absent: {missing}"
        result = subprocess.run(
            [str(GETCAP), *REQUIRED], capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0, result.stderr
        assert _parse(result.stdout) == REQUIRED, (
            f"installed capabilities differ: {_parse(result.stdout)!r}; required {REQUIRED!r}"
        )

    def test_ping6_has_no_setuid_or_setgid_bit(self):
        mode = stat.S_IMODE(Path("/usr/bin/ping6").stat().st_mode)
        assert mode == 0o755, f"/usr/bin/ping6 mode is {mode:o}; required 755"
