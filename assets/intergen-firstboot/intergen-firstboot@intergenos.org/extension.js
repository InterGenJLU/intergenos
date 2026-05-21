// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 InterGenOS
//
// intergen-firstboot extension.js — GNOME Shell extension that renders the
// InterGenOS first-login branded ECG animation. Fires once per user on the
// first login (gated by a filesystem marker at ~/.local/share/intergen/
// firstboot-animation-done); subsequent logins no-op.
//
// Architecture: renders at the gnome-shell internal level via St.DrawingArea
// + Main.layoutManager.addTopChrome, ABOVE all client surfaces and above
// compositor-drawn UI (activities overview, top bar, dock). This is the
// only layer on Mutter that defeats gnome-shell's own session-startup UI;
// wlr-layer-shell + ext-session-lock-v1 both empirically failed during
// v1-v5 iteration (Mutter does not implement ext-session-lock-v1 and the
// wlr-layer-shell OVERLAY layer is still a client surface that compositor-
// drawn chrome renders above).
//
// Math is ported VERBATIM from the canonical Python source at
// assets/intergen-firstboot-py/intergen-firstboot.py (which in turn ports
// from the C/DRM source at assets/intergen-firstboot/{pulse,text}.{c,h}).
// All Q5-locked constants and all 5 math functions transfer 1:1 with two
// documented substitutions: (a) the pycairo -> GJS Cairo naming convention
// change (snake_case -> camelCase); (b) PulseState.tick(dt) -> setTime(t)
// absolute-time adaptation for Clutter.Timeline new-frame semantics
// (justified at PulseState.setTime in-class comment). Q5 design lock per
// operator-direct 2026-05-20T~19:Z: sweep count, total duration, sweep
// rate, ECG curve shape, text content + fade curves, font choice,
// fade-easing are NON-NEGOTIABLE.
//
// Pairs with intergen-no-overview@intergenos.org which suppresses the
// activities overview at every login. Both extensions are default-enabled
// via config/gsettings/91_intergenos-extensions.gschema.override per D-006.
//
// Welcomer handoff: the intergen-welcome XDG autostart fires in parallel
// during session startup; its window sits beneath our chrome-level overlay
// until we remove the overlay on animation completion. No explicit chain
// handshake -- overlay-removal IS the handoff.

import GObject from 'gi://GObject';
import St from 'gi://St';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import Pango from 'gi://Pango';
import PangoCairo from 'gi://PangoCairo';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

// GJS Cairo binding is exposed via the legacy `imports` global, not as a
// gi:// module. Canonical pattern empirically verified in dash-to-dock
// (/usr/share/gnome-shell/extensions/dash-to-dock@micxgx.gmail.com/
// appIconIndicators.js:18) installed on the IGOS reference laptop.
const {cairo: Cairo} = imports;


// ===================================================================
// Q5 brand constants (LOCKED -- do not modify without operator-explicit
// greenlight to reopen Q5 of the chain-vs-phase walkthrough)
//
// Ported verbatim from assets/intergen-firstboot-py/intergen-firstboot.py
// lines 68-116, which in turn ports from assets/intergen-firstboot/pulse.h
// + text.h. Citations indicate source line in pulse.h.
// ===================================================================

// Peak positions (normalized screen width)                pulse.h:24
const PULSE_PEAK1_POS = 0.28;
const PULSE_PEAK2_POS = 0.78;
const PULSE_COMPLEX_WIDTH = 0.16;

// Rhythm                                                  pulse.h:29
const PULSE_BEAT_INTERVAL = 0.50;
const PULSE_BEAT_PERIOD = 1.6031;                          // ~37 BPM (5% faster)
const PULSE_SWEEP_SPEED = PULSE_BEAT_INTERVAL / PULSE_BEAT_PERIOD;
const PULSE_CYCLE_TIME = 1.0 / PULSE_SWEEP_SPEED;

