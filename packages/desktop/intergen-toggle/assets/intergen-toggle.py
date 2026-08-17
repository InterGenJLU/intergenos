#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# intergen-toggle -- single-screen GTK4 + libadwaita app that enables or
# disables the opt-in InterGen AI service. Mirrors the Forge installer's
# packages.py:_build_intergen_row() for post-install reconfiguration.
# Honors D-010 (opt-in; no auto-enable). Decided 2026-05-22
# (theming-arc Walk #24).
#
# UX flow:
#   ON  -> if model present: systemctl --user enable --now intergen.service
#          if model absent : spawn `gnome-terminal -- intergen setup`,
#                            then enable+start on completion (user closes
#                            the terminal window; we re-check on focus
#                            and re-evaluate).
#   OFF -> systemctl --user disable --now intergen.service

import os
import subprocess
import sys
import glob
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib  # noqa: E402


# Header-bar wordmark crop (170x45) shipped by intergen-mark. Decoupled
# from /usr/share/intergenos/intergenos_wordmark_transparent.png (full
# brand asset for GDM/About) so header consumers can pin to widget aspect.
WORDMARK_PATH = '/usr/share/intergenos/intergenos_wordmark_header.png'

# Model presence detection. The system-wide root-owned store
# (/var/lib/intergen/models/llm) is where `intergen setup` actually installs the
# model; the per-user/HF-cache paths are legacy/dev fallbacks. The system path
# was missing, so the toggle reported "not yet downloaded" even with the model
# installed.
MODEL_GLOB_CANDIDATES = [
    '/var/lib/intergen/models/llm/*.gguf',
    '/var/lib/intergen/models/*.gguf',
    os.path.expanduser('~/.local/share/intergen/models/*.gguf'),
    os.path.expanduser('~/.cache/huggingface/hub/models--*Qwen*/**/*.gguf'),
]


def service_status():
    """Return ('enabled'|'disabled'|'unknown', is_active_bool)."""
    try:
        out = subprocess.run(
            ['systemctl', '--user', 'is-enabled', 'intergen.service'],
            capture_output=True, text=True, timeout=5
        )
        enabled = out.stdout.strip()
        if enabled not in ('enabled', 'disabled'):
            enabled = 'unknown'
    except Exception:
        enabled = 'unknown'
    try:
        active_out = subprocess.run(
            ['systemctl', '--user', 'is-active', 'intergen.service'],
            capture_output=True, text=True, timeout=5
        )
        is_active = (active_out.stdout.strip() == 'active')
    except Exception:
        is_active = False
    return enabled, is_active


def model_present():
    """True if any GGUF model file is found in known intergen storage paths."""
    for pattern in MODEL_GLOB_CANDIDATES:
        if glob.glob(pattern, recursive=True):
            return True
    return False


def systemctl_user(*args):
    """Run a systemctl --user command. Returns (rc, stdout, stderr)."""
    try:
        out = subprocess.run(
            ['systemctl', '--user'] + list(args),
            capture_output=True, text=True, timeout=15
        )
        return out.returncode, out.stdout, out.stderr
    except Exception as e:
        return -1, '', str(e)


