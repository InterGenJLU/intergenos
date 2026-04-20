# Dependency Cycle Break Audit — 2026-04-08

After adding 537 BLFS-derived dependencies to 164 package.yml files, the graph resolver detected multiple cycles. Each cycle was evaluated and broken at the weakest link. This document records every removal and its justification.

## Categories

- **Wrong direction**: BLFS parser artifact — the real dependency goes the other way
- **Runtime only**: Found at runtime, not needed to compile
- **Docs/tests only**: Only needed for documentation or test suites
- **Pass2 created**: Proper fix via rebuild after the other side exists
- **Niche/optional**: Feature removed is extremely niche and not needed for desktop use

---

## Wrong Direction (dep reversed — no functionality loss)

| Package | Removed dep | Reason |
|---------|-------------|--------|
| libxcb | mesa | Mesa depends on libxcb, not vice versa |
| libxkbcommon | xwayland | XWayland depends on libxkbcommon, not vice versa |
| xkeyboard-config | libxkbcommon | libxkbcommon compiles xkeyboard-config data, not vice versa |
| colord | colord-gtk | colord-gtk provides GTK widgets for colord, depends ON colord |
| tinysparql | localsearch | localsearch uses tinysparql's API, not vice versa |
| xdg-desktop-portal | xdg-desktop-portal-gnome | Backend depends on portal, not vice versa |
| xdg-desktop-portal | xdg-desktop-portal-gtk | Backend depends on portal, not vice versa |
| pipewire | wireplumber | WirePlumber is a session manager ON TOP of PipeWire |
| graphite2 | freetype2, harfbuzz | BLFS: optional for tests only. Graphite2 is standalone |

## Runtime Only (not needed at build time — no functionality loss)

| Package | Removed dep | Reason |
|---------|-------------|--------|
| gtk3 | adwaita-icon-theme | Icons found at runtime, not needed to compile GTK |
| gtk4 | adwaita-icon-theme | Same — runtime theme lookup |
| gnome-settings-daemon | mutter | g-s-d runs inside mutter compositor at runtime |

## Docs/Tests Only (per policy — no functionality loss)

| Package | Removed dep | Reason |
|---------|-------------|--------|
| libxml2 | doxygen | API documentation generation |
| llvm | pygments | Syntax highlighting for LLVM docs/tests |
| libxslt | docbook-xml, docbook-xsl-nons | Man page generation for xsltproc |
| cairo | ghostscript | PostScript testing |

## Pass2 Created (proper cycle break — no functionality loss)

| Package | Cycle | Pass2 package |
|---------|-------|---------------|
| libtiff ↔ libwebp | libtiff needs libwebp, libwebp needs libtiff | libtiff-pass2 rebuilds with libwebp after libwebp exists |
| lame ↔ libsndfile | lame needs libsndfile for multi-format input, libsndfile needs lame for MP3 output | lame-pass2 rebuilds with libsndfile for FLAC/AIFF/OGG input |

## Functionality Evaluated and Accepted

These removals lose optional functionality that was evaluated as acceptable for our use case.

| Package | Removed dep | Lost functionality | Justification |
|---------|-------------|-------------------|---------------|
| harfbuzz | cairo | cairo-ft rendering backend | harfbuzz builds before cairo in current order — was never enabled. Most consumers (Pango) use their own rendering |
| harfbuzz | freetype2 | Final freetype2 | Already uses freetype2-pass1 which provides identical API |
| ~~lame~~ | ~~libsndfile~~ | ~~FLAC/AIFF input~~ | **FIXED: lame-pass2 created — multi-format input restored** |
| cairo | librsvg | SVG surface support | Wrong direction — librsvg uses cairo for rendering. Cairo writes SVG output natively |
| cairo | poppler | PDF surface support | Wrong direction — poppler uses cairo for rendering |
| cups | colord | Printer color management | Most users don't calibrate printers. Color-critical users can rebuild CUPS with colord |
| colord | gnome-desktop | GNOME integration in colord | Optional integration, colord functions fully without it |
| avahi | gtk3 | avahi-discover GUI tool | Avahi daemon (mDNS) works fully without GTK. GUI diagnostic tool is niche |
| cyrus-sasl | mitkrb | Kerberos (GSSAPI) auth | Enterprise feature. Desktop SASL uses PLAIN/LOGIN/DIGEST-MD5. Kerberos auth typically goes through PAM+SSSD |
| gcr | gnupg2 | Runtime crypto operations | gcr can display crypto info and manage keys without gnupg2 at build time. gnupg2 is available at runtime |
| gcr4 | gnupg2 | Same as gcr | Same reasoning |
| gstreamer | gtk3 | GTK3 video sink plugin | GStreamer core doesn't need GTK. Video sinks are loaded as plugins at runtime |
| gst-plugins-base | gtk3 | GTK3 video sink plugin | Same — plugin-based, found at runtime |
| mpg123 | pulseaudio | PulseAudio audio output | mpg123 outputs via ALSA directly. PulseAudio is a layer above ALSA |
| graphviz | cups, webkitgtk-gtk3 | Print-to-printer, web rendering | Graphviz renders to PDF/SVG/PNG via cairo. Direct-to-printer and WebKit are extremely niche |

## Summary

- **Total deps added**: 517 (after cycle removals)
- **Cycle breaks — no loss**: 15 (wrong direction, runtime, docs)
- **Cycle breaks — pass2**: 2 (libtiff-pass2, lame-pass2)
- **Cycle breaks — accepted loss**: 15 (all evaluated, all niche/optional)
- **Graph result**: 543 packages, 0 cycles, 0 missing deps
