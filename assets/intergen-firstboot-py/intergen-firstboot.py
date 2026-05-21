#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenOS
"""
intergen-firstboot — Python/GTK4 port of the C/DRM first-boot animation.

Renders the InterGenOS ECG heartbeat pulse animation as a fullscreen
GTK4/Wayland window. The math is ported verbatim from the canonical C
source at:
    assets/intergen-firstboot/pulse.c   (waveform + state)
    assets/intergen-firstboot/text.c    (pass-keyed text sequence)
    assets/intergen-firstboot-drm/firstboot-drm.c (render loop reference)

Per the operator-ratified flow ([[project_first_login_animation_flow]]):
    1. Forge runs
    2. System installs
    3. System reboots
    4. User logs in for the first time (GDM)
    5. This animation fires (XDG autostart, Initialization phase),
       fades to black at the end of pass 6
    6. Welcomer becomes visible with the desktop already there

Visual properties are LOCKED per operator-direct 2026-05-20T~19:Z (Q5
of the chain-vs-phase walkthrough). Only the rendering mechanism may
change in this rewrite — DRM/KMS pre-compositor -> GTK4/Wayland post-
login in-session. Sweep count, timing, ECG curve shape, text content,
font choice, and fade-easing curves are non-negotiable.
"""

import math
import os
import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import GLib, Gdk, Gtk, Pango, PangoCairo  # noqa: E402

# Gtk4LayerShell is optional at import-time. When available the animation
# renders as a wlr-layer-shell OVERLAY surface anchored to all 4 screen
# edges -- this is the canonical Wayland mechanism for session chrome
# (the same protocol gnome-shell uses for its own lock/login screen).
# When unavailable, the animation falls back to a normal Gtk window with
# fullscreen() request; in that path Mutter renders it as a normal
# application window which is constrained-size + competes with the
# gnome-shell activities overview chrome. The layer-shell path is the
# canonical answer to the operator-ratified post-login flow's "take over
# the screen" semantics.
try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell  # noqa: E402
    HAS_LAYER_SHELL = True
except (ValueError, ImportError):
    HAS_LAYER_SHELL = False
    Gtk4LayerShell = None

import cairo  # noqa: E402


# -------------------------------------------------------------------
# Brand constants — ported verbatim from assets/intergen-firstboot/pulse.h
# These are LOCKED design properties. Do not modify without operator-
# explicit greenlight to reopen Q5 of the chain-vs-phase walkthrough.
# -------------------------------------------------------------------

# Peak positions (normalized screen width)        ← pulse.h:24
PULSE_PEAK1_POS = 0.28
PULSE_PEAK2_POS = 0.78
PULSE_COMPLEX_WIDTH = 0.16

# Rhythm                                          ← pulse.h:29
PULSE_BEAT_INTERVAL = 0.50
PULSE_BEAT_PERIOD = 1.6031     # ~37 BPM (5% faster)
PULSE_SWEEP_SPEED = PULSE_BEAT_INTERVAL / PULSE_BEAT_PERIOD
PULSE_CYCLE_TIME = 1.0 / PULSE_SWEEP_SPEED

# Vertical positioning                            ← pulse.h:35
PULSE_Y_CENTER = 0.52
PULSE_Y_AMPLITUDE = 0.14

# Edge fading                                     ← pulse.h:39
PULSE_EDGE_FADE = 0.20

# Trailing darkness wave                          ← pulse.h:42
PULSE_TRAIL_VISIBLE = 0.25
PULSE_TRAIL_FADE_W = 0.30
PULSE_TRAIL_TRIGGER = PULSE_PEAK2_POS - PULSE_COMPLEX_WIDTH

# Brand palette                                   ← pulse.h:46
PULSE_LINE_RGB = (0.00, 0.60, 1.00)
PULSE_GLOW_RGB = (0.00, 0.55, 0.95)
PULSE_TEXT_RGB = (0.90, 0.92, 0.94)
PULSE_INNER_HIGHLIGHT_RGB = (0.85, 0.97, 1.00)   # ← firstboot-drm.c:223

