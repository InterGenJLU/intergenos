# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The one wording of the MOK enrolment window and its recovery path.

Every surface that talks about first-boot enrolment — the two installer
frontends' password prompts, the done screens, the Welcomer's first-login
card and the documents — states the same three facts (R001.3 row 37 (c),
decided 2026-09-05 after an enrolment was lost to an unseen prompt):

1. MokManager waits about 10 seconds for a key press; the window can pass
   unseen (a monitor still waking up), and then the queued request is gone.
2. Missing it is recoverable without another machine: shim's failure menu
   offers "Enroll key from disk"; the certificate is staged on the boot
   partition at EFI/InterGenOS/mok.der (no password, no timeout).
3. Which Microsoft signing authorities the firmware trusts, when readable.
"""

MOK_WINDOW_ADVISORY = (
    "MokManager waits about 10 seconds for a key press and the prompt can pass "
    "unseen while a monitor wakes up; if you miss it, the boot stops at a "
    "'Verification failed' menu — choose 'Enroll key from disk', open the boot "
    "partition and pick EFI > InterGenOS > mok.der, then Continue and reboot. "
    "No password is needed on that path."
)


def firmware_ca_line():
    """The plain-language sentence about the firmware's signature database,
    or an empty string when it cannot be read (never a guess)."""
    from .secureboot import microsoft_ca_advisory, microsoft_uefi_ca_state
    return microsoft_ca_advisory(microsoft_uefi_ca_state())
