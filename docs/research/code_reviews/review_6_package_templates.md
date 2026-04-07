# Code Review Request: InterGenOS Package Templates (Critical Packages)

I'm requesting a thorough code review of 40 critical package templates from InterGenOS, a Linux distribution built entirely from source following Linux From Scratch (LFS 13.0) and Beyond LFS (BLFS 13.0).

Each package in InterGenOS is defined by two files:
- **package.yml** — YAML metadata: name, version, source URL, SHA256 checksum, build/host/runtime dependencies, and license.
- **build.sh** — Bash script defining `configure()`, `build()`, `do_install()`, and optionally `post_install()` functions. These are sourced by the build system and executed in sequence inside the chroot.

The 40 packages included here were selected because they represent the highest risk:

- **Core system packages** with complex, non-standard builds (glibc, NSS, OpenSSH)
- **GPU/Vulkan stack** where we pre-download Rust crates for offline building (Mesa, shaderc, GTK4)
- **Packages that previously caused build failures** and were corrected (samba, spidermonkey, glycin, argcomplete, VTE, ibus, gnome-settings-daemon)
- **15 newly-added packages** that restore previously-disabled features (avahi, libcdio, libmtp, libgphoto2, libimobiledevice chain, libbluray, libnfs, libmsgraph, graphviz, gtksourceview5)
- **gvfs and gvfs-pass2** — the package whose 10 disabled backends triggered this entire remediation effort

The build environment is an offline chroot — no internet access. All source tarballs and patches must be pre-staged in `/sources/`.

I would appreciate your assessment of the following areas in particular:

1. **Configure flags** — Are they correct per BLFS 13.0 or upstream documentation? Are any critical flags missing?
2. **Dependency declarations** — Are all required build dependencies listed in package.yml? Missing deps cause configure failures.
3. **DESTDIR handling** — Does each `do_install()` correctly install to `$DESTDIR` rather than the live filesystem?
4. **Missing post_install() hooks** — Some packages need user/group creation, systemd service enablement, schema compilation, or ldconfig runs after installation. Are any missing?
5. **Offline build compatibility** — Will any of these packages attempt to download something during configure or build? (The chroot has no DNS or network.)
6. **Two-pass builds** — We use pass-1/pass-2 patterns for freetype2, gst-plugins-base, and gvfs. Is the dependency wiring correct to ensure pass-2 builds after its additional deps?

The complete package.yml and build.sh for all 40 packages follow.

---

## Package Templates

---
### glibc-core
```yaml
name: glibc-core
version: '2.43'
release: 1
description: GNU C Library (final system)
license: LGPL-2.1-or-later
homepage: https://www.gnu.org/software/libc/
tier: core
build_style: custom
install_func: do_install
source:
- url: https://ftp.gnu.org/gnu/glibc/glibc-${version}.tar.xz
  sha256: d9c86c6b5dbddb43a3e08270c5844fc5177d19442cf5b8df4be7c07cd5fa3831
patches:
- glibc-fhs-1.patch
dependencies:
  build: []
  host: []
  runtime: []
```
```bash
#!/bin/bash
# Glibc 2.43
# LFS 13.0 Section 8.5
#
# DESTDIR exception: Glibc uses install_root instead of DESTDIR.
# Post-install: nsswitch.conf, ld.so.conf, timezone, locales.

configure() {
    # FHS compliance patch
    patch -Np1 -i ${IGOS_PATCHES}/glibc-fhs-1.patch

    mkdir -v build
    cd       build

    echo "rootsbindir=/usr/sbin" > configparms

    ../configure --prefix=/usr                   \
        --build=x86_64-igos-linux-gnu            \
        --host=x86_64-igos-linux-gnu             \
        --disable-werror                         \
        --disable-nscd                           \
        libc_cv_slibdir=/usr/lib                 \
        --enable-stack-protector=strong           \
        --enable-kernel=5.4
}

build() {
    cd build
    make -j${IGOS_JOBS}
}

check() {
    cd build
    # CRITICAL: Do not skip the glibc test suite
    make check || true

    # Check for timeouts (common in chroot)
    echo ""
    echo "=== Glibc Timeout Check ==="
    grep "Timed out" $(find -name \*.out) 2>/dev/null || echo "No timeouts"
}

do_install() {
    cd build

    # Prevent warnings during install
    touch "${DESTDIR}/etc/ld.so.conf" 2>/dev/null || true

    # Skip test-installation rule (it would fail in DESTDIR)
    sed '/test-installation/s@$(PERL)@echo not running@' -i ../Makefile

    # Glibc uses install_root, not DESTDIR
    make install_root="$DESTDIR" install

    # Fix ldd path
    sed '/RTLDLIST=/s@/usr@@g' -i "${DESTDIR}/usr/bin/ldd"

    # Install minimal set of locales needed for tests and basic operation
    mkdir -pv "${DESTDIR}/usr/lib/locale"
    # These localedef commands must run against the staged glibc
    # They will be re-run in post_install against the live system
}

# Post-install: runs on the live system AFTER deploy
post_install() {
    # Create essential locales
    localedef -i C -f UTF-8 C.UTF-8
    localedef -i en_US -f ISO-8859-1 en_US
    localedef -i en_US -f UTF-8 en_US.UTF-8

    # nsswitch.conf for systemd
    cat > /etc/nsswitch.conf << "EOF"
# Begin /etc/nsswitch.conf

passwd: files systemd
group: files systemd
shadow: files systemd

hosts: mymachines resolve [!UNAVAIL=return] files myhostname dns
networks: files

protocols: files
services: files
ethers: files
rpc: files

# End /etc/nsswitch.conf
EOF

    # Timezone data
    tar -xf ${IGOS_SOURCES}/tzdata2025c.tar.gz -C /tmp

    ZONEINFO=/usr/share/zoneinfo
    mkdir -pv $ZONEINFO/{posix,right}

    for tz in etcetera southamerica northamerica europe africa antarctica \
              asia australasia backward; do
        zic -L /dev/null   -d $ZONEINFO       /tmp/${tz}
        zic -L /dev/null   -d $ZONEINFO/posix /tmp/${tz}
        zic -L /tmp/leapseconds -d $ZONEINFO/right /tmp/${tz}
    done

    cp -v /tmp/zone.tab /tmp/zone1970.tab /tmp/iso3166.tab $ZONEINFO
    zic -d $ZONEINFO -p America/New_York
    unset ZONEINFO

    # Set timezone from host — check TZ env, then /etc/timezone, fall back to UTC
    local tz="${TZ:-}"
    [ -z "$tz" ] && [ -f /etc/timezone ] && tz="$(cat /etc/timezone)"
    [ -z "$tz" ] && tz="UTC"
    if [ -f "/usr/share/zoneinfo/$tz" ]; then
        ln -sfv /usr/share/zoneinfo/$tz /etc/localtime
    else
        ln -sfv /usr/share/zoneinfo/UTC /etc/localtime
    fi

    # Dynamic loader configuration
    cat > /etc/ld.so.conf << "EOF"
# Begin /etc/ld.so.conf
/usr/local/lib
/opt/lib

EOF

    cat >> /etc/ld.so.conf << "EOF"
# Add an include directory
include /etc/ld.so.conf.d/*.conf

EOF
    mkdir -pv /etc/ld.so.conf.d

    # Rebuild ld cache
    ldconfig
}
```

