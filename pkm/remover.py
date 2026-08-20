# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""pkm remover — Safe package removal with dependency checking."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Forensic-trace shim — defensive import, same shape as installer.py and
# hooks.py: the trace module is present on a built system and absent in a
# bare source checkout, and neither case may change what removal does.
try:
    from . import _trace
    _TRACE_AVAILABLE = True
except ImportError:
    _trace = None
    _TRACE_AVAILABLE = False

from .database import PackageDB, MANIFEST_DIR, _sha256
from .hooks import HOOK_ENV_ALLOWLIST

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


def _remove_hook_cmd(root, hook):
    """Build the command that runs `hook` for an install rooted at `root`.

    Returns (argv, package_root) where package_root is the value the hook
    is given as PKM_PACKAGE_ROOT.

    Two cases, mirroring the install-side per-package hook runner:

      root "/"    — the live system. Run the hook directly.
      any other   — a chroot target (a Forge install, the mint chroot, a
                    recovery mount). Run the hook UNDER chroot(root), so
                    every filesystem-rooted path inside it (/lib/modules,
                    /etc, /var/log, /boot) resolves to the target rather
                    than to the machine driving the removal. A hook that
                    unloads kernel modules or deletes /lib/modules content
                    must never reach the host that way.

    PKM_PACKAGE_ROOT is "/" in both cases because it is the root from the
    HOOK's own perspective: under chroot the target IS the root.

    Kept as a module-level function rather than inlined so both branches
    can be read, and asserted, without executing a removal.
    """
    root = Path(root)
    if str(root) == "/":
        return [str(hook)], "/"
    hook_in_chroot = "/" + str(Path(hook).relative_to(root))
    return ["chroot", str(root), hook_in_chroot], "/"


def _pre_remove_cmd(root, hook):
    """The pre-remove hook's command. See _remove_hook_cmd."""
    return _remove_hook_cmd(root, hook)


