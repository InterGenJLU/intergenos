"""GUI integrity-mismatch dialog — Step 6b of install-time integrity verification.

Provides warning_callback + ack_callback compatible with
`installer.backend.integrity.verify_archives` for use from the GUI's
worker thread (per ProgressPage threading model).

The callbacks are invoked from the worker thread (because
verify_archives runs synchronously inside run_install which the worker
hosts). GTK is not safe to update from worker threads, so we marshal
the dialog rendering to the main loop via GLib.idle_add and block the
worker on a threading.Event until the user responds.

Acknowledgment dialog hardening (per design doc §6.5.1):

  * paste-clipboard signal suppressed (Gtk.Entry never accepts paste)
  * right-click paste menu suppressed (extra-menu set to None)
  * live-validation: submit button only enabled when entered text
    equals expected_override_phrase(package_name) exactly
  * input-hints set to NO_SPELLCHECK | NO_EMOJI to keep IME/autocomplete
    out of the path
  * input-purpose left at FREE_FORM (NOT password) so user can see
    what they're typing — they MUST be aware of which package they
    are acknowledging

Audit-log entry-method tracking (per design doc §6.5.1): the GUI ack
callback returns a 2-tuple (granted, "gui_typed") so the orchestrator
can record entry method in the hash-chained audit log. The TUI ack
callback returns just bool; integrity.verify_archives accepts either.
For now we keep the simpler bool contract — entry-method recording is
a follow-on if forensics need it later.
"""

import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from installer.backend.integrity import (
    INTEGRITY_WARNING_TEMPLATE,
    expected_override_phrase,
)


def _build_warning_text(package_name, expected_sha256, actual_sha256):
    """Fill the hard-coded warning template for display."""
    return INTEGRITY_WARNING_TEMPLATE.format(
        package=package_name,
        expected_sha256=expected_sha256,
        actual_sha256=actual_sha256,
        override_phrase=expected_override_phrase(package_name),
    )


def _make_paste_disabled_entry(expected_phrase, on_match_change):
    """Build a Gtk.Entry that refuses paste + DnD + live-validates against expected_phrase.

    on_match_change(matches: bool) is called whenever the entered text
    becomes equal-to or different-from expected_phrase. The caller wires
    this to enable/disable the submit button.

    Three input-suppression layers (per design doc §6.5.1, with F9
    DnD-closure added 2026-05-07 fix-wave):

      1. paste-clipboard signal suppressed — blocks Ctrl-V + middle-click.
      2. extra-menu=None — removes right-click context menu (no GUI Paste).
      3. Multi-character-insertion guard — rejects text growth >1 char per
         change event, which catches drag-and-drop, IME paste, and any
         other path that bypasses the paste-clipboard signal. Typing
         always inserts 1 char per keystroke; multi-char growth is by
         definition not a typed phrase.

    The third layer is the F9 fix: GTK4 dropped the GTK3
    `drag-data-received` signal in favour of `Gtk.DropTarget` controllers,
    and `Gtk.Entry` ships internal drop targets that accept text drops.
    Rather than enumerating + replacing internal controllers (fragile
    across GTK4 minor versions), we monitor text-growth at the buffer
    level — drop-text inserts the dragged string in one event, which
    the guard reverts.
    """
    entry = Gtk.Entry()
    entry.set_input_hints(Gtk.InputHints.NO_SPELLCHECK | Gtk.InputHints.NO_EMOJI)
    entry.set_input_purpose(Gtk.InputPurpose.FREE_FORM)
    entry.set_placeholder_text("Type the override phrase shown above…")

    # Suppress the paste-clipboard signal — any paste attempt is a no-op.
    def _block_paste(widget):
        widget.stop_emission_by_name("paste-clipboard")
    entry.connect("paste-clipboard", _block_paste)

    # GTK4 drops the populate-popup signal in favour of `extra-menu`.
    # Setting extra-menu to None removes the right-click menu entirely
    # (no Cut/Copy/Paste/etc. context menu).
    entry.set_extra_menu(None)

    # Combined typing-only validator + live-match notifier.
    # `last_text` tracks the previously-accepted text so we can revert any
    # multi-char insertion (paste/DnD/IME). `last_match` tracks match-state
    # transitions so on_match_change is only called when the boolean flips.
    # `in_revert` guards against recursive `changed` emission when set_text()
    # is called from within the handler.
    state = {"last_text": "", "last_match": False, "in_revert": False}

    def _on_changed(widget):
        if state["in_revert"]:
            return
        new_text = widget.get_text()
        # Reject growth >1 char per event — catches paste, DnD, and IME
        # paste-paths that bypass the paste-clipboard signal. Allow
        # shrinking (delete/backspace) and 1-char growth (typing).
        if len(new_text) > len(state["last_text"]) + 1:
            state["in_revert"] = True
            try:
                widget.set_text(state["last_text"])
            finally:
                state["in_revert"] = False
            return
        state["last_text"] = new_text
        # Notify caller only on match-state transitions.
        matches = new_text == expected_phrase
        if matches != state["last_match"]:
            state["last_match"] = matches
            on_match_change(matches)
    entry.connect("changed", _on_changed)

    return entry


