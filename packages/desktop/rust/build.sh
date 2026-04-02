#!/bin/bash
# rust 1.82.0 — Rust programming language
# BLFS 13.0
# Note: requires internet connection for bootstrap download

configure() {
    cat << EOF > bootstrap.toml
# See bootstrap.toml.example for more possible options
change-id = 148795

[llvm]
link-shared = true
targets = "X86"

[build]
description = "for InterGenOS"
docs = false
locked-deps = true
tools = ["cargo", "clippy", "rustdoc", "rustfmt", "src"]

[install]
prefix = "/opt/rustc-${version}"
docdir = "share/doc/rustc-${version}"

[rust]
channel = "stable"
lto = "thin"
codegen-units = 1
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

    # Create prefix directory
    mkdir -pv "${DESTDIR}/opt/rustc-${version}"
    ln -svfn "rustc-${version}" "${DESTDIR}/opt/rustc"

    DESTDIR="$DESTDIR" ./x.py install
}

post_install() {
    # Clean up docs
    rm -fv /opt/rustc-${version}/share/doc/rustc-${version}/*.old
    install -vm644 README.md /opt/rustc-${version}/share/doc/rustc-${version}

    # Zsh completions
    install -vdm755 /usr/share/zsh/site-functions
    ln -sfv /opt/rustc/share/zsh/site-functions/_cargo \
        /usr/share/zsh/site-functions

    # Bash completions
    mv -v /etc/bash_completion.d/cargo /usr/share/bash-completion/completions 2>/dev/null || true

    # PATH setup
    cat > /etc/profile.d/rustc.sh << "PROFILE"
# Begin /etc/profile.d/rustc.sh
pathprepend /opt/rustc/bin PATH
# End /etc/profile.d/rustc.sh
PROFILE

    # Symlink for ldconfig
    ln -svfn rustc-${version} /opt/rustc

    unset LIBSSH2_SYS_USE_PKG_CONFIG
    unset LIBSQLITE3_SYS_USE_PKG_CONFIG
}
