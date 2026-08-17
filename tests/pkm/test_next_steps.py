#!/usr/bin/env python3
"""pkm.services — relogin tier, boot-artifact reboot inference, and the
consolidated end-of-transaction "Next steps" block (format_next_steps).

Extends the Q5 classification coverage in test_q5_q6_helpers.py with:
  - classify_restart_requirement's manifest-inference reboot fallback
    (_BOOT_ARTIFACT_RE) for packages NOT in REBOOT_TRIGGER_PACKAGES;
  - the NEW "relogin" tier (_RELOGIN_RE) for desktop-shell payloads;
  - the classification precedence reboot > restart > relogin > none;
  - the systemd-pass2 and gnome-shell reboot-name additions;
  - format_next_steps rendering each tier, the combined block, the
    "Active now" tally, and the empty (nothing-actionable) case;
  - _print_transaction_next_steps printing the block (or nothing).

systemctl-driven cases use the same fake-bin PATH override pattern as
test_q5_q6_helpers.py.
"""

import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from pkm.services import (
    classify_restart_requirement,
    format_next_steps,
)
from pkm.cli import _print_transaction_next_steps


def _make_fake_systemctl(bindir, behavior):
    """Fake systemctl stub keyed on {unit_name: is-active exit code}.

    "default" is the fallback exit code for unknown units (3 = inactive).
    """
    path = Path(bindir) / "systemctl"
    behavior_lines = []
    for unit, exit_code in behavior.items():
        if unit != "default":
            behavior_lines.append(f'    "{unit}") exit {exit_code} ;;')
    default_code = behavior.get("default", 3)
    script = f"""#!/bin/bash
ACTION="$1"; shift
if [ "$1" = "--quiet" ]; then shift; fi
UNIT="$1"
case "$ACTION" in
    is-active)
        case "$UNIT" in
{chr(10).join(behavior_lines)}
            *) exit {default_code} ;;
        esac ;;
    *) exit 0 ;;
esac
"""
    path.write_text(script)
    path.chmod(0o755)
    return path


class ClassifyBootArtifactRebootTests(unittest.TestCase):
    """The manifest-inference reboot fallback for packages not in the
    structural REBOOT_TRIGGER_PACKAGES name set."""

    def test_out_of_tree_kernel_module_infers_reboot(self):
        r = classify_restart_requirement(
            "nvidia", ["usr/lib/modules/6.6.0-igos/nvidia.ko"]
        )
        self.assertEqual(r["requirement"], "reboot")

    def test_boot_dir_artifact_infers_reboot(self):
        r = classify_restart_requirement("some-loader", ["boot/vmlinuz-6.6"])
        self.assertEqual(r["requirement"], "reboot")

    def test_initramfs_artifact_infers_reboot(self):
        r = classify_restart_requirement(
            "dracut-hook", ["usr/lib/dracut/initramfs-tools/hook"]
        )
        # "initramfs" appears mid-path -> boot-artifact reboot.
        self.assertEqual(r["requirement"], "reboot")

    def test_declared_reboot_required_still_wins(self):
        r = classify_restart_requirement(
            "weird", ["usr/bin/weird"], declared_reboot_required=True
        )
        self.assertEqual(r["requirement"], "reboot")


class ClassifyReloginTierTests(unittest.TestCase):
    """The NEW relogin tier for desktop-shell payloads. None of these
    ship service units, so systemctl is never consulted."""

    def test_gnome_shell_extension_is_relogin(self):
        r = classify_restart_requirement(
            "my-ext",
            ["usr/share/gnome-shell/extensions/foo@bar/extension.js"],
        )
        self.assertEqual(r["requirement"], "relogin")
        self.assertEqual(r["services"], [])

    def test_icon_theme_index_is_relogin(self):
        r = classify_restart_requirement(
            "papirus", ["usr/share/icons/Papirus/index.theme"]
        )
        self.assertEqual(r["requirement"], "relogin")

    def test_gtk_theme_index_is_relogin(self):
        r = classify_restart_requirement(
            "arc-theme", ["usr/share/themes/Arc/index.theme"]
        )
        self.assertEqual(r["requirement"], "relogin")

    def test_stray_app_icon_is_not_relogin(self):
        # A normal app shipping a hicolor icon must NOT be flagged relogin:
        # the icon-theme pattern is anchored to <theme>/index.theme, not to
        # arbitrary icon files.
        r = classify_restart_requirement(
            "someapp",
            ["usr/share/icons/hicolor/48x48/apps/someapp.png",
             "usr/bin/someapp"],
        )
        self.assertEqual(r["requirement"], "none")


class ClassifyPrecedenceTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pkm-nextsteps-")
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        self._orig_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin}:{self._orig_path}"

    def tearDown(self):
        os.environ["PATH"] = self._orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reboot_name_beats_relogin_payload(self):
        # gnome-shell IS in REBOOT_TRIGGER_PACKAGES (change 5): the
        # forced-loud reboot wins over its own relogin-shaped payload.
        r = classify_restart_requirement(
            "gnome-shell",
            ["usr/share/gnome-shell/extensions/core@igos/extension.js"],
        )
        self.assertEqual(r["requirement"], "reboot")

    def test_active_service_beats_relogin(self):
        # A package shipping BOTH an active service unit AND a theme is a
        # restart, not a relogin — restart outranks relogin.
        _make_fake_systemctl(self.bin, {"daemon.service": 0, "default": 3})
        r = classify_restart_requirement(
            "hybrid",
            ["usr/lib/systemd/system/daemon.service",
             "usr/share/themes/Hybrid/index.theme"],
        )
        self.assertEqual(r["requirement"], "restart")
        self.assertEqual(r["services"], ["daemon.service"])

    def test_inactive_service_with_theme_is_relogin(self):
        # Service unit present but not running, plus a theme -> relogin
        # (restart requires a *running* unit; relogin then outranks none).
        _make_fake_systemctl(self.bin, {"default": 3})
        r = classify_restart_requirement(
            "hybrid-off",
            ["usr/lib/systemd/system/daemon.service",
             "usr/share/themes/Hybrid/index.theme"],
        )
        self.assertEqual(r["requirement"], "relogin")


class RebootNameSetAdditionsTests(unittest.TestCase):
    """Changes 4 + 5: systemd-pass2 and gnome-shell classify as reboot."""

    def test_systemd_pass2_is_reboot(self):
        r = classify_restart_requirement("systemd-pass2", [])
        self.assertEqual(r["requirement"], "reboot")

    def test_gnome_shell_is_reboot(self):
        r = classify_restart_requirement("gnome-shell", [])
        self.assertEqual(r["requirement"], "reboot")


def _cls(req, services=None):
    return {"requirement": req, "services": services or [], "reason": "x"}


class FormatNextStepsTests(unittest.TestCase):

    def test_all_none_returns_empty(self):
        self.assertEqual(
            format_next_steps([("a", _cls("none")), ("b", _cls("none"))]),
            "",
        )

    def test_empty_input_returns_empty(self):
        self.assertEqual(format_next_steps([]), "")

    def test_reboot_section(self):
        out = format_next_steps([("linux-kernel", _cls("reboot"))])
        self.assertIn("REBOOT REQUIRED", out)
        self.assertIn("sudo reboot", out)
        self.assertIn("linux-kernel", out)

    def test_restart_section_dedupes_units(self):
        out = format_next_steps([
            ("nginx", _cls("restart", ["nginx.service"])),
            ("nginx-extras", _cls("restart", ["nginx.service", "php.service"])),
        ])
        self.assertIn("RESTART SERVICES", out)
        self.assertIn("pkm restart-services --all", out)
        # nginx.service listed once despite appearing in both packages.
        self.assertEqual(out.count("- nginx.service"), 1)
        self.assertIn("- php.service", out)

    def test_relogin_section(self):
        out = format_next_steps([("arc-theme", _cls("relogin"))])
        self.assertIn("LOG OUT AND BACK IN", out)
        self.assertIn("arc-theme", out)

    def test_combined_block_ordering_and_tally(self):
        out = format_next_steps([
            ("linux-kernel", _cls("reboot")),
            ("nginx", _cls("restart", ["nginx.service"])),
            ("arc-theme", _cls("relogin")),
            ("vim", _cls("none")),
            ("less", _cls("none")),
        ])
        # Strongest-first ordering: reboot before restart before relogin.
        i_reboot = out.index("REBOOT REQUIRED")
        i_restart = out.index("RESTART SERVICES")
        i_relogin = out.index("LOG OUT AND BACK IN")
        self.assertLess(i_reboot, i_restart)
        self.assertLess(i_restart, i_relogin)
        # The two none-classified packages surface as a no-action tally.
        self.assertIn("Active now (no action): 2 package(s)", out)

    def test_active_now_tally_not_shown_alone(self):
        # none-only is not actionable -> empty (no tally rendered alone).
        self.assertEqual(
            format_next_steps([("vim", _cls("none"))]),
            "",
        )


