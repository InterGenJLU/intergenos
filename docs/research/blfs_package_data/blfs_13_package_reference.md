# BLFS 13.0 Package Reference for InterGenOS
# Extracted from /home/christopher/intergenos/docs/lfs-13.0/BLFS-BOOK-13.0-systemd.html
# Date: 2026-04-01

---

## PHASE 0 — Foundation Libraries & Utilities

---

### Which-2.23
URL: https://ftpmirror.gnu.org/which/which-2.23.tar.gz
MD5: 1963b85914132d78373f02a84cdb3c86
Pre-install: none
Configure: ./configure --prefix=/usr
Build: make
Install: make install
Post-install: none
Systemd: none
Patches: none
Notes: No test suite. Alternative: create a shell script instead of building the package.

---

### Time-1.9
URL: https://ftpmirror.gnu.org/time/time-1.9.tar.gz
MD5: d2356e0fe1c0b85285d83c6b2ad51b5f
Pre-install: none
Configure: ./configure --prefix=/usr
Build: make
Install: make install
Post-install: none
Systemd: none
Patches: Fix for GCC-15 required before configure:
```
sed -i 's/sighandler interrupt_signal/__sighandler_t interrupt_signal/' src/time.c
```
Notes: Required for LSB conformance. Tests: make check

---

### Pax-20240817
URL: http://www.mirbsd.org/MirOS/dist/mir/cpio/paxmirabilis-20240817.tgz
MD5: 9a723154a4201a0892b7ff815b6753b5
Pre-install: none
Configure: none (no configure script)
Build: bash Build.sh
Install (as root):
```
install -v pax /usr/bin &&
install -v -m644 pax.1 /usr/share/man/man1
```
Post-install: none
Systemd: none
Patches: none
Notes: Expands to directory "pax" (not paxmirabilis-*). Do NOT install the hard links to cpio and tar — they overwrite GNU versions. No test suite. Required for LSB conformance.

---

### cpio-2.15
URL: https://ftpmirror.gnu.org/cpio/cpio-2.15.tar.bz2
MD5: 3394d444ca1905ea56c94b628b706a0b
Pre-install: Fix for GCC-15:
```
sed -e "/^extern int (\*xstat)/s/()/(const char * restrict,  struct stat * restrict)/" \
    -i src/extern.h
sed -e "/^int (\*xstat)/s/()/(const char * restrict,  struct stat * restrict)/" \
    -i src/global.c
```
Configure:
```
./configure --prefix=/usr \
            --enable-mt   \
            --with-rmt=/usr/libexec/rmt
```
Build:
```
make &&
makeinfo --html            -o doc/html      doc/cpio.texi &&
makeinfo --html --no-split -o doc/cpio.html doc/cpio.texi &&
makeinfo --plaintext       -o doc/cpio.txt  doc/cpio.texi
```
Install (as root):
```
make install &&
install -v -m755 -d /usr/share/doc/cpio-2.15/html &&
install -v -m644    doc/html/* \
                    /usr/share/doc/cpio-2.15/html &&
install -v -m644    doc/cpio.{html,txt} \
                    /usr/share/doc/cpio-2.15
```
Post-install: none
Systemd: none
Patches: GCC-15 sed fix above (inline, not a patch file)
Notes: --enable-mt forces building the mt program. --with-rmt prevents overwriting rmt from Tar. Tests: make check. Optional dep: texlive for PDF docs.

---

### libtasn1-4.21.0
URL: https://ftpmirror.gnu.org/libtasn1/libtasn1-4.21.0.tar.gz
MD5: 2ee1d9f3aa66f1e308c46a283aa9a8c2
Pre-install: none
Configure: ./configure --prefix=/usr --disable-static
Build: make
Install: make install
Post-install: none
Systemd: none
Patches: none
Notes: Tests: make check. Optional deps: GTK-Doc, Valgrind.

---