---
### nss
```yaml
name: nss
version: "3.121"
release: 1
description: Network Security Services
license: MPL-2.0
homepage: https://developer.mozilla.org/en-US/docs/Mozilla/Projects/NSS
tier: core
build_style: custom
install_func: do_install

source:
  - url: https://archive.mozilla.org/pub/security/nss/releases/NSS_3_121_RTM/src/nss-${version}.tar.gz
    sha256: cb3a8f8781bea78b7b8edd3afb7a2cb58e4881bb0160d189a39b98216ba7632e

dependencies:
  build:
    - nspr
  host: []
  runtime:
    - p11-kit

patches:
  - nss-standalone-1.patch
```
```bash
#!/bin/bash
# NSS 3.121 — Network Security Services
# BLFS 13.0
# Non-standard build: uses raw make, no configure

configure() {
    # Apply standalone build patch (may already be applied in newer versions)
    patch -Np1 --forward -i "${IGOS_SOURCES}/nss-standalone-1.patch" || true
}

build() {
    cd nss

    make BUILD_OPT=1                      \
      NSPR_INCLUDE_DIR=/usr/include/nspr  \
      USE_SYSTEM_ZLIB=1                   \
      ZLIB_LIBS=-lz                       \
      NSS_ENABLE_WERROR=0                 \
      NSS_USE_SYSTEM_SQLITE=1             \
      $([ $(uname -m) = x86_64 ] && echo USE_64=1) \
      -j${IGOS_JOBS}
}

do_install() {
    cd dist

    install -v -m755 -d "${DESTDIR}/usr/lib"
    install -v -m755 Linux*/lib/*.so              "${DESTDIR}/usr/lib"
    install -v -m644 Linux*/lib/{*.chk,libcrmf.a} "${DESTDIR}/usr/lib"

    install -v -m755 -d                           "${DESTDIR}/usr/include/nss"
    cp -v -RL {public,private}/nss/*              "${DESTDIR}/usr/include/nss"

    install -v -m755 -d "${DESTDIR}/usr/bin"
    install -v -m755 Linux*/bin/{certutil,nss-config,pk12util} "${DESTDIR}/usr/bin"

    install -v -m755 -d "${DESTDIR}/usr/lib/pkgconfig"
    install -v -m644 Linux*/lib/pkgconfig/nss.pc  "${DESTDIR}/usr/lib/pkgconfig"

    # p11-kit trust module symlink
    ln -sfv ./pkcs11/p11-kit-trust.so "${DESTDIR}/usr/lib/libnssckbi.so"
}
```

---
### openssh
```yaml
name: openssh
version: "10.2p1"
release: 1
description: Secure Shell client and server
license: BSD-2-Clause
homepage: https://www.openssh.com/
tier: core
build_style: custom
install_func: do_install

source:
  - url: https://ftp.openbsd.org/pub/OpenBSD/OpenSSH/portable/openssh-${version}.tar.gz
    sha256: ccc42c0419937959263fa1dbd16dafc18c56b984c03562d2937ce56a60f798b2

dependencies:
  build:
    - openssl
    - linux-pam
  host: []
  runtime: []
```
```bash
#!/bin/bash
# OpenSSH 10.2p1
# BLFS 13.0 — with PAM support and InterGenOS systemd unit
#
# DESTDIR supported. Post-install creates sshd user/group, PAM config,
# installs systemd unit, and generates host keys.

configure() {
    ./configure --prefix=/usr                            \
                --sysconfdir=/etc/ssh                    \
                --with-privsep-path=/var/lib/sshd        \
                --with-default-path=/usr/bin             \
                --with-superuser-path=/usr/sbin:/usr/bin \
                --with-pid-dir=/run                      \
                --with-pam
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    # Tests require gdb and a running sshd — skip in chroot
    :
}

do_install() {
    make DESTDIR="$DESTDIR" install

    # Install ssh-copy-id utility (BLFS)
    install -v -m755 contrib/ssh-copy-id "${DESTDIR}/usr/bin"
    install -v -m644 contrib/ssh-copy-id.1 "${DESTDIR}/usr/share/man/man1"

    # Install documentation
    install -v -m755 -d "${DESTDIR}/usr/share/doc/openssh-10.2p1"
    install -v -m644 INSTALL LICENCE OVERVIEW README* \
        "${DESTDIR}/usr/share/doc/openssh-10.2p1"

    # Install InterGenOS sshd systemd unit
    install -v -Dm644 /mnt/intergenos/config/systemd/sshd.service \
        "${DESTDIR}/usr/lib/systemd/system/sshd.service"

    # Create tmpfiles.d config for /run/sshd
    install -v -Dm644 /dev/stdin "${DESTDIR}/usr/lib/tmpfiles.d/sshd.conf" << 'EOF'
d /run/sshd 755 root root -
EOF
}

# Post-install: runs on the live system AFTER deploy
post_install() {
    # Create privilege separation directory
    install -v -g sys -m700 -d /var/lib/sshd

    # Create sshd user and group for privilege separation
    if ! getent group sshd >/dev/null 2>&1; then
        groupadd -g 50 sshd
    fi
    if ! id sshd >/dev/null 2>&1; then
        useradd -c 'sshd PrivSep' \
                -d /var/lib/sshd   \
                -g sshd            \
                -s /bin/false      \
                -u 50 sshd
    fi

    # Create PAM config from shadow's login config (BLFS)
    sed 's@d/login@d/sshd@g' /etc/pam.d/login > /etc/pam.d/sshd
    # Remove pam_lastlog.so — deprecated and removed from Linux-PAM >= 1.6.0
    sed -i '/pam_lastlog\.so/d' /etc/pam.d/sshd
    chmod 644 /etc/pam.d/sshd

    # Enable PAM and root login in sshd_config
    echo "UsePAM yes" >> /etc/ssh/sshd_config
    sed -i 's/^#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config

    # Generate host keys if they don't exist
    ssh-keygen -A

    # Enable sshd service
    systemctl enable sshd.service
}
```

---
### mesa
```yaml
name: mesa
version: "25.3.5"
release: 1
description: OpenGL, Vulkan, and OpenCL implementation
license: MIT
homepage: https://www.mesa3d.org/
tier: desktop
build_style: meson
install_func: do_install
source:
- url: https://mesa.freedesktop.org/archive/mesa-${version}.tar.xz
  sha256: be472413475082df945e0f9be34f5af008baa03eb357e067ce5a611a2d44c44b
patches:
  - mesa-add_xdemos-4.patch
dependencies:
  build:
  - libdrm
  - libX11
  - libXext
  - libXfixes
  - libXrandr
  - libxshmfence
  - libxcb
  - libxkbcommon
  - wayland-protocols
  - llvm
  - Mako
  - glslang
  - vulkan-headers
  - vulkan-loader
  - cbindgen
  - rust-bindgen
  - libclc
  host: []
  runtime: []
```
```bash
#!/bin/bash
# mesa 25.3.5 — OpenGL, Vulkan, and OpenCL implementation
# BLFS 13.0

configure() {
    # Pre-place Rust crate tarballs for offline build.
    # NVK (Nouveau Vulkan) and other Rust-based components require 27 crates
    # from crates.io. Since the chroot has no internet, we pre-download them
    # on the host and archive them as mesa-25.3.5-rust-crates.tar.gz.
    # Meson checks subprojects/packagecache/ before attempting downloads.
    if [ -f "${IGOS_SOURCES}/mesa-25.3.5-rust-crates.tar.gz" ]; then
        mkdir -p subprojects/packagecache
        tar -xf "${IGOS_SOURCES}/mesa-25.3.5-rust-crates.tar.gz" \
            -C subprojects/packagecache
    fi

    mkdir build
    cd    build

    meson setup ..                 \
          --prefix=/usr            \
          --libdir=/usr/lib        \
          --buildtype=release      \
          --wrap-mode=nodownload   \
          -D platforms=x11,wayland \
          -D gallium-drivers=auto  \
          -D vulkan-drivers=auto   \
          -D valgrind=disabled     \
          -D video-codecs=all      \
          -D libunwind=disabled
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
```

---
### gtk4
```yaml
name: gtk4
version: "4.20.3"
release: 1
description: GTK 4 widget toolkit
license: LGPL-2.0-or-later
homepage: https://www.gtk.org/
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://download.gnome.org/sources/gtk/4.20/gtk-${version}.tar.xz
  sha256: 2873f2903088a66c71173ea2ed85ffae266a66b972c3a4842bbb2f6f187ec153
dependencies:
  build:
  - gdk-pixbuf
  - graphene
  - gst-plugins-base-pass2
  - iso-codes
  - libepoxy
  - librsvg
  - pango
  - pygobject3
  - shaderc
  - wayland-protocols
  - libxkbcommon
  - cairo
  - libXi
  - libXrandr
  - libXcursor
  host: []
  runtime: []
```
```bash
#!/bin/bash
# gtk4 4.20.3 — GTK 4 widget toolkit
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed "s@'doc'@& / 'gtk-${PKG_VERSION}'@" -i ../docs/reference/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          --wrap-mode=nofallback \
          -Dbroadway-backend=true \
          -Dintrospection=enabled \
          -Dvulkan=enabled
}

build() {
    cd build
    ninja
}

check() {
    cd build
    # Requires graphical session per BLFS; failures expected in chroot
    ninja test || true
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
```

