# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""pkm remover — Safe package removal with dependency checking."""

import os
import shutil
from pathlib import Path

from .database import PackageDB, MANIFEST_DIR, _sha256

# Subtrees the post-prune directory sweep never touches.
#
# `opt` — the hook-product class. A recipe's post_install hook writes its
# payload after the manifest is sealed, so those files belong to no manifest
# and a prune removes the package's recorded files without ever seeing them
# (a pruned JDK left /opt/jdk behind on the last candidate for exactly this
# reason). Whether a hook product should be removed with its package is a
# design question about hook-output ownership, not something this sweep can
# answer from pkm state — so the sweep leaves the whole subtree alone and
# keeps walking.
#
# `var/lib/igos`, `var/lib/pkm` — the package system's own state trees. The
# database, the text manifests, the archives and the helper manifests live
# here; an empty one of these is a state directory awaiting content, not
# residue, and the shipping-tree gate treats them as pkm state rather than
# package-owned content for the same reason.
SWEEP_EXEMPT_PREFIXES = ("opt/", "var/lib/igos/", "var/lib/pkm/")
SWEEP_EXEMPT_EXACT = frozenset({"opt", "var/lib/igos", "var/lib/pkm"})


def ancestor_chain(path):
    """Every directory prefix of `path` that a sweep may consider.

    Includes the path itself (a recorded directory that was non-empty when
    its own package was removed can be empty once a later package's files
    are gone) and stops above the top-level FHS entries: a single-segment
    path is system skeleton no package owns, and removing one breaks the
    merged-usr compat symlinks the whole system resolves through.
    """
    parts = [p for p in path.strip("/").split("/") if p]
    return {"/".join(parts[:i]) for i in range(2, len(parts) + 1)}


def _sweep_roots(candidates):
    """The shallowest members of `candidates` — every other member is inside
    one of them, so walking these covers the whole candidate set once."""
    roots = []
    for c in candidates:
        parts = c.split("/")
        if not any("/".join(parts[:i]) in candidates
                   for i in range(2, len(parts))):
            roots.append(c)
    return sorted(roots)


def prune_empty_unowned_dirs(db, root, candidates):
    """Remove the directory skeletons a prune emptied and nothing owns.

    `candidates` is the ancestor closure of every path the prune removed,
    collected before the rows went away. A directory is removed only when
    all of these hold:

      - it is a real directory on disk, not a symlink;
      - it is empty at the moment of removal;
      - no remaining installed package's file rows record it, under EITHER
        is_dir flag (the flag is unreliable at scale, and the same bad data
        that creates skeletons would otherwise blind the check);
      - it is not inside an exempt subtree (above).

    rmdir only, deepest-first, so a parent is judged after its children are
    gone. Emptiness is the hard safety floor: anything a live payload still
    uses is non-empty and therefore untouchable, whether or not a manifest
    records it.

    The walk covers each candidate's SUBTREE, not only the candidates
    themselves, because a single unrecorded empty leaf keeps the entire
    chain above it alive. Measured on a two-package prune: an unrecorded,
    empty `__pycache__` under a pruned package's directory left that
    directory, its site-packages parent and the whole interpreter path
    standing after every recorded path was gone. Restricting the sweep to
    the recorded chain would therefore have removed nothing in the exact
    case the skeletons come from. Every level of the subtree is judged by
    the same three rules, so widening the walk never widens what may be
    deleted — an unowned directory holding a file is still untouchable, and
    so is every directory above it.

    Returns (removed, exempt_seen): the directories removed, deepest-first,
    and the exempt-subtree directories that were empty and unowned and were
    left in place anyway.
    """
    root = Path(root)
    candidates = {c.strip("/") for c in candidates}
    candidates = {c for c in candidates if c and "/" in c}
    if not candidates:
        return [], []

    # Every target row is already gone at this point — the sweep runs after
    # the removal pass — so any file row still recording a path belongs to a
    # surviving package. No package_id exclusion is needed or wanted. The
    # whole path set is read in one go rather than probed per directory: the
    # walk reaches paths that are in no candidate list, and this is the same
    # set, built the same way, that the shipping-tree ownership gate reads,
    # which is what keeps the two verdicts comparable.
    recorded = {p.strip("/") for (p,) in
                db.conn.execute("SELECT path FROM files")}

    def _exempt(rel):
        return rel in SWEEP_EXEMPT_EXACT or rel.startswith(
            SWEEP_EXEMPT_PREFIXES)

    # Collect the directories to judge. Exempt subtrees are walked as well,
    # so an empty unowned directory inside one can still be REPORTED — the
    # sweep declines to remove it, which is worth saying out loud rather
    # than passing over in silence. Symlinked directories are never
    # descended (or judged): a link's target belongs to whoever owns it.
    seen = set()
    for top in _sweep_roots(candidates):
        abs_top = root / top
        if not abs_top.is_dir() or abs_top.is_symlink():
            continue
        seen.add(top)
        for dirpath, dirnames, _files in os.walk(abs_top, topdown=True,
                                                 followlinks=False):
            rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
            keep = []
            for d in dirnames:
                if os.path.islink(os.path.join(dirpath, d)):
                    continue
                seen.add(f"{rel_dir}/{d}")
                keep.append(d)
            dirnames[:] = keep

    removed = []
    exempt_seen = []
    for rel in sorted(seen, key=lambda p: (p.count("/"), p), reverse=True):
        if "/" not in rel or rel in recorded:
            continue
        abs_path = str(root / rel)
        try:
            if (not os.path.isdir(abs_path)
                    or os.path.islink(abs_path)
                    or os.listdir(abs_path)):
                continue
        except (OSError, PermissionError):
            continue
        if _exempt(rel):
            exempt_seen.append(rel)
            continue
        try:
            os.rmdir(abs_path)
            removed.append(rel)
        except (OSError, PermissionError):
            # Raced, or not permitted — leave it. The shipping-tree
            # ownership gate reports whatever survives, so nothing is lost
            # silently by declining here.
            continue
    return removed, exempt_seen


