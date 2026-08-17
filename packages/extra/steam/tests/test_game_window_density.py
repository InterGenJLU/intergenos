"""Tests for igos-game-window-density and the Steam wrapper that calls it.

The command matches a Wine/Proton game prefix's interface density to the
display scale. What these tests hold down, in order of how much a defect
would cost:

  * the density arithmetic and its refusal range -- a wrong number written
    into every future prefix is the expensive failure;
  * the scale-source order and each source's bounds, including the measured
    reason Xft.dpi is not consulted under Wayland (GNOME publishes an
    integer-rounded 192 to XWayland on a 1.333-scale panel);
  * the generated protonfixes hook: that it is valid Python, that it calls
    the shipped global default it displaces (so the -pf_* Steam launch
    options survive), and that it never overwrites a file someone else
    wrote at the same path;
  * the direct registry edit: both sections set, a section that is absent
    appended, an existing value replaced in place, the rest of the file
    left byte-identical, a backup made once and not again;
  * the refusal while Steam or Wine is running, driven from a planted
    /proc tree, and the fact that process selection reads the kernel's
    executable name rather than a command line;
  * the recipe and wrapper wiring: the command is installed, listed in
    verify_paths, and invoked from the launch wrapper without being able
    to stop a launch.
"""

import importlib.util
import re
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_DIR.parent.parent.parent
COMMAND_SOURCE = PACKAGE_DIR / "assets" / "igos-game-window-density.py"
BUILD_SH = PACKAGE_DIR / "build.sh"
PACKAGE_YML = PACKAGE_DIR / "package.yml"

_spec = importlib.util.spec_from_file_location("igos_game_window_density", COMMAND_SOURCE)
gwd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gwd)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class DensityArithmeticTests(unittest.TestCase):
    def test_known_scales_give_the_measured_wine_densities(self):
        self.assertEqual(gwd.logpixels_for_scale(1.0), 96)
        # The built-in panel this was developed against reports exactly this
        # value from the compositor; 96 x it rounds to 128.
        self.assertEqual(gwd.logpixels_for_scale(1.3333333730697632), 128)
        self.assertEqual(gwd.logpixels_for_scale(1.5), 144)
        self.assertEqual(gwd.logpixels_for_scale(2.0), 192)

    def test_a_scale_whose_product_falls_just_short_rounds_up(self):
        # GNOME's own list of supported scales holds values stored just BELOW
        # the fraction they represent. 96 x 5/3 as GNOME stores it is
        # 159.99999618..., and truncating instead of rounding would write 159
        # dots per inch where 160 is meant. The 1.333 case above cannot catch
        # that, because GNOME stores that one just ABOVE 4/3.
        self.assertEqual(gwd.logpixels_for_scale(1.6666666269302368), 160)
        self.assertEqual(gwd.logpixels_for_scale(3.3333332538604736), 320)

    def test_a_scale_below_the_wine_default_is_refused(self):
        with self.assertRaises(gwd.DensityError) as caught:
            gwd.logpixels_for_scale(0.5)
        self.assertIn("outside the accepted range", str(caught.exception))

    def test_an_absurd_scale_is_refused_rather_than_written(self):
        with self.assertRaises(gwd.DensityError):
            gwd.logpixels_for_scale(9.0)

    def test_the_dword_rendering_is_eight_hex_digits(self):
        self.assertEqual(gwd._dword(128), "dword:00000080")
        self.assertEqual(gwd._dword(192), "dword:000000c0")


