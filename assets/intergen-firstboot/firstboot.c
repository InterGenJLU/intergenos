/*
 * InterGenOS First-Boot Animation — SDL2 Prototype
 *
 * Real-time ECG heartbeat pulse animation with text sequence.
 * Uses SDL2 for windowed development; the pulse.c/text.c core
 * is backend-agnostic and portable to DRM/KMS for production.
 *
 * Rendering uses a software framebuffer with per-pixel Gaussian glow
 * (same approach as the approved Python/Cairo preview) uploaded as
 * an SDL texture each frame. This gives smooth anti-aliased output
 * that SDL's integer line drawing can't match.
 *
 * Usage:
 *   ./intergen-firstboot              # windowed 1280x720
 *   ./intergen-firstboot --fullscreen  # native resolution
 *   ./intergen-firstboot --loop        # infinite pulse (wait/loading mode)
 *
 * Copyright (C) 2026 InterGenOS — GPL-3.0-or-later
 */

#include <SDL2/SDL.h>
#include <SDL2/SDL_ttf.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pulse.h"
#include "text.h"

#define DEFAULT_W   1280
#define DEFAULT_H   720
#define TARGET_FPS  60

#define FONT_PATH   "/usr/share/fonts/inter/Inter-Bold.otf"

/* Glow vertical extent in pixels (at 1080p) — scales with resolution */
#define GLOW_RADIUS_1080 14

/* ------------------------------------------------------------------ */
/* Pixel-level framebuffer rendering                                   */
/* ------------------------------------------------------------------ */

/* Alpha-blend a color onto an ARGB8888 pixel buffer */
static inline void blend_pixel(Uint32 *fb, int pitch4, int x, int y,
                               int w, int h,
                               float r, float g, float b, float a)
{
    if (x < 0 || x >= w || y < 0 || y >= h || a < 0.002f) return;
    if (a > 1.0f) a = 1.0f;

    Uint32 *px = &fb[y * pitch4 + x];
    Uint32 existing = *px;

    /* Extract existing color (ARGB8888) */
    float er = (float)((existing >> 16) & 0xFF) / 255.0f;
    float eg = (float)((existing >>  8) & 0xFF) / 255.0f;
    float eb = (float)((existing      ) & 0xFF) / 255.0f;

    /* Alpha composite (source over) */
    float nr = r * a + er * (1.0f - a);
    float ng = g * a + eg * (1.0f - a);
    float nb = b * a + eb * (1.0f - a);

    *px = 0xFF000000
        | ((Uint32)(nr * 255.0f) << 16)
        | ((Uint32)(ng * 255.0f) <<  8)
        | ((Uint32)(nb * 255.0f));
}

/*
 * Stamp a 2D radial glow + core at a sub-pixel point on the waveform.
 *
 * Uses float (fx, fy) center with 2D Gaussian falloff in all directions.
 * Adjacent stamps overlap and blend naturally, eliminating stairstepping
 * even on steep slopes.
 */