---
### gst-plugins-base
```yaml
name: gst-plugins-base
version: "1.28.1"
release: 1
description: GStreamer base plugins
license: LGPL-2.0-or-later
homepage: https://gstreamer.freedesktop.org/
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://gstreamer.freedesktop.org/src/gst-plugins-base/gst-plugins-base-${version}.tar.xz
  sha256: 1446a4c2a92ff5d78d88e85a599f0038441d53333236f0c72d72f21a9c132497
dependencies:
  build:
  - gstreamer
  - alsa-lib
  - libogg
  - libvorbis
  - pango
  - libX11
  - libXext
  host: []
  runtime: []
```
```bash
#!/bin/bash
# gst-plugins-base 1.28.1 — GStreamer base plugins
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dtests=disabled \
          -Dexamples=disabled \
          -Ddoc=disabled \
          --wrap-mode=nodownload
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
```

---
### gst-plugins-base-pass2
```yaml
name: gst-plugins-base-pass2
version: "1.28.1"
release: 1
description: "GStreamer base plugins (pass 2 — with Mesa GL support)"
license: LGPL-2.0-or-later
homepage: https://gstreamer.freedesktop.org/
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://gstreamer.freedesktop.org/src/gst-plugins-base/gst-plugins-base-${version}.tar.xz
  sha256: 1446a4c2a92ff5d78d88e85a599f0038441d53333236f0c72d72f21a9c132497
dependencies:
  build:
  - gst-plugins-base
  - mesa
  - alsa-lib
  - libogg
  - libvorbis
  - pango
  - libX11
  - libXext
  host: []
  runtime: []
```
```bash
#!/bin/bash
# gst-plugins-base 1.28.1 — pass 2 rebuild with Mesa GL support
# BLFS 13.0
#
# Pass 1 builds before Mesa (no GL available), so libgstgl-1.0.so
# is not produced. GTK4 requires gstreamer-gl-1.0 for media playback.
# This pass rebuilds after Mesa is installed, enabling GL auto-detection.

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dtests=disabled \
          -Dexamples=disabled \
          -Ddoc=disabled \
          --wrap-mode=nodownload
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
```

---
### shaderc
```yaml
name: shaderc
version: "2026.1"
release: 1
description: Google GLSL/HLSL to SPIR-V shader compiler
license: Apache-2.0
homepage: https://github.com/google/shaderc
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://github.com/google/shaderc/archive/v${version}/shaderc-${version}.tar.gz
  sha256: 245002feccbe7f8361b223545a5654cea69780745886872d7efff50a38d96c66
dependencies:
  build:
  - cmake
  - glslang
  - spirv-tools
  host: []
  runtime: []
```
```bash
#!/bin/bash
# shaderc 2026.1 — Google GLSL/HLSL to SPIR-V shader compiler (glslc)
# BLFS 13.0

configure() {
    # Use system glslang and spirv-tools per BLFS
    sed '/build-version/d'   -i glslc/CMakeLists.txt
    sed '/third_party/d'     -i CMakeLists.txt
    sed 's|SPIRV|glslang/&|' -i libshaderc_util/src/compiler.cc

    echo '"2026.1"' > glslc/src/build-version.inc

    mkdir build
    cd    build

    cmake -D CMAKE_INSTALL_PREFIX=/usr \
          -D CMAKE_BUILD_TYPE=Release  \
          -D SHADERC_SKIP_TESTS=ON     \
          -G Ninja ..
}

build() {
    cd build
    ninja glslc/glslc
}

do_install() {
    install -v -m755 -d "${DESTDIR}/usr/bin"
    install -v -m755 build/glslc/glslc "${DESTDIR}/usr/bin"
}
```

---
### glycin
```yaml
name: glycin
version: "2.0.8"
release: 1
description: Sandboxed and extendable image loading library
license: MPL-2.0
homepage: https://gitlab.gnome.org/GNOME/glycin
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://download.gnome.org/sources/glycin/2.0/glycin-${version}.tar.xz
  sha256: 6b9aef4f626bf969dfd04b563b83d521271a30c3d6df6a6a8463fd55490891df
dependencies:
  build:
  - bubblewrap
  - fontconfig
  - lcms2
  - libheif
  - librsvg
  - libseccomp
  - rust
  - vala
  host: []
  runtime: []
```
```bash
#!/bin/bash
# glycin 2.0.8 — Sandboxed image loading library for GNOME
# BLFS 13.0
# Requires pre-vendored Rust crates (glycin-2.0.8-vendor.tar.gz)

configure() {
    # Extract pre-vendored Rust crates FIRST (patch needs vendor/ directory)
    if [ -f "${IGOS_SOURCES}/glycin-2.0.8-vendor.tar.gz" ]; then
        tar xf "${IGOS_SOURCES}/glycin-2.0.8-vendor.tar.gz"

        # Configure cargo to use vendored crates
        mkdir -p .cargo
        cat > .cargo/config.toml << 'CARGOEOF'
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "vendor"
CARGOEOF
    fi

    # Apply XBM/XPM support patch AFTER vendor extraction
    # (patch modifies files in vendor/ directory)
    # Use --no-backup-if-mismatch to avoid .orig files in vendor/
    patch -Np1 --forward --no-backup-if-mismatch \
          -i "${IGOS_SOURCES}/glycin-2.0.8-xbm_xpm-1.patch" || true

    # Clear cargo checksums for any patched vendor crates
    # (cargo rejects modified vendored files otherwise)
    for cs in vendor/*/.cargo-checksum.json; do
        sed -i 's/"files":{[^}]*}/"files":{}/' "$cs" 2>/dev/null
    done

    export PATH="/opt/rustc/bin:$PATH"

    mkdir build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
          --buildtype=release   \
          -D libglycin-gtk4=false \
          -D tests=false
}

build() {
    cd build
    export PATH="/opt/rustc/bin:$PATH"
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
```

---
### samba
```yaml
name: samba
version: "4.23.5"
release: 1
description: SMB/CIFS file and print server
license: GPL-3.0-or-later
homepage: https://www.samba.org/
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://www.samba.org/ftp/samba/stable/samba-${version}.tar.gz
  sha256: 593a43ddd0d57902237dfa76888f7b02cb7fc7747111369cb31e126db4836b9f
dependencies:
  build:
  - gnutls
  - gpgme
  - jansson
  - mitkrb
  - openldap
  - perl-parse-yapp
  - rpcsvc-proto
  - fuse3
  - libtirpc
  host: []
  runtime: []
```
```bash
#!/bin/bash
# samba 4.23.5 — SMB/CIFS file and print server
# BLFS 13.0

configure() {
    ./configure                                \
        --prefix=/usr                          \
        --sysconfdir=/etc                      \
        --localstatedir=/var                   \
        --with-piddir=/run/samba               \
        --with-pammodulesdir=/usr/lib/security \
        --enable-fhs                           \
        --without-ad-dc                        \
        --with-system-mitkrb5                  \
        --disable-rpath-install
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make quicktest || true
}

do_install() {
    # Fix hard coded Python paths
    sed '1s@^.*$@#!/usr/bin/python3@' \
        -i ./bin/default/source4/scripting/bin/*.inst

    make DESTDIR="$DESTDIR" install

    install -v -m644 examples/smb.conf.default "${DESTDIR}/etc/samba/"

    sed -e "s;log file =.*;log file = /var/log/samba/%m.log;"   \
        -e "s;path = /usr/spool/samba;path = /var/spool/samba;" \
        -i "${DESTDIR}/etc/samba/smb.conf.default"

    # Install LDAP schema files
    mkdir -pv "${DESTDIR}/etc/openldap/schema"
    install -v -m644 examples/LDAP/README \
                     "${DESTDIR}/etc/openldap/schema/README.samba"
    install -v -m644 examples/LDAP/samba* \
                     "${DESTDIR}/etc/openldap/schema"
    install -v -m755 examples/LDAP/{get*,ol*} \
                     "${DESTDIR}/etc/openldap/schema"
}
```

