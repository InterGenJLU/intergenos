# SPDX-License-Identifier: GPL-3.0-or-later
"""A failure after removal names the changed package and its recovery point."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pkm.remover import PackageRemover
from tests.pkm.test_local_archive_preflight import local, invoke

@pytest.mark.parametrize("failure", ["changed-archive", "unchanged-message"])
def test_late_refusal_names_removal_and_restore_id(local, failure):
    restore_id = "0000000042-abcdef123456"
    captured = SimpleNamespace(ran=True, handlers=["chronicle"], failures=[],
                               messages=[], restore_points=[restore_id])
    remove = PackageRemover.remove
    def removing(self, *args, **kwargs):
        result = remove(self, *args, **kwargs)
        local.new.write_bytes(b"changed after removal")
        return result
    install = local.installer.__class__.install
    def installing(self, *args, **kwargs):
        if failure == "unchanged-message":
            return False, "Archive refused. Nothing was changed."
        return install(self, *args, **kwargs)
    with patch.object(PackageRemover, "remove", removing), \
            patch.object(local.installer.__class__, "install", installing):
        rc, said = invoke(local, capture=lambda *a, **k: captured)
    assert rc == 1, said
    assert local.db.get_installed("widget") is None
    assert "widget 1.0-3 was removed" in said
    assert restore_id in said
    assert "Nothing was changed" not in said
