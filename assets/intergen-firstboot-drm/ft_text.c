/*
 * InterGenOS FreeType2 Text Renderer Implementation
 * Copyright (C) 2026 InterGenOS — GPL-3.0-or-later
 */

#include "ft_text.h"

#include <ft2build.h>
#include FT_FREETYPE_H
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct FtText {
    FT_Library library;
    FT_Face    face;
    int        pixel_size;
};

FtText *ft_text_init(const char *font_path, int pixel_size)
{
    FtText *ft = calloc(1, sizeof(FtText));
    if (!ft) return NULL;

    if (FT_Init_FreeType(&ft->library)) {
        fprintf(stderr, "intergen-firstboot: FreeType init failed\n");
        free(ft);
        return NULL;
    }

    if (FT_New_Face(ft->library, font_path, 0, &ft->face)) {
        fprintf(stderr, "intergen-firstboot: cannot load font: %s\n",
                font_path);
        FT_Done_FreeType(ft->library);
        free(ft);
        return NULL;
    }

    FT_Set_Pixel_Sizes(ft->face, 0, pixel_size);
    ft->pixel_size = pixel_size;

    return ft;
}

/* Alpha-blend a single pixel */
static inline void blend_px(uint32_t *fb, int stride, int x, int y,
                            int w, int h,
                            float r, float g, float b, float a)
{
    if (x < 0 || x >= w || y < 0 || y >= h || a < 0.002f) return;
    if (a > 1.0f) a = 1.0f;

    uint32_t *px = &fb[y * stride + x];
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

/* Measure text width without rendering */
static int measure_text(FtText *ft, const char *text)
{
    int width = 0;
    FT_GlyphSlot slot = ft->face->glyph;

    for (const char *p = text; *p; p++) {
        if (FT_Load_Char(ft->face, (unsigned char)*p, FT_LOAD_DEFAULT))
            continue;
        width += slot->advance.x >> 6;
    }
    return width;
}

void ft_text_render(FtText *ft, uint32_t *fb, int stride,
                    int fb_w, int fb_h,
                    const char *text, int center_y,
                    float r, float g, float b, float alpha)
{
    if (!ft || !text || !fb || alpha < 0.005f) return;

    /* Measure text to center horizontally */
    int text_w = measure_text(ft, text);
    int pen_x = (fb_w - text_w) / 2;
    int pen_y = center_y + ft->pixel_size / 3; /* baseline offset */

    FT_GlyphSlot slot = ft->face->glyph;

    for (const char *p = text; *p; p++) {
        if (FT_Load_Char(ft->face, (unsigned char)*p, FT_LOAD_RENDER))
            continue;

        FT_Bitmap *bmp = &slot->bitmap;
        int bx = pen_x + slot->bitmap_left;
        int by = pen_y - slot->bitmap_top;

        for (unsigned int row = 0; row < bmp->rows; row++) {
            for (unsigned int col = 0; col < bmp->width; col++) {
                unsigned char gray = bmp->buffer[row * bmp->pitch + col];
                if (gray == 0) continue;

                float ga = (float)gray / 255.0f * alpha;
                blend_px(fb, stride, bx + col, by + row, fb_w, fb_h,
                         r, g, b, ga);
            }
        }

        pen_x += slot->advance.x >> 6;
    }
}

void ft_text_cleanup(FtText *ft)
{
    if (!ft) return;
    if (ft->face) FT_Done_Face(ft->face);
    if (ft->library) FT_Done_FreeType(ft->library);
    free(ft);
}
