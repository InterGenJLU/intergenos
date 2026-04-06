#!/bin/bash
# glycin 2.0.8 — Sandboxed image loading library for GNOME
# BLFS 13.0
# Requires pre-vendored Rust crates (glycin-2.0.8-vendor.tar.gz)

configure() {
    # Extract pre-vendored Rust crates FIRST (patch needs vendor/ directory)
    if [ -f "${IGOS_SOURCES}/glycin-2.0.8-vendor.tar.gz" ]; then
        tar xf "${IGOS_SOURCES}/glycin-2.0.8-vendor.tar.gz"

        # Configure cargo to use vendored crates
        mkdir -p .cargo
        cat > .cargo/config.toml << 'CARGOEOF'
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "vendor"
CARGOEOF
    fi

    # Apply XBM/XPM support patch AFTER vendor extraction
    # (patch modifies files in vendor/ directory)
    patch -Np1 --forward -i "${IGOS_SOURCES}/glycin-2.0.8-xbm_xpm-1.patch" || true

    export PATH="/opt/rustc/bin:$PATH"

    mkdir build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
          --buildtype=release   \
          -D libglycin-gtk4=false \
          -D tests=false
}

build() {
    cd build
    export PATH="/opt/rustc/bin:$PATH"
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