class PackageRemover:
    """Remove packages safely, respecting dependencies and config files."""

    def __init__(self, db: PackageDB, root=None):
        """Initialize the remover.

        H-011 cross-cut: ``root`` is the install-root prefix used to
        reconstruct absolute paths from the DB's POSIX-relative file
        entries. Defaults to ``db.root`` so callers that already pass a
        chroot-aware PackageDB don't have to thread the value twice.
        Explicit override available for the rare case of operating on a
        DB whose root differs from the live filesystem (e.g. recovery
        tooling). Default to "/" if the DB has no root attribute
        (legacy compatibility — pre-Q9 PackageDB instances).
        """
        self.db = db
        if root is not None:
            self.root = Path(root)
        else:
            self.root = Path(getattr(db, "root", "/"))
        # Paths this remover left on disk ON PURPOSE, accumulated across every
        # remove() call it makes: co-owned payload, preserved configuration,
        # configuration that could not be read to prove it unmodified, and the
        # top-level FHS skeleton entries removal refuses on principle. A
        # caller that audits its own outcome — `pkm iso-prep` does — needs to
        # tell a deliberate retention from residue, and the difference is
        # knowable only here. Files that FAILED to unlink are deliberately NOT
        # in this set: those are the residue such an audit exists to catch.
        self.deliberately_retained = set()

    def _co_owned_paths(self, exclude_pkg_id, paths):
        """Component C — the co-ownership query. Return {path: [owner names]}
        for every path in `paths` that ANY OTHER installed package's file rows
        also claim (is_dir = 0). These bytes belong to a still-installed package
        too, so removing THIS package must NOT unlink them.

        The removed package's file list can be large (iso-prep prunes ~150
        packages, some with 100k+ files), so the query is batched — the IN-list
        is chunked to respect SQLite's per-statement variable limit (999). Cheap
        even at that scale: idx_files_path indexes files(path).
        """
        owners = {}
        # Leave headroom under the 999 SQLite variable limit for the extra
        # package_id bind and any future predicate.
        CHUNK = 900
        for i in range(0, len(paths), CHUNK):
            chunk = paths[i:i + CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = self.db.conn.execute(
                f"""SELECT DISTINCT f.path, i.name
                    FROM files f JOIN installed i ON f.package_id = i.id
                    WHERE f.is_dir = 0
                      AND f.package_id != ?
                      AND f.path IN ({placeholders})""",
                (exclude_pkg_id, *chunk),
            ).fetchall()
            for path, owner in rows:
                owners.setdefault(path, set()).add(owner)
        return {p: sorted(names) for p, names in owners.items()}

    def _co_owned_dirs(self, exclude_pkg_id, paths):
        """Directory analog of the Component-C co-ownership query. Return
        {path: [owner names]} for every directory in `paths` that ANY OTHER
        installed package records — under EITHER is_dir flag value.

        Component C fixed FILE co-ownership; directories stayed unguarded —
        removing a package whose file removals emptied a SHARED directory
        then rmdir'd it out from under the still-installed co-owner (the
        ge9b-03 /etc/sysconfig loss: two mirror-only co-owners were pruned
        by iso-prep, and the empty-dir cleanup deleted the directory that
        intergenos-base-files still records). The class is general: any
        remove can rmdir a shared-but-momentarily-empty directory on a real
        installed system. Same batching as _co_owned_paths.

        Flag-agnostic on the co-owner side (r41): real directories are
        recorded is_dir=0 at scale (the r26 finding — a path registered
        without its trailing slash lands is_dir=0), and an is_dir=1-only
        query let iso-prep rmdir /etc/sysconfig out from under
        intergenos-base-files (is_dir=0 row) on two consecutive ge9b-12
        mint runs. The same bad data must not blind the guard: what is
        being removed is decided by on-disk lstat; whether a co-owner
        still claims the path is decided by the path row alone. Worst
        case of the widening is retaining an empty directory another
        installed package genuinely records — fail-closed and traceable.
        """
        owners = {}
        CHUNK = 900
        for i in range(0, len(paths), CHUNK):
            chunk = paths[i:i + CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = self.db.conn.execute(
                f"""SELECT DISTINCT f.path, i.name
                    FROM files f JOIN installed i ON f.package_id = i.id
                    WHERE f.package_id != ?
                      AND f.path IN ({placeholders})""",
                (exclude_pkg_id, *chunk),
            ).fetchall()
            for path, owner in rows:
                owners.setdefault(path, set()).add(owner)
        return {p: sorted(names) for p, names in owners.items()}

    def remove(self, name, force=False, reporter=None, on_file=None):
        """Remove an installed package.

        Checks reverse dependencies unless force=True.
        Preserves modified config files. Never unlinks a path co-owned by
        another installed package (Component C), regardless of force.

        ``reporter`` (pkm.output.Reporter or None): when provided, the files
        actually removed are listed WITH their target paths (≤50 inline, else
        a per-dir breakdown), along with any preserved (user-edited) configs.
        None keeps the legacy silent (ok, msg) behavior.

        ``on_file`` is an optional callback invoked as
        ``on_file(index, total, path)`` as each recorded path is considered.
        Removing a large package unlinks a hundred thousand files and then
        walks their ancestor closure, all of it between the command and its
        one closing line — S3 of the silent-loop trio. The callback is the
        seam that lets the CLI report progress without this layer growing a
        console of its own, and it is deliberately called for every
        considered path (not only the unlinked ones), because a run that
        spends its time SKIPPING co-owned paths is working just as hard. A
        callback that raises is not allowed to abort the removal.

        Returns:
            (success: bool, message: str)
        """
        pkg = self.db.get_installed(name)
        if not pkg:
            return False, f"Package '{name}' is not installed"

        # Check reverse dependencies
        if not force:
            rdeps = self.db.get_reverse_depends(name)
            if rdeps:
                dep_list = ", ".join(f"{d['name']}" for d in rdeps)
                return False, (
                    f"Cannot remove {name}: {len(rdeps)} package(s) depend on it: {dep_list}\n"
                    f"  Use 'pkm remove {name} --force' to remove anyway."
                )

        # Get file list
        files = self.db.get_files(name)
        if not files:
            # No files tracked — just remove the DB entry
            self.db.remove_installed(name)
            self.db.log_operation("remove", name, old_version=pkg["version"])
            return True, f"Removed {name} {pkg['version']} (no files tracked)"

        # Classify each manifest path by what is ON DISK, not by the DB's
        # is_dir flag. The flag is unreliable at scale (ge9b-08 chroot DB:
        # thousands of real directories carried is_dir=0), which sent
        # directories through os.remove() — IsADirectoryError per entry,
        # swallowed into failed_removals — and left every removed package's
        # directory skeleton behind (the F41 empty-skeleton class: a pruned
        # mirror-only python package's site-packages tree shipped as empty
        # dirs and read as present via namespace-package import). A symlink
        # to a directory is a FILE for removal purposes (unlink the link,
        # never descend), hence the islink guard.
        def _on_disk_dir(f):
            p = str(self.root / f["path"])
            return os.path.isdir(p) and not os.path.islink(p)

        file_paths = sorted(
            [f for f in files if not _on_disk_dir(f)],
            key=lambda f: f["path"],
            reverse=True
        )
        dir_paths = sorted(
            [f for f in files if _on_disk_dir(f)],
            key=lambda f: f["path"],
            reverse=True
        )

        removed_count = 0
        removed_paths = []
        preserved_configs = []
        unreadable_preserved = []  # PKM-A20: /etc files we could not hash -> kept

        # Remove files (not directories yet). H-011: use self.root / path
        # for absolute-path reconstruction so Forge-installer scenarios
        # (root=/mnt/target before chroot pivot) and test fixtures
        # (root=tempdir) work alongside the live-system common case.
        failed_removals = []  # PKM-A08: files we could not unlink (surfaced, not swallowed)
        # FHS-SKELETON GUARD (GE-01, 2026-07-04): the archiver's DESTDIR
        # skeleton capture attributed the top-level merged-usr compat
        # symlinks (/bin /lib /sbin /lib64) to 908 of 913 package
        # manifests; removing/evicting ANY such package deleted the
        # system's compat symlinks (iso-prep eviction did exactly that —
        # squashfs Step 4.5 caught the broken chroot). NO package
        # legitimately owns a top-level FHS entry, so removal refuses ANY
        # single-segment path — loudly, never silently. The archiver-side
        # skeleton exclusion is the durable class fix (the next
        # from-scratch rebuilds the corpus clean); this chokepoint makes
        # the deletion impossible regardless of what a manifest claims.
        protected_skipped = []
        # Component C — co-ownership chokepoint. The iso-prep co-ownership loss:
        # `pkm iso-prep` pruning a MIRROR-only package (desktop/vala) unlinked
        # the 372-file payload that a SHIPPED package (core/vala-pass1) co-owns
        # at the same paths, because remove unlinked every manifest path without
        # asking whether another installed package still owns it (gate 4.5
        # fail-closed on the loss). Compute the co-owned
        # set ONCE up front; skip those paths in the loop below. This runs
        # BEFORE config-preservation / PKM-A20 (a co-owned path is retained
        # regardless of config state) and is UNCONDITIONAL — `--force` scopes to
        # the reverse-dependency override only; deleting bytes a still-installed
        # package owns is never correct under any flag. The removed package's DB
        # rows still cascade away, so the co-owner becomes sole owner — the
        # truthful end state. (The build-squashfs Step 2.5 pre-prune heal stays
        # as belt-and-suspenders; its retirement is a separate ruling.)
        co_owned = self._co_owned_paths(pkg["id"], [f["path"] for f in file_paths])
        retained_co_owned = []  # (path, [owner names]) — reported, never unlinked

        # S3 progress accounting. The ancestor closure is derived here rather
        # than at its own loop below so the total spans ALL THREE passes — the
        # file loop, the directory loop and the unowned-empty-ancestor sweep.
        # A count that restarted at each pass would tell a user the work had
        # gone backwards, which is worse than no count at all.
        ancestors = set()
        for f in files:
            parts = f["path"].strip("/").split("/")
            for i in range(2, len(parts)):
                ancestors.add("/".join(parts[:i]))
        _considered = 0
        _consider_total = len(file_paths) + len(dir_paths) + len(ancestors)

        def _note(path):
            nonlocal _considered
            _considered += 1
            if on_file is None:
                return
            try:
                on_file(_considered, _consider_total, path)
            except Exception:
                pass

        for f in file_paths:
            _note(f["path"])
            rel = f["path"].strip("/")
            if rel and "/" not in rel:
                protected_skipped.append(rel)
                continue
            if f["path"] in co_owned:
                retained_co_owned.append((f["path"], co_owned[f["path"]]))
                continue
            abs_path = str(self.root / f["path"])

            # Config file protection
            if f["path"].startswith("etc/"):
                if os.path.isfile(abs_path):
                    # Check if user modified it
                    config = self.db.conn.execute(
                        "SELECT original_checksum FROM config_files WHERE path = ?",
                        (f["path"],)
                    ).fetchone()
                    if config and config[0]:
                        try:
                            current = _sha256(abs_path)
                            if current != config[0]:
                                # User modified — preserve it
                                preserved_configs.append(f["path"])
                                continue
                        except (OSError, PermissionError):
                            # PKM-A20: fail-CLOSED. We could not read the file
                            # to compare against the recorded baseline, so we
                            # cannot prove it is unmodified — preserve it rather
                            # than delete a possibly user-edited config. The old
                            # `pass` fell through to os.remove (fail-OPEN),
                            # silently destroying user data on a transient read
                            # error. Surface separately (it is "could not
                            # verify", not "confirmed modified").
                            unreadable_preserved.append(f["path"])
                            continue

            # Remove the file
            try:
                if os.path.lexists(abs_path):
                    os.remove(abs_path)
                    removed_count += 1
                    removed_paths.append(f["path"])
            except (OSError, PermissionError) as e:
                # PKM-A08: do NOT silently swallow. A file left on disk while
                # the DB row is being removed is a real FS/DB inconsistency the
                # user must see — never report a bare "Removed" success over it.
                failed_removals.append((f["path"], str(e)))

        # Remove empty directories (only if they're empty after file removal).
        # Cross-package directory-ownership guard (audit ruling 2026-07-15):
        # a directory another installed package's manifest records is NEVER
        # rmdir'd here, even when momentarily empty — its co-owner still
        # depends on it existing (the /etc/sysconfig class). Unconditional,
        # like the file-level Component C: --force scopes to reverse-deps only.
        co_owned_dirs = self._co_owned_dirs(
            pkg["id"], [d["path"] for d in dir_paths]
        )
        retained_co_owned_dirs = []  # (path, [owner names]) — reported, never rmdir'd
        for d in dir_paths:
            _note(d["path"])
            rel = d["path"].strip("/")
            if rel and "/" not in rel:
                # Same FHS-skeleton guard as the file loop: a top-level
                # directory (e.g. /lib64) is system skeleton, never
                # package-owned — refuse even the empty-only rmdir.
                protected_skipped.append(rel)
                continue
            if d["path"] in co_owned_dirs:
                retained_co_owned_dirs.append(
                    (d["path"], co_owned_dirs[d["path"]])
                )
                continue
            abs_path = str(self.root / d["path"])
            try:
                if os.path.isdir(abs_path) and not os.listdir(abs_path):
                    os.rmdir(abs_path)
            except (OSError, PermissionError):
                pass  # Directory not empty or permission denied — leave it

        # Unowned-empty-ancestor sweep (F41 skeleton class, second half).
        # Manifests routinely omit intermediate directories their payload
        # created (pip package trees are the worst case), so after the
        # recorded paths are gone, unrecorded-but-now-empty parents survive
        # every remove — on the ISO prune that shipped whole site-packages
        # skeletons. Sweep the ancestor closure of everything this remove
        # touched, deepest-first, and rmdir any directory that is (a) empty,
        # (b) not top-level FHS (same guard as above), and (c) not recorded
        # by ANY still-installed package under EITHER is_dir flag — the
        # flag-agnostic query matters because the same bad is_dir data that
        # created this class would otherwise blind the co-ownership check.
        # Emptiness is the hard safety floor: a directory any live payload
        # still uses is non-empty and untouchable regardless of records.
        # `ancestors` was derived above, with the progress totals, so the
        # count spans this pass too.
        if ancestors:
            recorded = set()
            anc_list = sorted(ancestors)
            CHUNK = 900
            for i in range(0, len(anc_list), CHUNK):
                chunk = anc_list[i:i + CHUNK]
                placeholders = ",".join("?" * len(chunk))
                for (p,) in self.db.conn.execute(
                    f"""SELECT DISTINCT f.path FROM files f
                        WHERE f.package_id != ? AND f.path IN ({placeholders})""",
                    (pkg["id"], *chunk),
                ):
                    recorded.add(p)
            for rel in sorted(ancestors, key=lambda p: p.count("/"),
                              reverse=True):
                _note(rel)
                if rel in recorded:
                    continue
                abs_path = str(self.root / rel)
                try:
                    if (os.path.isdir(abs_path)
                            and not os.path.islink(abs_path)
                            and not os.listdir(abs_path)):
                        os.rmdir(abs_path)
                except (OSError, PermissionError):
                    pass  # Non-empty or unreadable — leave it

        # Remove manifest file. MANIFEST_DIR is the on-disk pkm state
        # directory; rebase under self.root so Forge / test scenarios
        # find the manifest under the install root rather than the host's.
        manifest = self.root / MANIFEST_DIR.relative_to("/") / f"{name}-{pkg['version']}"
        if manifest.exists():
            manifest.unlink()

        # For a proprietary download-helper, also drop its footprint manifest
        # (/var/lib/igos/helpers/<name>.manifest). That file is what
        # payload_installed() keys on; if it survives a remove, a later
        # `pkm install <name>` would route to the proprietary flow and no-op
        # "already installed" even though the payload was unlinked. Rebased
        # under self.root to mirror the MANIFEST_DIR handling above.
        helper_manifest = (
            self.root / "var/lib/igos/helpers" / f"{name}.manifest"
        )
        if helper_manifest.exists():
            helper_manifest.unlink()

        # Remove from database
        self.db.remove_installed(name)
        self.db.log_operation("remove", name, old_version=pkg["version"])

        # Record what was kept on purpose, for a caller that audits whether
        # the removal actually cleared what the package owned. Normalised the
        # same way the file rows are, so the caller can compare directly.
        for _p in protected_skipped:
            self.deliberately_retained.add(_p.strip("/"))
        for _p in preserved_configs:
            self.deliberately_retained.add(_p.strip("/"))
        for _p in unreadable_preserved:
            self.deliberately_retained.add(_p.strip("/"))
        for _p, _owners in retained_co_owned:
            self.deliberately_retained.add(_p.strip("/"))
        for _p, _owners in retained_co_owned_dirs:
            self.deliberately_retained.add(_p.strip("/"))

        from . import txn as _txn
        msg = (
            f"Removed {_txn.describe_subject(name, pkg)} "
            f"({removed_count} files)"
        )
        # CAPPED. The enumerated owner corpus is gone from the default
        # rendering — see txn.retained_report for why. The counts and the
        # per-path query remain, and -v still lists every path with its
        # owners, so nothing that was knowable became unknowable.
        for line in _txn.retained_report(retained_co_owned, "path"):
            msg += f"\n  {line}"
        for line in _txn.retained_report(
                retained_co_owned_dirs, "directory", "directories"):
            msg += f"\n  {line}"
        if protected_skipped:
            msg += (
                f"\n  NOTE: {len(protected_skipped)} top-level FHS skeleton "
                f"entr{'y' if len(protected_skipped) == 1 else 'ies'} in the "
                f"manifest were NOT removed (system skeleton, never "
                f"package-owned): " + ", ".join("/" + p for p in sorted(set(protected_skipped)))
            )
        if failed_removals:
            msg += (
                f"\n  WARNING: {len(failed_removals)} file(s) could NOT be "
                f"removed and remain on disk:"
            )
            for p, e in failed_removals:
                msg += f"\n    /{p}  ({e})"
        if preserved_configs:
            msg += f"\n  Preserved {len(preserved_configs)} modified config file(s):"
            for cf in preserved_configs:
                msg += f"\n    /{cf}"
        if unreadable_preserved:
            msg += (
                f"\n  Preserved {len(unreadable_preserved)} config file(s) that "
                f"could NOT be read to verify edits (kept to avoid data loss):"
            )
            for cf in unreadable_preserved:
                msg += f"\n    /{cf}"

        if reporter:
            reporter.file_list(
                removed_paths, action="Remove", pkg=name, root=self.root,
            )
            from .output import VERBOSE as _VERBOSE
            # Compared with == rather than >= on purpose: `reporter` is a
            # documented duck-typed seam (callers pass test doubles), and a
            # double's attribute does not support ordering. Equality against
            # the level constant answers the question without assuming the
            # attribute is a number.
            _verbose = getattr(reporter, "level", None) == _VERBOSE
            for line in _txn.retained_report(
                    retained_co_owned, "path", verbose=_verbose):
                reporter.note(line)
            for line in _txn.retained_report(
                    retained_co_owned_dirs, "directory", "directories",
                    verbose=_verbose):
                reporter.note(line)
            if failed_removals:
                reporter.warn(
                    f"{len(failed_removals)} file(s) could not be removed and "
                    f"remain on disk: "
                    + ", ".join("/" + p for p, _ in failed_removals)
                )
            if preserved_configs:
                reporter.note(
                    f"Preserved {len(preserved_configs)} modified config "
                    f"file(s):"
                )
                for cf in preserved_configs:
                    reporter.info(f"    /{cf}")
            if unreadable_preserved:
                reporter.warn(
                    f"{len(unreadable_preserved)} config file(s) could not be "
                    f"read to verify edits; preserved to avoid data loss: "
                    + ", ".join("/" + p for p in unreadable_preserved)
                )
            # PKM-A08: keep the success line honest — a bare green "Removed"
            # over files left on disk reads as a clean uninstall in scroll-back.
            if failed_removals:
                reporter.done(
                    f"Removed {name} {pkg['version']} — "
                    f"{len(failed_removals)} file(s) left on disk (see warning)"
                )
            else:
                reporter.done(f"Removed {name} {pkg['version']}")

        return True, msg
