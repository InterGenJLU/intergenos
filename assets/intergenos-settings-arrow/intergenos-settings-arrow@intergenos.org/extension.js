// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 InterGenOS
//
// intergenos-settings-arrow -- Re-route the Wi-Fi + Bluetooth QuickSettings
// menu arrows to open the corresponding Settings panel directly, instead of
// expanding the inline submenu.
//
// WHY (PI-Welcome-2, root-caused 2026-06-19): GNOME 49's QuickSettings renders
// an expanding quick-toggle submenu (`.quick-toggle-menu`) at full natural
// height with NO St.ScrollView / scroll area in the subtree. On a short panel
// (1366x768) the Wi-Fi network list is taller than the screen, so clicking the
// arrow re-lays-out the menu to its oversized height and the BoxPointer slides
// it off-screen (operator observed a "rapid downward motion off-screen"),
// silently. Scrolling/height-clamp was attempted (dash-to-panel's
// `_getBoxPointerPreferredHeight` clamp) and dropped -- the menu ignores the
// reduced preferred height with no scroll area; the theme CSS trim (9aa132a5)
// only recovers enough for the SHORT power submenu, not the tall Wi-Fi list.
//
// THE NON-SCROLL FIX (operator-chosen): re-route the Wi-Fi/Bluetooth arrow to
// launch the Settings panel (which has its own scrolling) where the full
// network/device list lives. The common case is unaffected -- the toggle
// itself still turns Wi-Fi/BT on/off and NetworkManager auto-connects known
// networks; only "pick a new network/device" now opens Settings. Bluetooth is
// included proactively (same overflow class once many devices are paired).
// Other toggles (power, wired, VPN) have short inline menus and are left alone.
//
// MECHANISM: override the target toggle's `menu.open()` (the method the arrow
// button invokes -- quickSettings.js: `_menuButton.connect('clicked', () =>
// this.menu.open())`) to launch the panel .desktop + close QuickSettings,
// instead of opening the inline menu. Targets are identified by
// `constructor.name` (NMWirelessToggle / BluetoothToggle) on the quickSettings
// `_network` / `_bluetooth` indicators' `quickSettingsItems`. Re-applied each
// time the QuickSettings menu opens (idempotent) so a Wi-Fi device toggle that
// NetworkManager creates after enable() is still covered. Fully restored on
// disable (the own-property override is deleted, reverting to the prototype
// QuickToggleMenu.open).

import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

// toggle class name -> Settings panel .desktop it should open instead.
const TARGETS = {
    'NMWirelessToggle': 'gnome-wifi-panel.desktop',
    'BluetoothToggle': 'gnome-bluetooth-panel.desktop',
};

export default class IntergenosSettingsArrowExtension extends Extension {
    enable() {
        this._patched = new Set();
        this._qs = Main.panel.statusArea.quickSettings;
        // Re-apply each time the QuickSettings menu opens: idempotent, and
        // covers a Wi-Fi device toggle that NM creates after enable().
        this._openId = this._qs.menu.connect('open-state-changed',
            (_m, isOpen) => {
                if (isOpen)
                    this._apply();
            });
        this._apply();
    }

    _apply() {
        const qs = this._qs;
        for (const indicator of [qs?._network, qs?._bluetooth]) {
            for (const item of indicator?.quickSettingsItems ?? []) {
                const desktopId = TARGETS[item?.constructor?.name];
                if (!desktopId || !item.menu || this._patched.has(item))
                    continue;
                // Capture the prototype open() for the fallback path.
                const origOpen = Object.getPrototypeOf(item.menu).open;
                item.menu.open = function () {
                    const app = Shell.AppSystem.get_default()
                        .lookup_app(desktopId);
                    if (app) {
                        qs.menu.close();
                        app.activate();
                    } else {
                        // Settings panel app missing -> fall back to the
                        // inline menu rather than dead-end the arrow.
                        origOpen.call(this);
                    }
                };
                this._patched.add(item);
            }
        }
    }

    disable() {
        if (this._openId) {
            this._qs?.menu.disconnect(this._openId);
            this._openId = 0;
        }
        for (const item of this._patched ?? []) {
            // Remove our own-property override -> reverts to the prototype's
            // QuickToggleMenu.open.
            if (item.menu)
                delete item.menu.open;
        }
        this._patched = null;
        this._qs = null;
    }
}
