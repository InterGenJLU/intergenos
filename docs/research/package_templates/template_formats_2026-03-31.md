# Package Template Format Survey — Real Examples

**Date:** March 31, 2026
**Context:** Designing InterGenOS package template format

---

## Real-World Template Examples (Bash Package)

### Void Linux xbps-src

```bash
# Template file for 'bash'
pkgname=bash
version=5.3
revision=2
build_style=gnu-configure
configure_args="--without-bash-malloc --with-curses --without-installed-readline"
make_build_args="TERMCAP_LIB=${XBPS_CROSS_BASE}/usr/lib/libncursesw.a"
make_check_target=tests
hostmakedepends="bison"
makedepends="ncurses-devel"
checkdepends="perl"
short_desc="GNU Bourne Again Shell"
maintainer="Enno Boland <gottox@voidlinux.org>"
license="GPL-3.0-or-later"
homepage="https://www.gnu.org/software/bash/bash.html"
distfiles="${GNU_SITE}/bash/bash-${version}.tar.gz"
checksum=0d5cd86965f869a26cf64f4b71be7b96f90a3ba8b3d74e27e8e9d9d5550f31ba

post_install() {
    rm -r ${DESTDIR}/usr/share/doc
    ln -s bash ${DESTDIR}/usr/bin/rbash
    vinstall ${FILESDIR}/bashrc 644 etc/bash
}
```

**Strengths:** Clean build_style abstraction, cross-compilation support built-in
**Weaknesses:** Smaller ecosystem

### Gentoo ebuild

```bash
EAPI=8
inherit bash-completion-r1 flag-o-matic toolchain-funcs

DESCRIPTION="The standard GNU Bourne again shell"
HOMEPAGE="https://www.gnu.org/software/bash/"
SRC_URI="mirror://gnu/bash/${P/_p/\-}.tar.gz"
LICENSE="GPL-3+"
SLOT="0"
KEYWORDS="~alpha ~amd64 ~arm ~arm64 ..."
IUSE="afs bashlogger examples mem-scramble net nls plugins pgo +readline"

DEPEND="
    >=sys-libs/ncurses-5.9-r1:0=
    readline? ( >=sys-libs/readline-8.1:0= )
"
RDEPEND="${DEPEND}"
BDEPEND="app-alternatives/yacc"

src_configure() {
    econf \
        --with-curses \
        $(use_with readline) \
        $(use_enable nls)
}
```

**Strengths:** Powerful USE flags, slots, version constraints, eclasses
**Weaknesses:** Complex EAPI versioning, steep learning curve

### CRUX Pkgfile

```bash
name=somelib
version=1.2.3
release=1
source=(ftp://ftp.gnu.org/gnu/$name/$name-$version.tar.gz Makefile.in.patch)

build() {
    cd $name-$version
    patch -p1 < ../Makefile.in.patch
    ./configure --prefix=/usr
    make
    make DESTDIR=$PKG install
}
```

**Strengths:** Maximum transparency, LFS-adjacent, trivial to learn
**Weaknesses:** No abstractions, tedious for complex packages, no feature flags

### Arch Linux PKGBUILD

```bash
pkgname=bash
_basever=5.2
_patchlevel=15
pkgver=${_basever}.${_patchlevel}
pkgrel=1
pkgdesc='The GNU Bourne Again shell'
arch=(x86_64)
license=(GPL)
depends=(readline libreadline.so glibc ncurses)
makedepends=(gcc make autoconf)
optdepends=('bash-completion: for tab completion')

prepare() {
    cd $pkgname-$_basever
    for (( _p=1; _p<=$((10#${_patchlevel})); _p++ )); do
        patch -p0 -i ../bash${_basever//.}-$(printf "%03d" $_p)
    done
}

build() {
    cd $pkgname-$_basever
    ./configure --prefix=/usr --with-curses --enable-readline
    make
}

check() { make -C $pkgname-$_basever check; }

package() {
    make -C $pkgname-$_basever DESTDIR="$pkgdir" install
    ln -s bash "$pkgdir/usr/bin/sh"
}
```

**Strengths:** Simplest syntax, flexible, pure bash, transparent
**Weaknesses:** Repetitive, no feature flags, no build system abstraction

### Alpine APKBUILD

```bash
pkgname=bash
pkgver=5.3.9
pkgrel=1
pkgdesc="The GNU Bourne Again shell"
url="https://www.gnu.org/software/bash/bash.html"
arch="all"
license="GPL-3.0-or-later"
makedepends_build="bison flex"
makedepends_host="readline-dev>8 ncurses-dev musl-libintl"
checkdepends="perl"
subpackages="$pkgname-dbg $pkgname-dev $pkgname-doc"

prepare() { default_prepare; }
build() {
    ./configure --build=$CBUILD --host=$CHOST --prefix=/usr --with-curses --enable-readline
    make
}
check() { make test; }
package() {
    make DESTDIR="$pkgdir" install
    install -Dm644 "$srcdir"/bashrc "$pkgdir"/etc/bash/bashrc
}
```

**Strengths:** Lightweight, cross-compilation aware, simple subpackages
**Weaknesses:** No feature flags, limited abstraction

### Yocto/BitBake Recipe

```python
DESCRIPTION = "GNU Bourne Again Shell"
HOMEPAGE = "https://www.gnu.org/software/bash/"
LICENSE = "GPLv3+"
SRC_URI = "https://ftpmirror.gnu.org/bash/bash-${PV}.tar.gz"

inherit autotools gettext

EXTRA_OECONF = "--with-curses --enable-readline --with-installed-readline"
DEPENDS = "ncurses readline"
RDEPENDS:${PN} = "base-files"
PACKAGES = "${PN} ${PN}-dev ${PN}-doc"
FILES:${PN} = "/bin/bash /usr/bin/bash"
```

**Strengths:** Powerful class inheritance, cross-compilation first, multi-variant support
**Weaknesses:** Steep learning curve, overkill for simple packages

---

## Design Decision for InterGenOS

The InterGenOS template format takes:
- **YAML metadata** (readability of Alpine/Void, machine-parseability)
- **Build styles from Void** (abstractions that reduce boilerplate)
- **Bash build functions from Arch/CRUX** (imperative control when needed)
- **Profile-based flags** (simpler than Gentoo USE flags, still remixable)
- **Python orchestration from Gentoo's model** (proven at scale)
