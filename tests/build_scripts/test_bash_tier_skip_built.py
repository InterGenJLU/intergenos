# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Exercise the actual run_package functions with fixture archives."""

import io
import os
from pathlib import Path
import re
import shlex
import subprocess
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
DRIVERS = {
    "chroot-build-ch8.sh": "build_ch8_package",
    "chroot-build-core-extra.sh": "build_core_package",
    "chroot-build-base.sh": "build_base_package",
}


def source(name):
    ref = os.environ.get("IGOS_TEST_SCRIPT_REF")
    if ref:
        return subprocess.run(
            ["/usr/bin/git", "-C", str(ROOT), "show", f"{ref}:scripts/{name}"],
            check=True, capture_output=True, text=True,
        ).stdout
    return (ROOT / "scripts" / name).read_text()


def function(text, name):
    return re.search(rf"^{name}\(\) \{{\n.*?^\}}", text, re.M | re.S).group()


def archive(directory, filename, version="2.0", release="4", name="sample"):
    data = f"pkgname = {name}\npkgver = {version}\npkgrel = {release}\n".encode()
    with tarfile.open(directory / filename, "w:gz") as tf:
        info = tarfile.TarInfo("./.PKGINFO")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))


def fixture(root):
    packages = root / "packages" / "core"
    recipe = packages / "sample-recipe" / "package.yml"
    recipe.parent.mkdir(parents=True)
    recipe.write_text("name: sample-recipe\nships_as: sample\nversion: '2.0'\nrelease: 4 # current\n")
    archives = root / "archives"
    archives.mkdir()
    manifests = root / "manifests"
    manifests.mkdir()
    (manifests / "sample-2.0").write_text(
        "PACKAGE NAME: sample-2.0\nPACKAGE VERSION: 2.0\n"
        "PACKAGE RELEASE: 4\nBUILD DATE: fixture\nFILE LIST:\n"
    )
    return packages, archives, manifests


def run_driver(root, driver, *, skip_built=True, start="", stop="", fail_build=False):
    packages, archives, manifests = (root / "packages" / "core", root / "archives", root / "manifests")
    script = root / "run-package.sh"
    script.write_text(
        "set -euo pipefail\n"
        + function(source(driver), "run_package") + "\n"
        + "log() { printf '%s\\n' \"$*\"; }\n"
        + "igos_progress_skip() { printf 'PROGRESS_SKIP %s\\n' \"$1\"; }\n"
        + "igos_progress_begin() { :; }\nigos_progress_end() { :; }\n"
        + f"{DRIVERS[driver]}() {{ printf 'REBUILT %s\\n' \"$2\"; "
        + ("return 37;" if fail_build else ":;") + " }\n"
        # The helper itself runs unchanged; only its installation path is
        # redirected to this checkout, as are the package/archive roots.
        + "python3() { if [ \"$1\" = /mnt/intergenos/scripts/bash-tier-build-state.py ]; then shift; "
        + f"command python3 {shlex.quote(str(ROOT / 'scripts/bash-tier-build-state.py'))} \"$@\"; "
        + "else command python3 \"$@\"; fi; }\n"
        + f"IGOS_PACKAGES={shlex.quote(str(packages))}\n"
        + f"IGOS_PKG_ARCHIVES={shlex.quote(str(archives))}\n"
        + f"IGOS_PKG_DB={shlex.quote(str(manifests))}\n"
        + f"IGOS_SKIP_BUILT={'true' if skip_built else 'false'}\n"
        + f"IGOS_START_AT={shlex.quote(start)}\nIGOS_STOP_AFTER={shlex.quote(stop)}\n"
        + f"SKIP={'true' if start else 'false'}\n"
        + 'run_package sample-recipe sample 2.0 source.tar.gz description\n'
        + "printf 'AFTER_PACKAGE\\n'\n"
    )
    result = subprocess.run(["/usr/bin/bash", str(script)], capture_output=True, text=True, timeout=20)
    print(f"{driver} skip_built={skip_built} start={start!r} stop={stop!r}\n{result.stdout}{result.stderr}")
    return result


@pytest.mark.parametrize("driver", DRIVERS)
@pytest.mark.parametrize("filename", ["sample-2.0.igos.tar.gz", "sample-2.0-4.igos.tar.gz"])
def test_current_archive_and_manifest_skip(tmp_path, driver, filename):
    _, archives, _ = fixture(tmp_path)
    archive(archives, filename)
    result = run_driver(tmp_path, driver)
    assert result.returncode == 0, result.stderr
    assert f"skipped (current 2.0-4, archive {filename})" in result.stdout
    assert "REBUILT" not in result.stdout


@pytest.mark.parametrize("driver", DRIVERS)
@pytest.mark.parametrize("state", ["stale", "missing", "uninstalled", "ahead", "foreign"])
def test_noncurrent_or_uninstalled_rebuilds(tmp_path, driver, state):
    _, archives, manifests = fixture(tmp_path)
    if state != "missing":
        archive(archives, "sample-2.0.igos.tar.gz", release="3" if state == "stale" else "5" if state == "ahead" else "4", name="sample-other" if state == "foreign" else "sample")
    if state == "uninstalled":
        (manifests / "sample-2.0").unlink()
    result = run_driver(tmp_path, driver)
    assert result.returncode == 0, result.stderr
    assert "REBUILT sample" in result.stdout
    assert "skipped (current" not in result.stdout
    if state == "stale":
        assert "stale: archive 2.0-3 behind tree 2.0-4" in result.stdout
    if state in ("missing", "foreign"):
        assert "never built" in result.stdout