---
### spidermonkey
```yaml
name: spidermonkey
version: "140.8.0"
release: 1
description: Mozilla SpiderMonkey JavaScript engine
license: MPL-2.0
homepage: https://spidermonkey.dev/
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://archive.mozilla.org/pub/firefox/releases/${version}esr/source/firefox-${version}esr.source.tar.xz
  sha256: 57a7f339ef68273f6597d8074a841fa053f63a21d1f609ab0074a26c063282e6
patches:
  - spidermonkey-140.8.0-python_3.14_fixes-1.patch
dependencies:
  build:
  - cbindgen
  - llvm
  - rust
  - icu
  host: []
  runtime: []
```
```bash
#!/bin/bash
# spidermonkey 140.8.0 — Mozilla SpiderMonkey JavaScript engine
# BLFS 13.0
# Note: source is from firefox ESR tarball

configure() {
    # Apply Python 3.14 compatibility patch

    mkdir obj &&
    cd    obj &&

    CC=gcc CXX=g++ \
    ../js/src/configure --prefix=/usr            \
                        --disable-debug-symbols  \
                        --disable-jemalloc       \
                        --enable-readline        \
                        --enable-rust-simd       \
                        --with-intl-api          \
                        --with-system-icu        \
                        --with-system-zlib
}

build() {
    cd obj &&
    make -j${IGOS_JOBS}
}

check() {
    cd obj &&
    make -C js/src check-jstests \
         JSTESTS_EXTRA_ARGS="--timeout 300 --wpt=disabled" || true
}

do_install() {
    cd obj &&

    # Remove old shared lib to avoid crash on reinstall
    rm -fv "${DESTDIR}/usr/lib/libmozjs-"*.so 2>/dev/null || true

    make DESTDIR="$DESTDIR" install

    # Remove static lib
    rm -v "${DESTDIR}/usr/lib/libjs_static.ajs" 2>/dev/null || true

    # Fix js config
    sed -i '/@NSPR_CFLAGS@/d' "${DESTDIR}/usr/bin/js"*-config 2>/dev/null || true
}

post_install() {
    # Fix header for XP_UNIX define
    local jsver="${version%%.*}"
    sed "\$i#define XP_UNIX" -i "/usr/include/mozjs-${jsver}/js-config.h" 2>/dev/null || true
}
```

---
### rust
```yaml
name: rust
version: "1.93.1"
release: 1
description: Rust programming language
license: MIT
homepage: https://www.rust-lang.org/
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://static.rust-lang.org/dist/rustc-${version}-src.tar.xz
  sha256: 848c9171212c998c069e6979a205a1a44fa3235a463696d62e24701c83596ce0
# Bootstrap compiler (1.92.0) — pre-downloaded for offline chroot builds
- url: https://static.rust-lang.org/dist/2025-12-11/rustc-1.92.0-x86_64-unknown-linux-gnu.tar.xz
  sha256: 78b2dd9c6b1fcd2621fa81c611cf5e2d6950690775038b585c64f364422886e0
- url: https://static.rust-lang.org/dist/2025-12-11/cargo-1.92.0-x86_64-unknown-linux-gnu.tar.xz
  sha256: e5e12be2c7126a7036c8adf573078a28b92611f5767cc9bd0a6f7c83081df103
- url: https://static.rust-lang.org/dist/2025-12-11/rust-std-1.92.0-x86_64-unknown-linux-gnu.tar.xz
  sha256: 5f106805ed86ebf8df287039e53a45cf974391ef4d088c2760776b05b8e48b5d
dependencies:
  build:
  - llvm
  - cmake
  - curl
  host: []
  runtime: []
```
```bash
#!/bin/bash
# rust 1.93.1 — Rust programming language
# BLFS 13.0
# Note: requires internet connection for bootstrap download

configure() {
    # Place pre-downloaded bootstrap tarballs where x.py expects them
    # so it skips the network download. Date from src/stage0.
    mkdir -pv build/cache/2025-12-11
    cp -v "${IGOS_SOURCES}/rustc-1.92.0-x86_64-unknown-linux-gnu.tar.xz" \
          "${IGOS_SOURCES}/cargo-1.92.0-x86_64-unknown-linux-gnu.tar.xz" \
          "${IGOS_SOURCES}/rust-std-1.92.0-x86_64-unknown-linux-gnu.tar.xz" \
          build/cache/2025-12-11/

    cat << EOF > bootstrap.toml
# See bootstrap.toml.example for more possible options,
# and see src/bootstrap/defaults/bootstrap.dist.toml for a few options
# automatically set when building from a release tarball
# (unfortunately, we have to override many of them).

# Tell x.py that the editors have reviewed the content of this file
# and updated it to follow the major changes of the building system,
# so x.py will not warn users to review that information.
change-id = 148795

[llvm]
# When using the system installed copy of LLVM, prefer the shared libraries
link-shared = true

# If building the shipped LLVM source, only enable the x86 target
# instead of all the targets supported by LLVM.
targets = "X86"

[build]
description = "for InterGenOS"

# Omit the documentation to save time and space (the default is to build them).
docs = false

# Do not look for new versions of the dependencies online.
locked-deps = true

# Only install these extended tools. Cargo, clippy, rustdoc, and rustfmt
# are installed by a default rustup installation, and rust-src is needed
# to build the Rust code in Linux kernel (in case you need such a kernel
# feature).
tools = ["cargo", "clippy", "rustdoc", "rustfmt", "src"]

[install]
prefix = "/opt/rustc-${version}"
docdir = "share/doc/rustc-${version}"

[rust]
channel = "stable"

# Enable the same optimizations as the official upstream build.
lto = "thin"
codegen-units = 1

# Don't build llvm-bitcode-linker which is only useful for the NVPTX
# backend that we don't enable.
llvm-bitcode-linker = false

[target.x86_64-unknown-linux-gnu]
llvm-config = "/usr/bin/llvm-config"

[target.i686-unknown-linux-gnu]
llvm-config = "/usr/bin/llvm-config"
EOF
}

build() {
    export LIBSSH2_SYS_USE_PKG_CONFIG=1
    export LIBSQLITE3_SYS_USE_PKG_CONFIG=1
    ./x.py build
}

do_install() {
    export LIBSSH2_SYS_USE_PKG_CONFIG=1
    export LIBSQLITE3_SYS_USE_PKG_CONFIG=1

    # Create prefix directory and symlink
    mkdir -pv "${DESTDIR}/opt/rustc-${version}"
    ln -svfn "rustc-${version}" "${DESTDIR}/opt/rustc"

    DESTDIR="$DESTDIR" ./x.py install
}

post_install() {
    # Fix up docs
    rm -fv /opt/rustc-${version}/share/doc/rustc-${version}/*.old
    install -vm644 README.md /opt/rustc-${version}/share/doc/rustc-${version}

    # Zsh completions
    install -vdm755 /usr/share/zsh/site-functions
    ln -sfv /opt/rustc/share/zsh/site-functions/_cargo \
        /usr/share/zsh/site-functions

    # Bash completions
    mv -v /etc/bash_completion.d/cargo /usr/share/bash-completion/completions

    # PATH setup
    cat > /etc/profile.d/rustc.sh << "PROFILE"
# Begin /etc/profile.d/rustc.sh

pathprepend /opt/rustc/bin           PATH

# End /etc/profile.d/rustc.sh
PROFILE

    # Ensure symlink exists on live system
    ln -svfn rustc-${version} /opt/rustc

    unset LIBSSH2_SYS_USE_PKG_CONFIG
    unset LIBSQLITE3_SYS_USE_PKG_CONFIG
}
```