class _FakeDB:
    """Minimal db exposing the two accessors _print_transaction_next_steps
    uses: get_files(name) -> [{"path","is_dir"}], get_installed(name) -> row.
    """

    def __init__(self, files_by_name, rows_by_name=None):
        self._files = files_by_name
        self._rows = rows_by_name or {}

    def get_files(self, name):
        return [
            {"path": p, "is_dir": p.endswith("/")}
            for p in self._files.get(name, [])
        ]

    def get_installed(self, name):
        return self._rows.get(name)


class PrintTransactionNextStepsTests(unittest.TestCase):

    def test_prints_block_for_reboot_package(self):
        db = _FakeDB({"linux-kernel": ["boot/vmlinuz-6.6"]})
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_transaction_next_steps(db, ["linux-kernel"])
        out = buf.getvalue()
        self.assertIn("REBOOT REQUIRED", out)
        self.assertIn("linux-kernel", out)

    def test_prints_nothing_when_all_none(self):
        db = _FakeDB({"vim": ["usr/bin/vim"]})
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_transaction_next_steps(db, ["vim"])
        self.assertEqual(buf.getvalue(), "")

    def test_relogin_block_from_manifest(self):
        db = _FakeDB(
            {"arc": ["usr/share/themes/Arc/index.theme"]}
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_transaction_next_steps(db, ["arc"])
        self.assertIn("LOG OUT AND BACK IN", buf.getvalue())


class EstimateModeTests(unittest.TestCase):
    """CUT-028 change 4: the pre-transaction plan summary renders the FULL
    classification ladder through the same renderer, marked as an estimate."""

    def test_estimate_banner_and_note_present(self):
        out = format_next_steps([("linux-kernel", _cls("reboot"))], estimate=True)
        self.assertIn("NEXT STEPS (ESTIMATE — before upgrade)", out)
        self.assertIn("authoritative", out)
        self.assertIn("after the upgrade", out)

    def test_authoritative_has_no_estimate_framing(self):
        out = format_next_steps([("linux-kernel", _cls("reboot"))])
        self.assertIn("NEXT STEPS", out)
        self.assertNotIn("ESTIMATE", out)
        self.assertNotIn("authoritative", out)

    def test_estimate_renders_full_ladder(self):
        # All four tiers appear in one estimate block (the coarse old preview
        # showed only reboot + restart).
        out = format_next_steps([
            ("linux-kernel", _cls("reboot")),
            ("nginx", _cls("restart", ["nginx.service"])),
            ("arc-theme", _cls("relogin")),
            ("vim", _cls("none")),
        ], estimate=True)
        self.assertIn("REBOOT REQUIRED", out)
        self.assertIn("RESTART SERVICES", out)
        self.assertIn("LOG OUT AND BACK IN", out)
        self.assertIn("Active now (no action): 1 package(s)", out)

    def test_estimate_and_authoritative_same_sections_differ_in_framing(self):
        cls = [("arc-theme", _cls("relogin"))]
        est = format_next_steps(cls, estimate=True)
        auth = format_next_steps(cls)
        # Same actionable section...
        self.assertIn("LOG OUT AND BACK IN", est)
        self.assertIn("LOG OUT AND BACK IN", auth)
        # ...distinct framing.
        self.assertIn("ESTIMATE", est)
        self.assertNotIn("ESTIMATE", auth)

    def test_estimate_empty_when_nothing_actionable(self):
        self.assertEqual(
            format_next_steps([("vim", _cls("none"))], estimate=True), "")


if __name__ == "__main__":
    unittest.main()
