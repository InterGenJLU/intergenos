# InterGenOS — Third-Party Notices

This file aggregates the upstream attribution for every package
InterGenOS builds and ships. It is generated mechanically from the
per-package `package.yml` inventory by
`scripts/generate-third-party-notices.py`; do not edit by hand —
re-run the generator after package additions or license-field
changes.

Each entry lists the package name, the SPDX license expression
declared by the package's `package.yml`, a one-line description,
and the upstream homepage where applicable. For helper packages
that fetch a proprietary payload at install time, the
`payload_license:` field is shown separately — that is the EULA the
user accepts when running the helper, distinct from the GPL-3
license of the helper script itself.

The full upstream LICENSE text of each shipped package is bundled
under `/usr/share/licenses/<package>/` on installed systems, per
the source-availability commitment in [SOURCES.md](SOURCES.md) and
the license policy at
[docs/governance/license-policy.md](docs/governance/license-policy.md).

The aggregate ships on the installed system at
`/usr/share/doc/intergenos/THIRD-PARTY-NOTICES` for offline access.

---

## Coverage summary


**Total packages:** 1153

**Distribution by tier:**

| Tier | Count |
|---|---:|
| toolchain | 28 |
| core | 315 |
| base | 33 |
| desktop | 468 |
| extra | 199 |
| ai | 58 |
| compute | 52 |

**Distribution by license (top 20):**

| License | Count |
|---|---:|
| `MIT` | 275 |
| `LGPL-2.1-or-later` | 152 |
| `GPL-3.0-or-later` | 151 |
| `GPL-2.0-or-later` | 141 |
| `BSD-3-Clause` | 96 |
| `Apache-2.0` | 71 |
| `LGPL-2.0-or-later` | 34 |
| `BSD-2-Clause` | 32 |
| `LGPL-3.0-or-later` | 19 |
| `MPL-2.0` | 18 |
| `ISC` | 18 |
| `GPL-2.0-only` | 12 |
| `Zlib` | 8 |
| `LicenseRef-Public-Domain` | 7 |
| `MIT OR Apache-2.0` | 7 |
| `LGPL-2.1-or-later AND GPL-2.0-or-later` | 4 |
| `BSL-1.0` | 4 |
| `OFL-1.1` | 4 |
| `ZPL-2.0 AND LGPL-2.1-or-later AND BSD-3-Clause` | 4 |
| `NCSA` | 4 |

**Helpers with proprietary payloads:**

| Helper | Helper license | Payload license (user-accepted) |
|---|---|---|
| intergenos-grub-theme | `GPL-3.0-or-later` | `GPL-3.0-or-later` |
| intergenos-wallpapers | `GPL-3.0-or-later` | `GPL-3.0-or-later` |
| brave | `GPL-3.0-or-later` | `LicenseRef-Brave-EULA` |
| chrome | `GPL-3.0-or-later` | `LicenseRef-Google-Chrome-ToS` |
| claude-code | `GPL-3.0-or-later` | `LicenseRef-Anthropic-Commercial-Terms` |
| discord | `GPL-3.0-or-later` | `LicenseRef-Discord-ToS` |
| edge | `GPL-3.0-or-later` | `LicenseRef-Microsoft-Edge-EULA` |
| ffmpeg-nonfree | `GPL-3.0-or-later` | `GPL-3.0-or-later AND FDK-AAC` |
| ge-proton | `GPL-3.0-or-later` | `LicenseRef-GE-Proton-Mixed` |
| signal | `GPL-3.0-or-later` | `AGPL-3.0-only` |
| spotify | `GPL-3.0-or-later` | `LicenseRef-Spotify-ToS` |
| vscode | `GPL-3.0-or-later` | `LicenseRef-Microsoft-VSCode-Software-License` |
| zoom | `GPL-3.0-or-later` | `LicenseRef-Zoom-Terms-of-Service` |
| cuda-toolkit | `GPL-3.0-or-later` | `LicenseRef-NVIDIA-CUDA-EULA` |

See [`docs/legal/payload-licenses.md`](docs/legal/payload-licenses.md) for each payload license's canonical URL + last-checked date.

---

## Package entries by tier

### Tier: `toolchain`

### bash-tmp (5.3)

GNU Bourne Again Shell (temporary tools)

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/bash/

### binutils-pass1 (2.46.0)

GNU Binutils — cross-linker and assembler (pass 1)

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/binutils/

### binutils-pass2 (2.46.0)

GNU Binutils (pass 2 — cross-compiled for target)

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/binutils/

### bison-tmp (3.8.2)

GNU parser generator (temporary tools)

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/bison/

### coreutils-tmp (9.10)

GNU core utilities (temporary tools)

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/coreutils/

### diffutils-tmp (3.12)

GNU diff utilities (temporary tools)

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/diffutils/

### file-tmp (5.46)

File type identification utility (temporary tools)

- License: `BSD-2-Clause`
- Homepage: https://www.darwinsys.com/file/

### findutils-tmp (4.10.0)

GNU find utilities (temporary tools)

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/findutils/

### gawk-tmp (5.3.2)

GNU awk (temporary tools)

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/gawk/

### gcc-pass1 (15.2.0)

GCC cross-compiler (pass 1 — C only)

- License: `GPL-3.0-or-later`
- Homepage: https://gcc.gnu.org/

### gcc-pass2 (15.2.0)

GCC compiler (pass 2 — cross-compiled for target, C and C++)

- License: `GPL-3.0-or-later`
- Homepage: https://gcc.gnu.org/

### gettext-tmp (1.0)

GNU internationalization utilities (temporary tools)

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/gettext/

### glibc (2.43)

GNU C Library

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gnu.org/software/libc/

### grep-tmp (3.12)

GNU grep (temporary tools)

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/grep/

### gzip-tmp (1.14)

GNU gzip compression (temporary tools)

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/gzip/

### libstdcpp (15.2.0)

Standard C++ library (from GCC source — deferred from pass 1)

- License: `GPL-3.0-or-later`
- Homepage: https://gcc.gnu.org/

### linux-headers (6.18.10)

Linux kernel API headers

- License: `GPL-2.0-only`
- Homepage: https://www.kernel.org/

### m4 (1.4.21)

GNU macro processor

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/m4/

### make-tmp (4.4.1)

GNU make (temporary tools)

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/make/

### ncurses (6.6)

Terminal-independent handling of character screens

- License: `MIT`
- Homepage: https://invisible-island.net/ncurses/

### patch-tmp (2.8)

GNU patch (temporary tools)

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/patch/

### perl-tmp (5.42.0)

Practical Extraction and Report Language (temporary tools)

- License: `Artistic-1.0-Perl OR GPL-1.0-or-later`
- Homepage: https://www.perl.org/

### python-tmp (3.14.3)

Python programming language (temporary tools)

- License: `PSF-2.0`
- Homepage: https://www.python.org/

### sed-tmp (4.9)

GNU sed stream editor (temporary tools)

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/sed/

### tar-tmp (1.35)

GNU tar archiver (temporary tools)

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/tar/

### texinfo-tmp (7.2)

GNU documentation system (temporary tools)

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/texinfo/

### util-linux-tmp (2.41.3)

Miscellaneous system utilities (temporary tools)

- License: `GPL-2.0-or-later`
- Homepage: https://www.kernel.org/pub/linux/utils/util-linux/

### xz-tmp (5.8.2)

XZ Utils compression (temporary tools)

- License: `GPL-2.0-or-later`
- Homepage: https://tukaani.org/xz/


### Tier: `core`

### abseil-cpp (20260107.1)

Abseil C++ common libraries

- License: `Apache-2.0`

### acl (2.3.2)

Access Control Lists utilities

- License: `LGPL-2.1-or-later`

### aiohappyeyeballs (2.6.2)

Happy Eyeballs connection helper for asyncio

- License: `MIT`
- Homepage: https://github.com/aio-libs/aiohappyeyeballs

### aiohttp (3.13.5)

Async HTTP client/server framework (asyncio)

- License: `Apache-2.0`
- Homepage: https://github.com/aio-libs/aiohttp

### aiosignal (1.4.0)

List of registered asynchronous callbacks

- License: `MIT`
- Homepage: https://github.com/aio-libs/aiosignal

### apparmor (3.1.7)

AppArmor MAC framework — libapparmor, parser, and profile set

- License: `GPL-2.0-only`
- Homepage: https://gitlab.com/apparmor/apparmor

### attr (2.5.2)

Extended attributes utilities

- License: `LGPL-2.1-or-later`

### attrs (26.1.0)

Classes without boilerplate (runtime dependency of aiohttp)

- License: `MIT`
- Homepage: https://github.com/python-attrs/attrs

### autoconf (2.72)

GNU autoconf

- License: `GPL-3.0-or-later`

### automake (1.18.1)

GNU automake

- License: `GPL-2.0-or-later`

### bash (5.3)

The GNU Bourne Again Shell

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/bash/

### bash-completion (2.17.0)

Programmable tab-completion for Bash

- License: `GPL-2.0-or-later`
- Homepage: https://github.com/scop/bash-completion

### bc (7.0.3)

Arbitrary precision calculator

- License: `BSD-2-Clause`

### beautifulsoup4 (4.15.0)

HTML/XML parsing library for Python (the BLFS book parser two validate gates read from)

- License: `MIT`
- Homepage: https://www.crummy.com/software/BeautifulSoup/bs4/

### binutils-core (2.46.0)

GNU Binutils (final system)

- License: `GPL-3.0-or-later`

### bison-core (3.8.2)

GNU parser generator

- License: `GPL-3.0-or-later`

### brotli (1.2.0)

Brotli compression library

- License: `MIT`
- Homepage: https://github.com/google/brotli

### btrfs-progs (6.19.1)

Userspace utilities and headers for the Btrfs filesystem

- License: `GPL-2.0-only`
- Homepage: https://btrfs.readthedocs.io/

### busybox-static (1.37.0)

Statically-linked busybox userland for early-boot environments (initramfs)

- License: `GPL-2.0-only`
- Homepage: https://busybox.net/

### bzip2 (1.0.8)

Block-sorting file compressor

- License: `bzip2-1.0.6`

### c-ares (1.34.6)

Asynchronous DNS resolver library

- License: `MIT`

### ca-certificates (2026.04.30)

Mozilla CA root certificate bundle (PEM) for OS-level TLS verification

- License: `MPL-2.0`
- Homepage: https://curl.se/docs/caextract.html

### cargo-c (0.10.20)

Cargo C-ABI helpers for building and installing C-compatible libraries

- License: `MIT`
- Homepage: https://github.com/lu-zero/cargo-c

### cbindgen (0.29.2)

C bindings generator for Rust

- License: `MPL-2.0`

### ccid (1.8.2)

PC/SC CCID/ICCD USB smart card reader driver for pcscd

- License: `LGPL-2.1-or-later`
- Homepage: https://ccid.apdu.fr/

### cffi (1.17.1)

Foreign Function Interface for Python calling C code

- License: `MIT`
- Homepage: https://cffi.readthedocs.io/

### cmake (4.3.1)

Cross-platform build system generator

- License: `BSD-3-Clause`
- Homepage: https://cmake.org/

### coreutils-core (9.10)

GNU core utilities

- License: `GPL-3.0-or-later`

### cpio (2.15)

GNU cpio - copies files into or out of archives

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/cpio/

### cracklib (2.10.3)

Password checking library

- License: `LGPL-2.1-or-later`

### cryptsetup (2.8.4)

Transparent disk encryption using the kernel crypto API

- License: `GPL-2.0-or-later`
- Homepage: https://gitlab.com/cryptsetup/cryptsetup

### cryptsetup-static (2.8.4)

Statically-linked cryptsetup + veritysetup for initramfs (LUKS unlock + dm-verity open)

- License: `GPL-2.0-or-later`
- Homepage: https://gitlab.com/cryptsetup/cryptsetup

### curl (8.19.0)

Command line tool and library for transferring data with URLs

- License: `MIT`
- Homepage: https://curl.se/

### cyrus-sasl (2.1.28)

Cyrus Simple Authentication and Security Layer

- License: `BSD-4-Clause-UC`
- Homepage: https://www.cyrusimap.org/sasl/

### cython (3.2.4)

C extensions for Python

- License: `Apache-2.0`

### dbus (1.16.2)

Message bus system

- License: `AFL-2.1 OR GPL-2.0-or-later`

### dejagnu (1.6.3)

Framework for testing programs

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/dejagnu/

### diffutils-core (3.12)

GNU diff utilities

- License: `GPL-3.0-or-later`

### docbook-xml (4.5)

DocBook XML DTD

- License: `DocBook-DTD`

### docbook-xsl-nons (1.79.2)

DocBook XSL stylesheets

- License: `MIT`

### docutils (0.22.4)

Python documentation utilities

- License: `LicenseRef-Public-Domain`

### dosfstools (4.2)

Utilities for FAT filesystems (mkfs.fat, fsck.fat)

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/dosfstools/dosfstools

### duktape (2.7.0)

Embeddable JavaScript engine

- License: `MIT`

### e2fsprogs (1.47.3)

Ext2/3/4 filesystem utilities

- License: `GPL-2.0-or-later`

### editables (0.5)

Python editable installs helper

- License: `MIT`

### efibootmgr (18)

Tool for managing UEFI boot entries

- License: `GPL-2.0-or-later`
- Homepage: https://github.com/rhboot/efibootmgr

### efitools (1.9.2)

Tools for manipulating UEFI Secure Boot variables and keys

- License: `GPL-2.0-only`
- Homepage: https://git.kernel.org/pub/scm/linux/kernel/git/jejb/efitools.git

### efivar (39)

Library and tools for EFI variable management

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/rhboot/efivar

### elfutils (0.194)

ELF object file utilities

- License: `GPL-2.0-or-later`

### exfatprogs (1.4.2)

exFAT filesystem userspace utilities (mkfs.exfat, fsck.exfat)

- License: `GPL-2.0-or-later`
- Homepage: https://github.com/exfatprogs/exfatprogs

### expandvars (1.1.2)

Expand system variables Unix style (build dependency)

- License: `MIT`
- Homepage: https://github.com/sayanarijit/expandvars

### expat (2.7.4)

XML parser library

- License: `MIT`

### expect (5.45.4)

Tool for automating interactive applications

- License: `LicenseRef-Public-Domain`
- Homepage: https://core.tcl.tk/expect/

### fido2-tools-static (1.17.0)

Statically-linked libfido2 tools subset for early-boot LUKS FIDO2 unlock (initramfs, EXPERIMENTAL)

- License: `BSD-2-Clause`
- Homepage: https://github.com/Yubico/libfido2

### file (5.46)

File type identification utility

- License: `BSD-2-Clause`

### findutils-core (4.10.0)

GNU find utilities

- License: `GPL-3.0-or-later`

### flex (2.6.4)

Fast lexical analyzer generator

- License: `BSD-2-Clause`

### flit-core (3.12.0)

Python build backend (minimal)

- License: `MIT`

### freetype-grub (2.14.1)

FreeType (minimal — no HarfBuzz/PNG/Brotli) built in Chapter 8 before GRUB so GRUB's configure detects FreeType, enables build-time grub-mkfont, and compiles unicode.pf2. The full-featured freetype2 is rebuilt later in the desktop tier and replaces this copy.

- License: `FTL`
- Homepage: https://www.freetype.org/

### frozenlist (1.8.0)

List-like structure that can be made immutable (hashable)

- License: `Apache-2.0`
- Homepage: https://github.com/aio-libs/frozenlist

### fuse3 (3.18.1)

Filesystem in Userspace

- License: `GPL-2.0-or-later`

### gawk-core (5.3.2)

GNU awk

- License: `GPL-3.0-or-later`

### gcc-core (15.2.0)

GNU Compiler Collection (final system)

- License: `GPL-3.0-or-later`

### gdbm (1.26)

GNU database manager

- License: `GPL-3.0-or-later`

### gettext (1.0)

GNU internationalization utilities

- License: `GPL-3.0-or-later`

### git (2.53.0)

Distributed version control system

- License: `GPL-2.0-only`
- Homepage: https://git-scm.com/

### glib2 (2.88.1)

GLib core library (full — with introspection)

- License: `LGPL-2.1-or-later`
- Homepage: https://docs.gtk.org/glib/

### glib2-bootstrap (2.88.1)

GLib core library (bootstrap — without introspection)

- License: `LGPL-2.1-or-later`
- Homepage: https://docs.gtk.org/glib/

### glibc-core (2.43)

GNU C Library (final system)

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gnu.org/software/libc/

### gmp (6.3.0)

GNU Multiple Precision Arithmetic Library

- License: `LGPL-3.0-or-later`

### gnu-efi (3.0.18)

GNU EFI development library — UEFI headers and libraries for building EFI applications (required by efitools, sbsigntool, and shim)

- License: `BSD-3-Clause`
- Homepage: https://sourceforge.net/projects/gnu-efi/

### gnupg2 (2.5.17)

GNU Privacy Guard

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnupg.org/

### gnutls (3.8.12)

GNU TLS library

- License: `LGPL-2.1-or-later`
- Homepage: https://gnutls.org/

### go (1.26.4)

The Go programming language compiler and toolchain

- License: `BSD-3-Clause`
- Homepage: https://go.dev/

### gobject-introspection-pass1 (1.86.0)

GObject type introspection framework (bootstrap — no cairo, no doctool)

- License: `LGPL-2.0-or-later`
- Homepage: https://gi.readthedocs.io/

### gperf (3.3)

Perfect hash function generator

- License: `GPL-3.0-or-later`

### gpgme (2.0.1)

GnuPG Made Easy library

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gnupg.org/

### gpgmepp (2.0.0)

C++ wrapper for GPGME

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gnupg.org/

### gptfdisk (1.0.10)

GPT fdisk partition tools (sgdisk, cgdisk, gdisk, fixparts)

- License: `GPL-2.0-only`
- Homepage: https://www.rodsbooks.com/gdisk/

### grep-core (3.12)

GNU grep pattern matcher

- License: `GPL-3.0-or-later`

### groff (1.23.0)

GNU troff text formatting

- License: `GPL-3.0-or-later`

### grub (2.14)

GNU bootloader

- License: `GPL-3.0-or-later`

### gyp (0.22.2)

Generate Your Projects — meta-build system (gyp-next, the maintained fork)

- License: `BSD-3-Clause`
- Homepage: https://github.com/nodejs/gyp-next

### gzip-core (1.14)

GNU gzip compression

- License: `GPL-3.0-or-later`

### hatch-fancy-pypi-readme (25.1.0)

Hatch plugin for fancy PyPI READMEs

- License: `MIT`
- Homepage: https://github.com/hynek/hatch-fancy-pypi-readme

### hatch-vcs (0.5.0)

Hatch plugin for VCS version source

- License: `MIT`
- Homepage: https://github.com/ofek/hatch-vcs

### hatchling (1.28.0)

Python build backend

- License: `MIT`

### help2man (1.49.3)

Generate man pages from --help output

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/help2man/

### highway (1.3.0)

Performance-portable SIMD/vector intrinsics library

- License: `Apache-2.0`
- Homepage: https://github.com/google/highway

### iana-etc (20260202)

IANA network service and protocol data

- License: `MIT`

### icu (78.2)

International Components for Unicode

- License: `ICU`
- Homepage: https://icu.unicode.org/

### idna (3.16)

Internationalized Domain Names in Applications (IDNA)

- License: `BSD-3-Clause`
- Homepage: https://github.com/kjd/idna

### inetutils (2.7)

GNU network utilities

- License: `GPL-3.0-or-later`

### iniconfig (2.3.0)

Brain-dead simple parsing of ini files

- License: `MIT`
- Homepage: https://github.com/pytest-dev/iniconfig

### inih (62)

Simple INI file parser

- License: `BSD-3-Clause`
- Homepage: https://github.com/benhoyt/inih

### intel-ucode (20260512)

Intel CPU microcode firmware

- License: `LicenseRef-Intel-Microcode-License`
- Homepage: https://github.com/intel/Intel-Linux-Processor-Microcode-Data-Files

### intergenos-base-files (1.0.0)

InterGenOS canonical /etc + FHS-skeleton + systemd-preset + tmpfiles ownership (Fedora setup + Arch filesystem analog)

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergenos-default-settings (1.0.0)

InterGenOS canonical SSoT for GNOME desktop defaults — gschema-overrides + /etc/skel libadwaita bridge + burn-my-windows per-user profile + GDM greeter dconf customization (Tier 2 2026-05-22). Per D-006 requirement. Release 12 (2026-07-23) flips the default icon theme Papirus-Dark -> InterGenOS (the first-party theme, promoted to default at 1.4; 90_intergenos.gschema.override — the config lives outside the content-hash walk, hence the manual release bump forcing the rebuild). Release 11 (2026-07-14) disables greeter idle-suspend on AC via the gdm.d dconf fragment (sleep-inactive-ac-type='nothing') — a fresh install left at the login screen suspended after ~20 min (gnome-settings-daemon default) with no pre-login way to adjust the policy; the battery-power default (suspend) is retained deliberately. Release 10 (2026-07-10, HiDPI arc Tier 1) enables mutter fractional scaling + XWayland native scaling by default (92_intergenos-desktop.gschema.override experimental-features — both keywords verified against the pinned mutter 49.4 schema; the config lives outside the content-hash walk, hence the manual release bump forcing the rebuild). Release 8 (2026-06-19) adds the HiDPI greeter fix — a gdm.service ExecStartPre that forces a readable 2x greeter scale on >=4K displays exposing no EDID (virtual GPUs / panels with absent EDID, where GNOME's DPI auto-scale can't trigger); EDID and sub-4K displays untouched. Release 6 (2026-06-11) adds the GNOME app-overview "InterGenOS" folder (collects every X-InterGenOS-tagged app). Release 5 (2026-05-22) removes the duplicate-wordmark bandaid — the wordmark referenced by the GDM logo key is now provided by the intergen-mark package (theming-arc Item C closure).

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergenos-firewall-defaults (1.0.0)

InterGenOS default-deny nftables policy per D-011 requirement. Ships /etc/nftables.conf with default-drop INPUT/FORWARD + established/related + loopback + ICMP echo-request + frag-needed allowed, plus a bridge-scoped DHCP/DNS accept for libvirt NAT guests. SSH closed by default.

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergenos-helper-lib (1.0.0)

Sourceable bash library for pkm install helpers — gives helpers an API for recording installed files / symlinks / depends to a manifest pkm reads at install time. Closes H-007.

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergenos-keyring (0.1.0)

InterGenOS GPG release keyring — populates /etc/pkm/trusted.gpg with the master pubkey so pkm can verify signed index downloads

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergenos-legal (0.1.0)

InterGenOS legal documents — LICENSE + SOURCES.md (GPL §6 source-availability commitment) installed to /usr/share/doc/intergenos/

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intltool (0.51.0)

Internationalization tool collection

- License: `GPL-2.0-or-later`

### iproute2 (6.18.0)

IP routing utilities

- License: `GPL-2.0-or-later`

### iso-codes (4.20.1)

Country, language, and currency code lists

