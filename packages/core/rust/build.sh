#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rust 1.95.0 — Rust programming language
# BLFS 13.0

configure() {
    set -e
    # Place pre-downloaded bootstrap tarballs where x.py expects them
    # so it skips the network download. Date from src/stage0.
    mkdir -pv build/cache/2026-03-05
    cp -v "${IGOS_SOURCES}/rustc-1.94.0-x86_64-unknown-linux-gnu.tar.xz" \
          "${IGOS_SOURCES}/cargo-1.94.0-x86_64-unknown-linux-gnu.tar.xz" \
          "${IGOS_SOURCES}/rust-std-1.94.0-x86_64-unknown-linux-gnu.tar.xz" \
          build/cache/2026-03-05/

    cat << EOF > bootstrap.toml
# See bootstrap.toml.example for more possible options,
# and see src/bootstrap/defaults/bootstrap.dist.toml for a few options
# automatically set when building from a release tarball
# (unfortunately, we have to override many of them).

# Tell x.py that the editors have reviewed the content of this file
# and updated it to follow the major changes of the building system,
# so x.py will not warn users to review that information.
change-id = 148795

[llvm]
# When using the system installed copy of LLVM, prefer the shared libraries
link-shared = true

# If building the shipped LLVM source, only enable the x86 target
# instead of all the targets supported by LLVM.
targets = "X86"

[build]
description = "for InterGenOS"

# Omit the documentation to save time and space (the default is to build them).
docs = false

# Do not look for new versions of the dependencies online.
locked-deps = true

# Only install these extended tools. Cargo, clippy, rustdoc, and rustfmt
# are installed by a default rustup installation, and rust-src is needed
# to build the Rust code in Linux kernel (in case you need such a kernel
# feature).
tools = ["cargo", "clippy", "rustdoc", "rustfmt", "src"]

# Multilib: build the Rust standard library for BOTH widths (GE-01 L15).
# The [target.i686-unknown-linux-gnu] llvm-config below was wired at
# wave-2, but without THIS list x.py builds std only for the host triple
# — lib32-mesa's NVK (Rust) then dies at meson's rustc sanity check with
# E0463 "can't find crate for std / the i686-unknown-linux-gnu target
# may not be installed" (/opt/rustc/lib/rustlib/ held only the x86_64
# std, verified). Upstream bootstrap semantics ([build].target in
# bootstrap.example.toml: build a toolchain for each listed triple); the
# reference multilib distro ships the same i686 std as lib32-rust-libs.
target = ["x86_64-unknown-linux-gnu", "i686-unknown-linux-gnu"]

[install]
prefix = "/opt/rustc-${version}"
docdir = "share/doc/rustc-${version}"

[rust]
channel = "stable"

# Do not attempt to download a pre-built rustc for bootstrapping.
# We provide the bootstrap compiler manually in build/cache/.
download-rustc = false

# Enable the same optimizations as the official upstream build.
lto = "thin"
codegen-units = 1

# Don't build llvm-bitcode-linker which is only useful for the NVPTX
# backend that we don't enable.
llvm-bitcode-linker = false

[target.x86_64-unknown-linux-gnu]
llvm-config = "/usr/bin/llvm-config"

[target.i686-unknown-linux-gnu]
llvm-config = "/usr/bin/llvm-config"
# Bootstrap's sanity check otherwise demands a cross-named
# i686-linux-gnu-gcc, which a multilib host does not have (L15 round 2:
# "couldn't find required command"). Pin the plain multilib drivers —
# the cc crate adds -m32 itself for i686-*-gnu targets (the reference
# distro's rust packaging uses exactly this shape for lib32-rust-libs).
cc = "/usr/bin/gcc"
cxx = "/usr/bin/g++"
ar = "/usr/bin/ar"
ranlib = "/usr/bin/ranlib"
linker = "/usr/bin/gcc"
EOF
}

build() {
    set -e
    export LIBSSH2_SYS_USE_PKG_CONFIG=1
    export LIBSQLITE3_SYS_USE_PKG_CONFIG=1
    ./x.py build
}

do_install() {
    set -e
    export LIBSSH2_SYS_USE_PKG_CONFIG=1
    export LIBSQLITE3_SYS_USE_PKG_CONFIG=1

    # Create prefix directory and symlink
    mkdir -pv "${DESTDIR}/opt/rustc-${version}"
    ln -svfn "rustc-${version}" "${DESTDIR}/opt/rustc"

    DESTDIR="$DESTDIR" ./x.py install

    # Stage the cargo bash completion at its canonical path BEFORE the
    # archive is cut. x.py installs it to /etc/bash_completion.d; moving
    # it post-archive left the manifest (/etc) and the chroot (/usr/share)
    # disagreeing, so pkm verify false-positived on the live root and the
    # installed system got the /etc copy back from the archive.
    install -vdm755 "${DESTDIR}/usr/share/bash-completion/completions"
    mv -v "${DESTDIR}/etc/bash_completion.d/cargo" \
        "${DESTDIR}/usr/share/bash-completion/completions/"
    rmdir -v "${DESTDIR}/etc/bash_completion.d"

    # Doc fixups belong here, where the build directory exists: the .old
    # twins never enter the archive, and README.md ships as owned payload.
    # A build-directory-relative path in post_install runs in a target
    # chroot whose cwd is /, fails, and its set -e aborts every line after
    # it -- which is why installed machines had no _cargo symlink and no
    # /etc/profile.d/rustc.sh.
    rm -fv "${DESTDIR}/opt/rustc-${version}/share/doc/rustc-${version}"/*.old
    install -vm644 README.md \
        "${DESTDIR}/opt/rustc-${version}/share/doc/rustc-${version}"

    # Completions + PATH profile + /opt/rustc symlink ship as owned payload
    # (hook-contract wave). Byte-identical to the retired hook's output.
    install -dm755 "${DESTDIR}/usr/share/zsh/site-functions" \
                   "${DESTDIR}/etc/profile.d" "${DESTDIR}/opt"
    ln -sfn /opt/rustc/share/zsh/site-functions/_cargo \
        "${DESTDIR}/usr/share/zsh/site-functions/_cargo"
    cat > "${DESTDIR}/etc/profile.d/rustc.sh" << "PROFILE"
# Begin /etc/profile.d/rustc.sh

case ":${PATH}:" in
    *:/opt/rustc/bin:*) ;;
    *) export PATH=/opt/rustc/bin:${PATH} ;;
esac

# End /etc/profile.d/rustc.sh
PROFILE
    chmod 644 "${DESTDIR}/etc/profile.d/rustc.sh"
    ln -sfn rustc-${version} "${DESTDIR}/opt/rustc"
}

