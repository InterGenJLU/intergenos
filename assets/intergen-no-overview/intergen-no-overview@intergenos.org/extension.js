// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 InterGenOS
//
// intergen-no-overview extension.js -- Suppresses the GNOME activities
// overview at every session startup via Pattern X1 (declarative session-
// mode property mutation + overview-controls state-adjustment reset).
//
// Default-enabled behavior, decided 2026-05-21.
//
// Architecture (5-vantage research convergence 2026-05-21):
//
// The GNOME Shell 49.x startup-overview-reveal does NOT call
// Main.overview.show. It calls Main.overview.runStartupAnimation() via
// LayoutManager._startupAnimationSession(), gated by
// Main.sessionMode.hasOverview. Setting hasOverview=false at extension
// enable() time (before LayoutManager._startupAnimationSession reads it)
// causes the startup animation to take the else-branch -- a plain
// uiGroup.ease scale+fade WITHOUT overview involvement. The Overview
// class's show() method also carries an upstream-canonical isDummy
// guard at overview.js:show() ~567-571 that auto-no-ops when isDummy
// is true (set when hasOverview becomes false via _sessionUpdated).
//
// The Main.overview._overview.controls._stateAdjustment.value = HIDDEN
// reset is REQUIRED per dash-to-dock empirical pitfall comment:
// "Convince LayoutManager to use the legacy startup animation: Reset
// overview controls state to HIDDEN, as skipping the startup overview
// leaves it stuck at WINDOW_PICKER". Without this reset the overview-
// controls state machine ends up in stale state and the user sees
// broken behavior when they later open the overview manually. Pitfall
// independently observed by just-perfection (startupStatusSet) and
// ArcMenu (hide-overview-on-startup).
//
// On 'startup-complete' (LayoutManager signal emitted after the cover
// pane is destroyed + _startingUp flips false, per layout.js
// _startupAnimationComplete) we restore hasOverview to its captured-
// original value so post-startup overview-toggle works normally.
//
// EXCLUDES Main.layoutManager.startInOverview = false (which appears
// in just-perfection main HEAD and fthx/no-overview historical pre-46
// code) -- empirical extraction at 49.4 found ZERO matches for
// startInOverview across 150 JS files; the API was REMOVED in 49.x.
//
// Production-pattern precedent: dash-to-dock disableOverviewOnStartup
// setting + just-perfection startup-status NONE + ArcMenu
// hide-overview-on-startup setting -- 5/5 surveyed extensions converge
// on this exact pattern (survey grep, 2026-05-21).
//
// Pairs with intergen-firstboot@intergenos.org which renders the
// once-per-user welcome animation overlay during the suppressed window.
// Both extensions are default-enabled via
// config/gsettings/91_intergenos-extensions.gschema.override per D-006.
// Per D4 operator-ratification 2026-05-21: this extension is the SOLE
// owner of Main.sessionMode.hasOverview mutation; intergen-firstboot
// does NOT touch hasOverview, eliminating any extension-load-order race.

import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as OverviewControls from 'resource:///org/gnome/shell/ui/overviewControls.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

