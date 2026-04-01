#!/bin/bash
# Nano 8.7.1
# BLFS 13.0 — added to InterGenOS core for quick editing
#
# Only needs ncurses (wide-char) and gettext from LFS Chapter 8.
# All features (syntax highlighting, line numbers, undo, regex,
# word completion, file browser, etc.) enabled by default.
# Spell checking compiles in but needs hunspell/aspell at runtime.

configure() {
    ./configure --prefix=/usr      \
        --sysconfdir=/etc          \
        --enable-utf8              \
        --docdir=/usr/share/doc/nano-8.7.1
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    : # No test suite
}

do_install() {
    make DESTDIR="$DESTDIR" install

    # Install documentation
    install -v -m644 doc/{nano.html,sample.nanorc} "${DESTDIR}/usr/share/doc/nano-8.7.1"

    # Create system-wide nanorc with creature comforts
    mkdir -pv "${DESTDIR}/etc"
    cat > "${DESTDIR}/etc/nanorc" << 'NANOEOF'
## /etc/nanorc — InterGenOS system defaults
## Override any setting in ~/.nanorc
## See 'man nanorc' for all options

## Syntax highlighting for all bundled file types
include "/usr/share/nano/*.nanorc"

## Display line numbers in the left margin
set linenumbers

## Show cursor position in the status bar
set constantshow

## Wrap long lines visually (no hard line breaks)
set softwrap

## Auto-indent new lines to match the previous line
set autoindent

## Remember cursor position between editing sessions
set positionlog

## Remember search/replace strings between sessions
set historylog

## Smart Home key — jump to first non-whitespace character
set smarthome

## Show a scrollbar indicator on the right
set indicator

## Tab settings — 4 spaces, convert tabs to spaces
set tabsize 4
set tabstospaces

## Clear status bar messages after one keystroke
set quickblank

## Trim trailing whitespace when saving
set trimblanks

## Display editor state flags in the title bar
set stateflags

## Enable multiple file buffers (open several files at once)
set multibuffer
NANOEOF
}
