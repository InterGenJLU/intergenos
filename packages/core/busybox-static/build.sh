#!/bin/bash
# busybox-static 1.37.0 — statically-linked busybox for initramfs userland.
#
# Distinct from a (future) dynamic busybox: this binary embeds glibc + applets
# so it can run before any other library is mounted. installer/init/init.sh
# exec's it as the only userland tool inside the live initramfs envelope.
#
# Installed at /usr/bin/busybox.static — the .static suffix preserves the
# /usr/bin/busybox namespace for a regular dynamic busybox if/when one is
# added. installer/init/build-initramfs.sh expects /usr/bin/busybox.static.

configure() {
    set -e
    # Start from upstream's defconfig (a balanced applet set), then force
    # static linking. defconfig is regenerated each build to track upstream
    # changes; CONFIG_STATIC override is the only project-specific knob.
    make defconfig

    # Force static linking (otherwise the binary needs glibc at runtime,
    # which initramfs envelopes don't have).
    sed -i 's/^# CONFIG_STATIC is not set$/CONFIG_STATIC=y/' .config

    # Disable applets that pull in network or hardware-specific code paths
    # we don't exercise from initramfs. Keeps the binary small + reduces
    # attack surface inside the early-boot envelope.
    #
    # Inspect actual size after build; if needed, trim further per the
    # APPLET list in installer/init/build-initramfs.sh (sh, mount, umount,
    # switch_root, awk, blkid, sleep, modprobe, mkdir, cp, ln, echo, cat,
    # printf, grep, sed, find).
    sed -i 's/^CONFIG_TC=y$/# CONFIG_TC is not set/'                .config
    sed -i 's/^CONFIG_FEATURE_TC_INGRESS=y$/# CONFIG_FEATURE_TC_INGRESS is not set/' .config

    # Re-run oldconfig to settle any dependency-cascade changes from the
    # CONFIG_STATIC flip + applet trims (some opts auto-enable/disable).
    # olddefconfig auto-accepts defaults without prompting and without the
    # `yes "" | make oldconfig` SIGPIPE-141 race under set -o pipefail.
    make olddefconfig
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # Static-linkage self-check. If this binary needs any shared library at
    # runtime, the initramfs will fail at boot — catch it here.
    if file ./busybox | grep -q "statically linked"; then
        echo "PASS: binary is statically linked"
    else
        echo "FAIL: binary is NOT statically linked" >&2
        file ./busybox >&2
        return 1
    fi

    # Smoke-test: --help should list applets. If the binary is broken, this
    # fails before we ship it.
    ./busybox --help >/dev/null 2>&1 || {
        echo "FAIL: ./busybox --help did not run" >&2
        return 1
    }

    # Verify a representative applet (sh, used by /init) actually executes.
    ./busybox sh -c 'echo "busybox sh OK"' || return 1
}

do_install() {
    set -e
    install -d "$DESTDIR/usr/bin"
    install -m755 busybox "$DESTDIR/usr/bin/busybox.static"

    install -d "$DESTDIR/usr/share/man/man1"
    install -m644 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/busybox-static.1" \
        "$DESTDIR/usr/share/man/man1/busybox-static.1"
}