- License: `LGPL-2.1-or-later`
- Homepage: https://salsa.debian.org/iso-codes-team/iso-codes

### itstool (2.0.7)

ITS-based XML translation tool

- License: `GPL-3.0-or-later`

### iucode-tool (2.3.1)

Intel processor microcode management tool

- License: `GPL-2.0-or-later`
- Homepage: https://gitlab.com/iucode-tool/iucode-tool

### jansson (2.15.0)

C library for encoding, decoding and manipulating JSON data

- License: `MIT`
- Homepage: https://github.com/akheron/jansson

### jinja2 (3.1.6)

Template engine for Python

- License: `BSD-3-Clause`

### json-c (0.18)

JSON library for C

- License: `MIT`
- Homepage: https://github.com/json-c/json-c

### json-glib (1.10.8)

JSON parser for GLib

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Projects/JsonGlib

### kbd (2.9.0)

Keyboard utilities

- License: `GPL-2.0-or-later`

### keyutils (1.6.3)

Linux key management utilities

- License: `GPL-2.0-or-later`
- Homepage: https://people.redhat.com/~dhowells/keyutils/

### kmod (34.2)

Kernel module utilities

- License: `GPL-2.0-or-later`

### less (692)

Text file viewer

- License: `GPL-3.0-or-later`

### lib32-dbus (1.16.2)

D-Bus message bus client library — libdbus-1 (32-bit multilib runtime)

- License: `AFL-2.1 OR GPL-2.0-or-later`
- Homepage: https://www.freedesktop.org/wiki/Software/dbus/

### lib32-elfutils (0.194)

ELF object file access library, libelf only (32-bit multilib runtime)

- License: `GPL-2.0-or-later`
- Homepage: https://sourceware.org/elfutils/

### lib32-expat (2.7.4)

