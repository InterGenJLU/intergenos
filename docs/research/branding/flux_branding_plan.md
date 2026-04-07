# InterGenOS Visual Branding Plan — FLUX.2 Image Generation

## Context

Every visual element from power-on to desktop should be deliberately designed, not stock art. FLUX.2-klein-4B runs locally on the RX 7900 XT via the JARVIS GPU swap system. All imagery is generated on our hardware — no licensed assets, no stock photos.

**FLUX Server:** `uvicorn services.flux_server:app --host 127.0.0.1 --port 8190`
**API:** POST `http://127.0.0.1:8190/generate` with `{prompt, width, height, steps, seed}`
**Model:** `/mnt/jarvis-storage/jarvis/models/flux/FLUX.2-klein-4B/`
**Output:** `/home/christopher/jarvis/generated_images/`

---

## Brand Identity

Before generating any images, we need a consistent visual language.

**Color palette:**
- Primary: Deep blue-black (#0f1117) — the void, control, trust
- Accent: Green (#4ade80) — growth, system health, terminal aesthetic
- Secondary: Cool gray (#9ca3af) — neutral surfaces
- Warning: Amber (#f59e0b)
- Error: Red (#ef4444)

**Visual motifs:**
- Circuit-meets-nature: technology as something organic and understandable
- Clean geometry with subtle depth
- Dark backgrounds — this is a power-user OS, not a toy

**Typography note:** FLUX generates images, not text. Any text overlay (logo wordmark, version strings) will be composited in post using Inkscape/ImageMagick with our chosen font.

---

## Assets to Generate

### 1. Logo Mark (icon only, no text)

**Purpose:** App icon, favicon, boot splash center, grub menu icon
**Dimensions:** 1024x1024 (will be scaled down to 512, 256, 128, 64, 48, 32, 16)
**Variants needed:** Full color on transparent, monochrome white, monochrome black

**Prompt:**
```
A minimalist geometric logo mark for a Linux operating system called InterGenOS. 
Abstract interlocking hexagonal shapes suggesting interconnected generations of 
technology. Deep blue-black background with glowing green (#4ade80) accent lines. 
Clean vector-style rendering, no text, no gradients, sharp edges, symmetric design. 
Professional software company logo, icon only.
```

**Parameters:** 1024x1024, steps=30, generate 10 variants (seeds 1-10)

---

### 2. GRUB Bootloader Background

**Purpose:** Background image shown during OS selection at boot
**Dimensions:** 1920x1080
**Requirements:** Dark, uncluttered, text must be readable over it. Logo area top-left or centered.

**Prompt:**
```
Dark futuristic desktop wallpaper for a Linux bootloader screen. Deep blue-black 
background (#0f1117) with subtle geometric circuit board patterns fading into the 
edges. Faint green (#4ade80) accent lines tracing minimal pathways across the lower 
third. Extremely minimal, almost entirely dark with sparse detail. No text, no 
logos. Professional, clean, understated. 8K quality render.
```

**Parameters:** 1920x1080, steps=25, generate 5 variants

---

### 3. Plymouth Boot Splash Background

**Purpose:** Shown during boot while systemd starts services
**Dimensions:** 1920x1080
**Requirements:** Very dark center area where the spinner/progress animation will overlay. Subtle edge detail.

**Prompt:**
```
Ultra-dark minimal boot splash background for a Linux operating system. Nearly 
pure black center with very subtle dark blue (#0f1117) geometric patterns radiating 
outward from center. Faint green (#4ade80) particle traces at extreme edges only. 
The center 40% must be almost completely black for logo overlay. Cinematic, 
professional. No text, no logos. 8K render.
```

**Parameters:** 1920x1080, steps=25, generate 5 variants

---

### 4. GDM Login Screen Background

**Purpose:** GNOME Display Manager login/lock screen
**Dimensions:** 1920x1080 (also generate 2560x1440 and 3840x2160)
**Requirements:** Must work with GDM's white text overlay. Dark with character.

**Prompt:**
```
Sophisticated dark wallpaper for a Linux login screen. Abstract landscape of 
geometric mountain-like formations rendered in dark blue-gray tones against a 
near-black sky. Single thin green (#4ade80) horizon line. Atmospheric, moody, 
minimal. Suggests depth and stability. No stars, no text, no logos. Professional 
quality, photorealistic lighting on abstract geometry.
```

**Parameters:** 1920x1080 (then upscale for 1440p/4K), steps=30, generate 5 variants

---

### 5. Desktop Wallpaper (Default)

**Purpose:** Default GNOME desktop background
**Dimensions:** 3840x2160 (4K native, scales down)
**Requirements:** Attractive but not distracting. Works with dark GNOME theme.

**Prompt v1 (Abstract):**
```
Abstract desktop wallpaper for a dark-themed Linux desktop. Flowing organic 
curves in deep navy and charcoal, with subtle green (#4ade80) bioluminescent 
accents. Feels like deep ocean or deep space. Smooth gradients, no hard edges. 
Calm, professional, beautiful. No text, no logos. 8K photorealistic render.
```

**Prompt v2 (Geometric):**
```
Geometric desktop wallpaper for a Linux operating system. Isometric grid of 
translucent dark hexagons receding into depth, lit from below by diffuse green 
(#4ade80) light. Dark blue-black (#0f1117) background. Clean, architectural, 
precise. Suggests structure and transparency. No text, no logos. 8K quality.
```

**Parameters:** 2048x2048 (FLUX max, then crop/upscale to 4K), steps=30, generate 5 of each

---

### 6. rEFInd Boot Manager Background

**Purpose:** UEFI boot manager OS selection screen (pre-GRUB)
**Dimensions:** 1920x1080
**Requirements:** Very dark, rEFInd draws OS icons over this

**Prompt:**
```
Extremely minimal dark background for a UEFI boot manager. Near-black (#0a0c10) 
with barely visible diagonal line pattern suggesting brushed metal. Single very 
faint green (#4ade80) horizontal rule across the lower quarter. Nothing else. 
Ultra-clean, ultra-dark, professional. No text, no logos.
```

**Parameters:** 1920x1080, steps=20, generate 3 variants

---

### 7. Installer (Forge) Background

**Purpose:** Background for the GTK4 GUI installer
**Dimensions:** 1920x1080
**Requirements:** Must work under the installer UI panels (dark with translucent overlays)

**Prompt:**
```
Dark application background for a Linux system installer. Soft gradient from 
deep navy (#0f1117) at top to slightly lighter dark blue (#1a1e2e) at bottom. 
Very subtle abstract topographic contour lines in slightly lighter shade, 
suggesting terrain being mapped. Minimal, professional, calming. Suggests 
precision and careful planning. No text, no logos. 8K quality.
```

**Parameters:** 1920x1080, steps=25, generate 3 variants

---

### 8. OS Selection Icon (for rEFInd/GRUB)

**Purpose:** Small icon representing InterGenOS in multi-boot menus
**Dimensions:** 512x512 (scales to 128x128 for rEFInd)
**Requirements:** Recognizable at small sizes, works on dark backgrounds

**Prompt:**
```
A simple, bold icon for a Linux operating system. Glowing green (#4ade80) 
interlocking geometric shape on pure black background. Must be instantly 
recognizable at 32x32 pixels. Think app icon — simple, bold, one shape. 
No text, no gradients, flat design with subtle glow. Clean vector style.
```

**Parameters:** 1024x1024, steps=25, generate 10 variants

---

## Generation Workflow

### Phase 1: Logo (do first — informs everything else)
1. Generate 10 logo variants
2. Owner selects the best direction
3. Refine with seed variations and prompt tweaks
4. Final logo vectorized in Inkscape for scalability
5. Text wordmark "InterGenOS" added in chosen font

### Phase 2: Boot chain (GRUB → Plymouth → GDM)
1. Generate variants for each stage
2. Owner selects, we refine
3. Apply to config files (GRUB theme, Plymouth theme, GDM dconf)

### Phase 3: Desktop + Installer
1. Generate wallpaper variants
2. Apply as gsettings default
3. Generate installer background
4. Apply to GTK4 installer CSS

### Phase 4: rEFInd (only if dual-boot is used)
1. Generate boot manager background + OS icon
2. Apply to rEFInd theme config

---

## Post-Processing Pipeline

FLUX generates raster images. Each asset needs post-processing:

1. **Logo:** Trace to SVG in Inkscape → export all sizes (16-1024px)
2. **Backgrounds:** Color-correct to exact palette → export at target resolutions
3. **Plymouth:** Extract background, create spinner frames, package as Plymouth theme
4. **GRUB:** Package as GRUB theme directory (background.png + theme.txt)
5. **GDM:** Set via gsettings/dconf override in `config/gsettings/`
6. **Wallpaper:** Set as default in gsettings-desktop-schemas override

**Tools needed (all in our desktop build):**
- Inkscape (SVG editing, raster trace)
- ImageMagick (resize, crop, format conversion)
- GIMP (if fine-tuning needed)

---

## Photorealism Techniques for FLUX.2-Klein

*Source: ChatGPT analysis of FLUX.2-Klein behavior*

The key insight: photo-realism with Klein is about **convincing the model it's generating a photograph, not an image.** That means camera physics, lighting realism, material detail, and imperfections.

### Layered Prompt Structure

Don't write flat sentences. Structure prompts in layers:

```
[SUBJECT + ACTION]
[ENVIRONMENT + CONTEXT]
[CAMERA + LENS + SETTINGS]
[LIGHTING MODEL]
[MATERIAL / TEXTURE DETAILS]
[REALISM ANCHORS]
[NEGATIVE SPACE / COMPOSITION]
```

### Critical Realism Tokens

These terms are disproportionately effective:

**Camera anchors:** `RAW photo`, `full-frame DSLR`, `50mm lens`, `85mm lens`, `shallow depth of field`, `bokeh`, `ISO 100`, `f/1.8`

**Lighting physics:** `global illumination`, `physically accurate lighting`, `soft shadows`, `natural light`, `window light`

**Surface realism:** `skin texture`, `pores`, `micro-details`, `subsurface scattering`, `fabric texture`, `imperfections`

**Output constraints:** `photorealistic`, `high dynamic range`, `unprocessed`, `no CGI`, `no illustration`

### Negative Prompting

Explicit exclusion makes a significant difference:
```
cartoon, illustration, CGI, 3D render, oversaturated,
plastic skin, smooth skin, blurry, low detail,
unrealistic lighting, fake shadows, distorted anatomy
```

### Step Count and Parameters

- **50 steps** is the sweet spot for Klein — beyond 60 is diminishing returns
- If realism is lacking, it's prompt structure or lighting, not step count
- **CFG guidance scale:** 5.5-7 (too high = artificial "AI look", too low = prompt drift)
- **Sampler:** DPM++ 2M Karras (stable) or Euler a (softer, sometimes more natural)

### Composition Tricks

- Add **imperfection cues** (slightly messy, wrinkled, worn)
- Avoid symmetry
- Include **depth cues** (foreground blur, background separation)
- Use **real-world constraints** (shot through window, reflections on glass)

### Advanced Pushes

- **Film simulation:** `Kodak Portra 400` for warm naturalism
- **Lens artifacts:** `chromatic aberration`, `vignetting` for realism
- **Environmental interaction:** `dust in air`, `light rays`

### When Klein Looks "Off"

| Problem | Fix |
|---------|-----|
| Overly clean surfaces | Add imperfections explicitly |
| Lighting too perfect | Specify light source + falloff |
| Skin looks waxy | Demand pores + texture |
| Too centered | Enforce composition rules |

### Applicability to Our Assets

Most of our branding assets are **abstract/geometric, not photorealistic** — so the full camera pipeline doesn't apply to everything. But these techniques are directly relevant for:

- **Desktop wallpaper** (the abstract-organic variant benefits from lighting physics and depth cues)
- **GDM login screen** (atmospheric, moody — benefits from photorealistic lighting on abstract geometry)
- **Any future promotional screenshots** showing InterGenOS in use

For the logo, boot splash, and GRUB background, the prompts are already tuned for clean vector/geometric output. The photorealism techniques above should be layered in selectively where they add depth without fighting the minimal aesthetic.

---

## FLUX API Call Template

```bash
# Generate a single image
curl -X POST http://127.0.0.1:8190/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "YOUR PROMPT HERE",
    "width": 1920,
    "height": 1080,
    "steps": 25,
    "seed": 42
  }'

# Batch generate 10 variants
for seed in $(seq 1 10); do
  curl -X POST http://127.0.0.1:8190/generate \
    -H "Content-Type: application/json" \
    -d "{
      \"prompt\": \"YOUR PROMPT HERE\",
      \"width\": 1024,
      \"height\": 1024,
      \"steps\": 30,
      \"seed\": $seed
    }"
done
```

---

## Notes

- FLUX.2-klein-4B max resolution is ~2048x2048. Larger sizes need upscaling (Real-ESRGAN or similar).
- The RX 7900 XT has 20GB VRAM — sufficient for 2048x2048 at BF16.
- GPU swap: FLUX server must be running (llama-server stopped). JARVIS handles this automatically.
- Expect ~15-30 seconds per image at 1024x1024 with 25 steps.
- All generated images go to `/home/christopher/intergenos/research/branding/` for review, not directly into the repo.