# Rendering parameters                            ← pulse.h:60
PULSE_LINE_WIDTH = 2.5
PULSE_GLOW_LAYERS = 6
PULSE_GLOW_MAX_W = 28.0
PULSE_GLOW_MAX_A = 0.08
PULSE_BEACON_RADIUS = 4.0

# Text geometry                                   ← text.h:18
TEXT_Y_POS = 0.42
TEXT_FONT_SIZE_1080 = 72
TEXT_FONT_FAMILY = "Inter Bold, DejaVu Sans Bold, Noto Sans Bold, Sans Bold"

# Text-state fade thresholds                      ← text.c:23
FADE_IN_END = 0.95
FADE_OUT_END = 0.63

# Run-loop parameters                             ← firstboot-drm.c:49
TOTAL_PASSES = 7
FINAL_FADE_LEAD_SEC = 1.5
GLOW_RADIUS_1080 = 14


# -------------------------------------------------------------------
# Math — ported verbatim from pulse.c + text.c
# -------------------------------------------------------------------

def smoothstep(edge0, edge1, x):
    if edge1 == edge0:
        return 1.0 if x >= edge1 else 0.0
    t = (x - edge0) / (edge1 - edge0)
    if t < 0.0:
        t = 0.0
    if t > 1.0:
        t = 1.0
    return t * t * (3.0 - 2.0 * t)


def ecg_complex(t):
    """Five-component ECG complex at normalized t in [0, 1]. ← pulse.c:23"""
    y = 0.0
    y += 0.12 * math.exp(-((t - 0.15) / 0.035) ** 2)   # P wave
    y -= 0.10 * math.exp(-((t - 0.27) / 0.012) ** 2)   # Q dip
    y += 1.00 * math.exp(-((t - 0.30) / 0.014) ** 2)   # R peak
    y -= 0.18 * math.exp(-((t - 0.34) / 0.016) ** 2)   # S dip
    y += 0.22 * math.exp(-((t - 0.52) / 0.045) ** 2)   # T wave
    return y


def waveform_y(norm_x):
    """Two ECG complexes summed at PEAK1 + PEAK2 positions. ← pulse.c:34"""
    y = 0.0
    hw = PULSE_COMPLEX_WIDTH / 2.0

    t1 = (norm_x - (PULSE_PEAK1_POS - hw)) / PULSE_COMPLEX_WIDTH
    if 0.0 <= t1 <= 1.0:
        y += ecg_complex(t1)

    t2 = (norm_x - (PULSE_PEAK2_POS - hw)) / PULSE_COMPLEX_WIDTH
    if 0.0 <= t2 <= 1.0:
        y += ecg_complex(t2)

    return y


def edge_alpha(norm_x, sweep_progress):
    """Leading fade-in + trailing dark-wave + right-edge fade. ← pulse.c:50"""
    fade_in = smoothstep(0.0, PULSE_EDGE_FADE, norm_x)

    if sweep_progress > PULSE_TRAIL_TRIGGER:
        wave = (sweep_progress - PULSE_TRAIL_TRIGGER) / (1.0 - PULSE_TRAIL_TRIGGER)
        dark_pos = wave * (sweep_progress - PULSE_TRAIL_VISIBLE)
        trail = smoothstep(dark_pos, dark_pos + PULSE_TRAIL_FADE_W, norm_x)
        fade_in *= trail

    fade_out = smoothstep(1.0, 1.0 - PULSE_EDGE_FADE * 0.5, norm_x)
    return fade_in * fade_out


# -------------------------------------------------------------------
# Animation state — ported from pulse.c PulseState
# -------------------------------------------------------------------

