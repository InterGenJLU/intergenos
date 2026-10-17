#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Run the shipped install steps against isolated extracted payloads."""
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


class InstallStepCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.extracted = self.root / "extracted"
        self.extracted.mkdir()
        for name in ("opt", "usr/bin", "usr/share/applications", "usr/share/man",
                     "usr/share/icons/hicolor", "commands"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def put(self, name, text="fixture", executable=False):
        path = self.extracted / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".desktop"):
            text = text.replace("/usr/", str(self.root / "usr") + "/")
        path.write_text(text)
        if executable:
            path.chmod(0o755)
        return path

    def fail_command(self, name, pattern="*"):
        path = self.root / "commands" / name
        path.write_text(f'#!/bin/bash\ncase "$*" in {pattern}) exit 73;; esac\n'
                        f'exec /usr/bin/{name} "$@"\n')
        path.chmod(0o755)

    def run_step(self, package, start, end, prefix=""):
        source = (REPO / "packages/extra" / package / "build.sh").read_text()
        body = source.split("<< 'HELPEREOF'\n", 1)[1].split("\nHELPEREOF", 1)[0]
        block = body[body.index(start):body.index(end)]
        # Only this isolated post-extraction step runs, with all system paths
        # redirected. No download, acceptance prompt or system install runs.
        block = block.replace("/usr/", str(self.root / "usr") + "/")
        block = block.replace("/opt/", str(self.root / "opt") + "/")
        script = 'set -e\n'
        script += 'igos_helper_record_file() { printf "file %s\\n" "$1"; }\n'
        script += 'igos_helper_record_symlink() { printf "link %s %s\\n" "$1" "$2"; }\n'
        script += 'igos_helper_record_symlink_literal() { printf "link %s\\n" "$1"; }\n'
        script += 'igos_helper_record_dep() { :; }\n'
        script += 'igos_helper_record_post_install_action() { printf "action %s\\n" "$1"; }\n'
        script += prefix + block + '\nprintf "STEP COMPLETE\\n"\n'
        env = {**os.environ, "TMPDIR": str(self.extracted),
               "PATH": str(self.root / "commands") + ":/usr/bin:/bin"}
        result = subprocess.run(["/bin/bash", "-c", script], cwd=self.extracted,
                                env=env, text=True, capture_output=True, timeout=10)
        print(f"{self.id()} exit={result.returncode}\n{result.stdout}{result.stderr}")
        return result

    def assert_refused(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("STEP COMPLETE", result.stdout)


class VSCodeInstallSteps(InstallStepCase):
    def setUp(self):
        super().setUp()
        self.put("usr/share/code/code", "#!/bin/sh\nexit 0\n", True)
        self.put("usr/share/code/bin/code", "#!/bin/sh\nexit 0\n", True)
        self.put("usr/share/pixmaps/vscode.png")

    def run_vscode(self):
        return self.run_step("vscode", "rm -rf /opt/vscode", "igos_helper_record_dep glibc")

    def assert_launchers(self):
        applications = self.root / "usr/share/applications"
        main = (applications / "code.desktop").read_text()
        url = (applications / "code-url-handler.desktop").read_text()
        for text in (main, url):
            self.assertIn("Type=Application", text)
            self.assertIn("Icon=vscode", text)
        self.assertIn(f"Exec={self.root}/opt/vscode/code %F", main)
        self.assertIn(f"Exec={self.root}/opt/vscode/code --open-url %U", url)
        self.assertIn("MimeType=x-scheme-handler/vscode;", url)
        self.assertIn("NoDisplay=true", url)

    def test_absent_vendor_launchers_create_main_and_url_handler(self):
        result = self.run_vscode()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_launchers()
        self.assertIn("code-url-handler.desktop", result.stdout)
        self.assertIn("code.desktop", result.stdout)

    def test_vendor_launchers_are_found_and_rewritten_in_either_layout(self):
        for location in ("usr/share/applications", "usr/share/code/resources/launchers"):
            with self.subTest(location=location):
                main = self.put(location + "/code.desktop", "[Desktop Entry]\nName=Vendor Editor\n"
                                "Type=Application\nExec=/usr/share/code/code %F\nIcon=code\n")
                url = self.put(location + "/code-url-handler.desktop", "[Desktop Entry]\n"
                               "Type=Application\nExec=/usr/share/code/code --open-url %U\n"
                               "Icon=code\nNoDisplay=true\nMimeType=x-scheme-handler/vscode;\n")
                result = self.run_vscode()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_launchers()
                self.assertIn("Name=Vendor Editor", (self.root / "usr/share/applications/code.desktop").read_text())
                main.unlink()
                url.unlink()

    def test_unwritable_launcher_destination_refuses_plainly(self):
        self.fail_command("mkdir", "*/usr/share/applications*")
        result = self.run_vscode()
        self.assert_refused(result)
        self.assertIn("launcher", result.stderr.lower())

    def test_fallback_write_failure_refuses_plainly(self):
        self.fail_command("cat")
        result = self.run_vscode()
        self.assert_refused(result)
        self.assertIn("launcher", result.stderr.lower())

    def test_vendor_rewrite_failure_refuses_plainly(self):
        self.put("usr/share/applications/code.desktop", "[Desktop Entry]\nType=Application\n"
                 "Exec=/usr/share/code/code %F\nIcon=code\n")
        self.fail_command("sed")
        result = self.run_vscode()
        self.assert_refused(result)
        self.assertIn("launcher", result.stderr.lower())

    def test_bundled_icon_is_used_when_pixmap_is_absent(self):
        (self.extracted / "usr/share/pixmaps/vscode.png").unlink()
        self.put("usr/share/code/resources/app/resources/linux/code.png", "bundled icon")
        result = self.run_vscode()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "usr/share/pixmaps/vscode.png").read_text(), "bundled icon")

    def test_missing_icon_refuses(self):
        (self.extracted / "usr/share/pixmaps/vscode.png").unlink()
        self.assert_refused(self.run_vscode())

    def test_manual_copy_failure_refuses(self):
        self.put("usr/share/man/man1/code.1", "manual")
        self.fail_command("cp", "*usr/share/man*")
        self.assert_refused(self.run_vscode())

    def test_missing_manual_is_reported(self):
        result = self.run_vscode()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("manual", result.stdout.lower())

    def test_failed_footprint_enumeration_refuses(self):
        self.fail_command("find", "*/opt/vscode*")
        self.assert_refused(self.run_vscode())

    def test_missing_command_wrapper_refuses(self):
        (self.extracted / "usr/share/code/bin/code").unlink()
        self.assert_refused(self.run_vscode())


