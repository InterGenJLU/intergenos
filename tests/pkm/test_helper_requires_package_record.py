# SPDX-License-Identifier: GPL-3.0-or-later
"""An unregistered helper cannot start a download or invent a package version."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pkm import cli, installer as installer_module
from pkm.database import PackageDB
from pkm.installer import PackageInstaller


@pytest.fixture
def helper_case(tmp_path, monkeypatch):
    db = PackageDB(tmp_path / "pkm.db", root=str(tmp_path / "root"))
    marker = tmp_path / "helper-ran"
    helper = tmp_path / "igos-install-example"
    helper.write_text("#!/usr/bin/python3\nfrom pathlib import Path\n"
                      f"Path({str(marker)!r}).write_text('ran')\n")
    helper.chmod(0o755)
    monkeypatch.setattr(installer_module, "HELPER_MANIFEST_DIR", tmp_path / "helpers")
    yield db, PackageInstaller(db, root=str(db.root)), helper, marker
    db.close()


def test_missing_package_refuses_before_the_executable_runs(helper_case):
    db, installer, helper, marker = helper_case
    ok, message, declined = installer._run_helper("example", helper)
    assert not marker.exists()
    assert not ok and not declined
    assert "sudo pkm install example" in message
    assert db.get_installed("example") is None


def test_install_helper_command_prints_the_package_first_step(helper_case, capsys):
    db, installer, helper, marker = helper_case
    with patch.object(cli, "package_installer", return_value=installer), \
            patch.object(installer, "_find_eula_helper", return_value=None), \
            patch.object(installer, "_find_helper", return_value=helper):
        with pytest.raises(SystemExit) as stopped:
            cli.cmd_install_helper(db, SimpleNamespace(package="example"))
    assert stopped.value.code == 1
    assert not marker.exists()
    captured = capsys.readouterr()
    assert "sudo pkm install example" in captured.out + captured.err


def test_direct_ingestion_also_requires_the_package_record(helper_case):
    db, installer, helper, marker = helper_case
    ok, message, declined = installer._register_helper_footprint(
        "example", {"version_installed": "0.8", "files": [],
                    "symlinks": [], "depends": []}, helper)
    assert not ok and not declined
    assert "sudo pkm install example" in message
    assert db.get_installed("example") is None
    assert not marker.exists()
