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

ctime says that SOMETHING happened to a path, not what. A chmod, a chown, a
touch, an extended attribute (a file capability is one) or a new hard link
moves it exactly as a rewrite does. So for the package's OWN payload, whose
content class the caller may change, the BYTES decide: own_payload_states
records what each own path is (a regular file's sha256, a symbolic link's
target) before the hook, and split_own_changes compares it after. Only a path
whose bytes changed, whose link target changed, or that became another kind of
entry was rewritten. A path whose bytes are the deploy's after a
metadata-only step keeps its content check, and the summary says in one line
what changed instead — a recorder that read that step as a rewrite turned the
byte check off for the file for good.

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

import hashlib
import json
import os
import stat

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


def _file_sha256(path):
    """The sha256 of the regular file at `path`, or None if it cannot be read.

    Opened without following a final symbolic link and without blocking, and
    checked to be a regular file on the open descriptor, so a path swapped for
    a link, a FIFO or a device after its lstat is never read through.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)
    except OSError:
        return None
    finally:
        os.close(fd)


def _xattrs(path):
    """The path's own extended attributes as sorted (name, value) pairs.

    None when they cannot be listed (a filesystem without them), so a listing
    that failed on one side is never read as an attribute that changed.
    """
    try:
        names = os.listxattr(path, follow_symlinks=False)
    except OSError:
        return None
    pairs = []
    for name in sorted(names):
        try:
            pairs.append((name, os.getxattr(path, name, follow_symlinks=False)))
        except OSError:
            pairs.append((name, None))
    return tuple(pairs)


def own_payload_states(root, paths, prune=None):
    """Record what each of a package's own payload paths IS, for the bytes rule.

    Args:
        root: install root (str or Path).
        paths: root-relative POSIX paths (no leading slash; a directory's
            trailing slash is ignored), the package's own rows.
        prune: as for fs_snapshot. A path beneath a pruned directory is never
            in the diff, so it is not read here either.

    Returns:
        dict mapping each path that exists to (kind, content, meta):
          kind     KIND_FILE, KIND_LINK or KIND_DIR, from lstat (a symbolic
                   link is never followed)
          content  a regular file's sha256 (None when its bytes could not be
                   read), a link's own target text, "" for a directory
          meta     (st_mode, st_uid, st_gid, st_mtime_ns, st_nlink, st_ino,
                   the extended attributes, or None when they could not be
                   listed, st_ctime_ns)
        A path that does not exist, or that is none of the three kinds, is
        omitted. Reading a file's bytes moves no ctime, so taking this record
        does not disturb the snapshot it accompanies.
    """
    root = os.path.abspath(str(root))
    prune_set = frozenset(
        p.strip("/") for p in
        (SNAPSHOT_PRUNE_DEFAULT if prune is None else prune)
    )
    states = {}
    for raw in paths:
        rel = str(raw).strip("/")
        if not rel:
            continue
        parts = rel.split("/")
        if any("/".join(parts[:i]) in prune_set for i in range(1, len(parts))):
            continue
        path = os.path.join(root, rel)
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if stat.S_ISLNK(st.st_mode):
            try:
                kind, content = KIND_LINK, os.readlink(path)
            except OSError:
                kind, content = KIND_LINK, None
        elif stat.S_ISREG(st.st_mode):
            kind, content = KIND_FILE, _file_sha256(path)
        elif stat.S_ISDIR(st.st_mode):
            kind, content = KIND_DIR, ""
        else:
            continue
        states[rel] = (kind, content, (
            st.st_mode, st.st_uid, st.st_gid, st.st_mtime_ns, st.st_nlink,
            st.st_ino, _xattrs(path), st.st_ctime_ns))
    return states


def _xattr_digests(xattrs):
    if xattrs is None:
        return None
    return tuple((name, None if value is None else
                  hashlib.sha256(value).hexdigest())
                 for name, value in xattrs)


def comparable_meta(meta):
    """A meta tuple with each extended attribute's value reduced to its
    sha256, the form encode_meta writes and decode_meta reads back."""
    return meta[:6] + (_xattr_digests(meta[6]),) + meta[7:]


def encode_meta(meta):
    """A meta tuple's first seven fields as colon-separated text with no
    space in it, for a state that travels through a file (the source-build
    lane's baseline): the extended attributes as the hex of their names and
    value digests, or '-' when they could not be listed."""
    mode, uid, gid, mtime_ns, nlink, ino, xattrs = comparable_meta(meta)[:7]
    listed = ("-" if xattrs is None else
              json.dumps([list(pair) for pair in xattrs]).encode().hex())
    return f"{mode}:{uid}:{gid}:{mtime_ns}:{nlink}:{ino}:{listed}"


def decode_meta(fields, ctime_ns):
    """encode_meta's seven fields read back as a comparable meta tuple.

    Raises ValueError for text encode_meta does not write, so a damaged
    baseline is refused rather than half read.
    """
    mode, uid, gid, mtime_ns, nlink, ino, listed = fields
    xattrs = None
    if listed != "-":
        pairs = json.loads(bytes.fromhex(listed))
        if not isinstance(pairs, list) or not all(
                isinstance(pair, list) and len(pair) == 2
                and isinstance(pair[0], str)
                and (pair[1] is None or isinstance(pair[1], str))
                for pair in pairs):
            raise ValueError("the extended attributes field is not a list "
                             "of names and digests")
        xattrs = tuple(tuple(pair) for pair in pairs)
    return (int(mode), int(uid), int(gid), int(mtime_ns), int(nlink),
            int(ino), xattrs, int(ctime_ns))


_ABSENT = object()


def metadata_changes(before, after, kind=KIND_FILE):
    """Name the metadata that moved between two own_payload_states meta tuples.

    Returns a list of phrases (possibly empty: then only the ctime moved, a
    chmod to the mode the file already had, for instance).
    """
    b_mode, b_uid, b_gid, b_mtime, b_nlink, b_ino, b_xattrs = before[:7]
    a_mode, a_uid, a_gid, a_mtime, a_nlink, a_ino, a_xattrs = after[:7]
    changes = []
    if stat.S_IMODE(b_mode) != stat.S_IMODE(a_mode):
        changes.append(f"the mode ({stat.S_IMODE(b_mode):04o} -> "
                       f"{stat.S_IMODE(a_mode):04o})")
    if (b_uid, b_gid) != (a_uid, a_gid):
        changes.append(f"the owner ({b_uid}:{b_gid} -> {a_uid}:{a_gid})")
    if b_mtime != a_mtime:
        changes.append("the modification time")
    if b_xattrs is not None and a_xattrs is not None and b_xattrs != a_xattrs:
        b_map, a_map = dict(b_xattrs), dict(a_xattrs)
        names = sorted(n for n in set(b_map) | set(a_map)
                       if b_map.get(n, _ABSENT) != a_map.get(n, _ABSENT))
        changes.append(f"the extended attributes ({', '.join(names)})")
    if b_ino != a_ino:
        changes.append("the inode (the same target in a new link)"
                       if kind == KIND_LINK else
                       "the inode (the same bytes in a new file)")
    elif b_nlink != a_nlink:
        changes.append(f"the link count ({b_nlink} -> {a_nlink})")
    return changes


def split_own_changes(before, after, paths):
    """Split the package's own modified paths by what the hook changed.

    before, after: own_payload_states taken immediately before the hook and
        after it.
    paths: the package's own paths the snapshot diff reported modified.

    Returns (rewritten, kept, undetermined), in path order:
      rewritten     paths whose bytes changed, whose link target changed, or
                    that became another kind of entry: the hook rewrote them
      kept          (path, kind, changes) for each path whose bytes (a link's
                    target) are what they were before the hook; `changes`
                    names the metadata that moved (metadata_changes)
      undetermined  paths that could not be read on one side, so no
                    comparison was made; neither rewritten nor kept
    """
    rewritten, kept, undetermined = [], [], []
    for path in sorted(paths):
        rel = path.strip("/")
        prior, now = before.get(rel), after.get(rel)
        if prior is None or now is None:
            undetermined.append(rel)
        elif prior[0] != now[0]:
            rewritten.append(rel)
        elif prior[1] is None or now[1] is None:
            undetermined.append(rel)
        elif prior[1] != now[1]:
            rewritten.append(rel)
        else:
            kept.append((rel, now[0], metadata_changes(prior[2], now[2],
                                                       now[0])))
    return rewritten, kept, undetermined


def _and_join(parts):
    if len(parts) < 2:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def format_kept_line(path, kind, changes):
    """The one line that names an own path a hook changed only the metadata
    of, and what changed; the same line for both recorders."""
    return (
        f"  hook[record] {path}: its hook changed "
        f"{_and_join(changes) or 'nothing but the change time'}, not "
        f"{'the target' if kind == KIND_LINK else 'the bytes'} — the "
        f"content stays checked"
    )


def format_record_summary(claimed, foreign, modified, own_modified=(),
                          own_kept=(), own_undetermined=()):
    """One-line-per-fact summary for the install transcript.

    Returns a list of message lines (possibly empty), matching the
    HookResult.messages convention in pkm/hooks.py.

    own_modified: paths of THIS package's own payload whose bytes the hook
        changed — reclassified to the hook-generated content class by the
        caller (the D-9b rule; see installer._record_hook_outputs).
        `modified` is every OTHER modified path (ownership and content
        class unchanged, reported so the fact is never lost).
    own_kept: (path, kind, changes) for each own path the hook changed only
        the metadata of (split_own_changes). Its content stays checked; one
        line per path names it and what changed.
    own_undetermined: own paths the hook touched whose bytes could not be
        read on one side; their content class stays as recorded, one line
        per path.
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
    for path, kind, changes in own_kept:
        lines.append(format_kept_line(path, kind, changes))
    for path in own_undetermined:
        lines.append(
            f"  hook[record] {path}: its bytes could not be read on both "
            f"sides of its hook — its content class stays as recorded"
        )
    if modified:
        lines.append(
            f"  hook[record] {len(modified)} existing path(s) modified by "
            f"the hook (ownership unchanged)"
        )
    return lines
