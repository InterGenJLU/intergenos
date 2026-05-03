#!/bin/bash
# loupe 49.2 — GNOME image viewer
# BLFS 13.0

configure() {
    # Extract pre-vendored Cargo crates so the offline chroot's cargo
    # can resolve crate deps without hitting index.crates.io.
    if [ -f "${IGOS_SOURCES}/loupe-49.2-vendor.tar.gz" ]; then
        tar xf "${IGOS_SOURCES}/loupe-49.2-vendor.tar.gz"

        mkdir -p .cargo
        cat > .cargo/config.toml <<'CARGOEOF'
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "vendor"
CARGOEOF
    fi

    export PATH="/opt/rustc/bin:$PATH"

    mkdir build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          --buildtype=release
}

build() {
    cd build
    export PATH="/opt/rustc/bin:$PATH"
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
    rm -fv ${DESTDIR}/usr/share/applications/org.gnome.eog.desktop 2>/dev/null || true
}