XML parser library (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://libexpat.github.io/

### lib32-glibc (2.43)

GNU C Library (32-bit multilib runtime)

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gnu.org/software/libc/

### lib32-libffi (3.5.2)

Foreign Function Interface library (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://sourceware.org/libffi/

### lib32-libgpg-error (1.59)

GPG error code library (32-bit multilib runtime)

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gnupg.org/

### lib32-libtasn1 (4.21.0)

ASN.1 library used by GnuTLS and p11-kit (32-bit multilib runtime)

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gnu.org/software/libtasn1/

### lib32-libxcrypt (4.5.2)

Modern password hashing library (32-bit multilib runtime)

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/besser82/libxcrypt

### lib32-llvm (21.1.8)

LLVM compiler infrastructure (32-bit runtime + build interface)

- License: `Apache-2.0`
- Homepage: https://llvm.org/

### lib32-nspr (4.38.2)

Netscape Portable Runtime (32-bit multilib runtime)

- License: `MPL-2.0`
- Homepage: https://developer.mozilla.org/en-US/docs/Mozilla/Projects/NSPR

### lib32-nss (3.121)

Network Security Services (32-bit multilib runtime)

- License: `MPL-2.0`
- Homepage: https://developer.mozilla.org/en-US/docs/Mozilla/Projects/NSS

### lib32-p11-kit (0.26.2)

PKCS#11 module loading library (32-bit multilib runtime)

- License: `BSD-3-Clause`
- Homepage: https://p11-glue.github.io/p11-glue/p11-kit.html

### lib32-sqlite (3510200)

SQL database engine (32-bit multilib runtime)

- License: `LicenseRef-Public-Domain`
- Homepage: https://sqlite.org/

### lib32-systemd-libs (259.1)

systemd shared libraries — libudev + libsystemd (32-bit multilib runtime)

- License: `LGPL-2.1-or-later`
- Homepage: https://systemd.io/

### lib32-zlib (1.3.2)

Compression library (32-bit multilib runtime)

- License: `Zlib`
- Homepage: https://zlib.net/

### lib32-zstd (1.5.7)

Zstandard real-time compression (32-bit multilib runtime)

- License: `BSD-3-Clause`
- Homepage: https://facebook.github.io/zstd/

### libaio (0.3.113)

Linux-native asynchronous I/O facility

- License: `LGPL-2.1-or-later`
- Homepage: https://pagure.io/libaio

### libarchive (3.8.6)

Multi-format archive and compression library

- License: `BSD-2-Clause`
- Homepage: https://libarchive.org/

### libassuan (3.0.2)

GnuPG IPC library

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gnupg.org/

### libatasmart (0.19)

ATA S.M.A.R.T. disk reporting library

- License: `LGPL-2.1-or-later`
- Homepage: https://0pointer.de/public/

### libblockdev (3.4.0)

Library for manipulating block devices

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/storaged-project/libblockdev

### libbytesize (2.12)

Library for operations with sizes in bytes

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/storaged-project/libbytesize

### libcap (2.77)

POSIX capabilities library

- License: `BSD-3-Clause`

### libcap-ng (0.9.3)

POSIX capabilities library and utilities

- License: `LGPL-2.1-or-later AND GPL-2.0-or-later`
- Homepage: https://github.com/stevegrubb/libcap-ng

### libffi (3.5.2)

Foreign Function Interface library

- License: `MIT`

### libfyaml (0.9.4)

YAML 1.3 parser and writer

- License: `MIT`
- Homepage: https://github.com/pantoniou/libfyaml

### libgcrypt (1.12.0)

General purpose cryptographic library

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gnupg.org/

### libgpg-error (1.59)

GPG error code library

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gnupg.org/

### libidn2 (2.3.8)

Internationalized domain names library

- License: `LGPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/libidn/

### libksba (1.6.7)

X.509 and CMS library

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gnupg.org/

### libmnl (1.0.5)

Minimalistic Netlink library

- License: `LGPL-2.1-or-later`
- Homepage: https://www.netfilter.org/projects/libmnl/

### libndp (1.9)

Neighbor Discovery Protocol library

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/jpirko/libndp

### libnftnl (1.2.9)

Netfilter nftables userspace library

- License: `GPL-2.0-or-later`
- Homepage: https://www.netfilter.org/projects/libnftnl/

### libnl (3.12.0)

Netlink protocol library suite

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/thom311/libnl

### libnvme (1.16.1)

NVMe management library

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/linux-nvme/libnvme

### libp11 (0.4.18)

PKCS#11 wrapper library and OpenSSL engine/provider for smart cards

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/OpenSC/libp11

### libpcap-pass1 (1.10.6)

Packet capture library (bootstrap — without Bluetooth capture)

- License: `BSD-3-Clause`

### libpipeline (1.5.8)

Pipeline manipulation library

- License: `GPL-3.0-or-later`

### libpsl (0.21.5)

Public Suffix List library

- License: `MIT`
- Homepage: https://github.com/rockdaboot/libpsl

### libpwquality (1.4.5)

Password quality checking library

- License: `GPL-2.0-or-later`

### libseccomp (2.6.0)

Enhanced seccomp library

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/seccomp/libseccomp

### libssh2 (1.11.1)

Client-side C library implementing the SSH2 protocol

- License: `BSD-3-Clause`
- Homepage: https://www.libssh2.org/

### libtasn1 (4.21.0)

ASN.1 library used by GnuTLS and p11-kit

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gnu.org/software/libtasn1/

### libtirpc (1.3.7)

Transport-Independent RPC library

- License: `BSD-3-Clause`
- Homepage: https://sourceforge.net/projects/libtirpc/

### libtool (2.5.4)

GNU generic library support script

- License: `GPL-2.0-or-later`

### libunistring (1.4.2)

Unicode string library for C

- License: `LGPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/libunistring/

### liburcu (0.15.3)

Userspace RCU (read-copy-update) library for lockless synchronization

- License: `LGPL-2.1-or-later`
- Homepage: https://liburcu.org/

### liburing (2.14)

Linux io_uring async I/O wrapper library (Jens Axboe)

- License: `MIT AND LGPL-2.1-only AND GPL-2.0-only`
- Homepage: https://github.com/axboe/liburing

### libusb (1.0.29)

USB access library

- License: `LGPL-2.1-or-later`
- Homepage: https://libusb.info/

### libuv (1.52.1)

Multi-platform asynchronous I/O library

- License: `MIT`
- Homepage: https://libuv.org/

### libxcrypt (4.5.2)

Modern password hashing library

- License: `LGPL-2.1-or-later`

### libxml2 (2.15.1)

XML parsing library

- License: `MIT`
- Homepage: https://gitlab.gnome.org/GNOME/libxml2

### libxslt (1.1.45)

XSLT processor library

- License: `MIT`
- Homepage: https://gitlab.gnome.org/GNOME/libxslt

### libyaml (0.2.5)

YAML 1.1 parser and emitter

- License: `MIT`
- Homepage: https://pyyaml.org/wiki/LibYAML

### linux-firmware (20260622)

Firmware files for Linux kernel drivers (WiFi, GPU, audio, etc.)

- License: `LicenseRef-Various-Redistributable`
- Homepage: https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git

### linux-kernel (6.18.10)

Linux kernel

- License: `GPL-2.0-only`

### linux-kernel-pass2 (6.18.10)

Linux kernel (pass 2 — rebuild with merged config fragments)

- License: `GPL-2.0-only`
- Homepage: https://www.kernel.org/

### linux-pam (1.7.2)

Pluggable Authentication Modules

- License: `GPL-2.0-or-later`
- Homepage: https://github.com/linux-pam/linux-pam

### llvm (21.1.8)

LLVM compiler infrastructure

- License: `Apache-2.0`
- Homepage: https://llvm.org/

### lmdb (0.9.35)

Lightning Memory-Mapped Database

- License: `OLDAP-2.8`
- Homepage: https://www.openldap.org/

### lua (5.4.8)

Lightweight scripting language

- License: `MIT`

### luajit (20260213)

Just-In-Time compiler for Lua

- License: `MIT`
- Homepage: https://luajit.org/

### lvm2 (2.03.38)

Logical Volume Manager

- License: `GPL-2.0-or-later`
- Homepage: https://sourceware.org/lvm2/

### lxml (6.0.2)

Python XML processing library

- License: `BSD-3-Clause`

### lz4 (1.10.0)

Extremely fast compression

- License: `BSD-2-Clause`

### lzip (1.26)

Lossless data compressor based on the LZMA algorithm (companion to xz/bzip2)

- License: `GPL-2.0-or-later`
- Homepage: https://www.nongnu.org/lzip/

### lzo (2.10)

Real-time data compression library

- License: `GPL-2.0-or-later`
- Homepage: https://www.oberhumer.com/opensource/lzo/

### m4-core (1.4.21)

GNU macro processor

- License: `GPL-3.0-or-later`

### make-ca (1.16.1)

CA certificate management utility

- License: `MIT`
- Homepage: https://github.com/lfs-book/make-ca

### make-core (4.4.1)

GNU make

- License: `GPL-3.0-or-later`

### man-db (2.13.1)

Manual page database

- License: `GPL-2.0-or-later`

### man-pages (6.17)

Linux man pages

- License: `LicenseRef-Linux-man-pages`

### mandoc (1.14.6)

BSD man page formatter and viewer

- License: `ISC`
- Homepage: https://mandoc.bsd.lv/

### markdown-it-py (4.2.0)

Markdown parser, done right — 100% CommonMark support

- License: `MIT`
- Homepage: https://github.com/executablebooks/markdown-it-py

### markupsafe (3.0.3)

Safe markup string implementation

- License: `BSD-3-Clause`

### maturin (1.13.1)

PEP 517 build backend for Rust + Python wheels (build dep for python-cryptography)

- License: `MIT OR Apache-2.0`
- Homepage: https://github.com/PyO3/maturin

### mdadm (4.4)

Linux MD software RAID administration utility

- License: `GPL-2.0-or-later`
- Homepage: https://kernel.org/pub/linux/utils/raid/mdadm/

### mdurl (0.1.2)

URL utilities for markdown-it-py

- License: `MIT`
- Homepage: https://github.com/executablebooks/mdurl

### meson (1.10.1)

High-performance build system

- License: `Apache-2.0`

### meson_python (0.19.0)

Python build backend (PEP 517) for Meson projects

- License: `MIT`
- Homepage: https://pypi.org/project/meson-python/

### mitkrb (1.22.2)

MIT Kerberos V5 authentication

- License: `MIT`

### mokutil (0.7.2)

Tool for managing Machine Owner Keys (MOK) for Secure Boot

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/lcp/mokutil

### mpc (1.3.1)

Multiple Precision Complex Library

- License: `LGPL-3.0-or-later`

### mpdecimal (4.0.1)

Library for correctly-rounded arbitrary-precision decimal floating-point arithmetic (libmpdec)

- License: `BSD-2-Clause`
- Homepage: https://www.bytereef.org/mpdecimal/

### mpfr (4.2.2)

Multiple Precision Floating-Point Reliable Library

- License: `LGPL-3.0-or-later`

### msgpack-cxx (8.0.0)

MessagePack serialization library for C++ (header-only)

- License: `BSL-1.0`
- Homepage: https://msgpack.org/

### multidict (6.7.1)

Multi-key dictionary (several values per key)

- License: `Apache-2.0`
- Homepage: https://github.com/aio-libs/multidict

### nano (8.7.1)

Small, friendly text editor with syntax highlighting and UTF-8 support

- License: `GPL-3.0-or-later`

### nasm (3.01)

Netwide Assembler

- License: `BSD-2-Clause`
- Homepage: https://www.nasm.us/

### ncurses-core (6.6)

Terminal handling library

- License: `MIT`

### nettle (3.10.2)

Low-level cryptographic library

- License: `LGPL-3.0-or-later`
- Homepage: https://www.lysator.liu.se/~nisse/nettle/

### networkmanager-pass1 (1.56.0)

Network connection manager (bootstrap — system networking only, no desktop integration)

- License: `GPL-2.0-or-later`
- Homepage: https://networkmanager.dev/

### newt (0.52.25)

Text mode windowing toolkit

- License: `LGPL-2.0-or-later`

### nftables (1.1.3)

Netfilter nftables packet filtering framework

- License: `GPL-2.0-or-later`
- Homepage: https://www.netfilter.org/projects/nftables/

### nghttp2 (1.68.1)

HTTP/2 C library

- License: `MIT`
- Homepage: https://nghttp2.org/

### ninja (1.13.2)

Small build system with focus on speed

- License: `Apache-2.0`

### nodejs (22.22.0)

JavaScript runtime built on V8

- License: `MIT`
- Homepage: https://nodejs.org/

### npth (1.8)

New portable threads library

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gnupg.org/

### nspr (4.38.2)

Netscape Portable Runtime

- License: `MPL-2.0`
- Homepage: https://developer.mozilla.org/en-US/docs/Mozilla/Projects/NSPR

### nss (3.121)

Network Security Services

- License: `MPL-2.0`
- Homepage: https://developer.mozilla.org/en-US/docs/Mozilla/Projects/NSS

### ntfs-3g (2026.2.25)

NTFS read/write driver + ntfsprogs userspace utilities (ntfsresize, ntfsfix, etc.)

- License: `GPL-2.0-or-later`
- Homepage: https://www.tuxera.com/community/open-source-ntfs-3g/

### numactl (2.0.19)

NUMA policy control library (libnuma) and utilities

- License: `LGPL-2.1-only AND GPL-2.0-only`
- Homepage: https://github.com/numactl/numactl

### oniguruma (6.9.10)

Multi-charset regular-expression library (jq's regex engine)

- License: `BSD-2-Clause`
- Homepage: https://github.com/kkos/oniguruma

### openldap (2.6.12)

Open source LDAP directory server and client libraries

- License: `OLDAP-2.8`
- Homepage: https://www.openldap.org/

### opensc (0.27.1)

PKCS#11 modules and tools for smart cards (PIV/PKCS#15), patched for NK1 RSA-4096

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/OpenSC/OpenSC

### openssh (10.2p1)

Secure Shell client and server

- License: `BSD-2-Clause`
- Homepage: https://www.openssh.com/

### openssl (3.6.1)

TLS/SSL and crypto library

- License: `Apache-2.0`

### os-prober (1.84)

Detect other operating systems for grub dual-boot menu entries

- License: `GPL-2.0-or-later`
- Homepage: https://salsa.debian.org/installer-team/os-prober

### p11-kit (0.26.2)

PKCS#11 module loading library

- License: `BSD-3-Clause`
- Homepage: https://p11-glue.github.io/p11-glue/p11-kit.html

### packaging (26.0)

Python packaging utilities

- License: `Apache-2.0`

### parted (3.7)

GNU Parted disk partition manipulation program (parted, partprobe, libparted)

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/parted/

### patch-core (2.8)

GNU patch

- License: `GPL-3.0-or-later`

### patchelf (0.18.0)

Tool for rewriting RPATH and dynamic-section entries of ELF binaries

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/NixOS/patchelf

### pathspec (1.0.4)

Utility library for gitignore style pattern matching

- License: `MPL-2.0`
- Homepage: https://github.com/cpburnz/python-pathspec

### pciutils (3.14.0)

PCI device listing and configuration utilities

- License: `GPL-2.0-or-later`
- Homepage: https://mj.ucw.cz/sw/pciutils/

### pcre2 (10.47)

Perl-compatible regular expressions

- License: `BSD-3-Clause`

### pcsc-lite (2.5.1)

PC/SC smart card daemon and middleware library

- License: `BSD-3-Clause`
- Homepage: https://pcsclite.apdu.fr/

### pdm-backend (2.4.9)

Build backend used by PDM, supporting current packaging standards

- License: `MIT`
- Homepage: https://github.com/pdm-project/pdm-backend

### perl-core (5.42.0)

Practical Extraction and Report Language

- License: `Artistic-1.0-Perl OR GPL-1.0-or-later`

### pinentry-pass1 (1.3.2)

PIN/passphrase entry dialog (bootstrap — TTY/curses only, no GNOME frontend)

- License: `GPL-2.0-or-later`
- Homepage: https://www.gnupg.org/

### pkgconf (2.5.1)

Package compiler and linker metadata toolkit

- License: `ISC`

### pkgconfig (1.6.0)

Python interface to the pkg-config command line tool (build dependency)

- License: `MIT`
- Homepage: https://github.com/matze/pkgconfig

### pkm (0.2.0)

InterGenOS package manager — install, remove, query, verify

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### pluggy (1.6.0)

Plugin management framework

- License: `MIT`

### poetry-core (2.4.1)

Poetry PEP 517 build backend (self-contained build dependency)

- License: `MIT`
- Homepage: https://github.com/python-poetry/poetry-core

### polkit (127)

PolicyKit authorization toolkit

- License: `LGPL-2.0-or-later`
- Homepage: https://github.com/polkit-org/polkit

### popt (1.19)

Command line option parsing library

- License: `MIT`
- Homepage: https://github.com/rpm-software-management/popt

### procps-ng (4.0.6)

Process monitoring utilities

- License: `GPL-2.0-or-later`

### prompt-toolkit (3.0.52)

Library for building powerful interactive command lines

- License: `BSD-3-Clause`
- Homepage: https://github.com/prompt-toolkit/python-prompt-toolkit

### propcache (0.5.2)

Accelerated property cache for Python

- License: `Apache-2.0`
- Homepage: https://github.com/aio-libs/propcache

### protobuf (33.5)

Protocol Buffers serialization library

- License: `BSD-3-Clause`

### psmisc (23.7)

Process management utilities

- License: `GPL-2.0-or-later`

### pycparser (2.22)

C parser in Python

- License: `BSD-3-Clause`
- Homepage: https://github.com/eliben/pycparser

### pyelftools (0.32)

Pure-Python library for parsing ELF + DWARF (build-time dependency for systemd-stub / sd-boot generation)

- License: `LicenseRef-Public-Domain`
- Homepage: https://github.com/eliben/pyelftools

### pygments (2.19.2)

Syntax highlighting library

- License: `BSD-2-Clause`

### pyproject-metadata (0.11.0)

PEP 621 metadata class with core metadata generation

- License: `MIT`
- Homepage: https://pypi.org/project/pyproject-metadata/

### pytest (9.1.1)

Simple powerful testing framework

- License: `MIT`
- Homepage: https://docs.pytest.org

### python (3.14.3)

Python programming language

- License: `PSF-2.0`

### python-cppheaderparser (2.7.4)

C++ header parser for Python (core — hipamd code-generation build dep)

- License: `BSD-3-Clause`
- Homepage: https://github.com/robotpy/robotpy-cppheaderparser

### python-cryptography (44.0.0)

Python cryptographic recipes and primitives

- License: `Apache-2.0 OR BSD-3-Clause`
- Homepage: https://cryptography.io/

### python-joblib (1.5.3)

Lightweight pipelining and parallelism for Python (core — Tensile build parallelism)

- License: `BSD-3-Clause`
- Homepage: https://joblib.readthedocs.io/

### python-msgpack (1.2.1)

MessagePack serializer for Python (core — Tensile logic-file fast path)

- License: `Apache-2.0`
- Homepage: https://msgpack.org/

### python-pefile (2024.8.26)

Python module to read and work with Portable Executable (PE) files

- License: `MIT`
- Homepage: https://github.com/erocarrera/pefile

### python-pip (25.3)

Python package installer, bootstrapped from the interpreter's bundled ensurepip wheel

- License: `MIT`
- Homepage: https://pip.pypa.io/

### python-ply (3.11)

Python Lex-Yacc parsing tools (core — CppHeaderParser runtime dep)

- License: `BSD-3-Clause`
- Homepage: https://www.dabeaz.com/ply/

### pyyaml (6.0.3)

YAML parser and emitter for Python (core — for igos-build)

- License: `MIT`
- Homepage: https://pyyaml.org/

### pyyaml-pass2 (6.0.3)

PyYAML (pass 2 — rebuild with Cython/libyaml C extension)

- License: `MIT`
- Homepage: https://pyyaml.org/

### readline (8.3)

Line input library with editing

- License: `GPL-3.0-or-later`

### rich (15.0.0)

Rich text and beautiful formatting in the terminal

- License: `MIT`
- Homepage: https://github.com/Textualize/rich

### rpcsvc-proto (1.4.4)

RPC service protocol definitions

- License: `BSD-3-Clause`

### rpm (4.18.2)

RPM package manager — provides rpm2cpio utility for extracting Fedora's MS-signed shim binary during the InterGenOS boot chain build (per shim-signed package dependency)

- License: `GPL-2.0-or-later`
- Homepage: https://rpm.org/

### ruby (4.0.1)

Ruby programming language

- License: `Ruby`

### rust (1.95.0)

Rust programming language

- License: `MIT`
- Homepage: https://www.rust-lang.org/

### rust-bindgen (0.72.1)

Rust FFI bindings generator

- License: `BSD-3-Clause`

### sbsigntool (0.9.5)

Tools for signing and verifying EFI binaries with Secure Boot keys

- License: `GPL-3.0-or-later`
- Homepage: https://git.kernel.org/pub/scm/linux/kernel/git/jejb/sbsigntools.git

### scikit-build-core (1.0.3)

Build backend for CMake-based Python projects

- License: `Apache-2.0`
- Homepage: https://github.com/scikit-build/scikit-build-core

### sed-core (4.9)

GNU stream editor

- License: `GPL-3.0-or-later`

### semantic-version (2.10.0)

Semantic-versioning comparison and constraint library for Python (a setuptools-rust runtime dependency)

- License: `BSD-2-Clause`
- Homepage: https://github.com/rbarrois/python-semanticversion

### setuptools (82.0.0)

Python package management

- License: `MIT`

### setuptools-rust (1.10.2)

Setuptools Rust extension plugin

- License: `MIT`
- Homepage: https://github.com/PyO3/setuptools-rust

### setuptools-scm (9.2.2)

Setuptools SCM plugin

- License: `MIT`

### sgml-common (0.6.3)

SGML common files

- License: `GPL-2.0-or-later`

### shadow (4.19.3)

Shadow password utilities

- License: `BSD-3-Clause`

### shadow-pam (4.19.3)

Shadow password suite (rebuilt with Linux-PAM support)

- License: `BSD-3-Clause`
- Homepage: https://github.com/shadow-maint/shadow

### shim-signed (16.1)

Microsoft-signed UEFI shim bootloader (Fedora-piggyback per D-002, dual-tracked with own shim-review PR per D-003)

- License: `BSD-2-Clause-Patent`
- Homepage: https://github.com/rhboot/shim

### slang-pass1 (2.3.3)

S-Lang programming library (bootstrap — without PNG image rendering)

- License: `GPL-2.0-or-later`

### sof-firmware (2025.12.2)

Sound Open Firmware binaries for Intel audio DSPs

- License: `BSD-3-Clause AND LicenseRef-Intel-SOF-Binary`
- Homepage: https://github.com/thesofproject/sof-bin

### soupsieve (2.9.1)

CSS selector library for Python (beautifulsoup4's required selector engine)

- License: `MIT`
- Homepage: https://github.com/facelessuser/soupsieve

### sqlite (3510200)

SQL database engine

- License: `LicenseRef-Public-Domain`

### sudo (1.9.17p2)

Execute commands as another user

- License: `ISC`
- Homepage: https://www.sudo.ws/

### systemd (259.1)

System and service manager

- License: `LGPL-2.1-or-later`

### tar-core (1.35)

GNU tar archiver

- License: `GPL-3.0-or-later`

### tcl (8.6.17)

Tool Command Language

- License: `TCL`
- Homepage: https://www.tcl.tk/

### texinfo-core (7.2)

GNU documentation system

- License: `GPL-3.0-or-later`

### tpm2-tools-static (5.7)

Statically-linked tpm2-tools subset for early-boot LUKS TPM2 unlock (initramfs, EXPERIMENTAL)

- License: `BSD-3-Clause`
- Homepage: https://github.com/tpm2-software/tpm2-tools

### trove-classifiers (2026.1.14.14)

Canonical trove classifiers for Python packages

- License: `Apache-2.0`
- Homepage: https://github.com/pypa/trove-classifiers

### typing-extensions (4.15.0)

Backported and experimental type hints

- License: `Python-2.0`
- Homepage: https://github.com/python/typing_extensions

### unifdef (2.12)

Remove

- License: `BSD-2-Clause`

### unifont (17.0.03)

GNU Unifont — Unicode bitmap font. Installs unifont.pcf so GRUB's build-time grub-mkfont compiles unicode.pf2, giving the boot menu a complete glyph set (prevents the missing-glyph TOFU '?'/'@' blocks). Matches BLFS 13.0.

- License: `GPL-2.0-or-later WITH Font-exception-2.0`
- Homepage: https://unifoundry.com/unifont/

### util-linux-core (2.41.3)

Miscellaneous system utilities

- License: `GPL-2.0-or-later`

### util-linux-pass2 (2.41.3)

Miscellaneous system utilities (pass 2 — rebuild with libcap-ng-backed setpriv)

- License: `GPL-2.0-or-later`

### util-macros (1.20.2)

Xorg autotools macros

- License: `MIT`
- Homepage: https://www.x.org/

### vala-pass1 (0.56.18)

Vala programming language compiler (bootstrap — without valadoc/graphviz)

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Projects/Vala

### versioneer (0.29)

Easy VCS-based management of project version strings

- License: `Unlicense`
- Homepage: https://github.com/python-versioneer/python-versioneer

### vim (9.2.0078)

Vi IMproved text editor

- License: `Vim`

### wcwidth (0.7.0)

Measure the rendered width of East Asian characters

- License: `MIT`
- Homepage: https://github.com/jquast/wcwidth

### wget (1.25.0)

Network file retriever

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/wget/

### wheel (0.46.3)

Python wheel packaging

- License: `MIT`

### which (2.23)

Utility to show the full path of commands

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/which/

### wpa_supplicant (2.11)

WPA/WPA2/IEEE 802.1X supplicant

- License: `BSD-3-Clause`
- Homepage: https://w1.fi/wpa_supplicant/

### xfsprogs (7.0.0)

XFS filesystem utilities (mkfs.xfs, xfs_repair, xfs_admin, xfs_growfs, etc.)

- License: `GPL-2.0-only`
- Homepage: https://xfs.org/

### xml-parser (2.47)

Perl XML parser module

- License: `Artistic-1.0-Perl`

### xmlto (0.0.29)

XML-to-format conversion tool

- License: `GPL-2.0-or-later`

### xorgproto (2025.1)

X11 protocol headers

- License: `MIT`
- Homepage: https://www.x.org/

### xxhash (0.8.3)

Extremely fast non-cryptographic hash algorithm (XXH32, XXH64, XXH3, XXH128) library + xxhsum CLI

- License: `BSD-2-Clause`
- Homepage: https://github.com/Cyan4973/xxHash

### xz (5.8.2)

XZ Utils compression

- License: `GPL-2.0-or-later`

### yarl (1.24.2)

Yet another URL library

- License: `Apache-2.0`
- Homepage: https://github.com/aio-libs/yarl

### zlib (1.3.2)

Compression library

- License: `Zlib`

### zram-generator (1.1.2)

Systemd unit generator for zram-backed compressed swap devices

- License: `MIT`
- Homepage: https://github.com/systemd/zram-generator

### zstd (1.5.7)

Zstandard real-time compression

- License: `BSD-3-Clause`


### Tier: `base`

### at (3.2.5)

Job scheduling commands (at, batch, atq, atrm)

- License: `GPL-2.0-or-later`

### atop (2.12.1)

Advanced system and process monitor

- License: `GPL-2.0-or-later`
- Homepage: https://www.atoptool.nl/

### bat (0.26.1)

A cat(1) clone with wings

- License: `MIT OR Apache-2.0`
- Homepage: https://github.com/sharkdp/bat

### bind-utils (9.20.19)

BIND DNS client utilities — dig, host, nslookup, nsupdate

- License: `MPL-2.0`
- Homepage: https://www.isc.org/bind/

### btop (1.4.6)

Resource monitor with TUI

- License: `Apache-2.0`
- Homepage: https://github.com/aristocratos/btop

### dialog (1.3-20260107)

Display dialog boxes from shell scripts (libdialog + dialog binary)

- License: `LGPL-2.1-or-later`
- Homepage: https://invisible-island.net/dialog/

### ed (1.22.5)

Classic UNIX line editor

- License: `GPL-2.0-or-later`
- Homepage: https://www.gnu.org/software/ed/

### exim (4.99.1)

Message Transfer Agent

- License: `GPL-2.0-or-later`
- Homepage: https://www.exim.org/

### fcron (3.4.0)

Periodical command scheduler

- License: `GPL-2.0-or-later`
- Homepage: http://fcron.free.fr/

### fd (9.0.0)

A simple, fast and user-friendly alternative to find

- License: `MIT OR Apache-2.0`
- Homepage: https://github.com/sharkdp/fd

### htop (3.4.1)

Interactive process viewer

- License: `GPL-2.0-or-later`
- Homepage: https://htop.dev/

### iotop (1.31)

I/O monitoring tool

- License: `GPL-2.0-or-later`
- Homepage: https://github.com/Tomas-M/iotop

### jq (1.8.2)

Command-line JSON processor

- License: `MIT`
- Homepage: https://jqlang.org

### libnsl (2.0.1)

NIS library (libnsl replacement for glibc)

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/thkukuk/libnsl

### lsof (4.99.6)

List open files

- License: `Zlib`
- Homepage: https://github.com/lsof-org/lsof

### mtools (4.0.49)

Utilities to access MS-DOS FAT filesystems without mounting them

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/mtools/

### neovim (0.12.3)

Hyperextensible Vim-based text editor

- License: `Apache-2.0`
- Homepage: https://neovim.io/

### parallel (20260322)

GNU parallel — shell tool for parallel command execution

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/parallel/

### pax (20240817)

POSIX standard archive utility

- License: `BSD-3-Clause`
- Homepage: http://www.mirbsd.org/pax.htm

### perl-file-fcntllock (0.22)

Perl module for file locking (required by Exim)

- License: `Artistic-2.0`
- Homepage: https://metacpan.org/pod/File::FcntlLock

### rdfind (1.8.0)

Redundant data finder — deduplicates files with hardlinks

- License: `GPL-2.0-or-later`
- Homepage: https://github.com/pauldreik/rdfind

### ripgrep (14.1.0)

Line-oriented search tool that recursively searches the current directory for a regex pattern

- License: `Unlicense OR MIT`
- Homepage: https://github.com/BurntSushi/ripgrep

### rsync (3.4.1)

Fast incremental file transfer

- License: `GPL-3.0-or-later`
- Homepage: https://rsync.samba.org/

### screen (5.0.1)

GNU Screen terminal multiplexer

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/screen/

### squashfs-tools (4.7.5)

Tools to create and extract Squashfs filesystems

- License: `GPL-2.0-or-later`
- Homepage: https://github.com/plougher/squashfs-tools

### sshpass (1.10)

Non-interactive ssh password provider

- License: `GPL-2.0-or-later`
- Homepage: https://sourceforge.net/projects/sshpass/

### strace (6.19)

System call tracer

- License: `LGPL-2.1-or-later`
- Homepage: https://strace.io/

### time (1.9)

GNU time - runs programs and summarizes resource usage

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/time/

### traceroute (2.1.6)

Trace the network path packets take to a host

- License: `GPL-2.0-or-later`
- Homepage: https://traceroute.sourceforge.net/

### unzip (6.0)

Extractor for PKZIP-compatible .zip archives

- License: `Info-ZIP`
- Homepage: https://infozip.sourceforge.net/UnZip.html

### whois (5.6.6)

Intelligent WHOIS client

- License: `GPL-2.0-or-later`
- Homepage: https://github.com/rfc1036/whois

### xorriso (1.5.8.pl02)

Creates, loads, manipulates and writes ISO 9660 filesystem images

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/xorriso/

### zip (3.0)

Info-ZIP archiver for creating ZIP archives

- License: `Info-ZIP`
- Homepage: http://infozip.sourceforge.net/


### Tier: `desktop`

### Mako (1.3.10)

Python template library (needed by Mesa)

- License: `MIT`

### a52dec (0.8.0)

Library for decoding ATSC A/52 (AC-3 / Dolby Digital) audio streams

- License: `GPL-2.0-or-later`
- Homepage: https://git.adelielinux.org/community/a52dec/

### accountsservice (23.13.9)

D-Bus interface for user account management

- License: `GPL-3.0-or-later`
- Homepage: https://www.freedesktop.org/wiki/Software/AccountsService/

### adw-gtk3-theme (6.5)

adw-gtk3 — libadwaita-styled GTK3 theme (light + dark), upstream of the Orchis Light welcomer GTK + the Orchis Dark welcomer GTK

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/lassekongo83/adw-gtk3

### adwaita-icon-theme (49.0)

GNOME default icon theme

- License: `LGPL-3.0-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/adwaita-icon-theme

### alsa-lib (1.2.15.3)

ALSA sound library

- License: `LGPL-2.1-or-later`
- Homepage: https://www.alsa-project.org/

### alsa-plugins (1.2.12)

ALSA plugin collection

- License: `LGPL-2.1-or-later`
- Homepage: https://www.alsa-project.org/

### alsa-ucm-conf (1.2.15.3)

ALSA Use Case Manager configuration profiles

- License: `BSD-3-Clause`
- Homepage: https://www.alsa-project.org

### alsa-utils (1.2.15.2)

ALSA utilities

- License: `GPL-2.0-or-later`
- Homepage: https://www.alsa-project.org/

### appstream (1.1.2)

AppStream metadata handling library

- License: `LGPL-2.1-or-later`
- Homepage: https://www.freedesktop.org/wiki/Distributions/AppStream/

### argcomplete (3.6.3)

Python tab-completion for argparse

- License: `Apache-2.0`
- Homepage: https://github.com/kislyuk/argcomplete

### aspell (0.60.8.2)

Interactive spell checking program and libraries

- License: `LGPL-2.1-or-later`
- Homepage: http://aspell.net/

### at-spi2-core (2.58.3)

Assistive Technology Service Provider Interface

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Accessibility

### atkmm (2.28.4)

C++ interface to the ATK accessibility toolkit (GTK3 version)

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gtkmm.org/

### avahi (0.8)

Service Discovery for Linux using mDNS/DNS-SD

- License: `LGPL-2.1-or-later`
- Homepage: https://avahi.org/

### babl (0.1.122)

Dynamic pixel format translation library

- License: `LGPL-3.0-or-later`
- Homepage: https://gegl.org/babl/

### baobab (49.1)

GNOME disk usage analyzer

- License: `GPL-2.0-or-later`
- Homepage: https://wiki.gnome.org/Apps/DiskUsageAnalyzer

### bdftopcf (1.1)

BDF to PCF bitmap font converter

- License: `MIT`
- Homepage: https://www.x.org/

### bibata-cursor-theme (2.0.7)

Bibata material-design cursor theme bundle (Modern-Classic + Modern-Amber + Modern-Ice; upstream v2.0.7)

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/ful1e5/Bibata_Cursor

### blueprint-compiler (0.18.0)

Markup language compiler for GTK UI files

- License: `LGPL-3.0-or-later`

### bluez (5.86)

Bluetooth protocol stack

- License: `GPL-2.0-or-later`
- Homepage: http://www.bluez.org/

### boost (1.90.0)

C++ utility libraries

- License: `BSL-1.0`
- Homepage: https://www.boost.org/

### bubblewrap (0.11.0)

Unprivileged sandboxing tool

- License: `LGPL-2.0-or-later`
- Homepage: https://github.com/containers/bubblewrap

### cairo (1.18.4)

2D graphics library

- License: `LGPL-2.1-or-later`
- Homepage: https://www.cairographics.org/

### cairomm (1.18.0)

C++ bindings for Cairo

- License: `LGPL-2.0-or-later`
- Homepage: https://www.cairographics.org/cairomm/

### catppuccin-gtk-theme (1.0.3)

Catppuccin GTK theme — Mocha-blue-standard variant (the welcomer's Catppuccin Mocha combo target)

- License: `MIT`
- Homepage: https://github.com/catppuccin/gtk

### cdparanoia (10.2)

CD audio extraction tool

- License: `GPL-2.0-or-later`

### colord (1.4.8)

Color management daemon

- License: `GPL-2.0-or-later`
- Homepage: https://www.freedesktop.org/software/colord/

### colord-gtk (0.3.1)

GTK integration for colord

- License: `LGPL-2.1-or-later`
- Homepage: https://www.freedesktop.org/software/colord/

### cups (2.4.16)

Common UNIX Printing System

- License: `Apache-2.0`
- Homepage: https://openprinting.github.io/cups/

### cups-filters (2.0.1)

CUPS print filters

- License: `Apache-2.0`

### cups-pk-helper (0.2.7)

PolicyKit mechanism exposing CUPS configuration to the GNOME Printers panel

- License: `GPL-2.0-or-later`
- Homepage: https://www.freedesktop.org/wiki/Software/cups-pk-helper/

### cybernetic-icon-theme (2.0)

Cybernetic Blue icon theme — featured alternate icon theme (decided 2026-05-22; the installed default is the first-party InterGenOS icon theme since 2026-07-23). Distinctive cybernetic-blue aesthetic; in-tree canonical bundle at /usr/share/icons/Cybernetic - Blue.

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/SethStormR/Cybernetic

### dart-sass (1.99.0)

Dart Sass — the official Sass compiler (CSS preprocessor, Dart Native AOT-compiled snapshot)

- License: `MIT`
- Homepage: https://sass-lang.com/dart-sass

### dav1d (1.5.3)

AV1 video decoder

- License: `BSD-2-Clause`
- Homepage: https://code.videolan.org/videolan/dav1d

### dbus-pass2 (1.16.2)

Message bus system (pass 2 — rebuild with doxygen API docs)

- License: `AFL-2.1 OR GPL-2.0-or-later`
- Homepage: https://www.freedesktop.org/wiki/Software/dbus/

### dbus-python (1.4.0)

Python bindings for D-Bus

- License: `MIT`
- Homepage: https://dbus.freedesktop.org/

### dconf (0.49.0)

Low-level GSettings backend

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Projects/dconf

### desktop-file-utils (0.28)

Desktop file validation and installation utilities

- License: `GPL-2.0-or-later`
- Homepage: https://www.freedesktop.org/wiki/Software/desktop-file-utils/

### double-conversion (3.4.0)

Binary-to-decimal and decimal-to-binary conversion routines for IEEE doubles

- License: `BSD-3-Clause`
- Homepage: https://github.com/google/double-conversion

### doxygen (1.16.1)

Documentation generation tool from annotated sources

- License: `GPL-2.0-or-later`
- Homepage: https://www.doxygen.nl/

### dracula-gtk-theme (4.0.0)

Dracula GTK theme — the classic Dracula dark color scheme for GTK + gnome-shell

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/dracula/gtk

### editorconfig-core-c (0.12.9)

EditorConfig core library — consistent coding styles across editors and IDEs

- License: `BSD-2-Clause`
- Homepage: https://editorconfig.org/

### enchant (2.8.15)

Generic spell checking library

- License: `LGPL-2.1-or-later`

### encodings (1.1.0)

X font encodings

- License: `MIT`

### evince (48.1)

GNOME document viewer

- License: `GPL-2.0-or-later`
- Homepage: https://wiki.gnome.org/Apps/Evince

### evolution-data-server (3.58.3)

Calendar and contacts data server

- License: `LGPL-2.0-or-later`

### exiv2 (0.28.7)

Image metadata library

- License: `GPL-2.0-or-later`

### fdk-aac (2.0.3)

Fraunhofer FDK AAC codec

- License: `FDK-AAC`
- Homepage: https://sourceforge.net/projects/opencore-amr/

### ffmpeg (8.0.1)

Complete multimedia framework — redistributable build (no nonfree codecs); use ffmpeg-nonfree for opt-in FDK-AAC

- License: `GPL-3.0-or-later`
- Homepage: https://ffmpeg.org/

### fftw (3.3.11)

Fastest Fourier Transform in the West (float + double precision)

- License: `GPL-2.0-or-later`
- Homepage: http://www.fftw.org/

### file-roller (44.6)

GNOME archive manager

- License: `GPL-2.0-or-later`
- Homepage: https://wiki.gnome.org/Apps/FileRoller

### flac (1.5.0)

Free Lossless Audio Codec

- License: `GPL-2.0-or-later`
- Homepage: https://xiph.org/flac/

### fluent-gtk-theme (2025-04-17)

Fluent GTK theme — Microsoft Fluent Design-inspired GTK and gnome-shell theme (default + light + dark)

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/vinceliuice/Fluent-gtk-theme

### fluent-icon-theme (2025-08-21)

Fluent icon theme — Microsoft Fluent Design-inspired icon set with default + light + dark variants

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/vinceliuice/Fluent-icon-theme

### folks (0.15.12)

People aggregation library

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Projects/Folks

### font-alias (1.0.6)

X font aliases

- License: `MIT`

### font-cursor-misc (1.0.4)

Cursor fonts for X

- License: `LicenseRef-Public-Domain`

### font-dejavu (2.37)

DejaVu TrueType fonts

- License: `Bitstream-Vera`

### font-inter (4.1)

Inter — clean modern geometric sans variable font (regular + italic). Ships InterVariable.ttf + InterVariable-Italic.ttf only (single-file all-weights variable fonts; static + web variants dropped). Author Rasmus Andersson. Used as InterGenOS system + document + titlebar font default (decided 2026-05-22; matches wordmark restrained-geometric voice).

- License: `OFL-1.1`
- Homepage: https://github.com/rsms/inter

### font-jetbrains-mono (2.304)

JetBrains Mono — modern monospace variable font with programming ligatures (regular + italic). Single-file variable-weight fonts; ligature-enabled variant (NOT the "NL" no-ligatures variant). Used as InterGenOS monospace default (decided 2026-05-22) for terminal + text editor + code surfaces.

- License: `OFL-1.1`
- Homepage: https://github.com/JetBrains/JetBrainsMono

### font-misc-misc (1.1.3)

Miscellaneous X fonts

- License: `LicenseRef-Public-Domain`

### font-noto (2025.12.01)

Google Noto fonts

- License: `OFL-1.1`
- Homepage: https://fonts.google.com/noto

### font-noto-color-emoji (2.051)

Google Noto Color Emoji font — flag emojis + full Unicode emoji coverage

- License: `OFL-1.1`
- Homepage: https://github.com/googlefonts/noto-emoji

### font-util (1.4.1)

X font utilities

- License: `MIT`

### fontconfig (2.17.1)

Font configuration and customization library

- License: `MIT`
- Homepage: https://www.freedesktop.org/wiki/Software/fontconfig/

### forge (1.0.0)

InterGenOS system installer (Forge) — GTK4/libadwaita GUI + declarative-builder TUI; entry point dispatched via igos.mode= kernel cmdline

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### freerdp (3.22.0)

Free implementation of the Remote Desktop Protocol

- License: `Apache-2.0`
- Homepage: https://www.freerdp.com/

### freetype2 (2.14.1)

FreeType font rendering library (pass 2 — with HarfBuzz)

- License: `FTL`
- Homepage: https://www.freetype.org/

### freetype2-pass1 (2.14.1)

FreeType font rendering library (pass 1 — without HarfBuzz)

- License: `FTL`
- Homepage: https://www.freetype.org/

### fribidi (1.0.16)

Unicode Bidirectional Algorithm library

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/fribidi/fribidi

### gcr (3.41.2)

GLib crypto and PKCS#11 framework (GTK3 version)

- License: `LGPL-2.1-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/gcr

### gcr4 (4.4.0.1)

GNOME crypto and certificate library

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Projects/GnomeCrypto

### gdk-pixbuf (2.44.5)

Image loading library for GTK

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Projects/GdkPixbuf

### gdk-pixbuf-pass2 (2.44.5)

gdk-pixbuf (pass 2 — rebuild with glycin support for GTK4 image loading)

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Projects/GdkPixbuf

### gdm (49.2)

GNOME Display Manager

- License: `GPL-2.0-or-later`
- Homepage: https://wiki.gnome.org/Projects/GDM

### gegl (0.4.66)

GEneric Graphics Library — graph-based image processing framework

- License: `LGPL-3.0-or-later`
- Homepage: https://gegl.org/

### geoclue2 (2.8.0)

D-Bus geolocation service

- License: `LGPL-2.0-or-later`
- Homepage: https://gitlab.freedesktop.org/geoclue/geoclue

### geocode-glib (3.26.4)

Geocoding and reverse geocoding library

- License: `LGPL-2.0-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/geocode-glib

### gexiv2 (0.16.0)

GObject wrapper for Exiv2

- License: `GPL-2.0-or-later`

### ghostscript (10.06.0)

Interpreter for the PostScript language and PDF

- License: `AGPL-3.0-or-later`
- Homepage: https://www.ghostscript.com/

### giflib (5.2.2)

GIF image library

- License: `MIT`
- Homepage: https://giflib.sourceforge.net/

### gjs (1.86.0)

GNOME JavaScript bindings

- License: `LGPL-2.0-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/gjs

### glad (2.0.8)

OpenGL/Vulkan/EGL loader generator

- License: `MIT`
- Homepage: https://github.com/Dav1dde/glad

### glib-networking (2.80.1)

GIO networking extensions

- License: `LGPL-2.1-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/glib-networking

### glibmm (2.86.0)

C++ bindings for GLib

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gtkmm.org/

### glibmm2 (2.66.7)

C++ bindings for GLib (API version 2.4, GTK3 compatibility)

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gtkmm.org/

### glm (1.0.3)

OpenGL Mathematics — header-only C++ math library for graphics

- License: `MIT`
- Homepage: https://github.com/g-truc/glm

### glslang (16.2.0)

GLSL/HLSL to SPIR-V compiler

- License: `Apache-2.0`

### glu (9.0.3)

Mesa OpenGL Utility library

- License: `SGI-B-2.0`
- Homepage: https://mesa3d.org/

### glycin (2.0.8)

Sandboxed and extendable image loading library

- License: `MPL-2.0`
- Homepage: https://gitlab.gnome.org/GNOME/glycin

### gnome-autoar (0.4.5)

GNOME automatic archive library

- License: `LGPL-2.1-or-later`

### gnome-backgrounds (49.0)

GNOME desktop wallpapers

- License: `CC-BY-SA-3.0`

### gnome-bluetooth (47.1)

GNOME Bluetooth integration

- License: `GPL-2.0-or-later`

### gnome-calculator (49.2)

GNOME calculator application

- License: `GPL-3.0-or-later`
- Homepage: https://wiki.gnome.org/Apps/Calculator

### gnome-calendar (49.1)

GNOME calendar application

- License: `GPL-3.0-or-later`
- Homepage: https://apps.gnome.org/Calendar/

### gnome-characters (49.1)

GNOME character map application

- License: `GPL-2.0-or-later`
- Homepage: https://apps.gnome.org/Characters/

### gnome-clocks (50.0)

GNOME clocks application

- License: `GPL-2.0-or-later`
- Homepage: https://apps.gnome.org/Clocks/

### gnome-connections (49.0)

GNOME remote desktop client (VNC and RDP)

- License: `GPL-3.0-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/connections

### gnome-contacts (49.0)

GNOME contacts manager

- License: `GPL-2.0-or-later`
- Homepage: https://apps.gnome.org/Contacts/

### gnome-control-center (49.4)

GNOME system settings

- License: `GPL-2.0-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/gnome-control-center

### gnome-desktop (44.5)

GNOME desktop core library

- License: `LGPL-2.0-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/gnome-desktop

### gnome-disk-utility (46.1)

GNOME disk management utility

- License: `GPL-2.0-or-later`
- Homepage: https://wiki.gnome.org/Apps/Disks

### gnome-font-viewer (50.0)

GNOME font viewer

- License: `GPL-2.0-or-later`
- Homepage: https://apps.gnome.org/FontViewer/

### gnome-keyring (48.0)

GNOME password and secret storage

- License: `GPL-2.0-or-later`
- Homepage: https://wiki.gnome.org/Projects/GnomeKeyring

### gnome-logs (45.0)

GNOME system log viewer

- License: `GPL-3.0-or-later`
- Homepage: https://wiki.gnome.org/Apps/Logs

### gnome-maps (49.4)

GNOME maps application

- License: `GPL-2.0-or-later`
- Homepage: https://wiki.gnome.org/Apps/Maps

### gnome-menus (3.38.1)

GNOME menu specification implementation

- License: `LGPL-2.0-or-later`

### gnome-music (49.1)

GNOME music player

- License: `GPL-2.0-or-later`
- Homepage: https://apps.gnome.org/Music/

### gnome-online-accounts (3.56.4)

GNOME online accounts service

- License: `LGPL-2.0-or-later`
- Homepage: https://wiki.gnome.org/Projects/GnomeOnlineAccounts

### gnome-screenshot (41.0)

GNOME screenshot utility

- License: `GPL-2.0-or-later`
- Homepage: https://wiki.gnome.org/Apps/Screenshot

### gnome-session (49.2)

GNOME session manager

- License: `GPL-2.0-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/gnome-session

### gnome-settings-daemon (49.1)

GNOME settings daemon

- License: `GPL-2.0-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/gnome-settings-daemon

### gnome-shell (49.4)

GNOME desktop shell

- License: `GPL-2.0-or-later`
- Homepage: https://wiki.gnome.org/Projects/GnomeShell

### gnome-shell-extensions (49.0)

GNOME Shell extensions collection

- License: `GPL-2.0-or-later`

### gnome-system-monitor (49.1)

GNOME system monitor

- License: `GPL-2.0-or-later`
- Homepage: https://wiki.gnome.org/Apps/SystemMonitor

### gnome-terminal (3.58.1)

GNOME terminal emulator

- License: `GPL-3.0-or-later`
- Homepage: https://wiki.gnome.org/Apps/Terminal

### gnome-text-editor (50.0)

GNOME text editor

- License: `GPL-3.0-or-later`
- Homepage: https://apps.gnome.org/TextEditor/

### gnome-tweaks (49.0)

GNOME advanced settings tool

- License: `GPL-3.0-or-later`
- Homepage: https://wiki.gnome.org/Apps/Tweaks

### gnome-user-docs (49.4)

GNOME user documentation

- License: `CC-BY-SA-3.0`

### gnome-weather (48.0)

GNOME weather application

- License: `GPL-2.0-or-later`
- Homepage: https://wiki.gnome.org/Apps/Weather

### gobject-introspection (1.86.0)

GObject type introspection framework (full — with cairo + doctool)

- License: `LGPL-2.0-or-later`
- Homepage: https://gi.readthedocs.io/

### graphene (1.10.8)

Graphics data types library

- License: `MIT`
- Homepage: https://ebassi.github.io/graphene/

### graphite-gtk-theme (2025-07-06)

Graphite GTK theme — minimal flat-design GTK and gnome-shell theme (default + dark + light variants)

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/vinceliuice/Graphite-gtk-theme

### graphite2 (1.3.14)

Font rendering engine for complex scripts

- License: `LGPL-2.1-or-later`
- Homepage: https://graphite.sil.org/

### graphviz (14.1.2)

Graph visualization software

- License: `EPL-1.0`
- Homepage: https://graphviz.org/

### grilo (0.3.19)

Media discovery framework

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Projects/Grilo

### grilo-plugins (0.3.18)

Plugins for the Grilo media discovery framework

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Projects/Grilo

### gsettings-desktop-schemas (49.1)

GSettings schemas for GNOME desktop

- License: `LGPL-2.1-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/gsettings-desktop-schemas

### gsound (1.0.3)

GObject wrapper for libcanberra

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Projects/GSound

### gspell (1.14.2)

Spell checking library for GTK applications

- License: `LGPL-2.1-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/gspell

### gst-libav (1.28.1)

GStreamer FFmpeg-backed broad codec coverage

- License: `LGPL-2.0-or-later`
- Homepage: https://gstreamer.freedesktop.org/

### gst-plugins-bad (1.28.1)

GStreamer bad plugins

- License: `LGPL-2.0-or-later`
- Homepage: https://gstreamer.freedesktop.org/

### gst-plugins-base (1.28.1)

GStreamer base plugins

- License: `LGPL-2.0-or-later`
- Homepage: https://gstreamer.freedesktop.org/

### gst-plugins-base-pass2 (1.28.1)

GStreamer base plugins (pass 2 — with Mesa GL support)

- License: `LGPL-2.0-or-later`
- Homepage: https://gstreamer.freedesktop.org/

### gst-plugins-good (1.28.1)

GStreamer good plugins

- License: `LGPL-2.0-or-later`
- Homepage: https://gstreamer.freedesktop.org/

### gst-plugins-ugly (1.28.1)

GStreamer ugly plugins (MP3 + patent-encumbered codecs)

- License: `LGPL-2.0-or-later`
- Homepage: https://gstreamer.freedesktop.org/

### gstreamer (1.28.1)

GStreamer multimedia framework

- License: `LGPL-2.0-or-later`
- Homepage: https://gstreamer.freedesktop.org/

### gtk-vnc (1.5.0)

VNC viewer widget for GTK

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Projects/gtk-vnc

### gtk3 (3.24.51)

GTK 3 widget toolkit

- License: `LGPL-2.0-or-later`
- Homepage: https://www.gtk.org/

### gtk4 (4.20.3)

GTK 4 widget toolkit

- License: `LGPL-2.0-or-later`
- Homepage: https://www.gtk.org/

### gtkmm4 (4.20.0)

C++ bindings for GTK4

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gtkmm.org/

### gtksourceview5 (5.18.0)

Source code editing widget for GTK4

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Projects/GtkSourceView

### gvfs (1.58.2)

GNOME virtual filesystem

- License: `LGPL-2.0-or-later`

### gvfs-pass2 (1.58.2)

GNOME virtual filesystem (pass 2 — with GOA and OneDrive)

- License: `LGPL-2.0-or-later`
- Homepage: https://wiki.gnome.org/Projects/gvfs

### harfbuzz (12.3.2)

Text shaping engine

- License: `MIT`
- Homepage: https://harfbuzz.github.io/

### hicolor-icon-theme (0.18)

Default fallback icon theme

- License: `GPL-2.0-or-later`
- Homepage: https://www.freedesktop.org/wiki/Software/icon-theme/

### hwdata (0.404)

Hardware identification and configuration data

- License: `GPL-2.0-or-later`
- Homepage: https://github.com/vcrhonek/hwdata

### ibus (1.5.33)

Intelligent Input Bus framework

- License: `LGPL-2.1-or-later`

### iceauth (1.0.10)

ICE authority file utility

- License: `MIT`

### imagemagick (7.1.2-13)

Image processing and conversion suite

- License: `ImageMagick`
- Homepage: https://imagemagick.org/

### intergen-firstboot (1.0)

InterGenOS first-login branded ECG-pulse animation (GNOME Shell extension) -- renders the operator-tuned 22.4s pulse + text sequence above gnome-shell's session-startup UI on the first login per user; subsequent logins no-op via filesystem marker gate

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergen-mark (1.0)

InterGenOS brand-mark icon + logo + wordmark assets installed to standard hicolor / pixmaps / /usr/share/intergenos locations. release 2 rounds the badge-tile corners (GNOME-style squircle, operator branding §B) and adds the greyscale boxed symbolic mark for the custom ArcMenu "InterGenOS" category. Closes audit-rows D-010 (brand assets never installed) + J-016 (brand assets missing from hicolor) + theming-arc dispatch Item C (canonical brand-mark package); supersedes the duplicate-wordmark bandaid in intergenos-default-settings. Source-of-truth at assets/intergen-mark/ stays the brand-source canonical (with generate.py + README + all variants); build.sh reaches into it via IGOS_SOURCE_ROOT mirroring intergenos-default-settings' in-tree-config pattern.

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergen-no-overview (1.0)

GNOME Shell extension that suppresses the activities overview at every session startup (InterGenOS fleet-default per the decision of 2026-05-21)

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergen-pkm-notifier (1.0)

GNOME Shell extension tray indicator for available pkm package upgrades (Q8 Phase C notification surface)

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergen-toggle (1.0)

GUI toggle to enable/disable the opt-in InterGen AI assistant. release 4 corrects StartupWMClass to com.intergenos.intergen-toggle (matches the GApplication id) so the running window gets the toggle icon, not a generic gear. release 3 relabels the launcher "InterGen AI" → "Enable InterGen AI", ships a proper app icon (pulse mark in a power-on ring, replacing the unresolved generic gear), and tags it under the custom ArcMenu "InterGenOS" category. Single-screen GTK4 + libadwaita app that mirrors the Forge installer's "Enable the InterGen AI assistant?" toggle for post-install reconfiguration. Honors D-010 (opt-in by design; user-control posture); user explicitly toggles to enable, never auto-enables. Provides the GUI path referenced in Forge + welcomer copy ("opt in later either by opening the InterGen AI app from your Applications menu OR by running `intergen setup` in a terminal").

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergen-welcome (1.0)

InterGenOS first-boot welcome greeter — GTK4/libadwaita appearance + extensions chooser + prompt chooser; auto-launches once per new user account. release 7 adds StartupWMClass=com.intergenos.welcome so GNOME's window tracker paints the running window with the InterGenOS Welcomer icon instead of a generic gear (the app-id didn't match the .desktop id). release 6 renames the launcher "InterGenOS Welcome" → "InterGenOS Welcomer", ships a proper app icon (framed glass squircle + pulse + welcome sparkle, replacing the generic preferences avatar), and tags it under the custom ArcMenu "InterGenOS" category. release 5 adds the "Enable Services" page — opt-in toggles for the services InterGenOS ships off by default (print / network-discovery / SSH), each driving a pkexec privileged helper (root-password prompt) that sets the service up end-to-end and is fully reversible. release 4 fixes the Meet InterGen page overflow (scroll-on-overflow + valign FILL + taller 760x720 window + short header title) so the title is no longer clipped under the header and the closing paragraph is no longer cut off the bottom; release 3 adds Stock-vs-Starship prompt-chooser page (D-014 ratified 2026-05-20) wiring; release 2 added Cybernetic Blue theme combo (10th combo) + 10 deterministic SVG-rendered appearance-picker thumbnails.

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergenos-backup (1.0)

Chronicle — the InterGenOS backup utility. A browse-by-time history of three layers on a stack with no copy-on-write filesystem: config state and package restore points in an always-on content-addressed store on the system disk, and hourly user-data history to an external POSIX target by hardlink rotation. Ships the chronicled engine + sentinel, the chronicle CLI, an org.intergenos.Chronicle GTK4/libadwaita GUI, the pkm pre-transaction restore point handler, and the systemd schedule (hourly capture, off-peak drain, weekly scrub) with a separate higher-capability restore leg.

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergenos-extensions-appearance (1.0)

InterGenOS Appearance-category GNOME Shell extensions — Blur My Shell, Burn My Windows, Rounded Window Corners, Desktop Cube, Night Theme Switcher

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergenos-extensions-layout (1.0)

InterGenOS Layout-category GNOME Shell extensions — Dash to Dock, Dash to Panel, ArcMenu, Show Desktop Button. release 2 carries a downstream ArcMenu patch (patches/arcmenu-intergenos-category.patch) adding a custom "InterGenOS" system category that collects every X-InterGenOS-tagged app, a greyscale boxed category icon, and a hover tooltip on the panel start button (operator branding §A/§G).

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergenos-extensions-productivity (1.0)

InterGenOS Productivity-category GNOME Shell extensions — Coverflow Alt-Tab, Clipboard Indicator, Tiling Shell, Forge, ddterm, Alphabetical App Grid

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergenos-extensions-utilities (1.0)

InterGenOS Utilities-category GNOME Shell extensions — AppIndicator, Bluetooth Quick Connect, Caffeine, Vitals, Media Controls, GSConnect, Just Perfection, Desktop Icons NG

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergenos-grub-theme (1.0)

InterGenOS GRUB boot-menu theme + resolution-aware background scaffold (operator-authored design 2026-05-21; K1 closure) -- ships 12 Steam-hardware-survey-grounded background images + a runtime /etc/grub.d/06_intergenos_multi_background override that picks the matching PNG based on GRUB's negotiated $gfxmode + a minimal theme.txt that suppresses the menu border so the lower-right brand mark stays unobscured. Composes with upstream 05_debian_theme rather than forking it.

- License: `GPL-3.0-or-later`
- Payload license: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergenos-icon-theme (1.0)

InterGenOS first-party icon theme — 283 marks (applications + places + shell status/symbolic) compiled by the in-house icon compiler; inherits Adwaita/hicolor for full coverage.

- License: `GPL-3.0-or-later`
- Homepage: https://intergenos.org

### intergenos-launch-monitor (1.0)

GNOME Shell extension that moves a launched game window to the user-declared monitor before its first frame (steam_app_*/gamescope match; gsettings monitor picker)

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergenos-settings-arrow (1.0)

GNOME Shell extension re-routing the Wi-Fi + Bluetooth quick-settings menu arrows to open their Settings panel directly, avoiding the off-screen submenu overflow on short panels (PI-Welcome-2)

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergenos-theme (1.0.0)

InterGenOS native metatheme — GTK 3/4 + GNOME Shell stylesheets (ECG blue on deep navy); references the first-party InterGenOS icons + Bibata-Modern-Classic cursor

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergenos-wallpapers (1.0)

InterGenOS curated desktop wallpapers (4 × 3840x2160) — ItIsOnly (default) + Helix + Overwatch + Pulse. First-party authored; ships PNGs + gnome-background-properties XML manifest so GNOME Settings → Appearance → Background surfaces them as a curated set. Default-set is keyed via picture-uri / picture-uri-dark gschema override shipped by intergenos-default-settings (D-006 SSoT).

- License: `GPL-3.0-or-later`
- Payload license: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### intergenos-wiki (1.0.0)

InterGenOS wiki — rendered mdBook HTML docs + operator-signed per-page sha256 manifest (InterGen verifies a page against this manifest before citing it)

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### iptables (1.8.12)

iptables CLI compat shim — translates iptables syntax to nftables rules

- License: `GPL-2.0-or-later`
- Homepage: https://www.netfilter.org/

### ladspa-sdk (1.17)

LADSPA audio plugin SDK — headers, example plugins, host-side analysis tools

- License: `LGPL-2.1-only`
- Homepage: https://www.ladspa.org/

### lame (3.100)

MP3 encoder

- License: `LGPL-2.0-or-later`
- Homepage: https://lame.sourceforge.io/

### lame-pass2 (3.100)

LAME (pass 2 — rebuild with libsndfile for multi-format audio input)

- License: `LGPL-2.0-or-later`
- Homepage: https://lame.sourceforge.io/

### lcms2 (2.18)

Little Color Management System

- License: `MIT`
- Homepage: https://www.littlecms.com/

### lib32-alsa-lib (1.2.15.3)

ALSA sound library (32-bit multilib runtime)

- License: `LGPL-2.1-or-later`
- Homepage: https://www.alsa-project.org/

### lib32-alsa-plugins (1.2.12)

ALSA plugin collection, pulse plugins only (32-bit multilib runtime)

- License: `LGPL-2.1-or-later`
- Homepage: https://www.alsa-project.org/

### lib32-flac (1.5.0)

Free Lossless Audio Codec (32-bit multilib runtime)

- License: `GPL-2.0-or-later`
- Homepage: https://xiph.org/flac/

### lib32-libX11 (1.8.13)

Core X11 client library (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://www.x.org/

### lib32-libXScrnSaver (1.2.5)

X11 Screen Saver extension library (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://www.x.org/

### lib32-libXau (1.0.12)

X11 Authorization Protocol library (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://www.x.org/

### lib32-libXdamage (1.1.7)

X Damage extension library (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://www.x.org/

### lib32-libXdmcp (1.1.5)

X Display Manager Control Protocol library (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://www.x.org/

### lib32-libXext (1.3.7)

X11 miscellaneous extensions library (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://www.x.org/

### lib32-libXfixes (6.0.2)

X Fixes extension library (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://www.x.org/

### lib32-libXinerama (1.1.6)

X Xinerama extension library (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://www.x.org/

### lib32-libXrandr (1.5.5)

X Resize and Rotate extension library (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://www.x.org/

### lib32-libXrender (0.9.12)

X Render extension library (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://www.x.org/

### lib32-libXxf86vm (1.1.7)

X XFree86 Video Mode extension library (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://www.x.org/

### lib32-libdrm (2.4.131)

Direct Rendering Manager library (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://dri.freedesktop.org/

### lib32-libglvnd (1.7.0)

GL Vendor-Neutral Dispatch library (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://gitlab.freedesktop.org/glvnd/libglvnd

### lib32-libogg (1.3.6)

Ogg bitstream container library (32-bit multilib runtime)

- License: `BSD-3-Clause`
- Homepage: https://www.xiph.org/ogg/

### lib32-libpulse (17.0)

PulseAudio client libraries (32-bit multilib runtime)

- License: `LGPL-2.1-or-later`
- Homepage: https://www.freedesktop.org/wiki/Software/PulseAudio/

### lib32-libsndfile (1.2.2)

Library for reading and writing sound files (32-bit multilib runtime)

- License: `LGPL-2.1-or-later`
- Homepage: https://libsndfile.github.io/libsndfile/

### lib32-libvorbis (1.3.7)

Vorbis audio codec library (32-bit multilib runtime)

- License: `BSD-3-Clause`
- Homepage: https://www.xiph.org/vorbis/

### lib32-libxcb (1.17.0)

X C Binding library (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://xcb.freedesktop.org/

### lib32-libxkbcommon (1.13.1)

Keyboard handling library (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://xkbcommon.org/

### lib32-libxshmfence (1.3.3)

X shared memory fence library (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://www.x.org/

### lib32-mesa (25.3.5)

OpenGL and Vulkan implementation (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://www.mesa3d.org/

### lib32-opus (1.6.1)

Interactive speech and audio codec (32-bit multilib runtime)

- License: `BSD-3-Clause`
- Homepage: https://opus-codec.org/

### lib32-vulkan-loader (1.4.341.0)

Vulkan ICD loader (32-bit multilib runtime)

- License: `Apache-2.0`
- Homepage: https://github.com/KhronosGroup/Vulkan-Loader

### lib32-wayland (1.24.0)

Wayland display server protocol libraries (32-bit multilib runtime)

- License: `MIT`
- Homepage: https://wayland.freedesktop.org/

### libFS (1.0.10)

X Font Service client library

- License: `MIT`
- Homepage: https://www.x.org/

### libICE (1.1.2)

Inter-Client Exchange library

- License: `MIT`
- Homepage: https://www.x.org/

### libSM (1.2.6)

X Session Management library

- License: `MIT`
- Homepage: https://www.x.org/

### libX11 (1.8.13)

Core X11 client library

- License: `MIT`
- Homepage: https://www.x.org/

### libXScrnSaver (1.2.5)

X11 Screen Saver extension library

- License: `MIT`
- Homepage: https://www.x.org/

### libXau (1.0.12)

X11 Authorization Protocol library

- License: `MIT`
- Homepage: https://www.x.org/

### libXaw (1.0.16)

X Athena Widgets library

- License: `MIT`
- Homepage: https://www.x.org/

### libXcomposite (0.4.7)

X Composite extension library

- License: `MIT`
- Homepage: https://www.x.org/

### libXcursor (1.2.3)

X Cursor management library

- License: `MIT`
- Homepage: https://www.x.org/

### libXdamage (1.1.7)

X Damage extension library

- License: `MIT`
- Homepage: https://www.x.org/

### libXdmcp (1.1.5)

X Display Manager Control Protocol library

- License: `MIT`
- Homepage: https://www.x.org/

### libXext (1.3.7)

X11 miscellaneous extensions library

- License: `MIT`
- Homepage: https://www.x.org/

### libXfixes (6.0.2)

X Fixes extension library

- License: `MIT`
- Homepage: https://www.x.org/

### libXfont2 (2.0.7)

X font handling library

- License: `MIT`

### libXft (2.3.9)

X FreeType interface library

- License: `MIT`
- Homepage: https://www.x.org/

### libXi (1.8.2)

X Input extension library

- License: `MIT`
- Homepage: https://www.x.org/

### libXinerama (1.1.6)

X Xinerama extension library

- License: `MIT`
- Homepage: https://www.x.org/

### libXmu (1.3.1)

X miscellaneous utility library

- License: `MIT`
- Homepage: https://www.x.org/

### libXpm (3.5.18)

X Pixmap library

- License: `MIT`
- Homepage: https://www.x.org/

### libXpresent (1.0.2)

X Present extension library

- License: `MIT`
- Homepage: https://www.x.org/

### libXrandr (1.5.5)

X Resize and Rotate extension library

- License: `MIT`
- Homepage: https://www.x.org/

### libXrender (0.9.12)

X Render extension library

- License: `MIT`
- Homepage: https://www.x.org/

### libXres (1.2.3)

X11 Resource extension client library (XRes)

- License: `MIT`
- Homepage: https://www.x.org/

### libXt (1.3.1)

X Toolkit Intrinsics library

- License: `MIT`
- Homepage: https://www.x.org/

### libXtst (1.2.5)

X Test extension library

- License: `MIT`
- Homepage: https://www.x.org/

### libXv (1.0.13)

X Video extension library

- License: `MIT`
- Homepage: https://www.x.org/

### libXvMC (1.0.15)

X Video Motion Compensation library

- License: `MIT`
- Homepage: https://www.x.org/

### libXxf86dga (1.1.7)

X XFree86-DGA extension library

- License: `MIT`
- Homepage: https://www.x.org/

### libXxf86vm (1.1.7)

X XFree86 Video Mode extension library

- License: `MIT`
- Homepage: https://www.x.org/

### libadwaita1 (1.8.4)

GTK4 adaptive widgets library

- License: `LGPL-2.1-or-later`
- Homepage: https://gnome.pages.gitlab.gnome.org/libadwaita/

### libaom (3.13.1)

AV1 video codec reference implementation

- License: `BSD-2-Clause`
- Homepage: https://aomedia.googlesource.com/aom/

### libass (0.17.4)

Portable subtitle renderer (ASS/SSA format)

- License: `ISC`
- Homepage: https://github.com/libass/libass

### libavif (1.3.0)

AVIF image format library

- License: `BSD-2-Clause`

### libbluray (1.4.1)

Blu-ray disc playback library

- License: `LGPL-2.1-or-later`
- Homepage: https://www.videolan.org/developers/libbluray.html

### libcamera (0.7.2)

Camera support library for complex camera pipelines (MIPI/IPU built-in cameras)

- License: `LGPL-2.1-or-later AND GPL-2.0-or-later`
- Homepage: https://libcamera.org

### libcanberra (0.30)

XDG sound theme and event sounds library

- License: `LGPL-2.1-or-later`
- Homepage: http://0pointer.de/lennart/projects/libcanberra/

### libcbor (0.13.0)

Concise Binary Object Representation (CBOR) library — RFC 8949

- License: `MIT`
- Homepage: https://github.com/PJK/libcbor

### libcdio (2.1.0)

GNU Compact Disc Input and Control library

- License: `GPL-3.0-or-later`
- Homepage: https://savannah.gnu.org/projects/libcdio/

### libcdio-paranoia (10.2+2.0.2)

CD paranoia library from libcdio

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/libcdio/

### libclc (21.1.8)

OpenCL C library

- License: `Apache-2.0`

### libcloudproviders (0.3.6)

Cloud storage integration library

- License: `LGPL-3.0-or-later`

### libcupsfilters (2.1.1)

CUPS filters library

- License: `Apache-2.0`

### libdaemon (0.14)

Lightweight C library for writing UNIX daemons

- License: `LGPL-2.1-or-later`
- Homepage: https://0pointer.de/lennart/projects/libdaemon/

### libdbusmenu (16.04.0)

Library for passing GTK menus over D-Bus — provides the Dbusmenu GObject-Introspection typelibs for GNOME Shell tray/quicklist support

- License: `GPL-3.0-only AND LGPL-2.1-only AND LGPL-3.0-only`
- Homepage: https://launchpad.net/libdbusmenu

### libde265 (1.0.16)

Open source H.265/HEVC video decoder

- License: `LGPL-3.0-or-later`
- Homepage: https://github.com/strukturag/libde265

### libdecor (0.2.5)

Client-side window decorations library for Wayland

- License: `MIT`
- Homepage: https://gitlab.freedesktop.org/libdecor/libdecor

### libdex (1.1.0)

Library for deferred execution and async/await primitives — GNOME async helper, required by sysprof and other desktop-tier profiling/IO consumers

- License: `LGPL-2.1-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/libdex

### libdisplay-info (0.3.0)

EDID and DisplayID library

- License: `MIT`
- Homepage: https://gitlab.freedesktop.org/emersion/libdisplay-info

### libdmx (1.1.5)

X DMX extension library

- License: `MIT`
- Homepage: https://www.x.org/

### libdrm (2.4.131)

Direct Rendering Manager library

- License: `MIT`
- Homepage: https://dri.freedesktop.org/

### libdvdnav (7.0.0)

DVD navigation library

- License: `GPL-2.0-or-later`
- Homepage: https://www.videolan.org/developers/libdvdnav.html

### libdvdread (7.0.1)

DVD reading library

- License: `GPL-2.0-or-later`
- Homepage: https://code.videolan.org/videolan/libdvdread

### libei (1.5.0)

Emulated Input library

- License: `MIT`
- Homepage: https://gitlab.freedesktop.org/libinput/libei

### libepoxy (1.5.10)

OpenGL function pointer management library

- License: `MIT`
- Homepage: https://github.com/anholt/libepoxy

### libevdev (1.13.6)

Input event device wrapper library

- License: `MIT`
- Homepage: https://www.freedesktop.org/wiki/Software/libevdev/

### libevent (2.1.12)

Event notification library

- License: `BSD-3-Clause`
- Homepage: https://libevent.org/

### libexif (0.6.25)

EXIF metadata library

- License: `LGPL-2.1-or-later`

### libfido2 (1.17.0)

Yubico FIDO2 library — CTAP 2.x and U2F authenticator interface

- License: `BSD-2-Clause`
- Homepage: https://github.com/Yubico/libfido2

### libfontenc (1.1.9)

Font encoding library

- License: `MIT`

### libgee (0.20.8)

GObject-based collection library

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Projects/Libgee

### libglvnd (1.7.0)

GL Vendor-Neutral Dispatch library — runtime GLX/EGL/OpenGL/GLES vendor dispatcher

- License: `MIT`
- Homepage: https://gitlab.freedesktop.org/glvnd/libglvnd

### libgphoto2 (2.5.33)

Digital camera access library

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/gphoto/libgphoto2

### libgtop (2.41.3)

System monitoring library

- License: `GPL-2.0-or-later`

### libgudev (238)

GObject-based wrapper around libudev

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Projects/libgudev

### libgusb (0.4.9)

GObject wrapper for libusb

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/hughsie/libgusb

### libgweather (4.4.4)

GNOME weather information library

- License: `GPL-2.0-or-later`
- Homepage: https://wiki.gnome.org/Projects/LibGWeather

### libhandy1 (1.8.3)

GTK3 adaptive widget library

- License: `LGPL-2.1-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/libhandy

### libheif (1.21.2)

HEIF and AVIF file format decoder and encoder

- License: `LGPL-3.0-or-later`
- Homepage: https://github.com/strukturag/libheif

### libical (3.0.20)

iCalendar protocol implementation

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/libical/libical

### libimobiledevice (1.4.0)

Apple mobile device access library

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/libimobiledevice/libimobiledevice

### libimobiledevice-glue (1.3.2)

Common code for libimobiledevice libraries

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/libimobiledevice/libimobiledevice-glue

### libinput (1.31.0)

Input device management and event handling library

- License: `MIT`
- Homepage: https://www.freedesktop.org/wiki/Software/libinput/

### libjpeg-turbo (3.1.3)

High-speed JPEG compression/decompression library

- License: `IJG`
- Homepage: https://libjpeg-turbo.org/

### libjxl (0.11.2)

JPEG XL image format library

- License: `BSD-3-Clause`

### libmad (0.15.1b)

High-quality MPEG audio decoder (24-bit output)

- License: `GPL-2.0-or-later`
- Homepage: https://sourceforge.net/projects/mad/

### libmbim (1.34.0)

MBIM protocol library

- License: `LGPL-2.1-or-later`

### libmediaart (1.9.7)

Media art extraction and caching library

- License: `LGPL-2.1-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/libmediaart

### libmpeg2 (0.5.1)

Library for decoding MPEG-1 and MPEG-2 video streams

- License: `GPL-2.0-or-later`
- Homepage: https://libmpeg2.sourceforge.io/

### libmsgraph (0.3.4)

Microsoft Graph API client library

- License: `LGPL-3.0-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/msgraph

### libmtp (1.1.23)

MTP media device access library

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/libmtp/libmtp

### libnfs (6.0.2)

NFS client library

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/sahlberg/libnfs

### libnma (1.10.6)

NetworkManager GTK widgets

- License: `GPL-2.0-or-later`

### libnotify (0.8.8)

Desktop notification library

- License: `LGPL-2.1-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/libnotify

### libogg (1.3.6)

Ogg bitstream container library

- License: `BSD-3-Clause`
- Homepage: https://www.xiph.org/ogg/

### libpcap (1.10.6)

Packet capture library (full — with Bluetooth capture support)

- License: `BSD-3-Clause`

### libpciaccess (0.18.1)

PCI access library

- License: `MIT`
- Homepage: https://www.x.org/

### libplacebo (7.360.0)

GPU-accelerated image and video processing library

- License: `LGPL-2.1-or-later`
- Homepage: https://code.videolan.org/videolan/libplacebo

### libplist (2.7.0)

Apple property list library

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/libimobiledevice/libplist

### libpng (1.6.55)

PNG reference library

- License: `Libpng`
- Homepage: http://www.libpng.org/

### libportal (0.9.1)

Flatpak portal library

- License: `LGPL-3.0-or-later`
- Homepage: https://github.com/flatpak/libportal

### libppd (2.1.1)

PPD file handling library

- License: `Apache-2.0`

### libproxy (0.5.12)

Automatic proxy configuration management library

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/libproxy/libproxy

### libqmi (1.38.0)

QMI protocol library

- License: `LGPL-2.1-or-later`

### libqrencode (4.1.1)

QR code encoding library

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/fukuchi/libqrencode

### librest (0.10.2)

REST web service access library

- License: `LGPL-2.1-or-later`

### librsvg (2.61.4)

SVG rendering library

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Projects/LibRsvg

### libsamplerate (0.2.2)

Audio sample rate conversion library

- License: `BSD-2-Clause`
- Homepage: https://libsndfile.github.io/libsamplerate/

### libsecret (0.21.7)

Library for accessing secrets stored in the keyring

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Projects/Libsecret

### libshumate (1.5.3)

GTK4 map widget

- License: `LGPL-2.1-or-later`

### libsigcpp2 (2.12.1)

Type-safe callback system for C++ (sigc++-2.0, GTK3 compatibility)

- License: `LGPL-2.1-or-later`
- Homepage: https://libsigcplusplus.github.io/libsigcplusplus/

### libsigcpp3 (3.6.0)

Typesafe callback system for C++

- License: `LGPL-3.0-or-later`
- Homepage: https://libsigcplusplus.github.io/libsigcplusplus/

### libsndfile (1.2.2)

Library for reading and writing sound files

- License: `LGPL-2.1-or-later`
- Homepage: https://libsndfile.github.io/libsndfile/

### libsoup3 (3.6.6)

HTTP client/server library for GNOME

- License: `LGPL-2.0-or-later`
- Homepage: https://wiki.gnome.org/Projects/libsoup

### libspelling (0.4.10)

Spellcheck library for GTK 4

- License: `LGPL-2.1-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/libspelling

### libtatsu (1.0.5)

Apple Tatsu Signing Stuff (TSS) library

- License: `LGPL-2.1-or-later`
- Homepage: https://libimobiledevice.org/

### libtiff (4.7.1)

TIFF image library

- License: `libtiff`
- Homepage: http://www.libtiff.org/

### libtiff-pass2 (4.7.1)

libtiff (pass 2 — rebuild with libwebp support)

- License: `libtiff`
- Homepage: http://www.libtiff.org/

### libunwind (1.8.3)

Portable and efficient C programming interface to determine the call-chain of a program

- License: `MIT`
- Homepage: https://www.nongnu.org/libunwind/

### libusbmuxd (2.1.1)

USB multiplexing daemon client library

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/libimobiledevice/libusbmuxd

### libva (2.23.0)

Video Acceleration API

- License: `MIT`
- Homepage: https://github.com/intel/libva

### libvorbis (1.3.7)

Vorbis audio codec library

- License: `BSD-3-Clause`
- Homepage: https://www.xiph.org/vorbis/

### libvpx (1.16.0)

VP8/VP9 video codec

- License: `BSD-3-Clause`
- Homepage: https://www.webmproject.org/

### libwacom (2.18.0)

Wacom tablet information library

- License: `MIT`

### libwebp (1.6.0)

WebP image format library

- License: `BSD-3-Clause`
- Homepage: https://developers.google.com/speed/webp/

### libxcb (1.17.0)

X C Binding library

- License: `MIT`
- Homepage: https://xcb.freedesktop.org/

### libxcvt (0.1.3)

VESA CVT standard timing modelines generator

- License: `MIT`
- Homepage: https://www.x.org/

### libxkbcommon (1.13.1)

Keyboard handling library

- License: `MIT`
- Homepage: https://xkbcommon.org/

### libxkbfile (1.2.0)

XKB file handling library

- License: `MIT`
- Homepage: https://www.x.org/

### libxml2-pass2 (2.15.1)

XML parsing library (pass 2 — rebuild with doxygen-generated Python bindings)

- License: `MIT`
- Homepage: https://gitlab.gnome.org/GNOME/libxml2

### libxmlb (0.3.25)

Library for querying compressed XML metadata

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/hughsie/libxmlb

### libxshmfence (1.3.3)

X shared memory fence library

- License: `MIT`
- Homepage: https://www.x.org/

### lilv (0.26.4)

LV2 plugin host library — discovers, loads, and runs LV2 plugins

- License: `ISC`
- Homepage: https://gitlab.com/lv2/lilv

### links (2.30)

Text and graphics mode web browser

- License: `GPL-2.0-or-later`
- Homepage: http://links.twibright.com/

### localsearch (3.10.2)

Filesystem indexer and metadata extractor

- License: `GPL-2.0-or-later`
- Homepage: https://gnome.pages.gitlab.gnome.org/localsearch/

### loupe (49.2)

GNOME image viewer

- License: `GPL-3.0-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/loupe

### lv2 (1.18.10)

LV2 audio plugin specification — headers, bundles, and example plugins

- License: `ISC`
- Homepage: https://lv2plug.in/

### lynx (2.9.2)

Text-mode web browser

- License: `GPL-2.0-or-later`

### macos-cursor-theme (2.0.1)

macOS XCursor port — Apple-style cursor theme (macOS, the dark variant of ful1e5/apple_cursor)

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/ful1e5/apple_cursor

### markdown (3.10.2)

Python Markdown implementation

- License: `BSD-3-Clause`

### mesa (25.3.5)

OpenGL, Vulkan, and OpenCL implementation

- License: `MIT`
- Homepage: https://www.mesa3d.org/

### mkfontscale (1.2.3)

Create index of scalable font files

- License: `MIT`

### modemmanager (1.24.2)

Mobile broadband modem management daemon

- License: `GPL-2.0-or-later`

### mpg123 (1.33.4)

MPEG audio decoder

- License: `LGPL-2.1-or-later`

### mtdev (1.1.7)

Multitouch protocol translation library

- License: `MIT`
- Homepage: https://bitmath.org/code/mtdev/

### mupdf (1.26.12)

Lightweight PDF and XPS viewer

- License: `AGPL-3.0-or-later`

### mutter (49.4)

GNOME window manager and Wayland compositor

- License: `GPL-2.0-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/mutter

### nautilus (49.3)

GNOME file manager

- License: `GPL-3.0-or-later`
- Homepage: https://wiki.gnome.org/Apps/Files

### networkmanager (1.56.0)

Network connection manager

- License: `GPL-2.0-or-later`
- Homepage: https://networkmanager.dev/

### nordic-theme (2.2.0)

Nordic theme — cool Nord-palette GTK and gnome-shell theme by EliverLara

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/EliverLara/Nordic

### nss-mdns (0.15.1)

NSS plugin for mDNS hostname resolution (Avahi)

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/avahi/nss-mdns

### nv-codec-headers (13.0.19.0)

FFmpeg version of the NVIDIA codec API headers (ffnvcodec) — build-time only

- License: `MIT`
- Homepage: https://github.com/FFmpeg/nv-codec-headers

### open-vm-tools (13.1.0)

Open-source VMware guest tools (vmtoolsd) for VMware virtual-machine guests

- License: `GPL-2.0-only AND LGPL-2.1-only AND BSD-3-Clause`
- Homepage: https://github.com/vmware/open-vm-tools

### openjpeg2 (2.5.4)

JPEG 2000 codec library

- License: `BSD-2-Clause`

### opus (1.6.1)

Interactive speech and audio codec

- License: `BSD-3-Clause`
- Homepage: https://opus-codec.org/

### orc (0.4.42)

Oil Runtime Compiler — JIT compiler for SIMD multimedia operations

- License: `BSD-2-Clause`
- Homepage: https://gstreamer.freedesktop.org/modules/orc.html

### orchis-theme (2025-04-25)

Orchis GTK + gnome-shell theme — Material-Design-inspired (Dark + Light + compact variants); supplies the shell themes referenced by 4 welcomer combos

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/vinceliuice/Orchis-theme

### pango (1.57.0)

Text layout and rendering library

- License: `LGPL-2.0-or-later`
- Homepage: https://pango.gnome.org/

### pangomm (2.56.1)

C++ bindings for Pango

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gtkmm.org/

### papirus-icon-theme (20250501)

Papirus icon theme — clean, flat icon set with light + dark + default variants

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/PapirusDevelopmentTeam/papirus-icon-theme

### perl-parse-yapp (1.21)

Perl parser generator

- License: `Artistic-1.0`

### phinger-cursors (2.1)

phinger cursors — large rounded cursor theme with light + dark + left-handed variants

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/phisch/phinger-cursors

### pinentry (1.3.2)

PIN/passphrase entry dialog

- License: `GPL-2.0-or-later`
- Homepage: https://www.gnupg.org/

### pipewire (1.6.0)

Multimedia processing framework

- License: `MIT`
- Homepage: https://pipewire.org/

### pixman (0.46.4)

Pixel manipulation library

- License: `MIT`
- Homepage: https://www.cairographics.org/

### plutosvg (0.0.8)

Tiny SVG rendering library in C on plutovg — color-emoji (SVG glyph) backend for SDL3_ttf

- License: `MIT`
- Homepage: https://github.com/sammycage/plutosvg

### plutovg (1.3.3)

Tiny 2D vector graphics library in C — rendering backend for plutosvg

- License: `MIT`
- Homepage: https://github.com/sammycage/plutovg

### poppler (26.02.0)

PDF rendering library

- License: `GPL-2.0-or-later`

### poppler-data (0.4.12)

Encoding data for poppler PDF rendering (CJK and Cyrillic)

- License: `BSD-3-Clause AND GPL-2.0-or-later`
- Homepage: https://poppler.freedesktop.org/

### power-profiles-daemon (0.30)

System-wide power profile management

- License: `GPL-3.0-or-later`
- Homepage: https://gitlab.freedesktop.org/upower/power-profiles-daemon

### protobuf-c (1.5.2)

C implementation of Protocol Buffers

- License: `BSD-2-Clause`

### pulseaudio (17.0)

PulseAudio sound server

- License: `LGPL-2.1-or-later`
- Homepage: https://www.freedesktop.org/wiki/Software/PulseAudio/

### pycairo (1.29.0)

Python bindings for Cairo

- License: `LGPL-2.1-or-later`
- Homepage: https://pycairo.readthedocs.io/

### pygobject3 (3.54.5)

Python GObject bindings

- License: `LGPL-2.1-or-later`
- Homepage: https://pygobject.gnome.org/

### qpdf (12.3.2)

PDF transformation utility

- License: `Apache-2.0`

### raptor2 (2.0.16)

RDF parser and serializer library

- License: `LGPL-2.1-or-later OR Apache-2.0`
- Homepage: https://librdf.org/raptor/

### rasqal (0.9.33)

RDF query language library (SPARQL)

- License: `LGPL-2.1-or-later`
- Homepage: https://librdf.org/rasqal/

### redland (1.0.17)

RDF metadata library

- License: `LGPL-2.1-or-later`
- Homepage: https://librdf.org/

### rtkit (0.13)

RealtimeKit — D-Bus service for real-time scheduling

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/heftig/rtkit

### samba (4.23.5)

SMB/CIFS file and print server

- License: `GPL-3.0-or-later`
- Homepage: https://www.samba.org/

### sassc (3.6.2)

SASS CSS preprocessor compiler

- License: `MIT`
- Homepage: https://github.com/sass/sassc

### sbc (2.2)

Bluetooth SBC audio codec

- License: `LGPL-2.1-or-later`
- Homepage: https://www.kernel.org/pub/linux/bluetooth/

### sdl2 (2.32.6)

Simple DirectMedia Layer 2

- License: `Zlib`
- Homepage: https://www.libsdl.org/

### sdl2-ttf (2.24.0)

TrueType font rendering library for SDL2

- License: `Zlib`
- Homepage: https://www.libsdl.org/

### sdl3 (3.4.12)

Simple DirectMedia Layer 3 — cross-platform media, input and GPU library

- License: `Zlib`
- Homepage: https://www.libsdl.org/

### sdl3-ttf (3.2.2)

TrueType font rendering library for SDL3 — harfbuzz shaping + plutosvg color-emoji, strict fail-closed dependency resolution

- License: `Zlib`
- Homepage: https://www.libsdl.org/

### seahorse (47.0.1)

GNOME password and encryption key manager

- License: `GPL-2.0-or-later`
- Homepage: https://wiki.gnome.org/Apps/Seahorse

### seatd (0.9.3)

Minimal seat management daemon and libseat session library

- License: `MIT`
- Homepage: https://git.sr.ht/~kennylevinsen/seatd

### serd (0.32.8)

Lightweight C library for RDF syntax (Turtle/NTriples/NQuads/TriG) — drobilla

- License: `ISC`
- Homepage: https://drobilla.net/software/serd/

### sessreg (1.1.4)

Manage utmpx/wtmpx entries for non-init sessions

- License: `MIT`

### setxkbmap (1.3.4)

Set the keyboard using the X Keyboard Extension

- License: `MIT`

### shaderc (2026.1)

Google GLSL/HLSL to SPIR-V shader compiler

- License: `Apache-2.0`
- Homepage: https://github.com/google/shaderc

### shared-mime-info (2.4)

Core MIME type database

- License: `GPL-2.0-or-later`
- Homepage: https://www.freedesktop.org/wiki/Software/shared-mime-info/

### slang (2.3.3)

S-Lang programming library

- License: `GPL-2.0-or-later`

### smproxy (1.0.8)

Session Manager Proxy

- License: `MIT`

### snapshot (49.1)

GNOME camera application

- License: `GPL-3.0-or-later`
- Homepage: https://apps.gnome.org/Snapshot/

### sord (0.16.22)

In-memory RDF store with triple-pattern lookup (drobilla)

- License: `ISC`
- Homepage: https://drobilla.net/software/sord/

### sound-theme-freedesktop (0.8)

Default XDG sound theme

- License: `CC-BY-SA-3.0`
- Homepage: https://www.freedesktop.org/wiki/Specifications/sound-theme-spec/

### soundtouch (2.4.0)

Audio tempo/pitch processing library

- License: `LGPL-2.1-or-later`
- Homepage: https://www.surina.net/soundtouch/

### speex (1.2.1)

Audio codec designed for speech compression

- License: `BSD-3-Clause`
- Homepage: https://www.speex.org/

### spidermonkey (140.8.0)

Mozilla SpiderMonkey JavaScript engine

- License: `MPL-2.0`
- Homepage: https://spidermonkey.dev/

### spirv-headers (1.4.341.0)

SPIR-V headers

- License: `MIT`

### spirv-llvm-translator (21.1.4)

SPIR-V to LLVM IR translator

- License: `Apache-2.0`

### spirv-tools (1.4.341.0)

SPIR-V tools

- License: `Apache-2.0`

### sratom (0.6.22)

Library for serialising LV2 atoms to/from RDF (drobilla)

- License: `ISC`
- Homepage: https://drobilla.net/software/sratom/

### startup-notification (0.12)

Startup notification protocol library

- License: `LGPL-2.0-or-later`
- Homepage: https://www.freedesktop.org/wiki/Software/startup-notification/

### suil (0.10.26)

LV2 plugin UI loader — wraps native plugin GUIs for host toolkits

- License: `ISC`
- Homepage: https://gitlab.com/lv2/suil

### svt-av1 (4.0.1)

SVT-based AV1 encoder

- License: `BSD-2-Clause`
- Homepage: https://gitlab.com/AOMediaCodec/SVT-AV1

### swh-plugins (0.4.17)

Steve Harris LADSPA plugin collection (~100 plugins — delay, reverb, EQ, distortion, etc.)

- License: `GPL-2.0-or-later`
- Homepage: http://plugin.org.uk/

### sysprof (46.0)

System-wide profiler for Linux — capture library for GTK/GNOME profiling support

- License: `GPL-3.0-or-later`
- Homepage: https://www.sysprof.com/

### systemd-pass2 (259.1)

Systemd (pass 2 — rebuild with PAM support for GNOME/GDM)

- License: `LGPL-2.1-or-later`
- Homepage: https://www.freedesktop.org/wiki/Software/systemd/

### taglib (2.2)

Library for reading and editing audio file metadata tags

- License: `LGPL-2.1-or-later AND MPL-1.1`
- Homepage: https://taglib.org/

### tdb (1.4.15)

Trivial Database — small, fast, simple key/value store from the Samba project

- License: `LGPL-3.0-or-later`
- Homepage: https://tdb.samba.org/

### tecla (49.0)

GNOME on-screen keyboard

- License: `GPL-2.0-or-later`

### tela-icon-theme (2025-02-10)

Tela icon theme — flat colorful icon set with default + light + dark variants

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/vinceliuice/Tela-icon-theme

### tinysparql (3.10.1)

RDF graph database and SPARQL query engine

- License: `GPL-2.0-or-later`

### totem-pl-parser (3.26.6)

Playlist parser library

- License: `LGPL-2.0-or-later`

### tpm2-tss (4.1.3)

TCG TPM2 Software Stack — userspace libraries for TPM 2.0 hardware

- License: `BSD-2-Clause`
- Homepage: https://github.com/tpm2-software/tpm2-tss

### twolame (0.4.0)

Optimised MPEG Audio Layer 2 (MP2) encoder

- License: `LGPL-2.0-or-later`
- Homepage: https://www.twolame.org/

### uchardet (0.0.8)

Character encoding detection library

- License: `MPL-1.1`
- Homepage: https://www.freedesktop.org/wiki/Software/uchardet/

### udisks2 (2.11.1)

Disk management D-Bus service

- License: `GPL-2.0-or-later`
- Homepage: https://github.com/storaged-project/udisks

### upower (1.91.1)

Power management service

- License: `GPL-2.0-or-later`
- Homepage: https://upower.freedesktop.org/

### user-theme (1.0)

GNOME Shell user-theme extension (load shell themes from /usr/share/themes/) — needed for InterGenOS shell theme selection

- License: `GPL-2.0-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/gnome-shell-extensions

### utfcpp (4.0.9)

UTF-8 with C++ — header-only library for handling UTF-8 strings

- License: `BSL-1.0`
- Homepage: https://github.com/nemtrif/utfcpp

### v4l-utils (1.32.0)

Video4Linux utilities and libraries (v4l2-ctl, media-ctl, libv4l)

- License: `LGPL-2.1-or-later AND GPL-2.0-or-later`
- Homepage: https://linuxtv.org/wiki/index.php/V4l-utils

### vala (0.56.18)

Vala programming language compiler

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Projects/Vala

### vorbis-tools (1.4.3)

Command-line tools (oggenc, oggdec, ogginfo, etc.) for the Vorbis audio codec

- License: `GPL-2.0-or-later`
- Homepage: https://xiph.org/vorbis/

### vte (0.82.3)

Virtual Terminal Emulator widget

- License: `LGPL-2.1-or-later`
- Homepage: https://wiki.gnome.org/Apps/Terminal/VTE

### vulkan-headers (1.4.341.0)

Vulkan API headers

- License: `Apache-2.0`

### vulkan-loader (1.4.341.0)

Vulkan ICD loader

- License: `Apache-2.0`

### wavpack (5.9.0)

Hybrid lossless/lossy audio compression library and CLI tools

- License: `BSD-3-Clause`
- Homepage: https://www.wavpack.com/

### wayland (1.24.0)

Wayland display server protocol

- License: `MIT`
- Homepage: https://wayland.freedesktop.org/

### wayland-protocols (1.47)

Wayland protocol extensions

- License: `MIT`
- Homepage: https://wayland.freedesktop.org/

### webkitgtk (2.50.5)

Web content engine for GTK

- License: `LGPL-2.0-or-later`
- Homepage: https://webkitgtk.org/

### webkitgtk-gtk3 (2.50.5)

Web content engine for GTK (GTK-3 version)

- License: `LGPL-2.0-or-later`
- Homepage: https://webkitgtk.org/

### webrtc-audio-processing (2.1)

WebRTC audio processing library (acoustic echo cancellation, noise suppression)

- License: `BSD-3-Clause`
- Homepage: https://gitlab.freedesktop.org/pulseaudio/webrtc-audio-processing

### whitesur-gtk-theme (2025-07-24)

WhiteSur GTK theme — macOS Big Sur-inspired GTK theme (default + light + dark variants)

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/vinceliuice/WhiteSur-gtk-theme

### whitesur-icon-theme (2025-12-27)

WhiteSur icon theme — macOS-inspired icon set with default + light + dark variants

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/vinceliuice/WhiteSur-icon-theme

### wireless-regdb (2026.02.04)

Wireless regulatory database for WiFi compliance

- License: `ISC`
- Homepage: https://www.kernel.org/pub/software/network/wireless-regdb/

### wireplumber (0.5.13)

PipeWire session manager

- License: `MIT`
- Homepage: https://pipewire.pages.freedesktop.org/wireplumber/

### woff2 (1.0.2)

Web Open Font Format 2.0 library

- License: `MIT`

### x264 (20250815)

H.264/AVC video encoder

- License: `GPL-2.0-or-later`
- Homepage: https://www.videolan.org/developers/x264.html

### x265 (4.1)

H.265/HEVC video encoder

- License: `GPL-2.0-or-later`
- Homepage: https://www.x265.org/

### xauth (1.1.5)

X authority file utility

- License: `MIT`

### xbitmaps (1.1.3)

X11 bitmap files

- License: `MIT`

### xcb-proto (1.17.0)

XCB protocol descriptions

- License: `MIT`
- Homepage: https://xcb.freedesktop.org/

### xcb-util (0.4.1)

XCB utility library

- License: `MIT`
- Homepage: https://xcb.freedesktop.org/

### xcb-util-cursor (0.1.6)

XCB cursor library

- License: `MIT`
- Homepage: https://xcb.freedesktop.org/

### xcb-util-image (0.4.1)

XCB image convenience library

- License: `MIT`
- Homepage: https://xcb.freedesktop.org/

### xcb-util-keysyms (0.4.1)

XCB keysym convenience library

- License: `MIT`
- Homepage: https://xcb.freedesktop.org/

### xcb-util-renderutil (0.3.10)

XCB render utility library

- License: `MIT`
- Homepage: https://xcb.freedesktop.org/

### xcb-util-wm (0.4.2)

XCB window manager utilities

- License: `MIT`
- Homepage: https://xcb.freedesktop.org/

### xcursor-themes (1.0.7)

Default X cursor themes

- License: `MIT`

### xcursorgen (1.0.9)

X cursor file generator

- License: `MIT`
- Homepage: https://www.x.org/

### xdg-dbus-proxy (0.1.6)

D-Bus proxy for sandboxed applications

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/flatpak/xdg-dbus-proxy

### xdg-desktop-portal (1.20.3)

Desktop integration portal

- License: `LGPL-2.1-or-later`

### xdg-desktop-portal-gnome (49.0)

GNOME backend for xdg-desktop-portal

- License: `LGPL-2.1-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome

### xdg-desktop-portal-gtk (1.15.3)

GTK backend for xdg-desktop-portal

- License: `LGPL-2.1-or-later`

### xdg-user-dirs (0.19)

XDG user directory management

- License: `GPL-2.0-or-later`
- Homepage: https://www.freedesktop.org/wiki/Software/xdg-user-dirs/

### xdg-utils (1.2.1)

Desktop integration utilities

- License: `MIT`

### xdpyinfo (1.4.0)

Display information utility for X

- License: `MIT`

### xdriinfo (1.0.8)

DRI information utility

- License: `MIT`

### xev (1.2.6)

X event monitor

- License: `MIT`

### xhost (1.0.10)

Server access control program

- License: `MIT`

### xinput (1.6.4)

Utility to configure and test X input devices

- License: `MIT`

### xkbcomp (1.5.0)

XKB keyboard description compiler

- License: `MIT`

### xkeyboard-config (2.46)

X Keyboard Configuration Database

- License: `MIT`
- Homepage: https://www.freedesktop.org/wiki/Software/XKeyboardConfig/

### xmodmap (1.0.11)

Keyboard modifier utility

- License: `MIT`

### xprop (1.2.8)

Property displayer for X

- License: `MIT`

### xrandr (1.5.3)

Primitive command line interface to RandR extension

- License: `MIT`

### xrdb (1.2.2)

X server resource database utility

- License: `MIT`

### xset (1.2.5)

User preference utility for X

- License: `MIT`

### xtrans (1.6.0)

X transport library

- License: `MIT`
- Homepage: https://www.x.org/

### xwayland (24.1.9)

X server running as a Wayland client

- License: `MIT`
- Homepage: https://gitlab.freedesktop.org/xorg/xserver

### xwininfo (1.1.6)

Window information utility for X

- License: `MIT`

### yelp-xsl (49.0)

GNOME help stylesheets

- License: `GPL-2.0-or-later`

### zenity (4.2.2)

GTK dialog boxes for shell scripts — InterGen's GUI consent modal

- License: `LGPL-2.1-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/zenity

### zix (0.8.0)

Lightweight C library of portability wrappers and data structures (drobilla)

- License: `ISC`
- Homepage: https://gitlab.com/drobilla/zix/


### Tier: `extra`

### R (4.6.1)

R environment for statistical computing and graphics

- License: `GPL-2.0-or-later`
- Homepage: https://www.r-project.org/

### aardvark-dns (1.17.1)

Authoritative DNS server for A/AAAA container records

- License: `Apache-2.0`
- Homepage: https://github.com/containers/aardvark-dns

### acpica (20260408)

ACPI Component Architecture utilities including the iasl compiler

- License: `GPL-2.0-only OR BSD-3-Clause`
- Homepage: https://github.com/acpica/acpica

### amdgpu (1.0.0)

AMD Radeon graphics meta-package — userland tools + diagnostics + VDPAU translation

- License: `GPL-3.0-or-later`
- Homepage: https://www.x.org/wiki/RadeonFeature/

### amdgpu_top (0.11.5)

TUI activity dashboard for AMD GPUs (VRAM, clocks, fan, decoders, ring buffers)

- License: `MIT`
- Homepage: https://github.com/Umio-Yasuno/amdgpu_top

### apache-httpd (2.4.67)

Apache HTTP Server 2.4.x — heritage HTTP/HTTPS server with mpm_event + DSO modules + default-secure config

- License: `Apache-2.0`
- Homepage: https://httpd.apache.org/

### appstream-glib (0.8.3)

AppStream metadata reading and writing library

- License: `LGPL-2.1-or-later`
- Homepage: https://people.freedesktop.org/~hughsient/appstream-glib/

### apr (1.7.6)

Apache Portable Runtime — OS-abstraction library used by apache-httpd 2.4.x

- License: `Apache-2.0`
- Homepage: https://apr.apache.org/

### apr-util (1.6.3)

APR Utilities — DBM/LDAP/XML/crypto helpers layered on top of apr; required by apache-httpd 2.4.x

- License: `Apache-2.0`
- Homepage: https://apr.apache.org/

### audacity (3.7.7)

Multi-track audio editor and recorder (wxWidgets GUI, source-built, no telemetry)

- License: `GPL-3.0-or-later`
- Homepage: https://www.audacityteam.org/

### bottom (0.12.3)

Yet another cross-platform graphical process/system monitor

- License: `MIT`
- Homepage: https://github.com/ClementTsang/bottom

### brave (1.0)

Helper to download and install Brave Browser

- License: `GPL-3.0-or-later`
- Payload license: `LicenseRef-Brave-EULA`
- Homepage: https://brave.com/

### cabextract (1.11)

Extractor for Microsoft Cabinet (.cab) archives

- License: `GPL-3.0-or-later`
- Homepage: https://www.cabextract.org.uk/

### caddy (2.11.3)

Modern ACME-aware HTTP/2/3 single-binary web server

- License: `Apache-2.0`
- Homepage: https://caddyserver.com

### cairocffi (1.7.1)

cffi-based Python bindings for the cairo graphics library (icon-toolchain dep)

- License: `BSD-3-Clause`
- Homepage: https://github.com/Kozea/cairocffi

### cairomm1 (1.14.5)

C++ bindings for Cairo graphics library (GTK3 API version 1.0)

- License: `LGPL-2.0-or-later`
- Homepage: https://www.cairographics.org/cairomm/

### cairosvg (2.9.0)

SVG to PNG/PDF/PS converter based on cairo (icon-generation toolchain)

- License: `LGPL-3.0-or-later`
- Homepage: https://cairosvg.org

### catatonit (0.2.1)

Container init that is so simple it is effectively brain-dead

- License: `GPL-2.0-or-later`
- Homepage: https://github.com/openSUSE/catatonit

### celluloid (0.27)

GTK4 frontend for mpv media player

- License: `GPL-3.0-or-later`
- Homepage: https://celluloid-player.github.io/

### chrome (1.0)

Helper to download and install Google Chrome

- License: `GPL-3.0-or-later`
- Payload license: `LicenseRef-Google-Chrome-ToS`
- Homepage: https://www.google.com/chrome/

### claude-code (1.0)

Helper to install Anthropic Claude Code CLI and VS Code extension

- License: `GPL-3.0-or-later`
- Payload license: `LicenseRef-Anthropic-Commercial-Terms`
- Homepage: https://code.claude.com/

### clucene (2.3.3.4)

C++ port of Lucene high performance text search engine

- License: `LGPL-2.1-or-later OR Apache-2.0`
- Homepage: https://clucene.sourceforge.net/

### composer (2.10.2)

Dependency manager for PHP

- License: `MIT`
- Homepage: https://getcomposer.org/

### compute (1.0.0)

Compute meta-package — the full InterGenOS ROCm GPU platform (runtime, math and ML libraries, compilers, profilers, debuggers) in one install step

- License: `GPL-3.0-or-later`
- Homepage: https://www.intergenos.org/

### conmon (2.2.1)

OCI container runtime monitor

- License: `Apache-2.0`
- Homepage: https://github.com/containers/conmon

### containerd (2.2.3)

Industry-standard container runtime daemon — OCI image + container lifecycle

- License: `Apache-2.0`
- Homepage: https://containerd.io/

### containers-common (0.64.1)

Common configuration files for container tools (Podman, Buildah, Skopeo)

- License: `Apache-2.0`
- Homepage: https://github.com/containers/common

### cppunit (1.15.1)

C++ unit testing framework

- License: `LGPL-2.1-or-later`
- Homepage: https://freedesktop.org/wiki/Software/cppunit/

### crun (1.27.1)

OCI container runtime written in C

- License: `GPL-2.0-or-later`
- Homepage: https://github.com/containers/crun

### cssselect2 (0.9.0)

CSS selector matching for Python ElementTree (icon-toolchain dep)

- License: `BSD-3-Clause`
- Homepage: https://github.com/Kozea/cssselect2

### defusedxml (0.7.1)

XML bomb protection for Python stdlib parsers (icon-toolchain dep)

- License: `PSF-2.0`
- Homepage: https://github.com/tiran/defusedxml

### discord (1.0)

Helper to download and install Discord

- License: `GPL-3.0-or-later`
- Payload license: `LicenseRef-Discord-ToS`
- Homepage: https://discord.com/

### dmidecode (3.7)

Report system hardware information from the BIOS (SMBIOS/DMI data)

- License: `GPL-2.0-or-later`
- Homepage: https://www.nongnu.org/dmidecode/

### dnsmasq (2.93)

Lightweight DNS forwarder and DHCP server

- License: `GPL-2.0-only OR GPL-3.0-only`
- Homepage: https://thekelleys.org.uk/dnsmasq/doc.html

### docker (29.5.2)

Pack, ship, and run any application as a lightweight container — daemon + CLI

- License: `Apache-2.0`
- Homepage: https://www.docker.com/

### dust (0.9.0)

A more intuitive version of du in rust

- License: `Apache-2.0`
- Homepage: https://github.com/bootandy/dust

### dxvk (3.0)

Vulkan-based D3D8/9/10/11 implementation for wine, both PE widths

- License: `Zlib`
- Homepage: https://github.com/doitsujin/dxvk

### edge (1.0)

Helper to download and install Microsoft Edge

- License: `GPL-3.0-or-later`
- Payload license: `LicenseRef-Microsoft-Edge-EULA`
- Homepage: https://www.microsoft.com/edge

### edk2-ovmf (202605)

OVMF UEFI firmware for QEMU guests with Secure Boot and TPM 2.0 support

- License: `BSD-2-Clause-Patent`
- Homepage: https://github.com/tianocore/edk2

### elfutils-libdw (0.194)

DWARF introspection library (libdw) from elfutils

- License: `GPL-2.0-or-later`
- Homepage: https://sourceware.org/elfutils/

### erlang (29.0.3)

Erlang/OTP concurrent programming language and runtime

- License: `Apache-2.0`
- Homepage: https://www.erlang.org/

### etcd (3.6.11)

Distributed reliable key-value store for cluster coordination

- License: `Apache-2.0`
- Homepage: https://etcd.io

### eza (0.23.4)

A modern, maintained replacement for ls

- License: `EUPL-1.2`
- Homepage: https://github.com/eza-community/eza

### ffmpeg-nonfree (1.0)

Opt-in helper to install ffmpeg with patent-encumbered nonfree codecs (FDK-AAC) to /opt/ffmpeg-nonfree/

- License: `GPL-3.0-or-later`
- Payload license: `GPL-3.0-or-later AND FDK-AAC`
- Homepage: https://ffmpeg.org/

### filelock (3.31.2)

Platform-independent file lock

- License: `MIT`
- Homepage: https://github.com/tox-dev/filelock

### firefox (140.9.0esr)

Mozilla Firefox ESR web browser -- telemetry-OFF posture via canonical Mozilla policies.json + 19 about:config Preferences locks (toolkit.telemetry.* + datareporting.* + app.normandy.* + app.shield.* per arkenfox canonical set); enforced at runtime by the shipped /usr/lib/firefox/distribution/policies.json (Locked, so the browser's own settings interface cannot re-enable them; root can edit the policy file). Matches docs/users/desktop-experience.md section 7 telemetry-OFF mandate.

- License: `MPL-2.0`
- Homepage: https://www.mozilla.org/firefox/

### fuse-overlayfs (1.16)

Overlay filesystem in userspace

- License: `GPL-2.0-or-later`
- Homepage: https://github.com/containers/fuse-overlayfs

### gamemode (1.8.2)

Feral Interactive daemon that applies game-optimised system settings on demand

- License: `BSD-3-Clause`
- Homepage: https://github.com/FeralInteractive/gamemode

### gamescope (3.16.24)

SteamOS session compositing window manager (micro-compositor for gaming)

- License: `BSD-2-Clause`
- Homepage: https://github.com/ValveSoftware/gamescope

### gaming (1.0.0)

Gaming meta-package — Steam, Wine, DXVK, VKD3D-Proton, GE-Proton + the complete 32-bit runtime (on NVIDIA, measured — NVK's 32-bit path does not reach the dGPU, so 32-bit titles render on the iGPU)

- License: `GPL-3.0-or-later`
- Homepage: https://www.intergenos.org/

### gc (8.2.12)

Boehm-Demers-Weiser conservative garbage collector

- License: `Boehm-GC`
- Homepage: https://www.hboehm.info/gc/

### ge-proton (11.1)

Helper to download and install GE-Proton (GloriousEggroll) system-wide

- License: `GPL-3.0-or-later`
- Payload license: `LicenseRef-GE-Proton-Mixed`
- Homepage: https://github.com/GloriousEggroll/proton-ge-custom

### gflags (2.3.0)

Google command-line flags library (gflags) — header-rich C++ flag parsing with sub-command + validators

- License: `BSD-3-Clause`
- Homepage: https://gflags.github.io/gflags/

### gh (2.95.0)

GitHub's official command-line tool

- License: `MIT`
- Homepage: https://github.com/cli/cli

### ghc (9.14.1)

The Glasgow Haskell Compiler

- License: `BSD-3-Clause`
- Homepage: https://www.haskell.org/ghc/

### gimp (3.2.4)

GNU Image Manipulation Program — advanced image editor

- License: `GPL-3.0-or-later`
- Homepage: https://www.gimp.org/

### go-md2man (2.0.7)

Convert Markdown to man pages (roff)

- License: `MIT`
- Homepage: https://github.com/cpuguy83/go-md2man

### gopls (0.21.1)

The Go language server

- License: `BSD-3-Clause`
- Homepage: https://pkg.go.dev/golang.org/x/tools/gopls

### gparted (1.8.1)

GNOME Partition Editor for graphically managing disk partitions

- License: `GPL-2.0-or-later`
- Homepage: https://gparted.org

### grex (1.4.5)

A command-line tool for generating regular expressions from user-provided test cases

- License: `Apache-2.0`
- Homepage: https://github.com/pemistahl/grex

### gsl (2.8)

GNU Scientific Library — numerical library for C and C++

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/gsl/

### gtkmm3 (3.24.10)

C++ interface to GTK3

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gtkmm.org/

### handbrake (1.11.2)

Video transcoder with a GTK front-end and a command-line interface

- License: `GPL-2.0-only`
- Homepage: https://handbrake.fr/

### haproxy (3.2.19)

Reliable, high-performance TCP/HTTP load balancer and proxy (LTS 3.2; default-secure config — loopback bind, stats locked to 127.0.0.1, no admin socket exposed)

- License: `GPL-2.0-or-later AND LGPL-2.1-only`
- Homepage: https://www.haproxy.org/

### hugo (0.125.4)

The world's fastest framework for building websites

- License: `Apache-2.0`
- Homepage: https://gohugo.io

### hyperfine (1.18.0)

A command-line benchmarking tool

- License: `MIT OR Apache-2.0`
- Homepage: https://github.com/sharkdp/hyperfine

### influxdb (3.9.0)

Time-series database (Core edition, Apache-2.0 OR MIT)

- License: `Apache-2.0 OR MIT`
- Homepage: https://www.influxdata.com

### inkscape (1.4.3)

Vector graphics editor with SVG support

- License: `GPL-2.0-or-later`
- Homepage: https://inkscape.org/

### jemalloc (5.3.1)

High-performance, fragmentation-resilient malloc implementation (Jason Evans / Facebook)

- License: `BSD-2-Clause`
- Homepage: https://jemalloc.net/

### julia (1.12.6)

The Julia programming language for technical computing

- License: `MIT`
- Homepage: https://julialang.org/

### just (1.26.0)

Just a command runner

- License: `CC0-1.0`
- Homepage: https://github.com/casey/just

### lazygit (0.41.0)

Simple terminal UI for git commands

- License: `MIT`
- Homepage: https://github.com/jesseduffield/lazygit

### lego (5.0.2)

Go-based ACME client (Let's Encrypt + RFC 8555) — single static binary CLI; Wave W1 web-server-wave cert companion

- License: `MIT`
- Homepage: https://go-acme.github.io/lego/

### leveldb (1.23)

Google LevelDB — fast embedded ordered key-value store (BSD-3-Clause)

- License: `BSD-3-Clause`
- Homepage: https://github.com/google/leveldb

### lib32-gamemode (1.8.2)

Feral Interactive gamemode — 32-bit client libraries (multilib runtime; mirror-only)

- License: `BSD-3-Clause`
- Homepage: https://github.com/FeralInteractive/gamemode

### lib32-nvidia (580.159.04)

NVIDIA proprietary GPU driver — 32-bit userspace libraries (multilib runtime; mirror-only, NVIDIA-hardware opt-in)

- License: `MIT AND GPL-2.0-only AND LicenseRef-NVIDIA-Linux-EULA`
- Homepage: https://www.nvidia.com/en-us/drivers/

### libatomic_ops (7.10.0)

Atomic memory update operations library

- License: `MIT`
- Homepage: https://github.com/bdwgc/libatomic_ops

### libcdr (0.1.8)

CorelDRAW document import library

- License: `MPL-2.0`
- Homepage: https://wiki.documentfoundation.org/DLP/Libraries/libcdr

### libdeflate (1.25)

Heavily optimized library for DEFLATE/zlib/gzip compression and decompression

- License: `MIT`
- Homepage: https://github.com/ebiggers/libdeflate

### libdht (0.27)

BitTorrent Mainline DHT client library (jech/dht)

- License: `MIT`
- Homepage: https://github.com/jech/dht

### libid3tag (0.15.1b)

ID3 tag manipulation library (MAD project)

- License: `GPL-2.0-or-later`
- Homepage: https://sourceforge.net/projects/mad/

### libmypaint (1.6.1)

Brush engine library for MyPaint and GIMP

- License: `ISC`
- Homepage: https://github.com/mypaint/libmypaint

### libnatpmp (20230423)

NAT-PMP (Port Mapping Protocol) client library and natpmpc utility

- License: `BSD-3-Clause`
- Homepage: http://miniupnp.free.fr/libnatpmp.html

### libosinfo (1.12.0)

GObject library for operating system and install-media metadata

- License: `LGPL-2.1-or-later`
- Homepage: https://libosinfo.org

### libpeas (1.36.0)

GObject plugin system

- License: `LGPL-2.1-or-later`

### libreoffice (26.2.1.2)

Full-featured office productivity suite

- License: `MPL-2.0`
- Homepage: https://www.libreoffice.org/

### librevenge (0.0.5)

Document import filter base library

- License: `LGPL-2.1-or-later OR MPL-2.0`
- Homepage: https://sourceforge.net/projects/libwpd/

### libslirp (4.7.0)

General purpose TCP-IP emulator library

- License: `BSD-3-Clause`
- Homepage: https://gitlab.freedesktop.org/slirp/libslirp

### libsodium (1.0.22)

Portable cryptography library providing the NaCl API

- License: `ISC`
- Homepage: https://doc.libsodium.org/

### libssh (0.12.2)

SSHv2 client and server library with SFTP support

- License: `LGPL-2.1-only`
- Homepage: https://www.libssh.org/

### libtheora (1.2.0)

Reference implementation of the Theora video codec

- License: `BSD-3-Clause`
- Homepage: https://www.theora.org/

### libtpms (0.10.2)

Library providing TPM 1.2 and TPM 2.0 emulation

- License: `BSD-3-Clause`
- Homepage: https://github.com/stefanberger/libtpms

### libva-utils (2.23.0)

VA-API utility programs (vainfo, vaplay, vapostproc)

- License: `MIT`
- Homepage: https://github.com/intel/libva-utils

### libvdpau (1.5)

Video Decode and Presentation API for Unix — dispatcher library

- License: `MIT`
- Homepage: https://gitlab.freedesktop.org/vdpau/libvdpau

### libvdpau-va-gl (0.4.2)

VDPAU driver with OpenGL/VAAPI back-end (translation layer)

- License: `MIT`
- Homepage: https://github.com/i-rinat/libvdpau-va-gl

### libvirt (12.5.0)

Virtualization management daemon and client tools for QEMU/KVM

- License: `LGPL-2.1-or-later`
- Homepage: https://libvirt.org

### libvirt-glib (5.0.0)

GLib and GObject bindings for libvirt

- License: `LGPL-2.1-or-later`
- Homepage: https://libvirt.org

### libvirt-python (12.5.0)

Python bindings for the libvirt API

- License: `LGPL-2.1-or-later`
- Homepage: https://libvirt.org

### libvisio (0.1.10)

Microsoft Visio document import library

- License: `MPL-2.0`
- Homepage: https://wiki.documentfoundation.org/DLP/Libraries/libvisio

### libvncserver (0.9.15)

VNC server and client libraries implementing the RFB protocol

- License: `GPL-2.0-or-later`
- Homepage: https://github.com/LibVNC/libvncserver

### libwpd (0.10.3)

WordPerfect Document import/export library

- License: `LGPL-2.1-or-later`
- Homepage: https://libwpd.sourceforge.net/

### libwpg (0.3.4)

WordPerfect Graphics import library

- License: `LGPL-2.1-or-later`
- Homepage: https://libwpg.sourceforge.net/

### libxapp (3.3.3)

Cross-desktop GTK3 utility library (XApp) used by timeshift

- License: `LGPL-3.0-only`
- Homepage: https://github.com/linuxmint/xapp

### libxcrypt-compat (4.5.2)

LSB compatibility — libcrypt.so.1 (ABI 1) alongside libcrypt.so.2

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/besser82/libxcrypt

### lighttpd (1.4.82)

Lightweight event-driven HTTP server (BSD-3)

- License: `BSD-3-Clause`
- Homepage: https://www.lighttpd.net/

### lm-sensors (3.6.0)

Hardware monitoring — libsensors, sensors CLI, sensors-detect, fancontrol

- License: `LGPL-2.1-or-later AND GPL-2.0-or-later`
- Homepage: https://github.com/lm-sensors/lm-sensors

### mariadb (11.8.6)

MariaDB 11.8 LTS — GPL-2.0-only relational database server (MySQL-compatible); statically bundles fmt (MIT) via CMake ExternalProject

- License: `GPL-2.0-only AND LGPL-2.1-or-later AND MIT`
- Homepage: https://mariadb.org/

### memcached (1.6.41)

High-performance distributed in-memory KV cache (default-secure config — bind 127.0.0.1 + UDP off + TLS + seccomp + SASL build-enabled)

- License: `BSD-3-Clause`
- Homepage: https://memcached.org/

### mingw-w64-binutils (2.46.0)

Cross-binutils for the two Windows PE targets (mingw-w64 toolchain, stage 1)

- License: `GPL-3.0-or-later`
- Homepage: https://www.gnu.org/software/binutils/

### mingw-w64-crt (14.0.0)

Windows C runtime import libraries and startup objects, both PE triplets (mingw-w64 toolchain, stage 4)

- License: `ZPL-2.0 AND LGPL-2.1-or-later AND BSD-3-Clause`
- Homepage: https://www.mingw-w64.org/

### mingw-w64-gcc (15.2.0)

Full cross-gcc for both PE triplets, posix threads (mingw-w64 toolchain, stage 6)

- License: `GPL-3.0-or-later`
- Homepage: https://gcc.gnu.org/

### mingw-w64-gcc-bootstrap (15.2.0)

Bootstrap cross-gcc for both PE triplets, compiler only (mingw-w64 toolchain, stage 3)

- License: `GPL-3.0-or-later`
- Homepage: https://gcc.gnu.org/

### mingw-w64-headers (14.0.0)

Windows API headers for both PE triplets (mingw-w64 toolchain, stage 2)

- License: `ZPL-2.0 AND LGPL-2.1-or-later AND BSD-3-Clause`
- Homepage: https://www.mingw-w64.org/

### mingw-w64-tools (14.0.0)

mingw-w64 host tools — the widl IDL compiler for both PE triplets (mingw-w64 toolchain, stage 7)

- License: `ZPL-2.0 AND LGPL-2.1-or-later AND BSD-3-Clause`
- Homepage: https://www.mingw-w64.org/

### mingw-w64-winpthreads (14.0.0)

POSIX threads implementation for Windows, both PE triplets (mingw-w64 toolchain, stage 5)

- License: `ZPL-2.0 AND LGPL-2.1-or-later AND BSD-3-Clause`
- Homepage: https://www.mingw-w64.org/

### miniupnpc (2.3.3)

UPnP IGD client library for NAT traversal in P2P and networking apps

- License: `BSD-3-Clause`
- Homepage: http://miniupnp.free.fr/

### mpv (0.41.0)

Free media player for the command line and desktop

- License: `GPL-2.0-or-later`
- Homepage: https://mpv.io/

### mypaint-brushes (2.0.2)

Brush data files for applications using libmypaint

- License: `CC0-1.0`
- Homepage: https://github.com/mypaint/mypaint-brushes

### ncurses-compat (6.6)

LSB compatibility — libncurses.so.5 and libncursesw.so.5 (ABI 5)

- License: `MIT`
- Homepage: https://invisible-island.net/ncurses/

### netavark (1.17.2)

Container network plugin written in Rust

- License: `Apache-2.0`
- Homepage: https://github.com/containers/netavark

### nginx (1.29.8)

Modern HTTP server / reverse proxy / load balancer (mainline; default-secure config — bind 127.0.0.1, server_tokens off, TLS-only sample, status locked to loopback)

- License: `BSD-2-Clause`
- Homepage: https://nginx.org/

### numpy (2.4.2)

Fundamental package for scientific computing with Python

- License: `BSD-3-Clause`
- Homepage: https://numpy.org/

### nvidia (580.159.04)

NVIDIA proprietary GPU driver — open kernel modules + closed userspace (mirror-only, post-install opt-in)

- License: `MIT AND GPL-2.0-only AND LicenseRef-NVIDIA-Linux-EULA`
- Homepage: https://www.nvidia.com/en-us/drivers/

### openjdk (24.0.2)

OpenJDK Java development kit and runtime, built from source

- License: `GPL-2.0-only WITH Classpath-exception-2.0`
- Homepage: https://openjdk.org/

### opusfile (0.12)

High-level decoder library for the standard Opus-in-Ogg container format

- License: `BSD-3-Clause`
- Homepage: https://opus-codec.org/

### osinfo-db (20251212)

Operating system metadata database for the osinfo ecosystem

- License: `GPL-2.0-or-later`
- Homepage: https://libosinfo.org

### osinfo-db-tools (1.12.0)

Tools for importing and exporting the osinfo database

- License: `GPL-2.0-or-later`
- Homepage: https://libosinfo.org

### pandas (3.0.3)

Powerful data structures for data analysis, time series, and statistics

- License: `BSD-3-Clause`
- Homepage: https://pandas.pydata.org/

### pangomm1 (2.46.4)

C++ interface to the Pango text rendering library (GTK3 version)

- License: `LGPL-2.1-or-later`
- Homepage: https://www.gtkmm.org/

### passt (2026_01_20.386b5f5)

User-mode networking for VMs and namespaces

- License: `GPL-2.0-or-later`
- Homepage: https://passt.top/

### perl-archive-zip (1.68)

Perl module for reading and writing Zip archive files

- License: `Artistic-2.0`
- Homepage: https://metacpan.org/pod/Archive::Zip

### phodav (3.0)

WebDAV server library with chezdav and spice-webdavd for VM folder sharing

- License: `LGPL-2.1-or-later`
- Homepage: https://gitlab.gnome.org/GNOME/phodav

### php (8.4.11)

PHP general-purpose scripting language and runtime

- License: `PHP-3.01`
- Homepage: https://www.php.net/

### podman (5.8.2)

Tool and library for managing OCI containers and pods

- License: `Apache-2.0`
- Homepage: https://podman.io/

### portaudio (19.7.0)

Cross-platform portable audio I/O library

- License: `MIT`
- Homepage: http://www.portaudio.com/

### portmidi (2.0.7)

Portable real-time MIDI I/O library (ALSA backend on Linux)

- License: `MIT`
- Homepage: https://github.com/PortMidi/portmidi

### postgresql (18.3)

PostgreSQL relational database server (default-secure config — listen 127.0.0.1, scram-sha-256 auth, deferred initdb via postgres-setup helper)

- License: `PostgreSQL`
- Homepage: https://www.postgresql.org/

### potrace (1.16)

Bitmap to vector graphics conversion tool and library

- License: `GPL-2.0-or-later`
- Homepage: https://potrace.sourceforge.net/

### python-certifi (2026.6.17)

Python TLS trust anchor shim redirected to the system CA bundle

- License: `MPL-2.0`
- Homepage: https://github.com/certifi/python-certifi

### python-charset-normalizer (3.4.9)

Character set detection library for Python (requests dependency)

- License: `MIT`
- Homepage: https://github.com/jawah/charset_normalizer

### python-dateutil (2.9.0.post0)

Extensions to the standard Python datetime module

- License: `Apache-2.0 AND BSD-3-Clause`
- Homepage: https://github.com/dateutil/dateutil

### python-pyparsing (3.3.2)

Python parsing module (spice-gtk codegen build dependency)

- License: `MIT`
- Homepage: https://github.com/pyparsing/pyparsing

### python-requests (2.34.2)

HTTP library for Python (virt-manager install-media fetching)

- License: `Apache-2.0`
- Homepage: https://github.com/psf/requests

### python-six (1.17.0)

Python 2/3 compatibility library (spice-gtk codegen build dependency)

- License: `MIT`
- Homepage: https://github.com/benjaminp/six

### python-urllib3 (2.7.0)

HTTP client library for Python (requests dependency)

- License: `MIT`
- Homepage: https://github.com/urllib3/urllib3

### qemu (11.0.2)

Full system emulator and virtualizer with KVM support and disk image tools

- License: `GPL-2.0-only`
- Homepage: https://www.qemu.org

### radeontop (1.4)

View AMD Radeon GPU utilization in real time (htop-style TUI)

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/clbr/radeontop

### rapidjson (0.0.0+master.20250205)

Fast JSON parser/generator for C++ with both SAX/DOM style API (header-only)

- License: `MIT`
- Homepage: https://rapidjson.org/

### re2 (2025-11-05)

Fast, safe, thread-friendly regular expression engine

- License: `BSD-3-Clause`
- Homepage: https://github.com/google/re2

### remmina (1.4.43)

Remote desktop client for RDP, VNC, SPICE and SSH connections

- License: `GPL-2.0-or-later`
- Homepage: https://remmina.org/

### rhythmbox (3.4.9)

GNOME music player and library manager (GTK3 + GStreamer)

- License: `GPL-2.0-or-later`
- Homepage: https://wiki.gnome.org/Apps/Rhythmbox

### rocksdb (11.1.1)

Facebook/Meta RocksDB — embedded high-performance persistent KV store (LSM-tree)

- License: `(Apache-2.0 OR GPL-2.0-only) AND BSD-3-Clause`
- Homepage: https://github.com/facebook/rocksdb

### runc (1.3.5)

CLI tool for spawning and running containers per the OCI specification

- License: `Apache-2.0`
- Homepage: https://github.com/opencontainers/runc

### scons (4.10.1)

Pure-Python software build tool (Make alternative)

- License: `MIT`
- Homepage: https://scons.org

### sd (1.0.0)

Intuitive find & replace CLI

- License: `MIT`
- Homepage: https://github.com/chmln/sd

### seabios (1.17.0)

Open-source legacy BIOS firmware for QEMU guests

- License: `LGPL-3.0-only`
- Homepage: https://www.seabios.org

### signal (1.0)

Helper to download and install Signal Desktop

- License: `GPL-3.0-or-later`
- Payload license: `AGPL-3.0-only`
- Homepage: https://signal.org/

### smartmontools (7.5)

S.M.A.R.T. disk health monitoring and reporting utilities (smartctl, smartd)

- License: `GPL-2.0-or-later`
- Homepage: https://www.smartmontools.org/

### snappy (1.2.2)

Fast compression/decompression library from Google

- License: `BSD-3-Clause`
- Homepage: https://github.com/google/snappy

### spice (0.16.0)

SPICE remote display server library

- License: `LGPL-2.1-or-later`
- Homepage: https://www.spice-space.org

### spice-gtk (0.42)

GTK client library for the SPICE remote display protocol

- License: `LGPL-2.1-or-later`
- Homepage: https://www.spice-space.org

### spice-protocol (0.14.5)

SPICE protocol headers for the remote display stack

- License: `BSD-3-Clause`
- Homepage: https://www.spice-space.org

### spotify (1.0)

Helper to download and install Spotify

- License: `GPL-3.0-or-later`
- Payload license: `LicenseRef-Spotify-ToS`
- Homepage: https://www.spotify.com/

### starship (1.25.1)

The minimal, blazing-fast, and infinitely customizable prompt for any shell

- License: `ISC`
- Homepage: https://starship.rs

### steam (1.0)

Steam client download-helper (Valve's signed apt repo; 32-bit runtime closure)

- License: `LicenseRef-Valve-SSA`
- Homepage: https://store.steampowered.com/

### swtpm (0.10.1)

Software TPM emulator with socket and control channel interfaces

- License: `BSD-3-Clause`
- Homepage: https://github.com/stefanberger/swtpm

### tailscale (1.98.5)

WireGuard-based mesh VPN — tailscale CLI + tailscaled node agent

- License: `BSD-3-Clause`
- Homepage: https://tailscale.com

### tealdeer (1.6.1)

A very fast implementation of tldr in Rust

- License: `MIT OR Apache-2.0`
- Homepage: https://github.com/dbrgn/tealdeer

### thrift (0.24.0)

Apache Thrift RPC framework (C++ library + compiler; parquet metadata dep)

- License: `Apache-2.0`
- Homepage: https://thrift.apache.org/

### thunderbird (140.8.0esr)

Mozilla Thunderbird email and news client

- License: `MPL-2.0`
- Homepage: https://www.thunderbird.net/

### timeshift (25.12.4)

System restore utility taking rsync or btrfs snapshots of the system

- License: `GPL-2.0-or-later`
- Homepage: https://github.com/linuxmint/timeshift

### tinycss2 (1.5.1)

Low-level CSS parser for Python (icon-toolchain dep)

- License: `BSD-3-Clause`
- Homepage: https://github.com/Kozea/tinycss2

### tmux (3.6b)

Terminal multiplexer

- License: `ISC`
- Homepage: https://github.com/tmux/tmux

### tokei (12.1.2)

Count your code, quickly

- License: `MIT OR Apache-2.0`
- Homepage: https://github.com/XAMPPRocky/tokei

### training (1.0.0)

Training meta-package — the complete InterGen model-training stack (unsloth, transformers, pytorch with ROCm, and their full mirror-side closure) in one install step

- License: `GPL-3.0-or-later`
- Homepage: https://www.intergenos.org/

### transmission (4.1.1)

Fast, easy, free BitTorrent client (daemon, CLI utils, GTK4 GUI)

- License: `GPL-3.0-or-later`
- Homepage: https://transmissionbt.com/

### unixodbc (2.3.14)

Open Database Connectivity (ODBC) implementation for Unix

- License: `LGPL-2.1-or-later`
- Homepage: https://www.unixodbc.org/

### usbredir (0.15.0)

USB network redirection protocol libraries and tools

- License: `LGPL-2.1-or-later`
- Homepage: https://gitlab.freedesktop.org/spice/usbredir

### utf8proc (2.11.3)

Clean C library for processing UTF-8 Unicode data

- License: `MIT`
- Homepage: https://github.com/JuliaStrings/utf8proc

### valkey (9.0.4)

High-performance in-memory KV store (Redis-wire-compatible)

- License: `BSD-3-Clause`
- Homepage: https://valkey.io

### virt-manager (5.1.0)

Desktop application for managing QEMU/KVM virtual machines

- License: `GPL-2.0-or-later`
- Homepage: https://virt-manager.org

### virt-viewer (11.0)

Graphical console viewer for virtual machines (SPICE and VNC)

- License: `GPL-2.0-or-later`
- Homepage: https://virt-manager.org

### virtiofsd (1.14.0)

vhost-user virtio-fs device backend daemon for sharing host directories with VMs

- License: `Apache-2.0 AND BSD-3-Clause`
- Homepage: https://gitlab.com/virtio-fs/virtiofsd

### vkd3d-proton (3.0.1)

Vulkan-based D3D12 implementation for wine, both PE widths

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/HansKristian-Work/vkd3d-proton

### vscode (1.0)

Helper to download and install Microsoft Visual Studio Code

- License: `GPL-3.0-or-later`
- Payload license: `LicenseRef-Microsoft-VSCode-Software-License`
- Homepage: https://code.visualstudio.com/

### vulkan-tools (1.4.341.0)

Vulkan utility programs — vulkaninfo, vkcube, vkcubepp

- License: `Apache-2.0`
- Homepage: https://github.com/KhronosGroup/Vulkan-Tools

### webencodings (0.5.1)

WHATWG character encoding labels for Python (icon-toolchain dep)

- License: `BSD-3-Clause`
- Homepage: https://github.com/gsnedders/python-webencodings

### websockets (16.0)

WebSocket implementation (RFC 6455 and 7692)

- License: `BSD-3-Clause`
- Homepage: https://github.com/python-websockets/websockets

### wine (11.12)

Windows compatibility layer, new-WoW64 build with PE cross-compilation

- License: `LGPL-2.1-or-later`
- Homepage: https://www.winehq.org/

### wine-gecko (2.47.4)

Gecko HTML engine MSI addons for wine prefix creation (both PE widths)

- License: `MPL-2.0`
- Homepage: https://wiki.winehq.org/Gecko

### wine-mono (11.2.0)

Mono .NET runtime MSI addon for wine prefix creation

- License: `MIT AND GPL-3.0-or-later AND LGPL-2.1-or-later`
- Homepage: https://wiki.winehq.org/Mono

### winetricks (20260125)

Helper to install Windows components and tweaks into a Wine prefix

- License: `LGPL-2.1-or-later`
- Homepage: https://github.com/Winetricks/winetricks

### wxwidgets (3.2.10)

Cross-platform C++ GUI toolkit (GTK 3 backend, Audacity gating dep)

- License: `LGPL-2.0-or-later WITH WxWindows-exception-3.1`
- Homepage: https://www.wxwidgets.org/

### xh (0.25.3)

Friendly and fast tool for sending HTTP requests

- License: `MIT`
- Homepage: https://github.com/ducaale/xh

### xsimd (14.3.0)

C++ wrappers for SIMD intrinsics (header-only)

- License: `BSD-3-Clause`
- Homepage: https://github.com/xtensor-stack/xsimd

### yajl (2.1.0)

Yet Another JSON Library

- License: `ISC`
- Homepage: https://github.com/lloyd/yajl

### zig (0.16.0)

The Zig programming language compiler and toolchain

- License: `MIT`
- Homepage: https://ziglang.org/

### zoom (1.0)

Helper to download and install Zoom Workplace

- License: `GPL-3.0-or-later`
- Payload license: `LicenseRef-Zoom-Terms-of-Service`
- Homepage: https://zoom.us/

### zoxide (0.9.4)

A smarter cd command

- License: `MIT`
- Homepage: https://github.com/ajeetdsouza/zoxide


### Tier: `ai`

### accelerate (1.14.0)

Training and inference acceleration for PyTorch (device placement, distributed)

- License: `Apache-2.0`
- Homepage: https://github.com/huggingface/accelerate

### annotated-doc (0.0.4)

Inline documentation for parameters/attributes/returns via Annotated

- License: `MIT`
- Homepage: https://github.com/fastapi/annotated-doc

### annotated-types (0.7.0)

Reusable constraint types for typing.Annotated

- License: `MIT`
- Homepage: https://github.com/annotated-types/annotated-types

### anyio (4.14.2)

Asynchronous compatibility layer over asyncio and trio

- License: `MIT`
- Homepage: https://github.com/agronholm/anyio

### arrow-cpp (25.0.0)

Apache Arrow columnar data platform — C++ libraries (compute, dataset, parquet)

- License: `Apache-2.0`
- Homepage: https://arrow.apache.org/

### bitsandbytes (0.49.2)

k-bit quantization (8-bit optimizers, LLM.int8, NF4/FP4) for PyTorch — ROCm/HIP backend

- License: `MIT`
- Homepage: https://github.com/bitsandbytes-foundation/bitsandbytes

### click (8.4.2)

Composable command-line interface toolkit

- License: `BSD-3-Clause`
- Homepage: https://github.com/pallets/click

### cut-cross-entropy (25.1.1)

Memory-efficient cross-entropy loss computation

- License: `AML`
- Homepage: https://github.com/apple/ml-cross-entropy

### datasets (4.3.0)

Access and share datasets for machine learning workflows

- License: `Apache-2.0`
- Homepage: https://github.com/huggingface/datasets

### diffusers (0.39.0)

State-of-the-art diffusion models for image and audio generation

- License: `Apache-2.0`
- Homepage: https://github.com/huggingface/diffusers

### dill (0.4.0)

Serialize all of Python (extended pickle)

- License: `BSD-3-Clause`
- Homepage: https://github.com/uqfoundation/dill

### docstring-parser (0.18.0)

Parse Python docstrings into structured data

- License: `MIT`
- Homepage: https://github.com/rr-/docstring_parser

### einops (0.8.2)

Flexible tensor operations for readable, reliable code

- License: `MIT`
- Homepage: https://github.com/arogozhnikov/einops

### fsspec (2025.9.0)

Filesystem spec — unified pythonic interface to local/remote/embedded filesystems

- License: `BSD-3-Clause`
- Homepage: https://github.com/fsspec/filesystem_spec

### h11 (0.16.0)

Pure-Python HTTP/1.1 protocol library (sans-IO)

- License: `MIT`
- Homepage: https://github.com/python-hyper/h11

### hf-transfer (0.1.9)

Rust-accelerated file transfer for the Hugging Face Hub

- License: `Apache-2.0`
- Homepage: https://github.com/huggingface/hf_transfer

### hf-xet (1.5.2)

Fast transfer of large files with the Hugging Face Hub (Rust core, Python bindings)

- License: `Apache-2.0`
- Homepage: https://github.com/huggingface/xet-core

### httpcore (1.0.9)

Minimal low-level HTTP client

- License: `BSD-3-Clause`
- Homepage: https://github.com/encode/httpcore

### httpx (0.28.1)

Next-generation HTTP client for Python

- License: `BSD-3-Clause`
- Homepage: https://github.com/encode/httpx

### huggingface-hub (1.24.0)

Client library for the Hugging Face Hub (model/dataset download + cache)

- License: `Apache-2.0`
- Homepage: https://github.com/huggingface/huggingface_hub

### intergen (0.1.0)

InterGen AI assistant — system-aware, local-first, hardware-adaptive

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/InterGenJLU/intergenos

### libcst (1.8.6)

Concrete syntax tree parser and serializer for Python (Rust-native parser)

- License: `MIT`
- Homepage: https://github.com/Instagram/LibCST

### llama-cpp (b8796)

LLM inference engine — server, CLI, benchmark tool, and shared libraries

- License: `MIT`
- Homepage: https://github.com/ggml-org/llama.cpp

### mpmath (1.3.0)

Arbitrary-precision floating-point arithmetic

- License: `BSD-3-Clause`
- Homepage: https://mpmath.org

### msgspec (0.21.1)

Fast serialization and validation (JSON, MessagePack) with a C core

- License: `BSD-3-Clause`
- Homepage: https://github.com/jcrist/msgspec

### multiprocess (0.70.16)

Better multiprocessing and multithreading (dill-backed fork of multiprocessing)

- License: `BSD-3-Clause`
- Homepage: https://github.com/uqfoundation/multiprocess

### nest-asyncio (1.6.0)

Patch asyncio to allow nested event loops

- License: `BSD-2-Clause`
- Homepage: https://github.com/erdewit/nest_asyncio

### networkx (3.6.1)

Graph and network data structures and algorithms

- License: `BSD-3-Clause`
- Homepage: https://networkx.org

### peft (0.19.1)

Parameter-efficient fine-tuning methods (LoRA, adapters) for large models

- License: `Apache-2.0`
- Homepage: https://github.com/huggingface/peft

### pillow (12.3.0)

Python Imaging Library (fork) — image processing

- License: `MIT-CMU`
- Homepage: https://python-pillow.github.io/

### psutil (7.2.2)

Cross-platform process and system utilities

- License: `BSD-3-Clause`
- Homepage: https://github.com/giampaolo/psutil

### pyarrow (25.0.0)

Python bindings for Apache Arrow (builds against the system arrow-cpp)

- License: `Apache-2.0`
- Homepage: https://arrow.apache.org/

### pydantic (2.13.4)

Data validation using Python type hints

- License: `MIT`
- Homepage: https://github.com/pydantic/pydantic

### pydantic-core (2.46.4)

Core validation logic for pydantic (Rust)

- License: `MIT`
- Homepage: https://github.com/pydantic/pydantic-core

### python-xxhash (3.8.1)

Python binding for the xxHash non-cryptographic hash algorithm (imported at load time by the datasets fingerprint module)

- License: `BSD-2-Clause`
- Homepage: https://github.com/ifduyue/python-xxhash

### pytorch (2.10.0)

Tensors and dynamic neural networks with GPU acceleration (ROCm/HIP build)

- License: `BSD-3-Clause`
- Homepage: https://pytorch.org/

### pytz (2026.2)

World timezone definitions (Olson tz database) for Python

- License: `MIT`
- Homepage: https://pythonhosted.org/pytz

### regex (2026.7.19)

Alternative regular expression module (drop-in re replacement)

- License: `Apache-2.0 AND CNRI-Python`
- Homepage: https://github.com/mrabarnett/mrab-regex

### safetensors (0.8.0)

Safe and portable tensor serialization format (Rust core, Python bindings)

- License: `Apache-2.0`
- Homepage: https://github.com/huggingface/safetensors

### sentencepiece (0.2.2)

Unsupervised text tokenizer for neural network text generation (C++ core, Python bindings)

- License: `Apache-2.0`
- Homepage: https://github.com/google/sentencepiece

### shellingham (1.5.4)

Detect the shell the current process is running in

- License: `ISC`
- Homepage: https://github.com/sarugaku/shellingham

### sniffio (1.3.1)

Detect which async library the code is running under

- License: `MIT OR Apache-2.0`
- Homepage: https://github.com/python-trio/sniffio

### sympy (1.14.0)

Symbolic mathematics library (computer algebra system)

- License: `BSD-3-Clause`
- Homepage: https://sympy.org

### timm (1.0.28)

PyTorch Image Models — image model architectures, layers and pretrained weights

- License: `Apache-2.0`
- Homepage: https://github.com/huggingface/pytorch-image-models

### tokenizers (0.22.2)

Fast state-of-the-art tokenizers for research and production (Rust core, Python bindings)

- License: `Apache-2.0`
- Homepage: https://github.com/huggingface/tokenizers

### torchao (0.16.0)

PyTorch-native quantization and optimization primitives

- License: `BSD-3-Clause`
- Homepage: https://github.com/pytorch/ao

### torchvision (0.25.0)

Datasets, transforms and models for computer vision (torch companion)

- License: `BSD-3-Clause`
- Homepage: https://github.com/pytorch/vision

### tqdm (4.69.0)

Fast, extensible progress bar for Python

- License: `MPL-2.0 AND MIT`
- Homepage: https://github.com/tqdm/tqdm

### transformers (5.5.0)

State-of-the-art machine learning models (pretrained transformers)

- License: `Apache-2.0`
- Homepage: https://github.com/huggingface/transformers

### triton (3.6.0)

Triton language and compiler (GPU kernel compiler) — AMD/ROCm backend

- License: `MIT`
- Homepage: https://github.com/triton-lang/triton

### trl (0.24.0)

Preference and reinforcement fine-tuning toolkit for transformer models

- License: `Apache-2.0`
- Homepage: https://github.com/huggingface/trl

### typeguard (4.5.2)

Run-time type checking for Python functions

- License: `MIT`
- Homepage: https://github.com/agronholm/typeguard

### typer (0.27.0)

Library for building CLI applications from Python type hints

- License: `MIT`
- Homepage: https://github.com/fastapi/typer

### typing-inspection (0.4.2)

Runtime inspection utilities for Python typing

- License: `MIT`
- Homepage: https://github.com/pydantic/typing-inspection

### tyro (1.0.15)

Zero-effort CLI interfaces and config objects from type hints

- License: `MIT`
- Homepage: https://github.com/brentyi/tyro

### unsloth (2026.7.4)

Fast fine-tuning and RL for open LLMs (LoRA/QLoRA on the local GPU stack)

- License: `Apache-2.0`
- Homepage: https://github.com/unslothai/unsloth

### unsloth-zoo (2026.7.4)

Shared utilities and kernels for unsloth

- License: `LGPL-3.0-or-later`
- Homepage: https://github.com/unslothai/unsloth-zoo

### xformers (0.0.35)

Hackable and optimized transformer building blocks (attention kernels)

- License: `BSD-3-Clause`
- Homepage: https://github.com/facebookresearch/xformers


### Tier: `compute`

### amdsmi (7.2.4)

AMD System Management Interface library + amd-smi CLI (successor GPU/CPU telemetry API; amdgpu_top's full-data source)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-systems

### aotriton (0.11.1b)

Ahead-of-time compiled Triton attention kernels for ROCm (PyTorch SDPA backend)

- License: `MIT`
- Homepage: https://github.com/ROCm/aotriton

### aotriton-triton-llvm (21.0.0)

Pristine LLVM/MLIR/LLD at AOTriton's bundled-triton pinned commit — private-prefix build product for aotriton

- License: `Apache-2.0 WITH LLVM-exception`
- Homepage: https://github.com/llvm/llvm-project

### aqlprofile (7.2.4)

HSA AQL profiling library (hardware performance-counter packets — rocprofiler's collection backend)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-systems

### composable-kernel (7.2.4)

Composable Kernel — performance-portable GPU operator templates + device operation libraries (MIOpen backend)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-libraries

### cuda-toolkit (13.3.1)

Helper to download and install the NVIDIA CUDA toolkit into /opt/cuda

- License: `GPL-3.0-or-later`
- Payload license: `LicenseRef-NVIDIA-CUDA-EULA`
- Homepage: https://developer.nvidia.com/cuda-toolkit

### eigen (3.4.0)

Eigen C++ template library for linear algebra (header-only)

- License: `MPL-2.0`
- Homepage: https://eigen.tuxfamily.org

### fmt (12.2.0)

Fast C++ formatting library ({fmt})

- License: `MIT`
- Homepage: https://github.com/fmtlib/fmt

### frugally-deep (0.17.1.38c52448)

Header-only library for running Keras models in C++ (MIOpen AI-heuristic dependency)

- License: `MIT`
- Homepage: https://github.com/Dobiasd/frugally-deep

### functionalplus (0.2.22)

FunctionalPlus C++ functional-programming header library (frugally-deep dependency)

- License: `BSL-1.0`
- Homepage: https://github.com/Dobiasd/FunctionalPlus

### glog (0.7.1)

C++ application-level logging library (google-glog)

- License: `BSD-3-Clause`
- Homepage: https://github.com/google/glog

### half (1.12.0)

IEEE-754 half-precision C++ header library, ROCm packaging (MIGraphX dep)

- License: `MIT`
- Homepage: https://github.com/ROCm/half

### hipblas (7.2.4)

BLAS marshalling library over rocBLAS (the hipBLAS API llama.cpp links)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-libraries

### hipblas-common (7.2.4)

Common headers shared by the hipBLAS API family

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-libraries

### hipblaslt (7.2.4)

hipBLASLt — lightweight GEMM library with flexible epilogues (MIOpen + PyTorch-class consumer surface)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-libraries

### hipcub (7.2.4)

CUB-compatible C++ header interface over rocPRIM (HIP parallel primitives portability layer)

- License: `BSD-3-Clause`
- Homepage: https://github.com/ROCm/rocm-libraries

### hipfft (7.2.4)

HIP portability interface over rocFFT (cuFFT-compatible FFT API)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-libraries

### hipify (7.2.4)

HIPIFY — CUDA-to-HIP source translation tools (hipify-clang + hipify-perl; the CUDA-porting user's on-ramp)

- License: `MIT`
- Homepage: https://github.com/ROCm/HIPIFY

### hiprand (7.2.4)

HIP portability interface over rocRAND (cuRAND-compatible RNG API)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-libraries

### hipsolver (7.2.4)

HIP portability interface over rocSOLVER (cuSOLVER-compatible dense + sparse solver API)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-libraries

### hipsparse (7.2.4)

HIP portability interface over rocSPARSE (cuSPARSE-compatible sparse API)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-libraries

### llama-cpp-cuda (b8796)

LLM inference engine — CUDA variant for NVIDIA GPUs (opt-in, mirror-only)

- License: `MIT`
- Homepage: https://github.com/ggml-org/llama.cpp

### llama-cpp-hip (b8796)

LLM inference engine — HIP/ROCm variant for AMD GPUs (opt-in, mirror-only)

- License: `MIT`
- Homepage: https://github.com/ggml-org/llama.cpp

### migraphx (7.2.4)

MIGraphX — AMD graph-inference engine (ONNX/TF model compiler + runtime, C++ and Python APIs)

- License: `MIT`
- Homepage: https://github.com/ROCm/AMDMIGraphX

### miopen (7.2.4)

MIOpen — AMD's deep-learning primitives library (convolutions, attention, norm — the PyTorch cudnn-equivalent)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-libraries

### nlohmann-json (3.12.0)

JSON for Modern C++ (header-only library + cmake config)

- License: `MIT`
- Homepage: https://github.com/nlohmann/json

### pybind11 (3.0.4)

pybind11 — C++/Python interoperability headers, cmake config, and python module

- License: `BSD-3-Clause`
- Homepage: https://github.com/pybind/pybind11

### rccl (7.2.4)

ROCm Communication Collectives Library (NCCL-compatible multi-GPU communication — the dual-GPU workstation consumer)

- License: `BSD-3-Clause`
- Homepage: https://github.com/ROCm/rccl

### rocblas (7.2.4)

ROCm BLAS library with Tensile-generated GPU kernels

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-libraries

### rocdbgapi (7.2.4)

AMD Debugger API library (GPU debug primitives — ROCgdb + the debug agent build on this)

- License: `MIT`
- Homepage: https://github.com/ROCm/ROCdbgapi

### rocfft (7.2.4)

ROCm Fast Fourier Transform library (device FFT kernels + runtime kernel cache)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-libraries

### rocgdb (7.2.4)

ROCgdb — AMD GPU-aware GDB (heterogeneous CPU+GPU source-level debugging)

- License: `GPL-3.0-or-later`
- Homepage: https://github.com/ROCm/ROCgdb

### rocm-cmake (7.2.4)

ROCm CMake build tools (ROCmCMakeBuildTools modules)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-cmake

### rocm-comgr (7.2.4)

AMD Code Object Manager library (libamd_comgr)

- License: `NCSA`
- Homepage: https://github.com/ROCm/llvm-project/tree/amd-staging/amd/comgr

### rocm-core (7.2.4)

ROCm platform base metadata (version header, .info/version, librocm-core path helper)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-core

### rocm-debug-agent (7.2.4)

ROCm debug agent library (GPU exception/state dump + wave debugging glue)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocr_debug_agent

### rocm-device-libs (7.2.4)

AMD device-side language runtime bitcode libraries (ocml, ockl, oclc, hip)

- License: `NCSA`
- Homepage: https://github.com/ROCm/llvm-project/tree/amd-staging/amd/device-libs

### rocm-hip (7.2.4)

HIP runtime and compiler driver for AMD GPUs (CLR/hipamd + hipcc/hipconfig)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-systems

### rocm-llvm (7.2.4)

AMD ROCm LLVM toolchain (amdclang, lld, compiler-rt) for GPU kernel compilation

- License: `Apache-2.0 WITH LLVM-exception`
- Homepage: https://github.com/ROCm/llvm-project

### rocm-smi-lib (7.2.4)

ROCm System Management Interface library + rocm-smi CLI (GPU monitoring)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-systems

### rocminfo (7.2.4)

ROCm system-info reporter (rocminfo, rocm_agent_enumerator)

- License: `NCSA`
- Homepage: https://github.com/ROCm/rocm-systems

### rocprim (7.2.4)

ROCm parallel primitives C++ header library (build-dep of the math library set)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-libraries

### rocprofiler-register (7.2.4)

ROCm profiler registration shim (hipamd/rocr load-time profiler hook)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-systems

### rocprofiler-sdk (7.2.4)

ROCm profiling SDK + rocprofv3 CLI (GPU kernel/counter profiling — the tuning surface for our own inference)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-systems

### rocr-runtime (7.2.4)

HSA Runtime API and runtime for ROCm (libhsa-runtime64, hsakmt thunk folded in)

- License: `NCSA`
- Homepage: https://github.com/ROCm/rocm-systems

### rocrand (7.2.4)

ROCm random number generation library (device-side RNG for ML training and simulation)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-libraries

### rocsolver (7.2.4)

ROCm dense linear algebra solvers (LAPACK-class routines on GPU)

- License: `BSD-2-Clause`
- Homepage: https://github.com/ROCm/rocm-libraries

### rocsparse (7.2.4)

ROCm sparse linear algebra library (BLAS for sparse matrices on GPU)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-libraries

### rocthrust (7.2.4)

Thrust-compatible C++ parallel algorithms header library over rocPRIM

- License: `Apache-2.0`
- Homepage: https://github.com/ROCm/rocm-libraries

### roctracer (7.2.4)

ROCm tracer + ROCTX annotation library (runtime API tracing; roctx64 is RCCL/hipBLASLt's marker backend)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-systems

### rocwmma (7.2.4)

Wave Matrix Multiply-Accumulate C++ headers (flash-attention build dep)

- License: `MIT`
- Homepage: https://github.com/ROCm/rocm-libraries

### triton-llvm (22.0.0)

Pristine LLVM/MLIR/LLD at triton 3.6.0's pinned commit — private-prefix build product for triton

- License: `Apache-2.0 WITH LLVM-exception`
- Homepage: https://github.com/llvm/llvm-project



---

## Provenance

Generated by `scripts/generate-third-party-notices.py` from
`packages/*/*/package.yml`. The script is the source of truth; the
present file is its output, regenerated on each package-tree
change.

This file closes the human-readable index portion of audit finding
**P-004** (High: no `THIRD-PARTY-NOTICES` / `NOTICE` / `legal/`
directory exists). The per-package LICENSE-text bundling at
`/usr/share/licenses/<package>/` on installed systems — the other
half of P-004's remediation — lands as the `bundle_license` build
hook tracked internally (K21).

License of this file: **CC0-1.0** (public domain dedication).