class PulseState:
    """← pulse.h:70 PulseState struct + pulse.c:76 init/tick."""

    __slots__ = ("time", "pass_num", "sweep_progress", "global_alpha",
                 "total_passes", "finished")

    def __init__(self, total_passes):
        self.time = 0.0
        self.pass_num = 0
        self.sweep_progress = 0.0
        self.global_alpha = 1.0
        self.total_passes = total_passes
        self.finished = False

    def tick(self, dt):
        """← pulse.c:86 pulse_tick."""
        self.time += dt
        self.pass_num = int(self.time / PULSE_CYCLE_TIME)
        time_in_pass = self.time - self.pass_num * PULSE_CYCLE_TIME
        self.sweep_progress = time_in_pass * PULSE_SWEEP_SPEED

        self.global_alpha = 1.0
        if self.total_passes > 0:
            if self.pass_num >= self.total_passes:
                self.finished = True
                self.global_alpha = 0.0
                return
            if self.pass_num == self.total_passes - 1:
                peak2_done = PULSE_PEAK2_POS + PULSE_COMPLEX_WIDTH / 2.0
                sp = min(self.sweep_progress, 1.0)
                self.global_alpha = 1.0 - smoothstep(0.0, peak2_done, sp)


def text_state(ps):
    """Return (text, alpha) for the current pass. ← text.c:26 text_get_state."""
    time_in_pass = ps.time - ps.pass_num * PULSE_CYCLE_TIME
    frac = time_in_pass / PULSE_CYCLE_TIME
    if frac < 0.0:
        frac = 0.0
    if frac > 1.0:
        frac = 1.0

    text = None
    alpha = 0.0
    pn = ps.pass_num

    if pn == 0:
        pass
    elif pn == 1:
        text = "Hello."
        alpha = smoothstep(0.05, FADE_IN_END, frac)
    elif pn == 2:
        text = "Hello."
        alpha = 1.0 - smoothstep(0.0, FADE_OUT_END, frac)
    elif pn == 3:
        text = "Welcome to InterGenOS."
        alpha = smoothstep(0.05, FADE_IN_END, frac)
    elif pn == 4:
        text = "Welcome to InterGenOS."
        alpha = 1.0 - smoothstep(0.0, FADE_OUT_END, frac)
    elif pn == 5:
        text = "Shall we get started?"
        alpha = smoothstep(0.05, FADE_IN_END, frac)
    elif pn == 6:
        text = "Shall we get started?"
        alpha = 1.0

    alpha *= ps.global_alpha
    return text, alpha


# -------------------------------------------------------------------
# Cairo rendering
# -------------------------------------------------------------------

def _build_pulse_path(cr, ps, w, h):
    """Append the pulse curve to the cairo path with substep interpolation
    where slope > 1.5px. Returns the list of (x, y, ea) sample tuples used,
    needed downstream for the beacon position lookup. ← firstboot-drm.c:130."""
    y_center = h * PULSE_Y_CENTER
    y_amp = h * PULSE_Y_AMPLITUDE

    draw_end = int(w * ps.sweep_progress)
    if draw_end < 1:
        return []
    if draw_end > w:
        draw_end = w

    # Pre-sample y per integer column.
    ys = [0.0] * (draw_end + 1)
    eas = [0.0] * (draw_end + 1)
    for x in range(draw_end + 1):
        norm_x = x / w
        ys[x] = y_center - waveform_y(norm_x) * y_amp
        eas[x] = edge_alpha(norm_x, ps.sweep_progress) * ps.global_alpha

    samples = []
    started = False
    for x in range(draw_end):
        ea = eas[x]
        if ea < 0.003:
            samples.append((float(x), ys[x], ea))
            continue

        if not started:
            cr.move_to(float(x), ys[x])
            started = True
        else:
            cr.line_to(float(x), ys[x])
        samples.append((float(x), ys[x], ea))

        slope = abs(ys[x + 1] - ys[x]) if x + 1 <= draw_end else 0.0
        if slope > 1.5:
            substeps = int(slope * 1.5)
            if substeps < 2:
                substeps = 2
            if substeps > 80:
                substeps = 80
            for s in range(1, substeps + 1):
                frac = s / substeps
                sub_x = float(x) + frac
                sub_norm = sub_x / w
                sub_py = y_center - waveform_y(sub_norm) * y_amp
                cr.line_to(sub_x, sub_py)

    return samples


