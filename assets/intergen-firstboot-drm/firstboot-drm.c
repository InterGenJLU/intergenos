/*
 * InterGenOS First-Boot Animation — DRM/KMS Production Binary
 *
 * Renders the ECG heartbeat pulse animation directly to the display
 * via DRM/KMS dumb buffers. No X11, no Wayland, no SDL — runs on
 * bare TTY before any display server starts.
 *
 * This is the production version of the SDL2 prototype. The pulse
 * and text modules (pulse.c, text.c) are shared between both.
 *
 * Usage:
 *   intergen-firstboot              # 7-sweep sequence, then exit
 *   intergen-firstboot --loop       # infinite pulse (loading mode)
 *
 * Requires:
 *   - /dev/dri/card* access (root or video group)
 *   - libdrm (DRM/KMS mode setting)
 *   - libfreetype (text rendering)
 *   - Inter-Bold.otf font at FONT_PATH
 *
 * Copyright (C) 2026 InterGenOS — GPL-3.0-or-later
 */

#include <math.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "drm_display.h"
#include "ft_text.h"
#include "pulse.h"
#include "text.h"

/* Font search paths — tried in order */
static const char *FONT_PATHS[] = {
    "/usr/share/fonts/Inter-Bold.otf",
    "/usr/share/fonts/inter/Inter-Bold.otf",
    "/usr/share/fonts/truetype/inter/Inter-Bold.otf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
    NULL
};

#define TARGET_FPS      60
#define FRAME_TIME_NS   (1000000000L / TARGET_FPS)

/* Glow vertical extent in pixels (at 1080p) */
#define GLOW_RADIUS_1080 14

static volatile int g_running = 1;

static void signal_handler(int sig)
{
    (void)sig;
    g_running = 0;
}

/* ------------------------------------------------------------------ */
/* Pixel-level framebuffer rendering                                   */
/* Ported from firstboot.c — identical math, uint32_t instead of Uint32*/
/* ------------------------------------------------------------------ */

static inline void blend_pixel(uint32_t *fb, int pitch4, int x, int y,
                               int w, int h,
                               float r, float g, float b, float a)
{
    if (x < 0 || x >= w || y < 0 || y >= h || a < 0.002f) return;
    if (a > 1.0f) a = 1.0f;

    uint32_t *px = &fb[y * pitch4 + x];
    uint32_t existing = *px;

    float er = (float)((existing >> 16) & 0xFF) / 255.0f;
    float eg = (float)((existing >>  8) & 0xFF) / 255.0f;
    float eb = (float)((existing      ) & 0xFF) / 255.0f;

    float nr = r * a + er * (1.0f - a);
    float ng = g * a + eg * (1.0f - a);
    float nb = b * a + eb * (1.0f - a);

    *px = 0xFF000000
        | ((uint32_t)(nr * 255.0f) << 16)
        | ((uint32_t)(ng * 255.0f) <<  8)
        | ((uint32_t)(nb * 255.0f));
}

static void stamp_line_point(uint32_t *fb, int pitch4, int w, int h,
                             float fx, float fy, float ea, int glow_r)
{
    int cx = (int)(fx + 0.5f);
    int cy = (int)(fy + 0.5f);
    float frac_x = fx - (float)cx;
    float frac_y = fy - (float)cy;

    for (int dy = -glow_r; dy <= glow_r; dy++) {
        float dist_y = (float)dy - frac_y;
        float dy2 = dist_y * dist_y;
        for (int dx = -2; dx <= 2; dx++) {
            float dist_x = (float)dx - frac_x;
            float dist2 = dist_x * dist_x + dy2;
            float glow_a = PULSE_GLOW_MAX_A
                         * expf(-dist2 / (float)(glow_r * 1.2f)) * ea;
            if (glow_a > 0.002f)
                blend_pixel(fb, pitch4, cx + dx, cy + dy, w, h,
                            PULSE_GLOW_R, PULSE_GLOW_G, PULSE_GLOW_B,
                            glow_a);
        }
    }

    for (int dy = -3; dy <= 3; dy++) {
        float dist_y = (float)dy - frac_y;
        float dy2 = dist_y * dist_y;
        for (int dx = -3; dx <= 3; dx++) {
            float dist_x = (float)dx - frac_x;
            float dist2 = dist_x * dist_x + dy2;
            float core_a = expf(-dist2 / 1.2f) * ea;
            if (core_a > 0.01f)
                blend_pixel(fb, pitch4, cx + dx, cy + dy, w, h,
                            PULSE_LINE_R, PULSE_LINE_G, PULSE_LINE_B,
                            core_a);
        }
    }
}

