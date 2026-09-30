# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""THE SEND IS NOT WHAT THE FALLBACK CONSENT DIALOG DOES BY DEFAULT.

THE DEFECT THIS CLOSES. Before any conversation content leaves the machine for
a frontier model, the consent step shows the person the exact outbound content
and waits for an explicit Send; the hop is not egress-scanned, so that dialog is
the only thing between a secret already in the conversation and the network. The
fallback dialog, the one used when the branded dialog cannot render, is built
from the dialog program's text-info mode, and that dialog's default response is
its OK button — labelled Send here. A bare Return, or anything else that
activates a window's default response, therefore sent the whole payload from a
dialog whose text nobody had scrolled through.

WHY THE FLAG THAT WAS THERE DID NOT DO IT. The command line carried
--default-cancel, and the surface's own comment said the dialog defaulted to
Cancel because of it. In the pinned dialog program (4.2.2) that option is bound
to the question-dialog structure only (src/option.c, the option's target is the
question dialog's default-cancel field, carried at build time into the message
dialog's data and nowhere else), and the text-info dialog is built from a
definition that sets its default response to ok (src/zenity.ui). The flag was
accepted, ignored, and read as a safeguard.

WHAT THE PINNED PROGRAM DOES SUPPORT FOR THIS DIALOG TYPE. --checkbox, a
text-info option: when it is present the program makes the box visible and
DISABLES the ok response as the dialog is built, then enables it from the box's
toggled state alone (src/text.c, the checkbox branch and its toggle callback).
So the dialog opens with Send disabled: the default response cannot send, and a
send needs the person to say first that they have read the content.

WHAT THIS CASE ASSERTS, AND WHY AGAINST THE ARGV. The argument list is the whole
of the claim — it is what the program is actually given — so the case reads the
command line the surface builds for a real run and asserts the argument is
present with the label the person reads, that the ignored flag is gone, and that
the two properties this dialog already had (the full payload shown scrollably,
the OK button labelled Send) are untouched. It reaches no dialog program, no
display and no network: the session probe, the branded dialog and the process
call are all replaced.
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen import consent_modal


class ConsentFallbackDialogDefaultTests(unittest.TestCase):
    def test_the_argv_opens_the_dialog_with_send_disabled(self):
        with mock.patch.object(consent_modal, "_session_active", return_value=True), \
             mock.patch.object(consent_modal.consent_dialog, "run_consent_dialog",
                               return_value=None), \
             mock.patch.object(consent_modal.shutil, "which",
                               return_value="/usr/bin/zenity"), \
             mock.patch.object(consent_modal.subprocess, "run",
                               return_value=mock.Mock(returncode=1, stdout="",
                                                      stderr="")) as run:
            self.assertFalse(consent_modal.prompt_send_consent("hi", "openai"))
        argv = run.call_args[0][0]

        # The box that has to be ticked before the OK response is enabled, with
        # the label the person reads on it.
        self.assertIn(f"--checkbox={consent_modal.REVIEW_ACKNOWLEDGED}", argv)
        self.assertIn("read", consent_modal.REVIEW_ACKNOWLEDGED)

        # The flag this dialog type ignores is not passed at all: it stated a
        # default the program never applied here.
        self.assertNotIn("--default-cancel", argv)

        # Unchanged by this: the full payload is shown in a scrollable view and
        # the response that sends is the one labelled Send.
        self.assertIn("--text-info", argv)
        self.assertIn("--ok-label=Send", argv)


if __name__ == "__main__":
    unittest.main()
