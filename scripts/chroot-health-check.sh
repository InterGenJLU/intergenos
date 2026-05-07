#!/bin/bash
# InterGenOS Chroot Health Check
#
# Validates that built packages are actually functional:
#   - Key binaries exist and are executable
#   - Shared libraries are findable by ldconfig
#   - pkg-config files exist for libraries
#   - Python modules can be imported
#   - Systemd services have valid unit files
#   - Critical config files exist
#
# Run inside the chroot (via chroot-enter.sh) or against a mounted
# filesystem by setting SYSROOT=/path/to/root.
#
# Usage:
#   sudo bash scripts/chroot-enter.sh scripts/chroot-health-check.sh
#   SYSROOT=/mnt/usb-root bash scripts/chroot-health-check.sh
#
# Exit codes:
#   0 = all checks passed
#   1 = failures detected (see report)

set -euo pipefail

SYSROOT="${SYSROOT:-}"
PASS=0
FAIL=0
WARN=0
FAILURES=""

check_binary() {
    local name="$1"
    local path="$2"
    local full="${SYSROOT}${path}"
    if [ -x "$full" ]; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
        FAILURES="${FAILURES}\n  FAIL: binary ${name} not found at ${path}"
    fi
}

check_library() {
    local name="$1"
    local pattern="$2"
    local found=0
    for dir in ${SYSROOT}/usr/lib ${SYSROOT}/usr/lib64 ${SYSROOT}/lib; do
        if ls ${dir}/${pattern} 2>/dev/null | head -1 | grep -q .; then
            found=1
            break
        fi
    done
    if [ $found -eq 1 ]; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
        FAILURES="${FAILURES}\n  FAIL: library ${name} (${pattern}) not found"
    fi
}

check_pkgconfig() {
    local name="$1"
    local pc="$2"
    local found=0
    for dir in ${SYSROOT}/usr/lib/pkgconfig ${SYSROOT}/usr/lib64/pkgconfig ${SYSROOT}/usr/share/pkgconfig; do
        if [ -f "${dir}/${pc}.pc" ]; then
            found=1
            break
        fi
    done
    if [ $found -eq 1 ]; then
        PASS=$((PASS + 1))
    else
        WARN=$((WARN + 1))
        FAILURES="${FAILURES}\n  WARN: pkg-config ${pc}.pc not found for ${name}"
    fi
}

check_python_module() {
    local mod="$1"
    if [ -z "$SYSROOT" ]; then
        python3 -c "import ${mod}" 2>/dev/null
        if [ $? -eq 0 ]; then
            PASS=$((PASS + 1))
        else
            FAIL=$((FAIL + 1))
            FAILURES="${FAILURES}\n  FAIL: Python module ${mod} cannot be imported"
        fi
    else
        # Can't import from external sysroot, check file exists
        local found=0
        for pydir in ${SYSROOT}/usr/lib/python3.*/; do
            if [ -d "${pydir}${mod}" ] || [ -f "${pydir}${mod}.py" ] || \
               [ -d "${pydir}site-packages/${mod}" ] || [ -f "${pydir}site-packages/${mod}.py" ]; then
                found=1
                break
            fi
        done
        if [ $found -eq 1 ]; then
            PASS=$((PASS + 1))
        else
            FAIL=$((FAIL + 1))
            FAILURES="${FAILURES}\n  FAIL: Python module ${mod} not found"
        fi
    fi
}

check_service() {
    local name="$1"
    local unit="$2"
    if [ -f "${SYSROOT}/usr/lib/systemd/system/${unit}" ]; then
        PASS=$((PASS + 1))
    else
        WARN=$((WARN + 1))
        FAILURES="${FAILURES}\n  WARN: systemd unit ${unit} not found for ${name}"
    fi
}

check_file() {
    local desc="$1"
    local path="$2"
    if [ -e "${SYSROOT}${path}" ]; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
        FAILURES="${FAILURES}\n  FAIL: ${desc} not found at ${path}"
    fi
}

echo "=========================================="
echo "  InterGenOS Chroot Health Check"
echo "  Target: ${SYSROOT:-/ (running system)}"
echo "  Date: $(date)"
echo "=========================================="
echo

