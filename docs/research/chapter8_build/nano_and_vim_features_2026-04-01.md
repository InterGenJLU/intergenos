# Nano and Vim Feature Research

**Date:** April 1, 2026
**Context:** Adding nano to InterGenOS core, improving vim configuration

---

## Nano 8.7.1

### Decision
Added to InterGenOS core (not in LFS, from BLFS 13.0). Builds after vim in the Chapter 8 sequence.

### Build Dependencies
Only ncurses (wide-char) and gettext — both already in LFS Chapter 8. Zero additional packages.

### Configure
```bash
./configure --prefix=/usr --sysconfdir=/etc --enable-utf8 --docdir=/usr/share/doc/nano-8.7.1
```

### Features Available with LFS-Only Dependencies
All compiled in by default (no `--enable-tiny`):
- Syntax highlighting (42 languages, shipped as `.nanorc` files in `/usr/share/nano/`)
- Line numbers (Alt+N toggle or `set linenumbers`)
- Undo/redo, word completion (Ctrl+]), regex search
- File browser, multiple buffers, tab completion
- Mouse support (terminal), position/search history
- Comment/uncomment (Alt+3), linter/formatter framework
- UTF-8 (with `--enable-utf8`)

### Features Needing BLFS Packages at Runtime
- Spell checking: needs `hunspell` or `aspell` (fails gracefully without them)
- Console mouse without X: needs `gpm`
- File type auto-detection via libmagic: needs `file` package at build time

### System-Wide /etc/nanorc
Configured with: syntax highlighting for all bundled types, line numbers, autoindent,
position history, smooth scrolling, constant status bar, smart home, 4-space tabs.
All settings overridable per-user via `~/.nanorc`.

---

## Vim 9.2.0078

### Build Change
Added `--with-tlib=ncursesw` to explicitly link against wide-character ncurses (BLFS does this).
Default `./configure --prefix=/usr` already builds with `huge` features.

### Feature Set (already compiled in with default huge)
- Syntax highlighting (900+ syntax files in `$VIMRUNTIME/syntax/`)
- Spell checking (needs .spl dictionary files downloaded separately)
- Terminal emulator (`:terminal`)
- Persistent undo, sessions, folding
- Multibyte (always on, cannot be disabled)
- Mouse support, color schemes (~20 built-in)
- File type detection, plugin support

### Features NOT Added (BLFS territory)
- `--enable-python3interp` — plugin authors only
- `--enable-gui=gtk3` — no GUI in base
- `--enable-cscope` — needs cscope binary

### System-Wide /etc/vimrc
Improved from LFS default to include:
- Line numbers, search highlighting, always-visible status line
- UTF-8 encoding, autoindent, smart tabs
- Persistent undo with centralized `~/.vim/undodir/` (dirs auto-created)
- Centralized swap files in `~/.vim/swap//` (safety without clutter)
- Mouse disabled by default (terminal paste works correctly)
- Dark background for xterm/256color terminals
- Every setting commented for transparency (Prime Directive)
- All settings overridable via `~/.vimrc`

### Sources
- LFS 13.0 Section 8.75 (Vim)
- BLFS 13.0 (Vim rebuild with GTK3, nano 8.7.1)
- Vim configure.ac defaults analysis
- Arch Linux PKGBUILD for readline/vim
