# SPDX-License-Identifier: GPL-3.0-or-later
"""The first-reboot notice explains the visible screen and recovery paths."""

import pytest

from installer.backend.mok_guidance import MOK_WINDOW_ADVISORY
from installer.tests.test_gui_done_rendered_string import (
    _rendered_success_description,
)


@pytest.mark.parametrize("surface", ["backend", "done"])
@pytest.mark.parametrize("elements", [
    ("blue", "MokManager"),
    ("black", "Esc"),
    ("10 seconds", "Enroll key from disk", "mok.der", "Continue boot"),
])
def test_notice_names_the_screen_and_recovery(surface, elements):
    text = (MOK_WINDOW_ADVISORY if surface == "backend" else
            _rendered_success_description(mok_chosen=True, capable=True))
    for element in elements:
        assert element in text
