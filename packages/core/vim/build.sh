#!/bin/bash
# Vim 9.2.0078
# LFS 13.0 Section 8.75

configure() {
    # Change default location of vimrc to /etc
    echo '#define SYS_VIMRC_FILE "/etc/vimrc"' >> src/feature.h

    ./configure --prefix=/usr \
        --with-tlib=ncursesw
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    # LFS: exclude test that requires curl/wget (not available during core build)
    sed '/test_plugin_glvs/d' -i src/testdir/Make_all.mak

    # Tests must be run as non-root and from a terminal
    chown -R tester .
    su tester -c "TERM=xterm-256color LANG=en_US.UTF-8 make -j1 test" &> vim-test.log || true
    echo ""
    echo "=== Vim Test Summary ==="
    grep -E 'Executed|FAILED|ALL DONE' vim-test.log || true
}

do_install() {
    make DESTDIR="$DESTDIR" install

    # Symlinks: vi -> vim, man pages
    ln -sv vim "${DESTDIR}/usr/bin/vi"
    for L in "${DESTDIR}"/usr/share/man/{,*/}man1/vim.1; do
        ln -sv vim.1 "$(dirname "$L")/vi.1"
    done

    # Link documentation
    ln -sv ../vim/vim92/doc "${DESTDIR}/usr/share/doc/vim-9.2.0078"

    # Create system-wide vimrc
    mkdir -pv "${DESTDIR}/etc"
    cat > "${DESTDIR}/etc/vimrc" << 'VIMEOF'
" /etc/vimrc — InterGenOS system defaults
" Override any setting in ~/.vimrc
" To see what any option does:  :help 'option-name'

" Load Vim's built-in sensible defaults (incsearch, ruler, wildmenu,
" filetype detection, syntax highlighting, scrolloff, history=200)
source $VIMRUNTIME/defaults.vim
let skip_defaults_vim=1

" --- Core behavior ---
set nocompatible                    " Vim mode, not Vi compatibility
set backspace=indent,eol,start      " Backspace works everywhere in insert mode
set encoding=utf-8                  " Internal character encoding

" --- Display ---
set number                          " Show line numbers
set showcmd                         " Show partial commands in status line
set laststatus=2                    " Always show status line
set showmatch                       " Briefly highlight matching bracket
set wildmenu                        " Tab-completion menu for commands

" --- Search ---
set hlsearch                        " Highlight all search matches
set incsearch                       " Search as you type

" --- Indentation ---
set autoindent                      " Copy indent from current line on new line
set smarttab                        " Tab at line start uses shiftwidth

" --- Mouse ---
" Disabled so terminal paste works without issues.
" Enable with:  :set mouse=a
set mouse=

" --- Terminal colors ---
if (&term == "xterm") || (&term == "putty") || (&term =~# '256color')
  set background=dark
endif

" --- File safety ---
" Centralize swap and undo files (dirs created automatically)
silent! call mkdir($HOME . '/.vim/swap', 'p')
silent! call mkdir($HOME . '/.vim/undodir', 'p')
set directory=~/.vim/swap//         " Swap files in one place
set undofile                        " Persistent undo across sessions
set undodir=~/.vim/undodir          " Undo files in one place
VIMEOF
}
