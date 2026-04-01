#!/bin/bash
# Vim 9.2.0078
# LFS 13.0 Section 8.72

configure() {
    # Change default location of vimrc to /etc
    echo '#define SYS_VIMRC_FILE "/etc/vimrc"' >> src/feature.h

    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    # Tests must be run as non-root and from a terminal
    chown -R tester .
    su tester -c "TERM=xterm-256color LANG=en_US.UTF-8 make -j1 test" &> vim-test.log || true
    echo ""
    echo "=== Vim Test Summary ==="
    grep -E 'Executed|FAILED|ALL DONE' vim-test.log || true
}

install() {
    make install

    # Symlink vi -> vim
    ln -sv vim /usr/bin/vimdiff

    # Create default vimrc
    cat > /etc/vimrc << "VIMEOF"
" Begin /etc/vimrc

" Ensure defaults are set before customizing
source $VIMRUNTIME/defaults.vim

let skip_defaults_vim=1

set nocompatible
set backspace=2
set mouse=
set number

" End /etc/vimrc
VIMEOF
}
