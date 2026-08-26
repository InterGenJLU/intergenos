# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Auto-derive verify_paths from installed-file lists at build time.

Called from the builder's track phase after a successful pkg_manifest /
pkg_archive_from_files. Picks 2-3 load-bearing paths the package installs
and writes them to packages/<tier>/<name>/auto-verify-paths.json. The
pre-squashfs-audit.py script falls back to this sidecar when package.yml
lacks a verify_paths field.

The same file also carries `produced_paths`: the WHOLE install list, not a
sample. scripts/check-aspirational-stubs.py reads it to answer "does this
package produce the path this surface cites?" instead of guessing from the
package's name — its rule (a) resolves any basename sharing a substring with
the package name, so a surface owned by foo citing /usr/bin/foo-does-not-exist
resolved (lane 13 finding c1, 2026-08-24). A sample cannot answer that
question: refusing a path requires knowing the package installs nothing of
that name, which only the complete list establishes.

Size, measured 2026-08-25 against this workstation's pkm database: 782804
non-directory file rows across 951 installed packages, 45.91 MB of raw path
text, median 44 paths per package and a maximum of 119657 (linux-kernel). The
file is written next to the recipe and is not tracked — no auto-verify-paths.json
is committed anywhere in the tree today — so this changes the SIZE of an
artifact the builder already writes per package, not whether it writes one.
Only the records for packages that own a citing surface are worth committing.

The sidecar is a JSON file (not YAML) to make the human-vs-machine
distinction obvious — verify_paths in package.yml is hand-curated /
book-derived; auto-verify-paths.json is filesystem-truth-derived.

Design:
- Don't overwrite package.yml — that's a source artifact, sidecar is
  generated artifact.
- Don't overwrite an existing sidecar if its content is identical (avoids
  churn-in-VCS on every rebuild).
