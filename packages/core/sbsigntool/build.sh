#!/bin/bash
# sbsigntool — sign/verify EFI binaries for Secure Boot
# Required by: Forge installer (mok.sign_efi_binary, mok.verify_efi_signature)
# Required by: build pipeline (signing the kernel image)

configure() {
    # autogen.sh expects lib/ccan.git/ to be a git checkout. The bundled
    # ccan tarball gives us the source files but no .git/. The
    # create-ccan-tree script uses `modfiles --git-only` to enumerate
    # source files; without a git repo, --git-only returns an empty
    # list and the generated lib/ccan/Makefile.am has an empty
    # libccan_a_SOURCES (just `libccan_a_SOURCES = \\` with no
    # continuation), which automake rejects.
    #
    # Initialize ccan.git as a single-commit git repo with explicit
    # -c overrides for user.name/email (chroot has no git config).
    if [ -d lib/ccan.git -a ! -d lib/ccan.git/.git ]; then
        cd lib/ccan.git
        git -c init.defaultBranch=master init -q
        git -c user.email=bundle@intergenos.local \
            -c user.name=bundle \
            -c commit.gpgsign=false \
            add -A
        git -c user.email=bundle@intergenos.local \
            -c user.name=bundle \
            -c commit.gpgsign=false \
            commit -q -m bundle
        cd ../..
    fi

    ./autogen.sh 2>/dev/null || autoreconf -fiv
    ./configure --prefix=/usr --sysconfdir=/etc --disable-static
}

build() {
    make
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
