"""Progress screen — sixth page of the 7-screen flow.

This is where the GUI hands off to the Phase 4 backend orchestrator
(`installer.backend.install.run_install`) and renders progress events as
the 12-phase pipeline executes.

Threading model
---------------

`run_install` is a blocking function — it spawns subprocesses for partition
/ chroot / package install / bootloader work. We MUST NOT call it from the
GTK main loop or the UI freezes for the duration of the install (~minutes).

Solution: run it on a worker thread. The orchestrator emits progress
events via `progress_callback(phase, current, total, message)`. We can't
update GTK widgets directly from the worker thread (GTK is not thread-safe
across all platforms), so we marshal each event back to the main loop via
`GLib.idle_add`.

Event shape
-----------

The orchestrator emits two kinds of events through the same callback:

  * Phase-boundary events — `total == len(PHASE_ORDER)` (12). `current`
    counts up by phase-index. `phase` is a stable PHASE_* string.
  * Sub-progress events — for PHASE_PACKAGES + PHASE_HOOKS, the
    orchestrator's wrapper passes the per-package `(current, total_pkgs,
    name)` tuple through. `total != 12`, so we can distinguish by
    comparing `total` against `len(PHASE_ORDER)`.

This screen renders the phase-boundary events as fraction increments on
the progress bar and updates a status label. Sub-progress events update
just the status label (no fraction bump — stays within the current phase).
"""

import threading

from gi.repository import GLib, Gtk

from ._base import _ForgePage


