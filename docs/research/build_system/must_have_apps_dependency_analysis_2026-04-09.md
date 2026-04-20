# Must-Have Apps — Complete Dependency Analysis

**Date:** 2026-04-09
**Prepared by:** InterGenJLU + Claude (Opus 4.6)

## Decision Summary

| App | Decision | Build Time |
|-----|----------|------------|
| GIMP 3.0.6 | Build | ~1.4 SBU |
| Inkscape 1.4.3 | Build | ~2.2 SBU |
| Thunderbird 140.8.0esr | Build | ~14 SBU |
| LibreOffice 26.2.1.2 | Build (--without-java) | ~21 SBU |
| mpv 0.41.0 + Celluloid | Build (replaces VLC) | ~1 SBU |

## Key Decisions

1. **VLC replaced by mpv + Celluloid** — GTK4/libadwaita native, no Qt6, ffmpeg handles
   all codecs internally. Saves 8 packages (liba52, libmad, libmpeg2, FAAD2, libcddb,
   Taglib, utfcpp, VLC itself).

2. **LibreOffice without Java** — Writer, Calc, Impress, Draw all work. Loses Base
   (database app) and help browser. Avoids OpenJDK + apache-ant massive dep chain.
   Can add Java later via binary JDK bootstrap if needed.

3. **Format import libraries included** — libcdr (CorelDRAW), libvisio (Visio),
   libwpg (WordPerfect Graphics). Nearly free once librevenge is in the tree.
   Benefits both Inkscape and LibreOffice.

4. **libheif included for GIMP** — HEIF/HEIC is iPhone's default photo format.
   Real user need.

## Skipped Packages

| Package | Reason |
|---------|--------|
| VLC + 8 codec deps | Replaced by mpv + Celluloid |
| apache-ant + OpenJDK | LibreOffice works without Java |
| Qt6 + KDE Frameworks | Not our desktop, massive chain |
| PostgreSQL, MariaDB | Server databases, overkill for desktop |
| libjxl | Incompatible with GIMP 3.0.6 |
| libplacebo (for VLC) | Broken upstream — but used by mpv (different context) |
| libmfx | Intel-only hardware accel, complex |
| libdv | DV camcorder format — dead tech |
| libcaca | Terminal graphics — novelty |
| libmng | MNG image format — dead format |
| wireless_tools | Legacy WiFi — NetworkManager handles this |
| SANE | Scanner support — niche, add later |
| Gi-DocGen, Doxygen (as dep) | Docs tools — skip per policy |
| Valgrind, GDB | Dev/testing tools — skip per policy |

## Master Package List — 48 Packages (Build Order)

### Tier 0: No dependencies (15 packages)
| # | Package | Version | For | Build System | Source |
|---|---------|---------|-----|--------------|--------|
| 1 | zip | 3.0 | LibreOffice | Make | https://downloads.sourceforge.net/infozip/zip30.tar.gz |
| 2 | cppunit | 1.15.1 | format libs | Autotools | https://sourceforge.net/projects/cppunit/ |
| 3 | libatomic_ops | 7.10.0 | GC, LibreOffice | Autotools | https://github.com/bdwgc/libatomic_ops/releases/download/v7.10.0/libatomic_ops-7.10.0.tar.gz |
| 4 | GLM | 1.0.3 | LibreOffice | Header-only | https://github.com/g-truc/glm/archive/1.0.3/glm-1.0.3.tar.gz |
| 5 | double-conversion | 3.4.0 | Inkscape | CMake | https://github.com/google/double-conversion/archive/v3.4.0/double-conversion-3.4.0.tar.gz |
| 6 | gsl | 2.8 | Inkscape | Autotools | https://ftpmirror.gnu.org/gsl/gsl-2.8.tar.gz |
| 7 | Potrace | 1.16 | Inkscape | Autotools | https://downloads.sourceforge.net/potrace/potrace-1.16.tar.gz |
| 8 | pciutils | 3.14.0 | Thunderbird | Make | https://mj.ucw.cz/download/linux/pci/pciutils-3.14.0.tar.gz |
| 9 | babl | 0.1.122 | GIMP | Meson | https://download.gimp.org/pub/babl/0.1/babl-0.1.122.tar.xz |
| 10 | libmypaint | 1.6.1 | GIMP | Meson | https://github.com/mypaint/libmypaint/releases/download/v1.6.1/libmypaint-1.6.1.tar.xz |
| 11 | libde265 | 1.0.16 | libheif | Autotools | https://github.com/strukturag/libde265/releases/download/v1.0.16/libde265-1.0.16.tar.gz |
| 12 | pyproject-metadata | 0.11.0 | NumPy | Python | PyPI |
| 13 | glad | 2.0.8 | libplacebo | Python | PyPI |
| 14 | luajit | 20260213 | mpv | Make | BLFS |
| 15 | uchardet | 0.0.8 | mpv | CMake | BLFS |

