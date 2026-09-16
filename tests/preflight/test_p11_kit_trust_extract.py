# SPDX-License-Identifier: GPL-3.0-or-later
"""The compatibility extraction producer uses the packaged trust inputs."""
from pathlib import Path
import subprocess
import yaml

ROOT = Path(__file__).resolve().parents[2]
P11 = ROOT / "packages/core/p11-kit"

def test_compat_producer_uses_staged_tls_extraction_and_atomic_exchange():
    script = (P11 / "files/usr/libexec/p11-kit/trust-extract-compat").read_text()
    install = (P11 / "build.sh").read_text()
    assert 'files/usr/libexec/p11-kit/trust-extract-compat"' in install
    assert 'usr/libexec/p11-kit/trust-extract-compat"' in install
    assert "make-ca" not in script
    assert "--filter=ca-anchors" in script
    assert "--purpose=server-auth" in script
    assert "--format=pem-directory-hash" in script
    assert "--format=pem-bundle" in script
    assert "--exchange --no-copy" in script
    assert "--overwrite" not in script
    assert "/usr/libexec/p11-kit/trust-extract-compat" in yaml.safe_load(
        (P11 / "package.yml").read_text())["verify_paths"]
    result = subprocess.run(["/bin/sh", str(P11 / "files/usr/libexec/p11-kit/trust-extract-compat"), "extra"],
                            capture_output=True, text=True)
    assert result.returncode == 2 and "usage:" in result.stderr