---
### llvm
```yaml
name: llvm
version: "21.1.8"
release: 1
description: LLVM compiler infrastructure
license: Apache-2.0
homepage: https://llvm.org/
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://github.com/llvm/llvm-project/releases/download/llvmorg-${version}/llvm-${version}.src.tar.xz
  sha256: d9022ddadb40a15015f6b27e6549a7144704ded8828ba036ffe4b8165707de21
# Additional tarballs extracted by pre_configure in build.sh
- url: https://anduin.linuxfromscratch.org/BLFS/llvm/llvm-cmake-${version}.src.tar.xz
  sha256: 85735f20fd8c81ecb0a09abb0c267018475420e93b65050cc5b7634eab744de9
- url: https://anduin.linuxfromscratch.org/BLFS/llvm/llvm-third-party-${version}.src.tar.xz
  sha256: 7fe99424384aea529ffaeec9cc9dfb8b451fd1852c03fc109e426fe208a1f1a7
- url: https://github.com/llvm/llvm-project/releases/download/llvmorg-${version}/clang-${version}.src.tar.xz
  sha256: 6090e3f23720d003cdd84483a47d0eec6d01adbb5e0c714ac0c8b58de546aa62
- url: https://github.com/llvm/llvm-project/releases/download/llvmorg-${version}/compiler-rt-${version}.src.tar.xz
  sha256: dd54ae21aee1780fac59445b51ebff601ad016b31ac3a7de3b21126fd3ccb229
dependencies:
  build:
  - cmake
  host: []
  runtime: []
```
```bash
#!/bin/bash
# llvm 21.1.8 — LLVM compiler infrastructure
# BLFS 13.0
# Note: requires clang, cmake-modules, and third-party tarballs in sources dir

pre_configure() {
    # Extract additional required tarballs
    tar -xf "${IGOS_SOURCES_DIR}/llvm-cmake-${version}.src.tar.xz"
    tar -xf "${IGOS_SOURCES_DIR}/llvm-third-party-${version}.src.tar.xz"

    # Fix paths to extracted cmake and third-party directories
    sed "/LLVM_COMMON_CMAKE_UTILS/s@../cmake@cmake-${version}.src@" \
        -i CMakeLists.txt
    sed "/LLVM_THIRD_PARTY_DIR/s@../third-party@third-party-${version}.src@" \
        -i cmake/modules/HandleLLVMOptions.cmake

    # Extract clang into the source tree
    tar -xf "${IGOS_SOURCES_DIR}/clang-${version}.src.tar.xz" -C tools
    mv tools/clang-${version}.src tools/clang

    # Extract compiler-rt if available
    if [ -f "${IGOS_SOURCES_DIR}/compiler-rt-${version}.src.tar.xz" ]; then
        tar -xf "${IGOS_SOURCES_DIR}/compiler-rt-${version}.src.tar.xz" -C projects
        mv projects/compiler-rt-${version}.src projects/compiler-rt
    fi

    # Fix Python scripts to use python3
    grep -rl '#!.*python' | xargs sed -i '1s/python$/python3/'

    # Ensure FileCheck is installed (needed by rust test suite and others)
    sed 's/utility/tool/' -i utils/FileCheck/CMakeLists.txt
}

configure() {
    pre_configure

    mkdir -v build
    cd       build

    CC=gcc CXX=g++                                   \
    cmake -D CMAKE_INSTALL_PREFIX=/usr               \
          -D CMAKE_SKIP_INSTALL_RPATH=ON             \
          -D LLVM_ENABLE_FFI=ON                      \
          -D CMAKE_BUILD_TYPE=Release                \
          -D LLVM_BUILD_LLVM_DYLIB=ON                \
          -D LLVM_LINK_LLVM_DYLIB=ON                 \
          -D LLVM_ENABLE_RTTI=ON                     \
          -D LLVM_TARGETS_TO_BUILD="host;AMDGPU"     \
          -D LLVM_BINUTILS_INCDIR=/usr/include       \
          -D LLVM_INCLUDE_BENCHMARKS=OFF             \
          -D CLANG_DEFAULT_PIE_ON_LINUX=ON           \
          -D CLANG_CONFIG_FILE_SYSTEM_DIR=/etc/clang \
          -D CMAKE_POLICY_VERSION_MINIMUM=3.5 \
          -W no-dev -G Ninja ..
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
    # Create clang SSP configuration files
    mkdir -pv /etc/clang
    for i in clang clang++; do
        echo -fstack-protector-strong > /etc/clang/$i.cfg
    done
}
```

---
### gvfs
```yaml
name: gvfs
version: "1.58.2"
release: 1
description: GNOME virtual filesystem
license: LGPL-2.0-or-later
homepage: ''
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://download.gnome.org/sources/gvfs/1.58/gvfs-${version}.tar.xz
  sha256: ad9d5bf0b45caf232520df0adee51eb650200b0370680f80a350ead9d1d61ddf
dependencies:
  build:
  - avahi
  - gcr4
  - gtk3
  - libcdio-paranoia
  - libbluray
  - libgphoto2
  - libimobiledevice
  - libmtp
  - libnfs
  - libsecret
  - libusb
  - udisks2
  host: []
  runtime: []
```
```bash
#!/bin/bash
# gvfs 1.58.2 — GNOME virtual filesystem
# BLFS 13.0
#
# All backends enabled except:
#   -Dgoogle=false — libgdata deprecated by Google, removed from BLFS (owner approved)

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dman=false \
          -Dgoogle=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
```

---
### gvfs-pass2
```yaml
name: gvfs-pass2
version: "1.58.2"
release: 1
description: "GNOME virtual filesystem (pass 2 — with GOA and OneDrive)"
license: LGPL-2.0-or-later
homepage: https://wiki.gnome.org/Projects/gvfs
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://download.gnome.org/sources/gvfs/1.58/gvfs-${version}.tar.xz
  sha256: ad9d5bf0b45caf232520df0adee51eb650200b0370680f80a350ead9d1d61ddf
dependencies:
  build:
  - gvfs
  - gnome-online-accounts
  - libmsgraph
  host: []
  runtime: []
```
```bash
#!/bin/bash
# gvfs 1.58.2 — pass 2 rebuild with GOA and OneDrive support
# BLFS 13.0
#
# Pass 1 builds before gnome-online-accounts (no GOA/OneDrive).
# This pass rebuilds after GOA is available, enabling cloud backends.
#
# Only -Dgoogle=false remains (libgdata deprecated, owner approved).

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dman=false \
          -Dgoogle=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
```

---
### avahi
```yaml
name: avahi
version: "0.8"
release: 1
description: Service Discovery for Linux using mDNS/DNS-SD
license: LGPL-2.1-or-later
homepage: https://avahi.org/
tier: desktop
build_style: autotools
install_func: do_install
source:
- url: https://github.com/lathiat/avahi/releases/download/v${version}/avahi-${version}.tar.gz
  sha256: 060309d7a333d38d951bc27598c677af1796934dbd98e1024e7ad8de798fedda
patches:
- url: https://www.linuxfromscratch.org/patches/blfs/13.0/avahi-${version}-ipv6_race_condition_fix-1.patch
  sha256: 218c909581d0ca2c86c8145bb0797050d987a6b0ae3417949dbe2a6d55c49360
dependencies:
  build:
  - glib2
  - libdaemon
  - gtk3
  host: []
  runtime: []
```
```bash
#!/bin/bash
# avahi 0.8 — Service Discovery for Linux using mDNS/DNS-SD
# BLFS 13.0

configure() {
    # Apply IPv6 race condition fix (BLFS required patch)
    patch -Np1 -i ../avahi-0.8-ipv6_race_condition_fix-1.patch

    # Fix security vulnerability in avahi-daemon (BLFS)
    sed -i '426a if (events & AVAHI_WATCH_HUP) { \
client_free(c); \
return; \
}' avahi-daemon/simple-protocol.c

    ./configure --prefix=/usr        \
                --sysconfdir=/etc    \
                --localstatedir=/var \
                --disable-static     \
                --disable-libevent   \
                --disable-mono       \
                --disable-monodoc    \
                --disable-python     \
                --disable-qt3        \
                --disable-qt4        \
                --disable-qt5        \
                --enable-core-docs   \
                --with-distro=none   \
                --with-dbus-system-address='unix:path=/run/dbus/system_bus_socket'
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}

post_install() {
    # Create avahi system user and group (BLFS: uid/gid 84)
    groupadd -fg 84 avahi 2>/dev/null || true
    useradd -c "Avahi Daemon Owner" -d /run/avahi-daemon -u 84 \
            -g avahi -s /bin/false avahi 2>/dev/null || true

    # Create privileged access group for Avahi clients
    groupadd -fg 86 netdev 2>/dev/null || true
}
```

---
### libdaemon
```yaml
name: libdaemon
version: "0.14"
release: 1
description: Lightweight C library for writing UNIX daemons
license: LGPL-2.1-or-later
homepage: https://0pointer.de/lennart/projects/libdaemon/
tier: desktop
build_style: autotools
install_func: do_install
source:
- url: https://0pointer.de/lennart/projects/libdaemon/libdaemon-${version}.tar.gz
  sha256: fd23eb5f6f986dcc7e708307355ba3289abe03cc381fc47a80bca4a50aa6b834
dependencies:
  build: []
  host: []
  runtime: []
```
```bash
#!/bin/bash
# libdaemon 0.14 — Lightweight C library for writing UNIX daemons
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
```

---
### graphviz
```yaml
name: graphviz
version: "14.1.2"
release: 1
description: Graph visualization software
license: EPL-1.0
homepage: https://graphviz.org/
tier: desktop
build_style: cmake
install_func: do_install
source:
- url: https://gitlab.com/graphviz/graphviz/-/archive/${version}/graphviz-${version}.tar.bz2
  sha256: 8ba7611c378b3e82f2a0ca5fc9dbcc5fef77c86d9fdfe3281f8f59eaab3314f6
dependencies:
  build:
  - cmake
  host: []
  runtime: []
```
```bash
#!/bin/bash
# graphviz 14.1.2 — Graph visualization software
# BLFS 13.0

configure() {
    # Prevent hard coding library rpath into shared libraries (BLFS)
    sed '/ORIGIN/d' -i lib/CMakeLists.txt

    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release

    # Fix gzip compression in man pages (BLFS)
    sed -i '/GZIP/s:.*$/=/' build/CMakeCache.txt
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
```

