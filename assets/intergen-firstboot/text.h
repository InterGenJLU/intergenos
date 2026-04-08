/*
 * InterGenOS Pulse — Text Sequence State Machine (Backend-Agnostic)
 *
 * Manages the "Hello." / "Welcome to InterGenOS." / "Shall we get started?"
 * text sequence with timed fades synchronized to pulse sweeps.
 *
 * NO rendering calls — returns (string, alpha) for the caller to draw.
 *
 * Copyright (C) 2026 InterGenOS — GPL-3.0-or-later
 */

#ifndef INTERGEN_TEXT_H
#define INTERGEN_TEXT_H

#include "pulse.h"

/* Text Y position (normalized, above the pulse line) */
#define TEXT_Y_POS  0.42f

/* Font size at 1080p — scale proportionally */
#define TEXT_FONT_SIZE_1080  72

typedef struct {
    const char *text;   /* NULL if no text this frame */
    float       alpha;  /* 0.0 = invisible, 1.0 = fully opaque */
} TextState;

/*
 * Get the text and alpha for the current animation time.
 * Uses the PulseState to synchronize text with sweep passes.
 */
TextState text_get_state(const PulseState *ps);

#endif /* INTERGEN_TEXT_H */
