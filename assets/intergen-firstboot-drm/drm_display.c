/*
 * InterGenOS DRM/KMS Display — Double-Buffered Implementation
 *
 * Uses two dumb buffers with drmModePageFlip for tear-free vsync'd
 * rendering. Render to back buffer → flip at vblank → swap pointers.
 *
 * Copyright (C) 2026 InterGenOS — GPL-3.0-or-later
 */

#include "drm_display.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <glob.h>

/* ------------------------------------------------------------------ */
/* DRM device discovery                                                */
/* ------------------------------------------------------------------ */

static int try_open_drm(const char *path)
{
    int fd = open(path, O_RDWR | O_CLOEXEC);
    if (fd < 0) return -1;

    /* Try to become DRM master (needed for setCrtc / pageFlip) */
    drmSetMaster(fd);

    return fd;
}

static int open_drm_device(void)
{
    glob_t g;
    int fd = -1;

    if (glob("/dev/dri/card[0-9]*", 0, NULL, &g) == 0) {
        for (size_t i = 0; i < g.gl_pathc; i++) {
            fd = try_open_drm(g.gl_pathv[i]);
            if (fd >= 0) {
                fprintf(stderr, "intergen-firstboot: opened %s\n",
                        g.gl_pathv[i]);
                break;
            }
        }
        globfree(&g);
    }

    return fd;
}

/* ------------------------------------------------------------------ */
/* Connector + CRTC discovery                                          */
/* ------------------------------------------------------------------ */

static int find_connector(int fd, uint32_t *conn_id_out,
                          drmModeModeInfo *mode_out,
                          uint32_t *crtc_id_out)
{
    drmModeRes *res = drmModeGetResources(fd);
    if (!res) {
        fprintf(stderr, "intergen-firstboot: drmModeGetResources failed\n");
        return -1;
    }

    for (int i = 0; i < res->count_connectors; i++) {
        drmModeConnector *conn = drmModeGetConnector(fd, res->connectors[i]);
        if (!conn) continue;

        if (conn->connection == DRM_MODE_CONNECTED && conn->count_modes > 0) {
            /* Prefer the mode marked PREFERRED (native resolution) */
            drmModeModeInfo *mode = NULL;
            for (int j = 0; j < conn->count_modes; j++) {
                if (conn->modes[j].type & DRM_MODE_TYPE_PREFERRED) {
                    mode = &conn->modes[j];
                    break;
                }
            }
            if (!mode)
                mode = &conn->modes[0];

            *conn_id_out = conn->connector_id;
            *mode_out = *mode;

            /* Find CRTC via current encoder, or first available */
            drmModeEncoder *enc = NULL;
            if (conn->encoder_id)
                enc = drmModeGetEncoder(fd, conn->encoder_id);

            if (enc && enc->crtc_id) {
                *crtc_id_out = enc->crtc_id;
                drmModeFreeEncoder(enc);
            } else {
                if (enc) drmModeFreeEncoder(enc);
                /* Find any CRTC that can drive this connector */
                for (int j = 0; j < conn->count_encoders; j++) {
                    enc = drmModeGetEncoder(fd, conn->encoders[j]);
                    if (!enc) continue;
                    for (int k = 0; k < res->count_crtcs; k++) {
                        if (enc->possible_crtcs & (1u << k)) {
                            *crtc_id_out = res->crtcs[k];
                            drmModeFreeEncoder(enc);
                            goto found;
                        }
                    }
                    drmModeFreeEncoder(enc);
                }
            }

found:
            fprintf(stderr, "intergen-firstboot: display %dx%d @ %dHz\n",
                    mode->hdisplay, mode->vdisplay, mode->vrefresh);

            drmModeFreeConnector(conn);
            drmModeFreeResources(res);
            return 0;
        }

        drmModeFreeConnector(conn);
    }

    drmModeFreeResources(res);
    fprintf(stderr, "intergen-firstboot: no connected display found\n");
    return -1;
}

/* ------------------------------------------------------------------ */
/* Dumb buffer creation / destruction                                  */
/* ------------------------------------------------------------------ */

static int create_buffer(int fd, int width, int height, DrmBuffer *buf)
{
    memset(buf, 0, sizeof(*buf));

    struct drm_mode_create_dumb create = {
        .width = width,
        .height = height,
        .bpp = 32,
    };

    if (drmIoctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &create) < 0) {
        fprintf(stderr, "intergen-firstboot: create dumb buffer failed: %s\n",
                strerror(errno));
        return -1;
    }

    buf->handle = create.handle;
    buf->stride = create.pitch;
    buf->size = create.size;

    /* Create framebuffer object */
    if (drmModeAddFB(fd, width, height, 24, 32,
                     buf->stride, buf->handle, &buf->fb_id) < 0) {
        fprintf(stderr, "intergen-firstboot: addFB failed: %s\n",
                strerror(errno));
        return -1;
    }

    /* Memory-map */
    struct drm_mode_map_dumb map = {
        .handle = buf->handle,
    };
    if (drmIoctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &map) < 0) {
        fprintf(stderr, "intergen-firstboot: map dumb buffer failed: %s\n",
                strerror(errno));
        return -1;
    }

    buf->map = mmap(NULL, buf->size, PROT_READ | PROT_WRITE,
                    MAP_SHARED, fd, map.offset);
    if (buf->map == MAP_FAILED) {
        fprintf(stderr, "intergen-firstboot: mmap failed: %s\n",
                strerror(errno));
        buf->map = NULL;
        return -1;
    }

    /* Clear to black */
    memset(buf->map, 0, buf->size);

    return 0;
}