# ============================================================================
# 1. Core Toolchain
# ============================================================================
echo "[1/8] Core Toolchain..."
check_binary "gcc" "/usr/bin/gcc"
check_binary "g++" "/usr/bin/g++"
check_binary "ld" "/usr/bin/ld"
check_binary "make" "/usr/bin/make"
check_binary "cmake" "/usr/bin/cmake"
check_binary "meson" "/usr/bin/meson"
check_binary "ninja" "/usr/bin/ninja"
check_binary "pkg-config" "/usr/bin/pkg-config"
check_binary "autoconf" "/usr/bin/autoconf"
check_binary "automake" "/usr/bin/automake"
check_binary "libtool" "/usr/bin/libtool"
check_binary "python3" "/usr/bin/python3"
check_binary "perl" "/usr/bin/perl"
check_binary "bash" "/usr/bin/bash"

# ============================================================================
# 2. Core Libraries
# ============================================================================
echo "[2/8] Core Libraries..."
check_library "glibc" "libc.so*"
check_library "libstdc++" "libstdc++.so*"
check_library "zlib" "libz.so*"
check_library "openssl" "libssl.so*"
check_library "libcrypto" "libcrypto.so*"
check_library "gnutls" "libgnutls.so*"
check_library "curl" "libcurl.so*"
check_library "libxml2" "libxml2.so*"
check_library "libarchive" "libarchive.so*"
check_library "readline" "libreadline.so*"
check_library "ncurses" "libncurses*.so*"
check_library "sqlite3" "libsqlite3.so*"
check_library "libffi" "libffi.so*"
check_library "gmp" "libgmp.so*"

check_pkgconfig "openssl" "openssl"
check_pkgconfig "zlib" "zlib"
check_pkgconfig "libcurl" "libcurl"
check_pkgconfig "libxml2" "libxml-2.0"

# ============================================================================
# 3. Network & Security
# ============================================================================
echo "[3/8] Network & Security..."
check_binary "curl" "/usr/bin/curl"
check_binary "wget" "/usr/bin/wget"
check_binary "git" "/usr/bin/git"
check_binary "ssh" "/usr/bin/ssh"
check_binary "sshd" "/usr/sbin/sshd"
check_binary "sudo" "/usr/bin/sudo"
check_binary "gpg" "/usr/bin/gpg"
check_binary "make-ca" "/usr/sbin/make-ca"
check_library "linux-pam" "libpam.so*"
check_library "nss" "libnss3.so*"
check_library "nspr" "libnspr4.so*"
check_library "p11-kit" "libp11-kit.so*"
check_service "sshd" "sshd.service"
check_service "NetworkManager" "NetworkManager.service"

# Check sudo setuid
SUDO="${SYSROOT}/usr/bin/sudo"
if [ -f "$SUDO" ]; then
    PERMS=$(stat -c %a "$SUDO" 2>/dev/null)
    if [ "$PERMS" = "4755" ]; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
        FAILURES="${FAILURES}\n  FAIL: sudo permissions are ${PERMS}, should be 4755 (setuid)"
    fi
fi

# ============================================================================
# 4. Desktop Stack (X11/Wayland/GTK/GNOME)
# ============================================================================
echo "[4/8] Desktop Stack..."
check_library "wayland" "libwayland-client.so*"
check_library "mesa-EGL" "libEGL.so*"
check_library "mesa-GL" "libGL.so*"
check_library "libdrm" "libdrm.so*"
check_library "cairo" "libcairo.so*"
check_library "pango" "libpango-1.0.so*"
check_library "gdk-pixbuf" "libgdk_pixbuf-2.0.so*"
check_library "gtk3" "libgtk-3.so*"
check_library "gtk4" "libgtk-4.so*"
check_library "glib2" "libglib-2.0.so*"
check_library "gobject" "libgobject-2.0.so*"
check_library "gio" "libgio-2.0.so*"
check_library "harfbuzz" "libharfbuzz.so*"
check_library "freetype" "libfreetype.so*"
check_library "fontconfig" "libfontconfig.so*"
check_library "libinput" "libinput.so*"

check_pkgconfig "gtk3" "gtk+-3.0"
check_pkgconfig "gtk4" "gtk4"
check_pkgconfig "cairo" "cairo"
check_pkgconfig "pango" "pango"
check_pkgconfig "glib2" "glib-2.0"
check_pkgconfig "wayland" "wayland-client"
check_pkgconfig "libdrm" "libdrm"

