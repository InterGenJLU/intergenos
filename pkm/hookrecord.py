# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""pkm hookrecord — record what an archive lifecycle hook writes.

Sealing a recipe's lifecycle functions into the signed archive (the seal
seam in igos-build/hookseal.py) closes EXECUTION: the hook now runs on a
pkm-only install instead of only on a source build. It does not close
OWNERSHIP. A hook that generates a cache, an index, or a machine-unique
file writes payload that no manifest declares, so pkm's own record says
nobody owns it: `pkm provides` answers "no package", `pkm remove` leaves
the file behind, `pkm verify` never checks it, and the squashfs ownership
gate (scripts/check-squashfs-ownership.py, which reads the files table)
either refuses the image or the file ships unaccounted. Closing execution
without closing ownership moves the unowned-file class rather than
eliminating it.

The measurement here is a before/after filesystem diff taken around the
hook — the same shape igos-build/tracker.py already uses to attribute a
direct_install build (fs_snapshot / diff_snapshots). Two properties of
that shape are the reason it is the right one:

  * It is EXACT. It observes what the hook actually wrote, not what the
    hook was predicted to write. A bounded snapshot — walk only the dirs
    a hook is expected to touch — is cheaper and silently misses the
    write that matters most, which is precisely the write nobody
    predicted.
  * ctime is the modification signal. The kernel bumps st_ctime on every
    content or metadata write and userland cannot set it; mtime survives
    `cp -a`, `tar -p` and `touch -r`. A hook that rewrites a file and
    restores its mtime is still observed.

The cost — a whole-tree walk, twice — is affordable for exactly one
reason: it is paid ONLY by packages that actually ship a lifecycle hook.
That is one recipe today and a small opt-in set by design (the hook
contract is maintenance-only), so ~99% of installs pay nothing. The
caller is responsible for that gating; see hooks.archive_lifecycle_hook_path.

ATTRIBUTION RULE: a path this package may claim is one the hook CREATED
and that no package already owns. Two exclusions are deliberate:

  * A created path already owned by another package is NOT claimed. The
    hook re-created a file its real owner records (the owner's own
    verify already tracks it); claiming it would fabricate co-ownership
    and let `pkm remove <this package>` unlink another package's file.
  * A MODIFIED path is not claimed. It existed before the hook ran, so
    the hook did not bring it into being; if it is unowned it was
    already unowned, which is a pre-existing condition for the ownership
    gate to surface rather than something to silently absorb under
    whichever package happened to run a hook near it. Modified paths are
    still counted and reported, so the fact is never lost.
