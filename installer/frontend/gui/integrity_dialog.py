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
    """Build a Gtk.Entry that refuses paste + live-validates against expected_phrase.

    on_match_change(matches: bool) is called whenever the entered text
    becomes equal-to or different-from expected_phrase. The caller wires
    this to enable/disable the submit button.
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

    # Live-validate on every keystroke.
    last_match = [False]

    def _on_changed(widget):
        matches = widget.get_text() == expected_phrase
        if matches != last_match[0]:
            last_match[0] = matches
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