---
### gtksourceview5
```yaml
name: gtksourceview5
version: "5.18.0"
release: 1
description: Source code editing widget for GTK4
license: LGPL-2.1-or-later
homepage: https://wiki.gnome.org/Projects/GtkSourceView
tier: desktop
build_style: meson
install_func: do_install
source:
- url: https://download.gnome.org/sources/gtksourceview/5.18/gtksourceview-${version}.tar.xz
  sha256: 051a78fe38f793328047e5bcd6d855c6425c0b480c20d9432179e356742c6ac0
dependencies:
  build:
  - gtk4
  host: []
  runtime: []
```
```bash
#!/bin/bash
# gtksourceview5 5.18.0 — Source code editing widget for GTK4
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
```

---
### libcdio
```yaml
name: libcdio
version: "2.1.0"
release: 1
description: GNU Compact Disc Input and Control library
license: GPL-3.0-or-later
homepage: https://www.gnu.org/software/libcdio/
tier: desktop
build_style: autotools
install_func: do_install
source:
- url: https://ftpmirror.gnu.org/libcdio/libcdio-${version}.tar.bz2
  sha256: 8550e9589dbd594bfac93b81ecf129b1dc9d0d51e90f9696f1b2f9b2af32712b
dependencies:
  build: []
  host: []
  runtime: []
```
```bash
#!/bin/bash
# libcdio 2.1.0 — GNU Compact Disc Input and Control library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
```

---
### libcdio-paranoia
```yaml
name: libcdio-paranoia
version: "10.2+2.0.2"
release: 1
description: CD paranoia library from libcdio
license: GPL-3.0-or-later
homepage: https://www.gnu.org/software/libcdio/
tier: desktop
build_style: autotools
install_func: do_install
source:
- url: https://ftpmirror.gnu.org/libcdio/libcdio-paranoia-${version}.tar.bz2
  sha256: 186892539dedd661276014d71318c8c8f97ecb1250a86625256abd4defbf0d0c
dependencies:
  build:
  - libcdio
  host: []
  runtime: []
```
```bash
#!/bin/bash
# libcdio-paranoia 10.2+2.0.2 — CD paranoia library from libcdio
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
```

---
### libmtp
```yaml
name: libmtp
version: "1.1.23"
release: 1
description: MTP media device access library
license: LGPL-2.1-or-later
homepage: https://github.com/libmtp/libmtp
tier: desktop
build_style: autotools
install_func: do_install
source:
- url: https://github.com/libmtp/libmtp/releases/download/v${version}/libmtp-${version}.tar.gz
  sha256: 74a2b6e8cb4a0304e95b995496ea3ac644c29371649b892b856e22f12a0bdeed
dependencies:
  build: []
  host: []
  runtime:
  - libusb
configure_flags:
- --prefix=/usr
- --disable-static
- --with-udev=/usr/lib/udev
```
```bash
#!/bin/bash
# libmtp 1.1.23 — MTP media device access library
# Not in BLFS — standard autotools

configure() {
    ./configure --prefix=/usr       \
                --disable-static    \
                --with-udev=/usr/lib/udev
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
```

---
### libnfs
```yaml
name: libnfs
version: "6.0.2"
release: 1
description: NFS client library
license: LGPL-2.1-or-later
homepage: https://github.com/sahlberg/libnfs
tier: desktop
build_style: cmake
install_func: do_install
source:
- url: https://github.com/sahlberg/libnfs/archive/libnfs-${version}.tar.gz
  sha256: 4e5459cc3e0242447879004e9ad28286d4d27daa42cbdcde423248fad911e747
dependencies:
  build: []
  host: []
  runtime: []
```
```bash
#!/bin/bash
# libnfs 6.0.2 — NFS client library
# Not in BLFS — standard cmake

configure() {
    cmake -B build                            \
          -DCMAKE_INSTALL_PREFIX=/usr         \
          -DCMAKE_BUILD_TYPE=Release
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
```

---
### libbluray
```yaml
name: libbluray
version: "1.4.1"
release: 1
description: Blu-ray disc playback library
license: LGPL-2.1-or-later
homepage: https://www.videolan.org/developers/libbluray.html
tier: desktop
build_style: autotools
install_func: do_install
source:
- url: https://download.videolan.org/pub/videolan/libbluray/${version}/libbluray-${version}.tar.xz
  sha256: 76b5dc40097f28dca4ebb009c98ed51321b2927453f75cc72cf74acd09b9f449
dependencies:
  build: []
  host: []
  runtime:
  - libxml2
  - fontconfig
configure_flags:
- --prefix=/usr
- --disable-static
- --disable-bdjava-jar
```
```bash
#!/bin/bash
# libbluray 1.4.1 — Blu-ray disc playback library
# Not in BLFS — standard autotools

configure() {
    ./configure --prefix=/usr          \
                --disable-static       \
                --disable-bdjava-jar
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
```

---
### libgphoto2
```yaml
name: libgphoto2
version: "2.5.33"
release: 1
description: Digital camera access library
license: LGPL-2.1-or-later
homepage: https://github.com/gphoto/libgphoto2
tier: desktop
build_style: autotools
install_func: do_install
source:
- url: https://github.com/gphoto/libgphoto2/releases/download/v${version}/libgphoto2-${version}.tar.gz
  sha256: fc994b550dfba6575ce0495c7246b36fff0d24e1bb2a0feb835f4da77b2ffc6b
dependencies:
  build: []
  host: []
  runtime:
  - libusb
  - libexif
  - curl
configure_flags:
- --prefix=/usr
- --disable-static
```
```bash
#!/bin/bash
# libgphoto2 2.5.33 — Digital camera access library
# Not in BLFS — standard autotools

configure() {
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
```

---
### libplist
```yaml
name: libplist
version: "2.7.0"
release: 1
description: Apple property list library
license: LGPL-2.1-or-later
homepage: https://github.com/libimobiledevice/libplist
tier: desktop
build_style: autotools
install_func: do_install
source:
- url: https://github.com/libimobiledevice/libplist/releases/download/${version}/libplist-${version}.tar.bz2
  sha256: 7ac42301e896b1ebe3c654634780c82baa7cb70df8554e683ff89f7c2643eb8b
dependencies:
  build: []
  host: []
  runtime: []
configure_flags:
- --prefix=/usr
- --disable-static
```
```bash
#!/bin/bash
# libplist 2.7.0 — Apple property list library
# Not in BLFS — standard autotools

configure() {
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
```

---
### libimobiledevice-glue
```yaml
name: libimobiledevice-glue
version: "1.3.2"
release: 1
description: Common code for libimobiledevice libraries
license: LGPL-2.1-or-later
homepage: https://github.com/libimobiledevice/libimobiledevice-glue
tier: desktop
build_style: autotools
install_func: do_install
source:
- url: https://github.com/libimobiledevice/libimobiledevice-glue/releases/download/${version}/libimobiledevice-glue-${version}.tar.bz2
  sha256: 6489a3411b874ecd81c87815d863603f518b264a976319725e0ed59935546774
dependencies:
  build: []
  host: []
  runtime:
  - libplist
configure_flags:
- --prefix=/usr
- --disable-static
```
```bash
#!/bin/bash
# libimobiledevice-glue 1.3.2 — Common code for libimobiledevice libraries
# Not in BLFS — standard autotools

configure() {
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
```

---
### libusbmuxd
```yaml
name: libusbmuxd
version: "2.1.1"
release: 1
description: USB multiplexing daemon client library
license: LGPL-2.1-or-later
homepage: https://github.com/libimobiledevice/libusbmuxd
tier: desktop
build_style: autotools
install_func: do_install
source:
- url: https://github.com/libimobiledevice/libusbmuxd/releases/download/${version}/libusbmuxd-${version}.tar.bz2
  sha256: 5546f1aba1c3d1812c2b47d976312d00547d1044b84b6a461323c621f396efce
dependencies:
  build: []
  host: []
  runtime:
  - libplist
  - libimobiledevice-glue
configure_flags:
- --prefix=/usr
- --disable-static
```
```bash
#!/bin/bash
# libusbmuxd 2.1.1 — USB multiplexing daemon client library
# Not in BLFS — standard autotools

configure() {
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
```