def _alpha_mask_pattern(samples, w, ps):
    """Build a horizontal linear-gradient mask encoding per-column edge_alpha
    so multi-pass strokes can be masked uniformly. Sample 64 stops across the
    visible range — sufficient resolution for visual smoothness."""
    pat = cairo.LinearGradient(0.0, 0.0, float(w), 0.0)
    draw_end = int(w * ps.sweep_progress)
    if draw_end < 1:
        return pat

    STOPS = 64
    for i in range(STOPS + 1):
        frac = i / STOPS
        x = int(frac * draw_end)
        if x >= len(samples):
            x = len(samples) - 1
        if x < 0:
            x = 0
        ea = samples[x][2] if samples else 0.0
        pat.add_color_stop_rgba(frac * draw_end / w, 1.0, 1.0, 1.0, ea)
    # Past the visible end, mask to zero.
    if draw_end < w:
        pat.add_color_stop_rgba(draw_end / w, 1.0, 1.0, 1.0, 0.0)
        pat.add_color_stop_rgba(1.0, 1.0, 1.0, 1.0, 0.0)
    return pat


def _render_pulse(cr, ps, w, h):
    """Multi-pass stroked pulse curve with horizontal-gradient alpha mask.
    Reproduces the visual signature of firstboot-drm.c:130 render_pulse with
    a path-stroke approach that GSK can hardware-accelerate."""
    cr.save()
    cr.push_group()

    cr.new_path()
    samples = _build_pulse_path(cr, ps, w, h)
    if not samples:
        cr.pop_group()
        cr.restore()
        return

    glow_r = max(4, GLOW_RADIUS_1080 * h // 1080)

    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)

    # Outer glow: PULSE_GLOW_LAYERS strokes, widest+lowest-alpha first.
    gr, gg, gb = PULSE_GLOW_RGB
    for i in range(PULSE_GLOW_LAYERS):
        # Reverse so widest+lowest-alpha is the OUTER layer drawn FIRST.
        layer = PULSE_GLOW_LAYERS - i
        width = PULSE_GLOW_MAX_W * layer / PULSE_GLOW_LAYERS
        # Per-layer alpha mimics the Gaussian falloff in stamp_line_point.
        alpha = PULSE_GLOW_MAX_A * math.exp(-((layer - 1) / float(PULSE_GLOW_LAYERS)) ** 2 * 2.0)
        cr.set_source_rgba(gr, gg, gb, alpha)
        cr.set_line_width(width * h / 1080.0)
        cr.stroke_preserve()

    # Core line on top, full alpha.
    lr, lg, lb = PULSE_LINE_RGB
    cr.set_source_rgba(lr, lg, lb, 1.0)
    cr.set_line_width(PULSE_LINE_WIDTH * h / 1080.0)
    cr.stroke()

    pattern_surface = cr.pop_group()

    # Mask with per-column edge_alpha.
    cr.set_source(pattern_surface)
    cr.mask(_alpha_mask_pattern(samples, w, ps))

    cr.restore()
    return samples


def _render_beacon(cr, ps, w, h):
    """Radial beacon at the leading tip. ← firstboot-drm.c:187."""
    draw_end = int(w * ps.sweep_progress)
    if draw_end < 5:
        return

    norm_x = (draw_end - 1) / w
    wy = waveform_y(norm_x)
    py = h * PULSE_Y_CENTER - wy * h * PULSE_Y_AMPLITUDE
    ea = edge_alpha(norm_x, ps.sweep_progress) * ps.global_alpha
    if ea < 0.01:
        return

    cx = float(draw_end - 1)
    cy = py
    max_r = PULSE_BEACON_RADIUS * 7.0 * h / 1080.0
    base_r = PULSE_BEACON_RADIUS * h / 1080.0
    if max_r < 4:
        max_r = 4
    if base_r < 1:
        base_r = 1

    cr.save()

    gr, gg, gb = PULSE_GLOW_RGB
    lr, lg, lb = PULSE_LINE_RGB
    ir, ig, ib = PULSE_INNER_HIGHLIGHT_RGB

    pat = cairo.RadialGradient(cx, cy, 0.0, cx, cy, max_r)
    # Inner highlight: sharp peak at center, narrow falloff (0.3 of base_r).
    pat.add_color_stop_rgba(0.0, ir, ig, ib, 0.9 * ea)
    pat.add_color_stop_rgba(base_r * 0.4 / max_r, ir, ig, ib, 0.25 * ea)
    # Core: full pulse-line color, medium falloff (1.5 of base_r).
    pat.add_color_stop_rgba(base_r / max_r, lr, lg, lb, 0.55 * ea)
    # Outer glow: pulse-glow color, broad falloff (3.5 of base_r).
    pat.add_color_stop_rgba(base_r * 3.0 / max_r, gr, gg, gb, 0.10 * ea)
    pat.add_color_stop_rgba(1.0, gr, gg, gb, 0.0)

    cr.set_source(pat)
    cr.arc(cx, cy, max_r, 0.0, 2.0 * math.pi)
    cr.fill()
    cr.restore()


