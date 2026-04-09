/*
 * InterGenOS DRM/KMS Display — Double-buffered framebuffer rendering
 *
 * Opens the GPU via /dev/dri/card*, finds a connected display,
 * sets the native mode, and provides double-buffered dumb buffers
 * with vsync page flipping for tear-free rendering.
 *
 * Copyright (C) 2026 InterGenOS — GPL-3.0-or-later
 */

#ifndef INTERGEN_DRM_DISPLAY_H
#define INTERGEN_DRM_DISPLAY_H

#include <stdint.h>
#include <xf86drm.h>
#include <xf86drmMode.h>

typedef struct {
    uint32_t handle;
    uint32_t fb_id;
    uint32_t stride;
    uint32_t size;
    uint32_t *map;          /* mmap'd pixel data (ARGB8888) */
} DrmBuffer;

typedef struct {
    int fd;                         /* DRM device file descriptor */
    uint32_t connector_id;
    uint32_t crtc_id;
    drmModeModeInfo mode;           /* active display mode */

    /* Double buffering */
    DrmBuffer bufs[2];
    int front;                      /* index of buffer currently displayed */
    int pflip_pending;              /* page flip in flight */

    /* Public: render to this pointer each frame */
    uint32_t *framebuffer;          /* points to back buffer's map */
    uint32_t buf_stride;            /* stride in bytes */
    uint32_t buf_size;              /* buffer size in bytes */

    /* Saved state for cleanup */
    drmModeCrtc *saved_crtc;

    /* Display dimensions */
    int width;
    int height;
} DrmDisplay;

/*
 * Initialize DRM display with double buffering.
 * Returns 0 on success, -1 on failure.
 */
int drm_display_init(DrmDisplay *d);

/*
 * Present: page flip the back buffer to front at next vblank.
 * Blocks until the previous flip completes (vsync).
 * After return, d->framebuffer points to the new back buffer.
 */
int drm_display_present(DrmDisplay *d);

/*
 * Clean up: restore saved CRTC, destroy buffers, close device.
 */
void drm_display_cleanup(DrmDisplay *d);

#endif /* INTERGEN_DRM_DISPLAY_H */
