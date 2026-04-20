# First-Boot Greeter / Welcome Experience Research — April 9, 2026

## Source
Research into how major Linux distros handle first-boot onboarding.
Used to inform the design of InterGenOS's intergen-welcome app.

## Distro Comparison

| Feature | g-i-s | Ubuntu | Fedora | Pop!_OS | Mint | elementary | Zorin | Manjaro | Vanilla |
|---------|-------|--------|--------|---------|------|------------|-------|---------|---------|
| Theme/appearance | Basic | Yes | Basic | Yes | Link | Yes | **Best** | Link | Yes |
| App choices | No | Minimal | No | No | Yes | No | No | Yes | **Best** |
| Keyboard/tour | No | Tour | Tour | **Best** | Link | Yes | Yes | Partial | No |
| Privacy/telemetry | Basic | **Yes** | Basic | No | No | No | No | No | No |
| What's included | No | No | No | No | Yes | Partial | Yes | Yes | Partial |
| Docs/community | No | No | No | Link | **Best** | No | Link | Yes | No |
| Visual polish | Meh | OK | Meh | Good | Meh | **Best** | Good | OK | Good |
| Reusable/forkable | Yes | No | Yes | No | Yes | Partial | No | **Yes** | **Yes** |

## Key Findings

### Best Practices Observed

1. **elementary OS** — best visual design, one concept per page, beautiful transitions
2. **Vanilla OS** — best architecture, recipe-driven pages, Python + GTK4/libadwaita
3. **Linux Mint** — best utility, actionable "first steps" checkboxes
4. **Zorin OS** — best appearance picker, layout/look chooser
5. **Pop!_OS** — best keyboard shortcut education (Super+/ overlay)

### Nobody Does What We Can Do

No distro ships a native libadwaita app that:
- Flows from a custom boot animation into the greeter as one continuous experience
- Lets you live-preview and switch between curated theme combos in real-time
- Has a full extension picker with 24 pre-installed options by category
- Does it all over cinematic background art with gradient scrims
- Applies everything to the session in real-time as you click

### Technology Decision

Built from scratch in Python + GTK4 + libadwaita.
NOT forked from Vanilla OS or Manjaro Hello.

**Reasoning (Glasswing):**
- Forking = inheriting code, dependencies, attack surface, update cadence
- A page-based wizard with gsettings toggles is not complex engineering
- Own your code, own your attack surface

## InterGenOS Welcome App — Design

### Pages (7)

1. **Welcome** — brand moment, matches ECG boot animation aesthetic
2. **Appearance** — live theme switcher, 8 curated combos with thumbnails
3. **Extensions** — toggle 24 pre-installed extensions by category
4. **Keyboard Shortcuts** — two-column reference card
5. **Meet InterGen** — AI assistant introduction
6. **Community** — GitHub/docs links
7. **You're All Set** — summary, close

### Technical Details

- File: `assets/intergen-welcome/intergen-welcome.py` (991 lines)
- CSS gradient backgrounds per page (ready for FLUX-generated art)
- Transparent header bars floating over backgrounds
- Extension toggles persist via gsettings on navigation
- Theme combos apply live via Gio.Settings
- Preview thumbnail slots at `assets/intergen-welcome/previews/`

### First-Run Detection

On completion, write `~/.config/intergen-welcome-done`.
Check for this file on startup — exit if present.
Autostart entry: `/etc/xdg/autostart/intergen-welcome.desktop`

### Flow

ECG pulse → "Hello." → "Welcome to InterGenOS." → "Shall we get started?" → GDM login → Welcome greeter