static void stamp_line_point(Uint32 *fb, int pitch4, int w, int h,
                             float fx, float fy, float ea, int glow_r)
{
    int cx = (int)(fx + 0.5f);
    int cy = (int)(fy + 0.5f);
    float frac_x = fx - (float)cx;  /* sub-pixel offset */
    float frac_y = fy - (float)cy;

    /* Glow — 2D radial Gaussian */
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

    /* Core line — tight 2D Gaussian, ~2.5px radius */
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

static void render_pulse_to_fb(Uint32 *fb, int pitch4, int w, int h,
                               const PulseState *ps)
{
    float y_center = h * PULSE_Y_CENTER;
    float y_amp    = h * PULSE_Y_AMPLITUDE;
    int glow_r = GLOW_RADIUS_1080 * h / 1080;
    if (glow_r < 4) glow_r = 4;

    float draw_end_f = w * ps->sweep_progress;
    if (draw_end_f > w) draw_end_f = w;

    /*
     * Oversample the waveform for smooth peaks.
     *
     * For flat regions (slope < 1), one sample per pixel is fine.
     * For steep regions (the PQRST spikes), we take sub-pixel samples
     * so the glow blends into a smooth curve instead of stairstepping.
     *
     * We do two passes:
     * 1. Compute Y for every pixel column (coarse pass)
     * 2. For columns where |dy| > 1, subdivide and stamp at fractional X
     */
    int coarse_end = (int)draw_end_f;
    if (coarse_end > w) coarse_end = w;

    /* Pre-compute all Y values */
    float *ys = (float *)malloc(sizeof(float) * (coarse_end + 1));
    float *eas = (float *)malloc(sizeof(float) * (coarse_end + 1));
    for (int x = 0; x <= coarse_end && x < w; x++) {
        float norm_x = (float)x / w;
        float wy = pulse_waveform_y(norm_x);
        ys[x] = y_center - wy * y_amp;
        eas[x] = pulse_edge_alpha(norm_x, ps->sweep_progress)
                * ps->global_alpha;
    }

    /* Render with adaptive oversampling */
    for (int x = 0; x < coarse_end; x++) {
        float ea = eas[x];
        if (ea < 0.003f) continue;

        /* Check slope to next column */
        float slope = 0;
        if (x + 1 <= coarse_end)
            slope = fabsf(ys[x + 1] - ys[x]);

        if (slope <= 1.5f) {
            /* Flat region — single stamp */
            stamp_line_point(fb, pitch4, w, h, (float)x, ys[x], ea, glow_r);
        } else {
            /*
             * Steep region — oversample along the curve.
             * Sample the actual waveform at sub-pixel X positions.
             * The 2D radial Gaussian stamps overlap and blend into
             * a smooth curve with no visible stairstepping.
             */
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

static void render_beacon_to_fb(Uint32 *fb, int pitch4, int w, int h,
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

    /* Outer glow + core + bright center */
    for (int dy = -max_r; dy <= max_r; dy++) {
        for (int dx = -max_r; dx <= max_r; dx++) {
            float dist = sqrtf((float)(dx * dx + dy * dy));

            /* Wide soft glow halo — larger radius, brighter */
            float glow_a = 0.10f * expf(-dist / (base_r * 3.5f)) * ea;

            /* Core dot */
            float core_a = expf(-dist * dist / (base_r * base_r * 1.5f)) * ea;

            /* Bright center */
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

static void render_baseline_to_fb(Uint32 *fb, int pitch4, int w, int h,
                                  float alpha)
{
    int y = (int)(h * PULSE_Y_CENTER);

    for (int x = 0; x < w; x++) {
        /* Wide faint glow */
        for (int dy = -4; dy <= 4; dy++) {
            float dist = fabsf((float)dy);
            float a = 0.025f * expf(-dist * dist / 6.0f) * alpha;
            blend_pixel(fb, pitch4, x, y + dy, w, h,
                        PULSE_GLOW_R, PULSE_GLOW_G, PULSE_GLOW_B, a);
        }
        /* Fine core */
        blend_pixel(fb, pitch4, x, y, w, h,
                    PULSE_LINE_R, PULSE_LINE_G, PULSE_LINE_B,
                    0.04f * alpha);
    }
}

/* ------------------------------------------------------------------ */
/* Text rendering via SDL_ttf                                          */
/* ------------------------------------------------------------------ */

static void render_text(SDL_Renderer *r, TTF_Font *font,
                        int w, int h, const TextState *ts)
{
    if (!ts->text || ts->alpha < 0.005f) return;

    SDL_Color color = {
        (Uint8)(PULSE_TEXT_R * 255),
        (Uint8)(PULSE_TEXT_G * 255),
        (Uint8)(PULSE_TEXT_B * 255),
        255
    };

    SDL_Surface *surface = TTF_RenderUTF8_Blended(font, ts->text, color);
    if (!surface) return;

    SDL_Texture *tex = SDL_CreateTextureFromSurface(r, surface);
    SDL_FreeSurface(surface);
    if (!tex) return;

    int tw, th;
    SDL_QueryTexture(tex, NULL, NULL, &tw, &th);

    SDL_Rect dst = {
        .x = (w - tw) / 2,
        .y = (int)(h * TEXT_Y_POS) - th / 2,
        .w = tw,
        .h = th
    };

    SDL_SetTextureAlphaMod(tex, (Uint8)(ts->alpha * 240));
    SDL_RenderCopy(r, tex, NULL, &dst);
    SDL_DestroyTexture(tex);
}

/* ------------------------------------------------------------------ */
/* Main                                                                */
/* ------------------------------------------------------------------ */

int main(int argc, char *argv[])
{
    int fullscreen = 0;
    int loop_mode = 0;
    int win_w = DEFAULT_W, win_h = DEFAULT_H;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--fullscreen") == 0 || strcmp(argv[i], "-f") == 0)
            fullscreen = 1;
        else if (strcmp(argv[i], "--loop") == 0 || strcmp(argv[i], "-l") == 0)
            loop_mode = 1;
    }

    /* --- Init SDL --- */
    if (SDL_Init(SDL_INIT_VIDEO) < 0) {
        fprintf(stderr, "SDL_Init failed: %s\n", SDL_GetError());
        return 1;
    }
    if (TTF_Init() < 0) {
        fprintf(stderr, "TTF_Init failed: %s\n", TTF_GetError());
        SDL_Quit();
        return 1;
    }

    Uint32 flags = SDL_WINDOW_SHOWN;
    if (fullscreen) {
        flags |= SDL_WINDOW_FULLSCREEN_DESKTOP;
        SDL_DisplayMode dm;
        if (SDL_GetDesktopDisplayMode(0, &dm) == 0) {
            win_w = dm.w;
            win_h = dm.h;
        }
    }

    SDL_Window *window = SDL_CreateWindow(
        "InterGenOS \xe2\x80\x94 First Boot",
        SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
        win_w, win_h, flags);
    if (!window) {
        fprintf(stderr, "SDL_CreateWindow failed: %s\n", SDL_GetError());
        TTF_Quit(); SDL_Quit();
        return 1;
    }
    if (fullscreen) {
        SDL_GetWindowSize(window, &win_w, &win_h);
        SDL_ShowCursor(SDL_DISABLE);
    }

    SDL_Renderer *renderer = SDL_CreateRenderer(window, -1,
        SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC);
    if (!renderer)
        renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_SOFTWARE);
    if (!renderer) {
        fprintf(stderr, "SDL_CreateRenderer failed: %s\n", SDL_GetError());
        SDL_DestroyWindow(window); TTF_Quit(); SDL_Quit();
        return 1;
    }
    SDL_SetRenderDrawBlendMode(renderer, SDL_BLENDMODE_BLEND);

    /* Streaming texture for the pulse framebuffer */
    SDL_Texture *pulse_tex = SDL_CreateTexture(renderer,
        SDL_PIXELFORMAT_ARGB8888, SDL_TEXTUREACCESS_STREAMING,
        win_w, win_h);
    SDL_SetTextureBlendMode(pulse_tex, SDL_BLENDMODE_BLEND);

    /* Pixel framebuffer */
    int fb_pitch = win_w * 4;
    Uint32 *fb = calloc(win_w * win_h, sizeof(Uint32));

    /* Font — scale to resolution */
    int font_size = TEXT_FONT_SIZE_1080 * win_h / 1080;
    if (font_size < 24) font_size = 24;

    TTF_Font *font = TTF_OpenFont(FONT_PATH, font_size);
    if (!font) {
        font = TTF_OpenFont(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            font_size);
    }
    if (!font) {
        fprintf(stderr, "No usable font found\n");
        free(fb); SDL_DestroyTexture(pulse_tex);
        SDL_DestroyRenderer(renderer); SDL_DestroyWindow(window);
        TTF_Quit(); SDL_Quit();
        return 1;
    }

    /* --- Animation state --- */
    PulseState ps;
    pulse_init(&ps, loop_mode ? 0 : 7);

    printf("InterGenOS First-Boot Animation\n");
    printf("  Resolution: %dx%d%s\n", win_w, win_h,
           fullscreen ? " (fullscreen)" : "");
    printf("  Mode: %s\n", loop_mode ? "infinite loop" : "7-sweep sequence");
    printf("  Press ESC or Q to exit\n\n");

    /* --- Main loop --- */
    Uint64 last_tick = SDL_GetPerformanceCounter();
    Uint64 freq = SDL_GetPerformanceFrequency();
    int running = 1;

    while (running) {
        SDL_Event e;
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_QUIT) running = 0;
            if (e.type == SDL_KEYDOWN &&
                (e.key.keysym.sym == SDLK_ESCAPE ||
                 e.key.keysym.sym == SDLK_q))
                running = 0;
        }

        Uint64 now = SDL_GetPerformanceCounter();
        float dt = (float)(now - last_tick) / (float)freq;
        last_tick = now;

        pulse_tick(&ps, dt);
        if (ps.finished) running = 0;

        /* Loop mode: reset after each cycle */
        if (loop_mode && ps.pass_num >= 1) {
            ps.time = fmodf(ps.time, PULSE_CYCLE_TIME);
            ps.pass_num = 0;
        }

        /* --- Clear framebuffer to black --- */
        memset(fb, 0, win_w * win_h * sizeof(Uint32));

        /* --- Render pulse to framebuffer --- */
        TextState ts = text_get_state(&ps);

        /* Baseline glow when text is showing */
        if (ts.text && ps.global_alpha > 0.01f)
            render_baseline_to_fb(fb, win_w, win_w, win_h, ps.global_alpha);

        /* Pulse line + glow */
        render_pulse_to_fb(fb, win_w, win_w, win_h, &ps);

        /* Beacon */
        render_beacon_to_fb(fb, win_w, win_w, win_h, &ps);

        /* Upload framebuffer to texture */
        SDL_UpdateTexture(pulse_tex, NULL, fb, fb_pitch);

        /* --- Compose final frame --- */
        SDL_SetRenderDrawColor(renderer, 0, 0, 0, 255);
        SDL_RenderClear(renderer);

        /* Pulse layer */
        SDL_RenderCopy(renderer, pulse_tex, NULL, NULL);

        /* Text layer (rendered via SDL_ttf, composited on top) */
        render_text(renderer, font, win_w, win_h, &ts);

        /* Final scene fade */
        if (!loop_mode) {
            int total = 7;
            float final_start = total * PULSE_CYCLE_TIME;
            if (ps.time >= final_start - 1.5f) {
                float fade = pulse_smoothstep(final_start - 1.5f,
                                              final_start, ps.time);
                SDL_SetRenderDrawColor(renderer, 0, 0, 0,
                                       (Uint8)(fade * 255));
                SDL_Rect full = { 0, 0, win_w, win_h };
                SDL_RenderFillRect(renderer, &full);
            }
        }

        SDL_RenderPresent(renderer);
    }

    free(fb);
    TTF_CloseFont(font);
    SDL_DestroyTexture(pulse_tex);
    SDL_DestroyRenderer(renderer);
    SDL_DestroyWindow(window);
    TTF_Quit();
    SDL_Quit();

    printf("Animation complete.\n");
    return 0;
}