class ScaleSourceTests(unittest.TestCase):
    def setUp(self):
        self.env_patches = []

    def _set_env(self, name, value):
        import os

        previous = os.environ.get(name)
        self.env_patches.append((name, previous))
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

    def tearDown(self):
        import os

        for name, previous in reversed(self.env_patches):
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous

    def test_the_environment_override_is_consulted_before_the_session(self):
        self._set_env("IGOS_GAME_WINDOW_DENSITY_SCALE", "1.75")
        scale, source = gwd.detect_scale()
        self.assertEqual(scale, 1.75)
        self.assertIn("environment variable", source)

    def test_a_non_numeric_override_is_refused_not_ignored(self):
        self._set_env("IGOS_GAME_WINDOW_DENSITY_SCALE", "big")
        with self.assertRaises(gwd.DensityError):
            gwd.scale_from_environment_override()

    def test_a_zero_override_is_refused(self):
        self._set_env("IGOS_GAME_WINDOW_DENSITY_SCALE", "0")
        with self.assertRaises(gwd.DensityError):
            gwd.scale_from_environment_override()

    def test_xft_dpi_is_not_consulted_under_wayland(self):
        # The measured reason this source is bounded to X11: on a Wayland
        # session with a 1.333-scale panel, GNOME publishes Xft.dpi 192 to
        # XWayland clients, which would give a 2x density on a 1.333x panel.
        self._set_env("XDG_SESSION_TYPE", "wayland")
        self.assertIsNone(gwd.scale_from_xft_dpi())

    def test_the_saved_monitor_configuration_prefers_the_primary_monitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                Path(tmp) / "monitors.xml",
                """<monitors version="2">
  <configuration>
    <logicalmonitor><scale>1.5</scale></logicalmonitor>
    <logicalmonitor><scale>1.25</scale><primary>yes</primary></logicalmonitor>
  </configuration>
</monitors>
""",
            )
            self.assertEqual(gwd.scale_from_saved_monitor_config(path), 1.25)

    def test_the_saved_monitor_configuration_falls_back_to_the_first_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                Path(tmp) / "monitors.xml",
                '<monitors version="2"><configuration>'
                "<logicalmonitor><scale>1.5</scale></logicalmonitor>"
                "</configuration></monitors>",
            )
            self.assertEqual(gwd.scale_from_saved_monitor_config(path), 1.5)

    def test_an_unparseable_monitor_configuration_answers_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "monitors.xml", "<monitors")
            self.assertIsNone(gwd.scale_from_saved_monitor_config(path))

    def test_a_missing_monitor_configuration_answers_nothing(self):
        self.assertIsNone(
            gwd.scale_from_saved_monitor_config(Path("/nonexistent/monitors.xml"))
        )

    def test_no_readable_source_refuses_and_names_the_option(self):
        self._set_env("IGOS_GAME_WINDOW_DENSITY_SCALE", None)
        original = (
            gwd.scale_from_compositor,
            gwd.scale_from_xft_dpi,
            gwd.scale_from_saved_monitor_config,
        )
        gwd.scale_from_compositor = lambda: None
        gwd.scale_from_xft_dpi = lambda: None
        gwd.scale_from_saved_monitor_config = lambda *a, **k: None
        try:
            with self.assertRaises(gwd.DensityError) as caught:
                gwd.detect_scale()
        finally:
            (
                gwd.scale_from_compositor,
                gwd.scale_from_xft_dpi,
                gwd.scale_from_saved_monitor_config,
            ) = original
        self.assertIn("--scale", str(caught.exception))


class GeneratedHookTests(unittest.TestCase):
    def setUp(self):
        self.text = gwd.hook_text(128, 1.3333333730697632, "a test")

    def test_the_generated_hook_is_valid_python(self):
        compile(self.text, "default.py", "exec")

    def test_the_generated_hook_carries_the_density_it_was_given(self):
        self.assertIn("IGOS_LOGPIXELS = 128", self.text)

    def test_the_generated_hook_exposes_both_protonfixes_stages(self):
        self.assertIn("def early_with_id(", self.text)
        self.assertIn("def main_with_id(", self.text)

    def test_the_marker_appears_exactly_once_so_deleting_it_hands_the_file_over(self):
        # The file's own instructions tell a user to delete the marker to take
        # the file over. A second copy of the marker anywhere in the text
        # would make that instruction untrue.
        self.assertEqual(self.text.count(gwd.HOOK_MARKER), 1)

    def test_a_density_failure_in_the_hook_cannot_stop_a_game_starting(self):
        self.assertIn("could not set game window density", self.text)