### libunistring-1.4.1
URL: https://ftpmirror.gnu.org/libunistring/libunistring-1.4.1.tar.xz
MD5: 7419fcbca7c0b29d3b218a09a15cbc76
Pre-install: Fix required by glibc-2.43+:
```
sed -r '/_GL_EXTERN_C/s/w?memchr|bsearch/(&)/' \
    -i $(find -name \*.in.h)
```
Configure:
```
./configure --prefix=/usr    \
            --disable-static \
            --docdir=/usr/share/doc/libunistring-1.4.1
```
Build: make
Install: make install
Post-install: none
Systemd: none
Patches: glibc-2.43 sed fix above (inline)
Notes: Tests: make check.

---

### libuv-1.52.0
URL: https://dist.libuv.org/dist/v1.52.0/libuv-v1.52.0.tar.gz
MD5: fc5065a74649e94ea84a06beb8a7e42f
Pre-install: none
Configure:
```
sh autogen.sh                              &&
./configure --prefix=/usr --disable-static
```
Build: make
Install: make install
Post-install: none
Systemd: none
Patches: none
Notes: CAUTION: If ACLOCAL env var is set (e.g. from Xorg-7), unset it before running autogen.sh. Tests: make check (run as non-root). Optional: sphinx for man page — if built, install with: install -Dm644 docs/build/man/libuv.1 /usr/share/man/man1

---

### libarchive-3.8.5
URL: https://github.com/libarchive/libarchive/releases/download/v3.8.5/libarchive-3.8.5.tar.xz
MD5: 2cd5a73ed7fe7f9da22d34ac1048534e
Pre-install: none
Configure: ./configure --prefix=/usr --disable-static
Build: make
Install (as root):
```
make install &&
ln -sfv bsdunzip /usr/bin/unzip
```
Post-install: Creates symlink bsdunzip -> unzip (replaces unmaintained Unzip package)
Systemd: none
Patches: none
Notes: Tests: make check. Optional deps: libxml2, LZO, Nettle.

---

### libtirpc-1.3.7
URL: https://downloads.sourceforge.net/libtirpc/libtirpc-1.3.7.tar.bz2
MD5: 74f97df306b8d6149d3d9898a1d44c6e
Pre-install: none
Configure:
```
./configure --prefix=/usr     \
            --sysconfdir=/etc \
            --disable-static  \
            --disable-gssapi
```
Build: make
Install: make install
Post-install: none
Systemd: none
Patches: none
Notes: No test suite. --disable-gssapi needed if MIT Kerberos not installed. If updating, also update rpcbind.

---

### NSPR-4.38.2
URL: https://archive.mozilla.org/pub/nspr/releases/v4.38.2/src/nspr-4.38.2.tar.gz
MD5: c1b2e2b3f63774bbbec25af84567135b
Pre-install: none
Configure (must cd into nspr/ first):
```
cd nspr &&

sed -i '/^RELEASE/s|^|#|' pr/src/misc/Makefile.in &&
sed -i 's|$(LIBRARY) ||'  config/rules.mk         &&

./configure --prefix=/usr   \
            --with-mozilla  \
            --with-pthreads \
            $([ $(uname -m) = x86_64 ] && echo --enable-64bit)
```
Build: make
Install: make install
Post-install: none
Systemd: none
Patches: Two sed commands above (disable installing unneeded scripts and static libs)
Notes: Source extracts with nspr/ subdirectory you must cd into. --enable-64bit required on x86_64.

---

### Popt-1.19
URL: https://ftp.osuosl.org/pub/rpm/popt/releases/popt-1.x/popt-1.19.tar.gz
MD5: eaa2135fddb6eb03f2c87ee1823e5a78
Pre-install: none
Configure: ./configure --prefix=/usr --disable-static
Build: make
Install: make install
Post-install: none
Systemd: none
Patches: none
Notes: Tests: make check. Optional: Doxygen for API docs.

---

## PHASE 1 — Security & Authentication Stack

---

### libidn2-2.3.8
URL: https://ftpmirror.gnu.org/libidn/libidn2-2.3.8.tar.gz
MD5: a8e113e040d57a523684e141970eea7a
Pre-install: none
Configure: ./configure --prefix=/usr --disable-static
Build: make
Install: make install
Post-install: none
Systemd: none
Patches: none
Notes: Recommended dep: libunistring. Tests: make check.

---

