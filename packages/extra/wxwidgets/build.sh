#!/bin/bash
# wxWidgets 3.2.10 — Cross-platform C++ GUI toolkit (GTK 3 backend)
#
# Why this version (pinned 3.2.x maintenance line, latest = 3.2.10):
#   * Audacity 3.7.x is the gating consumer; Audacity stability is the
#     priority and the 3.3.x line (released 2025-10) is too new for the
#     Audacity build to be considered safe — the Audacity changelog
#     specifically references compatibility fixes for 3.2.x but does not
#     yet list 3.3.x as supported, and the Audacity Conan manifest still
#     references the 3.x series.
#   * 3.2.10 is the latest 3.2.x maintenance release (published 2026-03-03,
#     7 maintenance releases beyond 3.2.6 which was the August-2024 baseline
#     identified during earlier feasibility research) and includes the
#     #5598/#5552 GTK detection + compilation fixes plus accumulated bug
#     fixes from 3.2.6 → 3.2.10.
#   * SHA-1 of the downloaded tarball matches upstream's release-notes
#     publication (3c98659f51952da65423904349f8336e2256240d), confirming
#     authenticity beyond just the SHA-256 we record in package.yml.
#
# Components built:
#   * libwx_baseu          — non-GUI core (filesystem, threads, events, etc.)
#   * libwx_baseu_net      — networking
#   * libwx_baseu_xml      — XML (expat-backed)
#   * libwx_gtk3u_core     — GTK 3 widgets
#   * libwx_gtk3u_adv      — advanced GUI widgets
#   * libwx_gtk3u_aui      — Advanced User Interface (docking)
#   * libwx_gtk3u_gl       — wxGLCanvas (OpenGL integration)
#   * libwx_gtk3u_html     — wxHTML rendering
#   * libwx_gtk3u_propgrid — property grid
#   * libwx_gtk3u_qa       — quality-assurance helpers
#   * libwx_gtk3u_ribbon   — ribbon UI
#   * libwx_gtk3u_richtext — rich-text editor
#   * libwx_gtk3u_stc      — Scintilla-based source-code editor
#   * libwx_gtk3u_xrc      — XRC resource loader
#   * wx-config            — pkg-config-equivalent helper used by Audacity
#
# Components SKIPPED (deliberate):
#   * --disable-mediactrl  — Audacity does NOT use wxMediaCtrl (it has its
#     own audio I/O via PortAudio/sndfile); pulling GStreamer into wx just
#     to be unused contradicts the dependency-enablement policy ("use-if-
#     have for optional"). Audacity does not have GStreamer in its dep set.
#   * --disable-webview    — wxWebView requires webkit2gtk; not needed by
#     Audacity and webkit2gtk is not in the desktop tier yet.
#   * --without-libcurl    — wxWebRequest is unused by Audacity. (curl IS
#     in core, but enabling wxWebRequest would link wx clients against
#     libcurl unnecessarily.)
#   * --disable-secretstore — keyring access not needed by Audacity.
#
# System libs preferred (--with-FOO=sys, the configure default but stated
# explicitly so the resolution is unambiguous in build logs):
#   libpng, libjpeg-turbo, libtiff, zlib, expat
# These are all in our package tree (libpng/libjpeg-turbo/libtiff under
# desktop/, zlib/expat under core/) so the bundled 3rdparty/ copies remain
# unused.

configure() {
    # Build in-tree. wxWidgets supports out-of-tree builds but its docs
    # still recommend in-tree for first-time builders, and the autotools
    # set-up is well-tested in this configuration.
    ./configure                        \
        --prefix=/usr                  \
        --with-gtk=3                   \
        --enable-unicode               \
        --enable-shared                \
        --with-opengl                  \
        --with-libpng=sys              \
        --with-libjpeg=sys             \
        --with-libtiff=sys             \
        --with-zlib=sys                \
        --with-expat=sys               \
        --disable-mediactrl            \
        --disable-webview              \
        --without-libcurl              \
        --disable-secretstore
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make install DESTDIR="${DESTDIR}"
}

# do_test:
#   The wxWidgets test suite (tests/ subdir, built with `make -C tests`) is
#   GUI-heavy: many tests instantiate wxApp, open windows, simulate events,
#   and require an X/Wayland display server, an active event loop, and
#   working clipboard / drag-and-drop / IME plumbing. None of this is
#   available inside the build chroot, where there is no display server.
#   Upstream documents this limitation in tests/README and recommends
#   running the suite only on developer workstations with a display.
#
#   Major distributions skip the wxWidgets test suite for the same reason:
#     * Debian's wxwidgets3.2 packaging runs no tests (debian/rules has no
#       override_dh_auto_test target).
#     * Fedora's wxGTK.spec contains no %check stanza.
#     * Arch Linux's wxwidgets3.2 PKGBUILD has no check() function.
#
#   We follow that consensus. The build itself is the verification: a
#   successful configure + compile of all 14 GTK 3 sub-libraries plus
#   wx-config is a strong signal of a correct build, and Audacity's own
#   test suite (when we package Audacity) exercises the wxWidgets API
#   surface that actually matters to InterGenOS users.
do_test() {
    return 0
}