export default class IntergenNoOverviewExtension extends Extension {
    enable() {
        this._hadOverview = null;
        this._startupCompleteId = 0;
        this._originalRunStartupAnimation = null;
        this._originalShow = null;
        this._lateHideId = 0;
        this._postStartupHideId = 0;

        // Enabled OUTSIDE the startup window. Two cases:
        //   (1) Warm logout->login race (GBC001.5, 2026-06-05): on a warm
        //       re-login the shell finishes startup faster than the cold
        //       boot, so the extension system can enable() us AFTER
        //       LayoutManager._startingUp already flipped false. The LAYER
        //       1/2 startup interception below never gets to run, and the
        //       startup overview slips through and stays on screen. (Cold
        //       boot enables us in time, so this only bites the re-login
        //       path -- a fresh boot lands in the desktop correctly.)
        //   (2) A deliberate mid-session manual enable (no overview showing).
        // For (1) the startup overview is up right now (or lands a tick
        // later); hide it so the user still lands in the desktop. For (2)
        // nothing is showing so hide() is a harmless no-op. We do NOT touch
        // hasOverview here, so a later user-driven overview-toggle behaves
        // normally either way.
        if (!Main.layoutManager._startingUp) {
            this._hideLateStartupOverview();
            // A second pass shortly after catches the reveal when it lands
            // just after our enable() on the warm path.
            this._lateHideId = GLib.timeout_add(
                GLib.PRIORITY_DEFAULT, 300, () => {
                    this._hideLateStartupOverview();
                    this._lateHideId = 0;
                    return GLib.SOURCE_REMOVE;
                });
            return;
        }

        // Save the original hasOverview value so the on-startup-complete
        // restore can return the session to its default-mode behavior.
        this._hadOverview = Main.sessionMode.hasOverview;

        // ============================================================
        // LAYER 1: Declarative Pattern X1 (gate-check intercept)
        // ============================================================
        // Flip hasOverview to false. LayoutManager._startupAnimationSession
        // (layout.js, the entry point of the visual startup animation)
        // reads this property at the if/else gate and takes the
        // overview-bypass else-branch (plain uiGroup.ease scale+fade)
        // WHEN this mutation lands before the gate read.
        // Property assignment does NOT emit Main.sessionMode 'updated';
        // Overview._sessionUpdated is therefore NOT triggered, isDummy
        // does not flip, _overview is not torn down -- so the paths we
        // touch in the next statements remain valid.
        Main.sessionMode.hasOverview = false;

        // Reset overview-controls state to HIDDEN. Without this the
        // _stateAdjustment.value remains at its constructor default
        // (effectively WINDOW_PICKER), and any later user-driven
        // overview-toggle finds the state machine in a stale state.
        // dash-to-dock + just-perfection both apply this two-step
        // mutation; fthx/no-overview's current variant does NOT, which
        // is the root cause of fthx issue #12 "Cannot leave overview &
        // broken search box".
        Main.overview._overview.controls._stateAdjustment.value =
            OverviewControls.ControlsState.HIDDEN;

        // ============================================================
        // LAYER 2: Method-replacement (race-loss safety net)
        // ============================================================
        // Even with this extension at position 1 in the enabled-extensions
        // array (load-first), v7.1 empirical retest 2026-05-21T~07:Z still
        // observed a brief overview flash. Root cause: systemBackground
        // 'loaded' fires asynchronously and its idle_add callback can
        // schedule _prepareStartupAnimation -> _startupAnimationSession
        // BEFORE this extension's enable() runs in pathological-fast
        // background-load cases. When the gate-check reads hasOverview
        // BEFORE our LAYER 1 mutation lands, the if-branch fires and
        // Main.overview.runStartupAnimation() is invoked.
        //
        // LAYER 2 mitigates the race by replacing the methods that the
        // startup chain calls with no-ops. Even if the gate-check sees
        // hasOverview=true and reaches the if-branch, the call to
        // Main.overview.runStartupAnimation() lands on our no-op and
        // returns immediately resolved; no visual animation runs.
        //
        // Per 5-vantage research convergence 2026-05-21: of the surveyed
        // production extensions, none monkey-patch runStartupAnimation,
        // but that is because none had our specific timing constraint
        // (extension+animation overlay with a 22.4s window where the
        // overview MUST NOT fire). The method-replacement pattern is
        // documented in gjs.guide "Updates and Breakage" as RISKY but
        // VALID when the alternative declarative mechanism is insufficient,
        // as we have empirically confirmed it is for our use case.
        this._originalRunStartupAnimation = Main.overview.runStartupAnimation;
        Main.overview.runStartupAnimation = async function() {
            // No-op. async to match upstream signature so layout.js
            // `await Main.overview.runStartupAnimation()` unblocks
            // immediately with a resolved Promise.
        };

        // Defensive: also override Main.overview.show. Worker A's research
        // 2026-05-21 confirmed show() is NOT called from the session-
        // startup path (runStartupAnimation is), but covering show()
        // catches any edge case where another extension or upstream code
        // calls it during the startup window. The upstream isDummy guard
        // at overview.js:show() ~567-571 would also no-op show() when
        // hasOverview=false, but isDummy is set via _sessionUpdated which
        // only fires on Main.sessionMode 'updated' signal -- pure property
        // mutation in LAYER 1 doesn't trigger that signal, so isDummy
        // stays at its previous value. This override is the safety net.
        this._originalShow = Main.overview.show;
        Main.overview.show = function() {
            // No-op. Restored on startup-complete.
        };

        // Restore on startup-complete. LayoutManager fires this signal
        // from _startupAnimationComplete() AFTER the cover-pane is
        // destroyed and _startingUp flips false. At that point the
        // startup-time gate has passed; the user's session is live and
        // overview-toggle should behave per normal session-mode.
        this._startupCompleteId = Main.layoutManager.connect(
            'startup-complete', () => this._onStartupComplete());
    }