### p11-kit-0.26.2
URL: https://github.com/p11-glue/p11-kit/releases/download/0.26.2/p11-kit-0.26.2.tar.xz
MD5: 99edde5f38697ed2d47c55544347be4e
Pre-install: Prepare distribution-specific anchor hook:
```
sed '20,$ d' -i trust/trust-extract-compat &&

cat >> trust/trust-extract-compat << "EOF"
# Copy existing anchor modifications to /etc/ssl/local
/usr/libexec/make-ca/copy-trust-modifications

# Update trust stores
/usr/sbin/make-ca -r
EOF
```
Configure:
```
mkdir p11-build &&
cd    p11-build &&

meson setup ..            \
      --prefix=/usr       \
      --buildtype=release \
      -D trust_paths=/etc/pki/anchors
```
Build: ninja
Install (as root):
```
ninja install &&
ln -sfv /usr/libexec/p11-kit/trust-extract-compat \
        /usr/bin/update-ca-certificates
```
Post-install (as root): Make p11-kit trust module available to NSS:
```
ln -sfv ./pkcs11/p11-kit-trust.so /usr/lib/libnssckbi.so
```
Systemd: none
Patches: none
Notes: Recommended dep: libtasn1. Runtime dep: make-ca. Tests: ninja test. Uses meson build system.

---

### libnsl-2.0.1
URL: https://github.com/thkukuk/libnsl/releases/download/v2.0.1/libnsl-2.0.1.tar.xz
MD5: fb178645dfa85ebab0f1e42e219b42ae
Pre-install: none
Configure: ./configure --sysconfdir=/etc --disable-static
Build: make
Install: make install
Post-install: none
Systemd: none
Patches: none
Notes: Required dep: libtirpc. No test suite.

---

### Ed-1.22.5
URL: https://ftpmirror.gnu.org/ed/ed-1.22.5.tar.lz
MD5: be4d48fec1535162059b9416d913e531
Pre-install: none
Configure: ./configure --prefix=/usr
Build: make
Install: make install
Post-install: none
Systemd: none
Patches: none
Notes: Requires libarchive (bsdtar) to uncompress .tar.lz tarball. Tests: make check.

---

### NSS-3.120.1
URL: https://archive.mozilla.org/pub/security/nss/releases/NSS_3_120_1_RTM/src/nss-3.120.1.tar.gz
MD5: c9642ff2241aa38c9e81589641652a50
Patch: https://www.linuxfromscratch.org/patches/blfs/13.0/nss-standalone-1.patch
Pre-install: none
Configure & Build:
```
patch -Np1 -i ../nss-standalone-1.patch &&

cd nss &&

make BUILD_OPT=1                      \
  NSPR_INCLUDE_DIR=/usr/include/nspr  \
  USE_SYSTEM_ZLIB=1                   \
  ZLIB_LIBS=-lz                       \
  NSS_ENABLE_WERROR=0                 \
  NSS_USE_SYSTEM_SQLITE=1             \
  $([ $(uname -m) = x86_64 ] && echo USE_64=1)
```
Install (as root):
```
cd ../dist                                                          &&

install -v -m755 Linux*/lib/*.so              /usr/lib              &&
install -v -m644 Linux*/lib/{*.chk,libcrmf.a} /usr/lib              &&

install -v -m755 -d                           /usr/include/nss      &&
cp -v -RL {public,private}/nss/*              /usr/include/nss      &&

install -v -m755 Linux*/bin/{certutil,nss-config,pk12util} /usr/bin &&

install -v -m644 Linux*/lib/pkgconfig/nss.pc  /usr/lib/pkgconfig
```
Post-install (as root, if p11-kit installed):
```
ln -sfv ./pkcs11/p11-kit-trust.so /usr/lib/libnssckbi.so
```
Systemd: none
Patches: nss-standalone-1.patch (required)
Notes: Required dep: NSPR. No configure script — uses raw make. USE_64=1 required on x86_64. NSS_USE_SYSTEM_SQLITE=1 uses system sqlite. Tests are complex and long (42+ SBU).

---

