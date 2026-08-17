# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Progress screen — eighth page of the 9-screen flow.

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

from gi.repository import Adw, Gio, GLib, Gtk

from ._base import _ForgePage


class ProgressPage(_ForgePage):
    tag = "progress"
    title = "Installing"
    # Install-execution phase — not a user-decision page; suppressed
    # from the step indicator's "X of N" count so Confirm reads as
    # the final user-facing step (7 of 7, not 7 of 9).
    in_step_indicator = False

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

        # Cancel button — left-aligned in the page body so it's visually
        # separate from the Next button in the page footer. Adw.NavigationView
        # doesn't expose a "tertiary action" slot, so an in-body button
        # is the cleanest placement.
        self._cancel_button = Gtk.Button(label="Cancel install")
        self._cancel_button.set_halign(Gtk.Align.START)
        self._cancel_button.add_css_class("destructive-action")
        self._cancel_button.connect("clicked", self._on_cancel_clicked)
        box.append(self._cancel_button)

        # ─── Live backend log (terminal-styled tail) ──────────────────
        # Decided 2026-05-26 ~14:00 CDT on the install retry:
        # "the indicator LOOKS nice, I have no way of knowing if it's
        # actually doing anything. Is it possible to have a small
        # terminal window showing the actual commands being ran?"
        #
        # The backend service logs to systemd journal as
        # forge-installer-backend.service. The intergenos user (uid 1000)
        # has read access to system unit journals on the live ISO, so
        # we can tail journalctl --follow directly from this unprivileged
        # GUI process — no extra privileges, no backend changes.
        log_label = Gtk.Label(label="Backend log (live)")
        log_label.add_css_class("heading")
        log_label.set_halign(Gtk.Align.START)
        log_label.set_margin_top(12)
        box.append(log_label)

        self._log_view = Gtk.TextView()
        self._log_view.set_editable(False)
        self._log_view.set_cursor_visible(False)
        self._log_view.set_monospace(True)
        self._log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._log_view.add_css_class("forge-backend-log")
        # Selectable text is on by default for TextView; keep it that way
        # so the user can copy a line out if they want to file a bug.
        # Don't focus-grab on present (matches the doc-viewer pattern).
        self._log_view.set_can_focus(False)

        self._log_scroller = Gtk.ScrolledWindow()
        self._log_scroller.set_min_content_height(220)
        self._log_scroller.set_hexpand(True)
        self._log_scroller.set_vexpand(True)
        self._log_scroller.set_policy(
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC,
        )
        self._log_scroller.add_css_class("forge-backend-log-card")
        self._log_scroller.set_child(self._log_view)
        box.append(self._log_scroller)

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
        # Fraction set by the most recent phase-boundary event; per-item
        # sub-progress rides the fill forward from here so the bar never
        # freezes mid-phase (notably through the long packages phase).
        self._last_boundary_fraction = 0.0
        # Cancellation: threading.Event set by the Cancel button, polled by
        # the backend orchestrator at every phase boundary. Created once
        # per page-instance lifetime; reset between back-then-forward navs
        # would let the install resume past a cancel, but the worker-thread
        # re-entry guard (F4) prevents re-spawn anyway.
        self._cancel_event = threading.Event()

        # Architecture B (2026-05-25): the install runs in the D-Bus
        # backend service, not in an in-process worker thread. These hold
        # the client + the backend's install_id so Cancel + integrity-ack
        # paths know who to call.
        self._client = None
        self._install_id = None
        self._install_started = False
        self._current_state = None

        # Backend log tail — Gio.Subprocess running journalctl --follow
        # against the forge-installer-backend.service unit. Streamed line
        # by line into self._log_view. Lifetime: started on on_load before
        # StartInstall, stopped on _on_install_complete / _on_install_failed
        # / Cancel-click.
        self._log_subprocess: Gio.Subprocess | None = None
        self._log_stream: Gio.DataInputStream | None = None

    def on_load(self, state):
        # Re-entry guard. NavigationView allows back/forward; on_load can
        # fire more than once for the same page-instance lifetime. Architecture
        # B (2026-05-25) routes the install through the D-Bus backend
        # instead of an in-process thread; re-entry guard still applies so
        # we don't issue two StartInstall calls.
        if self._install_started:
            return

        state.install_started = True
        self._install_started = True

        # Start the journalctl tail BEFORE StartInstall so we don't miss
        # the backend's first log lines.
        self._start_backend_log_tail()

        try:
            from installer.backend.install import PHASE_ORDER
            self._phases_total = len(PHASE_ORDER)
        except ImportError as e:
            self._on_install_failed(state, f"Cannot import orchestrator: {e}")
            return

        # Serialize install_io + yaml content for the D-Bus call. The
        # backend writes the yaml to /var/lib/forge/install.yaml itself
        # (root-owned dir, GUI can't write there).
        try:
            yaml_content = self._serialize_state_yaml(state)
        except Exception as e:
            self._on_install_failed(state, f"Cannot serialize install yaml: {e}")
            return

        install_io = state.to_install_io()
        dry_run = bool(getattr(self._window, "dry_run", False))
        self._current_state = state

        # Wire up the D-Bus client + signal subscriptions BEFORE issuing
        # StartInstall so we don't miss early Progress signals.
        from ..dbus_client import ForgeInstallerClient
        self._client = ForgeInstallerClient()
        try:
            self._client.subscribe(
                on_progress=self._on_bus_progress,
                on_integrity_warning=self._on_bus_integrity_warning,
                on_complete=self._on_bus_complete,
            )
            self._install_id = self._client.start_install(
                yaml_content, install_io, dry_run=dry_run,
            )
        except Exception as e:
            self._on_install_failed(
                state, f"Could not start install backend over D-Bus: "
                       f"{type(e).__name__}: {e}",
            )

    @staticmethod
    def _serialize_state_yaml(state) -> str:
        """Return state.build_install_yaml() rendered as a YAML string.

        Mirrors state.write_install_yaml's content but stays in-memory so
        the GUI (unprivileged) doesn't need write access to /var/lib/forge
        (that's the backend's job)."""
        import yaml as _yaml
        return _yaml.safe_dump(state.build_install_yaml(), sort_keys=False)

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
        from installer.backend.integrity import install_media_trust_decision

        manifest = Path(self._INSTALL_MEDIA_MANIFEST)
        pubkey = Path(self._INSTALL_MEDIA_PUBKEY)
        # Shared fail-closed policy (install-integrity §4C). Raises
        # ReleaseMediaIntegrityError on real release media missing its trust
        # set; "skip-dev" => explicit IGOS_DEV_ALLOW_UNVERIFIED marker => None.
        #
        # DEAD PATH in the live install: the GUI install runs via the D-Bus
        # backend (start_install -> backend_service._build_verify_config, which
        # owns the gating + the clean terminal-failure abort). This in-process
        # builder has NO live caller (the only in-process run_install is the
        # legacy test-fallback _run_install_worker, also not live under
        # Architecture B). It is kept for parity + test fallback. If ever
        # reached, the raised ReleaseMediaIntegrityError IS the intended
        # fail-closed signal (no disk write) — do NOT catch it and return None
        # here: that would reintroduce the silent-skip dormancy (red-team R1).
        decision = install_media_trust_decision(manifest, pubkey)
        if decision == "skip-dev":
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
    # D-Bus signal handlers — Architecture B
    # ------------------------------------------------------------------
    #
    # All three handlers run on the GLib main loop (Gio's signal
    # subscriptions dispatch there), so widget updates are safe directly
    # without GLib.idle_add re-marshaling. They map 1:1 onto the old
    # in-thread callbacks below; the rendering logic is unchanged so the
    # progress UI behaves identically to the pre-B architecture.

    def _on_bus_progress(self, install_id, phase, current, total, message):
        if self._install_id and install_id != self._install_id:
            return  # not our install
        self._on_progress_event(phase, current, total, message)

    def _on_bus_integrity_warning(self, install_id, package, expected, actual):
        if self._install_id and install_id != self._install_id:
            return
        from ..integrity_dialog import open_integrity_dialog_async
        open_integrity_dialog_async(
            self._window, package, expected, actual,
            lambda phrase: self._submit_integrity_ack(package, phrase),
        )

    def _submit_integrity_ack(self, package, override_phrase):
        if self._client is None or self._install_id is None:
            return False
        try:
            return self._client.ack_integrity_override(
                self._install_id, package, override_phrase,
            )
        except Exception:
            return False

    def _on_bus_complete(self, install_id, result_dict):
        if self._install_id and install_id != self._install_id:
            return

        # Re-package the dict into the same dataclass-shape attribute access
        # the existing _on_install_complete code path expects. Quick
        # SimpleNamespace wrap keeps the renderer untouched.
        from types import SimpleNamespace
        result_ns = SimpleNamespace(**result_dict)
        # Backend signals failed_packages as a list of (name, msg) tuples;
        # SimpleNamespace doesn't transform them. Existing code already
        # iterates as for n, m in result.failed_packages — compatible.
        self._on_install_complete(self._current_state, result_ns)

    def _run_install_worker(self, state, kwargs):
        """Legacy in-process worker — kept as a fallback hook for test
        suites that don't have a D-Bus environment. Not called in the
        live install path under Architecture B."""
        try:
            from installer.backend.install import run_install
            result = run_install(**kwargs)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            GLib.idle_add(self._on_install_failed, state, err)
            return
        GLib.idle_add(self._on_install_complete, state, result)

    def _on_progress_from_worker(self, phase, current, total, message):
        """Legacy worker → main-loop marshaller — see note above."""
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
            # Remember this boundary's fraction so per-item sub-progress can
            # ride the fill forward from here toward the next phase band.
            self._last_boundary_fraction = fraction
            label = f"{phase}: {message}" if message else phase
            self._progress_bar.set_text(label)
            self._status_label.set_label(
                f"Phase {min(current, self._phases_total)}/"
                f"{self._phases_total}: {phase} — {message}"
            )
        else:
            # Sub-progress fanout (per-package, per-hook). Update the bar TEXT,
            # the bar FILL, and the status line. Before this, only the status
            # line (below the bar) updated, so BOTH the bar's own label (above
            # the bar) AND the fill froze on the phase-start state — e.g.
            # "packages: installing 5 group(s)" at 5/13 — for the entire, and
            # longest, packages phase, making the installer look hung.
            # Operator-reported. The fill rides from the last phase-boundary
            # fraction toward the next band (each phase is 1/phases_total wide),
            # so per-item progress fills that band and meets the next boundary
            # fraction exactly (packages: 5/13 -> 6/13).
            self._progress_bar.set_text(f"{phase}: {current}/{total}")
            if self._phases_total and total:
                frac = (self._last_boundary_fraction
                        + (current / total) / self._phases_total)
                self._progress_bar.set_fraction(max(0.0, min(1.0, frac)))
            self._status_label.set_label(
                f"{phase}: {message} ({current}/{total})"
            )
        return False  # one-shot idle_add

    # ------------------------------------------------------------------
    # Completion paths — both success and failure end here.
    # ------------------------------------------------------------------

    def _on_install_complete(self, state, result):
        """Runs on the GTK main loop on orchestrator return."""
        # Cancel + failure paths both HIDE the Cancel button (PI-10) — the
        # install thread is gone, nothing left to interrupt, and a lingering
        # (even greyed) "Cancel install" on the completion page reads as broken.
        self._cancel_button.set_visible(False)
        # Backend won't emit further log lines past Complete; tear the
        # tail subprocess down so it doesn't leak past the page lifetime.
        self._stop_backend_log_tail()

        if getattr(result, "cancelled", False):
            # Cancel-routed completion: distinct from both success and
            # generic failure. State markers + status string both signal
            # cancel so the Done page renders the cancelled outcome.
            state.install_cancelled = True
            state.install_completed = False
            state.install_failed = False
            state.install_error_message = (
                result.error_message or "install cancelled by user"
            )
            state.clear_sensitive_data()
            self._progress_bar.set_text("Install cancelled")
            phase_str = (
                f" after {result.phase_completed}"
                if result.phase_completed else ""
            )
            self._status_label.set_label(
                f"Install CANCELLED{phase_str}.\n\n"
                "Click Continue for next steps."
            )
            self.next_button.set_sensitive(True)
        elif result.success:
            state.install_completed = True
            state.install_failed = False
            state.install_cancelled = False
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
        state.install_cancelled = False
        state.install_error_message = error_message
        self._cancel_button.set_visible(False)  # PI-10: hide, don't just grey
        self._stop_backend_log_tail()
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

    # ------------------------------------------------------------------
    # Backend log tail — Gio.Subprocess journalctl --follow streamer.
    # ------------------------------------------------------------------

    def _start_backend_log_tail(self) -> None:
        """Spawn journalctl --follow against the backend unit and start
        reading lines asynchronously. Output streams into self._log_view
        as it arrives. Safe to call once; idempotent on repeat invocation
        (subprocess won't be re-spawned if already running)."""
        if self._log_subprocess is not None:
            return
        try:
            self._log_subprocess = Gio.Subprocess.new(
                [
                    "journalctl",
                    "--follow",
                    "--output=short-iso",
                    "-u", "forge-installer-backend.service",
                    "--since", "now",
                ],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE,
            )
            stdout = self._log_subprocess.get_stdout_pipe()
            self._log_stream = Gio.DataInputStream.new(stdout)
            self._append_log_line(
                "[forge-gui] Backend log tail started. "
                "Streaming journalctl --follow -u forge-installer-backend.service"
            )
            self._read_next_log_line()
        except Exception as e:
            self._append_log_line(
                f"[forge-gui] Backend log tail failed to start: {e}"
            )

    def _read_next_log_line(self) -> None:
        if self._log_stream is None:
            return
        self._log_stream.read_line_async(
            GLib.PRIORITY_DEFAULT, None, self._on_log_line_read,
        )

    def _on_log_line_read(self, source, result):
        try:
            line, _length = source.read_line_finish_utf8(result)
        except Exception as e:
            self._append_log_line(f"[forge-gui] Log tail read error: {e}")
            return
        if line is None:
            # EOF — subprocess exited (journalctl wouldn't normally,
            # but a system journal restart or Cancel could cause it).
            self._append_log_line("[forge-gui] Log tail EOF.")
            return
        self._append_log_line(line)
        # Queue the next read on the GLib main loop.
        self._read_next_log_line()

    def _append_log_line(self, line: str) -> None:
        buf = self._log_view.get_buffer()
        end = buf.get_end_iter()
        buf.insert(end, line + "\n")
        # Auto-scroll to bottom — fresh end iter after insert.
        end = buf.get_end_iter()
        # scroll_to_iter args: iter, within_margin, use_align, xalign, yalign
        self._log_view.scroll_to_iter(end, 0.0, False, 0.0, 0.0)

    def _stop_backend_log_tail(self) -> None:
        if self._log_subprocess is None:
            return
        try:
            self._log_subprocess.force_exit()
        except Exception:
            pass
        self._log_subprocess = None
        self._log_stream = None

    # ------------------------------------------------------------------
    # Cancel handler — fires on Cancel button click.
    # ------------------------------------------------------------------

    def _on_cancel_clicked(self, _button):
        """Signal the backend orchestrator to abort at the next phase boundary.

        Granularity is phase-boundary, not mid-syscall — the in-flight
        phase finishes its work (so disk state stays consistent) then
        the orchestrator returns InstallResult(cancelled=True). The
        worker thread's completion routes through _on_install_complete
        which renders the cancelled outcome.

        Single-click cancel (no confirm dialog) — the button is already
        labelled "Cancel install" with destructive-action styling, and
        adding a confirm dialog would add friction in a recovery path
        the user has presumably thought about before clicking.

        Idempotent — clicking twice while the worker hasn't yet hit the
        next phase boundary just re-asserts the already-set event.
        """
        if self._cancel_event.is_set():
            return  # already cancelled; click ignored
        self._cancel_event.set()
        self._cancel_button.set_sensitive(False)
        self._cancel_button.set_label("Cancelling…")
        # Architecture B: cancel routes through the D-Bus backend.
        if self._client is not None and self._install_id is not None:
            try:
                self._client.cancel_install(self._install_id)
            except Exception as e:
                # Non-fatal — backend may have already completed.
                current_status = self._status_label.get_label() or ""
                self._status_label.set_label(
                    f"{current_status}\n\nCancel call failed: {e}"
                )
                return
        # Update status so the user sees acknowledgment even though the
        # backend may not hit the next phase boundary for some seconds.
        current_status = self._status_label.get_label() or ""
        self._status_label.set_label(
            f"{current_status}\n\n"
            "Cancel requested — install will stop at the next phase boundary."
        )