static void render_pulse(uint32_t *fb, int pitch4, int w, int h,
                         const PulseState *ps)
{
    float y_center = h * PULSE_Y_CENTER;
    float y_amp    = h * PULSE_Y_AMPLITUDE;
    int glow_r = GLOW_RADIUS_1080 * h / 1080;
    if (glow_r < 4) glow_r = 4;

    float draw_end_f = w * ps->sweep_progress;
    if (draw_end_f > w) draw_end_f = w;

    int coarse_end = (int)draw_end_f;
    if (coarse_end > w) coarse_end = w;

    float *ys = malloc(sizeof(float) * (coarse_end + 1));
    float *eas = malloc(sizeof(float) * (coarse_end + 1));
    for (int x = 0; x <= coarse_end && x < w; x++) {
        float norm_x = (float)x / w;
        float wy = pulse_waveform_y(norm_x);
        ys[x] = y_center - wy * y_amp;
        eas[x] = pulse_edge_alpha(norm_x, ps->sweep_progress)
                * ps->global_alpha;
    }

    for (int x = 0; x < coarse_end; x++) {
        float ea = eas[x];
        if (ea < 0.003f) continue;

        float slope = 0;
        if (x + 1 <= coarse_end)
            slope = fabsf(ys[x + 1] - ys[x]);

        if (slope <= 1.5f) {
            stamp_line_point(fb, pitch4, w, h, (float)x, ys[x], ea, glow_r);
        } else {
            int substeps = (int)(slope * 1.5f);
            if (substeps < 2) substeps = 2;
            if (substeps > 80) substeps = 80;

            for (int s = 0; s <= substeps; s++) {
                float frac = (float)s / substeps;
                float sub_x = (float)x + frac;
                float sub_norm = sub_x / w;
                float sub_wy = pulse_waveform_y(sub_norm);
                float sub_py = y_center - sub_wy * y_amp;
                float sub_ea = pulse_edge_alpha(sub_norm, ps->sweep_progress)
                             * ps->global_alpha;
                stamp_line_point(fb, pitch4, w, h, sub_x, sub_py,
                                 sub_ea, glow_r);
            }
        }
    }

    free(ys);
    free(eas);
}

static void render_beacon(uint32_t *fb, int pitch4, int w, int h,
                          const PulseState *ps)
{
    int draw_end = (int)(w * ps->sweep_progress);
    if (draw_end < 5) return;

    float norm_x = (float)(draw_end - 1) / w;
    float wy = pulse_waveform_y(norm_x);
    float py = h * PULSE_Y_CENTER - wy * h * PULSE_Y_AMPLITUDE;
    float ea = pulse_edge_alpha(norm_x, ps->sweep_progress)
             * ps->global_alpha;
    if (ea < 0.01f) return;

    int cx = draw_end - 1;
    int cy = (int)py;
    int max_r = (int)(PULSE_BEACON_RADIUS * 7.0f * h / 1080.0f);
    float base_r = PULSE_BEACON_RADIUS * h / 1080.0f;

    for (int dy = -max_r; dy <= max_r; dy++) {
        for (int dx = -max_r; dx <= max_r; dx++) {
            float dist = sqrtf((float)(dx * dx + dy * dy));

            float glow_a = 0.10f * expf(-dist / (base_r * 3.5f)) * ea;
            float core_a = expf(-dist * dist / (base_r * base_r * 1.5f)) * ea;
            float inner_a = 0.9f * expf(-dist * dist / (base_r * base_r * 0.3f)) * ea;

            if (glow_a > 0.002f)
                blend_pixel(fb, pitch4, cx + dx, cy + dy, w, h,
                            PULSE_GLOW_R, PULSE_GLOW_G, PULSE_GLOW_B,
                            glow_a);
            if (core_a > 0.01f)
                blend_pixel(fb, pitch4, cx + dx, cy + dy, w, h,
                            PULSE_LINE_R, PULSE_LINE_G, PULSE_LINE_B,
                            core_a);
            if (inner_a > 0.01f)
                blend_pixel(fb, pitch4, cx + dx, cy + dy, w, h,
                            0.85f, 0.97f, 1.0f, inner_a);
        }
    }
}

static void render_baseline(uint32_t *fb, int pitch4, int w, int h,
                            float alpha)
{
    int y = (int)(h * PULSE_Y_CENTER);

    for (int x = 0; x < w; x++) {
        for (int dy = -4; dy <= 4; dy++) {
            float dist = fabsf((float)dy);
            float a = 0.025f * expf(-dist * dist / 6.0f) * alpha;
            blend_pixel(fb, pitch4, x, y + dy, w, h,
                        PULSE_GLOW_R, PULSE_GLOW_G, PULSE_GLOW_B, a);
        }
        blend_pixel(fb, pitch4, x, y, w, h,
                    PULSE_LINE_R, PULSE_LINE_G, PULSE_LINE_B,
                    0.04f * alpha);
    }
}