def _show_warning_dialog_blocking(parent_window, package_name, expected_sha256, actual_sha256):
    """Show the warning to the user; block the worker thread until they dismiss it.

    Called from the worker thread. Marshals dialog rendering to the GTK main
    loop and blocks on threading.Event until the user clicks "I understand"
    (or closes the dialog — we treat that as "understood, continue to ack
    prompt"; the actual override decision lives in the next dialog).
    """
    done = threading.Event()

    def _present():
        dialog = Adw.MessageDialog(
            transient_for=parent_window,
            modal=True,
            heading="⚠ Integrity Mismatch Detected",
            body=_build_warning_text(package_name, expected_sha256, actual_sha256),
        )
        dialog.add_response("understood", "I understand — show me the override option")
        dialog.set_default_response("understood")
        dialog.set_close_response("understood")

        def _on_response(d, response):
            done.set()
            d.close()

        dialog.connect("response", _on_response)
        dialog.present()
        return False  # one-shot

    GLib.idle_add(_present)
    done.wait()


def _show_ack_dialog_blocking(parent_window, package_name):
    """Show the typed-phrase override dialog; block worker until user submits or cancels.

    Returns True iff the user typed expected_override_phrase(package_name)
    exactly and pressed Submit. False on cancel, close, or any other input.

    Called from the worker thread. Dialog runs on main loop; threading.Event
    coordinates the round-trip.
    """
    expected = expected_override_phrase(package_name)
    result = {"granted": False}
    done = threading.Event()

    def _present():
        dialog = Adw.MessageDialog(
            transient_for=parent_window,
            modal=True,
            heading=f"Override integrity check for {package_name}?",
            body=(
                "If you intentionally created this mismatch (testing a patched "
                "package, etc.), type the override phrase below to proceed.\n\n"
                "Otherwise, press Cancel — your install will be aborted with "
                "no changes to the target disk.\n\n"
                "Paste is disabled — you must type the phrase manually."
            ),
        )
        dialog.add_response("cancel", "Cancel install")
        dialog.add_response("submit", "Override and proceed")
        dialog.set_response_appearance("submit", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        # Submit is disabled until live-validation matches.
        dialog.set_response_enabled("submit", False)

        def _on_match(matches):
            dialog.set_response_enabled("submit", matches)

        entry = _make_paste_disabled_entry(expected, _on_match)
        dialog.set_extra_child(entry)

        def _on_response(d, response):
            if response == "submit" and entry.get_text() == expected:
                result["granted"] = True
            done.set()
            d.close()

        dialog.connect("response", _on_response)
        dialog.present()
        # Focus the entry so the user can start typing immediately.
        entry.grab_focus()
        return False

    GLib.idle_add(_present)
    done.wait()
    return result["granted"]


def make_gui_integrity_callbacks(parent_window):
    """Build (warning_callback, ack_callback) bound to the given parent window.

    Use these in a VerifyConfig passed to run_install from the GUI's worker
    thread. Both callbacks block the calling thread until the user responds
    on the GTK main loop.

    Returns a tuple (warning_callback, ack_callback) suitable for
    integrity.VerifyConfig — the callback signatures match the backend's
    contract:

        warning_callback(package_name, expected_sha256, actual_sha256) -> None
        ack_callback(package_name) -> bool
    """
    def warning_callback(package_name, expected_sha256, actual_sha256):
        _show_warning_dialog_blocking(
            parent_window, package_name, expected_sha256, actual_sha256
        )

    def ack_callback(package_name):
        return _show_ack_dialog_blocking(parent_window, package_name)

    return warning_callback, ack_callback
