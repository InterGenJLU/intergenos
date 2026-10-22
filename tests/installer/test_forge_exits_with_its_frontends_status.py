# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""forge exits with the status its frontend returned.

installer/__main__.py's dispatch ran the TUI's run_installer, which returns 0
after an install or a completed dry run, 1 after a failure and 130 when the
person cancels, and returned 0 whatever it returned; the GUI's branch did the
same with its run_installer, which itself returned 0 whatever its application
returned. So a failed, cancelled or crashed TUI install exited 0, and
forge-tui.service (a oneshot unit) recorded success for it, and a GUI that
never ran exited 0. The frontends are stood in at their run_installer and the
GUI's application at its run; dispatch and the GUI's run_installer are real.
"""

import unittest
from unittest import mock

import installer.__main__ as forge_main
from installer.frontend import tui


class ForgeExitsWithItsFrontendsStatus(unittest.TestCase):

    def test_the_tuis_status_reaches_the_process(self):
        for status in (0, 1, 130):
            with self.subTest(status=status), \
                    mock.patch.object(tui, "run_installer", lambda *a, **kw: status):
                self.assertEqual(forge_main.dispatch("tui", "/archives", None, False), status)

    def test_the_guis_status_reaches_the_process(self):
        from installer.frontend.gui import window

        class _App:
            def __init__(self, *a):
                pass

            def run(self, argv):
                return 1

        with mock.patch.object(window, "ForgeApplication", _App):
            self.assertEqual(window.run_installer(None, None, False), 1)
        with mock.patch.object(window, "run_installer", lambda *a, **kw: 1):
            self.assertEqual(forge_main.dispatch("gui", None, None, False), 1)


if __name__ == "__main__":
    unittest.main()
