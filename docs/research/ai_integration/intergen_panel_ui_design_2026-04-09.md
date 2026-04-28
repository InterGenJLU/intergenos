# InterGen Panel — UI Design Research

**Date:** 2026-04-09

## Recommended Architecture: Hybrid (Extension + GTK4 App)

### Why Hybrid?
- GNOME Shell extension: panel indicator, keyboard shortcuts, always-on-top, blur
- GTK4/libadwaita app: rich text, code blocks, proper scrolling, theme inheritance
- D-Bus connects them (proven pattern from ddterm and the prior assistant's desktop bridge)

### Why NOT pure extension?
- St widgets can't render code blocks, markdown, or rich text
- Network I/O in shell's main loop freezes the desktop

### Why NOT pure GTK4?
- gtk4-layer-shell does NOT work on GNOME/Mutter
- Can't do always-on-top, panel indicator, or global shortcuts from GTK4 on Wayland

## Dock/Float Behavior

**Docked states (edge-snapped):**
- Right edge → sidebar (desktop adjusts via struts)
- Left edge → sidebar
- Bottom → drawer
- Top → dropdown

**Floating state:**
- Drag away from edge → free-floating window
- Desktop reclaims reserved space
- User can resize, stays on top

**Implementation:** GNOME Shell extension manages struts (reserved space) when
docked, releases them when floating. GTK4 app handles rendering.

## Visual Design

**Panel indicator:** ECG pulse icon (22x22 SVG)
- Green pulse: running/ready
- Amber pulse: thinking/processing
- Gray flat line: offline

**Chat panel:** 360px wide × 520px tall default
- Frameless AdwWindow with drag handle
- User bubbles right-aligned (#1e3a5f bg, #2563eb border)
- Assistant bubbles left-aligned (#1a2236 bg, rgba(56,189,248,0.15) border)
- Background: rgba(26,26,46,0.92) with blur-behind
- ECG pulse animation as thinking indicator
- Code blocks with GtkSourceView syntax highlighting
- Input: single line expanding to multi-line

**Colors (Orchis-Dark + InterGenOS brand):**
- Panel bg: rgba(26,26,46,0.92)
- Header: #16213e
- Text: #e2e8f0
- Accent: #38bdf8
- ECG pulse: #38bdf8

## Architecture Diagram

```
GNOME Shell Extension (intergen@intergenos.com)
├── Panel indicator (ECG icon, status colors)
├── Click: toggle chat panel
├── Keyboard shortcut: Super+I
├── Window management: always-on-top, struts for docking
├── Blur-behind effect
└── D-Bus → com.intergenos.InterGen.Panel
        │
        ▼
InterGen GTK4 Chat Panel (standalone app)
├── Frameless AdwWindow, draggable, resizable
├── Chat messages (Gtk.ListView or Box in ScrolledWindow)
├── Code blocks (GtkSourceView 5)
├── ECG thinking indicator (Cairo drawing)
├── Input area (Gtk.TextView + send button)
├── Dock/float state management
└── D-Bus → com.intergenos.InterGen (daemon)
        │
        ▼
InterGen Daemon (Python, systemd user service)
├── Conversation Router (from prior assistant)
├── Semantic Matcher (embeddings)
├── LLM Interface (llama.cpp)
├── Tool Registry (built-in + MCP)
├── MCP Client Bridge
└── System tools
```

## Dependencies (all already in desktop tier)
- Python 3 + PyGObject
- GTK4 + libadwaita
- GtkSourceView 5
- Cairo
- Pango

## Prior Art Referenced
- ddterm: hybrid extension + GTK app pattern (most popular GNOME terminal)
- Floating Mini Panel: draggable panel via Clutter events
- Prior assistant's desktop bridge: D-Bus extension pattern
- Prior assistant's web UI CSS: color scheme and message styling
- Newelle: GTK4/libadwaita AI chat app for GNOME
- Windows Copilot: sidebar + floating panel dual-mode
