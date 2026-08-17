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

from installer.backend.packages import (
    install_packages, get_group_packages, compute_install_set_gap)


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

        success, fail_count, failed, _ = install_packages(
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

        success, fail_count, failed, _ = install_packages(
            str(self.target), str(self.archive_dir), groups=["core"]
        )

        self.assertEqual(success, 1)
        self.assertEqual(fail_count, 1)
        self.assertEqual(failed[0][0], "bad-pkg")
        for call in mock_installer.install.call_args_list:
            self.assertEqual(call.kwargs.get("queue"), ["ok-pkg", "bad-pkg"])


class TestHardwareGate(unittest.TestCase):
    """get_group_packages() install-time GPU-vendor gate (GBC001 nvidia-on-AMD
    blocker fix). A package declaring `requires_pci_vendor` in package.yml must
    be omitted from the install queue when no display controller from that
    vendor is present on the target — fail-closed, and never affecting
    non-gated packages or other tiers.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.archive_dir = Path(self.tmp) / "archives"
        self.archive_dir.mkdir()
        self.pkg_dir = Path(self.tmp) / "packages"
        # Archives (content irrelevant — get_archives only parses names)
        for name in ("nvidia-580.159.04", "firefox-1.0", "nodejs-22.0",
                     "bash-5.2"):
            (self.archive_dir / f"{name}.igos.tar.gz").write_text("x")
        # Tier tree: extra/{nvidia(gated),firefox,nodejs}, core/bash
        self._mk_pkg("extra", "nvidia", 'requires_pci_vendor: "10de"\n')
        self._mk_pkg("extra", "firefox", "")        # no gate
        self._mk_pkg("extra", "nodejs", "")          # no gate
        self._mk_pkg("core", "bash", "")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _mk_pkg(self, tier, name, yml_body):
        d = self.pkg_dir / tier / name
        d.mkdir(parents=True)
        (d / "package.yml").write_text(f"name: {name}\ntier: {tier}\n{yml_body}")

    def _names(self, groups, vendors):
        out = get_group_packages(groups, str(self.archive_dir),
                                 str(self.pkg_dir), detected_vendors=vendors)
        return {n for (n, _v, _p) in out}

    def test_gated_dropped_on_non_matching_vendor(self):
        names = self._names(["extra"], {"1002"})  # AMD
        self.assertNotIn("nvidia", names)
        self.assertIn("firefox", names)
        self.assertIn("nodejs", names)

    def test_gated_kept_on_matching_vendor(self):
        names = self._names(["extra"], {"10de"})  # NVIDIA
        self.assertIn("nvidia", names)

    def test_fail_closed_when_vendors_undetectable(self):
        names = self._names(["extra"], set())  # lspci failed -> empty
        self.assertNotIn("nvidia", names)
        self.assertIn("firefox", names)

    def test_only_gated_package_differs_by_vendor(self):
        amd = self._names(["extra"], {"1002"})
        nv = self._names(["extra"], {"10de"})
        self.assertEqual(nv - amd, {"nvidia"})
        self.assertEqual(amd - nv, set())

    def test_other_tier_unaffected_by_gpu_vendor(self):
        self.assertEqual(self._names(["core"], {"1002"}),
                         self._names(["core"], {"10de"}))


class TestNestedAndNamedDiscovery(unittest.TestCase):
    """G3-10: the installer must discover packages the SAME way the build does
    (rglob package.yml at any depth, archive named from the yaml `name:` field),
    so a package nested deeper than packages/<tier>/<pkg>/ — or whose dir name
    differs from its yaml name — is not silently dropped from the install set.

    The live case: websockets shipped at packages/extra/intergen-web-ui/
    websockets/; the build produced websockets-16.0.igos.tar.gz but the
    installer's fixed-depth, dir-name-keyed walk never put 'websockets' in the
    selected-tier set, so it was filtered out (826 staged -> 824 installed).
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.archive_dir = Path(self.tmp) / "archives"
        self.archive_dir.mkdir()
        self.pkg_dir = Path(self.tmp) / "packages"
        for name in ("bash-5.2", "websockets-16.0", "nodejs-22.0",
                     "renamed-3.1"):
            (self.archive_dir / f"{name}.igos.tar.gz").write_text("x")
        # core/bash (canonical), extra/nodejs (canonical)
        self._mk_yml("core/bash", "bash")
        self._mk_yml("extra/nodejs", "nodejs")
        # extra/intergen-web-ui/websockets — NESTED one level too deep, the
        # G3-10 anomaly: dir is 'websockets' but parent is a grouping dir.
        self._mk_yml("extra/intergen-web-ui/websockets", "websockets")
        # extra/zztool — dir name != yaml name ('renamed'), archive 'renamed'.
        self._mk_yml("extra/zztool", "renamed")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _mk_yml(self, relpath, name):
        d = self.pkg_dir / relpath
        d.mkdir(parents=True)
        tier = relpath.split("/")[0]
        (d / "package.yml").write_text(f"name: {name}\ntier: {tier}\n")

    def _names(self, groups):
        out = get_group_packages(groups, str(self.archive_dir),
                                 str(self.pkg_dir), detected_vendors={"1002"})
        return {n for (n, _v, _p) in out}

    def test_nested_package_discovered(self):
        # websockets is nested under intergen-web-ui/ — must still install.
        self.assertIn("websockets", self._names(["extra"]))

    def test_dir_name_ne_yaml_name_discovered(self):
        # dir 'zztool', yaml name 'renamed', archive 'renamed-3.1' — must
        # resolve by the authoritative yaml name, not the dir name.
        self.assertIn("renamed", self._names(["extra"]))

    def test_canonical_packages_still_resolve(self):
        self.assertIn("nodejs", self._names(["extra"]))
        self.assertIn("bash", self._names(["core"]))

    def test_nested_package_not_pulled_when_tier_unselected(self):
        # websockets lives in extra; a core-only install must not pull it.
        self.assertNotIn("websockets", self._names(["core"]))

    def test_gap_audit_clean_when_all_honored(self):
        gap = compute_install_set_gap(["extra"], str(self.archive_dir),
                                     str(self.pkg_dir),
                                      detected_vendors={"1002"})
        self.assertEqual(gap, [])

    def test_gap_audit_flags_a_dropped_archive(self):
        # Stage an archive whose package.yml claims tier=extra but whose dir is
        # only discoverable as a name the resolver can map -> if we instead put
        # an archive with NO matching package.yml anywhere, it maps to no tier
        # and is NOT 'expected'; the genuine net is a pipeline drop. Simulate a
        # resolver-side drop by monkeypatching get_group_packages to omit one
        # known selected-tier package, and assert the audit names it.
        import installer.backend.packages as pkgs
        real = pkgs.get_group_packages

        def _drop_websockets(groups, ad, pd, dv=None):
            return [t for t in real(groups, ad, pd, dv) if t[0] != "websockets"]

        with patch.object(pkgs, "get_group_packages", _drop_websockets):
            gap = compute_install_set_gap(["extra"], str(self.archive_dir),
                                         str(self.pkg_dir),
                                          detected_vendors={"1002"})
        self.assertIn(("websockets", "16.0"), gap)


if __name__ == "__main__":
    unittest.main()


class TestEulaGate(unittest.TestCase):
    """PI-Z6: a package declaring `eula_helper` in package.yml must be OMITTED
    from Forge's non-interactive install set regardless of hardware match —
    its license gate needs an interactive TTY accept that a Forge install can
    never provide (the Zephyrus first-NVIDIA install manufactured a spurious
    'package failed' line this way). The omission is policy, not loss: the
    silent-loss audit must treat it as legitimate.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.archive_dir = Path(self.tmp) / "archives"
        self.archive_dir.mkdir()
        self.pkg_dir = Path(self.tmp) / "packages"
        for name in ("nvidia-580.159.04", "firefox-1.0"):
            (self.archive_dir / f"{name}.igos.tar.gz").write_text("x")
        d = self.pkg_dir / "extra" / "nvidia"
        d.mkdir(parents=True)
        (d / "package.yml").write_text(
            'name: nvidia\ntier: extra\nrequires_pci_vendor: "10de"\n'
            "eula_helper: nvidia-eula\n")
        d = self.pkg_dir / "extra" / "firefox"
        d.mkdir(parents=True)
        (d / "package.yml").write_text("name: firefox\ntier: extra\n")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _names(self, vendors):
        out = get_group_packages(["extra"], str(self.archive_dir),
                                 str(self.pkg_dir), detected_vendors=vendors)
        return {n for (n, _v, _p) in out}

    def test_eula_package_omitted_even_on_matching_hardware(self):
        names = self._names({"10de"})  # NVIDIA present — still omitted
        self.assertNotIn("nvidia", names)
        self.assertIn("firefox", names)

    def test_eula_omission_is_not_a_silent_loss(self):
        from installer.backend.packages import compute_install_set_gap
        gaps = compute_install_set_gap(["extra"], str(self.archive_dir),
                                     str(self.pkg_dir),
                                      detected_vendors={"10de"})
        self.assertEqual(gaps, [], f"eula omission flagged as loss: {gaps}")


class TestRuntimeDepClosureYmlChain(unittest.TestCase):
    """Cross-tier runtime-dep closure through a CHAIN of pulled packages, and
    the loud-warning path when a pulled package's yml is missing (PI-ge9b04-C).

    The ge9b-04 dogfood install shipped an unloadable libSDL3_ttf: freerdp's
    yml pulled sdl3-ttf by name, but sdl3-ttf's own package.yml was absent
    from the installer-hooks tree (the fold skipped yml-only recipes), so its
    plutosvg/plutovg runtime deps were silently never pulled. These tests pin
    (1) the closure walks a dep chain to a fixpoint when the ymls are present
    — even across unselected tiers — and (2) a missing yml on a selected or
    pulled package warns LOUDLY instead of skipping silently.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.archive_dir = Path(self.tmp) / "archives"
        self.archive_dir.mkdir()
        self.pkg_dir = Path(self.tmp) / "packages"
        for name in ("freerdp-3.22.0", "sdl3-ttf-3.2.2",
                     "plutosvg-0.0.8", "plutovg-1.3.3"):
            (self.archive_dir / f"{name}.igos.tar.gz").write_text("x")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _mk_pkg(self, tier, name, runtime=()):
        d = self.pkg_dir / tier / name
        d.mkdir(parents=True)
        dep_block = ""
        if runtime:
            deps = "".join(f"  - {r}\n" for r in runtime)
            dep_block = f"dependencies:\n  runtime:\n{deps}"
        (d / "package.yml").write_text(
            f"name: {name}\ntier: {tier}\n{dep_block}")

    def _names(self, groups):
        out = get_group_packages(groups, str(self.archive_dir),
                                 str(self.pkg_dir), detected_vendors={"1002"})
        return {n for (n, _v, _p) in out}

    def test_chain_closes_to_fixpoint_across_unselected_tiers(self):
        # Only "desktop-gnome" is selected; the chain's tail lives in the
        # UNSELECTED extra tier and must still be pulled, transitively.
        self._mk_pkg("desktop", "freerdp", runtime=("sdl3-ttf",))
        self._mk_pkg("extra", "sdl3-ttf", runtime=("plutosvg",))
        self._mk_pkg("extra", "plutosvg", runtime=("plutovg",))
        self._mk_pkg("extra", "plutovg")
        names = self._names(["desktop-gnome"])
        self.assertIn("freerdp", names)
        self.assertIn("sdl3-ttf", names)
        self.assertIn("plutosvg", names,
                      "dep-of-pulled-dep not closed over — the PI-ge9b04-C "
                      "chain broke again")
        self.assertIn("plutovg", names)

    def test_missing_yml_on_pulled_package_warns_loudly(self):
        # sdl3-ttf has an archive but NO package.yml anywhere (the stale
        # hooks-tree shape): it is pulled by name, its deps are unreachable,
        # and that MUST be said loudly — never a silent skip.
        self._mk_pkg("desktop", "freerdp", runtime=("sdl3-ttf",))
        self._mk_pkg("extra", "plutosvg", runtime=("plutovg",))
        self._mk_pkg("extra", "plutovg")
        with self.assertLogs("forge.packages", level="WARNING") as cm:
            names = self._names(["desktop-gnome"])
        self.assertIn("sdl3-ttf", names)          # pulled by name
        self.assertNotIn("plutosvg", names)       # unreachable through no-yml
        self.assertTrue(
            any("sdl3-ttf" in m and "package.yml" in m for m in cm.output),
            f"expected a loud no-yml warning naming sdl3-ttf, got: {cm.output}")
