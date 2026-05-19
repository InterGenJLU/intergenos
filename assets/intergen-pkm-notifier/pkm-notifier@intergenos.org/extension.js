// pkm-notifier extension.js — Q8 Phase C (GNOME shell notification surface).
//
// Tray indicator that surfaces available pkm package upgrades in the GNOME
// top bar. Reads /var/lib/pkm/available-updates.json (written by the
// pkm-check-updates.timer-driven service per Q8 Phase B) and shows a tray
// icon when count > 0. On click, opens a terminal that runs
// `pkm upgrade --all --dry-run` so the user can review what would be
// upgraded before invoking the real upgrade themselves.
//
// NEVER auto-upgrades — informational only per the operator-greenlit Q8
// design. User explicitly runs `pkm upgrade --all` (or types it after the
// dry-run preview) to act on the notification.
//
// Data flow:
//
//     systemd timer (Phase B) -> pkm check-updates (Phase A)
//                                -> /var/lib/pkm/available-updates.json
//                                -> Gio.FileMonitor (here)
//                                -> tray icon visible if count > 0
//                                -> on click: terminal w/ dry-run preview
//
// Modern GNOME 47/48 extension shape — ES6 module + Extension class +
// resource:/// imports per https://gjs.guide/extensions/.

import GObject from 'gi://GObject';
import St from 'gi://St';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {Extension, gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';

// Path is hardcoded to match Phase A's AVAILABLE_UPDATES_PATH constant.
// If that constant ever changes, this string must update in lockstep —
// cross-module data contract.
const AVAILABLE_UPDATES_PATH = '/var/lib/pkm/available-updates.json';

// Fallback polling interval (seconds) when Gio.FileMonitor doesn't fire
// (e.g., remote filesystem, fanotify limits, edge cases). Per Q8 dispatch
// the consumer cadence is 10 minutes; the file monitor catches most cases
// in real time so the poll is a safety net not the primary mechanism.
const POLL_INTERVAL_SECONDS = 600;

// Command launched on click. Dry-run gives the user a no-side-effects
// preview; they then run `pkm upgrade --all` (without --dry-run) to
// actually upgrade. Per the operator-greenlit Q8 design — never auto-
// upgrades; user decides when and what.
//
// The wrapper script keeps the terminal open after the dry-run so the
// user can read the output and type the real command. `exec bash`
// drops into an interactive shell preserving the dry-run output.
const PREVIEW_COMMAND = [
    'bash', '-c',
    "echo '$ pkm upgrade --all --dry-run'; pkm upgrade --all --dry-run; " +
    "echo; echo 'Up-arrow + edit to remove --dry-run, then Enter to upgrade.'; " +
    "exec bash",
];

// The Indicator class is the actual tray icon + menu surface. Wrapped
// in a GObject.registerClass per modern GJS shell extension convention.
const PkmNotifierIndicator = GObject.registerClass(
class PkmNotifierIndicator extends PanelMenu.Button {
    _init() {
        super._init(0.0, 'pkm Notifier', false);

        // Icon: software-update-available-symbolic matches the conventional
        // package-update glyph in icon themes (GNOME, Papirus, AdwaitaIcon).
        this._icon = new St.Icon({
            icon_name: 'software-update-available-symbolic',
            style_class: 'system-status-icon',
        });
        this.add_child(this._icon);

        // Count label appears next to icon when count > 0; hidden when zero
        // so users only see the indicator at all when there's something to act on.
        this._label = new St.Label({
            text: '',
            y_align: Clutter.ActorAlign.CENTER,
            style_class: 'pkm-notifier-count',
        });
        this.add_child(this._label);

        // Menu items rebuilt on each refresh — kept minimal:
        //   - "N package update(s) available" header
        //   - "Preview with pkm upgrade --all --dry-run" action
        //   - "Open Settings" (placeholder; v1.1 may add prefs dialog)
        this._headerItem = new PopupMenu.PopupMenuItem('', {reactive: false});
        this.menu.addMenuItem(this._headerItem);

        this._previewItem = new PopupMenu.PopupMenuItem(
            _('Preview with pkm upgrade --all --dry-run')
        );
        this._previewItem.connect('activate', () => this._launchPreview());
        this.menu.addMenuItem(this._previewItem);

        // Hidden by default — _refresh sets visible when count > 0.
        this.visible = false;
    }

    // Called by the Extension on each file-change or poll tick. Reads the
    // JSON, parses count, updates label + visibility. Silent on read /
    // parse errors — first boot before timer fires legitimately has no
    // JSON, and we don't want to pollute the user's shell with errors.
    refresh() {
        let count = 0;
        try {
            const file = Gio.File.new_for_path(AVAILABLE_UPDATES_PATH);
            const [ok, contents] = file.load_contents(null);
            if (ok) {
                const decoder = new TextDecoder();
                const text = decoder.decode(contents);
                const parsed = JSON.parse(text);
                if (typeof parsed.count === 'number' && parsed.count >= 0) {
                    count = parsed.count;
                }
            }
        } catch (e) {
            // Silent. No JSON yet, malformed JSON, or permission denied —
            // either way we surface "no updates" to the UI rather than
            // log-spam or notify-spam the user.
            count = 0;
        }

        if (count > 0) {
            this._label.text = ` ${count}`;
            this._headerItem.label.text = (count === 1)
                ? _('1 package update available')
                : _(`${count} package updates available`);
            this.visible = true;
        } else {
            this._label.text = '';
            this._headerItem.label.text = _('No updates available');
            this.visible = false;
        }
    }

    _launchPreview() {
        // Use Gio.AppInfo.create_from_commandline with NEEDS_TERMINAL so
        // GIO picks the user's preferred terminal emulator (gnome-terminal,
        // xterm, kitty, etc. — whatever they have configured). The bash
        // wrapper runs the dry-run then drops into an interactive shell
        // so the user can read the output and type the real command.
        try {
            const commandline = PREVIEW_COMMAND.map(arg => GLib.shell_quote(arg)).join(' ');
            const appInfo = Gio.AppInfo.create_from_commandline(
                commandline,
                null,
                Gio.AppInfoCreateFlags.NEEDS_TERMINAL
            );
            appInfo.launch([], null);
        } catch (e) {
            // If terminal launch fails (no terminal emulator installed,
            // or sandbox restrictions), surface a polite Main.notify so
            // the user knows the click was registered + what command to run.
            Main.notifyError(
                _('pkm Notifier: cannot launch terminal'),
                _('Run `pkm upgrade --all --dry-run` manually to preview updates.')
            );
        }
        this.menu.close();
    }
});

export default class PkmNotifierExtension extends Extension {
    enable() {
        this._indicator = new PkmNotifierIndicator();
        Main.panel.addToStatusArea(this.uuid, this._indicator);

        // Initial refresh — populate count if JSON already exists at
        // extension-load time.
        this._indicator.refresh();

        // Gio.FileMonitor watches the JSON path for changes. CHANGED,
        // CREATED, and CHANGES_DONE_HINT all trigger a refresh because
        // os.replace (Phase A's atomic-write pattern) emits CREATED on
        // the destination + DELETED on the .tmp source. CHANGES_DONE_HINT
        // is the "writer finished" signal for ordinary writes.
        try {
            const file = Gio.File.new_for_path(AVAILABLE_UPDATES_PATH);
            this._monitor = file.monitor(Gio.FileMonitorFlags.NONE, null);
            this._monitorId = this._monitor.connect(
                'changed',
                (_monitor, _file, _otherFile, eventType) => {
                    // Refresh on any event — count tied to file content,
                    // and atomic-rename produces multiple event types.
                    this._indicator.refresh();
                }
            );
        } catch (e) {
            // Monitor setup can fail on unusual filesystems; the polling
            // fallback below covers that case.
            this._monitor = null;
        }

        // Polling fallback — fires every POLL_INTERVAL_SECONDS as a safety
        // net for cases where Gio.FileMonitor doesn't fire (remote FS,
        // fanotify exhaustion, etc.). GLib.timeout_add_seconds with a
        // SOURCE_CONTINUE return from the callback re-arms automatically.
        this._pollSourceId = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT,
            POLL_INTERVAL_SECONDS,
            () => {
                this._indicator.refresh();
                return GLib.SOURCE_CONTINUE;
            }
        );
    }

    disable() {
        if (this._pollSourceId) {
            GLib.source_remove(this._pollSourceId);
            this._pollSourceId = null;
        }
        if (this._monitor && this._monitorId) {
            this._monitor.disconnect(this._monitorId);
            this._monitorId = null;
        }
        if (this._monitor) {
            this._monitor.cancel();
            this._monitor = null;
        }
        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }
    }
}
