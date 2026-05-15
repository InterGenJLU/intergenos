#!/bin/bash
# pyelftools 0.32 — Pure-Python ELF + DWARF parser.
#
# Build-time dependency for systemd 259's sd-boot / linuxx64.efi.stub
# generation. Without pyelftools available at systemd configure-time,
# meson_options.txt's `bootloader=auto` silently resolves to disabled
# (per src/systemd/meson.build:1925-1928 — `get_option('bootloader')
# .require(pyelftools.found() and ...)`), and the linuxx64.efi.stub
# never gets built. build-uki.sh then fails much later with "STUB not
# found" — opaque from the user's perspective. Shipping pyelftools
# closes that silent-failure path.
#
# Pure-Python with no compiled components; no runtime deps beyond the
# Python standard library. DESTDIR exception (same as jinja2): pip uses
# --root instead of DESTDIR.

configure() {
    set -e
    : # No configure step
}

build() {
    set -e
    pip3 wheel -w dist --no-cache-dir --no-build-isolation --no-deps "$PWD"
}

check() {
    set -e
    # Smoke-test: import + version probe. Pure-Python module so the
    # only failure mode is install path / Python interpreter shape.
    python3 -c "import elftools, sys; print('pyelftools', elftools.__version__, 'on Python', sys.version.split()[0])"
}

do_install() {
    set -e
    pip3 install --no-index --no-user --root="$DESTDIR" --no-deps \
        --find-links dist pyelftools

    # Ship the InterGenOS-authored man page alongside the module install.
    local pkg_dir
    pkg_dir="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
    install -Dm644 "$pkg_dir/pyelftools.1" \
        "$DESTDIR/usr/share/man/man1/pyelftools.1"
}
