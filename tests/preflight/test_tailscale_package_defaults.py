"""Check the Tailscale recipe's installed configuration wiring."""

import configparser
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def test_tailscaled_installs_native_nftables_defaults(tmp_path):
    source, dest = tmp_path / "source", tmp_path / "dest"
    source.mkdir()
    # Only the configuration packaging is exercised; these binaries are inert.
    for name in ("tailscale", "tailscaled", "LICENSE"):
        (source / name).write_text(f"package fixture: {name}\n")
    recipe = REPO / "packages/extra/tailscale"
    subprocess.run(
        ["/usr/bin/bash", "-c", 'source "$1"; DESTDIR="$2"; do_install',
         "tailscale-package-test", str(recipe / "build.sh"), str(dest)],
        cwd=source, check=True, capture_output=True, text=True,
    )
    unit = configparser.ConfigParser(interpolation=None)
    unit.read(dest / "usr/lib/systemd/system/tailscaled.service")
    assert unit["Service"]["EnvironmentFile"] == "/etc/default/tailscaled"
    defaults = dest / unit["Service"]["EnvironmentFile"].lstrip("/")
    assert defaults.read_bytes() == (recipe / "tailscaled.defaults").read_bytes()
    modes = [line.partition("=")[2] for line in defaults.read_text().splitlines()
             if line.lstrip().startswith("TS_DEBUG_FIREWALL_MODE=")]
    assert modes == ["nftables"]
    assert defaults.stat().st_mode & 0o777 == 0o644
    assert (dest / "var/lib/tailscale").stat().st_mode & 0o777 == 0o700
