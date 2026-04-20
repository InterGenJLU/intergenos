# InterGenOS Boot Animation — Phase 2 Implementation Notes

**Date:** 2026-04-08
**Status:** SDL2 real-time prototype complete and approved
**Location:** `/home/christopher/intergenos/assets/intergen-firstboot/`

---

## What We Built

A real-time SDL2 prototype of the InterGenOS ECG heartbeat pulse animation. This is Phase 2 of the three-phase plan:

1. **Phase 1 (complete):** Python/Cairo preview generator — nailed the visual design, exported to MP4 for review
2. **Phase 2 (complete):** SDL2 real-time prototype — runs live at 60fps, iteratable, approved
3. **Phase 3 (future):** C/DRM production binary — swap SDL for DRM/KMS framebuffer, runs at boot before display server

## Architecture

The code is structured as a **reusable visual language library**, not a monolithic boot animation:

```
intergen-firstboot/
├── pulse.h / pulse.c       — Pure math: waveform, state machine, edge fading
│                              NO rendering calls. Backend-agnostic.
├── text.h / text.c         — Text state machine: string + alpha per frame
│                              NO rendering calls. Backend-agnostic.
├── firstboot.c             — SDL2 rendering backend + main loop
│                              Swappable for DRM/KMS in Phase 3.
└── Makefile
```

**Key design decision:** Animation logic (pulse.c, text.c) is completely separated from the rendering backend (firstboot.c). This means:
- Phase 3 (DRM/KMS) keeps pulse.c and text.c unchanged, only replaces the SDL rendering
- Future consumers (GNOME extension, installer, loading screens) call the same `pulse_waveform_y()`, `pulse_smoothstep()`, etc.

## The Visual Language (ChatGPT insight)

The ECG pulse is not just a boot animation — it's InterGenOS's **brand signature** that scales to:

| Use Case | Implementation |
|----------|---------------|
| **First-boot animation** | Full 7-sweep sequence with text (what we built) |
| **Loading/wait indicator** | `--loop` mode, continuous single-sweep pulse, no text |
| **UI accents** | Subtle pulse in GNOME extension status area |
| **Installer transition** | Pulse fades to 10%, installer UI fades in over it |
| **System notifications** | Brief pulse flash for system events |
| **System awareness** | Pulse speed could tie to CPU load (future) |
| **Identity** | Blue tone shift per install, seeded from machine-id (future) |

## Animation Parameters (approved)

| Parameter | Value |
|-----------|-------|
| Sweeps | 7 |
| BPM | ~37 (BEAT_PERIOD = 1.6031s) |
| Duration | ~22.4s total |
| Peak positions | 28% and 78% screen width (0.5 apart for perfect rhythm) |
| PQRST complex width | 16% of screen |
| Waveform | 5-component Gaussian (P, Q, R, S, T waves) |
| Y center | 52% screen height |
| Y amplitude | 14% screen height |
| Edge fade | 20% screen width |
| Line color | RGB(0, 153, 255) — slightly desaturated blue |
| Glow color | RGB(0, 140, 242) |
| Text color | RGB(230, 235, 240) — off-white, 94% opacity |
| Font | Inter Bold, 72pt at 1080p (scales with resolution) |
| Text Y | 42% screen height (above the pulse line) |

## Text Sequence

| Pass | Text | Behavior |
|------|------|----------|
| 0 | (none) | Pure pulse, establish the heartbeat |
| 1 | "Hello." | Slow fade in over full sweep |
| 2 | "Hello." | Fade out at 65% speed |
| 3 | "Welcome to InterGenOS." | Slow fade in |
| 4 | "Welcome to InterGenOS." | Fade out |
| 5 | "Shall we get started?" | Slow fade in |
| 6 | "Shall we get started?" | Hold solid, fades with pulse on final sweep |

## Rendering Technique

The prototype uses **per-pixel software rendering** with 2D radial Gaussian glow:

- Each frame clears a pixel framebuffer to black
- For each pixel column along the waveform, stamps a 2D Gaussian glow + core line
- Steep regions (ECG peaks) are **oversampled** at sub-pixel X positions — the waveform function is evaluated at fractional X coordinates and blended with 2D radial Gaussians
- The leading beacon uses concentric glow rings + bright core + white-hot center
- Framebuffer is uploaded as an SDL streaming texture each frame
- Text rendered via SDL_ttf (FreeType) with alpha modulation

This gives Cairo-quality anti-aliased output through SDL2's simple window management.

## Build & Run

```bash
# Dependencies (Ubuntu/Debian)
sudo apt install libsdl2-dev libsdl2-ttf-dev

# Build
cd assets/intergen-firstboot
make

# Run
./intergen-firstboot              # windowed 1280x720
./intergen-firstboot --fullscreen  # native resolution
./intergen-firstboot --loop        # infinite pulse (loading mode)

# Controls: ESC or Q to exit
```

Requires Inter Bold font at `~/.local/share/fonts/Inter-Bold.otf` (falls back to DejaVu Sans Bold).

## Phase 3 Plan (DRM/KMS Production)

When ready to make this a real boot component:

1. Replace `firstboot.c` with `firstboot_drm.c`:
   - Open `/dev/dri/card0`
   - Enumerate connectors, find preferred mode
   - Create double-buffered dumb framebuffers
   - Render to mmap'd buffer using the same pixel stamping code
   - Page-flip with vsync via `drmModePageFlip()`

2. Keep `pulse.c` and `text.c` unchanged

3. Font rendering: replace SDL_ttf with direct FreeType calls
   - `FT_Load_Char()` → rasterize glyphs
   - Alpha-blend onto framebuffer manually

4. Systemd integration:
   - `intergen-firstboot.service` with `Type=idle`, `ConditionPathExists`
   - Conflicts with `getty@tty1.service`
   - Before `forge-installer.service`

5. Dependencies: libdrm + freetype2 only (both already in desktop tier)

## Prior Art

- **Original concept:** ChatGPT conversation (PDF at `research/branding/BootAnimationConcept_ChatGPT.pdf`)
- **Implementation research:** `research/branding/boot_animation_implementation_research_2026-04-07.md`
- **Python preview:** `assets/boot_animation_preview.py` (Phase 1)
- **Preview video:** `assets/intergen_boot_preview.mp4`