def _render_baseline(cr, w, h, alpha):
    """Faint horizontal glow at the pulse Y line. ← firstboot-drm.c:228."""
    if alpha < 0.01:
        return
    y = h * PULSE_Y_CENTER
    gr, gg, gb = PULSE_GLOW_RGB
    lr, lg, lb = PULSE_LINE_RGB

    cr.save()
    # Vertical Gaussian falloff approximated by a vertical linear gradient
    # band: pivot at y, alpha drops toward y±4px.
    band_h = 8.0 * h / 1080.0
    if band_h < 4:
        band_h = 4
    grad = cairo.LinearGradient(0.0, y - band_h, 0.0, y + band_h)
    grad.add_color_stop_rgba(0.0, gr, gg, gb, 0.0)
    grad.add_color_stop_rgba(0.5, gr, gg, gb, 0.025 * alpha)
    grad.add_color_stop_rgba(1.0, gr, gg, gb, 0.0)
    cr.set_source(grad)
    cr.rectangle(0.0, y - band_h, float(w), 2.0 * band_h)
    cr.fill()

    cr.set_source_rgba(lr, lg, lb, 0.04 * alpha)
    cr.set_line_width(1.0)
    cr.move_to(0.0, y)
    cr.line_to(float(w), y)
    cr.stroke()
    cr.restore()


def _render_text(cr, ts_text, ts_alpha, w, h, font_size):
    """Centered text via PangoCairo. Pango handles HarfBuzz shaping +
    subpixel anti-aliasing automatically — the same engine GTK/Firefox use."""
    if not ts_text or ts_alpha < 0.005:
        return

    cr.save()
    layout = PangoCairo.create_layout(cr)
    font_desc = Pango.FontDescription(f"{TEXT_FONT_FAMILY} {font_size}px")
    layout.set_font_description(font_desc)
    layout.set_text(ts_text, -1)

    text_w, text_h = layout.get_pixel_size()
    pen_x = (w - text_w) / 2
    pen_y = h * TEXT_Y_POS - text_h / 2

    tr, tg, tb = PULSE_TEXT_RGB
    cr.set_source_rgba(tr, tg, tb, ts_alpha)
    cr.move_to(pen_x, pen_y)
    PangoCairo.show_layout(cr, layout)
    cr.restore()


def _apply_scene_fade(cr, w, h, fade):
    """Full-screen translucent black overlay. ← firstboot-drm.c:247."""
    if fade < 0.005:
        return
    cr.save()
    cr.set_source_rgba(0.0, 0.0, 0.0, fade)
    cr.rectangle(0.0, 0.0, float(w), float(h))
    cr.fill()
    cr.restore()


# -------------------------------------------------------------------
# GTK4 widget + application
# -------------------------------------------------------------------

