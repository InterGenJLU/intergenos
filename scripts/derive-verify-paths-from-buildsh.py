#!/usr/bin/env python3
"""Derive verify_paths for packages NOT covered by LFS/BLFS book data.

Inputs:
  - scripts/verify-paths-batches-2026-05-15.json  (batch_a list)
  - packages/<tier>/<name>/build.sh               (do_install body)
  - packages/<tier>/<name>/package.yml            (target file)

Strategy:
  1. Skip if package.yml already has verify_paths.
  2. Scan build.sh for explicit ${DESTDIR}/usr/... paths.
  3. Pick 2-3 candidate paths, priority: /usr/bin > /usr/sbin > /usr/lib*.so >
     /usr/share/<name>/ > /usr/lib/<name>/ > /etc/<name>* > fallbacks.
  4. Heuristics for common package types when build.sh is opaque
     (make DESTDIR=$DESTDIR install with no explicit paths in script):
       - *-icon-theme  -> /usr/share/icons/<Name>
       - *-cursor-theme / *-cursors / cursor-theme  -> /usr/share/icons/<Name>
       - *-gtk-theme / *-gtk3-theme / *-gtk4-theme / nordic-theme /
         catppuccin / orchis / dracula  -> /usr/share/themes/<Name>
       - font-* / fonts -> /usr/share/fonts/X11/<lastpart>
       - lib<name> -> /usr/lib/lib<name>.so + /usr/include/<name>/
       - perl-<x>  -> /usr/lib/perl5/site_perl
       - python-<x> / dbus-python  -> /usr/lib/python3.13/site-packages/<x>
       - intergenos-extensions-* / gnome-shell-extensions  -> /usr/share/gnome-shell/extensions/<id>
       - *-pass1 / *-pass2 / *-bootstrap -> mirror sibling non-bootstrap
       - linux-kernel -> /boot/vmlinuz-* + /usr/lib/modules/*
       - intel-ucode -> /lib/firmware/intel-ucode + /boot/intel-ucode.img-equivalent
       - sub-name xorg lib (libX*, libXft, etc) -> /usr/lib/lib<n>.so*
       - dbus / dbus-pass2 -> /usr/bin/dbus-daemon
       - go / rust / nodejs -> /usr/bin/<n> or /opt/<n>/bin/<n>
  5. Output a verify_paths YAML block + a tag for review confidence:
       - 'extracted' = found in build.sh DESTDIR
       - 'heuristic' = derived from naming pattern
       - 'skip-tbd'  = could not derive, emit TBD comment

Usage:
  python3 scripts/derive-verify-paths-from-buildsh.py [--dry-run]
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BATCH_JSON = REPO / 'scripts/verify-paths-batches-2026-05-15.json'

# DESTDIR path extractor — matches things like ${DESTDIR}/usr/bin/foo,
# $DESTDIR/usr/lib/libfoo.so, "${DESTDIR}/usr/share/<x>", etc.
DESTDIR_RE = re.compile(r'\$\{?DESTDIR\}?(/[^\s\'"\\]+)')
# also pkgdir / D / destdir (some recipes use synonyms — keep narrow)


def has_verify_paths(pkg_yml: Path) -> bool:
    text = pkg_yml.read_text(errors='replace')
    return bool(re.search(r'^verify_paths:\s*$', text, re.MULTILINE))


GENERIC_PARENTS = {
    '/usr/bin', '/usr/sbin', '/usr/lib', '/usr/lib64', '/usr/libexec',
    '/usr/share/icons', '/usr/share/themes', '/usr/share/fonts',
    '/usr/share/fonts/X11', '/usr/share/man', '/usr/share/man/man1',
    '/usr/share/man/man3', '/usr/share/man/man5', '/usr/share/man/man8',
    '/usr/share/doc', '/usr/share/applications', '/usr/share/info',
    '/usr/share/locale', '/usr/share/pkgconfig', '/usr/share/help',
    '/etc/ssl/certs', '/etc/dbus-1/system.d', '/var/lib/dbus',
    '/lib/firmware', '/usr/include',
}


def extract_destdir_paths(build_sh: Path):
    """Return ordered de-duplicated /usr/... (or /etc, /opt, /boot, /lib) paths.

    Filters out paths with shell globs/vars after the DESTDIR prefix,
    trims trailing punctuation/slashes, rejects generic-parent dirs
    and hidden (.git*) files."""
    if not build_sh.exists():
        return []
    text = build_sh.read_text(errors='replace')
    raw = DESTDIR_RE.findall(text)
    out = []
    seen = set()
    for p in raw:
        # Trim trailing punct + slash
        p = p.rstrip('.,;:\'"/ ')
        # Reject if contains shell var or glob or unmatched braces
        if any(c in p for c in '${}*?[]\\'):
            continue
        # Skip if depth < 3 (e.g. just /usr/lib or /usr/bin)
        depth = p.count('/')
        if depth < 3:
            continue
        # Reject generic-parent dirs (e.g. /usr/share/icons, /usr/share/themes)
        if p in GENERIC_PARENTS:
            continue
        # Reject paths containing /. (hidden, e.g. .git, .github, .gitignore)
        if '/.' in p:
            continue
        # Reject duplicates (post-strip)
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def prioritize(paths):
    """Sort by usefulness: /usr/bin > /usr/sbin > .so libs > share/lib dirs > etc."""
    def score(p):
        if p.startswith('/usr/bin/'):
            return (0, len(p))
        if p.startswith('/usr/sbin/'):
            return (1, len(p))
        if '.so' in p and p.startswith('/usr/lib'):
            return (2, len(p))
        if p.startswith('/usr/lib/'):
            return (3, len(p))
        if p.startswith('/usr/libexec/'):
            return (4, len(p))
        if p.startswith('/usr/share/'):
            return (5, len(p))
        if p.startswith('/etc/'):
            return (6, len(p))
        if p.startswith('/boot/'):
            return (7, len(p))
        if p.startswith('/lib/'):
            return (8, len(p))
        if p.startswith('/opt/'):
            return (9, len(p))
        return (10, len(p))
    return sorted(set(paths), key=score)


def heuristic(tier: str, name: str, build_sh_text: str):
    """Heuristic fallback when DESTDIR extraction yields nothing usable."""
    paths = []

    # --- icon + cursor themes (by full package name for known caps) ---
    icon_cursor_map = {
        'papirus-icon-theme': 'Papirus',
        'whitesur-icon-theme': 'WhiteSur-dark',
        'tela-icon-theme': 'Tela',
        'fluent-icon-theme': 'Fluent',
        'bibata-cursor-theme': 'Bibata-Modern-Classic',
        'phinger-cursors': 'phinger-cursors-light',
    }
    if name in icon_cursor_map:
        return [f'/usr/share/icons/{icon_cursor_map[name]}'], 'heuristic'

    if any(name.endswith(s) for s in ['-icon-theme', '-icons', '-cursor-theme', '-cursors']):
        stem = name
        for suf in ['-icon-theme', '-cursor-theme', '-icons', '-cursors']:
            if stem.endswith(suf):
                stem = stem[:-len(suf)]
                break
        return [f'/usr/share/icons/{stem.capitalize()}'], 'heuristic'

    if name in ('nordic-theme',):
        return ['/usr/share/themes/Nordic'], 'heuristic'

    if name in ('macos-cursor-theme',):
        return ['/usr/share/icons/macOS'], 'heuristic'

    # Match GTK themes by FULL package name (cap-map keyed on package name)
    gtk_theme_map = {
        'adw-gtk3-theme': 'adw-gtk3-dark',
        'whitesur-gtk-theme': 'WhiteSur-Dark',
        'graphite-gtk-theme': 'Graphite-Dark',
        'orchis-theme': 'Orchis-Dark',
        'catppuccin-gtk-theme': 'Catppuccin-Mocha-Standard-Blue-Dark',
        'fluent-gtk-theme': 'Fluent-Dark',
        'dracula-gtk-theme': 'Dracula',
    }
    if name in gtk_theme_map:
        return [f'/usr/share/themes/{gtk_theme_map[name]}'], 'heuristic'

    # --- intergenos-extensions super-pkgs (one extension per dir) ---
    if name.startswith('intergenos-extensions-'):
        return ['/usr/share/gnome-shell/extensions'], 'heuristic'

    # --- fonts ---
    if name.startswith('font-') or name == 'fonts':
        font_dir = name.replace('font-', '').replace('encodings', 'encodings')
        return [f'/usr/share/fonts/X11/{font_dir}'], 'heuristic'

    # --- perl modules ---
    if name.startswith('perl-'):
        return ['/usr/lib/perl5/site_perl'], 'heuristic'

    # --- python modules ---
    if name in ('dbus-python', 'pygobject3', 'argcomplete', 'cffi', 'pycparser',
                'pyelftools', 'python-cryptography', 'python-pefile',
                'setuptools-rust', 'setuptools-scm', 'hatch-vcs'):
        return ['/usr/lib/python3.13/site-packages'], 'heuristic'

    # --- xorg libs / generic lib<name> packages ---
    # IMPORTANT: KNOWN-dict consulted BEFORE this generic fallback (see KNOWN
    # check below). The lib-pattern is the LAST resort for unmatched libs.
    LIB_GENERIC_DEFER = True  # placeholder; KNOWN runs first now

    # --- pass1/pass2/bootstrap ---
    if any(name.endswith(s) for s in ['-pass1', '-pass2', '-bootstrap']):
        for suf in ['-pass1', '-pass2', '-bootstrap']:
            if name.endswith(suf):
                base = name[:-len(suf)]
                # Just point at the same shape as base would
                if base.startswith('lib') and len(base) > 3 and base[3:4].isupper():
                    return [f'/usr/lib/{base}.so'], 'heuristic'
                if base in ('freetype2',):
                    return ['/usr/lib/libfreetype.so'], 'heuristic'
                if base in ('glib2',):
                    return ['/usr/lib/libglib-2.0.so'], 'heuristic'
                if base in ('dbus',):
                    return ['/usr/bin/dbus-daemon'], 'heuristic'
                if base in ('linux-kernel',):
                    return ['/boot/vmlinuz-6.18.10-igos', '/usr/lib/modules/6.18.10-igos'], 'heuristic'
                if base in ('gobject-introspection',):
                    return ['/usr/lib/libgirepository-1.0.so'], 'heuristic'
                return [f'/usr/bin/{base}'], 'heuristic'

    # --- specific known names ---
    KNOWN = {
        'linux-kernel': ['/boot/vmlinuz-6.18.10-igos', '/usr/lib/modules/6.18.10-igos'],
        'intel-ucode': ['/lib/firmware/intel-ucode'],
        'iucode-tool': ['/usr/sbin/iucode_tool'],
        'iana-etc': ['/etc/protocols', '/etc/services'],
        'go': ['/usr/lib/go/bin/go', '/usr/bin/go'],
        'rust': ['/usr/bin/rustc', '/usr/bin/cargo'],
        'nodejs': ['/usr/bin/node', '/usr/bin/npm'],
        'gnu-efi': ['/usr/lib/gnuefi'],
        'efitools': ['/usr/bin/cert-to-efi-sig-list', '/usr/bin/sign-efi-sig-list'],
        'sbsigntool': ['/usr/bin/sbsign', '/usr/bin/sbverify'],
        'mokutil': ['/usr/bin/mokutil'],
        'nftables': ['/usr/sbin/nft', '/etc/nftables.conf'],
        'libnftnl': ['/usr/lib/libnftnl.so'],
        'apparmor': ['/sbin/apparmor_parser', '/usr/sbin/aa-status'],
        'bash-completion': ['/usr/share/bash-completion'],
        'busybox-static': ['/usr/bin/busybox'],
        'ca-certificates': ['/etc/ssl/certs/ca-certificates.crt'],
        'cyrus-sasl': ['/usr/lib/libsasl2.so'],
        'dbus': ['/usr/bin/dbus-daemon', '/etc/dbus-1/system.d'],
        'elfutils': ['/usr/bin/eu-readelf'],
        'fuse3': ['/usr/lib/libfuse3.so', '/usr/bin/fusermount3'],
        'glib2': ['/usr/lib/libglib-2.0.so', '/usr/bin/glib-compile-schemas'],
        'gnupg2': ['/usr/bin/gpg', '/usr/bin/gpgconf'],
        'help2man': ['/usr/bin/help2man'],
        'iso-codes': ['/usr/share/iso-codes'],
        'lzip': ['/usr/bin/lzip'],
        'man-pages': ['/usr/share/man/man1'],
        'mandoc': ['/usr/bin/mandoc'],
        'mitkrb': ['/usr/bin/krb5-config', '/usr/lib/libkrb5.so'],
        'rpm': ['/usr/bin/rpm'],
        'util-macros': ['/usr/share/aclocal/xorg-macros.m4'],
        'which': ['/usr/bin/which'],
        'xml-parser': ['/usr/lib/perl5/site_perl'],
        'xorgproto': ['/usr/share/pkgconfig/xorgproto.pc'],
        'xxhash': ['/usr/bin/xxhsum', '/usr/lib/libxxhash.so'],
        'gobject-introspection': ['/usr/lib/libgirepository-1.0.so', '/usr/bin/g-ir-scanner'],
        'freetype2': ['/usr/lib/libfreetype.so'],
        # base/
        'atop': ['/usr/bin/atop'],
        'btop': ['/usr/bin/btop'],
        'htop': ['/usr/bin/htop'],
        'iotop': ['/usr/sbin/iotop'],
        'parallel': ['/usr/bin/parallel'],
        'rdfind': ['/usr/bin/rdfind'],
        'strace': ['/usr/bin/strace'],
        # ai/
        'llama-cpp': ['/usr/bin/llama-cli', '/usr/bin/llama-server'],
        # desktop/specific
        'a52dec': ['/usr/bin/a52dec', '/usr/lib/liba52.so'],
        'bdftopcf': ['/usr/bin/bdftopcf'],
        'cairomm': ['/usr/lib/libcairomm-1.0.so'],
        'cdparanoia': ['/usr/bin/cdparanoia', '/usr/lib/libcdda_paranoia.so'],
        'dart-sass': ['/usr/lib/dart-sass/sass', '/usr/bin/sass'],
        'dconf': ['/usr/bin/dconf', '/usr/lib/libdconf.so'],
        'editorconfig-core-c': ['/usr/lib/libeditorconfig.so', '/usr/bin/editorconfig'],
        'encodings': ['/usr/share/fonts/X11/encodings'],
        'folks': ['/usr/lib/libfolks.so'],
        'font-util': ['/usr/bin/bdftruncate'],
        'gcr4': ['/usr/lib/libgcr-4.so'],
        'geoclue2': ['/usr/libexec/geoclue', '/etc/geoclue/geoclue.conf'],
        'glibmm2': ['/usr/lib/libglibmm-2.68.so'],
        'gnome-calendar': ['/usr/bin/gnome-calendar'],
        'gnome-characters': ['/usr/bin/gnome-characters'],
        'gnome-clocks': ['/usr/bin/gnome-clocks'],
        'gnome-contacts': ['/usr/bin/gnome-contacts'],
        'gnome-font-viewer': ['/usr/bin/gnome-font-viewer'],
        'gnome-music': ['/usr/bin/gnome-music'],
        'gnome-text-editor': ['/usr/bin/gnome-text-editor'],
        'gnome-user-docs': ['/usr/share/help'],
        'grilo': ['/usr/lib/libgrilo-0.3.so'],
        'grilo-plugins': ['/usr/lib/grilo-0.3'],
        'gtk3': ['/usr/lib/libgtk-3.so', '/usr/bin/gtk3-demo'],
        'gtk4': ['/usr/lib/libgtk-4.so', '/usr/bin/gtk4-demo'],
        'gtkmm4': ['/usr/lib/libgtkmm-4.0.so'],
        'iceauth': ['/usr/bin/iceauth'],
        'imagemagick': ['/usr/bin/magick'],
        'ladspa-sdk': ['/usr/include/ladspa.h', '/usr/bin/listplugins'],
        'lcms2': ['/usr/lib/liblcms2.so'],
        'libadwaita1': ['/usr/lib/libadwaita-1.so'],
        'libbluray': ['/usr/lib/libbluray.so'],
        'libcbor': ['/usr/lib/libcbor.so'],
        'libcdio-paranoia': ['/usr/lib/libcdio_paranoia.so'],
        'libdex': ['/usr/lib/libdex-1.so'],
        'libfido2': ['/usr/lib/libfido2.so'],
        'libfontenc': ['/usr/lib/libfontenc.so'],
        'libgphoto2': ['/usr/lib/libgphoto2.so'],
        'libhandy1': ['/usr/lib/libhandy-1.so'],
        'libimobiledevice': ['/usr/lib/libimobiledevice-1.0.so'],
        'libimobiledevice-glue': ['/usr/lib/libimobiledevice-glue-1.0.so'],
        'libmediaart': ['/usr/lib/libmediaart-2.0.so'],
        'libmsgraph': ['/usr/lib/libmsgraph-1.so'],
    }

    if name in KNOWN:
        return KNOWN[name], 'heuristic'

    # --- generic lib<name> packages (after KNOWN has had its say) ---
    if name.startswith('lib') and len(name) > 3:
        return [f'/usr/lib/{name}.so'], 'heuristic'

    # --- generic fallback: assume /usr/bin/<name> ---
    return [f'/usr/bin/{name}'], 'heuristic-weak'


def derive_paths(tier: str, name: str):
    pkg_dir = REPO / 'packages' / tier / name
    pkg_yml = pkg_dir / 'package.yml'
    build_sh = pkg_dir / 'build.sh'

    if not pkg_yml.exists():
        return None, 'no-package-yml'
    if has_verify_paths(pkg_yml):
        return None, 'already-has-verify-paths'

    # Try extraction first
    extracted = extract_destdir_paths(build_sh)
    if extracted:
        ranked = prioritize(extracted)
        # Pick top 2-3, but try to ensure type diversity
        out = ranked[:3]
        # If all 3 are same prefix, OK; just dedupe
        return out, 'extracted'

    # Fall back to heuristic
    build_sh_text = build_sh.read_text(errors='replace') if build_sh.exists() else ''
    paths, tag = heuristic(tier, name, build_sh_text)
    return paths, tag


def write_verify_paths(pkg_yml: Path, paths, dry_run=False):
    text = pkg_yml.read_text()
    if not text.endswith('\n'):
        text += '\n'
    block = '\nverify_paths:\n' + ''.join(f'  - {p}\n' for p in paths)
    new = text + block
    if dry_run:
        return new
    pkg_yml.write_text(new)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    data = json.loads(BATCH_JSON.read_text())
    batch = data['batch_a']

    stats = {'extracted': 0, 'heuristic': 0, 'heuristic-weak': 0, 'skip-already': 0, 'skip-no-yml': 0}
    weak_list = []

    for entry in batch:
        tier, name = entry['tier'], entry['name']
        paths, tag = derive_paths(tier, name)
        if paths is None:
            if tag == 'already-has-verify-paths':
                stats['skip-already'] += 1
            else:
                stats['skip-no-yml'] += 1
                print(f'SKIP {tier}/{name}: {tag}')
            continue
        stats[tag] += 1
        if tag == 'heuristic-weak':
            weak_list.append(f'{tier}/{name}: {paths}')
        write_verify_paths(REPO / 'packages' / tier / name / 'package.yml', paths, dry_run=args.dry_run)
        if args.dry_run:
            print(f'{tag:20} {tier}/{name:40} {paths}')

    print(f'\n=== STATS ===')
    for k, v in stats.items():
        print(f'  {k:20} {v}')
    if weak_list:
        print(f'\n=== WEAK (review carefully) ===')
        for w in weak_list:
            print(f'  {w}')


if __name__ == '__main__':
    main()
