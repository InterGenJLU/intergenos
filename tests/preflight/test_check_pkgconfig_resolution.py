"""The pkg-config resolution gate: every installed .pc file resolves, or is
allowlisted with a reason; a stale allowlist entry and an empty scan fail.

Origin: ncurses r2 supplied libtinfo.so links without a tinfo.pc; readline.pc
then said `Requires.private: tinfo` and the first pkg-config consumer of
readline halted the R001.3 core-extra phase (2026-10-12). The gate asks the
real pkg-config, against a root alone, so the test needs pkg-config on PATH.
"""

import io
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GATE = REPO_ROOT / "scripts" / "check-pkgconfig-resolution.py"


def _pc(root: Path, subdir: str, name: str, requires: str = "",
        requires_private: str = ""):
    d = root / subdir
    d.mkdir(parents=True, exist_ok=True)
    body = textwrap.dedent(f"""\
        prefix=/usr
        libdir=${{prefix}}/lib
        includedir=${{prefix}}/include

        Name: {name}
        Description: test module {name}
        Version: 1.0
        Libs: -L${{libdir}} -l{name}
        Cflags: -I${{includedir}}
        """)
    if requires:
        body += f"Requires: {requires}\n"
    if requires_private:
        body += f"Requires.private: {requires_private}\n"
    (d / f"{name}.pc").write_text(body)


def _run(root: Path, allowlist_text: str):
    allow = root / "allowlist.txt"
    allow.write_text(allowlist_text)
    r = subprocess.run([sys.executable, "-I", str(GATE), "--root", str(root),
                        "--allowlist", str(allow)],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


@unittest.skipUnless(shutil.which("pkg-config"), "pkg-config not on PATH")
class TestPkgconfigResolution(unittest.TestCase):
    def _tree(self, root: Path):
        _pc(root, "usr/lib/pkgconfig", "ncursesw")
        _pc(root, "usr/lib/pkgconfig", "history")
        # the origin: readline's Requires.private names a module no .pc supplies
        _pc(root, "usr/lib/pkgconfig", "readline", requires_private="tinfo")
        _pc(root, "usr/share/pkgconfig", "xproto")

    def test_unresolvable_requirement_fails_naming_module_and_missing_package(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._tree(root)
            rc, txt = _run(root, "# empty\n")
            self.assertEqual(rc, 1, txt)
            self.assertIn("readline (usr/lib/pkgconfig/readline.pc)", txt)
            self.assertIn("tinfo", txt)
            self.assertIn("1 unresolved", txt)

    def test_supplying_the_name_turns_it_green(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._tree(root)
            # the ncurses-core r4 shape: tinfo.pc as a link onto ncursesw.pc
            (root / "usr/lib/pkgconfig/tinfo.pc").symlink_to("ncursesw.pc")
            rc, txt = _run(root, "# empty\n")
            self.assertEqual(rc, 0, txt)
            self.assertIn("PASS", txt)
            self.assertIn("0 unresolved", txt)

    def test_reasoned_allowlist_entry_passes_and_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._tree(root)
            rc, txt = _run(root, "readline\ta test-only reason for the fixture (2026-10-12)\n")
            self.assertEqual(rc, 0, txt)
            self.assertIn("1 allowlisted (reasoned)", txt)
            self.assertIn("allowlisted: readline", txt)

    def test_allowlist_entry_without_reason_refuses_the_gate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._tree(root)
            rc, txt = _run(root, "readline\n")
            self.assertEqual(rc, 2, txt)
            self.assertIn("without a reason", txt)

    def test_stale_allowlist_entry_fails(self):
        # history resolves on its own; an entry for it no longer describes reality
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._tree(root)
            (root / "usr/lib/pkgconfig/tinfo.pc").symlink_to("ncursesw.pc")
            rc, txt = _run(root, "history\tonce unresolvable, not any more\n")
            self.assertEqual(rc, 1, txt)
            self.assertIn("stale", txt)
            self.assertIn("history", txt)

    def test_share_dir_modules_resolve_against_lib_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _pc(root, "usr/lib/pkgconfig", "glib-2.0")
            _pc(root, "usr/share/pkgconfig", "shared-mime-info", requires="glib-2.0")
            rc, txt = _run(root, "# empty\n")
            self.assertEqual(rc, 0, txt)

    def test_empty_scan_fails_loud(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "usr/lib/pkgconfig").mkdir(parents=True)
            rc, txt = _run(root, "# empty\n")
            self.assertEqual(rc, 1, txt)
            self.assertIn("zero pkg-config files", txt)

    def test_missing_root_or_allowlist_is_fatal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            r = subprocess.run([sys.executable, "-I", str(GATE), "--root", str(root / "absent"),
                                "--allowlist", str(root / "absent.txt")],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_the_shipped_allowlist_parses_with_reasons(self):
        allow = REPO_ROOT / "config" / "pkgconfig-resolution-allowlist.txt"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _pc(root, "usr/lib/pkgconfig", "glib-2.0")
            r = subprocess.run([sys.executable, "-I", str(GATE), "--root", str(root),
                                "--allowlist", str(allow)],
                               capture_output=True, text=True)
            # the shipped entries are stale against this fixture root (their
            # modules are not installed here), which proves the stale check;
            # the parse itself must not refuse (every entry carries a reason)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("stale", r.stdout)
            self.assertNotIn("without a reason", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
