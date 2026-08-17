# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen Panel — GTK4 WebKitGTK desktop window.

Two modes:

* BASIC (default) — a normal DECORATED GTK4 window with the native titlebar
  (minimize / maximize / close), holding the WebKit 6.0 WebView that renders the
  InterGen web UI at http://localhost:8089. This is the shippable, usable window:
  no frameless chrome, no drag/snap/dock, no D-Bus positioning. It is what the
  user interacts with on the installed OS.

* DOCK (--dock) — the frameless GTK4 window with magnetic edge snapping,
  blur-behind transparency, and the D-Bus interface for show/hide/toggle/dock
  control from the GNOME Shell extension. This is the in-progress a2-prime dock;
  its positioning/animation feel is iterated directly on real hardware (it cannot
  be judged through a nested compositor), so it is NOT the default yet.

Mode is selected by `intergen-panel --dock` (or `--basic`, the default), or by
the `panel.mode` key in ~/.config/intergen/panel.json ("basic" | "dock"). The CLI
flag wins over the config key.

Preceding-project pattern: D-Bus bridge for Shell extension lifecycle control.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
from pathlib import Path
from typing import Any

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")

from gi.repository import Gtk, WebKit, Gdk, Gio, GLib  # noqa: E402

logger = logging.getLogger(__name__)

APP_ID = "org.intergenos.InterGenPanel"
# DEFAULT_WIDTH must clear the web UI's compact-header CSS breakpoints so the
# full header renders on launch instead of a stripped "thin" bar that looks
# broken (G3-19). web/style.css collapses at @media (max-width:480px) — hides
# the wordmark + HUD stat chips — and at (max-width:640px) — hides the sidebar
# + shrinks the HUD. 720 (GTK4 logical px, so HiDPI-safe) clears both, so the
# wordmark, HUD, and sidebar are all present on first paint. MIN_WIDTH stays
# 320: the responsive CSS still degrades gracefully when the user shrinks it.
DEFAULT_WIDTH = 720
DEFAULT_HEIGHT = 680
MIN_WIDTH = 320
MIN_HEIGHT = 400
SNAP_ZONE = 20  # pixels from edge to trigger snap
SNAP_RESISTANCE = 8  # pixels of pull before unsnap
BLUR_RADIUS = 20
DBUS_IFACE_XML = """
<node>
  <interface name="org.intergenos.InterGenPanel">
    <method name="Show"/>
    <method name="Hide"/>
    <method name="Toggle"/>
    <method name="GetVisible">
      <arg type="b" direction="out" name="visible"/>
    </method>
    <method name="DockLeft"/>
    <method name="DockRight"/>
    <method name="Float"/>
    <method name="Quit"/>
  </interface>
</node>
"""