class ChromeInstallSteps(InstallStepCase):
    def setUp(self):
        super().setUp()
        self.put("opt/google/chrome/google-chrome", "#!/bin/sh\nexit 0\n", True)
        self.put("opt/google/chrome/product_logo_128.png", "product icon")
        self.put("usr/share/applications/google-chrome.desktop", "[Desktop Entry]\n"
                 "Type=Application\nExec=/usr/bin/google-chrome-stable %U\nIcon=google-chrome\n")
        binary = self.extracted / "usr/bin/google-chrome-stable"
        binary.parent.mkdir(parents=True)
        binary.symlink_to(self.root / "opt/google/chrome/google-chrome")
        cache = self.root / "commands/gtk-update-icon-cache"
        cache.write_text("#!/bin/sh\nexit 0\n")
        cache.chmod(0o755)

    def run_chrome(self):
        return self.run_step("chrome", "cp -a opt/google /opt/", "igos_helper_commit")

    def test_launcher_command_icon_and_manual_are_installed_and_recorded(self):
        self.put("usr/share/man/man1/google-chrome.1", "manual")
        result = self.run_chrome()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("usr/share/applications/google-chrome.desktop", "usr/bin/google-chrome-stable",
                     "usr/share/icons/hicolor/128x128/apps/google-chrome.png",
                     "usr/share/man/man1/google-chrome.1"):
            self.assertTrue((self.root / name).is_file(), name)
            self.assertIn(str(self.root / name), result.stdout)

    def test_missing_launcher_refuses(self):
        (self.extracted / "usr/share/applications/google-chrome.desktop").unlink()
        self.assert_refused(self.run_chrome())

    def test_launcher_copy_failure_refuses(self):
        self.fail_command("cp", "*usr/share/applications*")
        self.assert_refused(self.run_chrome())

    def test_missing_command_refuses(self):
        (self.extracted / "usr/bin/google-chrome-stable").unlink()
        self.assert_refused(self.run_chrome())

    def test_command_copy_failure_refuses(self):
        self.fail_command("cp", "*usr/bin*")
        self.assert_refused(self.run_chrome())

    def test_vendor_icon_copy_failure_refuses(self):
        self.put("usr/share/icons/hicolor/48x48/apps/google-chrome.png", "vendor icon")
        self.fail_command("cp", "*usr/share/icons*")
        self.assert_refused(self.run_chrome())

    def test_vendor_icon_suffices_without_product_logos(self):
        (self.extracted / "opt/google/chrome/product_logo_128.png").unlink()
        self.put("usr/share/icons/hicolor/48x48/apps/google-chrome.png", "vendor icon")
        result = self.run_chrome()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "usr/share/icons/hicolor/48x48/apps/google-chrome.png").read_text(), "vendor icon")

    def test_absent_icons_refuse(self):
        (self.extracted / "opt/google/chrome/product_logo_128.png").unlink()
        self.assert_refused(self.run_chrome())

    def test_manual_copy_failure_refuses(self):
        self.put("usr/share/man/man1/google-chrome.1", "manual")
        self.fail_command("cp", "*usr/share/man*")
        self.assert_refused(self.run_chrome())

    def test_absent_manual_is_reported(self):
        result = self.run_chrome()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("manual", result.stdout.lower())

    def test_footprint_enumeration_failure_refuses(self):
        self.fail_command("find", "*/opt/google/chrome*")
        self.assert_refused(self.run_chrome())

    def test_icon_cache_failure_refuses(self):
        self.fail_command("gtk-update-icon-cache")
        self.assert_refused(self.run_chrome())


if __name__ == "__main__":
    unittest.main()