class _RecordingProtonfixes:
    """A stand-in for the protonfixes modules the generated hook imports.

    Faithful to the real API surface the hook uses: util.regedit_add's four
    positional arguments, logger.log.info/warn, fix._run_fix's four
    positional arguments, and config.main.enable_global_fixes. The
    ShippedPayloadAgreementTests class below checks those signatures against
    the real payload wherever it is installed, so this stub cannot drift into
    agreeing with a hook that the real protonfixes would reject.
    """

    def __init__(self, enable_global_fixes=True, regedit_error=None):
        self.registry_calls = []
        self.shipped_default_calls = []
        self.warnings = []
        self.info = []
        self.regedit_error = regedit_error
        self.enable_global_fixes = enable_global_fixes

    def install(self):
        import types

        recorder = self

        util = types.ModuleType("protonfixes.util")

        def regedit_add(folder, name=None, typ=None, value=None, arch=False):
            if recorder.regedit_error is not None:
                raise recorder.regedit_error
            recorder.registry_calls.append((folder, name, typ, value))

        util.regedit_add = regedit_add

        logger = types.ModuleType("protonfixes.logger")

        class _Log:
            def info(self, message):
                recorder.info.append(message)

            def warn(self, message):
                recorder.warnings.append(message)

            def crit(self, message):
                recorder.warnings.append(message)

            def debug(self, message):
                pass

        logger.log = _Log()

        fix = types.ModuleType("protonfixes.fix")

        def _run_fix(game_id, stage, default=False, local=False):
            recorder.shipped_default_calls.append((game_id, stage, default, local))
            return True

        fix._run_fix = _run_fix

        config_module = types.ModuleType("protonfixes.config")

        class _Main:
            enable_global_fixes = recorder.enable_global_fixes

        class _Config:
            main = _Main()

        config_module.config = _Config()

        package = types.ModuleType("protonfixes")
        package.util = util
        package.fix = fix

        self._modules = {
            "protonfixes": package,
            "protonfixes.util": util,
            "protonfixes.logger": logger,
            "protonfixes.fix": fix,
            "protonfixes.config": config_module,
        }
        self._saved = {name: sys.modules.get(name) for name in self._modules}
        sys.modules.update(self._modules)
        return self

    def remove(self):
        for name, previous in self._saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class HookBehaviourTests(unittest.TestCase):
    """Execute the generated hook against a recording protonfixes."""

    def _load(self, logpixels=128, **stub_options):
        stub = _RecordingProtonfixes(**stub_options).install()
        self.addCleanup(stub.remove)
        namespace = {"__name__": "localfixes.default"}
        exec(compile(gwd.hook_text(logpixels, 1.333, "a test"), "default.py", "exec"), namespace)
        return stub, namespace

    def test_the_early_stage_sets_both_registry_locations_to_the_density(self):
        # Wine reads the window-metric density from one key and the font
        # density from the other; setting only one leaves the decorations
        # half-sized, which is the whole defect this command addresses.
        stub, hook = self._load(logpixels=128)
        hook["early_with_id"]("860510")
        self.assertEqual(
            stub.registry_calls,
            [
                ("HKCU\\Control Panel\\Desktop", "LogPixels", "REG_DWORD", "128"),
                ("HKCU\\Software\\Wine\\Fonts", "LogPixels", "REG_DWORD", "128"),
            ],
        )

    def test_the_density_written_is_the_one_the_hook_was_generated_with(self):
        stub, hook = self._load(logpixels=192)
        hook["early_with_id"]("620")
        self.assertEqual([call[3] for call in stub.registry_calls], ["192", "192"])

    def test_the_main_stage_does_not_write_the_registry_a_second_time(self):
        stub, hook = self._load()
        hook["main_with_id"]("860510")
        self.assertEqual(stub.registry_calls, [])

    def test_both_stages_run_the_shipped_default_the_hook_displaces(self):
        # protonfixes runs a local default INSTEAD of its shipped one, never
        # in addition. Without this call the -pf_tricks, -pf_dxvk_set and
        # -pf_replace_cmd Steam launch options stop working the moment this
        # hook is installed.
        stub, hook = self._load()
        hook["early_with_id"]("860510")
        hook["main_with_id"]("860510")
        self.assertEqual(
            stub.shipped_default_calls,
            [("860510", "early", True, False), ("860510", "main", True, False)],
        )

    def test_the_shipped_default_is_skipped_when_the_user_turned_it_off(self):
        stub, hook = self._load(enable_global_fixes=False)
        hook["main_with_id"]("860510")
        self.assertEqual(stub.shipped_default_calls, [])

    def test_a_registry_failure_is_reported_and_the_game_still_starts(self):
        stub, hook = self._load(regedit_error=RuntimeError("wine reg add failed"))
        hook["early_with_id"]("860510")  # must not raise
        self.assertTrue(
            any("could not set game window density" in w for w in stub.warnings),
            stub.warnings,
        )
        # And the displaced shipped default still ran.
        self.assertEqual(len(stub.shipped_default_calls), 1)