### Tier 1: One dependency (19 packages)
| # | Package | Version | Depends On | For |
|---|---------|---------|-----------|-----|
| 16 | GC | 8.2.12 | libatomic_ops | Inkscape |
| 17 | GLU | 9.0.3 | Mesa (in tree) | LibreOffice |
| 18 | gpgmepp | 2.0.0 | gpgme (in tree) | LibreOffice |
| 19 | CLucene | 2.3.3.4 | boost (in tree) | LibreOffice |
| 20 | unixODBC | 2.3.14 | — | LibreOffice |
| 21 | Raptor2 | 2.0.16 | curl, libxslt (in tree) | Redland |
| 22 | librevenge | 0.0.5 | boost (in tree) | format libs |
| 23 | libdvdnav | 7.0.0 | libdvdread (in tree) | mpv |
| 24 | gegl | 0.4.66 | babl | GIMP |
| 25 | mypaint-brushes | 1.3.1 | libmypaint | GIMP |
| 26 | appstream-glib | 0.8.3 | gtk3 (in tree) | GIMP |
| 27 | libheif | 1.21.2 | libde265 | GIMP |
| 28 | meson_python | 0.19.0 | pyproject-metadata | NumPy |
| 29 | libplacebo | 7.360.0 | glad | mpv |
| 30 | libproxy | 0.5.12 | glib2 (in tree) | Thunderbird |
| 31 | ImageMagick | 7.1.2 | — | Inkscape |
| 32 | gspell | 1.14.2 | enchant (in tree) | Inkscape |
| 33 | Atkmm | 2.28.4 | glibmm (in tree) | Inkscape |
| 34 | Pangomm | 2.46.4 | cairomm (in tree) | Inkscape |

### Tier 2: Two+ dependencies (5 packages)
| # | Package | Version | Depends On | For |
|---|---------|---------|-----------|-----|
| 35 | Rasqal | 0.9.33 | Raptor2 | Redland |
| 36 | libwpd | 0.10.3 | librevenge | format libs |
| 37 | Gtkmm-3 | 3.24.10 | Atkmm, Pangomm-2.46 | Inkscape |
| 38 | NumPy | 2.4.2 | meson_python | Inkscape |
| 39 | mpv | 0.41.0 | libplacebo, ffmpeg (in tree) | media player |

### Tier 3: Deep chain (5 packages)
| # | Package | Version | Depends On | For |
|---|---------|---------|-----------|-----|
| 40 | Redland | 1.0.17 | Rasqal | LibreOffice |
| 41 | libwpg | 0.3.4 | librevenge, libwpd | format libs |
| 42 | libcdr | 0.1.8 | librevenge | format libs |
| 43 | libvisio | 0.1.10 | librevenge | format libs |
| 44 | Celluloid | latest | mpv, gtk4 (in tree) | media player GUI |

### The 4 Must-Have Apps (4 packages)
| # | Package | Version | Build Time |
|---|---------|---------|------------|
| 45 | GIMP | 3.0.6 | ~1.4 SBU |
| 46 | Inkscape | 1.4.3 | ~2.2 SBU |
| 47 | Thunderbird | 140.8.0esr | ~14 SBU |
| 48 | LibreOffice | 26.2.1.2 | ~21 SBU |

## Dependency Policy Applied

| Category | Rule |
|----------|------|
| Required (BLFS) | Always declare. Always enable. No exceptions. |
| Recommended (BLFS) | Always declare if dep is in our tree. |
| Optional — functional | Declare if dep is in our tree ("if you have it, use it"). |
| Optional — docs/tests only | Skip (Doxygen, texlive, gtk-doc, LCOV, Valgrind). |

## Cascading Dependencies Discovered

| Originally Listed | Hidden Deps Found |
|-------------------|-------------------|
| Redland | Raptor2, Rasqal (2 extra packages) |
| libheif | libde265 (1 extra package) |
| Taglib | utfcpp (eliminated — VLC replaced by mpv) |
| mpv | glad, libplacebo (2 packages, but planned) |
| NumPy | meson_python, pyproject-metadata (2 extra packages) |
| apache-ant | OpenJDK (BLOCKED — skipped entirely) |

## Notes

- GTK3 C++ bindings (Atkmm-2.28, Pangomm-2.46, Gtkmm-3.24) coexist with
  GTK4 bindings already in tree — no conflict
- enchant is already in tree — was incorrectly listed as missing initially
- libplacebo appears in both VLC (broken upstream) and mpv (working) contexts —
  for mpv it's a required dep and works fine
- Celluloid is NOT in BLFS — sourced from GitHub directly
