#!/bin/bash
# influxdb 3.9.0 Core — Apache-2.0 OR MIT time-series database
# Upstream: https://github.com/influxdata/influxdb
# License dual: LICENSE-APACHE + LICENSE-MIT in tarball root
#
# Security posture (default-secure, no-tradeoffs):
# - HTTP bind to 127.0.0.1:8181 only (env-file override of upstream
#   0.0.0.0:8181 default)
# - Auth required by default (no INFLUXDB3_START_WITHOUT_AUTH override;
#   operator bootstraps admin token via `influxdb3 create token --admin`)
# - Local-disk object store at /var/lib/influxdb (no cloud uplink)
# - Python plugin runtime DISABLED by default
#   (INFLUXDB3_PACKAGE_MANAGER=disabled in influxdb.env)
# - Full systemd hardening baseline (§5e)
# - AppArmor profile in enforce mode (§5f)
#
# Pre-staged build inputs (mirrored host→chroot via orchestrator
# rsync at --start-at entry):
# - influxdb-3.9.0-vendor.tar.xz       — cargo-vendor output (769
#   crates including the influxdata datafusion + datafusion-udf-wasm
#   git-source forks pinned via Cargo.toml [patch.crates-io])
# - influxdb-3.9.0-wasm-binaries.tar.xz — pre-built WASM ELF binaries
#   for the iox_query_udf crate's build.rs. Upstream's build.rs runs
#   at compile time and downloads these from GitHub releases; we
#   pre-fetch + stage to keep the build offline. See the
#   iox-query-udf-offline-staging.patch for the build.rs env-var
#   override that consumes them.
#
# InfluxDB 3.x design notes (vs 2.x packaging that preceded this):
# - Single binary `influxdb3` with subcommands (serve, create, show, ...)
#   replaces the 2.x split of `influxd` + `influx`
# - No config-file format; daemon configured via flags or INFLUXDB3_* env
#   vars. Operator-tunable config lives in /etc/influxdb/influxdb.env
#   (systemd EnvironmentFile pattern, matches postgresql/mariadb)
# - jemalloc_replacing_malloc + azure/gcp/aws + large-strings are the
#   upstream default-features set; we ship them all (no
#   --no-default-features). The aws/azure/gcp features expand the
#   object-store options at runtime but require operator opt-in via env
#   vars to actually use a cloud bucket; default config uses local disk.
#
# Protobuf dep arrow (v33.5, core/protobuf on master):
# influxdb_iox uses tonic/tonic-build at compile time → protoc from PATH
# protoc v33.5 is installed by core/protobuf. prost-build + tonic-build
# generate from .proto source at build time; no ABI-lock to a specific
# protoc version.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

PKG_USER=influxdb
PKG_GROUP=influxdb

configure() {
    set -e

    # Extract pre-vendored Rust crates (generated via cargo-vendor-gen.sh).
    # The tarball includes .cargo/config.toml with the canonical cargo
    # vendor stdout — for influxdb that's [source.crates-io] + two
    # [source."git+..."] redirects for the influxdata/arrow-datafusion +
    # datafusion-udf-wasm forks + [source.vendored-sources]. We extract
    # with --strip-components=1 so vendor/ and .cargo/ land at cwd; we
    # trust the tarball's config (canonical pattern across all other
    # Rust packages in tree). DO NOT overwrite .cargo/config.toml here —
    # that would wipe the git-source redirects and break --frozen builds.
    if [ ! -f "${IGOS_SOURCES}/influxdb-${PKG_VERSION}-vendor.tar.xz" ]; then
        echo "ERROR: influxdb-${PKG_VERSION}-vendor.tar.xz not found in IGOS_SOURCES"
        echo "Run: scripts/cargo-vendor-gen.sh influxdb ${PKG_VERSION} <github-url>"
        exit 1
    fi
    tar -xf "${IGOS_SOURCES}/influxdb-${PKG_VERSION}-vendor.tar.xz" --strip-components=1

    # Apply offline-build patch to iox_query_udf/build.rs. The patched
    # build.rs honors IOX_QUERY_UDF_STAGED_DIR for pre-fetched wasm
    # binaries; upstream behavior is preserved when the env var is unset.
    patch -p0 < "$BUILD_DIR/iox-query-udf-offline-staging.patch"

    # Extract pre-staged wasm binaries for iox_query_udf. The build.rs
    # checks IOX_QUERY_UDF_STAGED_DIR (exported in build()) for these
    # files + verifies sha256 against the same checksums upstream uses
    # for a fresh download. The tarball contains:
    #   - sha256sum.txt                                                (3.8 KB)
    #   - datafusion_udf_wasm_python.release.x86_64-unknown-linux-gnu.elf (55.7 MB)
    if [ ! -f "${IGOS_SOURCES}/influxdb-${PKG_VERSION}-wasm-binaries.tar.xz" ]; then
        echo "ERROR: influxdb-${PKG_VERSION}-wasm-binaries.tar.xz not found in IGOS_SOURCES"
        echo "Fetch with:"
        echo "  BASE=https://github.com/influxdata/datafusion-udf-wasm/releases/download/wasm-binaries%2F2026-01-28T11-46-19%2F89ab4ae6312c3a44859ddd43d9df4d4300d3086a"
        echo "  curl -L \"\$BASE/sha256sum.txt\" -O"
        echo "  curl -L \"\$BASE/datafusion_udf_wasm_python.release.x86_64-unknown-linux-gnu.elf\" -O"
        echo "  tar -cJf influxdb-${PKG_VERSION}-wasm-binaries.tar.xz sha256sum.txt datafusion_udf_wasm_python.release.x86_64-unknown-linux-gnu.elf"
        exit 1
    fi
    mkdir -p wasm-staging
    tar -xf "${IGOS_SOURCES}/influxdb-${PKG_VERSION}-wasm-binaries.tar.xz" -C wasm-staging

    # Stage wit/world.wit for the vendored datafusion-udf-wasm-host crate.
    # The crate's src/bindings.rs uses wasmtime::component::bindgen! with
    # path = "../wit/world.wit" — relative to CARGO_MANIFEST_DIR. In the
    # upstream repo (influxdata/datafusion-udf-wasm) `host/` and `wit/`
    # are sibling directories, so the path resolves correctly. When cargo
    # vendor packs the `host` crate alone, the sibling `wit/` directory
    # is lost; the bindgen! macro then produces empty bindings → the
    # `exports` module is missing → cascade of E0433 + E0277 compile
    # errors across conversion/mod.rs, component.rs, error.rs, udf.rs.
    #
    # Fix: recreate the sibling directory inside vendor/ so the relative
    # path `../wit/world.wit` from the vendored crate manifest resolves
    # to the .wit file. world.wit is shipped in this package directory
    # (datafusion-udf-wasm-host.wit, sha256 565f97f6...) — fetched from
    # the upstream datafusion-udf-wasm repo at the same commit pinned in
    # iox_query_udf/build.rs (89ab4ae6...).
    mkdir -p vendor/wit
    install -m644 "$BUILD_DIR/datafusion-udf-wasm-host.wit" vendor/wit/world.wit
}

