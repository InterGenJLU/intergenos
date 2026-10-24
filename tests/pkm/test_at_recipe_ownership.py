# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the at recipe through pkm's real hook consumer in a scratch root.

Requires root and AT_TEST_ARCHIVE pointing to an at package archive.
The archive supplies the expected at.deny ownership and mode.
"""

import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]


def prepare_root(root, archive):
    with tarfile.open(archive) as tar:
        deny = next(m for m in tar.getmembers()
                    if m.name.removeprefix("./") == "etc/at.deny")
        assert (deny.uname, deny.gname) == ("root", "atd")
        expected = (deny.uid, deny.gid, deny.mode)
        tar.extractall(root, filter="data")
    # Match deployment's root-owned starting point, then use real commands.
    assert (root / "etc/at.deny").stat().st_gid == 0
    for directory in ("usr/bin", "usr/sbin", "etc", "dev"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "bin").symlink_to("usr/bin")
    (root / "sbin").symlink_to("usr/sbin")
    (root / "etc/passwd").write_text("root:x:0:0:root:/root:/bin/bash\n")
    (root / "etc/group").write_text("root:x:0:\n")
    (root / "etc/nsswitch.conf").write_text("passwd: files\ngroup: files\n")
    (root / "dev/null").touch()
    for program in ("bash", "chown", "chmod", "install", "systemd-sysusers"):
        binary = Path(shutil.which(program))
        deps = subprocess.run(["ldd", str(binary)], check=True,
                              capture_output=True, text=True).stdout
        for source in [binary, *map(Path, re.findall(r"/[^\s()]+", deps))]:
            target = root / source.relative_to("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    shutil.copy2(ROOT / "packages/base/at/build.sh", root / "recipe.sh")
    shutil.copy2(ROOT / "packages/base/at/files/usr/lib/sysusers.d/at.conf",
                 root / "usr/lib/sysusers.d/at.conf")
    hook = root / "var/lib/pkm/hooks/at/post-install"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/bin/bash\nset -x\nsource /recipe.sh\npost_install\n")
    hook.chmod(0o755)
    return expected


@pytest.mark.parametrize("sequence_present", [False, True])
def test_at_deny_matches_archive_after_the_package_hook(tmp_path, sequence_present):
    if os.geteuid() != 0:
        pytest.skip("real chroot hook needs root")
    archive = os.environ.get("AT_TEST_ARCHIVE")
    if not archive:
        pytest.skip("set AT_TEST_ARCHIVE to the at package archive")
    import pkm
    from pkm.installer import PackageInstaller

    assert Path(pkm.__file__).resolve() == ROOT / "pkm/__init__.py"
    print(f"pkm.__file__={pkm.__file__}; euid={os.geteuid()}; archive={archive}")
    expected = prepare_root(tmp_path, archive)
    sequence = tmp_path / "var/spool/atjobs/.SEQ"
    if not sequence_present:
        sequence.unlink(missing_ok=True)
    consumer = PackageInstaller.__new__(PackageInstaller)
    consumer.root = tmp_path
    for firing in range(2):
        assert consumer._run_post_install_hook("at", "3.2.5") == 0
        info = (tmp_path / "etc/at.deny").stat()
        actual = (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
        print(f"firing={firing + 1}; at.deny={actual}; archive={expected}")
        assert actual == expected
        for path in ("usr/bin/at", "usr/sbin/atd"):
            info = (tmp_path / path).stat()
            assert (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (
                0, expected[1], 0o4750)
        for path in ("var/spool/atjobs", "var/spool/atspool"):
            info = (tmp_path / path).stat()
            assert (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (
                expected[1], expected[1], 0o1770)
        if sequence_present:
            info = sequence.stat()
            assert (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (
                expected[1], expected[1], 0o600)