"""

import os

# Root-relative directories the snapshot never descends into. Every entry
# is here for a stated reason; this list is the single definition of what
# the diff does not see, and it is reported alongside the diff so a
# non-observation is visible rather than assumed.
SNAPSHOT_PRUNE_DEFAULT = frozenset({
    # Kernel-virtual and volatile trees. No package payload lives here and
    # their contents change under the walk's own feet.
    "proc", "sys", "dev", "run", "tmp", "var/tmp",
    # pkm's own state: the database and its WAL sidecars, the text
    # manifests, and the archive corpus. The transaction that is running
    # this hook writes all three, so observing them would attribute pkm's
    # bookkeeping to the package being installed.
    "var/lib/igos",
    # Chronicle's content-addressed restore-point store. Runtime state
    # only — zero paths under it are package-claimed (the same finding
    # build-squashfs.sh Step 3 acts on) — and a restore-point capture
    # landing inside the hook window would otherwise sweep an entire
    # content-addressed blob store in as this package's payload.
    "var/lib/chronicle",
    # Log output is operational noise, not payload.
    "var/log",
    # User state. A hook writing into a home directory is writing the
    # user's files, and pkm claiming ownership of a user's files inverts
    # the relationship the system exists to maintain.
    "root", "home",
})

# Snapshot entry kinds.
KIND_FILE = "f"
KIND_LINK = "l"
KIND_DIR = "d"


def fs_snapshot(root, prune=None):
    """Snapshot the tree under `root` as rel_path -> (kind, size, ctime_ns).

    Args:
        root: install root to walk (str or Path).
        prune: root-relative POSIX directory paths not to descend into.
            Defaults to SNAPSHOT_PRUNE_DEFAULT.

    Returns:
        dict mapping the root-relative POSIX path (no leading slash) to
        (kind, st_size, st_ctime_ns). Directories carry (KIND_DIR, 0, 0):
        their presence is the only thing tracked, because a directory's
        own stat changes whenever any child does and that is already
        observed on the child.
    """
    root = os.path.abspath(str(root))
    prune_set = frozenset(
        p.strip("/") for p in
        (SNAPSHOT_PRUNE_DEFAULT if prune is None else prune)
    )
    snapshot = {}
    if not os.path.isdir(root):
        return snapshot
    for dirpath, dirnames, filenames in os.walk(root, topdown=True,
                                                followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        # Never descend into a pruned directory. Rewriting dirnames in
        # place is the documented os.walk prune idiom (topdown only).
        kept = []
        for dn in dirnames:
            rel = f"{rel_dir}/{dn}" if rel_dir else dn
            if rel in prune_set:
                continue
            kept.append(dn)
            if os.path.islink(os.path.join(dirpath, dn)):
                # A symlinked directory is a payload entry in its own
                # right (the usr-merge compatibility links are exactly
                # this), and os.walk will not descend it with
                # followlinks=False, so record it here.
                try:
                    st = os.lstat(os.path.join(dirpath, dn))
                except OSError:
                    continue
                snapshot[rel] = (KIND_LINK, st.st_size, st.st_ctime_ns)
            else:
                snapshot[rel] = (KIND_DIR, 0, 0)
        dirnames[:] = kept
        for fn in filenames:
            rel = f"{rel_dir}/{fn}" if rel_dir else fn
            path = os.path.join(dirpath, fn)
            try:
                st = os.lstat(path)
            except OSError:
                # A file that vanished between the walk and the stat is
                # not observable either way; it cannot be claimed.
                continue
            kind = KIND_LINK if os.path.islink(path) else KIND_FILE
            snapshot[rel] = (kind, st.st_size, st.st_ctime_ns)
    return snapshot


def diff_snapshots(before, after):
    """Split two fs_snapshots into (created_files, created_dirs, modified).

    created_files: regular files and symlinks present only in `after`,
        sorted. These are what the hook brought into being.
    created_dirs: directories present only in `after`, sorted, with
        trailing "/" so they carry pkm's directory convention. Registering
        them is what lets `pkm remove` clean up after the hook instead of
        leaving the empty-directory class the ownership gate flags.
    modified: paths present in both whose (size, ctime_ns) changed, sorted.
        Reported, never claimed — see the module docstring's attribution
        rule.
    """
    created_files = []
    created_dirs = []
    modified = []
    for path, entry in after.items():
        prior = before.get(path)
        kind = entry[0]
        if prior is None:
            if kind == KIND_DIR:
                created_dirs.append(path + "/")
            else:
                created_files.append(path)
            continue
        if kind == KIND_DIR:
            continue
        if prior[1:] != entry[1:]:
            modified.append(path)
    return sorted(created_files), sorted(created_dirs), sorted(modified)


def claimable(created, owned_paths):
    """Split created paths into (claimable, foreign) against known owners.

    owned_paths: set of root-relative paths (no leading slash, directories
        WITHOUT the trailing slash, matching the files table's storage
        form) already owned by some installed package.

    Returns (claimable, foreign), both sorted. `foreign` is returned rather
    than dropped so the caller can report that the hook wrote over another
    package's file — a fact that matters and that a silent drop would lose.
    """
    claim, foreign = [], []
    for path in created:
        (foreign if path.rstrip("/") in owned_paths else claim).append(path)
    return sorted(claim), sorted(foreign)


def format_record_summary(claimed, foreign, modified, own_modified=()):
    """One-line-per-fact summary for the install transcript.

    Returns a list of message lines (possibly empty), matching the
    HookResult.messages convention in pkm/hooks.py.

    own_modified: paths the hook modified that THIS package's own payload
        already owns — reclassified to the hook-generated content class by
        the caller (the D-9b rule; see installer._record_hook_outputs).
        `modified` is every OTHER modified path (ownership and content
        class unchanged, reported so the fact is never lost).
    """
    lines = []
    if claimed:
        lines.append(
            f"  hook[record] registered {len(claimed)} hook-generated "
            f"path(s) to the owning package"
        )
    if foreign:
        lines.append(
            f"  hook[record] {len(foreign)} path(s) the hook created are "
            f"already owned by another package — left with their owner: "
            f"{', '.join(foreign[:5])}"
            + (" …" if len(foreign) > 5 else "")
        )
    if own_modified:
        lines.append(
            f"  hook[record] {len(own_modified)} of the package's own "
            f"payload file(s) rewritten by its hook — content is "
            f"hook-managed from here on (existence-checked): "
            f"{', '.join(own_modified[:5])}"
            + (" …" if len(own_modified) > 5 else "")
        )
    if modified:
        lines.append(
            f"  hook[record] {len(modified)} existing path(s) modified by "
            f"the hook (ownership unchanged)"
        )
    return lines