class ShippedPayloadAgreementTests(unittest.TestCase):
    """Check the hook's assumptions against a real installed protonfixes.

    Skipped where GE-Proton is not installed; where it is, this is what stops
    the recording stub above from agreeing with a hook the real protonfixes
    would reject.
    """

    PAYLOAD = Path("/opt/igos/compat-tools/GE-Proton11-1/protonfixes")

    def setUp(self):
        if not self.PAYLOAD.is_dir():
            self.skipTest(f"GE-Proton payload not installed at {self.PAYLOAD}")

    def test_a_local_default_suppresses_the_shipped_one(self):
        # The reason the generated hook has to call the shipped default at
        # all. If this line ever changes shape, the hook's delegation needs
        # re-deriving rather than trusting.
        text = (self.PAYLOAD / "fix.py").read_text(encoding="utf-8")
        self.assertIn(
            "if not _run_fix_local(game_id, stage, True) and "
            "config.main.enable_global_fixes:",
            text,
        )

    def test_the_private_call_the_hook_makes_exists_with_that_signature(self):
        text = (self.PAYLOAD / "fix.py").read_text(encoding="utf-8")
        self.assertIn(
            "def _run_fix(\n    game_id: str, stage: str, default: bool = False, "
            "local: bool = False\n) -> bool:",
            text,
        )

    def test_the_local_default_is_looked_for_at_the_path_the_command_writes(self):
        text = (self.PAYLOAD / "fix.py").read_text(encoding="utf-8")
        self.assertIn("'~/.config/protonfixes/localfixes'", text)
        self.assertEqual(str(gwd.HOOK_RELPATH), ".config/protonfixes/localfixes/default.py")

    def test_both_protonfixes_stages_dispatch_to_the_with_id_entry_points(self):
        text = (self.PAYLOAD / "fix.py").read_text(encoding="utf-8")
        self.assertIn("if hasattr(game_module, 'early_with_id'):", text)
        self.assertIn("if hasattr(game_module, 'main_with_id'):", text)

    def test_regedit_add_takes_the_four_arguments_the_hook_passes(self):
        text = (self.PAYLOAD / "util.py").read_text(encoding="utf-8")
        self.assertIn(
            "def regedit_add(\n    folder: str,\n    name: Optional[str] = None,\n"
            "    typ: Optional[str] = None,\n    value: Optional[str] = None,\n"
            "    arch: bool = False,\n) -> None:",
            text,
        )

    def test_the_shipped_default_is_what_parses_the_steam_launch_options(self):
        text = (self.PAYLOAD / "gamefixes-steam" / "default.py").read_text(encoding="utf-8")
        for alias in ("-pf_tricks", "-pf_dxvk_set", "-pf_replace_cmd"):
            self.assertIn(alias, text)