def spawn_setup_in_terminal():
    """Launch `gnome-terminal -- intergen setup`. Returns control to the
    GUI immediately (subprocess.Popen does not wait)."""
    try:
        subprocess.Popen(
            ['gnome-terminal', '--', 'intergen', 'setup'],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


CSS = b"""
.intergen-toggle-card {
    background: rgba(20, 22, 35, 0.55);
    border-radius: 16px;
    border: 1px solid rgba(0, 153, 255, 0.25);
    padding: 18px;
}
.intergen-toggle-title {
    font-size: 28px;
    font-weight: 200;
    letter-spacing: 0.04em;
}
.intergen-toggle-status-key {
    font-weight: 600;
    color: alpha(@accent_color, 0.9);
}
.intergen-toggle-status-val {
    font-family: 'JetBrains Mono', monospace;
}
.intergen-toggle-disclosure {
    color: alpha(@window_fg_color, 0.7);
    font-size: 13px;
}
"""


class IntergenToggleApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.intergenos.intergen-toggle')

    def do_activate(self):
        # CSS
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        display = self._display()
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        win = Adw.ApplicationWindow(application=self)
        win.set_title('InterGen AI')
        win.set_default_size(640, 520)

        # ------- Header bar with left-aligned wordmark (Walk #21 pattern)
        header = Adw.HeaderBar()
        header.add_css_class('flat')
        if os.path.exists(WORDMARK_PATH):
            wordmark = Gtk.Picture.new_for_filename(WORDMARK_PATH)
            wordmark.set_content_fit(Gtk.ContentFit.CONTAIN)
            wordmark.set_size_request(170, 46)
            header.pack_start(wordmark)

        # ------- Main layout
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        toolbar.set_content(scrolled)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        outer.set_margin_top(24)
        outer.set_margin_bottom(24)
        outer.set_margin_start(28)
        outer.set_margin_end(28)
        scrolled.set_child(outer)

        # ------- Title
        title = Gtk.Label(label='InterGen AI', xalign=0)
        title.add_css_class('intergen-toggle-title')
        outer.append(title)

        # ------- Status section
        self._status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._status_box.add_css_class('intergen-toggle-card')

        self._svc_label = Gtk.Label(xalign=0)
        self._svc_label.set_use_markup(True)
        self._status_box.append(self._svc_label)

        self._model_label = Gtk.Label(xalign=0)
        self._model_label.set_use_markup(True)
        self._status_box.append(self._model_label)

        outer.append(self._status_box)

        # ------- Toggle row (the actual control)
        toggle_group = Adw.PreferencesGroup()
        toggle_row = Adw.SwitchRow()
        toggle_row.set_title('Enable InterGen AI')
        toggle_row.set_subtitle(
            'Starts the local AI assistant service. Setup downloads a ~4–5 GB '
            'language model on first enable.'
        )
        self._switch_row = toggle_row
        toggle_row.connect('notify::active', self._on_toggled)
        toggle_group.add(toggle_row)
        outer.append(toggle_group)

        # ------- Disclosure
        disclosure = Gtk.Label(
            label='InterGen is opt-in by design. The AI runs entirely on '
                  'your machine — no conversation data leaves your computer. '
                  'You can also enable/disable from a terminal: '
                  '`intergen setup` (enable) or '
                  '`systemctl --user disable --now intergen.service` (disable).'
        )
        disclosure.add_css_class('intergen-toggle-disclosure')
        disclosure.set_wrap(True)
        disclosure.set_xalign(0)
        outer.append(disclosure)

        win.set_content(toolbar)
        self._refresh_status()
        win.present()

        # Refresh status when the window regains focus (e.g. user closed
        # the gnome-terminal that ran `intergen setup`).
        win.connect('notify::is-active', lambda w, p:
                    w.is_active() and self._refresh_status())

    def _display(self):
        # Modest compatibility shim: Gtk.StyleContext needs a Gdk.Display.
        from gi.repository import Gdk
        return Gdk.Display.get_default()

    def _refresh_status(self):
        enabled, is_active = service_status()
        if is_active:
            svc_text = '<b>Service:</b> <span foreground="#00CC66">running</span>'
        elif enabled == 'enabled':
            svc_text = '<b>Service:</b> <span foreground="#FFAA00">enabled (not yet started)</span>'
        elif enabled == 'disabled':
            svc_text = '<b>Service:</b> <span foreground="#777777">disabled</span>'
        else:
            svc_text = '<b>Service:</b> <span foreground="#FF5555">unknown</span>'
        self._svc_label.set_markup(svc_text)

        if model_present():
            model_text = '<b>Model:</b> <span foreground="#00CC66">downloaded</span>'
        else:
            model_text = '<b>Model:</b> <span foreground="#777777">not yet downloaded</span>'
        self._model_label.set_markup(model_text)

        # Sync the switch to actual service state without firing the
        # toggle handler.
        self._switch_row.handler_block_by_func(self._on_toggled)
        self._switch_row.set_active(is_active or enabled == 'enabled')
        self._switch_row.handler_unblock_by_func(self._on_toggled)

    def _on_toggled(self, switch_row, _param):
        want_on = switch_row.get_active()
        if want_on:
            if model_present():
                # Model already there -- enable + start.
                rc, _, err = systemctl_user(
                    'enable', '--now', 'intergen.service'
                )
                if rc != 0:
                    self._show_error(f'systemctl enable failed: {err.strip()}')
            else:
                # Model absent -- spawn `gnome-terminal -- intergen setup`.
                # `intergen setup` enables the service itself once the model
                # is downloaded; we just need to surface the terminal.
                if not spawn_setup_in_terminal():
                    self._show_error(
                        'Could not launch gnome-terminal. Open a terminal '
                        'manually and run `intergen setup`.'
                    )
            # Optimistic UI; real state refresh on window focus return.
            GLib.timeout_add_seconds(1, self._refresh_status_once)
        else:
            rc, _, err = systemctl_user(
                'disable', '--now', 'intergen.service'
            )
            if rc != 0:
                self._show_error(f'systemctl disable failed: {err.strip()}')
            GLib.timeout_add_seconds(1, self._refresh_status_once)

    def _refresh_status_once(self):
        self._refresh_status()
        return False  # one-shot timeout

    def _show_error(self, msg):
        dlg = Adw.MessageDialog.new(
            self.get_active_window(),
            'InterGen toggle error',
            msg
        )
        dlg.add_response('ok', 'OK')
        dlg.set_default_response('ok')
        dlg.present()


def main():
    app = IntergenToggleApp()
    return app.run(sys.argv)


if __name__ == '__main__':
    sys.exit(main())
