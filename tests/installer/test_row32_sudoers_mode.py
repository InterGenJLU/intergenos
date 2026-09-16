"""The privilege-configuration files ship and stay owner-and-group-read-only (R001.3 row 32).

The syntax checker refuses to validate a sudo configuration that is more
permissive than 0440, and it checks every file it reads: the main file and each
drop-in. Measured on an installed R001.2-03 machine before this change,
`visudo -c` exits 1 with `bad permissions, should be mode 0440` for BOTH
`/etc/sudoers` and `/etc/sudoers.d/00-sudo`.

The two files get there by different routes and both are covered here:
the drop-in's mode is written by the package recipe, and the main file's mode is
replaced by the installer, which stages a rewritten copy and renames it over the
shipped one — a rename carries the staged file's mode, so the install undoes
whatever the package shipped.
"""

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from installer.backend import users  # noqa: E402

SUDO_BUILD_SH = REPO_ROOT / "packages" / "core" / "sudo" / "build.sh"

SUDOERS_WITH_WHEEL_COMMENTED = """\
Defaults secure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"
root ALL=(ALL:ALL) ALL
# %wheel ALL=(ALL:ALL) ALL
"""


def _ok(*args, **kwargs):
    """A syntax check that passes."""
    class R:
        returncode = 0
        stdout = ""
        stderr = ""
    return R()


def _refuses(*args, **kwargs):
    """A syntax check that refuses, the way a malformed file would."""
    class R:
        returncode = 1
        stdout = ""
        stderr = ">>> /etc/sudoers.new: syntax error near line 3 <<<"
    return R()


class TestTheRecipeShipsTheDropInRestrictive(unittest.TestCase):
    def test_the_drop_in_is_chmod_440(self):
        text = SUDO_BUILD_SH.read_text()
        self.assertIn('chmod 440 "${DESTDIR}/etc/sudoers.d/00-sudo"', text,
                      "the recipe must ship the drop-in 0440 — the syntax "
                      "checker refuses anything more permissive")
        self.assertNotIn('chmod 644 "${DESTDIR}/etc/sudoers.d/00-sudo"', text)

    def test_the_main_file_is_shipped_restrictive_too(self):
        text = SUDO_BUILD_SH.read_text()
        self.assertIn('chmod 440 "${DESTDIR}/etc/sudoers"', text,
                      "upstream's own install mode is not relied on: the "
                      "recipe states it, so the shipped mode is readable in "
                      "the recipe rather than inferred from the build")


class TestTheInstallerKeepsTheMode(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "etc").mkdir()
        self.sudoers = self.tmp / "etc" / "sudoers"
        self.sudoers.write_text(SUDOERS_WITH_WHEEL_COMMENTED)
        os.chmod(self.sudoers, 0o440)

    def _mode(self):
        return stat.S_IMODE(self.sudoers.stat().st_mode)

    def test_the_rewrite_enables_wheel_and_keeps_0440(self):
        changed = users.enable_wheel_sudo(self.tmp, runner=_ok)
        self.assertTrue(changed)
        self.assertIn("%wheel ALL=(ALL:ALL) ALL", self.sudoers.read_text())
        self.assertNotIn("# %wheel", self.sudoers.read_text())
        self.assertEqual(self._mode(), 0o440,
                         "the install must not leave the file more permissive "
                         "than the syntax checker accepts")

    def test_no_staging_file_is_left_behind(self):
        users.enable_wheel_sudo(self.tmp, runner=_ok)
        self.assertFalse((self.tmp / "etc" / "sudoers.new").exists())

    def test_a_refused_syntax_check_leaves_the_file_and_its_mode_alone(self):
        before = self.sudoers.read_text()
        changed = users.enable_wheel_sudo(self.tmp, runner=_refuses)
        self.assertFalse(changed)
        self.assertEqual(self.sudoers.read_text(), before)
        self.assertEqual(self._mode(), 0o440)
        self.assertFalse((self.tmp / "etc" / "sudoers.new").exists())

    def test_an_already_enabled_file_is_left_alone_and_keeps_its_mode(self):
        os.chmod(self.sudoers, 0o600)   # writable by this test, then restored
        self.sudoers.write_text(
            SUDOERS_WITH_WHEEL_COMMENTED.replace("# %wheel", "%wheel"))
        os.chmod(self.sudoers, 0o440)
        changed = users.enable_wheel_sudo(self.tmp, runner=_ok)
        self.assertFalse(changed)
        self.assertEqual(self._mode(), 0o440)

    def test_a_file_that_was_shipped_permissive_is_corrected_by_the_install(self):
        """A machine whose package shipped 0644 is corrected, not preserved.

        Keeping the file's existing mode would carry a permissive mode forward
        on every upgrade install. The install writes the mode the checker
        requires, whatever it found.
        """
        os.chmod(self.sudoers, 0o644)
        users.enable_wheel_sudo(self.tmp, runner=_ok)
        self.assertEqual(self._mode(), 0o440)

    def test_no_sudoers_file_is_not_an_error(self):
        self.sudoers.unlink()
        self.assertFalse(users.enable_wheel_sudo(self.tmp, runner=_ok))


class TestTheDropInModeIsAlsoCorrectedOnInstall(unittest.TestCase):
    """A machine installed before this change carries a 0644 drop-in.

    The package's own mode fixes new installs; an install onto a disk that
    already holds a permissive drop-in fixes it too, because the checker reads
    every file in the directory and one permissive file refuses the whole run.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "etc" / "sudoers.d").mkdir(parents=True)
        self.sudoers = self.tmp / "etc" / "sudoers"
        self.sudoers.write_text(SUDOERS_WITH_WHEEL_COMMENTED)
        os.chmod(self.sudoers, 0o440)
        self.dropin = self.tmp / "etc" / "sudoers.d" / "00-sudo"
        self.dropin.write_text("%wheel ALL=(ALL) ALL\n")
        os.chmod(self.dropin, 0o644)

    def test_a_permissive_drop_in_is_corrected(self):
        users.enable_wheel_sudo(self.tmp, runner=_ok)
        self.assertEqual(stat.S_IMODE(self.dropin.stat().st_mode), 0o440)

    def test_a_drop_in_that_is_already_restrictive_is_untouched(self):
        os.chmod(self.dropin, 0o440)
        users.enable_wheel_sudo(self.tmp, runner=_ok)
        self.assertEqual(stat.S_IMODE(self.dropin.stat().st_mode), 0o440)


if __name__ == "__main__":
    unittest.main()
