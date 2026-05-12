#!/bin/bash
# maturin 1.13.1 — PEP 517 build backend for Rust+Python wheels.
#
# Source: GitHub archive at v1.13.1 tag (commit b27b7e12...), GPG-signed
# by @messense (key BB41A8A2C716CCA9, RSA-4096 from 2015-06-22). NOT from
# PyPI — built from source via the cargo-vendor pipeline to bypass the
# active 2026-05-11/12 PyPI supply-chain attack window (Mini Shai-Hulud
# wave). Required as build dep for python-cryptography v42+, which
# switched from setuptools-rust to maturin as its PEP 517 backend.
#
# Build flags: --no-default-features drops the upload/scaffolding/
# cli-completion/sbom/auditwheel features and their HTTP/TLS deps
# (ureq, rustls, native-tls). PEP 517 entry point is NOT feature-gated
# (verified at src/main.rs + src/commands/pep517.rs), so the minimal
# binary still supports `maturin pep517 build-wheel` which is what
# cryptography invokes during its build.

configure() {
    set -e
    # Maturin source ships its own Cargo.lock at repo root (verified
    # locally during preflight). The vendor tarball wraps vendor/ +
    # .cargo/config.toml without overwriting the lockfile.
    tar xf "${IGOS_SOURCES}/maturin-${PKG_VERSION}-vendor.tar.xz" \
        --strip-components=1
}

build() {
    set -e
    cargo build --release --no-default-features --frozen --offline
}

do_install() {
    set -e

    # 1. Install the standalone maturin binary on PATH.
    install -Dm755 target/release/maturin "${DESTDIR}/usr/bin/maturin"

    # 2. Install the PEP 517 Python wrapper.
    #
    # cryptography's pyproject.toml has `build-backend = "maturin"`. pip
    # imports the `maturin` Python package and calls its build_wheel /
    # build_sdist hooks. Those hooks subprocess-invoke `maturin` (bare,
    # looked up on PATH) which lands at /usr/bin/maturin from step 1.
    local pyver
    pyver=$(python3 -c 'import sys; print(f"python{sys.version_info[0]}.{sys.version_info[1]}")')
    local site="${DESTDIR}/usr/lib/${pyver}/site-packages"
    local pkgdir="${site}/maturin"
    install -dm755 "$pkgdir"
    install -Dm644 maturin/__init__.py "$pkgdir/__init__.py"
    install -Dm644 maturin/__main__.py "$pkgdir/__main__.py"
    install -Dm644 maturin/bootstrap.py "$pkgdir/bootstrap.py"

    # 3. Mint minimal .dist-info so pip's PEP 517 version resolver finds
    # maturin>=1,<2 (which is what cryptography requires).
    local distinfo="${site}/maturin-${PKG_VERSION}.dist-info"
    install -dm755 "$distinfo"
    cat > "$distinfo/METADATA" <<EOF
Metadata-Version: 2.1
Name: maturin
Version: ${PKG_VERSION}
Summary: Build and publish crates with pyo3, cffi and uniffi bindings as well as rust binaries as python packages
Home-page: https://github.com/PyO3/maturin
License: MIT OR Apache-2.0
Requires-Python: >=3.8
EOF
    cat > "$distinfo/WHEEL" <<EOF
Wheel-Version: 1.0
Generator: igos-source-build (${PKG_VERSION})
Root-Is-Purelib: false
Tag: py3-none-any
EOF
    cat > "$distinfo/entry_points.txt" <<EOF
[console_scripts]
maturin = maturin.__main__:main
EOF
    cat > "$distinfo/RECORD" <<'EOF'
maturin/__init__.py,,
maturin/__main__.py,,
maturin/bootstrap.py,,
EOF

    # 4. (Optional but compatible) symlink the binary into the bundled
    # location PyPI wheels use, so any consumer that inspects __file__
    # parent of the maturin package still finds the executable.
    ln -sf /usr/bin/maturin "${pkgdir}/maturin"
}