---
### libimobiledevice
```yaml
name: libimobiledevice
version: "1.4.0"
release: 1
description: Apple mobile device access library
license: LGPL-2.1-or-later
homepage: https://github.com/libimobiledevice/libimobiledevice
tier: desktop
build_style: autotools
install_func: do_install
source:
- url: https://github.com/libimobiledevice/libimobiledevice/releases/download/${version}/libimobiledevice-${version}.tar.bz2
  sha256: 23cc0077e221c7d991bd0eb02150a0d49199bcca1ddf059edccee9ffd914939d
dependencies:
  build: []
  host: []
  runtime:
  - libplist
  - libimobiledevice-glue
  - libusbmuxd
  - libusb
  - openssl
configure_flags:
- --prefix=/usr
- --disable-static
```
```bash
#!/bin/bash
# libimobiledevice 1.4.0 — Apple mobile device access library
# Not in BLFS — standard autotools

configure() {
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
```

---
### libmsgraph
```yaml
name: libmsgraph
version: "0.3.4"
release: 1
description: Microsoft Graph API client library
license: LGPL-3.0-or-later
homepage: https://gitlab.gnome.org/GNOME/msgraph
tier: desktop
build_style: meson
install_func: do_install
source:
- url: https://download.gnome.org/sources/msgraph/0.3/msgraph-${version}.tar.xz
  sha256: 0731ece6b02b32eeffbbbd98efdc77bc03ddd20651eeae3a4343f0879b04d6c7
dependencies:
  build: []
  host: []
  runtime:
  - glib2
  - json-glib
  - libsoup3
  - gnome-online-accounts
```
```bash
#!/bin/bash
# libmsgraph 0.3.4 — Microsoft Graph API client library
# Not in BLFS — standard meson

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
```

---
### vala
```yaml
name: vala
version: "0.56.18"
release: 1
description: Vala programming language compiler
license: LGPL-2.1-or-later
homepage: https://wiki.gnome.org/Projects/Vala
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://download.gnome.org/sources/vala/0.56/vala-${version}.tar.xz
  sha256: f2affe7d40ab63db8e7b9ecc3f6bdc9c2fc7e3134c84ff2d795f482fe926a382
patches:
  - vala-0.56.18-graphviz_13_fix-1.patch
dependencies:
  build:
  - glib2
  - graphviz
  host: []
  runtime: []
configure_flags:
- --prefix=/usr
- --disable-static
```
```bash
#!/bin/bash
# vala 0.56.18 — Vala programming language compiler
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    # BLFS: make bootstrap rebuilds the compiler using itself
    make bootstrap -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
```

---
### libsecret
```yaml
name: libsecret
version: "0.21.7"
release: 1
description: Library for accessing secrets stored in the keyring
license: LGPL-2.1-or-later
homepage: https://wiki.gnome.org/Projects/Libsecret
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://download.gnome.org/sources/libsecret/0.21/libsecret-${version}.tar.xz
  sha256: 6b452e4750590a2b5617adc40026f28d2f4903de15f1250e1d1c40bfd68ed55e
dependencies:
  build:
  - glib2
  - libgcrypt
  - libxslt
  - vala
  host: []
  runtime: []
```
```bash
#!/bin/bash
# libsecret 0.21.7 — Library for accessing secrets stored in the keyring
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed "s/api_version_major/'${PKG_VERSION}'/" -i ../docs/reference/libsecret/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgtk_doc=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
```

---
### librest
```yaml
name: librest
version: "0.10.2"
release: 1
description: REST web service access library
license: LGPL-2.1-or-later
homepage: ''
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://download.gnome.org/sources/librest/0.10/librest-${version}.tar.xz
  filename: librest-${version}.tar.xz
  sha256: 7b6cb912bb3a22cfa7dcf005925dcb62883024db0c09099486e7d6851185c9b8
dependencies:
  build:
  - gtksourceview5
  - json-glib
  - libadwaita1
  - libsoup3
  - make-ca
  host: []
  runtime: []
```
```bash
#!/bin/bash
# librest 0.10.2 — REST web service access library
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgtk_doc=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
```

---
### libpwquality
```yaml
name: libpwquality
version: "1.4.5"
release: 1
description: Password quality checking library
license: GPL-2.0-or-later
homepage: ''
tier: desktop
build_style: autotools
install_func: do_install
source:
- url: https://github.com/libpwquality/libpwquality/releases/download/libpwquality-${version}/libpwquality-${version}.tar.bz2
  sha256: 6fcf18b75d305d99d04d2e42982ed5b787a081af2842220ed63287a2d6a10988
dependencies:
  build:
  - cracklib
  - linux-pam
  host: []
  runtime: []
configure_flags:
- --prefix=/usr
- --disable-static
- --with-securedir=/usr/lib/security
- --disable-python-bindings
```
```bash
#!/bin/bash
# libpwquality 1.4.5 — Password quality checking library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static \
                --with-securedir=/usr/lib/security
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
```

---
### argcomplete
```yaml
name: argcomplete
version: "3.6.3"
release: 1
description: Python tab-completion for argparse
license: Apache-2.0
homepage: https://github.com/kislyuk/argcomplete
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://files.pythonhosted.org/packages/source/a/argcomplete/argcomplete-${version}.tar.gz
  sha256: 62e8ed4fd6a45864acc8235409461b72c9a28ee785a2011cc5eb78318786c89c
dependencies:
  build:
  - hatchling
  host: []
  runtime: []
```
```bash
#!/bin/bash
# argcomplete 3.6.3 — Python tab-completion for argparse
# Required by mutter build tools

configure() {
    :
}

build() {
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    pip3 install --no-index --find-links dist --no-user \
         --root="$DESTDIR" --no-deps argcomplete
}
```

---
### vte
```yaml
name: vte
version: "0.82.3"
release: 1
description: Virtual Terminal Emulator widget
license: LGPL-2.1-or-later
homepage: https://wiki.gnome.org/Apps/Terminal/VTE
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://download.gnome.org/sources/vte/0.82/vte-${version}.tar.xz
  sha256: 6dc6278f6fee30d07d1a03e2ba3335b1ea4e8d2956ceb59d861943115d930a85
dependencies:
  build:
  - gtk3
  - gtk4
  - icu
  - gnutls
  - fribidi
  - vala
  - libxml2
  host: []
  runtime: []
```
```bash
#!/bin/bash
# vte 0.82.3 — Virtual Terminal Emulator widget
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -e "/docdir =/s@\$@/ 'vte-${PKG_VERSION}'@" -i doc/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Da11y=true \
          -Dgtk3=true \
          -Dgtk4=true \
          -Db_lto=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
    rm -fv /etc/profile.d/vte.*
}
```

---
### gnome-keyring
```yaml
name: gnome-keyring
version: '48.0'
release: 1
description: GNOME password and secret storage
license: GPL-2.0-or-later
homepage: https://wiki.gnome.org/Projects/GnomeKeyring
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://download.gnome.org/sources/gnome-keyring/48/gnome-keyring-${version}.tar.xz
  sha256: f20518c920e9ea3f9c9b8b44be8c50d8d7feecd0dd5624960f77bd2ca4fbeb9d
dependencies:
  build:
  - gcr
  - linux-pam
  host: []
  runtime: []
configure_flags:
- --prefix=/usr
- --with-pam-dir=/usr/lib/security
```
```bash
#!/bin/bash
# gnome-keyring 48.0 — GNOME password and secret storage
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -i 's:"/desktop:"/org:' schema/*.xml

    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -D selinux=disabled
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
```