### Linux-PAM-1.7.2
URL: https://github.com/linux-pam/linux-pam/releases/download/v1.7.2/Linux-PAM-1.7.2.tar.xz
MD5: 934c26eca3fada956356a30489e86291
Optional docs: https://anduin.linuxfromscratch.org/BLFS/Linux-PAM/Linux-PAM-1.7.2-docs.tar.xz (MD5: 8b0c69931e4805ee5c297192c46d0e28)
Pre-install: Kernel config requires AUDIT support enabled.
For first-time install, create test config (as root):
```
install -v -m755 -d /etc/pam.d &&

cat > /etc/pam.d/other << "EOF"
auth     required       pam_deny.so
account  required       pam_deny.so
password required       pam_deny.so
session  required       pam_deny.so
EOF
```
Configure:
```
mkdir build &&
cd    build &&

meson setup ..        \
  --prefix=/usr       \
  --buildtype=release \
  -D docdir=/usr/share/doc/Linux-PAM-1.7.2
```
Build: ninja
Test: ninja test (then remove test config: rm -fv /etc/pam.d/other)
Install (as root):
```
ninja install &&
chmod -v 4755 /usr/sbin/unix_chkpwd
```
Post-install (as root) — create PAM configuration files:
```
install -vdm755 /etc/pam.d &&
cat > /etc/pam.d/system-account << "EOF" &&
# Begin /etc/pam.d/system-account

account   required    pam_unix.so

# End /etc/pam.d/system-account
EOF

cat > /etc/pam.d/system-auth << "EOF" &&
# Begin /etc/pam.d/system-auth

auth      required    pam_unix.so

# End /etc/pam.d/system-auth
EOF

cat > /etc/pam.d/system-session << "EOF" &&
# Begin /etc/pam.d/system-session

session   required    pam_unix.so

# End /etc/pam.d/system-session
EOF

cat > /etc/pam.d/system-password << "EOF"
# Begin /etc/pam.d/system-password

# use yescrypt hash for encryption, use shadow, and try to use any
# previously defined authentication token (chosen password) set by any
# prior module.
password  required    pam_unix.so       yescrypt shadow try_first_pass

# End /etc/pam.d/system-password
EOF
```
Then create restrictive /etc/pam.d/other:
```
cat > /etc/pam.d/other << "EOF"
# Begin /etc/pam.d/other

auth        required        pam_warn.so
auth        required        pam_deny.so
account     required        pam_warn.so
account     required        pam_deny.so
password    required        pam_warn.so
password    required        pam_deny.so
session     required        pam_warn.so
session     required        pam_deny.so

# End /etc/pam.d/other
EOF
```
Systemd: none (but Shadow and Systemd must be reinstalled after PAM)
Patches: none
Notes: IMPORTANT: Shadow and Systemd MUST be reinstalled and reconfigured after installing Linux PAM. Uses meson build system. Optional deps: libnsl, libtirpc.

---

