#!/bin/bash
# influxdb 3.9.0 Core — Apache-2.0 OR MIT time-series database
# Upstream: https://github.com/influxdata/influxdb
# License dual: LICENSE-APACHE + LICENSE-MIT in tarball root
# First consumer of cargo-vendor + protobuf (Wave 1b → Wave 2 arrow)
#
# Security posture (default-secure, no-tradeoffs):
# - Bind 127.0.0.1 only per shipped config
# - Full systemd hardening baseline (§5e)
# - AppArmor profile in enforce mode (§5f)
# - Python plugin runtime: off by default, AppArmor-constrained
#
# Protobuf dep arrow (v33.5, core/protobuf on master):
# influxdb_iox uses tonic/tonic-build at compile time → protoc from PATH
# protoc v33.5 is installed by core/protobuf. Wire format version-agnostic.
# Verified: prost-build + tonic-build generate from .proto source at build
# time using whichever protoc is on PATH — no ABI-lock to a specific version.

PKG_USER=influxdb
PKG_GROUP=influxdb
STATE_DIR=/var/lib/influxdb
LOG_DIR=/var/log/influxdb
RUNTIME_DIR=/run/influxdb
CONF_DIR=/etc/influxdb

configure() {
    set -e
    # Extract pre-vendored Rust crates (generated via cargo-vendor-gen.sh)
    if [ -f "${IGOS_SOURCES}/influxdb-3.9.0-vendor.tar.xz" ]; then
        tar -xf "${IGOS_SOURCES}/influxdb-3.9.0-vendor.tar.xz" --strip-components=1
        mkdir -p .cargo
        cat > .cargo/config.toml << 'CARGOEOF'
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "vendor"
CARGOEOF
    else
        echo "ERROR: influxdb-3.9.0-vendor.tar.xz not found in IGOS_SOURCES"
        echo "Run: scripts/cargo-vendor-gen.sh influxdb 3.9.0 <github-url>"
        exit 1
    fi
}

build() {
    set -e
    # Python plugin runtime is OFF by default — operator opts in
    # Documented threat surface: plugins run in-process via AppArmor constraint
    cargo build --release --frozen --offline \
        --no-default-features \
        --features server
}

check() {
    set -e
    true
}

do_install() {
    set -e
    install -d -m 755 "$DESTDIR"/usr/bin
    install -m 755 target/release/influxd "$DESTDIR"/usr/bin/influxd
    install -m 755 target/release/influx "$DESTDIR"/usr/bin/influx

    # Config skeleton
    install -d -m 750 "$DESTDIR"/etc/influxdb
    install -m 640 influxdb.conf "$DESTDIR"/etc/influxdb/influxdb.conf

    # State + log + runtime
    install -d -m 750 "$DESTDIR"/var/lib/influxdb
    install -d -m 750 "$DESTDIR"/var/log/influxdb
    install -d -m 755 "$DESTDIR"/run/influxdb

    # systemd unit
    install -d -m 755 "$DESTDIR"/usr/lib/systemd/system
    install -m 644 influxdb.service "$DESTDIR"/usr/lib/systemd/system/

    # AppArmor profile
    install -d -m 755 "$DESTDIR"/etc/apparmor.d
    install -m 644 usr.bin.influxd "$DESTDIR"/etc/apparmor.d/
}

post_install() {
    set -e
    if ! getent group "$PKG_GROUP" >/dev/null; then
        groupadd -r "$PKG_GROUP"
    fi
    if ! getent passwd "$PKG_USER" >/dev/null; then
        useradd -r -g "$PKG_GROUP" -d /var/lib/influxdb -s /sbin/nologin "$PKG_USER"
    fi
    chown -R "$PKG_USER":"$PKG_GROUP" /var/lib/influxdb /var/log/influxdb /run/influxdb /etc/influxdb
    systemctl daemon-reload 2>/dev/null || true
    apparmor_parser -r /etc/apparmor.d/usr.bin.influxd 2>/dev/null || true
}