def _post_remove_cmd(root, hook):
    """The post-remove hook's command. See _remove_hook_cmd.

    Both removal hooks are launched exactly the same way — the chroot
    decision is about WHERE the hook must act, which is the target root in
    both cases, not about when it fires. They share one implementation so
    the two can never drift into launching differently.
    """
    return _remove_hook_cmd(root, hook)


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

    def _manifest_recorded_paths(self, name, version):
        """Root-relative paths the package's on-disk text manifest records.

        The manifest is the second, independent record of the same payload
        the database rows describe (see cli._known_owned_paths, which reads
        the identical union for iso-prep's outcome assertion). Read before
        the removal, which unlinks the manifest itself. Missing or
        unparseable manifest: empty set — the database rows then stand
        alone, exactly the pre-union behavior.
        """
        from .database import MANIFEST_DIR, _parse_manifest

        if not version:
            return set()
        manifest = (Path(self.root) / MANIFEST_DIR.relative_to("/")
                    / f"{name}-{version}")
        try:
            parsed = _parse_manifest(manifest.read_text(errors="replace"))
        except OSError:
            return set()
        if not parsed:
            return set()
        return {p.strip("/") for p in parsed.get("files", []) if p.strip("/")}

    def _run_pre_remove_hook(self, name, version):
        """Fire the package's pre-remove runtime hook if it ships one.

        Hook path: <root>/var/lib/pkm/hooks/<name>/pre-remove, executable.
        Absent or non-executable: nothing happens and nothing is said —
        the overwhelming majority of packages ship no hook and must pay
        nothing for the ones that do.

        What it is for: work that is only possible while the package's
        payload is still on disk. Stopping a service the payload provides,
        unloading kernel modules built from it, deleting artefacts the
        package created AFTER its manifest was sealed — those artefacts
        are in no manifest, so the file-removal walk cannot see them and
        they survive the removal unless something removes them first. That
        is why this runs ahead of the walk rather than after it.

        Environment given to the hook:
            PKM_PACKAGE_NAME      — package name
            PKM_PACKAGE_VERSION   — version being removed
            PKM_PACKAGE_ROOT      — "/" (the root from the hook's own
                                    perspective; see _pre_remove_cmd)

        The inherited environment is stripped to HOOK_ENV_ALLOWLIST first.
        The hook runs with the privilege of the removing process, so an
        inherited LD_PRELOAD, PYTHONPATH or *_PROXY set by whoever could
        reach the parent environment would otherwise steer it. This uses
        the lifecycle-hook allowlist rather than the wider helper one: the
        two differ only in SUDO_USER, which exists so a helper can drop to
        the invoking user for a per-user install. No remove-time hook has
        that need, and the demonstrated-need rule in pkm/hooks.py says the
        hook environment stays minimal until one does.

        Failure is non-fatal and is REPORTED: a non-zero exit and an exec
        that could not happen at all both print a warning naming the
        package, what went wrong and the path to re-run by hand, and the
        removal then proceeds. Non-fatal is not silent — the hook cleans
        up around a removal, and a removal that stopped because a cleanup
        script failed would leave the package half-present with no way
        forward.
        """
        self._run_remove_hook(name, version, kind="pre_remove",
                              filename="pre-remove")

    def _run_post_remove_hook(self, name, version):
        """Fire the package's post-remove runtime hook if it ships one.

        Hook path: <root>/var/lib/pkm/hooks/<name>/post-remove, executable.
        Absent or non-executable: nothing happens and nothing is said.

        The mirror of the pre-remove hook, and the reason the pair exists.
        This one runs once the file-removal walk has finished and the
        package is out of the database, so it is for work that is only
        correct when the payload is already GONE: rebuilding a boot image
        so it stops referencing a driver that no longer exists, refreshing
        a cache that must not re-include the removed files, reloading a
        daemon that would otherwise hold a deleted path open. Doing any of
        that before the walk would capture the state being removed.

        `pkm/hooks.py` has named post_remove in LIFECYCLE_EVENTS since the
        R001 root while nothing invoked it, so a package could ship this
        script and state in its own documentation that it runs. The nvidia
        package is the live instance.

        Environment, privilege posture and failure handling are identical
        to the pre-remove hook — see _run_pre_remove_hook — and share one
        implementation so they cannot drift.
        """
        self._run_remove_hook(name, version, kind="post_remove",
                              filename="post-remove")

    def _run_remove_hook(self, name, version, kind, filename):
        """Shared implementation of both removal hooks.

        `kind` is the lifecycle event name from pkm/hooks.py used in the
        trace record; `filename` is the on-disk hook name, which is also
        the word used in any warning so the message names the thing the
        user has to go and re-run.
        """
        hook = self.root / "var" / "lib" / "pkm" / "hooks" / name / filename
        if not hook.is_file() or not os.access(str(hook), os.X_OK):
            return

        env = {k: v for k, v in os.environ.items() if k in HOOK_ENV_ALLOWLIST}
        cmd, package_root = _remove_hook_cmd(self.root, hook)
        env["PKM_PACKAGE_NAME"] = name
        env["PKM_PACKAGE_VERSION"] = version
        env["PKM_PACKAGE_ROOT"] = package_root

        try:
            if _TRACE_AVAILABLE:
                _trace.trace_event(
                    "pkm_hook_fire", pkg=name, hook=kind,
                    script_path=str(hook), argv=cmd,
                )
                result = _trace.traced_run(
                    cmd, env=env, phase=f"pkm_{kind}",
                    intent=f"{kind} hook for {name}", pkg=name,
                )
                _trace.trace_event(
                    "pkm_hook_done", pkg=name, hook=kind,
                    rc=result.returncode,
                )
            else:
                result = subprocess.run(cmd, env=env)  # trace-coverage: allow — _trace shim unavailable fallback
            if result.returncode != 0:
                print(
                    f"  WARNING: {filename} hook for {name} exited "
                    f"{result.returncode}; removal proceeds (hook is "
                    f"non-fatal). Whatever the hook was going to clean up "
                    f"may still be present. Re-run manually: {hook}",
                    file=sys.stderr,
                )
        except (OSError, subprocess.SubprocessError) as e:
            if _TRACE_AVAILABLE:
                try:
                    _trace.trace_event(
                        "pkm_hook_failed", pkg=name, hook=kind,
                        err=str(e),
                    )
                except Exception:
                    pass
            print(
                f"  WARNING: {filename} hook for {name} could not execute: "
                f"{e}; removal proceeds (hook is non-fatal).",
                file=sys.stderr,
            )

    def remove(self, name, force=False, reporter=None, on_file=None,
               run_pre_remove_hook=True, run_post_remove_hook=None):
        """Remove an installed package.

        Checks reverse dependencies unless force=True.
        Preserves modified config files. Never unlinks a path co-owned by
        another installed package (Component C), regardless of force.

        ``reporter`` (pkm.output.Reporter or None): when provided, the files
        actually removed are listed WITH their target paths (≤50 inline, else
        a per-dir breakdown), along with any preserved (user-edited) configs.
        None keeps the legacy silent (ok, msg) behavior.

        ``run_pre_remove_hook`` (default True): fire the package's
        pre-remove runtime hook, if it ships one, before anything on disk
        is touched. True is the default because "remove" means the package
        is going away, and a caller that says nothing should get the
        behaviour the package's own documentation describes. The callers
        that pass False are the ones whose operation is not that — see
        ``run_post_remove_hook`` (default None): fire the package's
        post-remove hook once the removal has completed. None means FOLLOW
        the pre-remove decision, which is the safe default and not a
        convenience: every caller that suppresses the pre-remove hook has
        judged that this is not a real removal on a real install — a
        rollback of an install that never completed, a reinstall, an
        upgrade, the build-time prune inside the mint chroot — and that
        judgment is equally true of both hooks. Defaulting to None means a
        new call site cannot exclude one hook and silently keep the other
        by saying nothing. Pass True or False to override deliberately.

        See _run_pre_remove_hook for what the hook is, and each call site for
        why it opts out.

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

        # Pre-remove hook, ahead of every filesystem change this method
        # makes. Placed after the two checks that can still refuse the
        # removal (not installed, reverse dependencies) so a refused
        # removal never runs it, and before the file list is read so a
        # package with no tracked files — which is still a package being
        # removed — fires it too.
        if run_post_remove_hook is None:
            run_post_remove_hook = run_pre_remove_hook

        if run_pre_remove_hook:
            self._run_pre_remove_hook(name, pkg["version"])

        # Get file list — the UNION of both of pkm's records, not the
        # database rows alone. The database has been incomplete on real
        # substrates while the text manifest carried the missing paths: the
        # 2026-08-15 build recorded the /opt/jdk and /opt/rocm/llvm compat
        # symlinks only in their packages' text manifests (as trailing-slash
        # directory entries the database import dropped), so this
        # database-driven removal left them behind as dangling symlinks —
        # caught by iso-prep's outcome assertion, which already checks the
        # union (2026-08-20). Removal now consumes the same union the
        # assertion checks. Disk truth still classifies every path, and the
        # FHS-skeleton and co-ownership guards below apply to the manifest
        # paths identically; a path on neither disk nor database is a no-op.
        files = self.db.get_files(name)
        db_recorded = {f["path"].strip("/") for f in files}
        for rel in sorted(self._manifest_recorded_paths(name, pkg["version"])):
            if rel and rel not in db_recorded:
                files.append({"path": rel, "is_dir": False})
        if not files:
            # No files tracked — just remove the DB entry
            self.db.remove_installed(name)
            self.db.log_operation("remove", name, old_version=pkg["version"])
            if run_post_remove_hook:
                self._run_post_remove_hook(name, pkg["version"])
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

        # The payload is off disk and the package is out of the database, so
        # the post-remove hook sees the finished state it exists for.
        if run_post_remove_hook:
            self._run_post_remove_hook(name, pkg["version"])

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