// Vertical positioning                                    pulse.h:35
const PULSE_Y_CENTER = 0.52;
const PULSE_Y_AMPLITUDE = 0.14;

// Edge fading                                             pulse.h:39
const PULSE_EDGE_FADE = 0.20;

// Trailing darkness wave                                  pulse.h:42
const PULSE_TRAIL_VISIBLE = 0.25;
const PULSE_TRAIL_FADE_W = 0.30;
const PULSE_TRAIL_TRIGGER = PULSE_PEAK2_POS - PULSE_COMPLEX_WIDTH;

// Brand palette                                           pulse.h:46
const PULSE_LINE_RGB = [0.00, 0.60, 1.00];
const PULSE_GLOW_RGB = [0.00, 0.55, 0.95];
const PULSE_TEXT_RGB = [0.90, 0.92, 0.94];
const PULSE_INNER_HIGHLIGHT_RGB = [0.85, 0.97, 1.00];      // firstboot-drm.c:223

// Rendering parameters                                    pulse.h:60
const PULSE_LINE_WIDTH = 2.5;
const PULSE_GLOW_LAYERS = 6;
const PULSE_GLOW_MAX_W = 28.0;
const PULSE_GLOW_MAX_A = 0.08;
const PULSE_BEACON_RADIUS = 4.0;

// Text geometry                                           text.h:18
const TEXT_Y_POS = 0.42;
const TEXT_FONT_SIZE_1080 = 72;
const TEXT_FONT_FAMILY = 'Inter Bold, DejaVu Sans Bold, Noto Sans Bold, Sans Bold';

// Text-state fade thresholds                              text.c:23
const FADE_IN_END = 0.95;
const FADE_OUT_END = 0.63;

// Run-loop parameters                                     firstboot-drm.c:49
const TOTAL_PASSES = 7;
const FINAL_FADE_LEAD_SEC = 1.5;
const GLOW_RADIUS_1080 = 14;

// Derived from Q5 constants per IGOSC peer-review MINOR 2026-05-21T01:14Z:
// preserves the design-lock invariant from-constants instead of hardcoding
// a magic millisecond value. Reader sees Q5 constants drive duration.
// 7 * 3.2062 * 1000 = 22443ms
const TOTAL_DURATION_MS = Math.round(TOTAL_PASSES * PULSE_CYCLE_TIME * 1000);

// Once-per-user marker. Path matches the prior Python implementation so a
// future C/DRM fallback (Q6 preserved at assets/intergen-firstboot{,-drm}/)
// can share state if ever needed.
const MARKER_REL_DIR = 'intergen';
const MARKER_REL_FILE = 'firstboot-animation-done';


// ===================================================================
// Math (verbatim port from pulse.c + text.c via intergen-firstboot.py)
// ===================================================================

function smoothstep(edge0, edge1, x) {
    if (edge1 === edge0) {
        return x >= edge1 ? 1.0 : 0.0;
    }
    let t = (x - edge0) / (edge1 - edge0);
    if (t < 0.0) t = 0.0;
    if (t > 1.0) t = 1.0;
    return t * t * (3.0 - 2.0 * t);
}

// Five-component ECG complex at normalized t in [0, 1].   pulse.c:23
function ecgComplex(t) {
    let y = 0.0;
    y += 0.12 * Math.exp(-Math.pow((t - 0.15) / 0.035, 2));   // P wave
    y -= 0.10 * Math.exp(-Math.pow((t - 0.27) / 0.012, 2));   // Q dip
    y += 1.00 * Math.exp(-Math.pow((t - 0.30) / 0.014, 2));   // R peak
    y -= 0.18 * Math.exp(-Math.pow((t - 0.34) / 0.016, 2));   // S dip
    y += 0.22 * Math.exp(-Math.pow((t - 0.52) / 0.045, 2));   // T wave
    return y;
}