@pytest.mark.parametrize("driver", DRIVERS)
def test_flag_absent_always_builds(tmp_path, driver):
    _, archives, _ = fixture(tmp_path)
    archive(archives, "sample-2.0-4.igos.tar.gz")
    result = run_driver(tmp_path, driver, skip_built=False)
    assert result.returncode == 0, result.stderr
    assert "REBUILT sample" in result.stdout


@pytest.mark.parametrize("driver", DRIVERS)
@pytest.mark.parametrize("start", ["sample", "sample-recipe"])
def test_explicit_restart_rebuilds_current_target(tmp_path, driver, start):
    _, archives, _ = fixture(tmp_path)
    archive(archives, "sample-2.0-4.igos.tar.gz")
    result = run_driver(tmp_path, driver, start=start)
    assert result.returncode == 0, result.stderr
    assert "REBUILT sample" in result.stdout


@pytest.mark.parametrize("driver", DRIVERS)
def test_skipped_stop_target_still_stops(tmp_path, driver):
    _, archives, _ = fixture(tmp_path)
    archive(archives, "sample-2.0-4.igos.tar.gz")
    result = run_driver(tmp_path, driver, stop="sample")
    assert result.returncode == 0, result.stderr
    assert "skipped (current" in result.stdout
    assert "AFTER_PACKAGE" not in result.stdout


@pytest.mark.parametrize("driver", DRIVERS)
def test_package_failure_still_stops_driver(tmp_path, driver):
    fixture(tmp_path)
    result = run_driver(tmp_path, driver, fail_build=True)
    assert result.returncode == 37, result.stdout + result.stderr
    assert "AFTER_PACKAGE" not in result.stdout


@pytest.mark.parametrize("driver", DRIVERS)
def test_newest_archive_uses_version_and_numeric_release(tmp_path, driver):
    packages, archives, manifests = fixture(tmp_path)
    (packages / "sample-recipe/package.yml").write_text("name: sample\nversion: '2.10'\nrelease: 10\n")
    (manifests / "sample-2.10").write_text("PACKAGE NAME: sample-2.10\nFILE LIST:\n")
    archive(archives, "sample-2.9-99.igos.tar.gz", version="2.9", release="99")
    archive(archives, "sample-2.10-9.igos.tar.gz", version="2.10", release="9")
    archive(archives, "sample-2.10-10.igos.tar.gz", version="2.10", release="10")
    result = run_driver(tmp_path, driver)
    assert result.returncode == 0, result.stderr
    assert "skipped (current 2.10-10, archive sample-2.10-10.igos.tar.gz)" in result.stdout
    assert "REBUILT" not in result.stdout


@pytest.mark.parametrize("driver", DRIVERS)
def test_archive_identity_is_read_from_metadata(tmp_path, driver):
    _, archives, _ = fixture(tmp_path)
    archive(archives, "unrelated-filename.igos.tar.gz")
    result = run_driver(tmp_path, driver)
    assert result.returncode == 0, result.stderr
    assert "archive unrelated-filename.igos.tar.gz)" in result.stdout
    assert "REBUILT" not in result.stdout


@pytest.mark.parametrize("driver", DRIVERS)
@pytest.mark.parametrize("broken", ["corrupt", "release"])
def test_unverifiable_archive_cannot_certify_a_skip(tmp_path, driver, broken):
    _, archives, _ = fixture(tmp_path)
    archive(archives, "sample-2.0-4.igos.tar.gz")
    if broken == "corrupt":
        (archives / "newer.igos.tar.gz").write_bytes(b"not an archive")
    else:
        archive(archives, "newer.igos.tar.gz", release="not-a-number")
    result = run_driver(tmp_path, driver)
    assert result.returncode != 0
    assert "cannot verify currency" in result.stderr
    assert "REBUILT" not in result.stdout
    assert "skipped (current" not in result.stdout
    assert "AFTER_PACKAGE" not in result.stdout


@pytest.mark.parametrize("driver", DRIVERS)
def test_directory_does_not_replace_installed_manifest(tmp_path, driver):
    _, archives, manifests = fixture(tmp_path)
    archive(archives, "sample-2.0-4.igos.tar.gz")
    path = manifests / "sample-2.0"
    path.unlink()
    path.mkdir()
    result = run_driver(tmp_path, driver)
    assert result.returncode == 0, result.stderr
    assert "installed manifest missing" in result.stdout
    assert "REBUILT sample" in result.stdout


@pytest.mark.parametrize("driver", DRIVERS)
def test_packages_before_explicit_start_keep_their_resume_skip(tmp_path, driver):
    _, archives, _ = fixture(tmp_path)
    (archives / "unreadable.igos.tar.gz").write_bytes(b"not an archive")
    result = run_driver(tmp_path, driver, start="later-package")
    assert result.returncode == 0, result.stderr
    assert "resuming from later-package" in result.stdout
    assert "REBUILT" not in result.stdout