static void destroy_buffer(int fd, DrmBuffer *buf)
{
    if (buf->map) {
        munmap(buf->map, buf->size);
        buf->map = NULL;
    }
    if (buf->fb_id) {
        drmModeRmFB(fd, buf->fb_id);
        buf->fb_id = 0;
    }
    if (buf->handle) {
        struct drm_mode_destroy_dumb destroy = {
            .handle = buf->handle,
        };
        drmIoctl(fd, DRM_IOCTL_MODE_DESTROY_DUMB, &destroy);
        buf->handle = 0;
    }
}

/* ------------------------------------------------------------------ */
/* Page flip callback                                                  */
/* ------------------------------------------------------------------ */

static void page_flip_handler(int fd, unsigned int frame,
                              unsigned int sec, unsigned int usec,
                              void *data)
{
    (void)fd; (void)frame; (void)sec; (void)usec;
    DrmDisplay *d = data;
    d->pflip_pending = 0;
}

/* Wait for a pending page flip to complete */
static void wait_for_flip(DrmDisplay *d)
{
    drmEventContext ev = {
        .version = DRM_EVENT_CONTEXT_VERSION,
        .page_flip_handler = page_flip_handler,
    };

    while (d->pflip_pending) {
        struct pollfd pfd = {
            .fd = d->fd,
            .events = POLLIN,
        };

        int ret = poll(&pfd, 1, 100); /* 100ms timeout */
        if (ret < 0) {
            if (errno == EINTR) continue;
            break;
        }
        if (ret > 0 && (pfd.revents & POLLIN))
            drmHandleEvent(d->fd, &ev);
    }
}

/* ------------------------------------------------------------------ */
/* Public API                                                          */
/* ------------------------------------------------------------------ */

int drm_display_init(DrmDisplay *d)
{
    memset(d, 0, sizeof(*d));
    d->fd = -1;

    /* Open DRM device */
    d->fd = open_drm_device();
    if (d->fd < 0) {
        fprintf(stderr, "intergen-firstboot: no DRM device found\n");
        return -1;
    }

    /* Find connected display */
    if (find_connector(d->fd, &d->connector_id, &d->mode, &d->crtc_id) < 0) {
        close(d->fd);
        return -1;
    }

    d->width = d->mode.hdisplay;
    d->height = d->mode.vdisplay;

    /* Save current CRTC state for cleanup */
    d->saved_crtc = drmModeGetCrtc(d->fd, d->crtc_id);

    /* Create two dumb buffers for double buffering */
    for (int i = 0; i < 2; i++) {
        if (create_buffer(d->fd, d->width, d->height, &d->bufs[i]) < 0) {
            /* Clean up any buffer we already created */
            for (int j = 0; j < i; j++)
                destroy_buffer(d->fd, &d->bufs[j]);
            if (d->saved_crtc) drmModeFreeCrtc(d->saved_crtc);
            close(d->fd);
            return -1;
        }
    }

    /* Buffer 0 is front (displayed), buffer 1 is back (we render to) */
    d->front = 0;
    d->framebuffer = d->bufs[1].map;
    d->buf_stride = d->bufs[1].stride;
    d->buf_size = d->bufs[1].size;

    /* Set initial display mode with front buffer */
    if (drmModeSetCrtc(d->fd, d->crtc_id, d->bufs[0].fb_id, 0, 0,
                       &d->connector_id, 1, &d->mode) < 0) {
        fprintf(stderr, "intergen-firstboot: setCrtc failed: %s\n",
                strerror(errno));
        drm_display_cleanup(d);
        return -1;
    }

    return 0;
}

int drm_display_present(DrmDisplay *d)
{
    /* Wait for any previous flip to complete */
    if (d->pflip_pending)
        wait_for_flip(d);

    /* Flip to the back buffer (which we just rendered to) */
    int back = 1 - d->front;

    int ret = drmModePageFlip(d->fd, d->crtc_id, d->bufs[back].fb_id,
                              DRM_MODE_PAGE_FLIP_EVENT, d);
    if (ret < 0) {
        /* Page flip failed — fall back to setCrtc (blocking, may tear
         * on one frame, but won't break the animation) */
        drmModeSetCrtc(d->fd, d->crtc_id, d->bufs[back].fb_id, 0, 0,
                       &d->connector_id, 1, &d->mode);
    } else {
        d->pflip_pending = 1;
        /* Wait for this flip to complete before we start writing
         * to the old front buffer (now becomes the new back buffer) */
        wait_for_flip(d);
    }

    /* Swap: old back becomes new front, old front becomes new back */
    d->front = back;

    /* Point framebuffer to the new back buffer for next frame's rendering */
    int new_back = 1 - d->front;
    d->framebuffer = d->bufs[new_back].map;
    d->buf_stride = d->bufs[new_back].stride;
    d->buf_size = d->bufs[new_back].size;

    return 0;
}

void drm_display_cleanup(DrmDisplay *d)
{
    if (!d) return;

    /* Wait for any pending flip */
    if (d->pflip_pending)
        wait_for_flip(d);

    /* Restore saved CRTC */
    if (d->saved_crtc) {
        drmModeSetCrtc(d->fd, d->saved_crtc->crtc_id,
                       d->saved_crtc->buffer_id,
                       d->saved_crtc->x, d->saved_crtc->y,
                       &d->connector_id, 1, &d->saved_crtc->mode);
        drmModeFreeCrtc(d->saved_crtc);
        d->saved_crtc = NULL;
    }

    /* Destroy both buffers */
    for (int i = 0; i < 2; i++)
        destroy_buffer(d->fd, &d->bufs[i]);

    d->framebuffer = NULL;

    if (d->fd >= 0) {
        drmDropMaster(d->fd);
        close(d->fd);
        d->fd = -1;
    }
}
