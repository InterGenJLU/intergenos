#!/bin/bash
# cups 2.4.11 — Common UNIX Printing System
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -i 's#@CUPS_HTMLVIEW@#firefox#' desktop/cups.desktop.in
    # Create lp user/group if needed
    useradd -c "Print Service User" -d /var/spool/cups -g lp -s /bin/false -u 9 lp 2>/dev/null || true
    groupadd -g 19 lpadmin 2>/dev/null || true

    # Fix IPP runtime issue
    sed -i '/& ipp->prev)/s/prev/& \&\& ipp->prev->next == *attr/' cups/ipp.c

    ./configure --libdir=/usr/lib            \
                --with-rundir=/run/cups      \
                --with-system-groups=lpadmin \
                --with-docdir=/usr/share/cups/doc-${version}
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    LC_ALL=C make -k check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install

    ln -svnf ../cups/doc-${version} "${DESTDIR}/usr/share/doc/cups-${version}"
}

post_install() {
    echo "ServerName /run/cups/cups.sock" > /etc/cups/client.conf
}