// Two ECG complexes summed at PEAK1 + PEAK2 positions.    pulse.c:34
function waveformY(normX) {
    let y = 0.0;
    const hw = PULSE_COMPLEX_WIDTH / 2.0;

    const t1 = (normX - (PULSE_PEAK1_POS - hw)) / PULSE_COMPLEX_WIDTH;
    if (t1 >= 0.0 && t1 <= 1.0) {
        y += ecgComplex(t1);
    }

    const t2 = (normX - (PULSE_PEAK2_POS - hw)) / PULSE_COMPLEX_WIDTH;
    if (t2 >= 0.0 && t2 <= 1.0) {
        y += ecgComplex(t2);
    }

    return y;
}

// Leading fade-in + trailing dark-wave + right-edge fade. pulse.c:50
function edgeAlpha(normX, sweepProgress) {
    let fadeIn = smoothstep(0.0, PULSE_EDGE_FADE, normX);

    if (sweepProgress > PULSE_TRAIL_TRIGGER) {
        const wave = (sweepProgress - PULSE_TRAIL_TRIGGER) / (1.0 - PULSE_TRAIL_TRIGGER);
        const darkPos = wave * (sweepProgress - PULSE_TRAIL_VISIBLE);
        const trail = smoothstep(darkPos, darkPos + PULSE_TRAIL_FADE_W, normX);
        fadeIn *= trail;
    }

    const fadeOut = smoothstep(1.0, 1.0 - PULSE_EDGE_FADE * 0.5, normX);
    return fadeIn * fadeOut;
}


// ===================================================================
// Animation state (PulseState; ported from pulse.c PulseState + tick)
// ===================================================================

class PulseState {
    constructor(totalPasses) {
        this.time = 0.0;
        this.passNum = 0;
        this.sweepProgress = 0.0;
        this.globalAlpha = 1.0;
        this.totalPasses = totalPasses;
        this.finished = false;
    }

    // Set absolute elapsed time. The Python implementation uses tick(dt)
    // with delta-time accumulation; Clutter.Timeline new-frame gives us
    // authoritative total elapsed time per frame so we set absolute time
    // here and derive all subsequent state from it. Avoids drift over the
    // 22.4s animation.
    setTime(t) {
        this.time = t;
        this.passNum = Math.floor(this.time / PULSE_CYCLE_TIME);
        const timeInPass = this.time - this.passNum * PULSE_CYCLE_TIME;
        this.sweepProgress = timeInPass * PULSE_SWEEP_SPEED;

        this.globalAlpha = 1.0;
        if (this.totalPasses > 0) {
            if (this.passNum >= this.totalPasses) {
                this.finished = true;
                this.globalAlpha = 0.0;
                return;
            }
            if (this.passNum === this.totalPasses - 1) {
                const peak2Done = PULSE_PEAK2_POS + PULSE_COMPLEX_WIDTH / 2.0;
                const sp = Math.min(this.sweepProgress, 1.0);
                this.globalAlpha = 1.0 - smoothstep(0.0, peak2Done, sp);
            }
        }
    }
}

// Return [text, alpha] for the current pass.              text.c:26
function textStateFor(ps) {
    const timeInPass = ps.time - ps.passNum * PULSE_CYCLE_TIME;
    let frac = timeInPass / PULSE_CYCLE_TIME;
    if (frac < 0.0) frac = 0.0;
    if (frac > 1.0) frac = 1.0;

    let text = null;
    let alpha = 0.0;
    const pn = ps.passNum;

    if (pn === 0) {
        // Intentionally empty -- pass 0 is the pure-pulse warmup
    } else if (pn === 1) {
        text = 'Hello.';
        alpha = smoothstep(0.05, FADE_IN_END, frac);
    } else if (pn === 2) {
        text = 'Hello.';
        alpha = 1.0 - smoothstep(0.0, FADE_OUT_END, frac);
    } else if (pn === 3) {
        text = 'Welcome to InterGenOS.';
        alpha = smoothstep(0.05, FADE_IN_END, frac);
    } else if (pn === 4) {
        text = 'Welcome to InterGenOS.';
        alpha = 1.0 - smoothstep(0.0, FADE_OUT_END, frac);
    } else if (pn === 5) {
        text = 'Shall we get started?';
        alpha = smoothstep(0.05, FADE_IN_END, frac);
    } else if (pn === 6) {
        text = 'Shall we get started?';
        alpha = 1.0;
    }

    alpha *= ps.globalAlpha;
    return [text, alpha];
}


