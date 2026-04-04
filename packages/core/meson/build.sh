#!/bin/bash
# Meson 1.10.1
# LFS 13.0 Section 8.59
#
# DESTDIR exception: pip uses --root instead of DESTDIR.
# Shell completions are installed manually.

configure() {
    : # No configure step
}

build() {
    : # Pure Python — no build step needed
}

do_install() {
    # setuptools 82.0.0 has entry_points incompatibility with Python 3.14
    # Install meson directly as a Python package
    SITE=$(python3 -c "import site; print(site.getsitepackages()[0])")
    mkdir -p "${DESTDIR}${SITE}"
    cp -r mesonbuild "${DESTDIR}${SITE}/"

    # Install the meson command
    mkdir -p "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/meson" << 'MESONEOF'
#!/usr/bin/env python3
from mesonbuild.mesonmain import main
import sys
sys.exit(main())
MESONEOF
    chmod 755 "${DESTDIR}/usr/bin/meson"

    # Shell completions
    install -vDm644 data/shell-completions/bash/meson "${DESTDIR}/usr/share/bash-completion/completions/meson"
    install -vDm644 data/shell-completions/zsh/_meson "${DESTDIR}/usr/share/zsh/site-functions/_meson"
}
