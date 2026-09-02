#!/usr/bin/env python3
"""Headless render harness for the InterGenOS welcomer pages.

Loads the real intergen-welcome.py, applies its CUSTOM_CSS, builds one page
in a 760x720 Adw window, and screenshots it under Xvfb. Lets us iterate the
welcomer visual design with actual renders instead of guessing.

Usage (under xvfb-run, or under the GTK broadway backend — see README.md):
  xvfb-run -s "-screen 0 1366x768x24" python3 render_page.py <page> <out.png>
  <page> = welcome|appearance|extensions|prompt|shortcuts|intergen|community|done
  IGOS_WELCOMER_SCENARIO=nvidia-offer|nvidia-driver-done renders the intergen
  page as an NVIDIA machine before / after the driver leg (see below).
"""
import os, sys, importlib.util
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk

PAGE = sys.argv[1]
OUT = sys.argv[2]
# The application beside this harness, not a fixed absolute path. The path was
# pinned to one checkout, so running the harness from any other working tree
# silently rendered a DIFFERENT copy of the application than the one being
# worked on — a render proof that proves nothing about the change in hand.
# IGOS_WELCOMER_SRC overrides when a specific copy is wanted.
SRC = os.environ.get(
    'IGOS_WELCOMER_SRC',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                 'intergen-welcome.py'))

spec = importlib.util.spec_from_file_location('welcomer_mod', SRC)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)  # __name__ != '__main__' -> main() guard does not fire

# Stub the GNOME/gsettings-touching helpers so pages build off the build VM/host.
mod.get_enabled_extensions = lambda: set()
mod.apply_theme = lambda *a, **k: None
mod.apply_prompt = lambda *a, **k: None

# A machine state to render the page IN, chosen by IGOS_WELCOMER_SCENARIO.
# The Meet InterGen page reads the installer's graphics record, the package
# database and the model offer; on the render host those describe the render
# host. The scenarios below describe the machines the page was written for:
#   nvidia-offer        NVIDIA card on the open driver, nothing installed yet —
#                       the advisory box with its switches is built.
#   nvidia-driver-done  the driver installed, the engine not, two model sizes
#                       offered — the state after the driver reboot, in which
#                       the page crashed on 2026-09-02.
# Unset: the page reads the render host, as it always did.
SCENARIO = os.environ.get('IGOS_WELCOMER_SCENARIO')
if SCENARIO in ('nvidia-offer', 'nvidia-driver-done'):
    driver_done = SCENARIO == 'nvidia-driver-done'
    mod._gpu_detection_record = lambda *a, **k: {
        'version': mod._GPU_RECORD_VERSION, 'vendor': 'nvidia',
        'pci_vendors': ['10de'], 'shipped_engine': 'vulkan',
        'upgrade_engine': 'cuda', 'upgrade_outranks_shipped': True,
        'gfx_targets': [], 'upgrade_engine_supported': None}
    mod._package_is_installed = lambda name: driver_done and name == 'nvidia'
    mod._intergen_is_set_up = lambda *a, **k: False
    mod._model_offer = (lambda *a, **k: {
        'tiers': [1, 2],
        'download_bytes': {'1': 2300000000, '2': 6100000000}}) if driver_done \
        else (lambda *a, **k: None)
    mod._qwen_attribution = lambda *a, **k: None
    mod._probe_download_sources = lambda *a, **k: type('P', (), {'cause': None})()
elif SCENARIO:
    sys.exit(f'unknown IGOS_WELCOMER_SCENARIO {SCENARIO!r}')

Adw.init()

def _apply_css():
    prov = Gtk.CssProvider()
    try:
        prov.load_from_string(mod.CUSTOM_CSS)
    except (AttributeError, TypeError):
        b = mod.CUSTOM_CSS.encode('utf-8')
        prov.load_from_data(b, len(b))
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id='org.intergenos.welcomerender',
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.connect('activate', self.on_activate)

    def on_activate(self, app):
        _apply_css()
        win = Adw.ApplicationWindow(application=app)
        win.set_default_size(760, 720)
        win.add_css_class('welcome-window')
        builder = getattr(mod, f'build_{PAGE}_page')
        content = builder()
        # Mirror the real app: a header bar + the page body, so spacing/clip
        # reproduces the shipped layout.
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        hb.add_css_class('welcome-header')
        tv.add_top_bar(hb)
        tv.set_content(content)
        win.set_content(tv)
        self.win = win
        win.present()
        GLib.timeout_add(1500, self._shot, app)

    def _shot(self, app):
        # GTK4-native offscreen rasterization: snapshot the realized widget
        # tree to a render node, render it to a texture via the window's
        # renderer (software/cairo under Xvfb), and save. No screen capture.
        win = self.win
        w = win.get_width() or 760
        h = win.get_height() or 720
        paintable = Gtk.WidgetPaintable.new(win)
        snap = Gtk.Snapshot.new()
        paintable.snapshot(snap, w, h)
        node = snap.to_node()
        if node is None:
            print('RENDER: no node (widget not drawn)', flush=True)
            app.quit(); return False
        renderer = win.get_renderer()
        texture = renderer.render_texture(node, None)
        texture.save_to_png(OUT)
        print(f'RENDER ok {w}x{h}', flush=True)
        app.quit()
        return False

App().run([])
