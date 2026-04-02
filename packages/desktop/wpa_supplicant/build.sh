#!/bin/bash
# wpa_supplicant 2.11 — WPA/WPA2/IEEE 802.1X supplicant
# BLFS 13.0

configure() {
    # Create build configuration
    cat > wpa_supplicant/.config << "EOF"
CONFIG_BACKEND=file
CONFIG_CTRL_IFACE=y
CONFIG_DEBUG_FILE=y
CONFIG_DEBUG_SYSLOG=y
CONFIG_DEBUG_SYSLOG_FACILITY=LOG_DAEMON
CONFIG_DRIVER_NL80211=y
CONFIG_DRIVER_WEXT=y
CONFIG_DRIVER_WIRED=y
CONFIG_EAP_GTC=y
CONFIG_EAP_LEAP=y
CONFIG_EAP_MD5=y
CONFIG_EAP_MSCHAPV2=y
CONFIG_EAP_OTP=y
CONFIG_EAP_PEAP=y
CONFIG_EAP_TLS=y
CONFIG_EAP_TTLS=y
CONFIG_IEEE8021X_EAPOL=y
CONFIG_IPV6=y
CONFIG_LIBNL32=y
CONFIG_PEERKEY=y
CONFIG_PKCS12=y
CONFIG_READLINE=y
CONFIG_SMARTCARD=y
CONFIG_WPS=y
CFLAGS += -I/usr/include/libnl3
CONFIG_CTRL_IFACE_DBUS=y
CONFIG_CTRL_IFACE_DBUS_NEW=y
CONFIG_CTRL_IFACE_DBUS_INTRO=y
EOF
}

build() {
    cd wpa_supplicant &&
    make -j${IGOS_JOBS} BINDIR=/usr/sbin LIBDIR=/usr/lib
}

do_install() {
    cd wpa_supplicant &&

    install -v -m755 wpa_{cli,passphrase,supplicant} "${DESTDIR}/usr/sbin/"
    install -v -d "${DESTDIR}/usr/share/man/man5"
    install -v -d "${DESTDIR}/usr/share/man/man8"
    install -v -m644 doc/docbook/wpa_supplicant.conf.5 "${DESTDIR}/usr/share/man/man5/"
    install -v -m644 doc/docbook/wpa_{cli,passphrase,supplicant}.8 "${DESTDIR}/usr/share/man/man8/"

    # systemd service files
    install -v -d "${DESTDIR}/usr/lib/systemd/system"
    install -v -m644 systemd/*.service "${DESTDIR}/usr/lib/systemd/system/"

    # D-Bus configuration
    install -v -d "${DESTDIR}/usr/share/dbus-1/system-services"
    install -v -m644 dbus/fi.w1.wpa_supplicant1.service \
                     "${DESTDIR}/usr/share/dbus-1/system-services/"
    install -v -d "${DESTDIR}/etc/dbus-1/system.d"
    install -v -m644 dbus/dbus-wpa_supplicant.conf \
                     "${DESTDIR}/etc/dbus-1/system.d/wpa_supplicant.conf"
}
