# SPDX-License-Identifier: GPL-3.0-or-later
"""The CA updater must have a real producer and use the configured trust store."""
from pathlib import Path
import subprocess
import yaml

ROOT = Path(__file__).resolve().parents[2]
CA = ROOT / "packages/core/ca-certificates"
P11 = ROOT / "packages/core/p11-kit"


def test_ca_recipe_ships_the_hook_program_and_its_manual():
    recipe = yaml.safe_load((CA / "package.yml").read_text())
    assert "/usr/bin/update-ca-trust" in recipe["verify_paths"]
    assert "p11-kit" in recipe["dependencies"]["runtime"]
    assert (CA / "files/usr/bin/update-ca-trust").is_file()
    assert (CA / "files/usr/share/man/man8/update-ca-trust.8").is_file()
    install = (CA / "build.sh").read_text()
    assert 'files/usr/bin/update-ca-trust"' in install
    assert 'usr/bin/update-ca-trust"' in install
    assert 'usr/share/man/man8/update-ca-trust.8"' in install


def test_updater_is_the_reviewed_thin_wrapper():
    script = (CA / "files/usr/bin/update-ca-trust").read_text()
    assert script == "\n".join([
        "#!/bin/sh",
        "# SPDX-License-Identifier: GPL-3.0-or-later",
        "# InterGenOS compatibility wrapper, not the Fedora update-ca-trust script.",
        'case "$#:$*" in',
        '    0:|1:extract) exec /usr/bin/trust extract-compat ;;',
        '    *) echo "usage: update-ca-trust [extract]" >&2; exit 2 ;;',
        "esac", "",
    ])
    assert "InterGenOS" in script and "Fedora" in script
    assert "exec /usr/bin/trust extract-compat" in script
    assert "/usr/sbin/make-ca" not in script
    for args in [["check"], ["extract", "extra"], ["unknown"], [""]]:
        result = subprocess.run(["/bin/sh", str(CA / "files/usr/bin/update-ca-trust"), *args],
                                capture_output=True, text=True)
        assert result.returncode == 2
        assert "usage:" in result.stderr