    _hideLateStartupOverview() {
        // Hide the startup overview if it slipped through on a late
        // (post-_startingUp) enable, and settle the overview-controls
        // state machine to HIDDEN so a later manual toggle isn't stuck at
        // WINDOW_PICKER (the same dash-to-dock pitfall the LAYER 1 reset
        // addresses on the cold-boot path).
        if (Main.overview.visible)
            Main.overview.hide();
        try {
            Main.overview._overview.controls._stateAdjustment.value =
                OverviewControls.ControlsState.HIDDEN;
        } catch (e) {
            // Internal layout not reachable at this instant; the hide()
            // above is the load-bearing action.
        }
    }

    _onStartupComplete() {
        // Restore LAYER 2 method overrides first so any handler triggered
        // by the LAYER 1 hasOverview restore (below) lands on the
        // original methods.
        if (this._originalRunStartupAnimation) {
            Main.overview.runStartupAnimation = this._originalRunStartupAnimation;
            this._originalRunStartupAnimation = null;
        }
        if (this._originalShow) {
            Main.overview.show = this._originalShow;
            this._originalShow = null;
        }
        // Restore LAYER 1 declarative state BEFORE the force-hide, so hide()
        // sees the real (non-dummy) overview.
        if (this._hadOverview !== null) {
            Main.sessionMode.hasOverview = this._hadOverview;
            this._hadOverview = null;
        }

        // ============================================================
        // LAYER 3: unconditional post-startup force-hide (PI-1 fix)
        // ============================================================
        // The fast-NVMe first boot (PI-1, .192) showed LAYER 1/2 can BOTH
        // lose the race: the extension loader awaits a dynamic import() of
        // this module, so enable() can land AFTER LayoutManager's low-priority
        // idle ran _startupAnimationSession() and read hasOverview=true ->
        // the overview is revealed and STAYS (the old _onStartupComplete only
        // restored state, never force-hid). 'startup-complete' is emitted by
        // _startupAnimationComplete() AFTER the gate read + after _startingUp
        // flips false -- it is a POST-condition of the reveal, so a force-hide
        // here CANNOT lose the race regardless of enable() timing. If the
        // reveal leaked, Overview.runStartupAnimation set _shown=true, so
        // hide() actually tears it down (not a !_shown early-return no-op);
        // if nothing leaked, hide() is a harmless no-op. A single short confirm
        // pass catches a reveal that lands a frame after startup-complete.
        this._hideLateStartupOverview();
        if (!this._postStartupHideId) {
            this._postStartupHideId = GLib.timeout_add(
                GLib.PRIORITY_DEFAULT, 300, () => {
                    this._hideLateStartupOverview();
                    this._postStartupHideId = 0;
                    return GLib.SOURCE_REMOVE;
                });
        }

        if (this._startupCompleteId) {
            Main.layoutManager.disconnect(this._startupCompleteId);
            this._startupCompleteId = 0;
        }
    }

    disable() {
        // Cancel any pending late-hide timeout (warm re-login path).
        if (this._lateHideId) {
            GLib.Source.remove(this._lateHideId);
            this._lateHideId = 0;
        }
        // Cancel any pending LAYER 3 post-startup confirm pass.
        if (this._postStartupHideId) {
            GLib.Source.remove(this._postStartupHideId);
            this._postStartupHideId = 0;
        }
        // Disconnect signal first to prevent the handler firing during
        // disable() teardown.
        if (this._startupCompleteId) {
            Main.layoutManager.disconnect(this._startupCompleteId);
            this._startupCompleteId = 0;
        }
        // Restore LAYER 2 method overrides if still held. This covers
        // mid-startup-window disable() where startup-complete has not
        // yet fired. If startup-complete already fired,
        // _onStartupComplete cleared these to null and the next two
        // blocks are no-ops.
        if (this._originalRunStartupAnimation) {
            Main.overview.runStartupAnimation = this._originalRunStartupAnimation;
            this._originalRunStartupAnimation = null;
        }
        if (this._originalShow) {
            Main.overview.show = this._originalShow;
            this._originalShow = null;
        }
        // Restore LAYER 1 hasOverview if still held.
        if (this._hadOverview !== null) {
            Main.sessionMode.hasOverview = this._hadOverview;
            this._hadOverview = null;
        }
    }
}