build() {
    set -e
    # Upstream's default feature set:
    #   jemalloc_replacing_malloc + azure + gcp + aws + large-strings
    # All of these are wanted shipped surface. Python plugin runtime is
    # gated at runtime via INFLUXDB3_PACKAGE_MANAGER, NOT at compile time.
    #
    # IOX_QUERY_UDF_STAGED_DIR points the patched build.rs at the
    # extracted wasm binaries staged in configure(). Absolute path is
    # required (cargo runs the build script in OUT_DIR, not cwd).
    export IOX_QUERY_UDF_STAGED_DIR="$(pwd)/wasm-staging"

    # influxdb3_process/build.rs embeds the upstream git commit hash
    # into the binary for `influxdb3 --version` output. Upstream relies
    # on `git rev-parse HEAD` to read it from the working tree, but
    # we build from a source tarball (no .git/), so the build.rs panics
    # with "attempting to embed empty git hash" unless GIT_HASH is
    # provided via env. We supply the upstream v${PKG_VERSION} tag's
    # commit sha — same value `git rev-parse HEAD` would produce if
    # the operator had run `git clone + git checkout v${PKG_VERSION}`.
    export GIT_HASH=0f1816e0690bbf547ebfefd13d939cfa1de71cb2

    cargo build --release --frozen --offline --bin influxdb3
}

do_install() {
    set -e

    # Single binary in InfluxDB 3.x — both daemon (`influxdb3 serve`) and
    # admin CLI (`influxdb3 create token --admin`, etc.) are subcommands.
    install -Dm755 target/release/influxdb3 "$DESTDIR/usr/bin/influxdb3"

    # Env file (replaces the 2.x-style influxdb.conf — InfluxDB 3 has no
    # config-file format; configured via flags or INFLUXDB3_* env vars).
    install -Dm640 "$BUILD_DIR/influxdb.env" "$DESTDIR/etc/influxdb/influxdb.env"

    # State + log + runtime directories
    install -d -m 750 "$DESTDIR/var/lib/influxdb"
    install -d -m 750 "$DESTDIR/var/log/influxdb"
    install -d -m 755 "$DESTDIR/run/influxdb"

    # systemd unit
    install -Dm644 "$BUILD_DIR/influxdb.service" \
        "$DESTDIR/usr/lib/systemd/system/influxdb.service"

    # AppArmor profile (enforce mode)
    install -Dm644 "$BUILD_DIR/usr.bin.influxdb3" \
        "$DESTDIR/etc/apparmor.d/usr.bin.influxdb3"
}

post_install() {
    set -e
    if ! getent group "$PKG_GROUP" >/dev/null; then
        groupadd -r "$PKG_GROUP"
    fi
    if ! getent passwd "$PKG_USER" >/dev/null; then
        useradd -r -g "$PKG_GROUP" -d /var/lib/influxdb -s /sbin/nologin "$PKG_USER"
    fi
    chown -R "$PKG_USER":"$PKG_GROUP" \
        /var/lib/influxdb /var/log/influxdb /run/influxdb /etc/influxdb
    systemctl daemon-reload 2>/dev/null || true
    apparmor_parser -r /etc/apparmor.d/usr.bin.influxdb3 2>/dev/null || true
}
