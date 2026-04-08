/*
 * InterGenOS Pulse — Visual Language Core Implementation
 * Copyright (C) 2026 InterGenOS — GPL-3.0-or-later
 */

#include "pulse.h"
#include <math.h>

/* ------------------------------------------------------------------ */
/* Math utilities                                                      */
/* ------------------------------------------------------------------ */

float pulse_smoothstep(float edge0, float edge1, float x)
{
    if (edge1 == edge0)
        return x >= edge1 ? 1.0f : 0.0f;
    float t = (x - edge0) / (edge1 - edge0);
    if (t < 0.0f) t = 0.0f;
    if (t > 1.0f) t = 1.0f;
    return t * t * (3.0f - 2.0f * t);
}

float pulse_ecg_complex(float t)
{
    float y = 0.0f;
    y += 0.12f * expf(-powf((t - 0.15f) / 0.035f, 2));   /* P wave */
    y -= 0.10f * expf(-powf((t - 0.27f) / 0.012f, 2));   /* Q dip  */
    y += 1.00f * expf(-powf((t - 0.30f) / 0.014f, 2));   /* R peak */
    y -= 0.18f * expf(-powf((t - 0.34f) / 0.016f, 2));   /* S dip  */
    y += 0.22f * expf(-powf((t - 0.52f) / 0.045f, 2));   /* T wave */
    return y;
}

float pulse_waveform_y(float norm_x)
{
    float y = 0.0f;
    float hw = PULSE_COMPLEX_WIDTH / 2.0f;

    float t1 = (norm_x - (PULSE_PEAK1_POS - hw)) / PULSE_COMPLEX_WIDTH;
    if (t1 >= 0.0f && t1 <= 1.0f)
        y += pulse_ecg_complex(t1);

    float t2 = (norm_x - (PULSE_PEAK2_POS - hw)) / PULSE_COMPLEX_WIDTH;
    if (t2 >= 0.0f && t2 <= 1.0f)
        y += pulse_ecg_complex(t2);

    return y;
}

float pulse_edge_alpha(float norm_x, float sweep_progress)
{
    /* Leading edge fade-in */
    float fade_in = pulse_smoothstep(0.0f, PULSE_EDGE_FADE, norm_x);

    /* Trailing darkness wave */
    if (sweep_progress > PULSE_TRAIL_TRIGGER) {
        float wave = (sweep_progress - PULSE_TRAIL_TRIGGER)
                   / (1.0f - PULSE_TRAIL_TRIGGER);
        float dark_pos = wave * (sweep_progress - PULSE_TRAIL_VISIBLE);
        float trail = pulse_smoothstep(dark_pos,
                                       dark_pos + PULSE_TRAIL_FADE_W,
                                       norm_x);
        fade_in *= trail;
    }

    /* Right edge fade at leading tip */
    float fade_out = pulse_smoothstep(1.0f, 1.0f - PULSE_EDGE_FADE * 0.5f,
                                      norm_x);
    return fade_in * fade_out;
}

/* ------------------------------------------------------------------ */
/* State management                                                    */
/* ------------------------------------------------------------------ */

void pulse_init(PulseState *s, int total_passes)
{
    s->time = 0.0f;
    s->pass_num = 0;
    s->sweep_progress = 0.0f;
    s->global_alpha = 1.0f;
    s->total_passes = total_passes;
    s->finished = 0;
}

void pulse_tick(PulseState *s, float dt)
{
    s->time += dt;

    s->pass_num = (int)(s->time / PULSE_CYCLE_TIME);
    float time_in_pass = s->time - s->pass_num * PULSE_CYCLE_TIME;
    s->sweep_progress = time_in_pass * PULSE_SWEEP_SPEED;

    /* Global fade on final pass */
    s->global_alpha = 1.0f;
    if (s->total_passes > 0) {
        if (s->pass_num >= s->total_passes) {
            s->finished = 1;
            s->global_alpha = 0.0f;
            return;
        }
        if (s->pass_num == s->total_passes - 1) {
            float peak2_done = PULSE_PEAK2_POS + PULSE_COMPLEX_WIDTH / 2.0f;
            float sp = s->sweep_progress < 1.0f ? s->sweep_progress : 1.0f;
            s->global_alpha = 1.0f - pulse_smoothstep(0.0f, peak2_done, sp);
        }
    }
}

/* ------------------------------------------------------------------ */
/* Point generation                                                    */
/* ------------------------------------------------------------------ */

int pulse_generate_points(const PulseState *s, int screen_w, int screen_h,
                          PulsePoint *out, int max_points)
{
    float y_center = screen_h * PULSE_Y_CENTER;
    float y_amp    = screen_h * PULSE_Y_AMPLITUDE;

    int draw_end = (int)(screen_w * s->sweep_progress);
    if (draw_end < 0) draw_end = 0;
    if (draw_end > screen_w) draw_end = screen_w;
    if (draw_end > max_points) draw_end = max_points;

    for (int x = 0; x < draw_end; x++) {
        float norm_x = (float)x / screen_w;
        float wy = pulse_waveform_y(norm_x);
        out[x].x = norm_x;
        out[x].y = y_center - wy * y_amp;
        out[x].alpha = pulse_edge_alpha(norm_x, s->sweep_progress)
                     * s->global_alpha;
    }
    return draw_end;
}
