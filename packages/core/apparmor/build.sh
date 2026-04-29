#!/bin/bash
# AppArmor profile set deployment
# Pulls substrate profiles + installs InterGenOS-specific profiles

configure() {
    :
}

build() {
    :
}

do_install() {
    # 1. Install Debian 12 (bookworm) substrate profiles from source tarball
    install -vdm 755 "$DESTDIR/etc/apparmor.d/"
    install -vdm 755 "$DESTDIR/etc/apparmor.d/disable/"
    install -vdm 755 "$DESTDIR/etc/apparmor.d/local/"
    
    cp -rv apparmor-3.0.8/profiles/apparmor.d/* "$DESTDIR/etc/apparmor.d/"
    cp -rv apparmor-profiles-extra-1.35/work/profiles/* "$DESTDIR/etc/apparmor.d/"
    
    # 2. Install InterGenOS-specific custom profiles
    install -vm 644 profiles/usr.bin.intergen-mcp "$DESTDIR/etc/apparmor.d/"
    install -vm 644 profiles/usr.bin.pkm "$DESTDIR/etc/apparmor.d/"
    install -vm 644 profiles/usr.bin.forge-sb-installer "$DESTDIR/etc/apparmor.d/"
    
    # 3. Set to complain mode by default per Prime Directive
    # This creates a marker file that the init script or orchestrator checks
    # to run aa-complain on all profiles.
    install -vdm 755 "$DESTDIR/usr/share/intergenos-apparmor/"
    echo "complain" > "$DESTDIR/usr/share/intergenos-apparmor/default_mode"
}
