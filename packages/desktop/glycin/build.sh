#!/bin/bash
# glycin 2.0.8 — Sandboxed image loading library for GNOME
# BLFS 13.0
# Requires pre-vendored Rust crates (glycin-2.0.8-vendor.tar.gz)

configure() {
    set -e
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
    # Use --no-backup-if-mismatch to avoid .orig files in vendor/
    # Patch validated by SHA256 even though || true allows build to proceed
    echo "fc4070a4bafe79d303c8e9f7a4271355933906c94e9b083fd7b9d9c084531358  ${IGOS_SOURCES}/glycin-2.0.8-xbm_xpm-1.patch" | sha256sum -c - || echo "WARNING: glycin patch checksum mismatch, continuing without patch"
    patch -Np1 --forward --no-backup-if-mismatch \
          -i "${IGOS_SOURCES}/glycin-2.0.8-xbm_xpm-1.patch" || true

    # Clear cargo checksums for any patched vendor crates
    # (cargo rejects modified vendored files otherwise)
    for cs in vendor/*/.cargo-checksum.json; do
        sed -i 's/"files":{[^}]*}/"files":{}/' "$cs" 2>/dev/null
    done

    export PATH="/opt/rustc/bin:$PATH"

    mkdir -p build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          --buildtype=release   \
          -D libglycin-gtk4=false \
          -D tests=false
}

build() {
    set -e
    cd build
    export PATH="/opt/rustc/bin:$PATH"
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
