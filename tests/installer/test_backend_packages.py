"""Forge install_packages() — install-queue threading to pkm.install().

Closes the forge-side gap for supersedes RFC §4 + Phase 4 design §2d NIT 1:
the install-order invariant is enforceable in pkm only when forge passes
the full queue. These tests pin the wiring so a future regression that
drops the kwarg fails loudly instead of silently disabling the invariant.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from installer.backend.packages import install_packages


class TestInstallQueueThreading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.target = Path(self.tmp) / "target"
        self.target.mkdir()
        self.archive_dir = Path(self.tmp) / "archives"
        self.archive_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    @patch("installer.backend.packages.PackageInstaller")
    @patch("installer.backend.packages.PackageDB")
    @patch("installer.backend.packages.get_group_packages")
    def test_queue_threaded_to_installer(self, mock_get_packages, mock_db_cls, mock_installer_cls):
        """install() receives queue=<all-package-names-in-order> on every call."""
        mock_get_packages.return_value = [
            ("pred", "1.0", Path("/fake/pred.tar.gz")),
            ("middle", "1.0", Path("/fake/middle.tar.gz")),
            ("succ", "2.0", Path("/fake/succ.tar.gz")),
        ]
        mock_installer = MagicMock()
        mock_installer.install.return_value = (True, "ok")
        mock_installer_cls.return_value = mock_installer

        install_packages(str(self.target), str(self.archive_dir), groups=["core"])

        self.assertEqual(mock_installer.install.call_count, 3)
        expected_queue = ["pred", "middle", "succ"]
        for call in mock_installer.install.call_args_list:
            self.assertEqual(call.kwargs.get("queue"), expected_queue)

    @patch("installer.backend.packages.PackageInstaller")
    @patch("installer.backend.packages.PackageDB")
    @patch("installer.backend.packages.get_group_packages")
    def test_queue_preserves_order(self, mock_get_packages, mock_db_cls, mock_installer_cls):
        """Queue reflects exact order from get_group_packages, not re-sorted."""
        mock_get_packages.return_value = [
            ("zebra", "1.0", Path("/fake/zebra.tar.gz")),
            ("alpha", "1.0", Path("/fake/alpha.tar.gz")),
            ("middle", "1.0", Path("/fake/middle.tar.gz")),
        ]
        mock_installer = MagicMock()
        mock_installer.install.return_value = (True, "ok")
        mock_installer_cls.return_value = mock_installer

        install_packages(str(self.target), str(self.archive_dir), groups=["core"])

        first_call = mock_installer.install.call_args_list[0]
        self.assertEqual(first_call.kwargs.get("queue"), ["zebra", "alpha", "middle"])

    @patch("installer.backend.packages.PackageInstaller")
    @patch("installer.backend.packages.PackageDB")
    @patch("installer.backend.packages.get_group_packages")
    def test_queue_consistent_across_calls(self, mock_get_packages, mock_db_cls, mock_installer_cls):
        """Queue is built once and identical for every per-package install() call."""
        mock_get_packages.return_value = [
            ("a", "1.0", Path("/fake/a.tar.gz")),
            ("b", "1.0", Path("/fake/b.tar.gz")),
        ]
        mock_installer = MagicMock()
        mock_installer.install.return_value = (True, "ok")
        mock_installer_cls.return_value = mock_installer

        install_packages(str(self.target), str(self.archive_dir), groups=["core"])

        queues = [call.kwargs.get("queue") for call in mock_installer.install.call_args_list]
        self.assertEqual(len(queues), 2)
        self.assertEqual(queues[0], queues[1])
        self.assertEqual(queues[0], ["a", "b"])

    @patch("installer.backend.packages.PackageInstaller")
    @patch("installer.backend.packages.PackageDB")
    @patch("installer.backend.packages.get_group_packages")
    def test_empty_packages_short_circuits(self, mock_get_packages, mock_db_cls, mock_installer_cls):
        """No packages = no DB / installer instantiation; queue never built."""
        mock_get_packages.return_value = []

        success, fail_count, failed = install_packages(
            str(self.target), str(self.archive_dir), groups=["core"]
        )

        self.assertEqual((success, fail_count, failed), (0, 0, []))
        mock_db_cls.assert_not_called()
        mock_installer_cls.assert_not_called()

    @patch("installer.backend.packages.PackageInstaller")
    @patch("installer.backend.packages.PackageDB")
    @patch("installer.backend.packages.get_group_packages")
    def test_queue_threaded_even_when_install_fails(self, mock_get_packages, mock_db_cls, mock_installer_cls):
        """Failed install() calls still receive queue= — the kwarg is unconditional."""
        mock_get_packages.return_value = [
            ("ok-pkg", "1.0", Path("/fake/ok-pkg.tar.gz")),
            ("bad-pkg", "1.0", Path("/fake/bad-pkg.tar.gz")),
        ]
        mock_installer = MagicMock()
        mock_installer.install.side_effect = [
            (True, "ok"),
            (False, "queue-order violation: succ before pred"),
        ]
        mock_installer_cls.return_value = mock_installer

        success, fail_count, failed = install_packages(
            str(self.target), str(self.archive_dir), groups=["core"]
        )

        self.assertEqual(success, 1)
        self.assertEqual(fail_count, 1)
        self.assertEqual(failed[0][0], "bad-pkg")
        for call in mock_installer.install.call_args_list:
            self.assertEqual(call.kwargs.get("queue"), ["ok-pkg", "bad-pkg"])


if __name__ == "__main__":
    unittest.main()