### Fcron-3.4.0
URL: http://fcron.free.fr/archives/fcron-3.4.0.src.tar.gz
MD5: 5732a766df42a090749c0c96a6afd42b
Pre-install (as root):
```
groupadd -g 22 fcron &&
useradd -d /dev/null -c "Fcron User" -g fcron -s /bin/false -u 22 fcron
```
Then fix doc paths:
```
find doc -type f -exec sed -i 's:/usr/local::g' {} \;
```
Configure:
```
./configure --prefix=/usr        \
            --sysconfdir=/etc    \
            --localstatedir=/var \
            --without-sendmail   \
            --with-piddir=/run   \
            --with-boot-install=no
```
Build: make
Install (as root): make install
Post-install (as root) — create run-parts script:
```
cat > /usr/bin/run-parts << "EOF"
#!/bin/sh
# run-parts:  Runs all the scripts found in a directory.
# from Slackware, by Patrick J. Volkerding with ideas borrowed
# from the Red Hat and Debian versions of this utility.

# keep going when something fails
set +e

if [ $# -lt 1 ]; then
  echo "Usage: run-parts <directory>"
  exit 1
fi

if [ ! -d $1 ]; then
  echo "Not a directory: $1"
  echo "Usage: run-parts <directory>"
  exit 1
fi

# There are several types of files that we would like to
# ignore automatically, as they are likely to be backups
# of other scripts:
IGNORE_SUFFIXES="~ ^ , .bak .new .rpmsave .rpmorig .rpmnew .swp"

# Main loop:
for SCRIPT in $1/* ; do
  # If this is not a regular file, skip it:
  if [ ! -f $SCRIPT ]; then
    continue
  fi
  # Determine if this file should be skipped by suffix:
  SKIP=false
  for SUFFIX in $IGNORE_SUFFIXES ; do
    if [ ! "$(basename $SCRIPT $SUFFIX)" = "$(basename $SCRIPT)" ]; then
      SKIP=true
      break
    fi
  done
  if [ "$SKIP" = "true" ]; then
    continue
  fi
  # If we've made it this far, then run the script if it's executable:
  if [ -x $SCRIPT ]; then
    $SCRIPT || echo "$SCRIPT failed."
  fi
done

exit 0
EOF
chmod -v 755 /usr/bin/run-parts
```
Create periodic job directories:
```
install -vdm754 /etc/cron.{hourly,daily,weekly,monthly}
```
Create system fcrontab:
```
cat > /var/spool/fcron/systab.orig << "EOF"
&bootrun 01 * * * * root run-parts /etc/cron.hourly
&bootrun 02 4 * * * root run-parts /etc/cron.daily
&bootrun 22 4 * * 0 root run-parts /etc/cron.weekly
&bootrun 42 4 1 * * root run-parts /etc/cron.monthly
EOF
```
Systemd:
```
systemctl enable fcron
systemctl start fcron &&
fcrontab -z -u systab
```
Patches: none
Notes: No test suite. DESTDIR install must be done as root. Optional: MTA, Linux-PAM, text editor (default vi).

---

### nghttp2-1.68.0
URL: https://github.com/nghttp2/nghttp2/releases/download/v1.68.0/nghttp2-1.68.0.tar.xz
MD5: d5ba082629f15c67e72b4c26f7935500
Pre-install: none
Configure:
```
./configure --prefix=/usr     \
            --disable-static  \
            --enable-lib-only \
            --docdir=/usr/share/doc/nghttp2-1.68.0
```
Build: make
Install: make install
Post-install: none
Systemd: none
Patches: none
Notes: --enable-lib-only builds only libnghttp2 (recommended for most uses). Tests: make check.

---

## PHASE 2 — Certificate Infrastructure

---

### libpsl-0.21.5
URL: https://github.com/rockdaboot/libpsl/releases/download/0.21.5/libpsl-0.21.5.tar.gz
MD5: 870a798ee9860b6e77896548428dba7b
Pre-install: none
Configure:
```
mkdir build &&
cd    build &&

meson setup --prefix=/usr --buildtype=release
```
Build: ninja
Install (as root): ninja install
Post-install: none
Systemd: none
Patches: none
Notes: Recommended deps: libidn2, libunistring. Tests: ninja test. Uses meson build system.

---

### make-ca-1.16.1
URL: https://github.com/lfs-book/make-ca/archive/v1.16.1/make-ca-1.16.1.tar.gz
MD5: bf9cea2d24fc5344d4951b49f275c595
Pre-install: Fix deprecated mktemp option:
```
sed '/mktemp/s/-t //' -i make-ca
```
Configure: none (shell script, not compiled)
Build: none
Install (as root):
```
make install &&
install -vdm755 /etc/ssl/local
```
Post-install (as root) — download and process certificates:
```
/usr/sbin/make-ca -g
```
Enable weekly certificate update timer:
```
systemctl enable update-pki.timer
```
Configure Python to use system certs:
```
mkdir -pv /etc/profile.d &&
cat > /etc/profile.d/pythoncerts.sh << "EOF"
# Begin /etc/profile.d/pythoncerts.sh

export _PIP_STANDALONE_CERT=/etc/pki/tls/certs/ca-bundle.crt

# End /etc/profile.d/pythoncerts.sh
EOF
```
Systemd: systemctl enable update-pki.timer
Patches: none
Notes: Required runtime dep: p11-kit (built after libtasn1). The -g flag is for first run; use -r for subsequent runs. No test suite.