class HookSyncTests(unittest.TestCase):
    def test_the_hook_is_written_with_its_package_marker_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = gwd.sync_hook(128, 1.333, "a test", home=tmp)
            path = gwd.hook_path(tmp)
            self.assertEqual(result["action"], "written")
            self.assertTrue(path.is_file())
            # protonfixes imports the directory as a package.
            self.assertTrue((path.parent / "__init__.py").is_file())

    def test_a_second_run_with_the_same_density_rewrites_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            gwd.sync_hook(128, 1.333, "a test", home=tmp)
            before = gwd.hook_path(tmp).stat().st_mtime_ns
            result = gwd.sync_hook(128, 1.333, "a test", home=tmp)
            self.assertEqual(result["action"], "already current")
            self.assertEqual(gwd.hook_path(tmp).stat().st_mtime_ns, before)

    def test_a_changed_density_refreshes_the_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            gwd.sync_hook(128, 1.333, "a test", home=tmp)
            result = gwd.sync_hook(192, 2.0, "a test", home=tmp)
            self.assertEqual(result["action"], "refreshed")
            self.assertIn("IGOS_LOGPIXELS = 192", gwd.hook_path(tmp).read_text())

    def test_a_file_written_by_someone_else_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = gwd.hook_path(tmp)
            _write(path, "# my own protonfixes default\ndef main():\n    pass\n")
            result = gwd.sync_hook(128, 1.333, "a test", home=tmp)
            self.assertEqual(result["action"], "left alone")
            self.assertIn("my own protonfixes default", path.read_text())

    def test_a_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = gwd.sync_hook(128, 1.333, "a test", home=tmp, dry_run=True)
            self.assertEqual(result["action"], "would write")
            self.assertFalse(gwd.hook_path(tmp).exists())

    def test_removal_takes_the_generated_hook_and_leaves_a_foreign_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            gwd.sync_hook(128, 1.333, "a test", home=tmp)
            self.assertEqual(gwd.remove_hook(home=tmp)["action"], "removed")
            self.assertFalse(gwd.hook_path(tmp).exists())

            _write(gwd.hook_path(tmp), "# someone else's file\n")
            self.assertEqual(gwd.remove_hook(home=tmp)["action"], "left alone")
            self.assertTrue(gwd.hook_path(tmp).exists())

    def test_removing_an_absent_hook_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(gwd.remove_hook(home=tmp)["action"], "not present")


USER_REG_WITH_DESKTOP = """WINE REGISTRY Version 2
;; All keys relative to \\\\User\\\\S-1-5-21

#arch=win64

[Control Panel\\\\Desktop] 1750000000
#time=1dbf000000000000
"DragFullWindows"="1"
"LogPixels"=dword:00000060

[Software\\\\Wine\\\\X11 Driver] 1750000000
"Decorated"="Y"
"""

USER_REG_WITHOUT_EITHER = """WINE REGISTRY Version 2
;; All keys relative to \\\\User\\\\S-1-5-21

#arch=win64

[Software\\\\Wine\\\\X11 Driver] 1750000000
"Decorated"="Y"
"""