class PulseArea(Gtk.DrawingArea):
    """Fullscreen Cairo-rendered pulse animation surface."""

    def __init__(self, on_finished):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_draw_func(self._on_draw)

        self._on_finished = on_finished
        self._state = PulseState(TOTAL_PASSES)
        self._last_time_us = None
        self._final_start = TOTAL_PASSES * PULSE_CYCLE_TIME

        self.add_tick_callback(self._on_tick)

    def _on_tick(self, _widget, frame_clock):
        now_us = frame_clock.get_frame_time()
        if self._last_time_us is None:
            self._last_time_us = now_us
            self.queue_draw()
            return GLib.SOURCE_CONTINUE

        dt = (now_us - self._last_time_us) / 1_000_000.0
        self._last_time_us = now_us
        if dt > 0.1:
            dt = 0.1

        self._state.tick(dt)
        self.queue_draw()

        if self._state.finished:
            self._on_finished()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _on_draw(self, _widget, cr, w, h):
        # Black background.
        cr.set_source_rgb(0.0, 0.0, 0.0)
        cr.rectangle(0.0, 0.0, float(w), float(h))
        cr.fill()

        ps = self._state
        ts_text, ts_alpha = text_state(ps)

        if ts_text is not None and ps.global_alpha > 0.01:
            _render_baseline(cr, w, h, ps.global_alpha)

        _render_pulse(cr, ps, w, h)
        _render_beacon(cr, ps, w, h)

        if ts_text is not None and ts_alpha > 0.005:
            font_size = max(24, int(TEXT_FONT_SIZE_1080 * h / 1080))
            _render_text(cr, ts_text, ts_alpha, w, h, font_size)

        if ps.time >= self._final_start - FINAL_FADE_LEAD_SEC:
            fade = smoothstep(self._final_start - FINAL_FADE_LEAD_SEC,
                              self._final_start, ps.time)
            _apply_scene_fade(cr, w, h, fade)


class FirstBootApp(Gtk.Application):

    def __init__(self):
        super().__init__(application_id="com.intergenos.firstboot",
                         flags=0)
        self._win = None
        self._activated_ok = False

    def do_activate(self):
        # Wrap activation in try/except so a GTK init failure (e.g. unit
        # fired before the user session imported WAYLAND_DISPLAY) doesn't
        # silently return rc=0 to systemd and trigger ExecStartPost to
        # write the done-marker. Without this, a transient init failure
        # would permanently bar the user from the animation.
        try:
            self._win = Gtk.ApplicationWindow(application=self)
            self._win.set_decorated(False)
            self._win.set_title("InterGenOS")

            if HAS_LAYER_SHELL:
                # Render as a wlr-layer-shell OVERLAY surface anchored to
                # all 4 screen edges + steal keyboard input. The compositor
                # treats this as session chrome (above normal application
                # windows + above gnome-shell activities overview), which
                # delivers the operator-ratified "take over the screen"
                # semantics. No fullscreen() call is needed -- the 4-edge
                # anchor IS the takeover mechanism.
                Gtk4LayerShell.init_for_window(self._win)
                Gtk4LayerShell.set_layer(
                    self._win, Gtk4LayerShell.Layer.OVERLAY)
                for edge in (Gtk4LayerShell.Edge.TOP,
                             Gtk4LayerShell.Edge.BOTTOM,
                             Gtk4LayerShell.Edge.LEFT,
                             Gtk4LayerShell.Edge.RIGHT):
                    Gtk4LayerShell.set_anchor(self._win, edge, True)
                Gtk4LayerShell.set_keyboard_mode(
                    self._win, Gtk4LayerShell.KeyboardMode.EXCLUSIVE)
            else:
                # Fallback path when gtk4-layer-shell is not installed.
                # The window will render as a normal Gtk.ApplicationWindow;
                # Mutter typically does not honor fullscreen() reliably for
                # these on Wayland + the gnome-shell activities overview
                # competes with the surface (empirically observed v3
                # retest 2026-05-20T~19:14Z on the IGOS laptop). Kept as a
                # degraded-graceful fallback for dev/test surfaces that
                # lack the layer-shell library.
                self._win.fullscreen()

            css = Gtk.CssProvider()
            css.load_from_data(
                b"window, drawingarea { background-color: #000000; }"
            )
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                css,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

            area = PulseArea(on_finished=self._on_finished)
            self._win.set_child(area)
            self._win.present()
            self._activated_ok = True
        except Exception as exc:
            print(f"intergen-firstboot: activation failed: {exc}",
                  file=sys.stderr)
            self.quit()

    def _on_finished(self):
        if self._win is not None:
            self._win.close()
        self.quit()


def main():
    app = FirstBootApp()
    rc = app.run(sys.argv)
    if not app._activated_ok:
        return 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
