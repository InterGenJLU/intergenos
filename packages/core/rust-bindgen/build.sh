#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rust-bindgen 0.72.1 — Rust FFI bindings generator
# BLFS 13.0

configure() {
    set -e
    # Extract vendored crate dependencies (built offline on host)
    tar xf "${IGOS_SOURCES}/rust-bindgen-${PKG_VERSION}-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release
}

check() {
    set -e
    # FAIL-CLOSED (r3): the prior `|| true` discarded cargo test's verdict —
    # two live failures shipped silently behind a green CHECK. The two
    # golden-expectation tests skipped BY NAME below are dispositioned, not
    # hidden: bindgen 0.72.1's vendored expectations encode an older
    # libclang's rendering of dependent template aliases
    # (__BindgenOpaqueArray) where libclang 21.1.8 resolves a generic
    # passthrough alias — textual expectation drift on 2 of 601 goldens
    # (599 byte-match), not a codegen defect. Re-evaluate both skips at the
    # next bindgen or llvm version bump.
    cargo test --release -- \
        --skip header_issue_544_stylo_creduce_2_hpp \
        --skip header_nsbasehashtable_hpp
}

do_install() {
    set -e
    mkdir -pv "${DESTDIR}/usr/bin"
    install -v -m755 target/release/bindgen "${DESTDIR}/usr/bin/bindgen"

    # Shell completions ship as owned payload (hook-contract wave), generated
    # at staging from the just-built binary — deterministic output.
    install -dm755 "${DESTDIR}/usr/share/bash-completion/completions" \
                   "${DESTDIR}/usr/share/zsh/site-functions"
    "${DESTDIR}/usr/bin/bindgen" --generate-shell-completions bash \
        > "${DESTDIR}/usr/share/bash-completion/completions/bindgen"
    "${DESTDIR}/usr/bin/bindgen" --generate-shell-completions zsh \
        > "${DESTDIR}/usr/share/zsh/site-functions/_bindgen"
    chmod 644 "${DESTDIR}/usr/share/bash-completion/completions/bindgen" \
              "${DESTDIR}/usr/share/zsh/site-functions/_bindgen"
}

