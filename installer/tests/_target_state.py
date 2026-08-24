# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Shared skip-gate classification for the post-install integration suites.

WHY THIS IS ONE MODULE AND NOT TWO COPIES. Both post-install suites decide
whether they are looking at a Forge-installed target from the same signal - the
MOK certificate bootloader.py writes at install time - and both used to decide
it with `Path.exists()` and report the same sentence when it came back False:

    MOK cert <path> not found (not a Forge-installed target)

`Path.exists()` swallows every OSError, EACCES included, and returns False. On
an installed system /var/lib/intergen/mok is mode 0700 root, so an unprivileged
run cannot traverse it, the check came back False, and nine cases skipped
saying the machine is not a Forge-installed target while the certificate sat
there at 1168 bytes (measured 2026-08-24 on R001.1).

The outcome of the skip was defensible - a run that cannot read the certificate
cannot do the work either. The REASON was false, and a false reason is worse
than a bare one: it tells the reader to stop looking. So the classification
returns three states rather than a boolean, and the callers say which one
happened.

Nothing here decides whether those cases SHOULD run with privilege on an
installed target. That is a coverage question for this lane and is untouched.
"""

import os
from pathlib import Path

MOK_PRESENT = "present"
MOK_ABSENT = "absent"
MOK_UNREADABLE = "unreadable"


def mok_cert_state(mok_cert) -> tuple[str, str]:
    """Classify the MOK certificate path into present / absent / unreadable.

    Returns (state, reason). `reason` is empty for MOK_PRESENT and is the
    skip text otherwise.

    os.stat is used directly, and its exceptions are separated, because that is
    the whole point: Path.exists() collapses "no such file" and "you may not
    look" into one False. FileNotFoundError means the path really is not there.
    PermissionError means a directory on the way refused the lookup, and this
    process therefore knows nothing about the certificate - not that it is
    missing, and not that it is present.
    """
    cert = Path(mok_cert)
    try:
        os.stat(cert)
    except FileNotFoundError:
        return MOK_ABSENT, (
            f"MOK cert {cert} is not there — this target was not installed by "
            "Forge, or the install did not reach the signing step"
        )
    except PermissionError as exc:
        return MOK_UNREADABLE, (
            f"MOK cert {cert} could not be examined: permission denied "
            f"({exc.strerror}). Its directory is root-only on an installed "
            "system, and this is an unprivileged run, so the certificate may "
            "well be there — this says nothing either way about whether the "
            "target is Forge-installed"
        )
    except OSError as exc:
        return MOK_UNREADABLE, (
            f"MOK cert {cert} could not be examined: {exc.strerror}. This is "
            "an unprivileged run and the check was refused, so nothing is "
            "claimed about the target"
        )
    return MOK_PRESENT, ""
