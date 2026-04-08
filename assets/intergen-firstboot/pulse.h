/*
 * InterGenOS Pulse — Visual Language Core (Backend-Agnostic)
 *
 * The ECG heartbeat pulse is InterGenOS's brand signature. This module
 * provides the waveform math and animation state used across:
 *   - First-boot animation (full 7-sweep sequence)
 *   - Loading/wait indicators (continuous pulse loop)
 *   - UI accents (GNOME extension, installer, notifications)
 *
 * NO rendering calls here — pure math. Rendering backends (SDL, DRM,
 * Cairo) consume this data to draw the visuals.
 *
 * Copyright (C) 2026 InterGenOS — GPL-3.0-or-later
 */

#ifndef INTERGEN_PULSE_H
#define INTERGEN_PULSE_H

/* ------------------------------------------------------------------ */
/* Brand constants — the visual language parameters                    */
/* ------------------------------------------------------------------ */

/* Peak positions (normalized screen width) */
#define PULSE_PEAK1_POS     0.28f
#define PULSE_PEAK2_POS     0.78f
#define PULSE_COMPLEX_WIDTH 0.16f

/* Rhythm */
#define PULSE_BEAT_INTERVAL 0.50f
#define PULSE_BEAT_PERIOD   1.6031f     /* ~37 BPM (5% faster) */
#define PULSE_SWEEP_SPEED   (PULSE_BEAT_INTERVAL / PULSE_BEAT_PERIOD)
#define PULSE_CYCLE_TIME    (1.0f / PULSE_SWEEP_SPEED)

/* Vertical positioning */
#define PULSE_Y_CENTER      0.52f
#define PULSE_Y_AMPLITUDE   0.14f

/* Edge fading */
#define PULSE_EDGE_FADE     0.20f

/* Trailing darkness wave */
#define PULSE_TRAIL_VISIBLE 0.25f
#define PULSE_TRAIL_FADE_W  0.30f
#define PULSE_TRAIL_TRIGGER (PULSE_PEAK2_POS - PULSE_COMPLEX_WIDTH)

/* Colors — brand palette (R, G, B as 0-1 floats) */
#define PULSE_LINE_R  0.00f
#define PULSE_LINE_G  0.60f
#define PULSE_LINE_B  1.00f

#define PULSE_GLOW_R  0.00f
#define PULSE_GLOW_G  0.55f
#define PULSE_GLOW_B  0.95f

#define PULSE_TEXT_R   0.90f
#define PULSE_TEXT_G   0.92f
#define PULSE_TEXT_B   0.94f

/* Rendering parameters */
#define PULSE_LINE_WIDTH     2.5f
#define PULSE_GLOW_LAYERS    6
#define PULSE_GLOW_MAX_W     28.0f
#define PULSE_GLOW_MAX_A     0.08f
#define PULSE_BEACON_RADIUS  4.0f

/* ------------------------------------------------------------------ */
/* Animation state                                                     */
/* ------------------------------------------------------------------ */

typedef struct {
    float time;             /* elapsed seconds */
    int   pass_num;         /* current sweep (0-based) */
    float sweep_progress;   /* cursor position [0..1+] */
    float global_alpha;     /* master opacity (fades on last pass) */
    int   total_passes;     /* total sweeps (7 for boot, 0 for infinite) */
    int   finished;         /* 1 when animation is done */
} PulseState;

/* ------------------------------------------------------------------ */
/* Math functions                                                      */
/* ------------------------------------------------------------------ */

float pulse_smoothstep(float edge0, float edge1, float x);
float pulse_ecg_complex(float t);
float pulse_waveform_y(float norm_x);
float pulse_edge_alpha(float norm_x, float sweep_progress);

/* ------------------------------------------------------------------ */
/* State management                                                    */
/* ------------------------------------------------------------------ */

void pulse_init(PulseState *s, int total_passes);
void pulse_tick(PulseState *s, float dt);

/* ------------------------------------------------------------------ */
/* Point generation — backend-agnostic                                 */
/* ------------------------------------------------------------------ */

typedef struct {
    float x;    /* normalized [0..1] */
    float y;    /* pixel Y */
    float alpha;/* edge-faded alpha */
} PulsePoint;

/*
 * Fill an array of PulsePoints for the current frame.
 * Returns the number of points written (up to max_points).
 * Caller provides screen dimensions for Y scaling.
 */
int pulse_generate_points(const PulseState *s, int screen_w, int screen_h,
                          PulsePoint *out, int max_points);

#endif /* INTERGEN_PULSE_H */