- Sidecar is .gitignore'd at the packages level (callers should ensure).
"""

import json
import re
from pathlib import Path


SIDECAR_NAME = 'auto-verify-paths.json'

# Path prefix priorities for picking load-bearing files.
# Earlier entries win.
_BIN_PREFIXES = ('usr/bin/', 'usr/sbin/', 'bin/', 'sbin/')
_LIB_PREFIXES = ('usr/lib/', 'usr/lib64/', 'lib/', 'lib64/')
_LIBEXEC_PREFIXES = ('usr/libexec/',)
_FW_PREFIXES = ('usr/lib/firmware/', 'lib/firmware/')
_SHARE_PREFIXES = ('usr/share/',)
_ETC_PREFIXES = ('etc/',)

# Path patterns to deprioritize — present in many packages but not
# identity-signal for THIS package.
_DEPRIO_SUBSTRINGS = (
    '/man/man',          # man pages — many tiny files
    '/locale/',          # locale data — many tiny files
    '/__pycache__/',     # python bytecode — auto-regenerated
    '.pyc',              # python bytecode
    '/doc/',             # docs — present but not signal
    '/bash-completion/', # generated
    '/zsh/site-functions/',  # generated
    '/applications/',    # .desktop files — too many of these
    '/icons/',           # icons — too many of these
)


def _looks_like_lib(path):
    """Identify shared/static libraries by filename pattern."""
    base = path.rsplit('/', 1)[-1]
    if base.startswith('lib') and ('.so' in base or base.endswith('.a')):
        return True
    if base.endswith('.so'):
        return True
    # versioned: libfoo.so.X.Y.Z
    if re.match(r'^lib[^/]+\.so(?:\.\d+)+$', base):
        return True
    return False


def derive_verify_paths(file_list, pkg_name, max_paths=3):
    """Pick up to max_paths load-bearing files from a package's install list.

    Args:
        file_list: list of relative paths (e.g., 'usr/bin/bzip2',
                   'usr/lib/libbz2.so'). Leading slash optional.
        pkg_name: the package's name (for identity-match priority).
        max_paths: cap on returned paths (default 3).

    Returns:
        list of absolute paths starting with /, prefixed with the path
        the package will be at on the live system (i.e., the staging dir
        is stripped by the caller before passing in).
    """
    # Normalize input
    paths = []
    seen = set()
    for p in file_list:
        if not isinstance(p, str):
            continue
        n = p.lstrip('/')
        if not n or n.endswith('/'):
            continue  # directories
        if n in seen:
            continue
        seen.add(n)
        paths.append(n)

    if not paths:
        return []

    name_l = pkg_name.lower()

    def score(p):
        s = 0
        pl = p.lower()
        # Massive boost for filename containing the package name
        last = pl.rsplit('/', 1)[-1]
        if last == name_l:
            s += 100
        elif name_l in last:
            s += 50
        elif name_l in pl:
            s += 20
        # Category boosts
        if any(p.startswith(pre) for pre in _BIN_PREFIXES):
            s += 30
        elif _looks_like_lib(p) and any(p.startswith(pre) for pre in _LIB_PREFIXES):
            s += 25
        elif any(p.startswith(pre) for pre in _FW_PREFIXES):
            s += 22
        elif any(p.startswith(pre) for pre in _LIBEXEC_PREFIXES):
            s += 20
        elif any(p.startswith(pre) for pre in _ETC_PREFIXES):
            s += 12
        elif any(p.startswith(pre) for pre in _SHARE_PREFIXES):
            s += 8
        # Deprioritize noise
        if any(sub in p for sub in _DEPRIO_SUBSTRINGS):
            s -= 30
        # Penalize very long paths (deep noise)
        depth = p.count('/')
        if depth > 5:
            s -= 5 * (depth - 5)
        return s

    ranked = sorted(paths, key=score, reverse=True)
    picked = []
    for p in ranked:
        if len(picked) >= max_paths:
            break
        picked.append('/' + p)
    return picked


def normalize_produced_paths(file_list):
    """The complete install list, as absolute paths, deduplicated and sorted.

    Sorted so a rebuild that installs the same files rewrites the same bytes:
    the sidecar is compared before it is written, and an unstable order would
    make every build report a change.
    """
    seen = set()
    out = []
    for p in file_list or ():
        if not isinstance(p, str):
            continue
        n = p.lstrip('/')
        if not n or n.endswith('/'):
            continue  # directories are not produced FILES
        if n in seen:
            continue
        seen.add(n)
        out.append('/' + n)
    out.sort()
    return out


def write_sidecar(pkg_dir, paths, produced_paths=None):
    """Write the auto-verify-paths sidecar next to package.yml.

    Args:
        pkg_dir: Path to packages/<tier>/<name>/
        paths: list of absolute paths (the 2-3 identity-signal sample)
        produced_paths: the complete install list, or None to omit the key

    Returns:
        True if written/updated, False if unchanged.
    """
    if not paths:
        return False
    sidecar = Path(pkg_dir) / SIDECAR_NAME
    new_payload = {
        'auto_derived': True,
        'verify_paths': paths,
        'comment': (
            'Auto-derived from build-time filesystem snapshot by '
            'igos-build/verify_paths_derive.py. Hand-edit verify_paths '
            'in package.yml to override; this sidecar is a fallback for '
            'the pre-squashfs audit when package.yml lacks the field. '
            'produced_paths, when present, is the COMPLETE list of files this '
            'package installed at that build — the Rule 21 stub gate reads it '
            'to answer a cited path instead of guessing from the package name.'
        ),
    }
    if produced_paths:
        new_payload['produced_paths'] = produced_paths
    new_json = json.dumps(new_payload, indent=2, sort_keys=True) + '\n'
    if sidecar.exists():
        try:
            if sidecar.read_text() == new_json:
                return False  # unchanged — no churn
        except OSError:
            pass
    sidecar.write_text(new_json)
    return True


def derive_and_write_sidecar(pkg, file_list):
    """Convenience: derive verify_paths from file_list, write sidecar.

    Args:
        pkg: a Package object with .name and .template_path
        file_list: list of installed file paths (relative or absolute)

    Returns:
        True if a sidecar was created/updated, False otherwise.
    """
    if not getattr(pkg, 'template_path', None):
        return False
    pkg_dir = Path(pkg.template_path).parent
    if not pkg_dir.is_dir():
        return False
    paths = derive_verify_paths(file_list, pkg.name)
    if not paths:
        return False
    return write_sidecar(pkg_dir, paths, normalize_produced_paths(file_list))
