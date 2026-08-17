// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 InterGenJLU
//
// Preferences for InterGenOS Launch Monitor — the gsettings-backed monitor
// picker. The user DECLARES which output launched game windows are moved to;
// the extension obeys. Enumerates outputs by connector via Gdk (prefs run in a
// separate GTK4 process with no Shell/Meta access).

import Adw from 'gi://Adw';
import Gtk from 'gi://Gtk';
import Gdk from 'gi://Gdk';
import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

export default class LaunchMonitorPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();

        const page = new Adw.PreferencesPage();
        const group = new Adw.PreferencesGroup({
            title: 'Launch Monitor',
            description: 'Move launched game windows to the output you declare, ' +
                'before the first frame. Matches Steam titles (steam_app_*) and ' +
                'gamescope by default.',
        });
        page.add(group);

        const connectors = this._connectors();
        const labels = ['Disabled (do not move)', ...connectors];
        const model = new Gtk.StringList();
        for (const l of labels)
            model.append(l);

        const row = new Adw.ComboRow({
            title: 'Move game windows to',
            subtitle: 'The output a game window is placed on at launch',
            model,
        });

        const current = settings.get_string('launch-monitor');
        let selected = 0;
        if (current) {
            const at = connectors.indexOf(current);
            selected = at >= 0 ? at + 1 : 0;
        }
        row.set_selected(selected);
        row.connect('notify::selected', () => {
            const sel = row.get_selected();
            settings.set_string('launch-monitor',
                sel === 0 ? '' : connectors[sel - 1]);
        });
        group.add(row);

        page.add(this._interGenGroup(settings));

        window.add(page);
    }

    // What happens to InterGen when a game starts. Declared once here; applied
    // at every launch with no prompt unless "Ask each time" is chosen. Same
    // declare-once shape as the monitor picker above: the user states what they
    // want, the system obeys.
    _interGenGroup(settings) {
        const group = new Adw.PreferencesGroup({
            title: 'InterGen at game launch',
            description: 'InterGen keeps a language model loaded in memory. ' +
                'Pausing it while a game runs hands that memory back to the ' +
                'machine, and it loads again when you close the game.',
        });

        // Index order IS the stored-value order below — keep them in step.
        const values = ['pause', 'keep', 'ask'];
        const labels = [
            'Pause InterGen (recommended)',
            'Keep InterGen available',
            'Ask each time',
        ];
        const model = new Gtk.StringList();
        for (const l of labels)
            model.append(l);

        const row = new Adw.ComboRow({
            title: 'When a game starts',
            subtitle: 'Keeping InterGen available makes sense when the game ' +
                'and InterGen do not share a graphics card',
            model,
        });

        const current = settings.get_string('game-launch-intergen');
        const at = values.indexOf(current);
        row.set_selected(at >= 0 ? at : 0);
        row.connect('notify::selected', () => {
            const sel = row.get_selected();
            if (sel >= 0 && sel < values.length)
                settings.set_string('game-launch-intergen', values[sel]);
        });
        group.add(row);
        return group;
    }

    _connectors() {
        const out = [];
        const display = Gdk.Display.get_default();
        const monitors = display ? display.get_monitors() : null;
        const n = monitors ? monitors.get_n_items() : 0;
        for (let i = 0; i < n; i++) {
            const m = monitors.get_item(i);
            const c = m && m.get_connector ? m.get_connector() : null;
            if (c)
                out.push(c);
        }
        return out;
    }
}
