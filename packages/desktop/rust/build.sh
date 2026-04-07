#!/bin/bash
# rust 1.93.1 — Rust programming language
# BLFS 13.0
# Note: requires internet connection for bootstrap download

configure() {
    # Place pre-downloaded bootstrap tarballs where x.py expects them
    # so it skips the network download. Date from src/stage0.
    mkdir -pv build/cache/2025-12-11
    cp -v "${IGOS_SOURCES}/rustc-1.92.0-x86_64-unknown-linux-gnu.tar.xz" \
          "${IGOS_SOURCES}/cargo-1.92.0-x86_64-unknown-linux-gnu.tar.xz" \
          "${IGOS_SOURCES}/rust-std-1.92.0-x86_64-unknown-linux-gnu.tar.xz" \
          build/cache/2025-12-11/

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
EOF
}

build() {
    export LIBSSH2_SYS_USE_PKG_CONFIG=1
    export LIBSQLITE3_SYS_USE_PKG_CONFIG=1
    ./x.py build
}

do_install() {
    export LIBSSH2_SYS_USE_PKG_CONFIG=1
    export LIBSQLITE3_SYS_USE_PKG_CONFIG=1

    # Create prefix directory and symlink
    mkdir -pv "${DESTDIR}/opt/rustc-${version}"
    ln -svfn "rustc-${version}" "${DESTDIR}/opt/rustc"

    DESTDIR="$DESTDIR" ./x.py install
}

post_install() {
    # Fix up docs
    rm -fv /opt/rustc-${version}/share/doc/rustc-${version}/*.old
    install -vm644 README.md /opt/rustc-${version}/share/doc/rustc-${version}

    # Zsh completions
    install -vdm755 /usr/share/zsh/site-functions
    ln -sfv /opt/rustc/share/zsh/site-functions/_cargo \
        /usr/share/zsh/site-functions

    # Bash completions
    mv -v /etc/bash_completion.d/cargo /usr/share/bash-completion/completions

    # PATH setup
    cat > /etc/profile.d/rustc.sh << "PROFILE"
# Begin /etc/profile.d/rustc.sh

case ":${PATH}:" in
    *:/opt/rustc/bin:*) ;;
    *) export PATH=/opt/rustc/bin:${PATH} ;;
esac

# End /etc/profile.d/rustc.sh
PROFILE

    # Ensure symlink exists on live system
    ln -svfn rustc-${version} /opt/rustc

    unset LIBSSH2_SYS_USE_PKG_CONFIG
    unset LIBSQLITE3_SYS_USE_PKG_CONFIG
}
