/*
 * InterGenOS FreeType2 Text Renderer — Direct pixel buffer rendering
 *
 * Renders anti-aliased text into an ARGB8888 pixel buffer using FreeType2.
 * No SDL_ttf, no display server, no window manager.
 *
 * Copyright (C) 2026 InterGenOS — GPL-3.0-or-later
 */

#ifndef INTERGEN_FT_TEXT_H
#define INTERGEN_FT_TEXT_H

#include <stdint.h>

typedef struct FtText FtText;

/*
 * Initialize the FreeType text renderer.
 * font_path: path to a .ttf or .otf font file
 * pixel_size: font size in pixels
 *
 * Returns a handle, or NULL on failure.
 */
FtText *ft_text_init(const char *font_path, int pixel_size);

/*
 * Render text centered horizontally at a given Y position.
 *
 * fb: ARGB8888 pixel buffer
 * stride: buffer stride in uint32_t units (usually width)
 * fb_w, fb_h: buffer dimensions
 * text: UTF-8 string to render
 * center_y: vertical center of the text (pixels)
 * r, g, b: text color (0.0-1.0)
 * alpha: text opacity (0.0-1.0)
 */
void ft_text_render(FtText *ft, uint32_t *fb, int stride,
                    int fb_w, int fb_h,
                    const char *text, int center_y,
                    float r, float g, float b, float alpha);

/*
 * Clean up FreeType resources.
 */
void ft_text_cleanup(FtText *ft);

#endif /* INTERGEN_FT_TEXT_H */