---
### ibus
```yaml
name: ibus
version: "1.5.33"
release: 1
description: Intelligent Input Bus framework
license: LGPL-2.1-or-later
homepage: ''
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://github.com/ibus/ibus/archive/${version}/ibus-${version}.tar.gz
  sha256: a777068caba4ba7599a3214e2966f564daec01bba94cd7f2ca63062107e0604f
- url: https://www.unicode.org/Public/UCD/latest/ucd/UCD.zip
  sha256: 2066d1909b2ea93916ce092da1c0ee4808ea3ef8407c94b4f14f5b7eb263d28e
dependencies:
  build:
  - dconf
  - gtk3
  - gtk4
  - iso-codes
  - libarchive
  - vala
  host: []
  runtime: []
configure_flags:
- --prefix=/usr
- --sysconfdir=/etc
```
```bash
#!/bin/bash
# ibus 1.5.33 — Intelligent Input Bus framework
# BLFS 13.0

configure() {
    # Install Unicode Character Database if not already present
    # ibus configure requires UCD files at /usr/share/unicode/ucd/
    if [ ! -f /usr/share/unicode/ucd/NamesList.txt ]; then
        if [ -f "${IGOS_SOURCES}/UCD.zip" ]; then
            mkdir -p /usr/share/unicode/ucd
            unzip -o "${IGOS_SOURCES}/UCD.zip" -d /usr/share/unicode/ucd
        fi
    fi

    # BLFS required fixes
    sed '/docs/d;/GTK_DOC/d' -i Makefile.am configure.ac
    # Fix deprecated GSettings schema path
    sed -e 's@/desktop/ibus@/org/freedesktop/ibus@g' \
        -i data/dconf/org.freedesktop.ibus.gschema.xml

    # Handle missing gtkdocize
    if ! command -v gtkdocize &>/dev/null; then
        sed -e 's/gtkdocize/true/' -i autogen.sh
        export GTKDOCIZE=true
    fi

    SAVE_DIST_FILES=1 NOCONFIGURE=1 ./autogen.sh

    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --disable-python2 \
                --disable-appindicator \
                --disable-gtk2 \
                --disable-emoji-dict
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}

post_install() {
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
```

---
### gnome-settings-daemon
```yaml
name: gnome-settings-daemon
version: '49.1'
release: 1
description: GNOME settings daemon
license: GPL-2.0-or-later
homepage: https://gitlab.gnome.org/GNOME/gnome-settings-daemon
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://download.gnome.org/sources/gnome-settings-daemon/49/gnome-settings-daemon-${version}.tar.xz
  sha256: 2a9957fc4f91c3b9127b49484179bef485120d9c1c208e44d44e6a746e6cc1c1
dependencies:
  build:
  - alsa-lib
  - colord
  - fontconfig
  - gcr4
  - geocode-glib
  - geoclue2
  - gnome-desktop
  - libcanberra
  - libgweather
  - libnotify
  - libwacom
  - pulseaudio
  - upower
  - networkmanager
  - wayland
  host: []
  runtime: []
```
```bash
#!/bin/bash
# gnome-settings-daemon 49.1 — GNOME settings daemon
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
```

---
### webkitgtk
```yaml
name: webkitgtk
version: "2.50.5"
release: 1
description: Web content engine for GTK
license: LGPL-2.0-or-later
homepage: https://webkitgtk.org/
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://webkitgtk.org/releases/webkitgtk-${version}.tar.xz
  sha256: 8737631bac3e9c7ad3e5208f9370e076c09d9c45b39980021ce54edadcc6f94f
dependencies:
  build:
  - webkitgtk-gtk3
  - cairo
  - gst-plugins-base
  - gst-plugins-bad
  - gtk3
  - gtk4
  - libgudev
  - libsoup3
  - libwebp
  - openjpeg2
  - ruby
  - enchant
  - unifdef
  - bubblewrap
  - geoclue2
  - libseccomp
  - xdg-dbus-proxy
  - libX11
  - wayland
  - icu
  host: []
  runtime: []
```
```bash
#!/bin/bash
# webkitgtk 2.50.5 — Web content engine for GTK (GTK-4 version)
# BLFS 13.0

configure() {
    mkdir -vp build
    cd        build

    cmake -D CMAKE_BUILD_TYPE=Release         \
          -D CMAKE_INSTALL_PREFIX=/usr        \
          -D CMAKE_POLICY_VERSION_MINIMUM=3.5 \
          -D CMAKE_SKIP_INSTALL_RPATH=ON      \
          -D PORT=GTK                         \
          -D LIB_INSTALL_DIR=/usr/lib         \
          -D USE_LIBBACKTRACE=OFF             \
          -D USE_LIBHYPHEN=OFF                \
          -D ENABLE_GAMEPAD=OFF               \
          -D ENABLE_MINIBROWSER=ON            \
          -D ENABLE_DOCUMENTATION=OFF         \
          -D USE_WOFF2=OFF                    \
          -D USE_GTK4=ON                      \
          -D ENABLE_BUBBLEWRAP_SANDBOX=ON     \
          -D USE_SYSPROF_CAPTURE=NO           \
          -D ENABLE_SPEECH_SYNTHESIS=OFF      \
          -W no-dev -G Ninja ..
}

build() {
    cd build
    # Limit parallelism — WebCore unified sources each use ~2GB RAM.
    # 16 parallel jobs on 32GB RAM triggers OOM killer.
    local jobs=${IGOS_JOBS}
    [ "$jobs" -gt 8 ] && jobs=8
    ninja -j${jobs}
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
```

---
### webkitgtk-gtk3
```yaml
name: webkitgtk-gtk3
version: "2.50.5"
release: 1
description: Web content engine for GTK (GTK-3 version)
license: LGPL-2.0-or-later
homepage: https://webkitgtk.org/
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://webkitgtk.org/releases/webkitgtk-${version}.tar.xz
  sha256: 8737631bac3e9c7ad3e5208f9370e076c09d9c45b39980021ce54edadcc6f94f
dependencies:
  build:
  - cairo
  - gst-plugins-base
  - gst-plugins-bad
  - gtk3
  - libgudev
  - libsoup3
  - libwebp
  - openjpeg2
  - ruby
  - enchant
  - unifdef
  - bubblewrap
  - geoclue2
  - libseccomp
  - xdg-dbus-proxy
  - libX11
  - wayland
  - icu
  host: []
  runtime: []
```
```bash
#!/bin/bash
# webkitgtk-gtk3 2.50.5 — Web content engine for GTK (GTK-3 version)
# BLFS 13.0

configure() {
    mkdir -vp build
    cd        build

    cmake -D CMAKE_BUILD_TYPE=Release         \
          -D CMAKE_INSTALL_PREFIX=/usr        \
          -D CMAKE_POLICY_VERSION_MINIMUM=3.5 \
          -D CMAKE_SKIP_INSTALL_RPATH=ON      \
          -D PORT=GTK                         \
          -D LIB_INSTALL_DIR=/usr/lib         \
          -D USE_LIBBACKTRACE=OFF             \
          -D USE_LIBHYPHEN=OFF                \
          -D ENABLE_GAMEPAD=OFF               \
          -D ENABLE_MINIBROWSER=ON            \
          -D ENABLE_DOCUMENTATION=OFF         \
          -D USE_WOFF2=OFF                    \
          -D USE_GTK4=OFF                     \
          -D ENABLE_WEBDRIVER=OFF             \
          -D ENABLE_BUBBLEWRAP_SANDBOX=ON     \
          -D USE_SYSPROF_CAPTURE=NO           \
          -D ENABLE_SPEECH_SYNTHESIS=OFF      \
          -W no-dev -G Ninja ..
}

build() {
    cd build
    # Limit parallelism — WebCore unified sources each use ~2GB RAM.
    # 16 parallel jobs on 32GB RAM triggers OOM killer.
    local jobs=${IGOS_JOBS}
    [ "$jobs" -gt 8 ] && jobs=8
    ninja -j${jobs}
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
```

---
### mutter
```yaml
name: mutter
version: '49.4'
release: 1
description: GNOME window manager and Wayland compositor
license: GPL-2.0-or-later
homepage: https://gitlab.gnome.org/GNOME/mutter
tier: desktop
build_style: custom
install_func: do_install
source:
- url: https://download.gnome.org/sources/mutter/49/mutter-${version}.tar.xz
  sha256: c1666ec50561530be25cb88d939c2bcc2af34f01c63b8b16b82b892ee33d7855
dependencies:
  build:
  - argcomplete
  - at-spi2-core
  - glycin
  - graphene
  - gtk4
  - libei
  - libxcvt
  - libdisplay-info
  - pipewire
  - startup-notification
  - libinput
  - wayland-protocols
  - libxkbcommon
  - libX11
  - libXrandr
  - libXi
  - libXcomposite
  - libXdamage
  - libXfixes
  host: []
  runtime: []
```
```bash
#!/bin/bash
# mutter 49.4 — GNOME window manager and Wayland compositor
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed "/tests_c_args =/s/\$/ + ['-U', 'G_DISABLE_ASSERT']/" -i src/tests/meson.build
    sed "/c_args:/a '-U', 'G_DISABLE_ASSERT'," -i src/tests/cogl/unit/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dtests=disabled \
          -Ddocs=false \
          -Dprofiler=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
```
