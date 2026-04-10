#!/bin/bash
# openldap 2.6.12 — Open source LDAP implementation
# BLFS 13.0 — Full server installation

configure() {
    # Patch applied by builder PATCH phase (package.yml) with SHA256 validation.
    autoconf

    ./configure --prefix=/usr         \
                --sysconfdir=/etc     \
                --localstatedir=/var  \
                --libexecdir=/usr/lib \
                --disable-static      \
                --disable-debug       \
                --with-tls=openssl    \
                --with-cyrus-sasl     \
                --without-systemd     \
                --enable-dynamic      \
                --enable-crypt        \
                --enable-spasswd      \
                --enable-slapd        \
                --enable-modules      \
                --enable-rlookups     \
                --enable-backends=mod \
                --disable-sql         \
                --disable-wt          \
                --enable-overlays=mod
}

build() {
    make depend
    make
}

do_install() {
    make DESTDIR="$DESTDIR" install

    # Fix .la references to .so in slapd config files
    sed -e "s/\.la/.so/" \
        -i "${DESTDIR}/etc/openldap/slapd.conf" \
        -i "${DESTDIR}/etc/openldap/slapd.ldif" \
        -i "${DESTDIR}/etc/openldap/slapd.conf.default" \
        -i "${DESTDIR}/etc/openldap/slapd.ldif.default" 2>/dev/null || true

    install -v -dm755 "${DESTDIR}/usr/share/doc/openldap-${PKG_VERSION}"
    cp -vfr doc/{drafts,rfc,guide} \
            "${DESTDIR}/usr/share/doc/openldap-${PKG_VERSION}"
}

post_install() {
    # Create ldap user and group for slapd
    if ! getent group ldap >/dev/null 2>&1; then
        groupadd -g 83 ldap
    fi
    if ! id ldap >/dev/null 2>&1; then
        useradd -c "OpenLDAP Daemon Owner" \
                -d /var/lib/openldap -u 83 \
                -g ldap -s /bin/false ldap
    fi

    # Create LDAP database directory with proper ownership
    install -v -dm700 -o ldap -g ldap /var/lib/openldap
    install -v -dm700 -o ldap -g ldap /etc/openldap/slapd.d

    # Set security permissions on config files (contain admin password in plain text)
    chmod  -v 640     /etc/openldap/slapd.{conf,ldif}   2>/dev/null || true
    chown  -v root:ldap /etc/openldap/slapd.{conf,ldif} 2>/dev/null || true
}
