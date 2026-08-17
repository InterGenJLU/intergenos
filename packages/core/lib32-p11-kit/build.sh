#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# p11-kit 0.26.2 — 32-bit multilib runtime (GE arc, nss forced closure)
# Sibling: packages/core/p11-kit (same tarball, same version — RT-9 lock).
# Profile: scripts/lib32-env.sh + config/lib32/lib32-cross.ini (RT-7).
# Option provenance: package.yml (every -D verified against the pinned
# 0.26.2 meson_options.txt).

source /mnt/intergenos/scripts/lib32-env.sh

configure() {
    set -e
    # The sibling's trust-extract-compat sed is deliberately NOT
    # mirrored — it edits a /usr/libexec script the allowlist never
    # stages (the 64-bit sibling owns that file).
    mkdir -p p11-build
    cd    p11-build

    meson setup ..                                                    \
          --cross-file /mnt/intergenos/config/lib32/lib32-cross.ini   \
          --prefix=/usr                                               \
          --libdir=/usr/lib32                                         \
          --buildtype=release                                         \
          -Dtrust_paths=/etc/pki/anchors                              \
          -Dlibffi=enabled                                            \
          -Dsystemd=disabled                                          \
          -Dbash_completion=disabled                                  \
          -Dzsh_completion=disabled                                   \
          -Dgtk_doc=false                                             \
          -Dman=false                                                 \
          -Dnls=false
}

build() {
    set -e
    cd p11-build
    # -v MANDATORY: the archive-time time64 log assertion refuses a log
    # with no visible compile evidence (RT-8/F2-a).
    ninja -v
}

do_install() {
    set -e
    cd p11-build
    DESTDIR="$PWD/m32root" ninja install
    lib32_stage_libs "$PWD/m32root"
    lib32_assert_only_lib32
    lib32_env_end
}
