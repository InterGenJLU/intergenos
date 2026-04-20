# InterGenOS Icon Design Brief

## What is InterGenOS?

InterGenOS is a Linux distribution built entirely from source with a built-in
AI assistant called InterGen. The visual identity centers on a single concept:
**Light Emerging from Dark** — the system is alive, and the blue energy that
pulses through its interface is the signal that proves it.

## What we need

We're designing a **custom icon theme** starting with the **folder icon**.
The folder is the most-seen icon in the system. It sets the tone for
everything that follows (~600 total icons across the theme).

## What we have

The files in this packet:

- **VISUAL_LANGUAGE.md** — The canonical design specification. Palette,
  typography, line rules, corner radius scale, the "glow don't tint"
  principle, and the complete interaction language. READ THIS FIRST.

- **gnome-shell.css** — The GNOME Shell theme (1,420 lines). This is the
  visual identity in code — panel, dash, quick settings, calendar,
  notifications, lock screen, login screen, tooltips, dialogs. Every
  surface, every hover state, every transition. This IS InterGenOS.

- **gtk4.css** — The GTK4 / libadwaita theme (1,375 lines). How every
  application looks — buttons, entries, sidebars, cards, tabs, switches,
  popovers, Nautilus file manager, GNOME Settings. The app-level visual
  identity.

- **gtk3.css** — The GTK3 theme (464 lines). Legacy app support (GIMP,
  Inkscape, etc.) matching the GTK4 visual language.

- **index.theme** — The theme metadata file.

## The logo mark

The InterGenOS logo is an ECG heartbeat pulse — a single waveform with
180° symmetry (Q dip mirrors T peak, R peak mirrors S trough). ECG blue
(#0099FF) on pure black. The pulse is the system's heartbeat.

## Core visual rules

From the VISUAL_LANGUAGE.md:

- **Palette:** Three-tier dark (void #050810, surface #0a0e1a, card #0f1525),
  ECG blue (#0099FF), bright blue (#33b1ff), off-white text (#e2e8f0)
- **Core rule: "Glow, don't tint."** — Accents emit light onto surfaces,
  they don't color the surfaces themselves. Borders are near-invisible
  at rest and glow blue on interaction.
- **Blur My Shell** is a hard dependency — transparency and frosted glass
  are foundational, not decorative.
- **No gradients on icons** was the original rule, BUT after studying
  reference themes, we think subtle gradients on icon surfaces (like the
  reference themes use) could work. Open to creative interpretation here.
- **Dark backgrounds are canonical.** The folder will sit on #050810 or
  #000000 in most contexts.

## What we've tried

We've gone through 8 rounds of SVG-generated folder concepts:
- Traditional folders with tabs (too Windows 2000)
- Dark bodies with blue accent lines (too flat, invisible)
- Blue gradient fills with light rays (getting closer but still generic)
- Circuit trace patterns on surfaces (nice touch, not enough on its own)
- ECG pulse as the crease line (cool idea, didn't fit visually)
- Holographic edge glow (interesting direction)

The owner's feedback: "None of these say InterGenOS. They look like they
came from decades ago. There's zero wow factor." The icons need to feel
like they belong to a system that contains an AI — premium, futuristic,
alive, and UNIQUE.

## Reference themes we studied

These dark icon themes showed us what "good" looks like:
- Amy-Dark-Icons — gradient color sweeps, diagonal light reflections, rich
- Gradient-Dark-Icons — multi-color gradients, glossy surfaces, premium
- Vivid-Glassy-Dark-Icons — glassmorphism, frosted surfaces, modern
- Vivid-Dark-Icons — bold gradients with emblematic icons on each folder
- Slot-Beauty-Dark-Icons — 3D spine effect, vivid colors, depth
- Infinity-Dark-Icons — outlined with interior content lines

## What we want from you

Creative folder icon concepts that:

1. Are unmistakably "folder" — the metaphor must read instantly
2. Are unmistakably "InterGenOS" — dark, alive, blue energy, premium
3. Have visual richness — gradients, depth, highlights, personality
4. Feel like 2026, not 2002 — modern, polished, futuristic
5. Would make someone say "wow" when they open their file manager
6. Could scale across folder variants (downloads, documents, music, etc.)
   by swapping an accent color or adding a small emblematic icon

We're open to ANY approach — traditional folder shapes reimagined,
completely new container metaphors, or something we haven't thought of.
The shell theme proves you can take something familiar (GNOME) and make
it entirely your own. The folder icon needs to do the same thing.
