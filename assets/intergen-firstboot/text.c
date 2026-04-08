/*
 * InterGenOS Pulse — Text Sequence Implementation
 * Copyright (C) 2026 InterGenOS — GPL-3.0-or-later
 */

#include "text.h"
#include <stddef.h>

/*
 * Text timing follows the pulse sweeps:
 *   Pass 0: pulse only, no text (establish the heartbeat)
 *   Pass 1: "Hello." fades in (slow, deliberate)
 *   Pass 2: "Hello." fades out
 *   Pass 3: "Welcome to InterGenOS." fades in
 *   Pass 4: "Welcome to InterGenOS." fades out
 *   Pass 5: "Shall we get started?" fades in
 *   Pass 6: "Shall we get started?" holds, fades with global alpha
 *
 * Fade in is deliberately slow (full sweep duration).
 * Fade out is ~65% of sweep duration (faster exit than entrance).
 */

#define FADE_IN_END   0.95f
#define FADE_OUT_END  0.63f

TextState text_get_state(const PulseState *ps)
{
    TextState ts = { .text = NULL, .alpha = 0.0f };

    float time_in_pass = ps->time - ps->pass_num * PULSE_CYCLE_TIME;
    float frac = time_in_pass / PULSE_CYCLE_TIME;
    if (frac < 0.0f) frac = 0.0f;
    if (frac > 1.0f) frac = 1.0f;

    switch (ps->pass_num) {
    case 0:
        /* Pure pulse, no text */
        break;

    case 1:
        ts.text = "Hello.";
        ts.alpha = pulse_smoothstep(0.05f, FADE_IN_END, frac);
        break;

    case 2:
        ts.text = "Hello.";
        ts.alpha = 1.0f - pulse_smoothstep(0.0f, FADE_OUT_END, frac);
        break;

    case 3:
        ts.text = "Welcome to InterGenOS.";
        ts.alpha = pulse_smoothstep(0.05f, FADE_IN_END, frac);
        break;

    case 4:
        ts.text = "Welcome to InterGenOS.";
        ts.alpha = 1.0f - pulse_smoothstep(0.0f, FADE_OUT_END, frac);
        break;

    case 5:
        ts.text = "Shall we get started?";
        ts.alpha = pulse_smoothstep(0.05f, FADE_IN_END, frac);
        break;

    case 6:
        /* Hold solid — fades with global_alpha (tied to pulse fade) */
        ts.text = "Shall we get started?";
        ts.alpha = 1.0f;
        break;

    default:
        break;
    }

    /* Apply global alpha (handles final fade-out) */
    ts.alpha *= ps->global_alpha;

    return ts;
}
