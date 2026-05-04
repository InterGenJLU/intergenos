#!/bin/bash
# rhythmbox 3.4.9 — GNOME music player and library manager
#
# Upstream: https://gitlab.gnome.org/GNOME/rhythmbox
# Build doc reference: README.md (upstream tarball) — Rhythmbox is not
# currently in the BLFS book, so this recipe follows the upstream
# README's "Simple install procedure" with InterGenOS conventions
# (system /usr prefix, /usr/lib libdir, release buildtype).
#
# Required deps (meson.build, "required: true"):
#   glib2 >= 2.66, gtk3 >= 3.16, libsoup-3.0 >= 3.0.7, gstreamer >= 1.4,
#   gst-plugins-base, totem-plparser >= 3.2.0, libpeas-1.0 >= 0.7.3,
#   json-glib, libxml2 >= 2.7.8, tdb >= 1.2.6, gettext >= 0.20,
#   gobject-introspection >= 0.10
#
# Optional deps explicitly DISABLED in this build (not in tree):
#   brasero (CD burning), lirc (IR remote), libgpod (iPod), libmtp,
#   libdmapsharing (DAAP), check (libcheck for unit tests),
#   gst-plugins-ugly + gst-libav (codec coverage; user can install
#   later for additional codec support — runtime-discovered)
#
# Optional deps ENABLED via auto-detection (present in tree):
#   libgudev (hardware detection), libnotify (desktop notifications),
#   libsecret (keyring), grilo (media discovery), python+pygobject3
#   (Python plugin support), vala (Vala plugin support)

configure() {
    mkdir build
    cd    build

    # meson_options.txt features explicitly pinned:
    #   - tests=disabled       — libcheck not in tree; tests would error
    #                            out under feature=auto when check missing
    #   - brasero=disabled     — libbrasero-media not in tree
    #   - lirc=disabled        — lirc not in tree
    #   - ipod=disabled        — libgpod not in tree
    #   - mtp=disabled         — libmtp not in tree
    #   - daap=disabled        — already disabled by upstream default,
    #                            re-affirmed for clarity
    #   - sample-plugins=false — example code, not for distribution
    #   - help=true            — install yelp user docs (itstool present)
    #   - apidoc=false         — gi-docgen optional, skip API docs
    #
    # Auto-detected (left at default 'auto'): libnotify, libsecret,
    # grilo, gudev, fm_radio, plugins_vala
    #
    # plugins_python=disabled — Rhythmbox 3.4.9 errors at meson configure
    # when pygobject >= 3.53 is paired with libpeas <= 1.36 (girepository
    # 1.0/2.0 ABI clash; meson check at meson.build:155-159 fires).
    # Our pygobject3 is 3.54.5 and libpeas is 1.36.0 (the LAST 1.x release
    # — upstream went 1.36.0 → 2.0 directly), so we hit the guard.
    # Per use-if-have policy (feedback_dependency_policy.md) this is a
    # permitted configure-off: the dep stack genuinely doesn't compose at
    # the ABI level. Trade-off: no Rhythmbox Python plugins (LastFM
    # scrobbler, Magnatune, Jamendo). Revisit when Rhythmbox 4.x lands
    # with libpeas 2.0 support.
    meson setup ..                       \
          --prefix=/usr                  \
          --libdir=/usr/lib              \
          --buildtype=release            \
          -Dtests=disabled               \
          -Dbrasero=disabled             \
          -Dlirc=disabled                \
          -Dipod=disabled                \
          -Dmtp=disabled                 \
          -Ddaap=disabled                \
          -Dsample-plugins=false         \
          -Dhelp=true                    \
          -Dapidoc=false                 \
          -Dplugins_python=disabled
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
    # Rhythmbox's meson.build invokes gnome.post_install() which already
    # runs glib-compile-schemas, gtk-update-icon-cache, and
    # update-desktop-database against DESTDIR during `ninja install`.
    # Re-run them on the live filesystem so installed schemas/icons take
    # effect immediately without requiring a session restart.
    # Best-effort: missing tools are non-fatal during chroot install.
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database -q 2>/dev/null || true

    # Verify upstream-shipped man pages installed by `ninja install`
    # (data/rhythmbox.1, data/rhythmbox-client.1).
    for m in rhythmbox.1 rhythmbox-client.1; do
        if [ ! -f /usr/share/man/man1/$m ] && [ ! -f /usr/share/man/man1/$m.gz ]; then
            echo "WARN: expected man page $m not found in /usr/share/man/man1/" >&2
        fi
    done
}

# do_test:
#   Upstream tests live under tests/ and require libcheck (not in tree).
#   We pin -Dtests=disabled in configure(), which removes the test
#   subdir from the build graph; `meson test` would have nothing to run.
#   Smoke testing happens at first launch (desktop entry + icon).
#   Re-evaluate when libcheck lands.
do_test() {
    return 0
}