---

### File::FcntlLock-0.22
URL: https://www.cpan.org/authors/id/J/JT/JTT/File-FcntlLock-0.22.tar.gz
MD5: 579698d735d864ee403674f1175f789d
Pre-install: none
Configure & Build:
```
perl Makefile.PL &&
make             &&
make test
```
Install (as root): make install
Post-install: none
Systemd: none
Patches: none
Notes: Standard Perl module build. Required by Exim.

---

## PHASE 3 — Network Clients & Privilege Escalation

---

### Wget-1.25.0
URL: https://ftpmirror.gnu.org/wget/wget-1.25.0.tar.gz
MD5: c70ba58b36f944e8ba1d655ace552881
Pre-install: none
Configure:
```
./configure --prefix=/usr      \
            --sysconfdir=/etc  \
            --with-ssl=openssl
```
Build: make
Install: make install
Post-install: none
Systemd: none
Patches: none
Notes: Recommended dep: libpsl. Runtime dep: make-ca. --with-ssl=openssl uses OpenSSL instead of GnuTLS. Config files: /etc/wgetrc, ~/.wgetrc. Tests: make check.

---

### cURL-8.18.0
URL: https://curl.se/download/curl-8.18.0.tar.xz
MD5: dae6088bf7af69d3b0a87c762de92248
Pre-install: none
Configure:
```
./configure --prefix=/usr    \
            --disable-static \
            --with-openssl   \
            --with-ca-path=/etc/ssl/certs
```
Build: make
Install (as root):
```
make install &&

rm -rf docs/examples/.deps &&

find docs \( -name Makefile\* -o  \
             -name \*.1       -o  \
             -name \*.3       -o  \
             -name CMakeLists.txt \) -delete &&

cp -v -R docs -T /usr/share/doc/curl-8.18.0
```
Post-install: none
Systemd: none
Patches: none
Notes: Recommended dep: libpsl (highly recommended for security). Runtime dep: make-ca. Optional deps: nghttp2, libidn2, libssh2, etc. Tests: make test.

---

### Sudo-1.9.17p2
URL: https://www.sudo.ws/dist/sudo-1.9.17p2.tar.gz
MD5: dcbf46f739ae06b076e1a11cbb271a10
Pre-install: none
Configure:
```
./configure --prefix=/usr         \
            --libexecdir=/usr/lib \
            --with-secure-path    \
            --with-env-editor     \
            --docdir=/usr/share/doc/sudo-1.9.17p2 \
            --with-passprompt="[sudo] password for %p: "
```
Build: make
Install (as root): make install
Post-install (as root) — create sudoers config:
```
cat > /etc/sudoers.d/00-sudo << "EOF"
Defaults secure_path="/usr/sbin:/usr/bin"
%wheel ALL=(ALL) ALL
EOF
```
If PAM installed, create PAM config:
```
cat > /etc/pam.d/sudo << "EOF"
# Begin /etc/pam.d/sudo

# include the default auth settings
auth      include     system-auth

# include the default account settings
account   include     system-account

# Set default environment variables for the service user
session   required    pam_env.so

# include system session defaults
session   include     system-session

# End /etc/pam.d/sudo
EOF
chmod 644 /etc/pam.d/sudo
```
Systemd: none
Patches: none
Notes: Tests: env LC_ALL=C make check |& tee make-check.log. Optional deps: Linux-PAM, MIT Kerberos, OpenLDAP.

---