// ===================================================================
// Cairo render functions (GJS camelCase bindings; verbatim math port)
// ===================================================================

function buildPulsePath(cr, ps, w, h) {
    const yCenter = h * PULSE_Y_CENTER;
    const yAmp = h * PULSE_Y_AMPLITUDE;

    let drawEnd = Math.floor(w * ps.sweepProgress);
    if (drawEnd < 1) return [];
    if (drawEnd > w) drawEnd = w;

    // Pre-sample y per integer column.
    const ys = new Array(drawEnd + 1);
    const eas = new Array(drawEnd + 1);
    for (let x = 0; x <= drawEnd; x++) {
        const normX = x / w;
        ys[x] = yCenter - waveformY(normX) * yAmp;
        eas[x] = edgeAlpha(normX, ps.sweepProgress) * ps.globalAlpha;
    }

    const samples = [];
    let started = false;
    for (let x = 0; x < drawEnd; x++) {
        const ea = eas[x];
        if (ea < 0.003) {
            samples.push([x, ys[x], ea]);
            continue;
        }

        if (!started) {
            cr.moveTo(x, ys[x]);
            started = true;
        } else {
            cr.lineTo(x, ys[x]);
        }
        samples.push([x, ys[x], ea]);

        const slope = (x + 1 <= drawEnd) ? Math.abs(ys[x + 1] - ys[x]) : 0.0;
        if (slope > 1.5) {
            let substeps = Math.floor(slope * 1.5);
            if (substeps < 2) substeps = 2;
            if (substeps > 80) substeps = 80;
            for (let s = 1; s <= substeps; s++) {
                const frac = s / substeps;
                const subX = x + frac;
                const subNorm = subX / w;
                const subPy = yCenter - waveformY(subNorm) * yAmp;
                cr.lineTo(subX, subPy);
            }
        }
    }

    return samples;
}

function alphaMaskPattern(samples, w, ps) {
    const pat = new Cairo.LinearGradient(0.0, 0.0, w, 0.0);
    const drawEnd = Math.floor(w * ps.sweepProgress);
    if (drawEnd < 1) return pat;

    const STOPS = 64;
    for (let i = 0; i <= STOPS; i++) {
        const frac = i / STOPS;
        let x = Math.floor(frac * drawEnd);
        if (x >= samples.length) x = samples.length - 1;
        if (x < 0) x = 0;
        const ea = samples.length > 0 ? samples[x][2] : 0.0;
        pat.addColorStopRGBA(frac * drawEnd / w, 1.0, 1.0, 1.0, ea);
    }
    if (drawEnd < w) {
        pat.addColorStopRGBA(drawEnd / w, 1.0, 1.0, 1.0, 0.0);
        pat.addColorStopRGBA(1.0, 1.0, 1.0, 1.0, 0.0);
    }
    return pat;
}

