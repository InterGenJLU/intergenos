"""Tests for version parsing and comparison in vps-source-poller."""

import sys
import importlib.util
from pathlib import Path

repo_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(repo_root / "scripts"))

spec = importlib.util.spec_from_file_location("vps_source_poller", repo_root / "scripts/vps-source-poller.py")
poller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poller)
_parse_version = poller._parse_version
resolve_url = poller.resolve_url
detect_strategy = poller.detect_strategy

import unittest


class TestVersionParsing(unittest.TestCase):
    def test_simple_semver(self):
        self.assertGreater(_parse_version("2.0.0"), _parse_version("1.0.0"))
        self.assertGreater(_parse_version("1.2.0"), _parse_version("1.1.0"))
        self.assertGreater(_parse_version("1.0.1"), _parse_version("1.0.0"))

    def test_major_minor_only(self):
        self.assertGreater(_parse_version("2.0"), _parse_version("1.9"))
        self.assertGreater(_parse_version("3.0"), _parse_version("2.99"))

    def test_equal_versions(self):
        self.assertEqual(_parse_version("1.0.0"), _parse_version("1.0.0"))

    def test_pre_release_detected(self):
        # Pre-releases are detected as newer (owner filters during adoption review)
        self.assertGreater(_parse_version("1.0.0-rc1"), _parse_version("1.0.0"))

    def test_dot_separated(self):
        self.assertGreater(_parse_version("2.43"), _parse_version("2.42"))

    def test_hyphen_separated_release(self):
        self.assertGreater(_parse_version("2.0-1"), _parse_version("1.0-1"))

    def test_letter_suffix(self):
        self.assertGreater(_parse_version("1.2a"), _parse_version("1.2"))

    def test_long_version(self):
        self.assertGreater(_parse_version("2.2.0.1"), _parse_version("2.2.0"))

    def test_url_resolution(self):
        result = resolve_url("https://example.com/${name}-${version}.tar.xz", "glibc", "2.43")
        self.assertEqual(result, "https://example.com/glibc-2.43.tar.xz")

    def test_url_resolution_major_minor(self):
        result = resolve_url("https://example.com/${name}/${version_major_minor}/${name}-${version}.tar.xz", "glib", "2.88.1")
        self.assertEqual(result, "https://example.com/glib/2.88/glib-2.88.1.tar.xz")

    def test_strategy_detection_gnu_ftp(self):
        result = detect_strategy("https://ftpmirror.gnu.org/bash/bash-5.2.tar.gz", {})
        self.assertEqual(result, "gnu-ftp")

    def test_strategy_detection_github(self):
        result = detect_strategy("https://github.com/torvalds/linux/archive/v6.18.tar.gz", {})
        self.assertEqual(result, "github")

    def test_strategy_detection_pypi(self):
        result = detect_strategy("https://files.pythonhosted.org/packages/.../Jinja2-3.1.2.tar.gz", {})
        self.assertEqual(result, "pypi")

    def test_strategy_detection_gnome(self):
        result = detect_strategy("https://download.gnome.org/sources/glib/2.88/glib-2.88.1.tar.xz", {})
        self.assertEqual(result, "gnome")

    def test_strategy_detection_freedesktop(self):
        result = detect_strategy("https://www.freedesktop.org/software/systemd/systemd-259.1.tar.xz", {})
        self.assertEqual(result, "freedesktop")

    def test_strategy_override_from_yml(self):
        result = detect_strategy("https://example.com/pkg-1.0.tar.gz", {"upstream_check": {"type": "github", "repo": "owner/pkg"}})
        self.assertEqual(result, "github")


if __name__ == "__main__":
    unittest.main()