### Exim-4.99.1
URL: https://ftp.exim.org/pub/exim/exim4/exim-4.99.1.tar.xz
MD5: 281df763c79f1d68cb4f9ee9c9d8a2e1
Pre-install (as root):
```
groupadd -g 31 exim &&
useradd -d /dev/null -c "Exim Daemon" -g exim -s /bin/false -u 31 exim
```
Configure (creates Local/Makefile from src/EDITME):
```
sed -e 's,^BIN_DIR.*$,BIN_DIRECTORY=/usr/sbin,'    \
    -e 's,^CONF.*$,CONFIGURE_FILE=/etc/exim.conf,' \
    -e 's,^EXIM_USER.*$,EXIM_USER=exim,'           \
    -e '/# USE_OPENSSL/s,^#,,' src/EDITME > Local/Makefile &&

printf "USE_GDBM = yes\nDBMLIB = -lgdbm\n" >> Local/Makefile
```
Optional — add PAM support:
```
sed -i '/# SUPPORT_PAM=yes/s,^#,,' Local/Makefile
echo "EXTRALIBS=-lpam" >> Local/Makefile
```
Build: make
Install (as root):
```
make install                                    &&
install -v -m644 doc/exim.8 /usr/share/man/man8 &&

install -vdm 755    /usr/share/doc/exim-4.99.1 &&
cp      -Rv doc/*   /usr/share/doc/exim-4.99.1 &&

ln -sfv exim /usr/sbin/sendmail                 &&
install -v -d -m750 -o exim -g exim /var/spool/exim
```
Post-install (as root):
```
chmod -v a+wt /var/mail
```
Create aliases:
```
cat >> /etc/aliases << "EOF"
postmaster: root
MAILER-DAEMON: root
EOF
```
If PAM installed:
```
cat > /etc/pam.d/exim << "EOF"
# Begin /etc/pam.d/exim

auth    include system-auth
account include system-account
session include system-session

# End /etc/pam.d/exim
EOF
```
Systemd: make install-exim (from blfs-systemd-units package)
Patches: none (configuration is via sed on src/EDITME)
Notes: Required deps: libnsl, File::FcntlLock. No configure script — uses edited Makefile. No test suite. Creates sendmail symlink.

---

## PHASE 4 — Build Tools & Version Control

---

### CMake-4.2.3
URL: https://cmake.org/files/v4.2/cmake-4.2.3.tar.gz
MD5: 803a1720ec822a8660118a38ca51fc1b
Pre-install: none
Configure:
```
sed -i '/"lib64"/s/64//' Modules/GNUInstallDirs.cmake &&

./bootstrap --prefix=/usr        \
            --system-libs        \
            --mandir=/share/man  \
            --no-system-jsoncpp  \
            --no-system-cppdap   \
            --no-system-librhash \
            --docdir=/share/doc/cmake-4.2.3
```
Build: make
Install (as root): make install
Post-install: none
Systemd: none
Patches: sed to fix lib64 path (inline)
Notes: Recommended deps: cURL, libarchive, libuv, nghttp2. Tests: bin/ctest -j$(nproc). Uses its own bootstrap script, not autotools.

---

### Git-2.53.0
URL: https://www.kernel.org/pub/software/scm/git/git-2.53.0.tar.xz
MD5: 3857733169a6443e48d20c75ee32f732
Man pages: https://www.kernel.org/pub/software/scm/git/git-manpages-2.53.0.tar.xz
HTML docs: https://www.kernel.org/pub/software/scm/git/git-htmldocs-2.53.0.tar.xz
Pre-install: none
Configure:
```
./configure --prefix=/usr                   \
            --with-gitconfig=/etc/gitconfig \
            --with-python=python3           \
            --with-libpcre2
```
Build: make
Install (as root):
```
make perllibdir=/usr/lib/perl5/5.42/site_perl install
```
Install pre-built man pages (as root):
```
tar -xf ../git-manpages-2.53.0.tar.xz \
    -C /usr/share/man --no-same-owner --no-overwrite-dir
```
Post-install: Config files: ~/.gitconfig and /etc/gitconfig
Systemd: none
Patches: none
Notes: Recommended dep: cURL (for http/https/ftp). Tests: GIT_UNZIP=nonexist make test -k. Optional deps: OpenSSH, GnuPG, Fcron.

---