function renderPulse(cr, ps, w, h) {
    cr.save();
    cr.pushGroup();

    cr.newPath();
    const samples = buildPulsePath(cr, ps, w, h);
    if (samples.length === 0) {
        cr.popGroup();
        cr.restore();
        return;
    }

    cr.setLineCap(Cairo.LineCap.ROUND);
    cr.setLineJoin(Cairo.LineJoin.ROUND);

    // Outer glow: PULSE_GLOW_LAYERS strokes, widest+lowest-alpha first.
    const [gr, gg, gb] = PULSE_GLOW_RGB;
    for (let i = 0; i < PULSE_GLOW_LAYERS; i++) {
        const layer = PULSE_GLOW_LAYERS - i;
        const width = PULSE_GLOW_MAX_W * layer / PULSE_GLOW_LAYERS;
        const alpha = PULSE_GLOW_MAX_A * Math.exp(
            -Math.pow((layer - 1) / PULSE_GLOW_LAYERS, 2) * 2.0);
        cr.setSourceRGBA(gr, gg, gb, alpha);
        cr.setLineWidth(width * h / 1080.0);
        cr.strokePreserve();
    }

    // Core line on top, full alpha.
    const [lr, lg, lb] = PULSE_LINE_RGB;
    cr.setSourceRGBA(lr, lg, lb, 1.0);
    cr.setLineWidth(PULSE_LINE_WIDTH * h / 1080.0);
    cr.stroke();

    const patternSurface = cr.popGroup();
    cr.setSource(patternSurface);
    cr.mask(alphaMaskPattern(samples, w, ps));

    cr.restore();
}

// Radial beacon at the leading tip.                       firstboot-drm.c:187
function renderBeacon(cr, ps, w, h) {
    const drawEnd = Math.floor(w * ps.sweepProgress);
    if (drawEnd < 5) return;

    const normX = (drawEnd - 1) / w;
    const wy = waveformY(normX);
    const py = h * PULSE_Y_CENTER - wy * h * PULSE_Y_AMPLITUDE;
    const ea = edgeAlpha(normX, ps.sweepProgress) * ps.globalAlpha;
    if (ea < 0.01) return;

    const cx = drawEnd - 1;
    const cy = py;
    let maxR = PULSE_BEACON_RADIUS * 7.0 * h / 1080.0;
    let baseR = PULSE_BEACON_RADIUS * h / 1080.0;
    if (maxR < 4) maxR = 4;
    if (baseR < 1) baseR = 1;

    cr.save();

    const [gr, gg, gb] = PULSE_GLOW_RGB;
    const [lr, lg, lb] = PULSE_LINE_RGB;
    const [ir, ig, ib] = PULSE_INNER_HIGHLIGHT_RGB;

    const pat = new Cairo.RadialGradient(cx, cy, 0.0, cx, cy, maxR);
    pat.addColorStopRGBA(0.0, ir, ig, ib, 0.9 * ea);
    pat.addColorStopRGBA(baseR * 0.4 / maxR, ir, ig, ib, 0.25 * ea);
    pat.addColorStopRGBA(baseR / maxR, lr, lg, lb, 0.55 * ea);
    pat.addColorStopRGBA(baseR * 3.0 / maxR, gr, gg, gb, 0.10 * ea);
    pat.addColorStopRGBA(1.0, gr, gg, gb, 0.0);

    cr.setSource(pat);
    cr.arc(cx, cy, maxR, 0.0, 2.0 * Math.PI);
    cr.fill();
    cr.restore();
}

// Faint horizontal glow at the pulse Y line.              firstboot-drm.c:228
function renderBaseline(cr, w, h, alpha) {
    if (alpha < 0.01) return;
    const y = h * PULSE_Y_CENTER;
    const [gr, gg, gb] = PULSE_GLOW_RGB;
    const [lr, lg, lb] = PULSE_LINE_RGB;

    cr.save();
    let bandH = 8.0 * h / 1080.0;
    if (bandH < 4) bandH = 4;
    const grad = new Cairo.LinearGradient(0.0, y - bandH, 0.0, y + bandH);
    grad.addColorStopRGBA(0.0, gr, gg, gb, 0.0);
    grad.addColorStopRGBA(0.5, gr, gg, gb, 0.025 * alpha);
    grad.addColorStopRGBA(1.0, gr, gg, gb, 0.0);
    cr.setSource(grad);
    cr.rectangle(0.0, y - bandH, w, 2.0 * bandH);
    cr.fill();

    cr.setSourceRGBA(lr, lg, lb, 0.04 * alpha);
    cr.setLineWidth(1.0);
    cr.moveTo(0.0, y);
    cr.lineTo(w, y);
    cr.stroke();
    cr.restore();
}

