# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Done screen — final page of the 9-screen flow.

Adw.StatusPage with success/failure messaging. Back hidden. The action
button's label + behaviour depends on install outcome:
  * success → "Reboot now" — invokes `systemctl reboot` via subprocess.
  * failure → "Quit" — closes the window; user retries via live media.

The Progress screen sets `state.install_failed` + `state.install_error_message`
on failure so we can render an error path here too.
"""

import subprocess

from gi.repository import Adw, Gtk

from installer.backend.disks import live_media_kind
from installer.backend.secureboot import allows_mok_enrollment

from ._base import _ForgePage, _toast

# Venue-aware media wording (live_media_kind() keys). A VM's virtual
# CD/DVD drive presents as an optical device, so "cdrom" carries the
# detach-the-ISO instruction; unknown venues fall back to the generic
# phrasing via _MEDIA_CLAUSE_DEFAULT.
_MEDIA_CLAUSE = {
    "usb": "remove the USB install stick",
    "cdrom": (
        "eject the install disc (in a virtual machine, detach the ISO "
        "from the virtual CD/DVD drive)"
    ),
}
_MEDIA_CLAUSE_DEFAULT = "remove the install media"

# The Secure-Boot re-enable reminder. Definite wording ("the password you
# set"), because it is only ever appended when the user actually set one —
# the old unconditional text had to hedge "If you set…" at every user,
# including machines where MokManager can never fire.
_MOK_REMINDER = (
    " Re-enable Secure Boot in your UEFI firmware setup on this reboot — "
    "that is what triggers MokManager, where you enter the MOK enrollment "
    "password you set to register your machine's signing key. See "
    "docs/users/secure-boot-and-mok.md for the full first-boot walkthrough."
)




def _success_description(mok_reminder=False, media_kind=None):
    """Compose the success-path guidance the user actually sees.

    ONE composer consumed by BOTH assignments (_build_body's default and
    on_load's success branch) so the two can never drift apart: the
    guidance correction once landed only in the _build_body default,
    which on_load unconditionally overwrites before the page renders —
    the user kept seeing the retired "you'll be prompted on first boot"
    promise. The reboot-then-remove-media order is the safe one (the
    TUI's order — ejecting first squashfs-panics the live session).

    mok_reminder — append the Secure-Boot re-enable guidance. The caller
    passes True only when BOTH display conditions hold: the machine can
    take a MOK enrollment at all (allows_mok_enrollment() is True) AND
    the user opted into enrollment by setting a passphrase
    (state.mok_enrollment_chosen). On every other machine — BIOS,
    firmware without Secure Boot machinery, enrollment declined — the
    reminder would instruct an impossible or pointless firmware trip,
    so it is omitted entirely.

    media_kind — live_media_kind()'s verdict, choosing the venue wording.

    """
    media = _MEDIA_CLAUSE.get(media_kind, _MEDIA_CLAUSE_DEFAULT)
    text = f"Click Reboot now, and {media} as the system restarts."
    if mok_reminder:
        text += _MOK_REMINDER
    return text


class DonePage(_ForgePage):
    tag = "done"
    title = "Done"
    # Post-install success / failure screen — not a user-decision
    # page; suppressed from the step indicator for the same reason
    # ProgressPage is.
    in_step_indicator = False

    def _build_body(self) -> Gtk.Widget:
        self._status = Adw.StatusPage()
        # Defaults — overwritten in on_load based on install outcome.
        # The default deliberately renders the composer's conservative
        # form (no MOK reminder, generic media wording): on_load always
        # runs before display and supplies the probed values.
        self._status.set_icon_name("object-select-symbolic")
        self._status.set_title("Install complete")
        self._status.set_description(_success_description())
        return self._status

    def __init__(self, window):
        super().__init__(window)
        self.back_button.set_visible(False)
        self.next_button.set_label("Reboot now")
        # Default action — overridden in on_load for the failure path.
        self._on_success_path = True

    def on_load(self, state):
        if getattr(state, "install_cancelled", False):
            # Cancelled path: distinct from failure (user-initiated, not
            # a crash) and distinct from success (target may be in an
            # indeterminate state depending on which phase the cancel
            # landed in).
            self._on_success_path = False
            self._status.set_icon_name("process-stop-symbolic")
            self._status.set_title("Install cancelled")
            err = state.install_error_message or "(no phase captured)"
            self._status.set_description(
                f"The install was cancelled before it completed:\n\n{err}\n\n"
                "If the cancel landed after the partition phase the target "
                "disk's partition table is modified. Reboot to the live "
                "media and run the installer again from scratch, or open "
                "a terminal in the live session to inspect the target."
            )
            self.next_button.set_label("Quit")
        elif state.install_failed:
            self._on_success_path = False
            self._status.set_icon_name("dialog-error-symbolic")
            self._status.set_title("Install failed")
            err = state.install_error_message or "(no detail captured)"
            self._status.set_description(
                f"The install did not complete cleanly:\n\n{err}\n\n"
                "Reboot to the live media to retry, or open a terminal in the "
                "live session to investigate."
            )
            self.next_button.set_label("Quit")
        else:
            self._on_success_path = True
            self._status.set_icon_name("object-select-symbolic")
            self._status.set_title("Install complete")
            # The two operator-specified display conditions for the
            # Secure-Boot re-enable reminder (both must hold): the machine
            # allows key enrollment (capability probed NOW, on the machine
            # being installed) and the user chose enrollment by setting a
            # passphrase. mok_enrollment_chosen is the surviving record of
            # that choice — clear_sensitive_data() scrubbed mok_password
            # before this page loaded. Both probes are no-raise by
            # contract; unknown capability (None) withholds the reminder
            # rather than promising MokManager on faith.
            mok_reminder = (
                getattr(state, "mok_enrollment_chosen", False)
                and allows_mok_enrollment() is True
            )
            self._status.set_description(_success_description(
                mok_reminder=mok_reminder,
                media_kind=live_media_kind(),
            ))
            self.next_button.set_label("Reboot now")

    def _on_next_clicked(self, _button):  # noqa: override
        if not self._on_success_path:
            self._window.close()
            return

        # Success path: trigger system reboot. systemctl reboot returns
        # immediately after queueing the reboot job with systemd; the
        # actual shutdown sequence runs out from under us. We toast first
        # so the user sees acknowledgment if there's a brief lag before
        # the session tears down.
        _toast(self._window, "Rebooting…")
        try:
            subprocess.Popen(
                ["systemctl", "reboot"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except (OSError, FileNotFoundError) as e:
            _toast(self._window,
                   f"Could not invoke reboot: {e}. "
                   "Use the system menu or run `systemctl reboot` from a terminal.")
