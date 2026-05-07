#!/bin/bash
# docbook-xsl-nons 1.79.2 — DocBook XSL stylesheets
# BLFS 13.0

configure() { : ; }

build() { : ; }

do_install() {
    set -e
    install -v -m755 -d "${DESTDIR}/usr/share/xml/docbook/xsl-stylesheets-nons-1.79.2"

    cp -v -R VERSION assembly common eclipse epub epub3 extensions fo \
             highlighting html htmlhelp images javahelp lib manpages params \
             profiling roundtrip slides template tests tools webhelp website \
             xhtml xhtml-1_1 xhtml5 \
        "${DESTDIR}/usr/share/xml/docbook/xsl-stylesheets-nons-1.79.2"

    ln -svf VERSION "${DESTDIR}/usr/share/xml/docbook/xsl-stylesheets-nons-1.79.2/VERSION.xsl"

    install -v -m644 -D README \
        "${DESTDIR}/usr/share/doc/docbook-xsl-nons-1.79.2/README.txt"

    install -v -m644 RELEASE-NOTES* NEWS* \
        "${DESTDIR}/usr/share/doc/docbook-xsl-nons-1.79.2"
}

post_install() {
    set -e
    # Create or update /etc/xml/catalog
    install -v -d -m755 /etc/xml
    [ -e /etc/xml/catalog ] || xmlcatalog --noout --create /etc/xml/catalog

    for uri in http{,s}://cdn.docbook.org/release/xsl-nons/{1.79.2,current} \
               http://docbook.sourceforge.net/release/xsl/current; do
        for rewrite in System URI; do
            xmlcatalog --noout --add "rewrite$rewrite" \
                "$uri" \
                "/usr/share/xml/docbook/xsl-stylesheets-nons-1.79.2" \
                /etc/xml/catalog
        done
    done
}