class ProgressPage(_ForgePage):
    tag = "progress"
    title = "Installing"

    def _build_body(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        heading = Gtk.Label(label="Installing InterGenOS")
        heading.add_css_class("title-1")
        heading.set_halign(Gtk.Align.START)
        box.append(heading)

        self._progress_bar = Gtk.ProgressBar()
        self._progress_bar.set_show_text(True)
        self._progress_bar.set_text("Starting…")
        box.append(self._progress_bar)

        self._status_label = Gtk.Label(label="Preparing install...")
        self._status_label.set_wrap(True)
        self._status_label.set_xalign(0)
        self._status_label.set_selectable(True)
        box.append(self._status_label)

        return box

    def __init__(self, window):
        super().__init__(window)
        # During install, no Back; Next is "Continue" → Done. Disabled until
        # the install thread completes (success or failure both unlock it).
        self.back_button.set_visible(False)
        self.next_button.set_label("Continue")
        self.next_button.set_sensitive(False)

        self._worker_thread = None
        self._phases_total = None  # cached on first event so test rigs can mock

    def on_load(self, state):
        state.install_started = True

        try:
            from installer.backend.install import PHASE_ORDER
            self._phases_total = len(PHASE_ORDER)
        except ImportError as e:
            self._on_install_failed(state, f"Cannot import orchestrator: {e}")
            return

        from ..state import DEFAULT_YAML_PATH
        try:
            yaml_path = state.write_install_yaml(DEFAULT_YAML_PATH)
        except (OSError, PermissionError) as e:
            self._on_install_failed(state, f"Cannot write install yaml: {e}")
            return

        kwargs = state.to_run_install_kwargs(
            yaml_path,
            self._window.archive_dir,
            packages_dir=self._window.packages_dir,
            progress_callback=self._on_progress_from_worker,
            dry_run=getattr(self._window, "dry_run", False),
        )

        # Inject install-time integrity verification if signed manifest is on
        # install media. Mirrors tui._build_verify_config_if_present() — same
        # paths, GUI-flavoured dialog callbacks instead of stdin/stdout. Skip
        # in dry_run because dev/test environments don't ship a signed manifest.
        if not kwargs.get("dry_run"):
            kwargs["verify_config"] = self._build_verify_config_if_present()

        self._worker_thread = threading.Thread(
            target=self._run_install_worker,
            args=(state, kwargs),
            daemon=True,
        )
        self._worker_thread.start()

    # ------------------------------------------------------------------
    # Integrity verify_config builder.
    # ------------------------------------------------------------------

    # Install-media integrity manifest paths — mirror tui.py's constants.
    # Production install media has the manifest + release-key public component
    # placed by the build's `manifest` phase + signing ceremony. Dev/test
    # environments without those files skip integrity verification (the GUI
    # ProgressPage shows the orchestrator's "verify phase skipped" event in
    # its status label).
    _INSTALL_MEDIA_MANIFEST = "/install/intergenos-archive-manifest.txt"
    _INSTALL_MEDIA_PUBKEY = "/install/intergenos-release-key.asc"
    _INTEGRITY_AUDIT_LOG = "/var/log/igos-integrity-override.log"

    def _build_verify_config_if_present(self):
        """Return VerifyConfig if install-media manifest+key exist, else None.

        On signed install media, returns a VerifyConfig wired to GUI dialogs
        (paste-disabled Gtk.Entry per design doc §6.5.1). On dev/test
        environments without those files, returns None to skip the phase.
        """
        from pathlib import Path
        from installer.backend.install import VerifyConfig

        manifest = Path(self._INSTALL_MEDIA_MANIFEST)
        pubkey = Path(self._INSTALL_MEDIA_PUBKEY)
        if not manifest.exists() or not pubkey.exists():
            return None

        from ..integrity_dialog import make_gui_integrity_callbacks
        warning_cb, ack_cb = make_gui_integrity_callbacks(self._window)

        return VerifyConfig(
            manifest_path=manifest,
            public_key_path=pubkey,
            audit_log_path=Path(self._INTEGRITY_AUDIT_LOG),
            warning_callback=warning_cb,
            ack_callback=ack_cb,
        )

    # ------------------------------------------------------------------
    # Worker thread — runs run_install + posts completion to main loop.
    # ------------------------------------------------------------------

    def _run_install_worker(self, state, kwargs):
        """Runs OFF the GTK main loop. Must not touch widgets directly."""
        try:
            from installer.backend.install import run_install
            result = run_install(**kwargs)
        except Exception as e:  # last-resort guard — orchestrator shouldn't escape
            err = f"{type(e).__name__}: {e}"
            GLib.idle_add(self._on_install_failed, state, err)
            return

        GLib.idle_add(self._on_install_complete, state, result)

    # ------------------------------------------------------------------
    # Progress event marshaling — worker thread → GTK main loop.
    # ------------------------------------------------------------------

    def _on_progress_from_worker(self, phase, current, total, message):
        """Called from the worker thread by the orchestrator's
        progress_callback. Re-marshal to the main loop so widget updates
        run on the GTK thread."""
        GLib.idle_add(self._on_progress_event, phase, current, total, message)

    def _on_progress_event(self, phase, current, total, message):
        """Runs on the GTK main loop. Updates progress widgets.

        Distinguishes phase-boundary events (`total == self._phases_total`)
        from per-item sub-progress (`total != self._phases_total`, comes
        from PHASE_PACKAGES + PHASE_HOOKS fanout).
        """
        if self._phases_total is not None and total == self._phases_total:
            fraction = max(0.0, min(1.0, current / self._phases_total))
            self._progress_bar.set_fraction(fraction)
            label = f"{phase}: {message}" if message else phase
            self._progress_bar.set_text(label)
            self._status_label.set_label(
                f"Phase {min(current, self._phases_total)}/"
                f"{self._phases_total}: {phase} — {message}"
            )
        else:
            # Sub-progress fanout (per-package, per-hook). Don't touch the
            # phase-fraction; just update status text.
            self._status_label.set_label(
                f"{phase}: {message} ({current}/{total})"
            )
        return False  # one-shot idle_add

    # ------------------------------------------------------------------
    # Completion paths — both success and failure end here.
    # ------------------------------------------------------------------

    def _on_install_complete(self, state, result):
        """Runs on the GTK main loop on orchestrator return."""
        if result.success:
            state.install_completed = True
            state.install_failed = False
            # Drop password references from state now that they've been
            # consumed by the orchestrator. Defense-in-depth against
            # crash-dump / core-file credential leakage.
            state.clear_sensitive_data()
            self._progress_bar.set_fraction(1.0)
            self._progress_bar.set_text("Install complete")
            msg = "Install complete."
            overrides = getattr(result, "integrity_overrides_granted", 0)
            if overrides:
                msg += (
                    f"\n\n⚠ {overrides} integrity override(s) granted during install. "
                    f"Review {self._INTEGRITY_AUDIT_LOG} on the installed system "
                    f"for details."
                )
            if result.package_fail_count:
                msg += (
                    f"\n\nNote: {result.package_fail_count} package(s) failed "
                    f"during the packages phase (install continued):"
                )
                for n, m in result.failed_packages:
                    msg += f"\n  • {n}: {m}"
            self._status_label.set_label(msg)
            self.next_button.set_sensitive(True)
        else:
            # Integrity-abort gets a more specific error so the Done page
            # can surface it differently from a mid-pipeline crash.
            integrity_aborted = getattr(result, "integrity_aborted_at", None)
            if integrity_aborted:
                err_msg = (
                    f"Integrity verification aborted at {integrity_aborted}. "
                    f"No changes were made to the target disk."
                )
            else:
                err_msg = result.error_message or "(no error captured)"
            self._on_install_failed(
                state,
                err_msg,
                phase_completed=result.phase_completed,
            )
        return False  # one-shot idle_add

    def _on_install_failed(self, state, error_message, phase_completed=None):
        state.install_failed = True
        state.install_completed = False
        state.install_error_message = error_message
        # Drop password references on failure too — credentials were captured
        # but install didn't complete. We don't want them sitting in state
        # while the user is on the Done page reading the error message.
        state.clear_sensitive_data()
        self._progress_bar.set_text("Install failed")
        where = f" at phase {phase_completed}" if phase_completed else ""
        self._status_label.set_label(
            f"Install FAILED{where}.\n\nError: {error_message}\n\n"
            "Click Continue to view the failure summary."
        )
        # Failure still unlocks Continue — user goes to Done page which
        # surfaces the install_error_message + a retry-via-live-media hint.
        self.next_button.set_sensitive(True)
        return False  # one-shot idle_add