// Centered text via PangoCairo. Pango handles HarfBuzz shaping + subpixel
// anti-aliasing automatically -- the same engine GTK/Firefox/GNOME-Shell
// itself use, so glyph rendering carries from Python+GTK to JS+St without
// regression.
function renderText(cr, text, alpha, w, h, fontSize) {
    if (!text || alpha < 0.005) return;

    cr.save();
    const layout = PangoCairo.create_layout(cr);
    const fontDesc = Pango.FontDescription.from_string(`${TEXT_FONT_FAMILY} ${fontSize}px`);
    layout.set_font_description(fontDesc);
    layout.set_text(text, -1);

    const [textW, textH] = layout.get_pixel_size();
    const penX = (w - textW) / 2;
    const penY = h * TEXT_Y_POS - textH / 2;

    const [tr, tg, tb] = PULSE_TEXT_RGB;
    cr.setSourceRGBA(tr, tg, tb, alpha);
    cr.moveTo(penX, penY);
    PangoCairo.show_layout(cr, layout);
    cr.restore();
}

// Full-screen translucent black overlay.                  firstboot-drm.c:247
function applySceneFade(cr, w, h, fade) {
    if (fade < 0.005) return;
    cr.save();
    cr.setSourceRGBA(0.0, 0.0, 0.0, fade);
    cr.rectangle(0.0, 0.0, w, h);
    cr.fill();
    cr.restore();
}


// ===================================================================
// IntergenFirstbootOverlay -- St.DrawingArea subclass that hosts the
// animation surface. addTopChrome'd into the shell's chrome layer above
// all client surfaces and above gnome-shell compositor-drawn UI.
// ===================================================================

const IntergenFirstbootOverlay = GObject.registerClass(
class IntergenFirstbootOverlay extends St.DrawingArea {
    _init(width, height) {
        super._init({
            width,
            height,
            x: 0,
            y: 0,
            reactive: true,
        });
        this._state = new PulseState(TOTAL_PASSES);
        this._finalStart = TOTAL_PASSES * PULSE_CYCLE_TIME;
    }

    setTime(seconds) {
        this._state.setTime(seconds);
        this.queue_repaint();
    }

    isFinished() {
        return this._state.finished;
    }

    vfunc_repaint() {
        const cr = this.get_context();
        const [width, height] = this.get_surface_size();

        try {
            // Black background.
            cr.setSourceRGB(0.0, 0.0, 0.0);
            cr.rectangle(0.0, 0.0, width, height);
            cr.fill();

            const ps = this._state;
            const [text, alpha] = textStateFor(ps);

            if (text !== null && ps.globalAlpha > 0.01) {
                renderBaseline(cr, width, height, ps.globalAlpha);
            }

            renderPulse(cr, ps, width, height);
            renderBeacon(cr, ps, width, height);

            if (text !== null && alpha > 0.005) {
                let fontSize = Math.floor(TEXT_FONT_SIZE_1080 * height / 1080);
                if (fontSize < 24) fontSize = 24;
                renderText(cr, text, alpha, width, height, fontSize);
            }

            if (ps.time >= this._finalStart - FINAL_FADE_LEAD_SEC) {
                const fade = smoothstep(
                    this._finalStart - FINAL_FADE_LEAD_SEC,
                    this._finalStart, ps.time);
                applySceneFade(cr, width, height, fade);
            }
        } finally {
            // GJS does not auto-finalize Cairo contexts; explicit $dispose
            // is required or the context lingers and leaks frame state.
            cr.$dispose();
        }
    }
});


// ===================================================================
// IntergenFirstbootExtension -- the Extension entry point
// ===================================================================

