// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 InterGenOS
//
// intergen-no-overview extension.js — Suppresses the GNOME activities
// overview at every session startup. Operator-direct fleet-default per
// 2026-05-21T~01:Z: "PLEASE MAKE THIS OUR DEFAULT".
//
// Pairs with intergen-firstboot@intergenos.org which uses the same
// startup-complete signal to fire the once-per-user branded animation.
// This extension is independent of the firstboot lifecycle -- it runs
// every login (not gated by a marker), so the activities overview stays
// suppressed even after the firstboot marker is set.
//
// Modeled on the canonical fthx/no-overview source: subscribe to
// Main.layoutManager 'startup-complete' + invoke Main.overview.hide().
//
// Default-enabled via config/gsettings/91_intergenos-extensions.gschema.override
// per D-006 SSoT.

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

export default class IntergenNoOverviewExtension extends Extension {
    enable() {
        this._startupCompleteId = Main.layoutManager.connect(
            'startup-complete', () => Main.overview.hide());

        // If startup-complete already fired before our enable() (which
        // happens when the extension is manually toggled mid-session),
        // hide the overview directly so the user-toggled enable behaves
        // consistently with the every-login intent.
        if (Main.layoutManager._startingUp === false) {
            Main.overview.hide();
        }
    }

    disable() {
        if (this._startupCompleteId) {
            Main.layoutManager.disconnect(this._startupCompleteId);
            this._startupCompleteId = 0;
        }
    }
}