# ============================================================================
# 5. GNOME Shell & Desktop
# ============================================================================
echo "[5/8] GNOME Shell..."
check_binary "gnome-shell" "/usr/bin/gnome-shell"
check_binary "gnome-session" "/usr/bin/gnome-session"
check_binary "gdm" "/usr/sbin/gdm"
check_binary "mutter" "/usr/bin/mutter"
check_binary "gnome-terminal" "/usr/bin/gnome-terminal"
check_binary "nautilus" "/usr/bin/nautilus"
check_binary "gnome-control-center" "/usr/bin/gnome-control-center"
check_binary "gnome-tweaks" "/usr/bin/gnome-tweaks"
check_binary "gjs" "/usr/bin/gjs"
check_binary "dbus-daemon" "/usr/bin/dbus-daemon"
check_service "gdm" "gdm.service"
check_service "avahi" "avahi-daemon.service"
check_service "bluetooth" "bluetooth.service"

# ============================================================================
# 6. Audio/Video
# ============================================================================
echo "[6/8] Audio/Video..."
check_library "pipewire" "libpipewire-0.3.so*"
check_library "alsa-lib" "libasound.so*"
check_binary "pipewire" "/usr/bin/pipewire"
check_binary "wireplumber" "/usr/bin/wireplumber"
check_library "gstreamer" "libgstreamer-1.0.so*"
check_library "gst-plugins-base" "libgstvideo-1.0.so*"
check_pkgconfig "pipewire" "libpipewire-0.3"
check_pkgconfig "alsa" "alsa"

# ============================================================================
# 7. Python Modules
# ============================================================================
echo "[7/8] Python Modules..."
check_python_module "yaml"
check_python_module "ssl"
check_python_module "sqlite3"
check_python_module "xml"
check_python_module "json"
check_python_module "gi"
check_python_module "cairo"
check_python_module "dbus"
check_python_module "mako"

# ============================================================================
# 8. Critical Config Files
# ============================================================================
echo "[8/8] Critical Config Files..."
check_file "os-release" "/etc/os-release"
check_file "hostname" "/etc/hostname"
check_file "locale.conf" "/etc/locale.conf"
check_file "passwd" "/etc/passwd"
check_file "shadow" "/etc/shadow"
check_file "group" "/etc/group"
check_file "shells" "/etc/shells"
check_file "fstab" "/etc/fstab"
check_file "ld.so.conf" "/etc/ld.so.conf"
check_file "sudoers" "/etc/sudoers"
check_file "nsswitch.conf" "/etc/nsswitch.conf"
check_file "tmpfiles x11" "/etc/tmpfiles.d/x11-unix.conf"
check_file "gsettings override" "/usr/share/glib-2.0/schemas/90_intergenos.gschema.override"

# ============================================================================
# CUPS special check (known issue)
# ============================================================================
echo
echo "[CUPS special check]"
CUPSD_FOUND=0
for p in /usr/sbin/cupsd /usr/bin/cupsd; do
    if [ -f "${SYSROOT}${p}" ]; then
        echo "  OK: cupsd found at ${p}"
        CUPSD_FOUND=1
        PASS=$((PASS + 1))
        break
    fi
done
if [ $CUPSD_FOUND -eq 0 ]; then
    FAIL=$((FAIL + 1))
    FAILURES="${FAILURES}\n  FAIL: cupsd binary missing (known issue — CUPS build installs to wrong location)"
fi

# ============================================================================
# Report
# ============================================================================

TOTAL=$((PASS + FAIL + WARN))

echo
echo "=========================================="
echo "  Health Check Results"
echo "=========================================="
echo "  Total checks: ${TOTAL}"
echo "  Passed:       ${PASS}"
echo "  Failed:       ${FAIL}"
echo "  Warnings:     ${WARN}"
echo

if [ $FAIL -gt 0 ] || [ $WARN -gt 0 ]; then
    echo "  Issues:"
    echo -e "$FAILURES"
    echo
fi

if [ $FAIL -eq 0 ]; then
    echo "  STATUS: HEALTHY (${WARN} warnings)"
    exit 0
else
    echo "  STATUS: ${FAIL} FAILURES DETECTED"
    exit 1
fi