export default class IntergenFirstbootExtension extends Extension {
    enable() {
        this._overlay = null;
        this._timeline = null;
        this._newFrameId = 0;
        this._completedId = 0;
        this._startupCompleteId = 0;

        // Hook startup-complete -- our entry point. We do not check the
        // marker here; the connect-to-startup-complete handler does, so
        // we run if-and-only-if Main.layoutManager is fully ready.
        this._startupCompleteId = Main.layoutManager.connect(
            'startup-complete', () => this._maybeRunAnimation());

        // If startup-complete already fired (e.g., user enables/disables
        // the extension manually mid-session), run the check anyway.
        // Re-ordering one frame so any in-flight signal handlers settle
        // first.
        if (Main.layoutManager._startingUp === false) {
            GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
                this._maybeRunAnimation();
                return GLib.SOURCE_REMOVE;
            });
        }
    }

    _maybeRunAnimation() {
        const markerPath = GLib.build_filenamev([
            GLib.get_user_data_dir(), MARKER_REL_DIR, MARKER_REL_FILE]);

        const markerFile = Gio.File.new_for_path(markerPath);
        if (markerFile.query_exists(null)) {
            // Already ran for this user -- subsequent logins land directly
            // in the desktop with no animation.
            return;
        }

        this._runAnimation(markerPath);
    }

    _runAnimation(markerPath) {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor) {
            // No primary monitor (headless? Wayland-edge?). Refuse to run
            // -- without a size we cannot scale the animation; leave the
            // marker absent so a subsequent login can retry.
            log('intergen-firstboot: no primary monitor; skipping animation');
            return;
        }

        // Suppress the activities overview that would otherwise compete
        // with our overlay. The intergen-no-overview sibling extension
        // does this for every login; we do it here additionally so the
        // animation works even if no-overview is disabled.
        Main.overview.hide();

        this._overlay = new IntergenFirstbootOverlay(
            monitor.width, monitor.height);

        // addTopChrome places the actor in the top chrome layer above
        // every other chrome (tooltips, OSD, etc.). affectsInputRegion
        // captures input so the user cannot accidentally click through
        // to the activities overview or panel during the 22.4s animation.
        Main.layoutManager.addTopChrome(this._overlay, {
            affectsInputRegion: true,
            affectsStruts: false,
            trackFullscreen: false,
        });

        this._timeline = new Clutter.Timeline({duration: TOTAL_DURATION_MS});

        this._newFrameId = this._timeline.connect('new-frame',
            (_timeline, msecs) => {
                if (!this._overlay) return;
                this._overlay.setTime(msecs / 1000.0);
            });

        this._completedId = this._timeline.connect('completed', () => {
            this._writeMarker(markerPath);
            this._teardownOverlay();
        });

        this._timeline.start();
    }

    _writeMarker(markerPath) {
        try {
            const markerDir = GLib.path_get_dirname(markerPath);
            GLib.mkdir_with_parents(markerDir, 0o755);
            GLib.file_set_contents(markerPath, '');
        } catch (e) {
            // If we cannot write the marker (FS permission, quota, etc.),
            // log and continue. The animation already ran; the worst case
            // is that subsequent login re-fires the animation.
            log(`intergen-firstboot: could not write marker ${markerPath}: ${e}`);
        }
    }

    _teardownOverlay() {
        if (this._timeline) {
            if (this._newFrameId) {
                this._timeline.disconnect(this._newFrameId);
                this._newFrameId = 0;
            }
            if (this._completedId) {
                this._timeline.disconnect(this._completedId);
                this._completedId = 0;
            }
            this._timeline.stop();
            this._timeline = null;
        }
        if (this._overlay) {
            Main.layoutManager.removeChrome(this._overlay);
            this._overlay.destroy();
            this._overlay = null;
        }
    }

    disable() {
        if (this._startupCompleteId) {
            Main.layoutManager.disconnect(this._startupCompleteId);
            this._startupCompleteId = 0;
        }
        this._teardownOverlay();
    }
}
