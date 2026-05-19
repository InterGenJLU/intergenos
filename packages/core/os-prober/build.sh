#!/bin/bash
# os-prober 1.84 — Detect other OSes for grub dual-boot menu generation.
# T0-3 sub-cluster 1 — installer runtime dep (grub-mkconfig invokes os-prober).
#
# os-prober has no autotools layer — bare Makefile that only builds the newns
# C helper. Install layout is encoded in debian/os-prober.install + debian/rules;
# we replicate it here in do_install. Architecture-specific probes filter to x86
# (our v1.0 supported architecture per the kernel config strategy doc).

configure() {
    set -e
    :  # no-op — bare-Makefile build, no configure step
}

build() {
    set -e
    # Builds the newns C binary (used by mounted probes to enter a private
    # mount namespace before mounting candidate filesystems). Shell scripts
    # need no compilation.
    make -j${IGOS_JOBS} CFLAGS="-Os -g -Wall"
}

do_install() {
    set -e
    # Per debian/os-prober.install + debian/rules dh_install layout:
    install -dm755 "$DESTDIR/usr/bin"
    install -dm755 "$DESTDIR/usr/lib/os-prober"
    install -dm755 "$DESTDIR/usr/share/os-prober"
    install -dm755 "$DESTDIR/var/lib/os-prober"

    # Top-level shell entry points → /usr/bin
    install -m755 os-prober "$DESTDIR/usr/bin/os-prober"
    install -m755 linux-boot-prober "$DESTDIR/usr/bin/linux-boot-prober"

    # newns C helper → /usr/lib/os-prober/
    install -m755 newns "$DESTDIR/usr/lib/os-prober/newns"

    # common.sh shared lib → /usr/share/os-prober/
    install -m644 common.sh "$DESTDIR/usr/share/os-prober/common.sh"

    # Probe scripts. Layout: <kind>/common/* + <kind>/<arch>/* where arch=x86
    # on amd64 (per Debian rules ARCH=x86 mapping for i386+amd64).
    for kind in os-probes os-probes/mounted os-probes/init \
                linux-boot-probes linux-boot-probes/mounted; do
        install -dm755 "$DESTDIR/usr/lib/$kind"
        if [ -d "$kind/common" ]; then
            for f in "$kind"/common/*; do
                [ -f "$f" ] && install -m755 "$f" "$DESTDIR/usr/lib/$kind/"
            done
        fi
        # x86 arch-specific probes (we target amd64; arch=x86 per debian rules)
        if [ -d "$kind/x86" ]; then
            for f in "$kind"/x86/*; do
                [ -f "$f" ] && install -m755 "$f" "$DESTDIR/usr/lib/$kind/"
            done
        fi
    done

    # debian/rules: macOS probe is unconditionally installed under x86 mounted
    # (works on any x86 box with an HFS+ partition); replicate that.
    if [ -f os-probes/mounted/powerpc/20macosx ]; then
        install -m755 os-probes/mounted/powerpc/20macosx \
            "$DESTDIR/usr/lib/os-probes/mounted/20macosx"
    fi
}
