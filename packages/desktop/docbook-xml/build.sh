#!/bin/bash
# docbook-xml 4.5 — DocBook XML DTD
# BLFS 13.0
# Note: source is a zip, not a tarball — must be extracted with unzip

configure() { : ; }
build() { : ; }

do_install() {
    install -v -d -m755 "${DESTDIR}/usr/share/xml/docbook/xml-dtd-4.5"
    install -v -d -m755 "${DESTDIR}/etc/xml"
    cp -v -af --no-preserve=ownership \
        catalog.xml docbook.cat *.dtd ent/ *.mod \
        "${DESTDIR}/usr/share/xml/docbook/xml-dtd-4.5"
}

post_install() {
    # Add URL rewrites to the installed catalog
    xmlcatalog --noout --add "rewriteSystem" \
        "http://www.oasis-open.org/docbook/xml/4.5" \
        "file:///usr/share/xml/docbook/xml-dtd-4.5" \
        /usr/share/xml/docbook/xml-dtd-4.5/catalog.xml &&

    xmlcatalog --noout --add "rewriteURI" \
        "http://www.oasis-open.org/docbook/xml/4.5" \
        "file:///usr/share/xml/docbook/xml-dtd-4.5" \
        /usr/share/xml/docbook/xml-dtd-4.5/catalog.xml

    # Create or update /etc/xml/catalog
    if [ ! -e /etc/xml/catalog ]; then
        xmlcatalog --noout --create /etc/xml/catalog
    fi &&

    xmlcatalog --noout --add "delegatePublic" \
        "-//OASIS//ENTITIES DocBook XML" \
        "file:///usr/share/xml/docbook/xml-dtd-4.5/catalog.xml" \
        /etc/xml/catalog &&

    xmlcatalog --noout --add "delegatePublic" \
        "-//OASIS//DTD DocBook XML" \
        "file:///usr/share/xml/docbook/xml-dtd-4.5/catalog.xml" \
        /etc/xml/catalog &&

    xmlcatalog --noout --add "delegateSystem" \
        "http://www.oasis-open.org/docbook/" \
        "file:///usr/share/xml/docbook/xml-dtd-4.5/catalog.xml" \
        /etc/xml/catalog &&

    xmlcatalog --noout --add "delegateURI" \
        "http://www.oasis-open.org/docbook/" \
        "file:///usr/share/xml/docbook/xml-dtd-4.5/catalog.xml" \
        /etc/xml/catalog

    # Map older DTD versions to 4.5
    for DTDVERSION in 4.1.2 4.2 4.3 4.4; do
        xmlcatalog --noout --add "public" \
            "-//OASIS//DTD DocBook XML V$DTDVERSION//EN" \
            "http://www.oasis-open.org/docbook/xml/$DTDVERSION/docbookx.dtd" \
            /usr/share/xml/docbook/xml-dtd-4.5/catalog.xml

        xmlcatalog --noout --add "rewriteSystem" \
            "http://www.oasis-open.org/docbook/xml/$DTDVERSION" \
            "file:///usr/share/xml/docbook/xml-dtd-4.5" \
            /usr/share/xml/docbook/xml-dtd-4.5/catalog.xml

        xmlcatalog --noout --add "rewriteURI" \
            "http://www.oasis-open.org/docbook/xml/$DTDVERSION" \
            "file:///usr/share/xml/docbook/xml-dtd-4.5" \
            /usr/share/xml/docbook/xml-dtd-4.5/catalog.xml
    done
}