### at-3.2.5
URL: https://anduin.linuxfromscratch.org/BLFS/at/at_3.2.5.orig.tar.gz
MD5: ca3657a1c90d7c3d252e0bc17feddc6e
Pre-install (as root):
```
groupadd -g 17 atd                                                  &&
useradd -d /dev/null -c "atd daemon" -g atd -s /bin/false -u 17 atd
```
Configure:
```
./configure --with-daemon_username=atd        \
            --with-daemon_groupname=atd       \
            SENDMAIL=/usr/sbin/sendmail       \
            --with-jobdir=/var/spool/atjobs   \
            --with-atspool=/var/spool/atspool \
            --with-systemdsystemunitdir=/lib/systemd/system
```
Build: make -j1
Install (as root):
```
make install docdir=/usr/share/doc/at-3.2.5 \
             atdocdir=/usr/share/doc/at-3.2.5
```
Post-install: If PAM installed (as root):
```
cat > /etc/pam.d/atd << "EOF"
# Begin /etc/pam.d/atd

auth     required pam_unix.so
account  required pam_unix.so
password required pam_unix.so
session  required pam_unix.so

# End /etc/pam.d/atd
EOF
```
Systemd: systemctl enable atd
Patches: none
Notes: Required dep: MTA (e.g. Exim). Must build with make -j1. Tests: make test. Optional: Linux-PAM.

---

## PHASE 5 — System Utilities

---

### rsync-3.4.1
URL: https://www.samba.org/ftp/rsync/src/rsync-3.4.1.tar.gz
MD5: 04ce67866db04fd7a1cde0b78168406e
Patch: https://www.linuxfromscratch.org/patches/blfs/13.0/rsync-3.4.1-security_fix-1.patch
Pre-install (as root, if running as daemon):
```
groupadd -g 48 rsyncd &&
useradd -c "rsyncd Daemon" -m -d /home/rsync -g rsyncd \
    -s /bin/false -u 48 rsyncd
```
Apply security patch:
```
patch -Np1 -i ../rsync-3.4.1-security_fix-1.patch
```
Configure:
```
./configure --prefix=/usr    \
            --disable-xxhash \
            --without-included-zlib
```
Build: make
Install (as root): make install
Post-install: If running as daemon, create /etc/rsyncd.conf:
```
cat > /etc/rsyncd.conf << "EOF"
# This is a basic rsync configuration file
# It exports a single module without user authentication.

motd file = /home/rsync/welcome.msg
use chroot = yes

[localhost]
    path = /home/rsync
    comment = Default rsync module
    read only = yes
    list = yes
    uid = rsyncd
    gid = rsyncd

EOF
```
Systemd: make install-rsyncd (from blfs-systemd-units package)
Patches: rsync-3.4.1-security_fix-1.patch (required)
Notes: Recommended dep: popt. Tests require sed fix first: sed -i '/typedef/d' wildtest.c && make check. --disable-xxhash unless xxhash installed.

---

### Screen-5.0.1
URL: https://ftpmirror.gnu.org/screen/screen-5.0.1.tar.gz
MD5: fb5e5dfc9353225c2d6929777344b1a6
Pre-install: Fix info page build issue:
```
sed 's/\([a-z]\)@opensuse/\1@@opensuse/' -i doc/screen.texinfo
```
Configure:
```
./configure --prefix=/usr                   \
            --infodir=/usr/share/info       \
            --mandir=/usr/share/man         \
            --disable-pam                   \
            --enable-socket-dir=/run/screen \
            --with-pty-group=5              \
            --with-system_screenrc=/etc/screenrc &&

sed -i -e "s%/usr/local/etc/screenrc%/etc/screenrc%" {etc,doc}/*
```
Build: make
Install (as root):
```
make install &&
install -m 644 etc/etcscreenrc /etc/screenrc
```
Post-install: none
Systemd: none
Patches: sed fix for info page (inline)
Notes: --disable-pam removes PAM dependency (remove if you want PAM support). --with-pty-group=5 matches LFS. No test suite.

---

### lsof-4.99.5
URL: https://github.com/lsof-org/lsof/releases/download/4.99.5/lsof-4.99.5.tar.gz
MD5: 00f5844ae3520b5699c249dd424500c2
Pre-install: none
Configure: ./configure --prefix=/usr --disable-static
Build: make
Install (as root): make install
Post-install: none
Systemd: none
Patches: none
Notes: Required dep: libtirpc. Tests (as root): make check. Kernel needs POSIX_MQUEUE enabled for tests.