class RegistryEditTests(unittest.TestCase):
    def test_the_current_value_is_read_from_each_section(self):
        values = gwd.read_logpixels(USER_REG_WITH_DESKTOP)
        self.assertEqual(values[gwd.DESKTOP_SECTION], 0x60)
        self.assertNotIn(gwd.FONTS_SECTION, values)

    def test_an_existing_value_is_replaced_and_the_font_section_appended(self):
        updated = gwd.set_logpixels(USER_REG_WITH_DESKTOP, 128)
        values = gwd.read_logpixels(updated)
        self.assertEqual(values[gwd.DESKTOP_SECTION], 128)
        self.assertEqual(values[gwd.FONTS_SECTION], 128)
        # The old value must be gone, not merely joined by a new line.
        self.assertNotIn("dword:00000060", updated)

    def test_both_sections_are_appended_when_neither_exists(self):
        updated = gwd.set_logpixels(USER_REG_WITHOUT_EITHER, 144)
        values = gwd.read_logpixels(updated)
        self.assertEqual(values[gwd.DESKTOP_SECTION], 144)
        self.assertEqual(values[gwd.FONTS_SECTION], 144)

    def test_a_present_section_without_the_value_gains_it_in_place(self):
        text = USER_REG_WITH_DESKTOP.replace('"LogPixels"=dword:00000060\n', "")
        updated = gwd.set_logpixels(text, 128)
        self.assertEqual(gwd.read_logpixels(updated)[gwd.DESKTOP_SECTION], 128)
        # The value must land inside the Desktop section, before the next
        # section header -- otherwise Wine never reads it.
        desktop_at = updated.index("[Control Panel")
        next_section_at = updated.index("[Software\\\\Wine\\\\X11 Driver]")
        self.assertLess(desktop_at, updated.index('"LogPixels"'))
        self.assertLess(updated.index('"LogPixels"'), next_section_at)

    def test_unrelated_registry_content_is_left_byte_identical(self):
        updated = gwd.set_logpixels(USER_REG_WITH_DESKTOP, 128)
        for line in ('"DragFullWindows"="1"', '"Decorated"="Y"', "#arch=win64"):
            self.assertIn(line, updated)
        self.assertTrue(updated.startswith("WINE REGISTRY Version 2\n"))

    def test_setting_the_value_twice_is_stable(self):
        once = gwd.set_logpixels(USER_REG_WITHOUT_EITHER, 128)
        twice = gwd.set_logpixels(once, 128)
        self.assertEqual(once, twice)

    def test_carriage_returns_are_preserved(self):
        text = USER_REG_WITHOUT_EITHER.replace("\n", "\r\n")
        updated = gwd.set_logpixels(text, 128)
        # Every line ending stays the file's own; a bare newline mixed into a
        # carriage-return file is the signature of a line written blind.
        self.assertEqual(updated.count("\n"), updated.count("\r\n"))
        self.assertEqual(gwd.read_logpixels(updated)[gwd.DESKTOP_SECTION], 128)
        self.assertEqual(gwd.read_logpixels(updated)[gwd.FONTS_SECTION], 128)


class ApplyToPrefixTests(unittest.TestCase):
    def _prefix(self, tmp, text=USER_REG_WITH_DESKTOP):
        return _write(Path(tmp) / "pfx" / "user.reg", text)

    def test_a_backup_is_made_beside_the_registry_before_it_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            user_reg = self._prefix(tmp)
            result = gwd.apply_to_prefix(user_reg, 128)
            self.assertEqual(result["action"], "updated")
            backup = Path(result["backup"])
            self.assertTrue(backup.is_file())
            self.assertEqual(backup.parent, user_reg.parent)
            self.assertIn("bak-pre-logpixels-", backup.name)
            self.assertEqual(backup.read_text(), USER_REG_WITH_DESKTOP)

    def test_a_prefix_already_at_the_right_density_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            user_reg = self._prefix(tmp)
            gwd.apply_to_prefix(user_reg, 128)
            before = user_reg.read_text()
            result = gwd.apply_to_prefix(user_reg, 128)
            self.assertEqual(result["action"], "already correct")
            self.assertIsNone(result["backup"])
            self.assertEqual(user_reg.read_text(), before)
            backups = sorted(user_reg.parent.glob("user.reg.bak-pre-logpixels-*"))
            self.assertEqual(len(backups), 1)

    def test_a_dry_run_changes_nothing_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            user_reg = self._prefix(tmp)
            result = gwd.apply_to_prefix(user_reg, 128, dry_run=True)
            self.assertEqual(result["action"], "would update")
            self.assertEqual(user_reg.read_text(), USER_REG_WITH_DESKTOP)
            self.assertEqual(list(user_reg.parent.glob("*.bak-pre-logpixels-*")), [])


