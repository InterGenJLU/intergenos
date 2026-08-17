# SPDX-License-Identifier: GPL-3.0-or-later
"""review_modal must self-heal a missing DISPLAY/WAYLAND_DISPLAY at review time.

Regression for the consent-UX boot bug (internvl-02 .218 post-install eval): the
systemd --user intergen daemon can start at boot BEFORE the graphical session
imports DISPLAY/WAYLAND_DISPLAY into the --user manager, so the daemon inherits
neither. _session_active() then sees no display and every held action degrades
to the buttonless notify-send fallback (a D-008 consent dead-end the user cannot
act on) even though a graphical session is present — the "needs review" popup
with nothing to click. _ensure_display_env() recovers the values from
`systemctl --user show-environment` (populated by review time, since the check
is lazy) so the zenity Allow/Deny modal can render.
"""
from __future__ import annotations

import os
import unittest
import unittest.mock as mock

from intergen import review_modal


SHOW_ENV = (
    "LANG=en_US.UTF-8\n"
    "DISPLAY=:0\n"
    "WAYLAND_DISPLAY=wayland-0\n"
    "XAUTHORITY=/run/user/1000/.mutter-Xwaylandauth.ABC\n"
)


class EnsureDisplayEnvTests(unittest.TestCase):
    def setUp(self):
        # patch.dict restores the original environment on stop, even after we
        # pop the display vars to simulate the boot daemon's stripped env.
        self._patch_env = mock.patch.dict(os.environ, {}, clear=False)
        self._patch_env.start()
        for var in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"):
            os.environ.pop(var, None)

    def tearDown(self):
        self._patch_env.stop()

    def test_recovers_display_from_user_manager_when_absent(self):
        result = mock.Mock(returncode=0, stdout=SHOW_ENV)
        with mock.patch.object(review_modal.shutil, "which",
                               return_value="/usr/bin/systemctl"), \
             mock.patch.object(review_modal.subprocess, "run",
                               return_value=result) as run:
            review_modal._ensure_display_env()
        run.assert_called_once()
        self.assertEqual(os.environ.get("DISPLAY"), ":0")
        self.assertEqual(os.environ.get("WAYLAND_DISPLAY"), "wayland-0")
        self.assertEqual(os.environ.get("XAUTHORITY"),
                         "/run/user/1000/.mutter-Xwaylandauth.ABC")

    def test_noop_when_display_already_present(self):
        os.environ["DISPLAY"] = ":1"
        with mock.patch.object(review_modal.shutil, "which",
                               return_value="/usr/bin/systemctl"), \
             mock.patch.object(review_modal.subprocess, "run") as run:
            review_modal._ensure_display_env()
        run.assert_not_called()  # short-circuits before any subprocess
        self.assertEqual(os.environ.get("DISPLAY"), ":1")

    def test_session_active_true_after_selfheal(self):
        # DISPLAY recovered by the self-heal + gdbus absent (screensaver stage
        # assumes active) -> _session_active flips False -> True, so the held
        # action gets the zenity modal instead of the buttonless fallback.
        result = mock.Mock(returncode=0, stdout=SHOW_ENV)

        def which(name):
            return "/usr/bin/systemctl" if name == "systemctl" else None

        with mock.patch.object(review_modal.shutil, "which", side_effect=which), \
             mock.patch.object(review_modal.subprocess, "run",
                               return_value=result):
            self.assertTrue(review_modal._session_active())

    def test_best_effort_when_systemctl_missing(self):
        with mock.patch.object(review_modal.shutil, "which",
                               return_value=None), \
             mock.patch.object(review_modal.subprocess, "run") as run:
            review_modal._ensure_display_env()
        run.assert_not_called()
        self.assertIsNone(os.environ.get("DISPLAY"))


if __name__ == "__main__":
    unittest.main()