/* Apply a full-screen fade to black */
static void apply_scene_fade(uint32_t *fb, int pitch4, int w, int h,
                             float fade)
{
    if (fade < 0.005f) return;
    /* Multiply every pixel by (1 - fade) */
    float keep = 1.0f - fade;
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            uint32_t *px = &fb[y * pitch4 + x];
            uint32_t p = *px;
            uint32_t r = (uint32_t)((float)((p >> 16) & 0xFF) * keep);
            uint32_t g = (uint32_t)((float)((p >>  8) & 0xFF) * keep);
            uint32_t b = (uint32_t)((float)((p      ) & 0xFF) * keep);
            *px = 0xFF000000 | (r << 16) | (g << 8) | b;
        }
    }
}

/* ------------------------------------------------------------------ */
/* Timing helpers                                                      */
/* ------------------------------------------------------------------ */

static uint64_t clock_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

/* ------------------------------------------------------------------ */
/* Main                                                                */
/* ------------------------------------------------------------------ */

int main(int argc, char *argv[])
{
    int loop_mode = 0;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--loop") == 0 || strcmp(argv[i], "-l") == 0)
            loop_mode = 1;
    }

    /* Handle signals for clean shutdown */
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    /* --- Init DRM display --- */
    DrmDisplay disp;
    if (drm_display_init(&disp) < 0) {
        fprintf(stderr, "intergen-firstboot: cannot initialize display\n");
        return 1;
    }

    int w = disp.width;
    int h = disp.height;

    fprintf(stderr, "intergen-firstboot: %dx%d, stride=%u, %s mode\n",
            w, h, disp.buf_stride, loop_mode ? "loop" : "7-sweep");

    /* --- Init FreeType text --- */
    int font_size = TEXT_FONT_SIZE_1080 * h / 1080;
    if (font_size < 24) font_size = 24;

    FtText *ft = NULL;
    for (const char **fp = FONT_PATHS; *fp; fp++) {
        ft = ft_text_init(*fp, font_size);
        if (ft) {
            fprintf(stderr, "intergen-firstboot: font: %s (%dpx)\n",
                    *fp, font_size);
            break;
        }
    }
    if (!ft) {
        fprintf(stderr, "intergen-firstboot: no usable font found\n");
        drm_display_cleanup(&disp);
        return 1;
    }

    /* --- Animation state --- */
    PulseState ps;
    pulse_init(&ps, loop_mode ? 0 : 7);

    /* --- Main loop --- */
    uint64_t last_ns = clock_ns();

    while (g_running) {
        uint64_t now = clock_ns();
        float dt = (float)(now - last_ns) / 1000000000.0f;
        last_ns = now;

        /* Cap dt to avoid jumps on stall */
        if (dt > 0.1f) dt = 0.1f;

        pulse_tick(&ps, dt);
        if (ps.finished) break;

        if (loop_mode && ps.pass_num >= 1) {
            ps.time = fmodf(ps.time, PULSE_CYCLE_TIME);
            ps.pass_num = 0;
        }

        /* pitch4 may change after present (buffer swap) — read each frame */
        int pitch4 = disp.buf_stride / 4;

        /* --- Clear back buffer to black --- */
        memset(disp.framebuffer, 0, disp.buf_size);

        /* --- Render to back buffer --- */
        TextState ts = text_get_state(&ps);

        /* Baseline glow when text is showing */
        if (ts.text && ps.global_alpha > 0.01f)
            render_baseline(disp.framebuffer, pitch4, w, h, ps.global_alpha);

        /* Pulse line + glow */
        render_pulse(disp.framebuffer, pitch4, w, h, &ps);

        /* Beacon */
        render_beacon(disp.framebuffer, pitch4, w, h, &ps);

        /* Text */
        if (ts.text && ts.alpha > 0.005f) {
            int text_y = (int)(h * TEXT_Y_POS);
            ft_text_render(ft, disp.framebuffer, pitch4, w, h,
                          ts.text, text_y,
                          PULSE_TEXT_R, PULSE_TEXT_G, PULSE_TEXT_B,
                          ts.alpha);
        }

        /* Final scene fade */
        if (!loop_mode) {
            int total = 7;
            float final_start = total * PULSE_CYCLE_TIME;
            if (ps.time >= final_start - 1.5f) {
                float fade = pulse_smoothstep(final_start - 1.5f,
                                              final_start, ps.time);
                apply_scene_fade(disp.framebuffer, pitch4, w, h, fade);
            }
        }

        /* Flip back buffer to front at vblank — tear-free.
         * The page flip wait provides natural vsync timing,
         * so no manual nanosleep is needed. */
        drm_display_present(&disp);
    }

    /* --- Cleanup --- */
    ft_text_cleanup(ft);

    /* Clear to black before restoring console */
    memset(disp.framebuffer, 0, disp.buf_size);
    drm_display_present(&disp);

    drm_display_cleanup(&disp);

    fprintf(stderr, "intergen-firstboot: animation complete\n");
    return 0;
}
