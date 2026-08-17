// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 InterGenJLU
//
// InterGenOS Launch Monitor — in-compositor game-window placement, and the
// game-launch handover of the machine's memory to the game.
//
// WHY, placement (design item, proposed 2026-07-08): a game launched on the
// wrong output cannot be moved by any external process under GNOME Wayland —
// the compositor owns window placement. So the mover must live IN the
// compositor, as a first-party GNOME Shell extension (the shipped
// intergenos-settings-arrow is the precedent + infrastructure): watch
// window-created, and when a window's class is a game class, move it to the
// user-declared output BEFORE the first frame so the user never sees the wrong
// placement.
//
// WHY, the pause (decided 2026-08-04): the same signal that identifies a game
// window is the only reliable moment to tell InterGen a game has started. By
// default InterGen then gets out of the way — it stops its model servers, so
// the video memory AND the system memory they held go back to the machine —
// and loads them again when the last game window closes. Letting the kernel
// evict the weights instead is worse in practice: the migration cost is paid
// during play, as stutter, and eviction returns no system memory at all. There
// is no prompt at launch by default, because a dialog that steals focus is the
// wrong thing to do at the moment a game is starting; the user declares what
// should happen ONCE, in this extension's preferences, and the system obeys.
//
// MATCH: Steam sets WM_CLASS to steam_app_<appid> for every title it launches
// (native or Proton/pressure-vessel); gamescope tags its own surface. Both are
// prefix-matched by default (derived from the measured launch path — all titles
// run through Steam Linux Runtime — not a hardcoded per-title list), and the
// prefix + exact lists are user-extensible via gsettings for non-Steam titles.
//
// PRIME DIRECTIVE + fail-safe, on both behaviours: the user DECLARES the
// monitor (empty = disabled, nothing ever moves) and DECLARES what happens to
// InterGen at game launch (pause / keep available / ask). An unresolved or
// absent declared output moves nothing (never to a wrong monitor); a pause is
// never recorded without the daemon confirming it; and every pause this
// extension places is released again when the window goes, when the extension
// is disabled, or — enforced by the daemon itself — if this process dies while
// still holding one.

import Meta from 'gi://Meta';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';