class PrefixDiscoveryTests(unittest.TestCase):
    def _plant(self, home, appid, library=None):
        base = Path(home) / (library or ".local/share/Steam")
        return _write(
            base / "steamapps/compatdata" / str(appid) / "pfx/user.reg",
            USER_REG_WITHOUT_EITHER,
        )

    def test_every_prefix_under_the_default_library_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plant(tmp, 860510)
            self._plant(tmp, 620)
            found = gwd.find_prefixes(home=tmp)
            self.assertEqual(sorted(appid for appid, _ in found), ["620", "860510"])

    def test_one_application_id_can_be_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plant(tmp, 860510)
            self._plant(tmp, 620)
            found = gwd.find_prefixes(home=tmp, appid=620)
            self.assertEqual([appid for appid, _ in found], ["620"])

    def test_a_library_named_in_libraryfolders_is_searched_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            extra = Path(tmp) / "games/SteamLibrary"
            _write(
                extra / "steamapps/compatdata/1091500/pfx/user.reg",
                USER_REG_WITHOUT_EITHER,
            )
            _write(
                Path(tmp) / ".local/share/Steam/steamapps/libraryfolders.vdf",
                '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % extra,
            )
            found = gwd.find_prefixes(home=tmp)
            self.assertIn("1091500", [appid for appid, _ in found])

    def test_a_directory_without_a_registry_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".local/share/Steam/steamapps/compatdata/999").mkdir(
                parents=True
            )
            self.assertEqual(gwd.find_prefixes(home=tmp), [])


class RunningProcessTests(unittest.TestCase):
    def _plant_proc(self, root, pid, comm):
        _write(Path(root) / str(pid) / "comm", comm + "\n")

    def test_a_running_steam_is_detected_by_its_executable_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plant_proc(tmp, 101, "steam")
            self._plant_proc(tmp, 102, "bash")
            self.assertEqual(gwd.running_prefix_holders(tmp), {"steam"})

    def test_a_running_wineserver_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plant_proc(tmp, 201, "wineserver")
            self.assertEqual(gwd.running_prefix_holders(tmp), {"wineserver"})

    def test_an_idle_machine_reports_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plant_proc(tmp, 301, "systemd")
            self._plant_proc(tmp, 302, "gnome-shell")
            self.assertEqual(gwd.running_prefix_holders(tmp), set())

    def test_selection_reads_the_executable_name_not_a_command_line(self):
        # A pattern match over /proc/<pid>/cmdline reads the searching process
        # back as a hit whenever its own arguments mention the name. comm is
        # filled by the kernel from the executable, so it cannot.
        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp) / "401" / "comm", "grep\n")
            _write(Path(tmp) / "401" / "cmdline", "grep\0steamwebhelper\0")
            self.assertEqual(gwd.running_prefix_holders(tmp), set())

    def test_a_non_numeric_directory_entry_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "self").mkdir()
            self._plant_proc(tmp, 501, "steam")
            self.assertEqual(gwd.running_prefix_holders(tmp), {"steam"})


