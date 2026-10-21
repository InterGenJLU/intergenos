# SPDX-License-Identifier: GPL-3.0-or-later
"""A removed installation cannot establish a later helper row's identity."""

import pytest

from pkm.database import PackageDB
from tests.pkm.test_helper_history_version_repair import history, missing_archive


@pytest.mark.parametrize("success", [0, 1])
def test_later_removal_leaves_version_unestablished(tmp_path, capsys, success):
    root, path, legacy, original, _ = missing_archive(tmp_path)
    history(path)
    history(path, operation="remove", method=None, success=success)
    capsys.readouterr()
    for _ in range(2):
        with PackageDB(path, root=str(root)) as db:
            assert db.get_installed("example")["version"] == "0.8"
            assert not any(h["operation"] == "repair-version"
                           for h in db.get_history("example"))
        lines = capsys.readouterr().err.splitlines()
        assert len(lines) == 1 and "left as recorded" in lines[0]
    assert legacy.read_bytes() == original


@pytest.mark.parametrize("shape", ["earlier-remove", "different-package", "new-install"])
def test_history_after_removal_can_establish_current_install(tmp_path, shape):
    root, path, *_ = missing_archive(tmp_path)
    if shape == "earlier-remove":
        history(path, operation="remove", method=None)
    history(path, "1.0")
    if shape == "different-package":
        history(path, operation="remove", package_name="different", method=None)
    if shape == "new-install":
        history(path, operation="remove", method=None)
        history(path, "1.1")
    with PackageDB(path, root=str(root)) as db:
        assert db.get_installed("example")["version"] == (
            "1.1" if shape == "new-install" else "1.0")
