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

    # create-ccan-tree calls modfiles with the relative module_dir
    # ("ccan/talloc" etc.) rather than the full $srcdir/$module_dir
    # path. modfiles' path_canon() then fails because cwd is the
    # sbsigntool source root, not lib/ccan.git/. Result: empty
    # libccan_a_SOURCES, automake rejects with "trailing backslash on
    # last line". Sed-patch the script to pass module_srcdir (full
    # path) instead — output filenames are unchanged because modfiles
    # default emits f->name (module-relative) regardless of how the
    # input path is given.
    sed -i 's|"$modfiles" $MODULES_ARGS --no-license --git-only "$module_dir"|"$modfiles" $MODULES_ARGS --no-license --git-only "$module_srcdir"|' \
        lib/ccan.git/tools/create-ccan-tree

    # autogen.sh's ccan_modules list is incomplete — sbkeysync.c
    # #includes <ccan/list/list.h> but `list` isn't in the modules
    # list, and isn't pulled in as a transitive dep of the listed 5.
    # Add it to the list. (Upstream sbsigntool 0.9.5 bug.)
    sed -i 's|^ccan_modules="talloc read_write_all build_assert array_size endian"$|ccan_modules="talloc read_write_all build_assert array_size endian list"|' \
        autogen.sh

    ./autogen.sh 2>/dev/null || autoreconf -fiv
    ./configure --prefix=/usr --sysconfdir=/etc --disable-static
}

build() {
    make
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