class CommandLineTests(unittest.TestCase):
    def _run(self, argv):
        out, err = StringIO(), StringIO()
        code = gwd.main(argv, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def test_show_reports_the_scale_the_density_and_the_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = self._run(["--scale", "1.5", "--home", tmp, "show"])
            self.assertEqual(code, 0)
            self.assertIn("display scale:  1.5", out)
            self.assertIn("144 dots per inch", out)
            self.assertIn("--scale option", out)

    def test_show_names_a_prefix_that_does_not_match_the_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                Path(tmp) / ".local/share/Steam/steamapps/compatdata/860510/pfx/user.reg",
                USER_REG_WITH_DESKTOP,
            )
            _, out, _ = self._run(["--scale", "1.5", "--home", tmp, "show"])
            self.assertIn("860510", out)
            self.assertIn("does not match the panel", out)

    def test_sync_hook_from_the_command_line_writes_the_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = self._run(["--scale", "2.0", "--home", tmp, "sync-hook"])
            self.assertEqual(code, 0)
            self.assertIn("written", out)
            self.assertIn("IGOS_LOGPIXELS = 192", gwd.hook_path(tmp).read_text())

    def test_apply_refuses_while_steam_is_running_and_says_why(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                Path(tmp) / ".local/share/Steam/steamapps/compatdata/860510/pfx/user.reg",
                USER_REG_WITH_DESKTOP,
            )
            original = gwd.running_prefix_holders
            gwd.running_prefix_holders = lambda *a, **k: {"steam"}
            try:
                code, _, err = self._run(["--scale", "1.5", "--home", tmp, "apply", "--all"])
            finally:
                gwd.running_prefix_holders = original
            self.assertEqual(code, 1)
            self.assertIn("Steam or a Wine process is running", err)
            self.assertIn("discard this edit", err)
            # And the registry is untouched.
            self.assertIn("dword:00000060", (
                Path(tmp) / ".local/share/Steam/steamapps/compatdata/860510/pfx/user.reg"
            ).read_text())

    def test_apply_updates_every_prefix_when_nothing_is_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = _write(
                Path(tmp) / ".local/share/Steam/steamapps/compatdata/860510/pfx/user.reg",
                USER_REG_WITH_DESKTOP,
            )
            original = gwd.running_prefix_holders
            gwd.running_prefix_holders = lambda *a, **k: set()
            try:
                code, out, _ = self._run(["--scale", "1.5", "--home", tmp, "apply", "--all"])
            finally:
                gwd.running_prefix_holders = original
            self.assertEqual(code, 0)
            self.assertIn("updated", out)
            self.assertEqual(gwd.read_logpixels(reg.read_text())[gwd.DESKTOP_SECTION], 144)

    def test_apply_reports_when_no_prefix_matches_the_application_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = gwd.running_prefix_holders
            gwd.running_prefix_holders = lambda *a, **k: set()
            try:
                code, _, err = self._run(
                    ["--scale", "1.5", "--home", tmp, "apply", "--appid", "1"]
                )
            finally:
                gwd.running_prefix_holders = original
            self.assertEqual(code, 1)
            self.assertIn("No game prefix found", err)

    def test_apply_requires_an_application_id_or_all(self):
        with self.assertRaises(SystemExit):
            gwd.main(["apply"], out=StringIO(), err=StringIO())

    def test_no_subcommand_prints_help_without_claiming_success(self):
        code, out, _ = self._run([])
        self.assertEqual(code, 2)
        self.assertIn("igos-game-window-density", out)

    def test_the_shipped_file_runs_as_a_command(self):
        result = subprocess.run(
            [sys.executable, str(COMMAND_SOURCE), "--scale", "1.5", "show"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("144 dots per inch", result.stdout)


class RecipeWiringTests(unittest.TestCase):
    def setUp(self):
        self.build_sh = BUILD_SH.read_text(encoding="utf-8")
        self.package_yml = PACKAGE_YML.read_text(encoding="utf-8")

    def test_the_command_is_installed_from_the_repository_file(self):
        self.assertIn(
            'install -m 755 "$BUILD_DIR/assets/igos-game-window-density.py"',
            self.build_sh,
        )
        self.assertIn('"${DESTDIR}/usr/bin/igos-game-window-density"', self.build_sh)

    def test_build_dir_is_defined_before_it_is_used(self):
        defined_at = self.build_sh.index("BUILD_DIR=")
        used_at = self.build_sh.index('"$BUILD_DIR/assets/')
        self.assertLess(defined_at, used_at)

    def test_the_installed_command_is_listed_in_verify_paths(self):
        self.assertIn("- /usr/bin/igos-game-window-density", self.package_yml)

    def test_the_launch_wrapper_syncs_the_hook_before_starting_steam(self):
        wrapper = self.build_sh[
            self.build_sh.index("<<'WRAPEOF'") : self.build_sh.index("WRAPEOF\n", 100)
        ]
        self.assertIn("igos-game-window-density --quiet sync-hook", wrapper)
        sync_at = wrapper.index("igos-game-window-density --quiet sync-hook")
        exec_at = wrapper.index('exec "$BOOTSTRAP"')
        self.assertLess(sync_at, exec_at)

    def test_a_density_failure_cannot_stop_steam_from_launching(self):
        # The call is guarded and its failure only prints; if this ever became
        # a bare unguarded call under `set -e`, a density problem would become
        # a Steam that will not start.
        self.assertIn("starting Steam anyway", self.build_sh)
        self.assertNotIn("\nigos-game-window-density", self.build_sh)

    def test_the_release_was_bumped_for_this_change(self):
        match = re.search(r"^release:\s*(\d+)", self.package_yml, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertGreaterEqual(int(match.group(1)), 4)


if __name__ == "__main__":
    unittest.main()
