"""Check the Tailscale recipe's installed configuration wiring."""

import configparser
import json
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


def test_tailscaled_installs_a_policy_that_turns_its_own_updater_off(tmp_path):
    """The package manager is the installer of record for both binaries.

    tailscaled carries its own updater: when the coordination server asks, it
    replaces /usr/bin/tailscale and /usr/sbin/tailscaled in place, and the
    package database goes on describing the build it installed. The package
    ships the daemon's device policy file with both update settings at
    "never", which tailscaled reads from this path by default at start and
    enforces over the node's own preference and the tailnet's default.
    """
    source, dest = tmp_path / "source", tmp_path / "dest"
    source.mkdir()
    for name in ("tailscale", "tailscaled", "LICENSE"):
        (source / name).write_text(f"package fixture: {name}\n")
    recipe = REPO / "packages/extra/tailscale"
    subprocess.run(
        ["/usr/bin/bash", "-c", 'source "$1"; DESTDIR="$2"; do_install',
         "tailscale-package-test", str(recipe / "build.sh"), str(dest)],
        cwd=source, check=True, capture_output=True, text=True,
    )
    policy = dest / "etc/tailscale/syspolicy.json"
    assert policy.is_file(), "the package installs no /etc/tailscale/syspolicy.json"
    assert policy.read_bytes() == (recipe / "syspolicy.json").read_bytes()
    assert policy.stat().st_mode & 0o777 == 0o644
    # Plain JSON, so it parses whether or not the daemon was built with its
    # comment-tolerant reader; exactly the two update settings, both "never".
    assert json.loads(policy.read_text()) == {
        "CheckUpdates": "never",
        "InstallUpdates": "never",
    }
    # The unit passes no --syspolicy-file, so the daemon reads the default path.
    unit = (dest / "usr/lib/systemd/system/tailscaled.service").read_text()
    defaults = (dest / "etc/default/tailscaled").read_text()
    assert "syspolicy-file" not in unit + defaults