import {Extension, gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';

const INTERGEN_BUS_NAME = 'com.intergenos.InterGen';
const INTERGEN_OBJECT_PATH = '/com/intergenos/InterGen';
const INTERGEN_INTERFACE = 'com.intergenos.InterGen';

// Generous on purpose. Pausing stops two model servers (a graceful stop waits
// on each), and resuming loads a multi-gigabyte model back onto the accelerator
// and waits for it to answer healthy. Both calls are made ASYNCHRONOUSLY — the
// compositor never waits on them — so a long ceiling costs nothing and only
// prevents a slow-but-succeeding call from being misread as a failure.
const DAEMON_CALL_TIMEOUT_MS = 180000;

// The icon the InterGen panel ships under; used for the notification so the
// message is recognisably from InterGen rather than from a nameless extension.
const INTERGEN_ICON = 'org.intergenos.InterGenPanel';

export default class LaunchMonitorExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        // Meta.Window -> {id, unmanagingId, held, pending}
        //   id        the window class, the identifier both pause edges use
        //   held      the daemon has confirmed a pause placed for this window
        //   pending   a pause call is in flight for this window
        this._gameWindows = new Map();
        this._windowCreatedId = global.display.connect('window-created',
            (_display, win) => this._onWindowCreated(win));
    }

    disable() {
        if (this._windowCreatedId) {
            global.display.disconnect(this._windowCreatedId);
            this._windowCreatedId = 0;
        }
        // Release every pause this extension placed. A disabled extension must
        // never leave InterGen paused: nothing would be left watching the game
        // windows to release it. (If this process dies without reaching here,
        // the daemon releases the holds itself when our bus name vanishes.)
        if (this._gameWindows) {
            for (const [win, rec] of this._gameWindows) {
                if (rec.unmanagingId) {
                    try {
                        win.disconnect(rec.unmanagingId);
                    } catch (e) {
                        // The window may already be gone; nothing to disconnect.
                    }
                }
                if (rec.held || rec.pending)
                    this._callDaemon('ResumeAfterGame', rec.id);
            }
            this._gameWindows.clear();
            this._gameWindows = null;
        }
        this._settings = null;
    }

    // ---- game-window matching ---------------------------------------------

    _matchesGame(wmClass) {
        if (!wmClass)
            return false;
        const lc = wmClass.toLowerCase();
        for (const p of this._settings.get_strv('game-wm-class-prefixes')) {
            if (p && lc.startsWith(p.toLowerCase()))
                return true;
        }
        for (const c of this._settings.get_strv('game-wm-classes')) {
            if (c && lc === c.toLowerCase())
                return true;
        }
        return false;
    }

    // Resolve the declared output to a monitor index, or -1 (do not move).
    _targetMonitorIndex() {
        const connector = this._settings.get_string('launch-monitor');
        if (!connector)
            return -1; // disabled — never move
        // Direct-index fallback (a plain integer).
        if (/^\d+$/.test(connector)) {
            const idx = parseInt(connector, 10);
            return idx >= 0 && idx < global.display.get_n_monitors() ? idx : -1;
        }
        try {
            const mm = global.backend.get_monitor_manager();
            const idx = mm.get_monitor_for_connector(connector);
            return (typeof idx === 'number' && idx >= 0) ? idx : -1;
        } catch (e) {
            // Fail-safe: unresolved connector never moves the window.
            logError(e, 'intergenos-launch-monitor: could not resolve connector ' +
                `"${connector}" — not moving`);
            return -1;
        }
    }

    _moveToDeclaredMonitor(win) {
        const idx = this._targetMonitorIndex();
        if (idx < 0)
            return;
        if (win.get_monitor() === idx)
            return; // already on the declared output
        win.move_to_monitor(idx);
    }

    // A window has been identified as a game window: place it, then start
    // tracking it so InterGen is told about both edges of its life.
    _onGameWindow(win) {
        if (!win || win.get_window_type() !== Meta.WindowType.NORMAL)
            return;
        if (!this._matchesGame(win.get_wm_class()))
            return;
        this._moveToDeclaredMonitor(win);
        this._trackGameWindow(win);
    }

    _onWindowCreated(win) {
        // WM_CLASS may be unset at window-created; act as early as possible
        // (pre-first-frame). If the class is already known, act now; else act
        // once on the first wm-class notification, cleaning up either way.
        if (win.get_wm_class()) {
            this._onGameWindow(win);
            return;
        }
        let classId = 0;
        let unmanagingId = 0;
        const cleanup = () => {
            if (classId)
                win.disconnect(classId);
            if (unmanagingId)
                win.disconnect(unmanagingId);
            classId = 0;
            unmanagingId = 0;
        };
        classId = win.connect('notify::wm-class', () => {
            this._onGameWindow(win);
            cleanup();
        });
        unmanagingId = win.connect('unmanaging', cleanup);
    }

    // ---- game-launch pause -------------------------------------------------

    _trackGameWindow(win) {
        if (!this._gameWindows || this._gameWindows.has(win))
            return;
        const rec = {
            id: win.get_wm_class() || 'a game',
            unmanagingId: 0,
            held: false,
            pending: false,
        };
        // 'unmanaging' is the window's exit edge: it fires when the window
        // stops being managed by the compositor, which is what "the game
        // closed" looks like from in here.
        rec.unmanagingId = win.connect('unmanaging',
            () => this._untrackGameWindow(win));
        this._gameWindows.set(win, rec);
        this._applyLaunchPolicy(win, rec);
    }

    _untrackGameWindow(win) {
        if (!this._gameWindows)
            return;
        const rec = this._gameWindows.get(win);
        if (!rec)
            return;
        this._gameWindows.delete(win);
        if (rec.unmanagingId) {
            try {
                win.disconnect(rec.unmanagingId);
            } catch (e) {
                // Already gone; nothing to disconnect.
            }
            rec.unmanagingId = 0;
        }
        // Only release what we actually placed. A pause still in flight is
        // released by its own reply handler, which sees the window is no longer
        // tracked — that is the window-closed-before-the-reply-landed case, and
        // without it a fast-exiting game would strand the pause.
        if (rec.held)
            this._callDaemon('ResumeAfterGame', rec.id);
    }

    // What happens to InterGen when a game starts is the user's declared
    // choice. 'pause' (the default) hands the memory over; 'keep' leaves
    // InterGen running, which is the honest answer on a machine where the game
    // and InterGen do not share an accelerator; 'ask' asks, once per launch.
    _applyLaunchPolicy(win, rec) {
        let policy = 'pause';
        try {
            policy = this._settings.get_string('game-launch-intergen');
        } catch (e) {
            logError(e, 'intergenos-launch-monitor: could not read the ' +
                'game-launch setting — using the declared default (pause)');
        }
        if (policy === 'keep')
            return;
        if (policy === 'ask') {
            this._askWhetherToPause(win, rec);
            return;
        }
        if (policy !== 'pause') {
            log('intergenos-launch-monitor: unrecognised game-launch setting ' +
                `"${policy}" — using the declared default (pause)`);
        }
        this._requestPause(win, rec);
    }

    _requestPause(win, rec) {
        if (!this._gameWindows || !this._gameWindows.has(win))
            return; // the window went away before we got here
        rec.pending = true;
        this._callDaemon('PauseForGame', rec.id, payload => {
            rec.pending = false;
            // If the window closed while the call was in flight, the pause
            // landed with nobody left to release it — release it now.
            if (!this._gameWindows || !this._gameWindows.has(win)) {
                this._callDaemon('ResumeAfterGame', rec.id);
                return;
            }
            rec.held = true;
            // Tell the user ONCE. The daemon reports every hold it is holding;
            // a length of one means this call is what actually paused InterGen,
            // so a second window of the same game does not produce a second
            // message.
            const games = payload ? payload.games : null;
            if (!Array.isArray(games) || games.length === 1)
                this._notifyPaused(rec.id);
        }, () => {
            rec.pending = false;
        });
    }

    // 'ask each time' — a notification with buttons, deliberately NOT a modal
    // dialog. The reason a dialog is wrong at game launch does not stop
    // applying because the user asked to be asked: this way the question is
    // visible without taking the keyboard away from a game that is starting.
    // No answer means no change, which is the choice that leaves the user's
    // machine as they left it.
    _askWhetherToPause(win, rec) {
        const source = this._notificationSource();
        const notification = new MessageTray.Notification({
            source,
            title: _('Pause InterGen?'),
            body: _(`${rec.id} is starting. Pausing InterGen frees the memory ` +
                    'its model is holding until you close the game.'),
            iconName: INTERGEN_ICON,
            isTransient: false,
        });
        notification.addAction(_('Pause InterGen'),
            () => this._requestPause(win, rec));
        notification.addAction(_('Leave it running'), () => {});
        source.addNotification(notification);
    }

    _notifyPaused(gameId) {
        const source = this._notificationSource();
        const notification = new MessageTray.Notification({
            source,
            title: _('InterGen paused'),
            body: _(`InterGen is paused while ${gameId} runs, and loads again ` +
                    'when you close it.'),
            iconName: INTERGEN_ICON,
            isTransient: true,
        });
        source.addNotification(notification);
    }

    // A fresh source per notification: a MessageTray source destroys itself
    // once its last notification is gone, so a retained one would be dead by
    // the second message.
    _notificationSource() {
        const source = new MessageTray.Source({
            title: _('InterGen'),
            iconName: INTERGEN_ICON,
        });
        Main.messageTray.add(source);
        return source;
    }

    // ---- the daemon call ---------------------------------------------------

    _callDaemon(method, gameId, onSuccess, onFailure) {
        let bus;
        try {
            bus = Gio.DBus.session;
        } catch (e) {
            logError(e, 'intergenos-launch-monitor: no session bus');
            if (onFailure)
                onFailure();
            return;
        }
        bus.call(
            INTERGEN_BUS_NAME,
            INTERGEN_OBJECT_PATH,
            INTERGEN_INTERFACE,
            method,
            new GLib.Variant('(s)', [gameId]),
            new GLib.VariantType('(s)'),
            // DO_NOT_AUTO_START is load-bearing, not a tidiness flag: InterGen
            // ships a D-Bus activation file, so a plain call would START the
            // assistant in order to tell it to stop. On a machine where InterGen
            // is not running there is nothing to pause, and this makes the call
            // fail harmlessly instead of launching it.
            Gio.DBusCallFlags.DO_NOT_AUTO_START,
            DAEMON_CALL_TIMEOUT_MS,
            null,
            (connection, res) => {
                let payload = null;
                try {
                    const reply = connection.call_finish(res);
                    const [text] = reply.deepUnpack();
                    payload = JSON.parse(text);
                } catch (e) {
                    this._onDaemonCallFailed(method, e);
                    if (onFailure)
                        onFailure();
                    return;
                }
                if (onSuccess)
                    onSuccess(payload);
            });
    }

    _onDaemonCallFailed(method, error) {
        // InterGen not running at all is the ordinary case on a machine that
        // does not use it — there is nothing to pause and nothing to say. Any
        // other failure is logged, because a pause that silently did not happen
        // would otherwise look exactly like one that did.
        try {
            const remote = Gio.DBusError.get_remote_error(error);
            if (remote === 'org.freedesktop.DBus.Error.ServiceUnknown' ||
                remote === 'org.freedesktop.DBus.Error.NameHasNoOwner')
                return;
        } catch (e) {
            // Not a D-Bus error at all — fall through and log it.
        }
        logError(error,
            `intergenos-launch-monitor: InterGen ${method} call failed`);
    }
}