class PanelWindow:
    """Frameless GTK4 window with WebKitGTK WebView."""

    def __init__(self, mode: str = "basic") -> None:
        self._mode = mode  # "basic" (decorated, default) or "dock" (frameless)
        # Single-instance (FLAGS_NONE, not NON_UNIQUE): a second `intergen-panel`
        # invocation activates the primary and re-presents the EXISTING window
        # (_on_activate's `if self._window: present()` path) instead of stacking
        # a new one — the per-login UI layering the operator observed.
        self._app = Gtk.Application(application_id=APP_ID,
                                     flags=Gio.ApplicationFlags.FLAGS_NONE)
        self._app.connect("activate", self._on_activate)
        self._window: Gtk.Window | None = None
        self._webview: WebKit.WebView | None = None
        self._docked: str | None = None  # "left", "right", or None (floating)
        self._dragging = False
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._monitor_geometry: tuple[int, int, int, int] = (0, 0, 1920, 1080)
        self._prefs_path = Path.home() / ".config" / "intergen" / "panel.json"

    # -- Gtk.Application callbacks -------------------------------------------

    def _on_activate(self, app: Gtk.Application) -> None:
        if self._window:
            self._window.present()
            return
        if self._mode == "dock":
            self._build_window()      # frameless dock window
            self._export_dbus()       # D-Bus positioning interface (dock only)
        else:
            self._build_basic_window()  # decorated, native min/max/close
        self._window.present()

    # -- Basic mode (default): decorated window, native titlebar ------------

    def _build_basic_window(self) -> None:
        """A plain decorated GTK4 window: native minimize / maximize / close,
        holding the same WebKit 6.0 view. No frameless chrome, drag, snap, dock,
        or D-Bus positioning — the shippable window the user actually interacts
        with. Closing it quits the app (unlike dock mode, which hides-on-close)."""
        win = Gtk.ApplicationWindow(application=self._app)
        win.set_title("InterGen")
        win.set_decorated(True)                       # native titlebar + buttons
        win.set_default_size(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        win.set_size_request(MIN_WIDTH, MIN_HEIGHT)
        win.set_resizable(True)                       # supports maximize

        # Brand the titlebar: a HeaderBar carrying the InterGen mark at the
        # top-left corner (consistent with the mark's placement across the rest
        # of the UI), with the native window controls + centered "InterGen"
        # title. Falls back to the plain native titlebar if the mark can't load.
        try:
            header = Gtk.HeaderBar()
            mark = Gtk.Image.new_from_icon_name("org.intergenos.InterGenPanel")
            mark.set_pixel_size(22)
            mark.set_margin_start(8)
            mark.set_margin_end(2)
            header.pack_start(mark)
            win.set_titlebar(header)
        except Exception as e:  # noqa: BLE001 — titlebar branding is non-essential
            logger.debug("HeaderBar branding skipped: %s", e)

        self._webview = self._make_webview()
        self._load_web_ui()                           # shared loader (token/UI)

        win.set_child(self._webview)
        self._window = win

    def _build_window(self) -> None:
        win = Gtk.ApplicationWindow(application=self._app)
        win.set_title("InterGen")
        win.set_decorated(False)
        win.set_default_size(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        win.set_size_request(MIN_WIDTH, MIN_HEIGHT)
        win.set_resizable(True)

        # Load preferences
        prefs = self._load_prefs()

        # Window styling
        win.set_opacity(prefs.get("opacity", 0.96))

        # CSS provider
        css_provider = Gtk.CssProvider()
        css_provider.load_from_string(
            f"window {{ "
            f"  border-radius: 14px; "
            f"  border: 1px solid rgba(0,153,255,0.12); "
            f"  box-shadow: 0 2px 12px rgba(0,0,0,0.3), "
            f"              0 0 18px rgba(0,153,255,0.08); "
            f"}} "
            f"window.docked-left {{ border-radius: 0 14px 14px 0; }} "
            f"window.docked-right {{ border-radius: 14px 0 0 14px; }} "
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # WebView
        self._webview = self._make_webview()
        self._load_web_ui()

        # Drag handle: top 32px zone
        drag_ctrl = Gtk.EventControllerMotion.new()
        drag_ctrl.connect("motion", self._on_drag_motion)
        win.add_controller(drag_ctrl)

        click_ctrl = Gtk.GestureClick.new()
        click_ctrl.connect("pressed", self._on_click_press)
        click_ctrl.connect("released", self._on_click_release)
        win.add_controller(click_ctrl)

        # Keyboard: Escape hides
        key_ctrl = Gtk.EventControllerKey.new()
        key_ctrl.connect("key-pressed", self._on_key)
        win.add_controller(key_ctrl)

        # Window events
        win.connect("close-request", self._on_close_request)

        # Set monitor geometry
        display = Gdk.Display.get_default()
        if display:
            monitors = display.get_monitors()
            if monitors:
                monitors_list = list(monitors)
                if monitors_list:
                    mon = monitors_list[0]
                    geom = mon.get_geometry()
                    self._monitor_geometry = (geom.x, geom.y, geom.width, geom.height)

        win.set_child(self._webview)
        self._window = win

    # -- Drag handling -------------------------------------------------------

    def _on_click_press(self, gesture: Gtk.GestureClick, n_press: int,
                         x: float, y: float) -> None:
        if y < 32:  # Drag handle zone
            self._dragging = True
            self._drag_start_x = x
            self._drag_start_y = y

    def _on_click_release(self, gesture: Gtk.GestureClick, n_press: int,
                           x: float, y: float) -> None:
        if self._dragging and self._window:
            self._dragging = False
            self._apply_snap()

    def _on_drag_motion(self, ctrl: Gtk.EventControllerMotion,
                         x: float, y: float) -> None:
        if not self._dragging or not self._window:
            return
        delta_x = x - self._drag_start_x
        delta_y = y - self._drag_start_y
        win_x, win_y = self._window.get_position()
        self._window.set_position(int(win_x + delta_x), int(win_y + delta_y))

    # -- Magnetic snap -------------------------------------------------------

    def _apply_snap(self) -> None:
        if not self._window:
            return
        mx, my, mw, mh = self._monitor_geometry
        wx, wy = self._window.get_position()
        ww = self._window.get_width()
        wh = self._window.get_height()

        left_dist = abs(wx - mx)
        right_dist = abs((mx + mw) - (wx + ww))
        top_dist = abs(wy - my)
        bottom_dist = abs((my + mh) - (wy + wh))

        snap_threshold = SNAP_ZONE
        # Snap to closest edge if within threshold
        if left_dist < snap_threshold and left_dist <= right_dist:
            self._set_docked("left")
            self._window.set_position(mx, my)
            self._window.set_size_request(DEFAULT_WIDTH, mh)
        elif right_dist < snap_threshold:
            self._set_docked("right")
            self._window.set_position(mx + mw - ww, my)
            self._window.set_size_request(DEFAULT_WIDTH, mh)
        else:
            self._set_docked(None)

    def _set_docked(self, edge: str | None) -> None:
        self._docked = edge
        if self._window:
            if edge == "left":
                self._window.add_css_class("docked-left")
            elif edge == "right":
                self._window.add_css_class("docked-right")
            else:
                self._window.remove_css_class("docked-left")
                self._window.remove_css_class("docked-right")

    # -- D-Bus interface ----------------------------------------------------

    def _export_dbus(self) -> None:
        # Gio.DBus.session is a GJS idiom that does not exist in PyGObject —
        # this method raised AttributeError on every dock-mode activate. Get
        # the session connection explicitly; register via the "2" entry point
        # (the closures variant it replaces is deprecated in GLib).
        node_info = Gio.DBusNodeInfo.new_for_xml(DBUS_IFACE_XML)
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._dbus_conn = bus
        self._dbus_id = bus.register_object_with_closures2(
            "/org/intergenos/InterGenPanel",
            node_info.interfaces[0],
            self._on_dbus_call,
            None, None,
        )
        self._bus_name_id = Gio.bus_own_name_on_connection(
            bus,
            "org.intergenos.InterGenPanel",
            Gio.BusNameOwnerFlags.NONE,
            None, None,
        )
        logger.info("Panel D-Bus interface exported: %s", APP_ID)

    def _on_dbus_call(self, connection, sender, object_path,
                       interface_name, method_name, parameters,
                       invocation) -> None:
        try:
            if method_name == "Show":
                GLib.idle_add(self._show)
                invocation.return_value(None)
            elif method_name == "Hide":
                GLib.idle_add(self._hide)
                invocation.return_value(None)
            elif method_name == "Toggle":
                GLib.idle_add(self._toggle)
                invocation.return_value(None)
            elif method_name == "GetVisible":
                visible = self._window and self._window.is_visible()
                invocation.return_value(GLib.Variant("(b)", [visible]))
            elif method_name == "DockLeft":
                GLib.idle_add(self._set_docked, "left")
                invocation.return_value(None)
            elif method_name == "DockRight":
                GLib.idle_add(self._set_docked, "right")
                invocation.return_value(None)
            elif method_name == "Float":
                GLib.idle_add(self._set_docked, None)
                invocation.return_value(None)
            elif method_name == "Quit":
                GLib.idle_add(self._app.quit)
                invocation.return_value(None)
        except Exception as e:
            invocation.return_dbus_error(
                "org.intergenos.InterGenPanel.Error",
                f"{method_name} failed: {e}",
            )

    def _show(self) -> None:
        if self._window:
            self._window.present()

    def _hide(self) -> None:
        if self._window:
            self._window.hide()

    def _toggle(self) -> None:
        if self._window:
            if self._window.is_visible():
                self._window.hide()
            else:
                self._window.present()

    # -- Keyboard -----------------------------------------------------------

    def _on_key(self, ctrl, keyval, keycode, state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self._hide()
            return True
        return False

    # -- Lifecycle ----------------------------------------------------------

    def _on_close_request(self, window) -> bool:
        self._hide()
        return True  # Prevent actual close — just hide

    # -- Helpers ------------------------------------------------------------

    def _make_webview(self) -> WebKit.WebView:
        """Build the WebView, injecting the auth token before any page script runs.

        The web UI's WebSocket (app.js) authenticates with the on-disk web token,
        which it reads from ``window.__INTERGEN_TOKEN__``. The server injects that
        global into index.html, but only when the document request carries an
        Authorization header — and WebKitGTK does NOT propagate the main-document
        Authorization header onto the subsequent WebSocket upgrade. With the global
        absent, app.js falls back to a random client token that can never match the
        server, so the panel's WebSocket is rejected with 401 (the bug (d) masked
        on the dev box by a loopback-trust relaxation we are deliberately NOT
        shipping).

        The durable handoff: a document-start user script sets
        ``window.__INTERGEN_TOKEN__`` from the trusted local token file before
        app.js runs — independent of HTTP header propagation, no server-side
        loopback trust required.
        """
        ucm = WebKit.UserContentManager()
        token = self._load_token()
        if token:
            script = WebKit.UserScript.new(
                f"window.__INTERGEN_TOKEN__ = {json.dumps(token)};",
                WebKit.UserContentInjectedFrames.TOP_FRAME,
                WebKit.UserScriptInjectionTime.START,
                None,
                None,
            )
            ucm.add_script(script)
        webview = WebKit.WebView(user_content_manager=ucm)
        webview.set_vexpand(True)
        webview.set_hexpand(True)
        webview.connect("load-failed", self._on_load_failed)
        return webview

    def _on_load_failed(self, webview, load_event, failing_uri, error) -> bool:
        """Handle a failed page load gracefully.

        The daemon's web server may not be listening yet — e.g. the panel was
        opened in the first seconds after login, before intergen.service bound to
        :8089 (the white "Could not connect to localhost" page the operator hit).
        Instead of WebKit's raw error page, show a friendly "starting" placeholder
        and retry the load a few times (the service comes up within seconds).
        Returns True to suppress WebKit's default error page.
        """
        if "localhost:8089" not in (failing_uri or ""):
            return False  # not our startup race — let WebKit show the default
        self._load_retries = getattr(self, "_load_retries", 0) + 1
        if self._load_retries <= 12:
            webview.load_html(
                "<html><body style='background:#050810;color:#e2e8f0;"
                "font-family:Inter,sans-serif;padding:40px;text-align:center'>"
                "<h2 style='color:#0099FF'>InterGen</h2>"
                "<p>Starting up…</p></body></html>",
                "about:blank",
            )
            GLib.timeout_add(800, self._retry_load_web_ui)
        else:
            webview.load_html(
                "<html><body style='background:#050810;color:#e2e8f0;"
                "font-family:Inter,sans-serif;padding:40px;text-align:center'>"
                "<h2 style='color:#0099FF'>InterGen</h2>"
                "<p>Couldn't reach the InterGen service.</p>"
                "<p style='color:#7a8ba8'>Check that it is running:<br>"
                "<code>systemctl --user status intergen</code></p></body></html>",
                "about:blank",
            )
        return True

    def _retry_load_web_ui(self) -> bool:
        self._do_load_web_ui()
        return False  # one-shot timeout

    def _load_web_ui(self) -> None:
        """Public entry: (re)load the UI, resetting the startup-retry budget."""
        self._load_retries = 0
        self._do_load_web_ui()

    def _do_load_web_ui(self) -> None:
        """Point the WebView at the InterGen web UI (with the auth token), or show
        the setup-fallback HTML if no token exists. Shared by both window modes."""
        token = self._load_token()
        if token is None:
            self._webview.load_html(
                "<html><body style='background:#050810;color:#e2e8f0;"
                "font-family:Inter,sans-serif;padding:40px;text-align:center'>"
                "<h2 style='color:#0099FF'>InterGen Panel</h2>"
                "<p>No auth token found. Run <code>intergen setup</code> first.</p>"
                "</body></html>",
                "about:blank",
            )
        else:
            req = WebKit.URIRequest.new("http://localhost:8089/")
            req.get_http_headers().append("Authorization",
                                          f"Bearer {token}")
            self._webview.load_request(req)

    def _load_token(self) -> str | None:
        """Read the web auth token. Returns None if unavailable."""
        token_path = Path.home() / ".config" / "intergen" / "web-token"
        try:
            if token_path.exists():
                token = token_path.read_text().strip()
                if token:
                    return token
        except OSError:
            pass
        return None

    def _load_prefs(self) -> dict[str, Any]:
        try:
            if self._prefs_path.exists():
                return json.loads(self._prefs_path.read_text())
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def run(self) -> None:
        # We parse our own flags (--basic/--dock) in _resolve_mode; don't hand
        # them to GApplication, which would reject them as unknown options.
        self._app.run([sys.argv[0]])


def _resolve_mode(argv: list[str] | None = None) -> str:
    """Resolve the window mode. CLI flag (--basic/--dock) wins over the
    panel.json `panel.mode` key; default is basic. Unknown config values fall
    back to basic rather than erroring (a usable window beats a hard failure)."""
    parser = argparse.ArgumentParser(prog="intergen-panel",
                                     description="InterGen panel window")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--basic", dest="mode", action="store_const", const="basic",
                       help="decorated window with native min/max/close (default)")
    group.add_argument("--dock", dest="mode", action="store_const", const="dock",
                       help="frameless magnetic-dock window (in development)")
    args, _ = parser.parse_known_args(argv)
    if args.mode:
        return args.mode
    # Fall back to the config key, then to basic.
    try:
        prefs_path = Path.home() / ".config" / "intergen" / "panel.json"
        if prefs_path.exists():
            mode = json.loads(prefs_path.read_text()).get("mode")
            if mode in ("basic", "dock"):
                return mode
    except (OSError, json.JSONDecodeError):
        pass
    return "basic"


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    panel = PanelWindow(mode=_resolve_mode())
    panel.run()


if __name__ == "__main__":
    main()