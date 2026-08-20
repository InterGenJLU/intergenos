# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""pkm CLI — Natural-language command interface for InterGenOS package management."""

import argparse
import contextlib
import os
import re
import sys
from pathlib import Path

try:
    import fcntl
    _HAS_FLOCK = True
except ImportError:
    fcntl = None
    _HAS_FLOCK = False

from . import __version__
from . import preflight as preflight_module
from . import progress
from . import txn
from .configprotect import summary_lines as configprotect_summary_lines
from .database import PackageDB, _sha256
from .installer import (
    PackageInstaller, is_download_helper, payload_installed,
    acceptance_record_exists,
)
from .remover import PackageRemover, ancestor_chain, prune_empty_unowned_dirs
from .verifier import PackageVerifier
from .repo import RepoManager
from .output import (
    Reporter, QUIET, NORMAL, VERBOSE,
    set_process_level, emit, emit_info, emit_note, emit_done, emit_warn, emit_error,
)

# Build-stage intermediate packages: the install set ships these (they're
# depended on in the recipe graph) but they are deliberately NOT published to the
# mirror. Mirrors scripts/inject-pkginfo.py's INTERMEDIATE marker — keep in sync.
# `pkm update` must NOT dump them on the user as "not in any repository": they're
# plumbing the user never chose and can't act on, and listing the wall of them
# buries genuine orphan signal (and reads as broken/novice output).
_BUILD_INTERMEDIATE_RE = re.compile(r"-(pass[123]|tmp|bootstrap)(-|$)")

# Forensic-trace shim — defensive import. When IGOS_BUILD_DEBUG_VERBOSE /
# FORGE_DEBUG_VERBOSE is unset, every _trace call short-circuits at its
# gate. pkm runs in two contexts (build-time hand-off from pkg-functions.sh
# AND installed-system operator-typed); both inherit env-vars from the
# parent so the shared-runid contract works in either.
try:
    from . import _trace
    _TRACE_AVAILABLE = True
except ImportError:
    _trace = None
    _TRACE_AVAILABLE = False


# H-023: serialize concurrent pkm mutations via fcntl.flock on /var/lock/pkm.lock.
# Mutating subcommands acquire LOCK_EX | LOCK_NB at top of dispatch; second
# concurrent mutator gets immediate failure with a hint, not a silent wait
# (security-aligned posture: prefer fail-loud over queue-and-hope).
PKM_LOCK_PATH = Path("/var/lock/pkm.lock")
PKM_MUTATING_COMMANDS = frozenset({
    "install", "install-helper", "remove", "reinstall", "update", "upgrade", "import",
    "restart-services",
    # Q9: hold/unhold/mark mutate DB state; autoremove mutates filesystem +
    # DB. All four go through the flock gate.
    "hold", "unhold", "mark", "autoremove",
    # O-013: cache subcommand mutates /var/cache/pkm/packages/.
    "cache",
    # PKM-A23: these mutate but were in NEITHER set, so a non-root run skipped
    # the clean "must be run as root" advisory below and instead blew up with a
    # raw PermissionError further down. refresh-baseline commits config_files
    # UPDATEs; iso-prep removes packages (FS + DB).
    "refresh-baseline", "iso-prep",
    # 085: record-hook-changes marks file rows hook-managed and re-emits the
    # package's text manifest, so it mutates both halves of the record.
    "record-hook-changes",
    # 086: vacuum rewrites the whole database file. It changes no record and
    # no installed file, but it is emphatically a write, and it must not be
    # able to run beside an install that is mid-transaction.
    "vacuum",
})

# B7/S-D 2 (USA-1 walk closure): commands that legitimately initialize the DB
# on first run. Every other command operates on existing DB state and surfaces
# a clear FileNotFoundError diagnostic when the DB doesn't exist, rather than
# auto-creating an empty DB that would silently pass-by-vacuity (`pkm verify
# --all` on a never-populated DB returning EXIT_OK with 0 packages is the
# masked-diagnostic class the audit named).
PKM_DB_INIT_COMMANDS = frozenset({"install", "install-helper", "import"})

# Pure-read subcommands that issue only SELECTs. These open the DB read-only
# (database.py read_only=True -> immutable open) so a regular user can inspect
# their own installed system without root. Without this, every read command
# hit "attempt to write a readonly database" on the root-owned
# /var/lib/igos/pkm.db (the normal open path runs `PRAGMA journal_mode = WAL`,
# a write). Conservative allowlist: a command NOT listed keeps the read-write
# open, so no mutating path (install/remove/iso-prep/verify/cache/...) is ever
# accidentally opened immutable. Prime Directive: read your own machine
# without sudo.
PKM_READONLY_COMMANDS = frozenset({
    "list", "search", "info", "files", "provides", "depends", "history",
    # PKM-A23: pure-read DB access (SELECTs only) but were in NEITHER set, so
    # the default read-WRITE open hit "attempt to write a readonly database" on
    # the root-owned pkm.db for a non-root user. verify checks files against the
    # DB; check-updates only SELECTs installed (it writes a separate JSON, which
    # fails cleanly on its own if the dir isn't writable). Both let a normal
    # user read their own system without sudo (Prime Directive). With these +
    # the two mutating adds, EVERY dispatch command is in exactly one set.
    "verify", "check-updates",
    # 085: hook-baseline only SELECTs the package's own rows and hashes those
    # files; the baseline it writes is an ordinary file outside the database,
    # the same shape check-updates already has.
    "hook-baseline",
})


def _is_dry_run_invocation(args):
    """True when this invocation is a plan-only PREVIEW (`--dry-run`).

    The notifier's top-bar click launches `pkm upgrade --all --dry-run`
    UNPRIVILEGED; iso-prep and autoremove have the same preview flag. A dry-run
    computes and prints a plan and modifies NOTHING, so it is treated as
    strictly read-only: exempt from the root gate and the mutation lock, and it
    opens the DB read-only. A NAMED real mutation (no --dry-run) is unaffected
    and refuses under non-root exactly as before."""
    return bool(
        getattr(args, "upgrade_dry_run", False)
        or getattr(args, "autoremove_dry_run", False)
        or getattr(args, "iso_prep_dry_run", False)
        # `pkm vacuum --dry-run` reads two PRAGMAs and one file size and
        # prints what a rebuild would return. Refusing that to a normal user
        # would be the same user-control failure the upgrade preview
        # already had fixed: a person is entitled to ask how their own
        # machine is doing without sudo.
        or getattr(args, "vacuum_dry_run", False)
    )


@contextlib.contextmanager
def _pkm_mutation_lock(command, dry_run=False):
    """Acquire fcntl.flock on PKM_LOCK_PATH for the duration of a mutating
    subcommand. No-op for read-only commands or platforms without fcntl
    (e.g. test runs on Windows where fcntl is unavailable; production pkm
    only runs on Linux). Raises sys.exit(1) on lock-contention.
    """
    # A dry-run preview mutates nothing, so it must not take the mutation lock
    # (which would need write access to /var/lock and defeats the unprivileged
    # preview). Read-only and lock-free by construction.
    if command not in PKM_MUTATING_COMMANDS or not _HAS_FLOCK or dry_run:
        yield
        return
    # Chroot-install robustness: /var/lock is conventionally a symlink to
    # /run/lock on systemd systems. At chroot-install time (golden-builder
    # invoking `pkm import` after each package deploy via
    # scripts/pkg-functions.sh:688), /run is not mounted, so the symlink
    # dangles. Python's Path.mkdir(exist_ok=True) checks isdir() on
    # FileExistsError; for a dangling symlink isdir() returns False, so
    # the exception re-raises despite exist_ok=True. Resolve the symlink
    # target first and mkdir THAT path. If even that fails (truly broken
    # filesystem), skip locking entirely — pkm-install during chroot build
    # has no concurrent pkm processes to serialize against. The H-023
    # invariant (serialize concurrent mutations) still holds at runtime
    # on the installed system where /var/lock resolves cleanly.
    #
    # 2026-05-23: pre-fix behavior caused EVERY chroot-install pkm-import
    # to fail with sys.exit(1), leaving pkm's SQLite DB EMPTY for all
    # ~700 installed packages — the same "phantom-installed" class the
    # comment at scripts/pkg-functions.sh:680-686 specifically warns
    # about ("Discovered when /usr/bin/ping triaged as an orphan binary").
    try:
        lock_dir = PKM_LOCK_PATH.parent
        if lock_dir.is_symlink():
            target = lock_dir.readlink()
            if not target.is_absolute():
                target = lock_dir.parent / target
            target.mkdir(parents=True, exist_ok=True)
        else:
            lock_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        emit_warn(f"cannot create lock-file parent dir {PKM_LOCK_PATH.parent}: {e}")
        emit_warn("skipping lock acquisition (chroot-install context assumed; "
                  "no concurrent pkm in build chroots)")
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event(
                    "pkm_lock_chroot_fallback",
                    command=command, reason=str(e),
                    lock_path=str(PKM_LOCK_PATH),
                )
            except Exception:
                pass
        yield
        return
    fd = open(str(PKM_LOCK_PATH), "w")
    acquired = False
    try:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            if _TRACE_AVAILABLE:
                try:
                    _trace.trace_event("pkm_lock_acquired",
                                       command=command, lock_path=str(PKM_LOCK_PATH))
                except Exception:
                    pass
        except (BlockingIOError, OSError):
            fd.close()
            if _TRACE_AVAILABLE:
                try:
                    _trace.trace_event(
                        "pkm_lock_contention",
                        command=command, lock_path=str(PKM_LOCK_PATH),
                    )
                except Exception:
                    pass
            emit_error(
                f"another pkm operation is in progress (lock held on "
                f"{PKM_LOCK_PATH}). Wait for it to complete, or check for stale "
                f"pkm processes (`fuser {PKM_LOCK_PATH}` or `lsof {PKM_LOCK_PATH}`)."
            )
            sys.exit(1)
        yield
    finally:
        # Only unlock+close a lock we actually acquired. On the contention
        # path fd is already closed and sys.exit(1) raises SystemExit into
        # this finally; unlocking the closed fd raised ValueError ("I/O
        # operation on closed file") — NOT an OSError, so it escaped the
        # old `except OSError` and clobbered the clean error with a
        # traceback. Guard on `acquired` and catch ValueError too.
        if acquired:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
                if _TRACE_AVAILABLE:
                    try:
                        _trace.trace_event("pkm_lock_released",
                                           command=command, lock_path=str(PKM_LOCK_PATH))
                    except Exception:
                        pass
            except (OSError, ValueError):
                pass
            try:
                fd.close()
            except (OSError, ValueError):
                pass


def main():
    parser = argparse.ArgumentParser(
        prog="pkm",
        description="InterGenOS Package Manager",
    )
    parser.add_argument("--version", action="version", version=f"pkm {__version__}")
    parser.add_argument("--db", help="Database path override")

    # Verbosity (PRIME DIRECTIVE transparency layer). Shared across the
    # mutating subcommands via `parents=[verbosity]` AND accepted before the
    # subcommand on the top-level parser, so both `pkm -v install foo` and
    # `pkm install foo -v` work. NORMAL (neither flag) is the informative
    # default; -v lists every path/URL inline; -q collapses to summary lines.
    verbosity = argparse.ArgumentParser(add_help=False)
    verbosity.add_argument("-v", "--verbose", action="store_true",
                           help="Show every file path, URL, and hook line")
    verbosity.add_argument("-q", "--quiet", action="store_true",
                           help="Summary lines only (for scripts)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show every file path, URL, and hook line")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Summary lines only (for scripts)")

    sub = parser.add_subparsers(dest="command", metavar="command")

    # -- install --
    p_install = sub.add_parser("install", help="Install a package",
                               parents=[verbosity])
    p_install.add_argument("packages", nargs="+", metavar="package")
    p_install.add_argument("--archive", help="Path to .igos.tar.gz archive")
    p_install.add_argument("--archive-trust", choices=["strict","loose","repo-only"],
                           default="strict",
                           help="Trust mode for --archive installs (default: strict)")
    p_install.add_argument(
        "--allow-downgrade", action="store_true",
        help="Permit replacing an installed package with an OLDER "
             "version-release. Without this, a would-be downgrade is refused "
             "and nothing is changed.",
    )
    p_install.add_argument(
        "--yes", "-y", action="store_true", dest="assume_yes",
        help="Accept the resolved transaction without the confirmation "
             "pause. The summary still prints.",
    )

    # -- install-helper (hidden back-compat alias; pkm 2b unified this into
    #    `pkm install <app>`. Kept working, suppressed from --help.) --
    p_helper = sub.add_parser("install-helper", help=argparse.SUPPRESS,
                              parents=[verbosity])
    p_helper.add_argument("package", help="Package to install (e.g., chrome, vscode, claude-code)")

    # -- remove --
    p_remove = sub.add_parser("remove", aliases=["uninstall"], help="Remove a package",
                              parents=[verbosity])
    p_remove.add_argument("package")
    p_remove.add_argument("--force", action="store_true", help="Remove even if others depend on it")

    # -- reinstall --
    p_reinstall = sub.add_parser("reinstall", help="Remove + reinstall a package (repo-fetched)",
                                 parents=[verbosity])
    p_reinstall.add_argument("packages", nargs="+", metavar="package")
    p_reinstall.add_argument(
        "--allow-downgrade", action="store_true",
        help="Permit reinstalling with an OLDER version-release than the one "
             "installed. Without this, a reinstall that would move the "
             "package backwards is refused and nothing is removed. This is "
             "the guard on the locally-built-package-ahead-of-the-mirror "
             "case.",
    )

    # -- list --
    p_list = sub.add_parser("list", aliases=["ls"], help="List packages")
    p_list.add_argument("what",
                        choices=["installed", "available", "upgradable", "upgradeable"],
                        nargs="?", default="installed",
                        help="upgradeable is accepted as a spelling alias of upgradable")
    p_list.add_argument("--tier", help="Filter by tier")

    # -- sync -- (primary; `update`/`refresh` remain accepted aliases)
    sub.add_parser("sync", aliases=["update", "refresh"], help="Refresh the package index from the mirror",
                   parents=[verbosity])

    # -- upgrade --
    p_upgrade = sub.add_parser("upgrade", help="Upgrade installed packages",
                               parents=[verbosity])
    p_upgrade.add_argument(
        "packages", nargs="*", metavar="package",
        help="Specific packages to upgrade. With no packages and no --all, "
             "pkm refuses to mass-modify the system.",
    )
    p_upgrade.add_argument(
        "--all", action="store_true", dest="upgrade_all",
        help="Upgrade every upgradable package. Required (with positional "
             "packages as the alternative) for non-empty scope; bare "
             "`pkm upgrade` refuses to act per Q3 confirmation gate.",
    )
    p_upgrade.add_argument(
        "--yes", "-y", action="store_true", dest="upgrade_yes",
        help="Skip the [y/N] confirmation prompt after the plan summary "
             "prints. Non-tty stdin + no --yes = hard error.",
    )
    p_upgrade.add_argument(
        "--dry-run", action="store_true", dest="upgrade_dry_run",
        help="Print the plan summary and exit 0 without modifying anything.",
    )
    p_upgrade.add_argument(
        "--allow-downgrade", action="store_true",
        help="Treat any version mismatch as upgradable, including repo-older-than-installed "
             "(used to roll back after a bad release).",
    )
    p_upgrade.add_argument(
        "--ignore-holds", action="store_true",
        help="Override `pkm hold` and upgrade held packages too. Intended "
             "for emergency security upgrades; surface a justification when "
             "using this flag in scripted contexts.",
    )
    p_upgrade.add_argument(
        "--security-only", action="store_true", dest="upgrade_security_only",
        help="Filter upgrade candidates to entries flagged security=true in "
             "the repository index. The flag is set at index-generation time "
             "from docs/governance/security-advisories.yml, which InterGenOS "
             "maintainers hand-curate as patches land.",
    )
    p_upgrade.add_argument(
        "--allow-kernel-replace", action="store_true",
        dest="upgrade_allow_kernel_replace",
        help="Required to upgrade `linux-kernel`. Kernel upgrades can leave "
             "a system unbootable on partial failure (O-002); pkm refuses to "
             "touch the running kernel package without this explicit flag.",
    )

    # -- search --
    p_search = sub.add_parser("search", aliases=["find"], help="Search packages")
    p_search.add_argument("term")

    # -- info --
    p_info = sub.add_parser("info", aliases=["show"], help="Show package details")
    p_info.add_argument("package")

    # -- files --
    p_files = sub.add_parser("files", aliases=["contents"], help="List files in a package")
    p_files.add_argument("package")

    # -- provides --
    p_provides = sub.add_parser("provides", help="Find which package owns a file")
    p_provides.add_argument("file")

    # -- verify --
    p_verify = sub.add_parser("verify", help="Verify package integrity")
    p_verify.add_argument("package", nargs="?")
    p_verify.add_argument("--all", action="store_true", dest="verify_all")
    p_verify.add_argument(
        "--detail", action="store_true", dest="verify_detail",
        help="List the expected-absent paths under each named class",
    )
    p_verify_mode = p_verify.add_mutually_exclusive_group()
    p_verify_mode.add_argument(
        "--strict", action="store_const", const="strict", dest="verify_mode",
        help="Existence + SHA-256 content hash (default)",
    )
    p_verify_mode.add_argument(
        "--fast", action="store_const", const="fast", dest="verify_mode",
        help="Existence (lexists) only — sub-second per package",
    )
    p_verify.set_defaults(verify_mode="strict")

    # -- depends --
    p_depends = sub.add_parser("depends", aliases=["deps"], help="Show dependencies")
    p_depends.add_argument("package")
    p_depends.add_argument("--reverse", action="store_true", help="Show reverse dependencies")

    # -- history --
    p_history = sub.add_parser("history", help="Show operation history")
    p_history.add_argument("package", nargs="?")

    # -- import --
    p_import = sub.add_parser("import", help="Import existing text manifests into database")

    # -- vacuum --
    # Compaction is an explicit command and never a side effect. Decided
    # 2026-08-06 with the per-part progress standard: a migration that frees
    # a large number of rows says so in one line, and the user decides when
    # the whole-file rewrite happens.
    p_vacuum = sub.add_parser(
        "vacuum",
        help="Return unused space inside the package database to the filesystem",
        description=(
            "Compacts the pkm database file. Removing packages and collapsing "
            "duplicated records leave unused pages inside the file; the "
            "database reuses them but does not hand them back to the "
            "filesystem until it is rebuilt. This rewrites the database into "
            "a new file and swaps it in, which needs free disk space of about "
            "twice the current file size while both exist — so it checks for "
            "that space first and refuses rather than running out partway. "
            "Nothing runs this automatically. No installed file is touched "
            "and no package record changes."
        ),
    )
    p_vacuum.add_argument(
        "--dry-run", action="store_true", dest="vacuum_dry_run",
        help=("report how much space would be returned and whether there is "
              "room to do it, then exit without changing anything"),
    )

    # -- hook-baseline / record-hook-changes --
    # The source-build lane's half of the hook-managed content class. The
    # archive install path observes its own hook by snapshotting before and
    # after; a recipe's post_install is run by the build driver, after the
    # manifest is written and after `pkm import` has registered the rows, so
    # that lane had no observation at all. These two give it the same
    # before/after evidence, scoped to the package's own files.
    p_hook_base = sub.add_parser(
        "hook-baseline",
        help="Record a package's own file hashes before its post_install runs",
        description=(
            "Writes the current sha256 of every regular file the named package "
            "owns to a file, for comparison after the package's post_install "
            "has run. Reads only; changes nothing."
        ),
    )
    p_hook_base.add_argument("package")
    p_hook_base.add_argument("--out", required=True,
                             help="path to write the baseline to")

    p_hook_rec = sub.add_parser(
        "record-hook-changes",
        help="Record which of a package's own files its post_install rewrote",
        description=(
            "Compares the package's own files against a baseline written by "
            "`pkm hook-baseline` before its post_install ran. Files whose "
            "content changed across that window are recorded as hook-managed: "
            "existence is checked, the byte comparison against the payload "
            "hash is not, and the text manifest is re-emitted stating the "
            "class so the record survives a later import. Only the package's "
            "own files are considered; a file another package owns is never "
            "touched."
        ),
    )
    p_hook_rec.add_argument("package")
    p_hook_rec.add_argument("--baseline", required=True,
                            help="baseline file written by `pkm hook-baseline`")

    # -- refresh-baseline -- (Q4 .pkmnew accept-new helper)
    p_refresh = sub.add_parser(
        "refresh-baseline",
        help="Record the current /etc/* file content as the new baseline",
        description=(
            "Re-records the original_checksum for one or more tracked config "
            "files from the current live content. Use after manually merging "
            "a .pkmnew sidecar (e.g. `mv /etc/foo.conf.pkmnew /etc/foo.conf`) "
            "so subsequent upgrades treat the new content as the baseline "
            "for the user-edited detection check."
        ),
    )
    p_refresh.add_argument("paths", nargs="+", metavar="path",
                           help="One or more /etc/* paths (absolute or relative)")

    # -- check-updates -- (Q8 Phase A notification-surface substrate)
    p_check = sub.add_parser(
        "check-updates",
        help="Check for available package upgrades; write JSON summary",
        description=(
            "Compares installed package versions against the configured repos "
            "and writes a structured JSON summary to "
            "/var/lib/pkm/available-updates.json. Consumed by the systemd "
            "timer (Q8 Phase B) + GNOME notification extension (Q8 Phase C) + "
            "MOTD line (Q8 Phase D). NEVER auto-upgrades — informational only "
            "per the approved Q8 design."
        ),
    )
    p_check.add_argument(
        "--quiet", action="store_true",
        help="Suppress stdout; write JSON only (default for unattended timer use)",
    )

    # -- restart-services -- (Q5 user-driven service restart per O-029)
    p_restart = sub.add_parser(
        "restart-services",
        help="Restart system services after pkm upgrade",
        description=(
            "Restart systemd service units owned by installed pkm packages. "
            "pkm never auto-restarts daemons during upgrade (PRIME DIRECTIVE "
            "— user controls when their machine takes the downtime); this "
            "subcommand is the user-driven companion. --list classifies all "
            "installed packages; --all restarts every active service owned "
            "by a pkm package; positional unit names restart specific units."
        ),
    )
    p_restart_mode = p_restart.add_mutually_exclusive_group()
    p_restart_mode.add_argument(
        "--list", action="store_true", dest="restart_list",
        help="Print restart classification for all installed packages "
             "(reboot / restart / none)",
    )
    p_restart_mode.add_argument(
        "--all", action="store_true", dest="restart_all",
        help="Restart every currently-active service owned by an installed "
             "pkm package",
    )
    p_restart.add_argument(
        "services", nargs="*", metavar="service",
        help="Specific systemd unit names to restart (ignored with "
             "--list or --all)",
    )

    # -- hold / unhold / mark / autoremove -- (Q9 install_reason + hold)
    p_hold = sub.add_parser(
        "hold",
        help="Hold a package — exclude it from `pkm upgrade --all`",
        description=(
            "Sets held=1 on the named package(s). Held packages are "
            "skipped by `pkm upgrade` and refuse explicit `pkm upgrade "
            "<name>` invocations until released via `pkm unhold`. Use "
            "`pkm upgrade --ignore-holds` for emergency security overrides."
        ),
    )
    p_hold.add_argument("packages", nargs="+", metavar="package")

    p_unhold = sub.add_parser(
        "unhold",
        help="Release a hold on a package",
    )
    p_unhold.add_argument("packages", nargs="+", metavar="package")

    p_mark = sub.add_parser(
        "mark",
        help="Mark a package as manually- or dependency-installed",
        description=(
            "Updates the install_reason field. 'auto' (dependency) makes "
            "the package eligible for autoremove if no rdeps point to it; "
            "'manual' protects it from autoremove regardless of rdep state."
        ),
    )
    p_mark.add_argument("reason", choices=["auto", "manual"])
    p_mark.add_argument("packages", nargs="+", metavar="package")

    p_autoremove = sub.add_parser(
        "autoremove",
        help="Remove orphaned dependency-installed packages with no rdeps",
        description=(
            "Removes packages where install_reason='dependency' AND no "
            "currently-installed package depends on them. Manual-installed "
            "packages are never touched. Run after upgrades that drop "
            "dependencies to reclaim disk."
        ),
    )
    p_autoremove.add_argument(
        "--yes", action="store_true", dest="autoremove_yes",
        help="Skip the [y/N] confirmation prompt",
    )
    p_autoremove.add_argument(
        "--dry-run", action="store_true", dest="autoremove_dry_run",
        help="List orphans without removing anything; exit 0",
    )

    # -- iso-prep -- (D-014 Path-b ISO curation, 2026-05-28 walk)
    p_iso_prep = sub.add_parser(
        "iso-prep",
        help="Remove a list of packages from the system for ISO assembly",
        description=(
            "Removes every package named in --packages-from from the "
            "system, in a topologically-sound order that respects pkm's "
            "runtime-dep graph. Used by the live-ISO build pipeline to "
            "evict iso_include:false packages from the chroot before "
            "mksquashfs runs (Path-b of the D-014 ISO curation). Aborts "
            "if any iso_include:true package depends on an iso_include:"
            "false target — that indicates a metadata bug we want "
            "surfaced at build time rather than masked by `pkm remove "
            "--force`. The input names list is normally produced by "
            "scripts/derive-iso-exclusions.py --mode=names."
        ),
    )
    p_iso_prep.add_argument(
        "--packages-from", required=True, metavar="FILE",
        dest="iso_prep_packages_from",
        help="Path to a text file listing package names to remove, one "
             "per line. Blank lines and `#` comments are ignored.",
    )
    p_iso_prep.add_argument(
        "--yes", action="store_true", dest="iso_prep_yes",
        help="Skip the [y/N] confirmation prompt",
    )
    p_iso_prep.add_argument(
        "--dry-run", action="store_true", dest="iso_prep_dry_run",
        help="Print the removal plan + estimated reclaimed size "
             "without modifying the system; exit 0",
    )

    # -- cache -- (O-013 cache GC)
    p_cache = sub.add_parser(
        "cache",
        help="Manage the pkm download + rollback caches",
        description=(
            "Inspect and prune the pkm caches under /var/cache/pkm/: "
            "packages/ (each upgrade adds a new archive and the old one "
            "stays) and rollback/ (each `pkm upgrade` writes a "
            "pre-upgrade snapshot so failed installs can be reverted). "
            "One subcommand: clean (remove archives per policy; "
            "--rollback switches target to the rollback cache)."
        ),
    )
    p_cache_sub = p_cache.add_subparsers(dest="cache_action", metavar="action")

    p_cache_clean = p_cache_sub.add_parser(
        "clean",
        help="Remove cached archives by policy",
    )
    p_cache_clean_mode = p_cache_clean.add_mutually_exclusive_group()
    p_cache_clean_mode.add_argument(
        "--keep-current", action="store_true", dest="cache_keep_current",
        help="Default. Per package: keep the archive matching the installed "
             "version; remove others. Packages not installed have all their "
             "cached archives removed.",
    )
    p_cache_clean_mode.add_argument(
        "--keep", type=int, metavar="N", dest="cache_keep_n",
        help="Per package: keep the N most-recent archives by mtime; remove "
             "older ones. Useful for rollback-availability tuning.",
    )
    p_cache_clean_mode.add_argument(
        "--all", action="store_true", dest="cache_all",
        help="Remove every cached archive. Subsequent installs re-download.",
    )
    p_cache_clean_mode.add_argument(
        "--rollback", action="store_true", dest="cache_rollback",
        help="Operate on /var/cache/pkm/rollback/ instead of the packages "
             "cache. Per package: keep the most recent rollback archive for "
             "installed packages; remove older entries (and all entries for "
             "packages no longer installed). Each `pkm upgrade` writes a "
             "fresh archive here, so without periodic cleanup the directory "
             "grows unbounded.",
    )

    args = parser.parse_args()

    # Natural-language aliases — pkm's "Natural-language CLI" positioning
    # (README.md:39) accepts what users naturally type. Each alias resolves
    # to its canonical command name before dispatch so the if/elif chain
    # below stays single-name-per-operation.
    _COMMAND_ALIASES = {
        "sync": "update", "refresh": "update",
        "uninstall": "remove",
        "find": "search",
        "show": "info",
        "ls": "list",
        "contents": "files",
        "deps": "depends",
    }
    if args.command in _COMMAND_ALIASES:
        args.command = _COMMAND_ALIASES[args.command]

    # PKM-A28: set the process-wide prose verbosity ONCE from argv so every
    # command's wrapped-prose output (the module-level emit_* helpers) honors
    # -q/-v — not just the four commands that build their own Reporter. The
    # mutually-exclusive flags are validated at the parser layer.
    if getattr(args, "quiet", False):
        set_process_level(QUIET)
    elif getattr(args, "verbose", False):
        set_process_level(VERBOSE)
    else:
        set_process_level(NORMAL)

    if not args.command:
        parser.print_help()
        return

    # Root gate. The mutating commands modify the system (the pkm DB under
    # /var/lib/igos, the live filesystem, the repo cache, the lock) and need
    # root. Run as a normal user they would otherwise blow up further down
    # with a raw PermissionError traceback — precisely the scary, opaque
    # output the PRIME DIRECTIVE rejects. Advise the user plainly and exit.
    # geteuid is the pragmatic gate (pkm has no polkit/group path today).
    # EXEMPT a dry-run PREVIEW: it modifies nothing (read-only DB + cache, no
    # lock, plan-only), so it is meant to run unprivileged — the notifier's
    # top-bar click launches `pkm upgrade --all --dry-run` as the desktop user.
    # A named real mutation (no --dry-run) still refuses under non-root here.
    dry_run_preview = _is_dry_run_invocation(args)
    if (args.command in PKM_MUTATING_COMMANDS and os.geteuid() != 0
            and not dry_run_preview):
        print(
            f"pkm: '{args.command}' changes the system and must be run as root.",
            file=sys.stderr,
        )
        print(
            f"     Try:  sudo {' '.join(['pkm'] + sys.argv[1:])}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Open the structured-trace sink for this pkm invocation. When the
    # caller (build pipeline -> pkg-functions.sh -> pkm import) has already
    # exported IGOS_TRACE_RUNID + IGOS_TRACE_START_TS, the sink inherits
    # those and the pkm trail joins the parent build trail by shared runid.
    # When pkm is invoked standalone on the installed system, a fresh runid
    # is generated and the trail is per-invocation.
    if _TRACE_AVAILABLE:
        try:
            _trace.init_package_trace(f"pkm-{args.command}", phase=args.command)
            _trace.trace_event(
                "pkm_invoke",
                subcommand=args.command,
                argv=sys.argv[1:],
                cwd=str(Path.cwd()),
                version=__version__,
            )
        except Exception:
            pass

    # B7/S-D 2: only the DB-init commands auto-create the SQLite file. Every
    # other command surfaces FileNotFoundError with a diagnostic message
    # rather than silently auto-creating an empty DB. Prime Directive:
    # transparency over convenience for state inspection.
    create_if_missing = args.command in PKM_DB_INIT_COMMANDS
    # A dry-run preview opens the DB read-only (immutable) too, so an
    # unprivileged preview against the root-owned pkm.db cannot write and any
    # accidental write attempt in a preview path fails closed rather than
    # mutating state.
    read_only = (args.command in PKM_READONLY_COMMANDS) or dry_run_preview
    try:
        db = PackageDB(args.db, create_if_missing=create_if_missing,
                       read_only=read_only)
    except FileNotFoundError as e:
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event(
                    "pkm_db_open_failed",
                    subcommand=args.command, error=str(e),
                )
            except Exception:
                pass
        print(f"pkm: {e}", file=sys.stderr)
        sys.exit(2)
    except PermissionError as e:
        # Belt-and-suspenders for the read-only commands (the mutating set
        # is already gated above): a non-root user hitting a root-owned DB
        # gets a clean message, not a traceback.
        print(f"pkm: cannot access the package database: {e}", file=sys.stderr)
        print(
            f"     This usually needs root — try:  sudo "
            f"{' '.join(['pkm'] + sys.argv[1:])}",
            file=sys.stderr,
        )
        sys.exit(1)

    # PKM-A07: dispatch through a table and PROPAGATE the handler's exit code.
    # Several handlers (iso-prep, restart-services, refresh-baseline, cache,
    # hold/unhold/mark, autoremove) return a nonzero code on failure; calling
    # them as bare statements discarded it, so a failed command exited 0 and a
    # pipeline gating on e.g. `pkm iso-prep` saw success while the chroot was
    # left "in a partial state". A handler that returns None/0 still exits 0.
    _dispatch = {
        "install": cmd_install,
        "install-helper": cmd_install_helper,
        "remove": cmd_remove,
        "reinstall": cmd_reinstall,
        "update": cmd_update,
        "upgrade": cmd_upgrade,
        "list": cmd_list,
        "search": cmd_search,
        "info": cmd_info,
        "files": cmd_files,
        "provides": cmd_provides,
        "verify": cmd_verify,
        "depends": cmd_depends,
        "history": cmd_history,
        "import": cmd_import,
        "hook-baseline": cmd_hook_baseline,
        "record-hook-changes": cmd_record_hook_changes,
        "refresh-baseline": cmd_refresh_baseline,
        "restart-services": cmd_restart_services,
        "hold": cmd_hold,
        "unhold": cmd_unhold,
        "mark": cmd_mark,
        "autoremove": cmd_autoremove,
        "iso-prep": cmd_iso_prep,
        "check-updates": cmd_check_updates,
        "cache": cmd_cache,
        "vacuum": cmd_vacuum,
    }
    try:
        with _pkm_mutation_lock(args.command, dry_run=dry_run_preview):
            handler = _dispatch.get(args.command)
            if handler is None:
                emit_error(f"unknown command: {args.command}")
                sys.exit(2)
            rc = handler(db, args)
            if rc:
                sys.exit(rc)
    finally:
        db.close()


# ------------------------------------------------------------------
# Command implementations
# ------------------------------------------------------------------

def _rollback_proprietary(db, pkg_name, reporter):
    """Remove a half-installed proprietary package after a declined EULA or a
    helper failure, so `[installed]` never shows <app> without the real app."""
    try:
        # No pre-remove hook: this undoes an install that never completed
        # (declined EULA, failed helper). The package's payload was never
        # in service, so there is nothing for a hook to stop, unload or
        # clean up — and the hook itself may be half-deployed.
        PackageRemover(db).remove(pkg_name, force=True, reporter=reporter,
                                  run_pre_remove_hook=False)
    except Exception as e:  # rollback is best-effort; surface but don't crash
        reporter.warn(f"could not fully roll back {pkg_name}: {e}")


def _proprietary_install(db, installer, repo, reporter, pkg_name, payload_license,
                         replace=False):
    """pkm 2b items 1+3 — unified `pkm install <app>` for a proprietary-download
    package (one whose .PKGINFO carries payload_license).

    Flow: the continue-prompt (the pause) → install the package infra
    (igos-install-<app>) → run its helper, which downloads the real app, runs
    the vendor "I ACCEPT" EULA, and merges the footprint into ONE honest <app>
    DB entry. A declined EULA (or a helper failure) rolls the package back. Never
    auto-accepts a EULA non-interactively (security-first + PRIME DIRECTIVE).

    replace=True is the `pkm reinstall <app>` path (PKM-A19): it skips the
    "already installed" refusal so the helper RE-RUNS and re-fetches the
    proprietary payload. The stub infra is already present (existing row), so
    `laid_down` stays False and only the helper re-download happens.
    """
    # The helper package ships in the default install set, so a DB row for
    # <app> is normally already present — that means the HELPER is there, not
    # the app. Refuse as "already installed" ONLY when the real app footprint
    # exists (payload_installed); otherwise this is the expected "helper present,
    # app not yet downloaded" state and we proceed to run the download. On the
    # reinstall path (replace=True) the refusal is skipped — re-fetching the
    # payload IS the requested operation.
    existing = db.get_installed(pkg_name)
    if (not replace and existing and not existing.get("superseded_by")
            and payload_installed(pkg_name)):
        reporter.info(
            f"{pkg_name} is already installed. Use `pkm reinstall {pkg_name}` "
            f"to replace."
        )
        return

    # Non-interactive runs are refused unless this machine ALREADY holds an
    # acceptance record for the package. The property being protected is that
    # pkm never accepts a vendor licence on the user's behalf — and a recorded
    # acceptance is the user having accepted it here, which is why consulting
    # the record does not weaken the rule. A machine with no record is refused
    # exactly as before.
    #
    # Refusing regardless of the record had a cost that was not obvious: the
    # only remaining way to update an already-accepted payload without a human
    # present was to run the install helper directly, and a direct helper run
    # is precisely the path pkm cannot ingest — the payload lands on disk while
    # the database goes on describing the previous one. The strict-looking gate
    # was pushing every unattended update onto the one road that breaks
    # `pkm verify`.
    interactive = sys.stdin.isatty()
    already_accepted = acceptance_record_exists(pkg_name)
    if not interactive and not already_accepted:
        reporter.error(
            f"'{pkg_name}' downloads proprietary software that requires accepting "
            f"a vendor EULA ({payload_license}); pkm will not accept a license on "
            f"your behalf non-interactively, and this machine holds no acceptance "
            f"record for it. Run this in an interactive terminal."
        )
        sys.exit(1)

    # The pause (operator-authored). The helper runs the actual "I ACCEPT" EULA;
    # this is the discoverability gate that warns before any download.
    print()
    reporter.info(
        f"Installing the {pkg_name} package will download proprietary software "
        f"that may require the acceptance of a vendor EULA (End User License "
        f"Agreement)."
    )
    reporter.info(f"Vendor license: {payload_license}")
    if interactive:
        try:
            reply = input("  Continue? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            reply = ""
        if reply not in ("y", "yes"):
            reporter.info("Installation cancelled.")
            return
    else:
        # Reached only with an acceptance record in hand. The pause exists to
        # warn a human before a proprietary download; with nobody at the
        # terminal there is nobody to warn, so it is stated and the run
        # proceeds. The helper still consults its OWN acceptance record and
        # still prompts if it finds none — reading end-of-file there makes it
        # exit as a decline, which pkm reports as a clean cancellation.
        reporter.info(
            f"No terminal attached; proceeding on the acceptance record this "
            f"machine already holds for {pkg_name}."
        )

    # 1. Ensure the package infra (igos-install-<app> + keyring + the <app> DB
    #    entry) is present. It ships in the default install set, so on a normal
    #    system this is already done — only lay it down if the row is missing.
    #    `laid_down` tracks whether WE installed it this invocation, so a later
    #    decline/failure rolls back only what we added (never the pre-shipped
    #    base helper).
    laid_down = not existing
    if laid_down:
        archive = installer._find_archive(pkg_name)
        expected_sha = None
        if not archive:
            repo_pkg = repo.get_package(pkg_name)
            if not repo_pkg:
                reporter.error(
                    f"'{pkg_name}' is not available locally or from any configured "
                    f"repository."
                )
                return
            dl_ok, dl_result = repo.download_package(pkg_name, reporter=reporter)
            if not dl_ok:
                reporter.error(str(dl_result))
                return
            archive = dl_result
            expected_sha = repo_pkg.get("sha256")
        inst_ok, inst_msg = installer.install(
            pkg_name, archive_path=archive, expected_sha256=expected_sha,
            reporter=reporter,
        )
        if not inst_ok:
            reporter.error(f"installing {pkg_name}: {inst_msg}")
            return

    # 2. Run the helper — downloads the real app + its own "I ACCEPT" EULA and
    #    records the footprint manifest (which is what makes payload_installed
    #    true, i.e. [installed] honest).
    helper = installer._find_helper(pkg_name)
    if not helper:
        reporter.error(
            f"'{pkg_name}' installed but ships no install helper "
            f"(igos-install-{pkg_name}); cannot complete the download."
        )
        if laid_down:
            _rollback_proprietary(db, pkg_name, reporter)
        return
    ok, msg, declined = installer._run_helper(pkg_name, helper)
    if ok:
        reporter.info(msg)
        return

    # 3. Declined or failed. Roll back only if WE laid the package down; a
    #    pre-shipped helper stays (the app footprint was never recorded, so
    #    [installed] already reads honestly as not-installed).
    if laid_down:
        _rollback_proprietary(db, pkg_name, reporter)
        suffix = f"; rolled back {pkg_name}."
    else:
        suffix = "."
    if declined:
        reporter.info(f"Installation cancelled ({msg}){suffix}")
    else:
        reporter.error(f"{msg}{suffix}")


def _continue_into_payload_if_helper(db, installer, repo, reporter, name):
    """After a package is deployed, enter its payload flow if it is a helper.

    WHY THIS IS ASKED TWICE. Routing to the proprietary-payload flow tests
    is_download_helper(), which is the ON-DISK presence of
    /usr/bin/igos-install-<name>. On a first install from the repository that
    binary is not there when the routing decision is first made — it arrives
    INSIDE the archive the transaction is about to deploy. So the transaction
    laid the helper down, reported success, and stopped with the actual
    application absent, while the application's own launcher advised running
    the very command that had just claimed success. `pkm install steam` ended
    exactly there.

    The decision is therefore re-asked AFTER deployment, when the on-disk
    signal finally exists, and the payload flow is entered in the SAME
    invocation. The two-step alternative — stop and print the next command —
    leaves a transaction reporting success with the payload missing, which is
    the silent-failure shape rather than a verified one.

    No-op for a package that is not a helper, and for one whose payload is
    already installed.
    """
    if not is_download_helper(name) or payload_installed(name):
        return
    rp = repo.get_package(name)
    payload_license = (rp or {}).get("payload_license") or \
        "a proprietary vendor license (shown during install)"
    reporter.blank()
    reporter.info(
        f"{name} installs its application by download. Continuing into that "
        f"step now — the package just deployed is the installer for it, not "
        f"the application."
    )
    _proprietary_install(db, installer, repo, reporter, name, payload_license)


def cmd_install(db, args):
    installer = PackageInstaller(db)
    repo = RepoManager()
    reporter = Reporter.from_args(args)

    if args.archive and len(args.packages) > 1:
        reporter.error(
            f"--archive {args.archive} cannot be used with multiple "
            f"packages ({', '.join(args.packages)}). The archive is a "
            f"single-package artifact. Run separately per package, or omit "
            f"--archive to fetch all packages from the repo."
        )
        sys.exit(1)

    # Chronicle: pre-transaction restore point. Fires before the per-package
    # loop mutates the live filesystem, so a registered backup engine can
    # capture the current bytes of exactly this transaction's footprint first.
    # No-op when no handler is installed (see pkm/pretxn.py); a handler failure
    # is loud but never blocks the install.
    from . import pretxn
    pretxn.run_pre_transaction_hook(
        db, "install", args.packages,
        reason=f"pre-transaction install: {', '.join(args.packages)}",
        reporter=reporter,
    )

    # 3.0-F28: names successfully installed in THIS transaction, so a single
    # loud reboot banner can be aggregated + printed once at the end (rather
    # than one advisory lost per-package in the install scroll).
    installed_this_txn = []

    for pkg_name in args.packages:
        archive = args.archive if len(args.packages) == 1 else None

        # pkm 2b: proprietary-download detection. A package whose repo index /
        # .PKGINFO carries payload_license (vscode/chrome/...) is routed through
        # the unified continue-prompt + helper flow instead of a bare archive
        # install. An explicit --archive install falls through to the normal
        # path (the helper can still be run via the package's own infra).
        if not archive:
            _rp = repo.get_package(pkg_name)
            _payload_license = (_rp or {}).get("payload_license")
            # Offline-reliable routing: repo.get_package() only sees a synced
            # repo index (/var/cache/pkm/db), which a clean ISO install does
            # NOT have — so the index-only check above missed helper packages
            # whose stub ships in the base set, and `pkm install <app>` fell
            # through to the generic installer and no-op'd "already installed"
            # on the stub row (the GBC003.2 .241 finding). The signed helper
            # package is on disk regardless of network/index, so its on-disk
            # marker (igos-install-<app>) routes correctly offline. The real
            # per-vendor EULA is still shown by the helper's own "I ACCEPT"
            # gate; a generic label keeps the pre-download consent pause intact
            # when the index isn't present to supply the precise license name.
            if not _payload_license and is_download_helper(pkg_name):
                _payload_license = "a proprietary vendor license (shown during install)"
            if _payload_license:
                _proprietary_install(
                    db, installer, repo, reporter, pkg_name,
                    _payload_license,
                )
                continue

        # ALREADY-INSTALLED ROUTING, BY RELEASE (before any acquisition).
        #
        # `pkm install <installed-pkg>` used to answer "already installed, use
        # reinstall" — true, and the line that invited a silent downgrade,
        # because it never said which side was newer. Reinstalling a package
        # whose installed build is AHEAD of the mirror moves it backwards, and
        # the advice gave no way to know that.
        #
        # Now the two releases are compared and named: installed-newer states
        # it and stops, index-newer routes to the upgrade path, identical says
        # so. No branch here replaces anything — `pkm install` still never
        # overwrites an installed package.
        _already = db.get_installed(pkg_name)
        if _already and not _already.get("superseded_by") and not archive:
            _state, _msg = txn.installed_side(
                pkg_name, _already, repo.get_package(pkg_name)
            )
            if _state == "index-newer":
                reporter.info(_msg)
                reporter.info(
                    f"Run `pkm upgrade {pkg_name}` to install it. Nothing was "
                    f"changed."
                )
                continue
            if _state in ("installed-newer", "same", "unknown"):
                reporter.info(_msg)
                continue

        # Try local archive first
        if archive:
            trust_mode = getattr(args, "archive_trust", "strict")

            # Compute SHA256 for all modes (used for display + matching)
            import hashlib
            try:
                sha = hashlib.sha256()
                with open(archive, "rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        sha.update(chunk)
                archive_sha = sha.hexdigest()
            except (IOError, OSError) as e:
                reporter.error(f"cannot read archive: {e}")
                sys.exit(1)

            reporter.step("Archive", str(archive))
            reporter.step("SHA256", archive_sha)

            # Cross-check against repo index
            repo_match = False
            repo_pkg = None
            try:
                repo_pkg = repo.get_package(pkg_name)
                if repo_pkg and repo_pkg.get("sha256") == archive_sha:
                    repo_match = True
                    reporter.verify(
                        f"sha256 matches repository index for {pkg_name} "
                        f"{repo_pkg.get('version','?')} ✓"
                    )
            except Exception:
                repo_pkg = None

            if not repo_match and repo_pkg and repo_pkg.get("sha256"):
                reporter.error("archive SHA256 does not match repository index!")
                reporter.info(f"    archive: {archive_sha}")
                reporter.info(f"    repo:    {repo_pkg['sha256']}")

            # Trust gate (H3)
            if trust_mode == "repo-only" and not repo_match:
                reporter.error(
                    "--archive-trust=repo-only requires archive SHA256 to "
                    "match the repository index. Use --archive-trust=loose "
                    "to override."
                )
                continue
            elif trust_mode == "strict" and not repo_match:
                reporter.error(
                    "--archive-trust=strict requires SHA256 match against "
                    "repository index. Use --archive-trust=loose to override."
                )
                continue
            elif trust_mode == "loose":
                reporter.warn(
                    "--archive-trust=loose — skipping repo verification. "
                    "Verify SHA256 independently before trusting this archive."
                )

        # S5-1 (security-review 2026-07-01): a bare `pkm install <name>` may
        # resolve a cached archive in ARCHIVE_DIR. Trust it ONLY when its sha256
        # matches the signed repo index — the same gate the --archive path applies
        # — and thread that reference into install() so the integrity check fires.
        # A stale/unverifiable cache is deliberately LEFT UNSET here: install() is
        # then called with archive_path=None, its S5-1 backstop refuses to extract
        # the unverified local file, and the fetch-from-repo fallback below routes
        # on that refusal to pull the verified package instead. Never install an
        # unverified local cache.
        if not archive:
            _local = installer._find_archive(pkg_name)
            if _local:
                _rp = repo.get_package(pkg_name)
                if _rp and _rp.get("sha256") == _sha256(str(_local)):
                    archive, archive_sha = str(_local), _rp["sha256"]
                    reporter.verify(
                        f"cached archive {_local.name} matches the signed index ✓")
                elif _rp and _rp.get("sha256"):
                    reporter.warn(
                        f"cached archive {_local.name} does not match the signed "
                        f"index for {pkg_name} — fetching the verified package")
                else:
                    reporter.warn(
                        f"cached archive {_local.name} for {pkg_name} is not in the "
                        f"signed index — fetching the verified package (use "
                        f"`pkm install --archive {_local} --archive-trust loose` to "
                        f"force a deliberate local install)")

        # L-021: pass the SHA256 we computed for --archive path through
        # to installer.install for the TOCTOU re-verification gate.
        ok, msg = installer.install(
            pkg_name, archive_path=archive,
            expected_sha256=(archive_sha if archive else None),
            reporter=reporter,
        )
        if ok:
            # reporter already emitted the deploy file-list + completion line.
            installed_this_txn.append(pkg_name)
            # THE SECOND DOOR INTO THE SAME SILENT FAILURE. This path installs
            # from a verified cached archive rather than a fresh resolution,
            # and it reaches a completed install without passing through the
            # resolution block below — so a helper package installed from the
            # cache would deploy the helper, report success, and stop with the
            # payload absent, exactly as the repository path did. Fixed at the
            # mechanism, not at the one door where it was observed.
            _continue_into_payload_if_helper(
                db, installer, repo, reporter, pkg_name)
            continue

        # No usable local archive — either none is cached, or a cached one was
        # unverifiable and install()'s S5-1 backstop refused it. Either way, fetch
        # the verified package from the repo.
        if ("No archive found" in msg or "not found" in msg.lower()
                or "no signed-index verification reference" in msg):
            reporter.info(f"Resolving '{pkg_name}'…")

            # Resolve dependencies
            dep_ok, deps = repo.resolve_dependencies(pkg_name, db)
            if not dep_ok:
                reporter.error(str(deps))
                sys.exit(1)

            # THE CONFIRMATION GATE — BEFORE ANY DOWNLOAD.
            #
            # `sudo pkm install steam` resolved a 40-package lib32 closure and
            # installed every one of it without asking anything. The
            # transaction was correct; the user was never given the chance to
            # see its size and decline. The gate fires whenever the resolution
            # goes BEYOND the package that was named — installing exactly what
            # was asked for needs no summary of itself.
            #
            # It prints the count, the download total, the installed total and
            # the full resolved name list, then asks. Default is YES: the gate
            # exists so nobody is surprised, not to make routine installs
            # adversarial. Headless states the acceptance rather than asking,
            # the same pattern the proprietary-payload pause already uses.
            _plan = txn.TransactionPlan(
                pkg_name,
                [(d, repo.get_package(d)) for d in deps],
                action="Install",
            )
            if _plan.beyond_requested:
                if not txn.confirm(
                    _plan, reporter,
                    assume_yes=getattr(args, "assume_yes", False),
                ):
                    sys.exit(1)
                reporter.blank()

            # The [N/M] counter on each completion line belongs to the
            # transaction, so it is opened here and closed after the loop.
            _subject_width = max(
                (len(f"{d} {txn.format_vr(repo.get_package(d))}") for d in deps),
                default=0,
            )
            reporter.begin_transaction(len(deps), name_width=_subject_width)

            # Q6 (O-025): free-disk preflight across the resolved-dep queue.
            from . import preflight
            from .repo import REPO_PKG_CACHE
            dep_sizes = []
            for d in deps:
                dpkg = repo.get_package(d)
                if dpkg:
                    dep_sizes.append(int(dpkg.get("size") or 0))
            if any(s > 0 for s in dep_sizes):
                required = preflight.estimate_required_space(dep_sizes)
                check = preflight.check_free_space(required, REPO_PKG_CACHE)
                if not check["ok"]:
                    reporter.error(
                        preflight.format_preflight_failure(check, REPO_PKG_CACHE)
                    )
                    sys.exit(1)

            for dep_name in deps:
                dl_ok, dl_result = repo.download_package(
                    dep_name, reporter=reporter
                )
                if not dl_ok:
                    reporter.error(str(dl_result))
                    sys.exit(1)

                # L-021: extract expected sha256 from repo index for the
                # installer-side TOCTOU re-verification gate.
                dep_pkg = repo.get_package(dep_name)
                dep_sha = dep_pkg.get("sha256") if dep_pkg else None
                # Q9: the user-requested package is the install_reason=
                # 'manual' anchor; everything else in the resolved queue
                # is dep-resolution-pulled. autoremove later uses this to
                # distinguish "remove the package I asked for" from
                # "remove an orphan I never explicitly wanted".
                dep_reason = "manual" if dep_name == pkg_name else "dependency"
                inst_ok, inst_msg = installer.install(
                    dep_name, archive_path=dl_result,
                    expected_sha256=dep_sha,
                    install_reason=dep_reason,
                    reporter=reporter,
                )
                if not inst_ok:
                    reporter.error(f"installing {dep_name}: {inst_msg}")
                    sys.exit(1)
                installed_this_txn.append(dep_name)

            reporter.end_transaction()
            reporter.transaction_footer(
                count=len(deps), installed_bytes=_plan.installed_bytes,
            )

            _continue_into_payload_if_helper(
                db, installer, repo, reporter, pkg_name)
        else:
            reporter.error(msg)
            sys.exit(1)

    # 3.0-F28 / Q5: after the whole transaction, print ONE consolidated,
    # strongest-first "Next steps" block naming every just-installed package
    # that needs a reboot, a service restart, or a re-login before its payload
    # is active. This closes the silent-install PD failure — e.g. nvidia's
    # kernel modules land behind the nouveau blacklist and stay inactive until
    # reboot — and now also surfaces service-restart / desktop-relogin needs in
    # the same place rather than only reboot.
    _print_transaction_next_steps(db, installed_this_txn)

    # Refresh the update advisory so the notifier's top-bar count reflects what
    # this transaction just installed, not the last scheduled check (offline;
    # never fails the transaction).
    if installed_this_txn:
        refresh_available_updates_after_transaction(db)


def cmd_install_helper(db, args):
    installer = PackageInstaller(db)
    name = args.package

    # EULA install-helper hybrid-model resolution path (2026-05-28).
    # Names ending in `-eula` or living under EULA_HELPER_DIR resolve to
    # the EULA helper surface rather than the proprietary-userspace
    # download-helper surface. This lets the operator drive the EULA
    # flow directly for testing (`pkm install-helper nvidia-eula`)
    # without going through a full `pkm install nvidia` first.
    from .installer import EULA_HELPER_DIR
    eula_helper_path = installer._find_eula_helper(name)
    if eula_helper_path is not None:
        # Surface as a direct EULA-helper invocation. Exit code mirrors
        # the helper's exit code so the operator sees the same outcome
        # they would see via the install gate.
        ok, msg = installer._run_eula_helper(name, name)
        if ok:
            emit_info(f"EULA helper '{name}' completed successfully.")
            return
        emit_error(msg)
        sys.exit(1)

    helper = installer._find_helper(name)
    if not helper:
        # Try common aliases
        aliases = {
            "google-chrome": "chrome",
            "code": "vscode",
            "vs-code": "vscode",
            "claude": "claude-code",
        }
        alt = aliases.get(name)
        if alt:
            helper = installer._find_helper(alt)
            if helper:
                name = alt

    if not helper:
        available = []
        from pathlib import Path
        for f in Path("/usr/bin").glob("igos-install-*"):
            available.append(f.name.replace("igos-install-", ""))
        # Surface EULA helpers in the available-list output too so
        # `pkm install-helper` discoverability covers both surfaces.
        eula_available = []
        if EULA_HELPER_DIR.is_dir():
            for f in EULA_HELPER_DIR.iterdir():
                if f.is_file() and os.access(str(f), os.X_OK):
                    eula_available.append(f.name)
        if available or eula_available:
            emit_info(f"No install helper found for '{name}'")
            if available:
                emit_info(f"Available download-helpers: {', '.join(sorted(available))}")
            if eula_available:
                emit_info(f"Available EULA-helpers: {', '.join(sorted(eula_available))}")
        else:
            emit_info("No install helpers found on this system")
        sys.exit(1)

    ok, msg, declined = installer._run_helper(name, helper)
    if ok:
        emit_info(msg)
    elif declined:
        # Item 4: a declined EULA is a user choice, not a failure.
        emit_info(f"Installation cancelled ({msg}).")
        return
    else:
        emit_error(msg)
        sys.exit(1)


def cmd_reinstall(db, args):
    """Reinstall packages — ACQUIRE the verified replacement BEFORE removing.

    Closes audit row O-001 (subcommand never existed) + H-010.

    Foot-gun fix (2026-06-14, PRIME DIRECTIVE): the old flow removed the
    package FIRST, then tried `installer.install(name)` with no archive —
    which only ever looks at the local archive cache (empty on a clean
    install) and the helper dir, never the repo. So a routine reinstall of
    a repo-available package always self-destructed into a "degraded state"
    the user had to manually recover. A package manager that breaks a
    working install during its most basic operation is the antithesis of a
    system the user can trust.

    New invariant: resolve + download + signature/sha-verify the replacement
    archive FIRST. Only once a verified archive is in hand do we remove the
    old copy and install the new one. If acquisition fails, the system is
    left exactly as it was — nothing is removed.
    """
    installer = PackageInstaller(db)
    remover = PackageRemover(db)
    repo = RepoManager()
    reporter = Reporter.from_args(args)

    for pkg_name in args.packages:
        existing = db.get_installed(pkg_name)
        if not existing:
            reporter.error(
                f"'{pkg_name}' is not installed; nothing to reinstall. "
                f"Use `pkm install {pkg_name}` instead."
            )
            sys.exit(1)

        # PKM-A19: a proprietary-download helper (vscode/chrome/...) must be
        # reinstalled by RE-RUNNING its helper to re-fetch the payload — NOT by
        # the generic acquire+remove+reinstall below, which only re-acquires
        # the stub archive, removes the package (deleting the downloaded
        # payload), and lays the stub back: the exact opposite of "replace",
        # and contradicting the advice _proprietary_install gives the user.
        if is_download_helper(pkg_name):
            _rp = repo.get_package(pkg_name)
            _payload_license = (_rp or {}).get("payload_license") or \
                "a proprietary vendor license (shown during install)"
            _proprietary_install(
                db, installer, repo, reporter, pkg_name, _payload_license,
                replace=True,
            )
            continue

        # --- THE DOWNGRADE GUARD, BEFORE ANYTHING IS ACQUIRED OR REMOVED ---
        #
        # This is the path that silently replaced forge release 133 with the
        # mirror's release 110. Both numbers were already in hand — the
        # installed release in the database row above, the resolved release in
        # the signed index — and nothing compared them. The comparison happens
        # here, before the acquire, so a refusal costs no download and touches
        # nothing.
        _resolved = repo.get_package(pkg_name)
        _decision = txn.downgrade_decision(
            pkg_name, existing, _resolved,
            allow_downgrade=getattr(args, "allow_downgrade", False),
        )
        if not _decision.ok:
            reporter.error(_decision.message)
            sys.exit(1)
        if _decision.kind == "downgrade":
            reporter.warn(_decision.message)

        # Release-bearing transaction line. The old line printed the version
        # only and named one side, so a replacement in either direction read
        # identically.
        if _resolved:
            reporter.info(
                f"Reinstalling {txn.describe_change(pkg_name, existing, _resolved)}"
                if txn.format_vr(existing) != txn.format_vr(_resolved)
                else f"Reinstalling {txn.describe_subject(pkg_name, existing)}"
            )
        else:
            reporter.info(f"Reinstalling {txn.describe_subject(pkg_name, existing)}")

        # --- ACQUIRE FIRST (nothing removed until this succeeds) ---
        # Prefer a local archive if one is cached; otherwise fetch + verify
        # from the repo. Either way we hold a verified archive path before
        # touching the installed copy.
        archive_path = installer._find_archive(pkg_name)
        expected_sha = None
        if archive_path:
            # S5-1 (security-review 2026-07-01): trust the cached archive ONLY
            # when its sha256 matches the signed index; otherwise discard it and
            # fetch the verified package. Reinstall passes archive_path
            # explicitly, so install()'s implicit-resolution backstop does not
            # fire here — this is reinstall's own copy of the same gate.
            _rp = repo.get_package(pkg_name)
            if _rp and _rp.get("sha256") == _sha256(str(archive_path)):
                expected_sha = _rp["sha256"]
                reporter.verify(
                    f"cached archive {archive_path.name} matches the signed index ✓")
            else:
                if _rp and _rp.get("sha256"):
                    reporter.warn(
                        f"cached archive {archive_path.name} does not match the "
                        f"signed index for {pkg_name} — fetching the verified package")
                else:
                    reporter.warn(
                        f"cached archive {archive_path.name} for {pkg_name} is not "
                        f"in the signed index — fetching the verified package")
                archive_path = None
        if not archive_path:
            repo_pkg = repo.get_package(pkg_name)
            if not repo_pkg:
                reporter.error(
                    f"cannot reinstall '{pkg_name}': no local archive cached "
                    f"and the package is not available from any configured "
                    f"repository. Run `pkm sync`, or check the package name. "
                    f"Nothing was removed — '{pkg_name}' is still installed."
                )
                sys.exit(1)
            dl_ok, dl_result = repo.download_package(pkg_name, reporter=reporter)
            if not dl_ok:
                reporter.error(
                    f"cannot reinstall '{pkg_name}': {dl_result}. Nothing was "
                    f"removed — '{pkg_name}' is still installed."
                )
                sys.exit(1)
            archive_path = dl_result
            expected_sha = repo_pkg.get("sha256")

        # --- now safe to remove + reinstall ---
        # No pre-remove hook: the package is not going away. Remove-then-
        # install is how reinstall replaces the files, and a hook written
        # for an uninstall would tear down state the replacement expects
        # to keep (nvidia's, for one, disables the units its own package
        # ships and nothing re-enables them).
        ok, rmsg = remover.remove(pkg_name, force=True, reporter=reporter,
                                  run_pre_remove_hook=False)
        if not ok:
            reporter.error(f"remove step failed for {pkg_name}: {rmsg}")
            sys.exit(1)

        ok, imsg = installer.install(
            pkg_name, archive_path=str(archive_path),
            expected_sha256=expected_sha, reporter=reporter,
        )
        if not ok:
            # With acquire-before-remove this is now genuinely rare (the
            # archive was verified present + sha-matched before remove). Keep
            # the recovery hint for the residual deploy/DB-failure window.
            reporter.error(
                f"install step failed after remove for {pkg_name}: {imsg}"
            )
            reporter.error(
                f"System is in degraded state — {pkg_name} is removed but not "
                f"reinstalled. Run `pkm install {pkg_name}` to recover."
            )
            sys.exit(1)


def cmd_remove(db, args):
    remover = PackageRemover(db)
    reporter = Reporter.from_args(args)
    # Chronicle: pre-transaction restore point, before the removal mutates the
    # live filesystem (captures the outgoing package's current bytes + pkm.db).
    # No-op without a registered handler; a handler failure is loud, not fatal.
    from . import pretxn
    pretxn.run_pre_transaction_hook(
        db, "remove", [args.package],
        reason=f"pre-transaction remove: {args.package}",
        reporter=reporter,
    )
    # S3 — removing a large package unlinks its whole payload and then walks
    # the ancestor closure of every path it touched, all of it between the
    # command and its one closing line. The per-part progress standard
    # brackets it; the heartbeat only ever appears once the part has been
    # running longer than a person would wait without wondering, so an
    # ordinary small removal still prints just its announce and its outcome.
    op = progress.LongOperation(
        f"Removing {args.package}",
        detail="unlinking the files this package owns and pruning the "
               "directories nothing else needs.",
        parts=(progress.PART_REMOVE,),
    )
    op.announce()
    op.step("unlinking recorded files and pruning empty directories")

    def _on_file(index, total, path):
        op.tick(note=f"{index:,} of {total:,}")

    try:
        ok, msg = remover.remove(
            args.package, force=args.force, reporter=reporter,
            on_file=_on_file,
        )
    except Exception:
        op.failed()
        raise
    op.end_step()
    if not ok:
        op.failed(msg.splitlines()[0] if msg else None)
        reporter.error(msg)
        sys.exit(1)
    # The reporter's own `Removed <name> <version>` line is the completion
    # signal for the package; this states that the long part is over and how
    # long it took, without repeating the subject.
    op.finish("done")
    # On success the reporter already emitted the removed-file list + done
    # line; nothing more to print.
    # A removal can change what is upgradable (a removed package drops off the
    # advisory); refresh it so the notifier count stays truthful (offline;
    # never fails the transaction).
    refresh_available_updates_after_transaction(db)


def cmd_update(db, args):
    repo = RepoManager()
    reporter = Reporter.from_args(args)
    results = repo.sync(reporter=reporter)
    # The reporter surfaces each repo's Hit/Index/Signature/count inline.
    # Any FAILED repo still needs its error shown (sync only emits the
    # signature-fail line, not the reason string) so the user sees why.
    failures = [(name, msg) for name, ok, msg in results if not ok]
    for name, msg in failures:
        reporter.error(f"{name}: {msg}")
    if failures:
        sys.exit(1)

    # Descriptive summary (apt/dnf style) so `pkm update` tells the user what
    # it did and what to do next, instead of returning silently. `pkm update`
    # only refreshes the repo index — it never changes installed packages — so
    # the actionable next step is `pkm upgrade`.
    from .version import is_upgradable, VersionParseError
    held = set(db.list_held())
    upgradable = 0
    held_upgradable = 0
    not_in_repo = []     # PKM-A10: installed but absent from every repo index
    unevaluable = []     # PKM-A09: version/release could not be compared
    for pkg in db.list_installed():
        remote = repo.get_package(pkg["name"])
        if not remote:
            # Suppress build-stage intermediates (plumbing, by-design unpublished)
            # so the warning surfaces only genuine orphans — not a wall of -pass/
            # -bootstrap internals that buries real signal (PKM-A10).
            if not _BUILD_INTERMEDIATE_RE.search(pkg["name"]):
                not_in_repo.append(pkg["name"])
            continue
        try:
            if is_upgradable(pkg, remote):
                if pkg["name"] in held:
                    held_upgradable += 1
                else:
                    upgradable += 1
        except VersionParseError:
            unevaluable.append(pkg["name"])
            continue
    print()
    if upgradable:
        noun = "package" if upgradable == 1 else "packages"
        reporter.info(
            f"Package index updated. {upgradable} {noun} can be upgraded — "
            f"run `sudo pkm upgrade --all` (or `pkm list upgradable` to preview)."
        )
    else:
        reporter.info("Package index updated. All packages are up to date.")
    if held_upgradable:
        hn = "package" if held_upgradable == 1 else "packages"
        reporter.info(
            f"({held_upgradable} held {hn} also have updates — "
            f"`pkm unhold <name>` to include them.)"
        )
    # PKM-A09: an all-clear must NEVER hide packages pkm could not evaluate.
    if unevaluable:
        n = len(unevaluable)
        reporter.warn(
            f"{n} package(s) could not be version-compared and were NOT "
            f"assessed for updates: {', '.join(sorted(unevaluable))}. This "
            f"points to a malformed version or release in the local DB or the "
            f"repo index — investigate rather than assume up to date."
        )
    # PKM-A10: surface installed packages outside every repo's update horizon
    # — they receive no updates and the user has a right to know which.
    if not_in_repo:
        n = len(not_in_repo)
        reporter.note(
            f"{n} installed package(s) are not in any configured repository "
            f"and will not receive updates: {', '.join(sorted(not_in_repo))}."
        )


def cmd_upgrade(db, args):
    from .version import is_upgradable, VersionParseError

    # Q3 (O-027): refuse bare `pkm upgrade` invocations. Bare = no
    # positional packages AND no --all. Default-deny on destructive
    # mass-mutate — silent mass-modify is the opposite of "when in
    # doubt, deny."
    upgrade_all = getattr(args, "upgrade_all", False)
    if not args.packages and not upgrade_all:
        emit_error(
            "bare `pkm upgrade` refuses to mass-modify the system. "
            "Pass --all to upgrade everything (a plan summary + "
            "confirmation prompt follows), or name specific packages to "
            "upgrade. Add --dry-run to preview without modifying anything."
        )
        sys.exit(1)

    repo = RepoManager()
    installer = PackageInstaller(db)
    allow_downgrade = getattr(args, "allow_downgrade", False)
    ignore_holds = getattr(args, "ignore_holds", False)
    held_set = set(db.list_held())

    # O-010: route (version, release) compare through pkm.version so that
    # 1.10 sorts above 1.9, release-suffix bumps are detected, and the
    # downgrade case requires explicit --allow-downgrade.
    installed = db.list_installed()
    upgradable = []
    # Packages the repository would move BACKWARDS. Without --allow-downgrade
    # these are correctly not upgraded — but they were also silently dropped,
    # so naming one explicitly produced "nothing to upgrade" with no reason
    # given. Collected here and reported below for anything the user named.
    downgrade_blocked = []

    for pkg in installed:
        remote = repo.get_package(pkg["name"])
        if not remote:
            continue
        try:
            if is_upgradable(pkg, remote, allow_downgrade=allow_downgrade):
                upgradable.append((pkg, remote))
                continue
        except VersionParseError as e:
            emit_warn(f"cannot compare versions for {pkg['name']}: {e}")
            continue
        _d = txn.downgrade_decision(
            pkg["name"], pkg, remote, allow_downgrade=allow_downgrade)
        if _d.kind == "refuse":
            downgrade_blocked.append((pkg["name"], _d))

    # Q7 (O-030): --security-only restricts candidates to repo entries
    # flagged security=true (set by generate-repodb.py from docs/
    # governance/security-advisories.yml). Applied before held-filter so
    # the held-skip notice only mentions held packages that WOULD have
    # been security-eligible — keeps the user signal sharp.
    security_only = getattr(args, "upgrade_security_only", False)
    if security_only:
        upgradable = [(i, r) for i, r in upgradable if r.get("security")]
        if not upgradable:
            emit_info(
                "No security-flagged upgrades available. The repository "
                "index has no entries with security=true matching installed "
                "packages."
            )
            return

    held_excluded_names = []
    if args.packages:
        # Filter to requested packages
        names = set(args.packages)
        # A named package the repository would move BACKWARDS gets the reason,
        # with both version-releases, instead of vanishing into "nothing to
        # upgrade". Same guard as install and reinstall, same override.
        for _name, _d in downgrade_blocked:
            if _name in names:
                emit_error(_d.message)
        # Q9: explicit-named upgrade of a held package fails loud unless
        # --ignore-holds. Avoids the "I asked for nginx and got nothing"
        # silent skip.
        if not ignore_holds:
            held_requested = names & held_set
            if held_requested:
                listed = ", ".join(sorted(held_requested))
                verb = "is" if len(held_requested) == 1 else "are"
                emit_error(
                    f"{listed} {verb} held. Run `pkm unhold <name>` "
                    f"first, or pass --ignore-holds to override (intended "
                    f"for emergency security upgrades only)."
                )
                sys.exit(1)
        upgradable = [(i, r) for i, r in upgradable if i["name"] in names]
    elif not ignore_holds:
        # Q9: --all `pkm upgrade` filters held packages with informational
        # notice. --ignore-holds bypasses for emergency security override.
        held_excluded_names = sorted(
            p["name"] for p, _ in upgradable if p["name"] in held_set
        )
        if held_excluded_names:
            upgradable = [
                (i, r) for i, r in upgradable if i["name"] not in held_set
            ]

    if not upgradable:
        if held_excluded_names:
            emit_info(
                f"Nothing to upgrade — the only candidates "
                f"({', '.join(held_excluded_names)}) are held. Run "
                f"`pkm unhold <name>` to release, or pass --ignore-holds."
            )
        else:
            emit_info("Everything is up to date.")
        return

    # Q1 (O-002): kernel-replace gate. The running kernel image stays loaded in
    # memory until reboot, so a partial-failure kernel upgrade can leave the
    # system unbootable (modules deleted, new vmlinuz not yet signed/installed).
    # `linux-kernel` therefore requires explicit --allow-kernel-replace intent.
    # Placed BEFORE the disk preflight + plan summary so the queue the user is
    # shown and asked to confirm already reflects the exclusion.
    #   - `pkm upgrade --all` (no named packages): EXCLUDE the kernel with a
    #     loud notice and upgrade everything else — a mass upgrade should not be
    #     aborted wholesale by one gated package.
    #   - `pkm upgrade linux-kernel` (named): the user asked for the kernel by
    #     name; refuse loudly (exit 1) rather than silently drop their request.
    allow_kernel_replace = getattr(args, "upgrade_allow_kernel_replace", False)
    KERNEL_REPLACE_GATED = frozenset({"linux-kernel"})
    if not allow_kernel_replace:
        gated_in_queue = [
            (p, r) for p, r in upgradable if p["name"] in KERNEL_REPLACE_GATED
        ]
        if gated_in_queue:
            if args.packages:
                listed = ", ".join(
                    sorted(p["name"] for p, _ in gated_in_queue)
                )
                emit_error(
                    f"refusing to upgrade {listed} without "
                    f"--allow-kernel-replace. Kernel upgrades can leave the "
                    f"system unbootable on partial failure; pass the flag to "
                    f"confirm intent."
                )
                sys.exit(1)
            for p, r in gated_in_queue:
                emit_warn(
                    f"excluding {p['name']} "
                    f"{_vr_str(r['version'], r.get('release', 1))} from this "
                    f"upgrade — pass --allow-kernel-replace to upgrade it "
                    f"deliberately (kernel replacement can leave the system "
                    f"unbootable on partial failure)."
                )
            gated_names = {p["name"] for p, _ in gated_in_queue}
            upgradable = [
                (p, r) for p, r in upgradable if p["name"] not in gated_names
            ]
            if not upgradable:
                emit_info(
                    "Nothing to upgrade — the only candidate was the kernel, "
                    "which is excluded without --allow-kernel-replace."
                )
                return

    # Q2: order the queue topologically so each package's in-queue runtime deps
    # upgrade before it (repo-index runtime edges; alphabetical tiebreak within
    # a rank). An authorized kernel replacement is forced to the very end so a
    # mid-queue failure never strands the system on a half-swapped kernel.
    # Runs before the plan summary so the plan prints in execution order.
    kernel_last = "linux-kernel" if (
        allow_kernel_replace
        and any(p["name"] == "linux-kernel" for p, _ in upgradable)
    ) else None
    upgradable, cycle_groups = _topological_upgrade_order(
        upgradable, kernel_last_name=kernel_last
    )
    for group in cycle_groups:
        emit_warn(
            f"dependency cycle among {', '.join(group)} — the repo index "
            f"declares a circular runtime dependency; upgrading them in "
            f"alphabetical order within the cycle (a correct index has none)."
        )

    # Q6 (O-025): free-disk preflight. Sum repo-declared compressed
    # sizes across the queue; refuse if /var/cache/pkm/ can't hold the
    # extraction estimate with safety margin. Run BEFORE plan summary +
    # confirmation prompt so the user isn't asked to confirm an upgrade
    # that's going to fail mid-extraction.
    from . import preflight
    from .repo import REPO_PKG_CACHE
    archive_sizes = [int(r.get("size") or 0) for _, r in upgradable]
    if any(s > 0 for s in archive_sizes):
        required = preflight.estimate_required_space(archive_sizes)
        check = preflight.check_free_space(required, REPO_PKG_CACHE)
        if not check["ok"]:
            emit_error(preflight.format_preflight_failure(check, REPO_PKG_CACHE))
            sys.exit(1)

    # Q3: plan summary + Q5 service-restart classification integration.
    _print_upgrade_plan_summary(upgradable, held_excluded_names, db)

    # Q3: confirmation gate.
    if not _confirm_upgrade(args):
        return

    # Chronicle: pre-transaction restore point, taken after the user confirms
    # and before the loop mutates anything (so a cancelled upgrade captures
    # nothing). Captures the current bytes of every to-be-upgraded package plus
    # pkm.db. No-op without a registered handler; a handler failure is loud but
    # never blocks the upgrade.
    from . import pretxn
    _upgrade_names = [p["name"] for p, _ in upgradable]
    pretxn.run_pre_transaction_hook(
        db, "upgrade", _upgrade_names,
        reason=f"pre-transaction upgrade: {', '.join(_upgrade_names)}",
    )

    upgraded_this_txn = []
    # Every package whose upgrade did NOT succeed, with the reason. The
    # upgrade loop used to print an error and carry on, and the command then
    # returned normally — so `pkm upgrade linux-kernel --allow-kernel-replace`
    # exited 0 having reported a CRITICAL hook failure (measured on this
    # machine 2026-08-06: the unit recorded ExecMainStatus=0 while the output
    # said the package was marked DEGRADED). Any automation gating on the
    # exit code was told the upgrade worked. Collected here and turned into
    # a non-zero exit at the end, after every other package has had its turn.
    failed_this_txn = []
    # Did this transaction change installed state at all? Not the same
    # question as "did anything upgrade": a package whose files deployed and
    # whose critical hook then failed IS a changed system — it is registered,
    # it is marked degraded, and what is available to upgrade has moved. The
    # advisory refresh keyed on the success list alone, so precisely that
    # case left the notifier lit against a stale count (the kernel step of
    # a multi-package upgrade on 2026-08-06: JSON written 11:25:12, kernel replaced
    # 11:29:44, nothing rewrote it).
    state_changed = False
    # Config sidecars written anywhere in this transaction, reported once at
    # the end. Per-package reporting during a 44-package upgrade scrolls away
    # before the run is over.
    sidecars_this_txn = []
    for installed_pkg, remote_pkg in upgradable:
        # O-005: install any new dependencies the upgrade introduces
        # BEFORE touching the upgrade target. resolve_dependencies
        # short-circuits on already-installed, so call it once per
        # direct dep + union the topo-sorted install orders. Failure
        # to resolve / download / install a new dep skips this upgrade
        # (treated as one transactional unit per the audit-row remediation).
        new_deps_to_install = []
        seen_new = set()
        dep_resolution_failed = False
        for direct_dep in (remote_pkg.get("depends", []) or []):
            if db.get_installed(direct_dep):
                continue
            dep_ok, chain = repo.resolve_dependencies(direct_dep, db)
            if not dep_ok:
                emit_warn(
                    f"cannot resolve new dep '{direct_dep}' for "
                    f"{remote_pkg['name']}: {chain}"
                )
                dep_resolution_failed = True
                break
            for d in chain:
                if d not in seen_new:
                    seen_new.add(d)
                    new_deps_to_install.append(d)
        if dep_resolution_failed:
            emit_warn(
                f"Skipping upgrade of {remote_pkg['name']}: new "
                f"dependency resolution failed (see warning above)."
            )
            continue

        dep_install_failed = False
        for new_dep in new_deps_to_install:
            dl_ok, dl_result = repo.download_package(new_dep)
            if not dl_ok:
                emit_error(f"downloading new dep {new_dep}: {dl_result}")
                dep_install_failed = True
                break
            new_dep_pkg = repo.get_package(new_dep)
            new_dep_sha = new_dep_pkg.get("sha256") if new_dep_pkg else None
            dep_ok, dep_msg = installer.install(
                new_dep, archive_path=dl_result,
                expected_sha256=new_dep_sha,
                install_reason="dependency",
            )
            if not dep_ok:
                emit_error(f"installing new dep {new_dep}: {dep_msg}")
                dep_install_failed = True
                break
            emit_info(f"Installed new dep for {remote_pkg['name']}: {new_dep}")
        if dep_install_failed:
            emit_warn(
                f"Skipping upgrade of {remote_pkg['name']} due to new "
                f"dependency install failure."
            )
            continue

        dl_ok, dl_result = repo.download_package(remote_pkg["name"])
        if not dl_ok:
            emit_error(f"downloading {remote_pkg['name']}: {dl_result}")
            continue

        # Q1 (O-007): save the old archive to the rollback cache BEFORE
        # remove. The current pkg-cache archive at REPO_PKG_CACHE/<name>-
        # <oldver>-<rel>.igos.tar.gz becomes the restore source on
        # install-failure (covered below). Missing archive (cache was
        # cleared) → rollback unavailable → WARN but proceed.
        rollback_saved = _save_rollback_archive(
            installed_pkg["name"],
            installed_pkg["version"],
            installed_pkg.get("release", 1),
        )
        rollback_archive, rollback_sha = (
            rollback_saved if rollback_saved is not None else (None, None)
        )
        if rollback_archive is None:
            emit_warn(
                f"no cached archive for {installed_pkg['name']} "
                f"{installed_pkg['version']}; rollback unavailable if the "
                f"install fails. Run `pkm cache clean --keep-current` to "
                f"keep installed-version archives in future."
            )

        # Remove old, install new
        from .remover import PackageRemover
        remover = PackageRemover(db)
        # No pre-remove hook: same reason as reinstall — an upgrade is not
        # an uninstall. The old version's files are being replaced by the
        # new version's, and the new version's post-install hook is what
        # re-establishes runtime state.
        remove_ok, remove_msg = remover.remove(
            installed_pkg["name"], force=True, run_pre_remove_hook=False)
        if not remove_ok:
            # The return value was previously discarded outright. A remove
            # that refuses leaves the OLD package in place, and the install
            # that follows then lands on top of a package that was supposed
            # to be gone — a state nobody was told about.
            emit_error(
                f"removing the installed {installed_pkg['name']} before "
                f"upgrading it did not succeed: {remove_msg}"
            )
            failed_this_txn.append((installed_pkg["name"], remove_msg))
            continue
        state_changed = True
        pkg_sidecars = []
        ok, msg = installer.install(
            remote_pkg["name"], archive_path=dl_result,
            # Thread the signed-index sha through to the install-time
            # re-hash gate, like every other download-then-install site.
            expected_sha256=remote_pkg.get("sha256"),
            # Q9: preserve install_reason across the upgrade so an
            # autoremove-eligible dependency stays dependency-marked.
            install_reason=installed_pkg.get("install_reason", "manual"),
            sidecars_out=pkg_sidecars,
        )
        sidecars_this_txn.extend(pkg_sidecars)
        # Q1 (O-007): install-failure rollback. If installer.install
        # returned not-ok, the old package is gone (removed) and the new
        # package didn't land. Reinstall from the rollback archive to
        # leave the system at its pre-upgrade state.
        if not ok and rollback_archive is not None and rollback_archive.exists():
            emit(
                f"Install of {remote_pkg['name']} {remote_pkg['version']} "
                f"failed; restoring {installed_pkg['name']} "
                f"{installed_pkg['version']} from rollback cache...",
                err=True,
            )
            rb_ok, rb_msg = installer.install(
                installed_pkg["name"], archive_path=str(rollback_archive),
                # Re-supply the sha recorded when the rollback archive was
                # saved, so the install-time re-hash gate covers the
                # save-to-restore window too.
                expected_sha256=rollback_sha,
                install_reason=installed_pkg.get("install_reason", "manual"),
            )
            if rb_ok:
                emit_info(
                    f"Rollback succeeded: {installed_pkg['name']} "
                    f"{installed_pkg['version']} restored."
                )
            else:
                emit(
                    f"CRITICAL: rollback of {installed_pkg['name']} also "
                    f"failed: {rb_msg}. System may be in a partially-upgraded "
                    f"state. Manual recovery: `pkm install "
                    f"{installed_pkg['name']} --archive={rollback_archive}`",
                    err=True,
                )
        if ok:
            # O-009: record the upgrade as its own history row with old/new
            # version linkage so `pkm history` shows the version transition
            # explicitly. The constituent remove/install rows still land
            # (logged inside PackageRemover.remove and PackageInstaller.install
            # respectively); this entry sits above them as the upgrade-aware
            # summary. Tag-or-suppress of the constituent rows is intentionally
            # out-of-scope per the audit row remediation note.
            db.log_operation(
                "upgrade",
                remote_pkg["name"],
                old_version=installed_pkg["version"],
                new_version=remote_pkg["version"],
                method="archive",
            )
            emit_info(f"Upgraded {remote_pkg['name']} to {remote_pkg['version']}")
            upgraded_this_txn.append(remote_pkg["name"])
        else:
            emit_error(f"upgrading {remote_pkg['name']}: {msg}")
            failed_this_txn.append((remote_pkg["name"], msg))

    # Q5/Q3: ONE consolidated end-of-transaction "Next steps" block across every
    # package actually upgraded — reboot / restart-services / log-out, strongest
    # first. Classified from the POST-upgrade installed state (fresh file_list +
    # reboot_required row), so the advice reflects what actually landed.
    _print_transaction_next_steps(db, upgraded_this_txn)

    # The config sidecars this transaction wrote, named once, here. They were
    # already on disk before this change and nothing in the upgrade path ever
    # said so — the message that carries the text is discarded on success, so
    # the pending-merge work was invisible on every box that upgraded.
    if sidecars_this_txn:
        block = configprotect_summary_lines(sidecars_this_txn)
        if block:
            print(block)

    # Refresh the update advisory so the notifier's top-bar count reflects what
    # this transaction just installed, not the last scheduled check (offline;
    # never fails the transaction). Keyed on whether installed state CHANGED,
    # not on whether every change succeeded: a package that deployed and then
    # failed a critical hook has moved the system, and leaving the advisory
    # describing the system as it was before is the stale-indicator class.
    if state_changed or upgraded_this_txn:
        refresh_available_updates_after_transaction(db)

    # EXIT-CODE TRUTH. A transaction that reported a failure exits non-zero,
    # so a script, a timer or another machine's automation can see it. The
    # packages that DID upgrade are already installed and are not undone by
    # this; the code describes the transaction, and a transaction with a
    # failed member did not do what it was asked.
    if failed_this_txn:
        names = ", ".join(n for n, _ in failed_this_txn)
        emit_error(
            f"{len(failed_this_txn)} of {len(upgradable)} package(s) did not "
            f"upgrade: {names}. See the errors above. Exiting non-zero so a "
            f"caller that checks the exit code is not told this succeeded."
        )
        return 1
    return 0


def _print_transaction_next_steps(db, package_names):
    """Classify each just-installed/upgraded package and print the consolidated
    end-of-transaction "Next steps" block (reboot / restart / relogin / none).

    Shared by cmd_install and cmd_upgrade so both paths render an identical,
    strongest-first advisory from the post-transaction installed state.
    """
    from .services import classify_restart_requirement, format_next_steps
    classifications = []
    for name in package_names:
        files = db.get_files(name)
        file_list = [
            f["path"] + ("/" if f["is_dir"] else "") for f in files
        ]
        row = db.get_installed(name)
        declared = bool(row.get("reboot_required")) if row else False
        classifications.append((
            name,
            classify_restart_requirement(
                name, file_list, declared_reboot_required=declared
            ),
        ))
    block = format_next_steps(classifications)
    if block:
        print(block)


def _app_status(name, installed_names):
    """Honest install-status token for a search/list entry.

    A proprietary-download helper (is_download_helper) ships in the default
    install set, so its DB row is always present — but that means only the
    HELPER is there, not the app. Report `[installed]` for such a package
    ONLY when the real app footprint exists (payload_installed); otherwise
    report it as available-to-install. A normal package is `[installed]`
    whenever its DB row is present. (PRIME DIRECTIVE: never tell the user the
    app is installed when it is not.)
    """
    if is_download_helper(name):
        if payload_installed(name):
            return "[installed]"
        return f"(available — run: pkm install {name})"
    return "[installed]" if name in installed_names else ""


def _print_pkg_entry(name, version, tier, description, status):
    """Two-line, terminal-width-aware package entry (pacman -Ss style).

    Line 1: name / version / [tier] / status. Line 2: the FULL description
    word-wrapped to the terminal width and indented, so a long description
    never runs off the right edge (the prior `desc[:50]` truncation +
    single-line layout overflowed narrow terminals).
    """
    import shutil
    import textwrap

    head = f"    {name}  {version}"
    if tier:
        head += f"  [{tier}]"
    if status:
        head += f"  {status}"
    print(head)
    if description:
        width = shutil.get_terminal_size((80, 24)).columns
        indent = "        "
        for line in textwrap.wrap(
            description.strip(), width=max(24, width - len(indent))
        ):
            print(indent + line)


def cmd_list(db, args):
    if args.what == "installed":
        packages = db.list_installed(tier=args.tier)
        if not packages:
            emit_info("No packages installed" + (f" in tier '{args.tier}'" if args.tier else ""))
            return
        installed_names = {p["name"] for p in packages}
        print(f"  Installed packages ({len(packages)}):")
        for pkg in packages:
            status = _app_status(pkg["name"], installed_names)
            # A helper-only proprietary entry is not really the app being
            # installed; mark it so `list installed` doesn't imply it is.
            tag = "" if status == "[installed]" else status
            # PKM-A25: a degraded package (a critical post-install hook failed)
            # is registered but the live system may diverge from its metadata —
            # flag it loudly so it never reads as a clean install.
            if pkg.get("degraded"):
                tag = (tag + " " if tag else "") + f"[DEGRADED: {pkg['degraded']}]"
            _print_pkg_entry(
                pkg["name"], pkg["version"], pkg.get("tier", ""),
                pkg.get("description", ""), tag,
            )
    elif args.what == "available":
        repo = RepoManager()
        packages = repo.list_available(tier=args.tier)
        if not packages:
            emit_info("No packages available. Run `pkm sync` first.")
            return
        print(f"  Available packages ({len(packages)}):")
        for pkg in packages:
            _print_pkg_entry(
                pkg["name"], pkg["version"], pkg.get("tier", ""),
                pkg.get("description", ""), "",
            )
    elif args.what in ("upgradable", "upgradeable"):  # A29: accept both spellings
        from .version import is_upgradable, VersionParseError
        repo = RepoManager()
        installed = db.list_installed()
        count = 0
        for pkg in installed:
            remote = repo.get_package(pkg["name"])
            if not remote:
                continue
            try:
                # O-010: same version-aware compare as cmd_upgrade. Listing
                # has no --allow-downgrade surface, so default (upgrades only).
                if is_upgradable(pkg, remote):
                    print(f"    {pkg['name']:30s} "
                          f"{_vr_str(pkg['version'], pkg.get('release', 1)):15s} → "
                          f"{_vr_str(remote['version'], remote.get('release', 1))}")
                    count += 1
            except VersionParseError as e:
                emit_warn(f"cannot compare versions for {pkg['name']}: {e}")
                continue
        if count == 0:
            emit_info("Everything is up to date.")


def cmd_search(db, args):
    # Search local database
    local = db.search(args.term)

    # Search repositories
    repo = RepoManager()
    remote = repo.search(args.term)

    # Merge — mark installed packages
    installed_names = {p["name"] for p in local}
    all_results = list(local)
    for r in remote:
        if r["name"] not in installed_names:
            all_results.append(r)

    if not all_results:
        if not repo.has_synced_index():
            # Fresh system, no index pulled yet — say WHY rather than imply
            # the package doesn't exist (the non-root read path no longer
            # crashes on the missing cache; advise the update instead).
            emit_info(
                "No repository index yet. Run 'sudo pkm sync' to download "
                "it, then search again."
            )
        else:
            emit_info(f"No packages matching '{args.term}'")
        return
    print(f"  Search results for '{args.term}' ({len(all_results)} matches):")
    for pkg in all_results:
        status = _app_status(pkg["name"], installed_names)
        _print_pkg_entry(
            pkg["name"], pkg["version"], pkg.get("tier", ""),
            pkg.get("description", ""), status,
        )


def cmd_info(db, args):
    pkg = db.get_installed(args.package)
    if not pkg:
        emit_info(f"Package '{args.package}' is not installed")
        return

    # PKM-A30: show the full version-release identity (a same-version mirror
    # republish only advances release — version alone hides it, per A06), and
    # size the rule to the title instead of a fixed 50 columns.
    title = f"{pkg['name']} {_vr_str(pkg['version'], pkg.get('release', 1))}"
    rule = "=" * len(title)
    print(f"  {rule}")
    print(f"  {title}")
    print(f"  {rule}")
    # For a download-helper package the identity above is the STUB's — the
    # small package that ships the helper and the vendor keyring. The build of
    # the vendor application the machine actually runs is recorded separately,
    # and is the thing an operator asking "what version of this app am I on?"
    # means. Printed first so it is not read as a detail of the stub.
    payload = pkg.get("payload_version")
    if payload:
        print(f"  {'payload_version':20s}: {payload}")
    for key in ["tier", "description", "license", "build_date", "install_date",
                 "install_method", "uncompressed_size"]:
        val = pkg.get(key)
        if val:
            if key == "uncompressed_size" and isinstance(val, int) and val > 0:
                val = f"{val / 1024 / 1024:.1f} MB" if val > 1024*1024 else f"{val / 1024:.0f} KB"
            print(f"  {key:20s}: {val}")

    deps = db.get_depends(args.package)
    if deps:
        print(f"\n  Dependencies ({len(deps)}):")
        for d in deps:
            print(f"    [{d['type']:8s}] {d['name']}")

    rdeps = db.get_reverse_depends(args.package)
    if rdeps:
        print(f"\n  Required by ({len(rdeps)}):")
        for d in rdeps:
            print(f"    {d['name']} {_vr_str(d['version'], d.get('release', 1))}")

    files = db.get_files(args.package)
    file_count = len([f for f in files if not f["is_dir"]])
    print(f"\n  Files: {file_count}")
    print()


def cmd_files(db, args):
    files = db.get_files(args.package)
    if not files:
        emit_info(f"Package '{args.package}' not found or has no tracked files")
        return
    print(f"  Files in {args.package} ({len(files)}):")
    for f in files:
        prefix = "d " if f["is_dir"] else "  "
        print(f"  {prefix}/{f['path']}")


def cmd_provides(db, args):
    result = db.find_owner(args.file)
    if result:
        emit_info(f"/{result['path']} is owned by {result['name']} {result['version']}")
    else:
        emit_info(f"No package owns '{args.file}'")


def _merge_expected_absent_classes(dest, by_class):
    """Fold a single package's expected_absent_by_class ({class_id: [paths]})
    into a running aggregate ({class_id: count}). Component B — so `verify --all`
    can name the classes across the whole set, not just a bare total."""
    for cls, paths in (by_class or {}).items():
        dest[cls] = dest.get(cls, 0) + len(paths)


def _expected_absent_note(class_counts):
    """Render an expected-absent summary that NAMES each class (Component B):
    `; 28 expected-absent: 26 ch8-la-sweep, 2 volatile-run`. Empty string when
    there are none. Ordered by count desc then class_id so output is stable."""
    total = sum(class_counts.values())
    if not total:
        return ""
    parts = ", ".join(
        f"{n} {cls}" for cls, n in
        sorted(class_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    return f"; {total} expected-absent: {parts}"


def _generated_note(count):
    """Render the hook-generated summary: `; 3 hook-generated (existence-
    checked)`. Empty string when there are none. D-9: a generated file's
    content is regenerated by the next run of the same cache/index refresh,
    so verify checks that it EXISTS and says so — naming the exemption on
    the output is what keeps it from reading as a silent pass."""
    if not count:
        return ""
    return f"; {count} hook-generated (existence-checked)"


def _print_generated_detail(paths):
    """Under --detail, list the hook-generated paths verify did not
    content-check."""
    if not paths:
        return
    print(f"  hook-generated — existence-checked, not content-checked "
          f"({len(paths)}):")
    for p in sorted(paths):
        print(f"    /{p}")


def _print_expected_absent_detail(by_class):
    """Under --detail, list the expected-absent paths grouped by named class."""
    for cls, paths in sorted(by_class.items()):
        print(f"  expected-absent [{cls}] ({len(paths)}):")
        for p in sorted(paths):
            print(f"    /{p}")


def cmd_verify(db, args):
    # Exit codes (CLI-local; differ from verifier.py API EXIT_* dict codes):
    #   0 = OK (incl. single-pkg routed-to-superseded-successor message)
    #   1 = verification FAILED — files are missing or modified, or a
    #       critical install hook left the package degraded
    #   2 = usage error (no package + no --all)
    #   3 = verification COULD NOT BE COMPLETED — nothing failed, but at
    #       least one check could not run: files this process may not read
    #       (undeterminable) or files with no recorded hash to compare
    #       against (unverifiable). Distinct from 1 because a run that was
    #       prevented from checking has not found a fault, and reporting it
    #       as one hands the user a fright about a healthy system. The usual
    #       cause is running verify as a non-root user over root-only files.
    # API-level EXIT_SUPERSEDED=2 is a dict-level informational code and is
    # NEVER propagated as sys.exit; CLI translates it to message + exit 0.
    verifier = PackageVerifier(db)
    mode = getattr(args, "verify_mode", "strict")

    if args.verify_all or not args.package:
        if not args.verify_all and not args.package:
            emit_error("usage: pkm verify <package> — or — pkm verify --all")
            sys.exit(2)
        # S1 — the whole-system verify was the longest silent pause pkm had:
        # a strict run reads and hashes every owned file on the machine and
        # printed nothing at all until it was over (over forty seconds
        # measured). The per-part progress standard brackets it: what is
        # about to happen, which package is being read now, that it is still
        # working, and what the answer was. The opening and closing lines
        # survive -q; the per-package detail does not.
        _installed_total = len(db.list_installed())
        _op = progress.LongOperation(
            f"Verifying installed packages ({mode})",
            detail=(f"{_installed_total:,} packages to check — a strict "
                    f"verify reads and hashes every file each one owns."),
            parts=(progress.PART_SCAN,),
        )
        _op.announce()
        _op.step("reading recorded files and comparing them against disk")

        def _on_package(index, total, name):
            _op.tick(note=f"{index:,} of {total:,} — {name}")

        try:
            results = verifier.verify_all(mode=mode, on_package=_on_package)
        except Exception:
            _op.failed()
            raise
        _op.end_step()
        ok_count = 0
        problem_count = 0
        undetermined_count = 0        # packages whose checks could not run
        expected_absent_classes = {}  # Component B: class_id -> count across all
        generated_total = 0           # D-9: hook-generated across the whole set
        file_problem_names = set()
        for name, version, result in results:
            _merge_expected_absent_classes(
                expected_absent_classes, result.get("expected_absent_by_class"))
            generated_total += len(result.get("generated", []))
            und = result.get("undeterminable", [])
            unv = result.get("unverifiable", [])
            if result["missing"] or result["modified"]:
                problem_count += 1
                file_problem_names.add(name)
                unv_note = f", {len(unv)} unverifiable" if unv else ""
                und_note = (f", {len(und)} could not be checked" if und else "")
                print(f"  ✗ {name} {version} — {len(result['missing'])} missing, "
                      f"{len(result['modified'])} modified{unv_note}{und_note}")
            elif und or unv:
                # Nothing is wrong that we can see; we were prevented from
                # looking. A separate marker and a separate count, so this
                # never reads as a failure.
                undetermined_count += 1
                parts = []
                if und:
                    parts.append(f"{len(und)} could not be checked")
                if unv:
                    parts.append(f"{len(unv)} unverifiable")
                print(f"  ? {name} {version} — {', '.join(parts)} "
                      f"(no fault found; the check could not run)")
            else:
                ok_count += 1
            if getattr(args, "verify_detail", False):
                _print_expected_absent_detail(
                    result.get("expected_absent_by_class", {}))
                _print_generated_detail(result.get("generated", []))
        # PKM-A25: a degraded package may have intact FILES but a failed
        # critical post-install hook (e.g. an unsigned UKI) — file-integrity
        # alone passes it silently. Surface degraded packages as problems too
        # (don't double-count one already reported for file issues).
        for p in db.list_installed():
            if p.get("degraded") and p["name"] not in file_problem_names:
                problem_count += 1
                print(f"  ✗ {p['name']} {p['version']} — DEGRADED: critical "
                      f"hook(s) failed at install: {p['degraded']}; reinstall "
                      f"to retry.")
        print()
        ea_note = _expected_absent_note(expected_absent_classes)
        gen_note = _generated_note(generated_total)
        und_note = (f", {undetermined_count} could not be checked"
                    if undetermined_count else "")
        # The closing line of the long operation IS the outcome line. There
        # is deliberately not a second summary beside it: two lines saying
        # the same thing is how a run's real result gets lost inside its own
        # narration (the same rule the completion line for an install
        # follows). It prints at every level, -q included.
        _op.finish(f"{ok_count} ok, {problem_count} with "
                   f"issues{und_note}{ea_note}{gen_note}")
        if undetermined_count and not problem_count:
            emit_info("Some checks could not run — this is not a fault report. "
                      "Re-run as root to check the files this user cannot read.")
        if problem_count > 0:
            sys.exit(1)
        if undetermined_count > 0:
            sys.exit(3)
        return

    result = verifier.verify(args.package, mode=mode)
    if result is None:
        emit_info(f"Package '{args.package}' is not installed")
        return
    if result.get("superseded_by"):
        emit_info(result["message"])
        return
    # PKM-A25: surface a degraded marker (critical hook failed at install) even
    # when the files themselves verify clean.
    _row = db.get_installed(args.package)
    _degraded = _row.get("degraded") if _row else None
    if (not result["missing"] and not result["modified"]
            and not result.get("unverifiable")
            and not result.get("undeterminable")):
        if _degraded:
            emit_done(f"✗ {args.package}: DEGRADED — files ok, but critical "
                      f"hook(s) failed at install: {_degraded}; reinstall to retry.")
            sys.exit(1)
        suffix = "files verified" if mode == "strict" else "files present (existence-only)"
        by_class = result.get("expected_absent_by_class", {})
        ea_note = _expected_absent_note(
            {cls: len(paths) for cls, paths in by_class.items()})
        gen_note = _generated_note(len(result.get("generated", [])))
        if getattr(args, "verify_detail", False):
            _print_expected_absent_detail(by_class)
            _print_generated_detail(result.get("generated", []))
        emit_done(f"✓ {args.package}: ok ({result['total']} {suffix}"
                  f"{ea_note}{gen_note})")
        return
    if _degraded:
        emit_done(f"✗ {args.package}: DEGRADED — critical hook(s) failed at "
                  f"install: {_degraded}")
    if result["missing"]:
        print(f"  missing ({len(result['missing'])}):")
        for f in result["missing"][:20]:
            print(f"    /{f}")
        if len(result["missing"]) > 20:
            print(f"    … and {len(result['missing']) - 20} more")
    if result["modified"]:
        print(f"  modified ({len(result['modified'])}):")
        for f in result["modified"][:20]:
            print(f"    /{f}")
    if result.get("unverifiable"):
        print(f"  unverifiable — no recorded content hash "
              f"({len(result['unverifiable'])}):")
        for f in result["unverifiable"][:20]:
            print(f"    /{f}")
        print(f"    (existence confirmed; content cannot be checked. "
              f"Reinstall the package to record hashes.)")
    if result.get("undeterminable"):
        print(f"  could not be checked — not readable by this user "
              f"({len(result['undeterminable'])}):")
        for f in result["undeterminable"][:20]:
            print(f"    /{f}")
        if len(result["undeterminable"]) > 20:
            print(f"    … and {len(result['undeterminable']) - 20} more")
        print(f"    (these files are NOT reported missing or modified — this "
              f"user cannot read them, so their state is unknown. Re-run as "
              f"root to check them.)")
    if getattr(args, "verify_detail", False):
        _print_generated_detail(result.get("generated", []))
    elif result.get("generated"):
        print(f"  hook-generated — existence-checked, not content-checked "
              f"({len(result['generated'])}); --detail to list")
    # A real fault outranks an unknown. Only when nothing failed and nothing
    # is degraded does the run report "could not be completed" instead.
    if (not result["missing"] and not result["modified"] and not _degraded):
        sys.exit(3)
    sys.exit(1)


def cmd_depends(db, args):
    if args.reverse:
        rdeps = db.get_reverse_depends(args.package)
        if not rdeps:
            emit_info(f"No packages depend on '{args.package}'")
            return
        print(f"  Packages that depend on {args.package} ({len(rdeps)}):")
        for d in rdeps:
            print(f"    {d['name']} {d['version']} ({d['type']})")
    else:
        deps = db.get_depends(args.package)
        if not deps:
            pkg = db.get_installed(args.package)
            if not pkg:
                emit_info(f"Package '{args.package}' is not installed")
            else:
                emit_info(f"{args.package} has no tracked dependencies")
            return
        print(f"  Dependencies of {args.package} ({len(deps)}):")
        for d in deps:
            installed = db.get_installed(d["name"])
            status = f" [installed: {installed['version']}]" if installed else " [not installed]"
            print(f"    [{d['type']:8s}] {d['name']}{status}")


def cmd_history(db, args):
    entries = db.get_history(package_name=args.package)
    if not entries:
        emit_info("No history recorded")
        return
    print(f"  Package history ({len(entries)} entries):")
    for e in entries:
        status = "✓" if e["success"] else "✗"
        ver = ""
        if e["old_version"] and e["new_version"]:
            ver = f" {e['old_version']} → {e['new_version']}"
        elif e["new_version"]:
            ver = f" {e['new_version']}"
        elif e["old_version"]:
            ver = f" {e['old_version']}"
        method = f" ({e['method']})" if e["method"] else ""
        print(f"    {e['timestamp'][:19]}  {e['operation']:10s} {e['package_name']}{ver}{method} [{status}]")


def cmd_import(db, args):
    # S2 — a corpus-wide import re-reads every installed package's text
    # manifest and rewrites the file rows of each one whose bytes changed.
    # On a full system that is a thousand manifests and hundreds of
    # thousands of rows, and it printed one line before and one line after,
    # with everything in between silent. The per-part progress standard
    # brackets it and reports which manifest it is on.
    op = progress.LongOperation(
        "Importing installed package manifests",
        detail=("reading each package's text manifest and bringing its "
                "database record into line with it."),
        parts=(progress.PART_SCAN, progress.PART_DATABASE),
    )
    op.announce()
    op.step("reading manifests and updating package records")

    def _on_manifest(index, total, name):
        op.tick(note=f"{index:,} of {total:,} — {name}")

    try:
        count = db.import_manifests(on_manifest=_on_manifest)
    except Exception:
        op.failed()
        raise
    op.end_step()
    # A refused claim is a finding, and the import runs unattended inside the
    # build pipeline — printing it beside the count is the only place an
    # operator can see that a manifest asked for something it was not
    # entitled to. Reported, never absorbed.
    for refusal in db.import_refusals:
        emit_warn(refusal)
    op.finish(f"{count} package(s) imported into the pkm database")


def cmd_vacuum(db, args):
    """pkm vacuum — return unused space inside the database to the filesystem.

    Exit codes:
      0 — compacted, or nothing worth compacting, or a dry run that answered
      1 — refused: not enough free disk to rebuild the database safely, or
          the database file could not be measured to decide

    Decided 2026-08-06. Three properties are the point of the command:
    it is EXPLICIT (nothing in pkm compacts on its own), its space check is
    FAIL-CLOSED (a rebuild that runs out of disk halfway leaves a full
    filesystem and no compaction, which is worse than not starting), and it
    reports its parts while it runs like every other long pkm operation.
    """
    from .output import human_size

    reclaimable = db.reclaimable_bytes()
    check = db.vacuum_space_check()
    if check is None:
        emit_error(
            f"cannot measure the package database at {db.db_path}, so the "
            f"free space a rebuild needs cannot be established. Refusing "
            f"rather than starting a whole-file rewrite blind."
        )
        return 1

    db_h = human_size(check["db_bytes"], precision=1)
    recl_h = human_size(reclaimable, precision=1)
    emit_info(
        f"Package database: {db_h}, of which about {recl_h} is unused space "
        f"inside the file."
    )

    if getattr(args, "vacuum_dry_run", False):
        # A preview states BOTH answers — how much there is to reclaim and
        # whether there is room to do it — because either one alone would
        # leave the user to guess the other.
        #
        # It also applies the SAME not-worth-it threshold the real command
        # applies, and in the same order. A preview that promises a rebuild
        # the command would then decline is worse than no preview: it
        # describes a different program. (Caught by running both against a
        # real 4.3 MiB database on 2026-08-06, where the preview offered a
        # rebuild and the command correctly refused to spend it.)
        if reclaimable < db.VACUUM_ADVICE_MIN_BYTES:
            emit_done(
                f"Dry run: {recl_h} of unused space is below the point where "
                f"a rebuild is worth its cost, so `pkm vacuum` would leave "
                f"the database exactly as it is."
            )
            return 0
        if check["ok"]:
            emit_done(
                f"Dry run: `pkm vacuum` would rebuild the database and return "
                f"about {recl_h} to the filesystem. There is room to do it "
                f"({human_size(check['available_bytes'], precision=1)} free; "
                f"the rebuild needs about "
                f"{human_size(check['required_with_margin'], precision=1)})."
            )
            return 0
        emit_done(
            f"Dry run: about {recl_h} could be returned, but there is not "
            f"enough free space to rebuild the database "
            f"({human_size(check['available_bytes'], precision=1)} free; the "
            f"rebuild needs about "
            f"{human_size(check['required_with_margin'], precision=1)}). "
            f"`pkm vacuum` would refuse."
        )
        return 0

    if not check["ok"]:
        # FAIL-CLOSED. The rebuilt copy stands beside the original until the
        # swap, so the peak requirement is about twice the file. Refusing is
        # the whole reason the preflight exists.
        emit_error(
            preflight_module.format_preflight_failure(check, db.db_path.parent)
            + f" A database rebuild needs room for the new copy beside the "
              f"current {db_h} file until the swap completes."
        )
        return 1

    if reclaimable < db.VACUUM_ADVICE_MIN_BYTES:
        # Truthful no-op. Rebuilding a database with nothing to reclaim
        # spends the whole cost for no gain, so say so and stop.
        emit_done(
            f"Nothing worth reclaiming — {recl_h} of unused space is below "
            f"the point where a rebuild is worth its cost. The database was "
            f"left exactly as it was."
        )
        return 0

    op = progress.LongOperation(
        "Compacting the package database",
        detail=(f"rebuilding {db_h} into a fresh file and swapping it in. No "
                f"installed file is touched and no package record changes."),
        parts=(progress.PART_DATABASE,),
    )
    op.announce()
    op.step("rewriting the database")
    try:
        before, after = db.vacuum()
    except Exception as e:
        op.failed(str(e))
        emit_error(
            f"the database rebuild did not complete: {e}. SQLite performs "
            f"this as a transaction, so the original database is intact and "
            f"unchanged."
        )
        return 1
    op.end_step()
    freed = max(before - after, 0)
    op.finish(
        f"{human_size(before, precision=1)} → "
        f"{human_size(after, precision=1)}; "
        f"{human_size(freed, precision=1)} returned to the filesystem"
    )
    return 0


def cmd_hook_baseline(db, args):
    """pkm hook-baseline <package> --out FILE — state before a post_install.

    Exit code:
      0 — a baseline was written (including an empty one, which is a truthful
          answer for a package that owns no readable regular files)
      1 — the package is not registered, or the baseline cannot be written.
          Fail loud: a build driver that silently got no baseline would later
          compare against nothing and record no hook changes at all, which is
          the exact silent-loss shape this seam exists to remove.
    """
    from .installer import PackageInstaller
    if db.get_installed(args.package) is None:
        emit_error(f"{args.package} is not registered — no baseline to take")
        return 1
    installer = PackageInstaller(db, root=str(db.root))
    baseline = installer.hook_baseline(args.package)
    try:
        with open(args.out, "w") as fh:
            for path in sorted(baseline):
                fh.write(f"{baseline[path]}  {path}\n")
    except OSError as e:
        emit_error(f"cannot write baseline {args.out}: {e}")
        return 1
    emit_done(
        f"Baseline recorded for {args.package}: {len(baseline)} file(s)")
    return 0


def cmd_record_hook_changes(db, args):
    """pkm record-hook-changes <package> --baseline FILE — after post_install.

    Exit code:
      0 — the comparison ran, whether or not anything changed
      1 — the package is not registered, or the baseline cannot be read
    """
    from .installer import PackageInstaller
    if db.get_installed(args.package) is None:
        emit_error(f"{args.package} is not registered — nothing to record")
        return 1
    baseline = {}
    try:
        with open(args.baseline) as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line:
                    continue
                # "<sha256>  <path>", split on the FIRST double space so a
                # path containing spaces survives the round trip.
                digest, _, path = line.partition("  ")
                if path:
                    baseline[path] = digest
    except OSError as e:
        emit_error(f"cannot read baseline {args.baseline}: {e}")
        return 1

    installer = PackageInstaller(db, root=str(db.root))
    changed, messages = installer.record_hook_changes(args.package, baseline)
    for line in messages:
        emit(line)
    if changed:
        emit_done(
            f"{args.package}: {len(changed)} own payload file(s) recorded as "
            f"hook-managed")
    else:
        emit_done(f"{args.package}: post_install rewrote none of its own "
                  f"payload files")
    return 0


def cmd_refresh_baseline(db, args):
    """pkm refresh-baseline <path>... — record current live content as baseline.

    User-facing accept-new step after manually merging a .pkmnew sidecar.
    Recomputes the live file's sha256 and stores it as the original_checksum
    for each tracked config path, so subsequent upgrades treat the new content
    as the baseline for the user-edited detection check.

    Exit code:
      0 — all paths refreshed successfully
      1 — at least one path failed (not tracked, file not found, etc.);
          successful paths are still committed.
    """
    any_failed = False
    for path in args.paths:
        success, msg = db.refresh_baseline(path)
        emit_info(msg)
        if not success:
            any_failed = True
    return 1 if any_failed else 0


def cmd_restart_services(db, args):
    """pkm restart-services [--list | --all | <service>...] — Q5 user-driven
    service restart after upgrade.

    pkm never auto-restarts daemons during upgrade (PRIME DIRECTIVE — user
    controls when their machine takes the downtime). This subcommand is the
    user-driven companion that surfaces what needs attention and performs
    the restarts on explicit request.

    Three modes:

      --list      Classify every installed package against the Q5 restart
                  rules (reboot-trigger / restart-needed / none) and print
                  the non-trivial classifications. Read-only.
      --all       Walk installed packages; restart every active systemd
                  unit owned by a pkm package. Reboot-required packages
                  surface as REBOOT REQUIRED notices but are not auto-
                  rebooted.
      <service>...  Restart specific systemd unit names directly. No
                  classification scan — operator-driven targeted action.

    Exit code: 0 on full success, 1 if any restart failed.
    """
    from .services import (
        classify_restart_requirement,
        format_service_summary,
        run_restart_services,
    )

    if args.restart_list:
        installed = db.list_installed()
        any_action = False
        for pkg in installed:
            files = db.get_files(pkg["name"])
            file_list = [f["path"] + ("/" if f["is_dir"] else "") for f in files]
            classification = classify_restart_requirement(
                pkg["name"], file_list,
                declared_reboot_required=bool(pkg.get("reboot_required")),
            )
            if classification["requirement"] == "none":
                continue
            any_action = True
            print(f"  {pkg['name']}:")
            summary = format_service_summary(classification)
            if summary:
                print(summary)
        if not any_action:
            emit_info("No services need restart and no reboot is required.")
        return 0

    if args.restart_all:
        installed = db.list_installed()
        all_services = []
        reboot_reasons = []
        for pkg in installed:
            files = db.get_files(pkg["name"])
            file_list = [f["path"] + ("/" if f["is_dir"] else "") for f in files]
            classification = classify_restart_requirement(
                pkg["name"], file_list,
                declared_reboot_required=bool(pkg.get("reboot_required")),
            )
            if classification["requirement"] == "restart":
                all_services.extend(classification["services"])
            elif classification["requirement"] == "reboot":
                reboot_reasons.append(format_service_summary(classification))

        # Dedupe while preserving discovery order so summary output is
        # stable across runs against the same install state.
        seen = set()
        unique_services = []
        for s in all_services:
            if s not in seen:
                seen.add(s)
                unique_services.append(s)

        if reboot_reasons:
            for r in reboot_reasons:
                print(r)
        if not unique_services:
            if not reboot_reasons:
                emit_info("No active services to restart.")
            return 0
        emit_info(f"Restarting {len(unique_services)} service(s): "
                  f"{', '.join(unique_services)}")
        results = run_restart_services(unique_services)
        return _render_restart_results(results)

    if args.services:
        emit_info(f"Restarting {len(args.services)} service(s): "
                  f"{', '.join(args.services)}")
        results = run_restart_services(args.services)
        return _render_restart_results(results)

    # No flag, no positional — print usage hint.
    print("  Usage: pkm restart-services [--list | --all | <service>...]")
    print("    --list       Classify all installed packages")
    print("    --all        Restart every active service owned by a pkm package")
    print("    <service>    Restart specific systemd unit name(s)")
    return 0


def _save_rollback_archive(name, version, release):
    """Q1 (O-007): copy the installed-version's archive from the pkg
    cache to the rollback cache so it survives upgrade-time cache
    cleanup + is available for automatic restore on install failure.

    Args:
        name: package name.
        version: version string of the currently-installed package.
        release: integer release counter (defaults to 1 if unset).

    Returns:
        (Path, sha256) tuple for the rollback archive on success — the
        sha is computed from the saved copy at save time so a later
        rollback install can re-verify the archive was not swapped in
        the save-to-restore window — or None when the old archive is
        not in REPO_PKG_CACHE (cache was cleared, --archive install
        never cached, etc.). Caller treats None as "rollback
        unavailable; proceed with WARN."
    """
    import shutil
    from .repo import REPO_PKG_CACHE, REPO_ROLLBACK_DIR

    archive_name = f"{name}-{version}-{int(release or 1)}.igos.tar.gz"
    src = REPO_PKG_CACHE / archive_name
    if not src.exists():
        return None
    try:
        REPO_ROLLBACK_DIR.mkdir(parents=True, exist_ok=True)
        dest = REPO_ROLLBACK_DIR / archive_name
        shutil.copy2(str(src), str(dest))
        return dest, _sha256(str(dest))
    except (OSError, IOError):
        return None


def _render_restart_results(results):
    """Print per-unit success/failure summary for a restart batch.

    Args:
        results: dict {unit_name: success_bool} from run_restart_services.

    Returns:
        0 if every unit succeeded; 1 if any failed (so the caller can
        propagate as the process exit code).
    """
    successes = [u for u, ok in results.items() if ok]
    failures = [u for u, ok in results.items() if not ok]
    if successes:
        emit_info(f"Restarted: {', '.join(successes)}")
    if failures:
        emit_error(f"failed to restart: {', '.join(failures)}")
        return 1
    return 0


def _vr_str(version, release=1):
    """Render a package's full identity as 'version-release'.

    Release matters: a same-version mirror republish only advances `release`,
    so showing the version alone makes a real upgrade render as a no-op
    ('0.1.0 -> 0.1.0') at the confirmation gate (PKM-A06). An absent release
    is the schema default 1; a malformed one is shown verbatim rather than
    masked, so the anomaly is visible.
    """
    try:
        rel = int(release) if release is not None else 1
    except (TypeError, ValueError):
        rel = release
    return f"{version}-{rel}"


def _topological_upgrade_order(upgradable, kernel_last_name=None):
    """Order the upgrade queue so each package's in-queue deps upgrade first.

    Kahn topological sort over the in-queue package names, using repo-index
    runtime-dependency edges (remote_pkg["depends"]) restricted to names that
    are themselves in the queue — a dep already installed (or not being
    upgraded) imposes no intra-queue ordering constraint. Within a topological
    rank, ready nodes are emitted ALPHABETICALLY so the order is fully
    deterministic (stable plan output, reproducible execution).

    A correct repo graph is acyclic; a corrupt or hand-edited index could
    declare a runtime-dependency cycle, which cannot be topologically ordered.
    Cycle nodes are grouped by connected component, each group sorted
    alphabetically, and the groups appended (alphabetically by first member)
    after the acyclic prefix — the upgrade still proceeds and the caller
    loud-notes each cycle group.

    Args:
        upgradable: list of (installed_pkg, remote_pkg) tuples.
        kernel_last_name: when set (only for an AUTHORIZED kernel replacement
            present in the queue), that package is forced to the very end
            regardless of topological rank, so a mid-queue failure never
            strands the system on a half-swapped kernel.

    Returns:
        (ordered, cycle_groups):
          ordered — the input tuples reordered for execution.
          cycle_groups — list[list[str]] of name groups in a dependency cycle
            (empty when the graph is acyclic).
    """
    by_name = {p["name"]: (p, r) for p, r in upgradable}
    names = set(by_name)

    # deps[x] = in-queue packages x depends on (must upgrade before x).
    # The sort itself is the shared pkm.deporder implementation — the ONE
    # sorter (Forge's install ordering consumes the same one; a second
    # implementation here is exactly the drift class that left Forge
    # alphabetical while upgrades were ordered).
    deps = {
        name: [d for d in (remote.get("depends", []) or [])]
        for name, (_, remote) in by_name.items()
    }
    from .deporder import topological_order
    ordered_names, cycle_groups = topological_order(names, deps)

    if kernel_last_name and kernel_last_name in names:
        ordered_names = [n for n in ordered_names if n != kernel_last_name]
        ordered_names.append(kernel_last_name)

    ordered = [by_name[n] for n in ordered_names]
    return ordered, cycle_groups


def _print_upgrade_plan_summary(upgradable, held_excluded_names, db):
    """Q3: structured plan summary printed before the confirmation gate.

    Args:
        upgradable: list of (installed_pkg, remote_pkg) tuples.
        held_excluded_names: list of held package names that were filtered.
        db: PackageDB for per-package file_list lookups (Q5 classification).
    """
    from .services import classify_restart_requirement, format_next_steps

    n = len(upgradable)
    print(f"  Upgrade plan: {n} package(s)")
    for installed_pkg, remote_pkg in upgradable:
        print(
            f"    {installed_pkg['name']:30s} "
            f"{_vr_str(installed_pkg['version'], installed_pkg.get('release', 1)):15s}"
            f" -> {_vr_str(remote_pkg['version'], remote_pkg.get('release', 1))}"
        )

    # Download size summary — sum repo-declared sizes for packages where
    # the repo index provides one. Missing size is treated as 0 (no warn).
    total_size = sum(int(r.get("size") or 0) for _, r in upgradable)
    if total_size > 0:
        mb = total_size / (1024 * 1024)
        print(f"  Download size: ~{mb:.1f} MiB")

    if held_excluded_names:
        emit_info(
            f"Excluded (held): {', '.join(held_excluded_names)} "
            f"(use --ignore-holds to override)"
        )

    # O-005: surface new dependencies the upgrades will pull in so the
    # user knows what they're consenting to before the [y/N] prompt.
    # Walk each remote_pkg.depends list; entries not in the installed
    # set are new deps. Transitive deps may add more at install time;
    # those surface in per-package install output.
    installed_name_set = {p["name"] for p in db.list_installed()}
    new_dep_set = set()
    for _, remote_pkg in upgradable:
        for d in (remote_pkg.get("depends", []) or []):
            if d not in installed_name_set:
                new_dep_set.add(d)
    if new_dep_set:
        emit_info(
            f"New dependencies to install: {', '.join(sorted(new_dep_set))}"
        )

    # Q5 / CUT-028: full-ladder pre-transaction ESTIMATE. Classify every upgrade
    # target and render it through the SAME classifier + renderer the
    # authoritative post-transaction block uses — reboot / restart-services /
    # relogin / active-now — instead of the coarse reboot+restart-only preview
    # this used to print. Classified against the CURRENTLY-installed file_list
    # (the new files are not on disk until the upgrade runs; service and
    # desktop-shell paths rarely change between versions, so this is a good
    # estimate). Marked as an estimate; the post-transaction Next-steps block,
    # classified from what actually landed, remains authoritative.
    classifications = []
    for installed_pkg, _ in upgradable:
        files = db.get_files(installed_pkg["name"])
        file_list = [
            f["path"] + ("/" if f["is_dir"] else "") for f in files
        ]
        classifications.append((
            installed_pkg["name"],
            classify_restart_requirement(
                installed_pkg["name"], file_list,
                declared_reboot_required=bool(
                    installed_pkg.get("reboot_required")),
            ),
        ))
    estimate_block = format_next_steps(classifications, estimate=True)
    if estimate_block:
        print(estimate_block)

    # Q4 sidecar reminder — exact count requires staging-extraction which
    # happens inside installer.install; surfaced per-package in the install
    # output. Plan summary lets the user know to watch for sidecars.
    emit_info(
        "Configuration-file changes (.pkmnew sidecars) are reported "
        "per-package at install time; review them at end of upgrade."
    )


def _confirm_upgrade(args):
    """Q3 confirmation gate.

    Returns True if the upgrade should proceed; False if the user
    declined OR --dry-run was passed (preview only). Calls sys.exit(1)
    on non-tty stdin without --yes (hard error per dispatch text).
    """
    if getattr(args, "upgrade_dry_run", False):
        emit_info("--dry-run: plan only; nothing modified.")
        return False
    if getattr(args, "upgrade_yes", False):
        return True
    if not sys.stdin.isatty():
        emit_error(
            "stdin is not a tty. Pass --yes to confirm "
            "non-interactively, or --dry-run to preview without changes."
        )
        sys.exit(1)
    try:
        answer = input("  Proceed with upgrade? [y/N] ").strip().lower()
    except EOFError:
        answer = ""
    if answer != "y":
        print("  Aborted.")
        return False
    return True


def cmd_hold(db, args):
    """pkm hold <pkg>... — exclude packages from `pkm upgrade --all`.

    Exit code 0 if every named package was found and held; 1 if any
    name was not installed (already-held packages count as success).
    """
    any_failed = False
    for name in args.packages:
        if not db.get_installed(name):
            emit_error(f"{name} is not installed")
            any_failed = True
            continue
        db.set_held(name, held=True)
        emit_done(f"Held {name}")
    return 1 if any_failed else 0


def cmd_unhold(db, args):
    """pkm unhold <pkg>... — release a hold."""
    any_failed = False
    for name in args.packages:
        if not db.get_installed(name):
            emit_error(f"{name} is not installed")
            any_failed = True
            continue
        db.set_held(name, held=False)
        emit_done(f"Released {name}")
    return 1 if any_failed else 0


def cmd_mark(db, args):
    """pkm mark auto|manual <pkg>... — update install_reason."""
    reason = "dependency" if args.reason == "auto" else "manual"
    any_failed = False
    for name in args.packages:
        if not db.get_installed(name):
            emit_error(f"{name} is not installed")
            any_failed = True
            continue
        db.set_install_reason(name, reason)
        emit_done(f"Marked {name} as {args.reason} ({reason})")
    return 1 if any_failed else 0


def cmd_autoremove(db, args):
    """pkm autoremove [--yes] [--dry-run] — remove orphan dep-installed pkgs.

    Eligibility: install_reason='dependency' AND no currently-installed
    package depends on them. Manual-installed packages are NEVER touched.
    """
    orphans = db.find_orphan_packages()
    if not orphans:
        emit_info("No orphan packages to remove.")
        return 0

    emit_info(f"{len(orphans)} orphan package(s) eligible for removal:")
    for o in orphans:
        tier = f" [{o['tier']}]" if o.get("tier") else ""
        print(f"    {o['name']:30s} {o['version']:15s}{tier}")

    if getattr(args, "autoremove_dry_run", False):
        emit_info("--dry-run: nothing removed.")
        return 0

    if not getattr(args, "autoremove_yes", False):
        if not sys.stdin.isatty():
            emit_error(
                "stdin is not a tty; pass --yes to confirm "
                "non-interactively, or --dry-run to preview."
            )
            return 1
        try:
            answer = input("  Proceed with removal? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer != "y":
            print("  Aborted.")
            return 0

    remover = PackageRemover(db)
    any_failed = False
    for o in orphans:
        ok, msg = remover.remove(o["name"], force=False)
        if ok:
            emit_info(f"Removed {o['name']}")
        else:
            emit_error(f"removing {o['name']}: {msg}")
            any_failed = True
    return 1 if any_failed else 0


def _known_owned_paths(db, root, name, version):
    """Every path `name` is known to own, from BOTH of pkm's records.

    The database file rows are the obvious source and are not sufficient on
    their own: a package whose rows are missing or incomplete has no rows to
    check, and a build chroot's database has carried exactly that damage
    (directory rows written with the wrong is_dir flag corpus-wide, release
    columns reset). The on-disk text manifest is the second, independent
    record of the same payload, so the union is what the package is known to
    own. Read before the removal, which unlinks the manifest.

    Returns root-relative paths with no leading or trailing slash.
    """
    from .database import MANIFEST_DIR, _parse_manifest

    paths = {f["path"].strip("/") for f in db.get_files(name)}
    if version:
        manifest = (Path(root) / MANIFEST_DIR.relative_to("/")
                    / f"{name}-{version}")
        try:
            parsed = _parse_manifest(manifest.read_text(errors="replace"))
        except OSError:
            parsed = None
        if parsed:
            paths |= {p.strip("/") for p in parsed.get("files", [])}
    return {p for p in paths if p}


def cmd_iso_prep(db, args):
    """pkm iso-prep --packages-from FILE [--yes] [--dry-run]

    Remove a list of packages from the system in a topologically-sound
    order that respects pkm's runtime-dep graph. Used by the live-ISO
    build pipeline to evict iso_include:false packages from the chroot
    before mksquashfs runs (Path-b of the D-014 ISO curation walked
    on 2026-05-28).

    Safety rules:
    - Names in the input list that aren't installed are skipped with a
      warning.
    - If any NOT-in-list installed package has a runtime dep on an IN-list
      package, ABORT. That's a metadata bug: an iso_include:true package
      cannot depend on an iso_include:false library because the library
      lives in the mirror, not the ISO. We surface it as a fixable build-
      time error rather than mask it via --force.
    - Removal order is consumers-first / libraries-last via Kahn's
      algorithm against the runtime-dep graph. PackageRemover.remove(...,
      force=False) refuses to remove anything still depended-on, so a
      wrong order would also abort mid-flight with a clear error.
    """
    path = Path(args.iso_prep_packages_from)
    if not path.is_file():
        emit_error(f"--packages-from file not found: {path}")
        sys.exit(1)

    candidates = []
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        candidates.append(s)

    if not candidates:
        emit_info(f"No package names listed in {path} — nothing to do.")
        return 0

    installed = {p["name"]: p for p in db.list_installed()}
    targets = [n for n in candidates if n in installed]
    skipped_not_installed = [n for n in candidates if n not in installed]

    if skipped_not_installed:
        emit_info(
            f"{len(skipped_not_installed)} package(s) in input list not installed, "
            f"skipping:"
        )
        for n in skipped_not_installed[:5]:
            print(f"    {n}")
        if len(skipped_not_installed) > 5:
            print(f"    ... and {len(skipped_not_installed) - 5} more")

    if not targets:
        emit_info("No installed packages match the input list — nothing to remove.")
        return 0

    emit_info(f"{len(targets)} installed package(s) match the iso-prep list.")

    # Safety check: no iso_include:true (not-in-list) package should depend
    # on an iso_include:false (in-list) package. If found, ABORT.
    target_set = set(targets)
    cross_deps = []  # (consumer_name, dep_name)
    for pkg in db.list_installed():
        if pkg["name"] in target_set:
            continue
        for dep in db.get_depends(pkg["name"]):
            # PKM-A18: get_depends yields {"name","type"} — NOT "dep_name"
            # (that's the DB column). The old dep["dep_name"] raised KeyError on
            # the first dependency of any non-target package, crashing this
            # fail-closed cross-dep guard with an opaque traceback instead of
            # either passing or aborting with the metadata-bug message below.
            if dep["name"] in target_set:
                cross_deps.append((pkg["name"], dep["name"]))

    if cross_deps:
        emit_error(
            f"{len(cross_deps)} ISO-shipped package(s) depend on MIRROR-only "
            f"targets.\n"
            f"This is a metadata bug — an iso_include:true package cannot "
            f"depend on an iso_include:false library.\n"
            f"Fix by either flipping the library to iso_include:true (move "
            f"to ISO),\n"
            f"or flipping the consumer to iso_include:false (move to mirror)."
        )
        # Group + sort the offenders by consumer for readable output
        from collections import defaultdict
        by_consumer = defaultdict(list)
        for (consumer, dep) in cross_deps:
            by_consumer[consumer].append(dep)
        for consumer in sorted(by_consumer):
            deps_str = ", ".join(sorted(by_consumer[consumer]))
            print(f"    {consumer} -> {deps_str}", file=sys.stderr)
        sys.exit(1)

    # Topo-sort: Kahn's algorithm against the runtime-dep graph restricted
    # to the target set. Consumers (with no in-target rdeps) go first.
    remaining = set(targets)
    removal_order = []
    while remaining:
        ready = []
        for n in sorted(remaining):
            rdep_names = {r["name"] for r in db.get_reverse_depends(n)}
            if not (rdep_names & remaining):
                ready.append(n)
        if not ready:
            # Cycle within the target set — should not happen given cross-deps
            # check passed, but surface it loudly if it does.
            emit_error(
                f"removal-order topological sort stuck. "
                f"{len(remaining)} package(s) form a cycle:"
            )
            for n in sorted(remaining):
                print(f"    {n}", file=sys.stderr)
            sys.exit(1)
        removal_order.extend(ready)
        remaining -= set(ready)

    # Aggregate reclaimed-size estimate (uncompressed). Sizes can be NULL
    # chroot-wide (the build-time registration path does not populate
    # uncompressed_size), and summing NULLs as 0 printed "0.00 MB
    # reclaimed" over a multi-GB prune — a masked signal that helped the
    # F41 no-op-shaped log read as normal. Track the unknowns explicitly
    # and say so instead of reporting a confident zero.
    _known_sizes = [
        installed[n].get("uncompressed_size") for n in removal_order
        if installed[n].get("uncompressed_size")
    ]
    reclaimed_uncompressed = sum(_known_sizes)
    size_unknown_count = len(removal_order) - len(_known_sizes)

    # --dry-run mode: print plan + size estimate, exit 0
    if getattr(args, "iso_prep_dry_run", False):
        print(f"\n  --dry-run: removal plan ({len(removal_order)} packages):")
        for n in removal_order:
            sz = installed[n].get("uncompressed_size")
            if sz:
                print(f"    {n:40s} {sz / (1024 * 1024):8.2f} MB")
            else:
                print(f"    {n:40s}  size unrecorded")
        total_mb = reclaimed_uncompressed / (1024 * 1024)
        print(f"\n  Estimated reclaimed: {total_mb:.2f} MB uncompressed"
              + (f" (+{size_unknown_count} package(s) of unrecorded size)."
                 if size_unknown_count else "."))
        return 0

    if not getattr(args, "iso_prep_yes", False):
        if not sys.stdin.isatty():
            emit_error(
                "stdin is not a tty; pass --yes to confirm "
                "non-interactively, or --dry-run to preview."
            )
            return 1
        try:
            answer = input(
                f"\n  Proceed with removal of {len(removal_order)} package(s)? "
                f"[y/N] "
            ).strip().lower()
        except EOFError:
            answer = ""
        if answer != "y":
            print("  Aborted.")
            return 0

    remover = PackageRemover(db)
    failures = []
    # Ancestor closure of everything the prune removes, collected per package
    # BEFORE its rows go away — the post-pass below has no other way to know
    # which directories the prune touched once the file rows are gone.
    swept_candidates = set()
    # Every path each pruned package was KNOWN to own, kept per package so the
    # post-removal audit can attribute a survivor to the package that owned
    # it. Collected before the removal, because the removal takes both the
    # rows and the text manifest away.
    owned_by_target = {}
    for n in removal_order:
        for f in db.get_files(n):
            swept_candidates |= ancestor_chain(f["path"])
        owned_by_target[n] = _known_owned_paths(db, remover.root, n,
                                                installed[n].get("version"))
        # No pre-remove hook: this is the build-time prune of packages the
        # image never ships, and it runs inside the mint chroot, whose
        # /run, /proc and /sys belong to the build machine. A hook fired
        # here would act on the builder — stopping its services, unloading
        # its modules — for a package that was never in service on any
        # machine. Runtime hooks belong to real removals on real installs.
        ok, msg = remover.remove(n, force=False, run_pre_remove_hook=False)
        if ok:
            # Surface the remover's own message — it carries the actual
            # file count plus any retained/failed/preserved warnings. The
            # old bare "Removed {n}" line discarded all of it, which let a
            # row-only removal (0 files touched) read identically to a
            # real 16k-file prune in the build log (F41).
            emit_info(msg.replace("\n", "\n    "))
        else:
            emit_error(f"removing {n}: {msg}")
            failures.append((n, msg))

    if failures:
        emit_error(
            f"{len(failures)} removal(s) failed — chroot is in a partial state. "
            f"Inspect each error above and re-run after fixes."
        )
        return 1

    # Emptied-directory cleanup. Removing a package's files leaves its parent
    # chains behind as unowned skeletons: remove() sweeps each package's own
    # ancestors, but a chain usually only becomes empty once a LATER package
    # in the order is removed, by which time that sweep has already run. The
    # residue then ships — a pruned package's directory tree read as present
    # payload on a live evaluation, and 51 such chains needed manual
    # disposition at the shipping-tree ownership gate on the last candidate.
    # This post-pass makes that disposition the prune's own step. It runs only
    # after every removal succeeded: on a partial state the chroot is being
    # inspected and re-run, and the sweep is idempotent, so there is nothing
    # to gain by sweeping a tree that is about to change again.
    swept, exempt_seen = prune_empty_unowned_dirs(db, remover.root,
                                                  swept_candidates)
    if swept:
        emit_info(f"{len(swept)} emptied director"
                  f"{'y' if len(swept) == 1 else 'ies'} removed "
                  f"(empty, and recorded by no remaining package):")
        # Every path, not a truncated sample: the shipping-tree ownership gate
        # reports what survives, and the two lists are only cross-checkable if
        # this one is complete. A count alone is the shape that let an earlier
        # no-op prune read as a normal one in the build log.
        for rel in swept:
            print(f"    /{rel}")
    if exempt_seen:
        emit_info(f"{len(exempt_seen)} empty director"
                  f"{'y' if len(exempt_seen) == 1 else 'ies'} left in place "
                  f"(hook-product or package-state subtree):")
        for rel in exempt_seen:
            print(f"    /{rel}")

    # Outcome assertion. The prune states that the listed packages are gone;
    # this proves it rather than assuming it. On the 2026-08-15 from-scratch
    # build two DESTDIR-staged compatibility symlinks of pruned packages
    # (/opt/jdk, /opt/rocm/llvm) survived the prune and surfaced later at the
    # shipping-tree ownership gate as unowned content with nothing to
    # attribute them to; why removal did not unlink them was never determined
    # from the evidence that survived the burn. An undetermined cause is a
    # reason to check the outcome, not to guess at the cause.
    #
    # Residue is a path a pruned package was known to own that is still on
    # disk, that no remaining package records, and that pkm did not retain on
    # purpose. Directories belong to the emptied-skeleton sweep above: a
    # non-empty one holds somebody's payload, and an empty unowned one has
    # already been judged. Nothing is deleted here — a removal path that left
    # payload behind is not one to give a second, unaudited deletion pass.
    remaining_recorded = {p.strip("/") for (p,) in
                          db.conn.execute("SELECT path FROM files")}
    residue = []  # (package, path)
    checked = 0
    for name in removal_order:
        for rel in sorted(owned_by_target.get(name, ())):
            if "/" not in rel:
                continue  # top-level FHS skeleton: removal refuses it by rule
            checked += 1
            if rel in remaining_recorded:
                continue  # a remaining package still records it
            if rel in remover.deliberately_retained:
                continue  # co-owned, or configuration preserved on purpose
            abs_path = str(remover.root / rel)
            if not os.path.lexists(abs_path):
                continue
            if os.path.isdir(abs_path) and not os.path.islink(abs_path):
                continue  # the emptied-skeleton sweep's subject, not this one
            residue.append((name, rel))

    if residue:
        emit_error(
            f"{len(residue)} path(s) of pruned package(s) are STILL ON DISK "
            f"after the prune, recorded by no remaining package.\n"
            f"The prune did not do what it reports. Each path below would "
            f"reach the shipping-tree ownership gate as unowned content with "
            f"nothing to attribute it to.\n"
            f"Nothing was deleted here: fix the removal of the named package "
            f"and re-run iso-prep on a clean substrate."
        )
        # Every path, never a sample: the ownership gate reports what
        # survives, and the two lists are only cross-checkable in full.
        for name, rel in residue:
            print(f"    /{rel}   (owned by pruned {name})", file=sys.stderr)
        return 1

    emit_info(f"prune outcome verified: {checked} owned path(s) across "
              f"{len(removal_order)} pruned package(s) checked, none left on "
              f"disk unowned.")

    _size_part = f"{reclaimed_uncompressed / (1024*1024):.2f} MB reclaimed"
    if size_unknown_count:
        _size_part += (
            f" ({size_unknown_count} package(s) had no recorded size — "
            f"true total is higher; per-package file counts above are "
            f"the honest record)"
        )
    emit_done(
        f"iso-prep complete: {len(removal_order)} package(s) removed, "
        f"{_size_part}."
    )
    return 0


# Q8 Phase A: notification-surface substrate. The systemd timer +
# GNOME extension + MOTD line (Phases B/C/D, landing in a follow-on
# bundle commit) all read this JSON file. Atomic write via tmp +
# rename so concurrent readers never see a partial JSON document.
# Path is module-level for production use; cmd_check_updates accepts
# an `output_path` kwarg so tests can target a temp directory without
# touching system state.
AVAILABLE_UPDATES_PATH = Path("/var/lib/pkm/available-updates.json")


def _compute_available_updates(db, repo, warn=None):
    """Compute (packages, skipped) for the update advisory from the installed
    DB against `repo`'s LOCAL cached index (offline — no network). `packages`
    are the upgradable entries in the stable consumer shape; `skipped` records
    each installed package whose version could not be compared. `warn(name,
    reason)` is called per skip (the interactive check prints a WARN; the
    end-of-transaction refresh passes None). The single source of the advisory's
    package shape, shared by the scheduled check and the transaction refresh."""
    from .version import is_upgradable, VersionParseError
    packages = []
    skipped = []  # PKM-A15: packages that could NOT be evaluated (name+reason)
    for pkg in db.list_installed():
        remote = repo.get_package(pkg["name"])
        if not remote:
            continue
        try:
            if is_upgradable(pkg, remote):
                packages.append({
                    "name": pkg["name"],
                    "installed_version": pkg["version"],
                    "installed_release": pkg.get("release", 1),
                    "remote_version": remote["version"],
                    "remote_release": remote.get("release", 1),
                })
        except VersionParseError as e:
            skipped.append({
                "name": pkg["name"],
                "reason": f"version comparison failed: {e}",
            })
            if warn:
                warn(pkg["name"], str(e))
    # Stable alphabetical order so successive JSONs diff cleanly.
    packages.sort(key=lambda p: p["name"])
    skipped.sort(key=lambda p: p["name"])
    return packages, skipped


def _available_updates_summary(packages, skipped):
    """Assemble the advisory JSON dict — the stable consumer contract the
    notifier extension, the MOTD line, and the timer all read."""
    import time
    now = time.time()
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "checked_at": int(now),
        "count": len(packages),
        "packages": packages,
        # PKM-A15: surface un-evaluated packages to the notification readers.
        "skipped_count": len(skipped),
        "skipped": skipped,
    }


def _write_available_updates_json(summary, output_path):
    """Atomically write the advisory JSON (.tmp sibling + os.replace, atomic on
    POSIX within one filesystem, so a reader never observes a partial file).
    Raises OSError on failure; the caller decides fail-hard (the scheduled
    check, whose systemd unit wants Restart=on-failure) vs fail-soft (a
    transaction refresh, which must never fail the transaction)."""
    import json
    import os
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(summary, f, indent=2, sort_keys=True)
        os.replace(str(tmp_path), str(output_path))
    except OSError:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def refresh_available_updates_after_transaction(db, output_path=None):
    """Recompute the update advisory from the DB against the LOCAL cached index
    and rewrite it atomically at the end of a state-changing transaction, so the
    notifier's top-bar count reflects what just landed instead of the last
    scheduled check (the stale-indicator class fixed here). Cache-only — the
    scheduled timer stays the freshness mechanism for NEW remote releases, so
    this never reaches the network at transaction end.

    Degrades loudly-informationally, never failing the transaction: with no
    synced index (e.g. a purely-local `--archive` install before any
    `pkm update`) recomputing would report zero upgradable and CLOBBER a real
    advisory, so the last-good file is left untouched and the reason is stated;
    an unwritable path is likewise reported and tolerated."""
    if output_path is None:
        output_path = AVAILABLE_UPDATES_PATH
    # Best-effort by contract: the transaction has already committed, so a
    # cosmetic re-count must NEVER propagate an error and undo/abort it. A
    # deliberate single broad boundary (not bulk hardening) — every failure
    # mode degrades loudly-informationally, never silently.
    try:
        repo = RepoManager()
        if not repo.has_synced_index():
            emit_warn(
                "skipped refreshing the available-updates advisory: no synced "
                "repository index in the local cache. The top-bar update count "
                "reflects the last scheduled check until `pkm update` runs."
            )
            return
        packages, skipped = _compute_available_updates(db, repo)
        summary = _available_updates_summary(packages, skipped)
        _write_available_updates_json(summary, output_path)
    except Exception as e:
        emit_warn(
            f"could not refresh the available-updates advisory at "
            f"{output_path}: {e}. The top-bar update count may be stale until "
            f"the next scheduled check."
        )


def cmd_check_updates(db, args, output_path=None):
    """Compare installed packages against repos; write JSON summary.

    Substrate for the Q8 notification surface. NEVER auto-upgrades —
    informational only per the approved Q8 design. The
    consumers (systemd timer + GNOME extension + MOTD line) read the
    written JSON; this function only writes it.

    JSON shape:
        {
            "timestamp": "2026-05-19T07:24:00Z",
            "checked_at": 1748254800,
            "count": 3,
            "packages": [
                {
                    "name": "firefox",
                    "installed_version": "138.0",
                    "installed_release": 1,
                    "remote_version": "139.0",
                    "remote_release": 1
                },
                ...
            ],
            "skipped_count": 1,
            "skipped": [
                {"name": "bad-pkg",
                 "reason": "version comparison failed: <detail>"}
            ]
        }

    `skipped` (PKM-A15) records every installed package whose version
    could not be compared (VersionParseError). It is written regardless
    of --quiet so the unattended consumers can surface "K packages could
    not be evaluated" instead of silently under-reporting.

    Returns 0 on success, exits with 1 on write failure (so the
    systemd timer's Restart=on-failure policy sees the error). The
    --quiet flag suppresses stdout output for unattended timer runs;
    the JSON is always written regardless of --quiet.
    """
    if output_path is None:
        output_path = AVAILABLE_UPDATES_PATH
    output_path = Path(output_path)

    def _warn(name, _reason):
        if not getattr(args, "quiet", False):
            print(
                f"  WARN: cannot compare versions for {name}; skipping",
                file=sys.stderr,
            )

    repo = RepoManager()
    packages, skipped = _compute_available_updates(db, repo, warn=_warn)
    summary = _available_updates_summary(packages, skipped)

    # Fail-hard on write failure so the timer's Restart=on-failure sees it.
    try:
        _write_available_updates_json(summary, output_path)
    except OSError as e:
        print(f"  ERROR: cannot write {output_path}: {e}", file=sys.stderr)
        sys.exit(1)

    if not getattr(args, "quiet", False):
        if summary["count"] == 0:
            print("  Everything is up to date.")
        else:
            print(f"  {summary['count']} package(s) have updates available:")
            for p in packages:
                print(
                    f"    {p['name']:30s} "
                    f"{_vr_str(p['installed_version'], p.get('installed_release', 1)):15s} "
                    f"→ {_vr_str(p['remote_version'], p.get('remote_release', 1))}"
                )
            print(f"  Run `sudo pkm upgrade --all` to install.")
        if summary["skipped_count"]:
            print(
                f"  {summary['skipped_count']} package(s) could NOT be "
                f"evaluated (see `skipped` in {output_path.name}):",
                file=sys.stderr,
            )
            for s in skipped:
                print(f"    {s['name']}: {s['reason']}", file=sys.stderr)
        print(f"  Wrote {output_path}")


def cmd_cache(db, args):
    """pkm cache <action> — manage the pkm download + rollback caches.

    Two cache directories under /var/cache/pkm/:
      packages/   each upgrade adds a fresh archive (the primary
                  download cache that `pkm install` reads).
      rollback/   each `pkm upgrade` writes a pre-upgrade snapshot
                  so failed installs can be reverted.

    Subcommands:
      clean   Remove cached archives by policy. Default target is the
              packages/ cache (--keep-current, --keep N, or --all);
              --rollback switches target to the rollback/ cache.

    No subcommand: print usage hint.
    """
    if getattr(args, "cache_action", None) == "clean":
        return cmd_cache_clean(db, args)
    print("  Usage: pkm cache <action>")
    print("    clean   Remove cached archives by policy")
    return 0


def cmd_cache_clean(db, args):
    """pkm cache clean [--keep-current | --keep N | --all | --rollback].

    Default target: walks /var/cache/pkm/packages/, parses each archive
    filename into (name, version, release), groups by package name, and
    applies the selected policy. Default behavior (no flag) is
    --keep-current. With --rollback, the target switches to
    /var/cache/pkm/rollback/ and the policy is fixed: keep most-recent
    archive per installed package; remove older entries + all entries
    for packages no longer installed.

    --keep-current  Per package: keep the archive matching the installed
                    version (the one that can serve `pkm reinstall`);
                    remove all other versions. For packages NOT
                    currently installed, all cached archives are
                    removed (no rollback target to preserve).
    --keep N        Per package: keep the N most-recent archives by
                    mtime; remove older ones. Useful when the operator
                    wants more than one rollback target available.
    --all           Remove every cached archive. Subsequent installs
                    re-download.
    --rollback      Switch target to /var/cache/pkm/rollback/: keep
                    most-recent per installed package; remove older
                    entries + entries for packages no longer installed.
    """
    if getattr(args, "cache_rollback", False):
        return _cache_clean_rollback(db)

    import re
    from .repo import REPO_PKG_CACHE

    if not REPO_PKG_CACHE.exists():
        emit_info(f"Cache directory {REPO_PKG_CACHE} does not exist; nothing to clean.")
        return 0

    archives = sorted(REPO_PKG_CACHE.glob("*.igos.tar.gz"))
    if not archives:
        emit_info("Cache is empty; nothing to clean.")
        return 0

    # Filename shape: <name>-<version>-<release>.igos.tar.gz.
    # Name can contain dashes (e.g., glibc-core, linux-firmware); use a
    # non-greedy first capture and anchor release as the trailing
    # integer before .igos.tar.gz.
    pattern = re.compile(r"^(.+)-([^-]+)-(\d+)\.igos\.tar\.gz$")
    by_pkg = {}  # name -> list of (path, version, release, mtime)
    unmatched = []
    for path in archives:
        m = pattern.match(path.name)
        if not m:
            unmatched.append(path)
            continue
        name, version, release = m.group(1), m.group(2), int(m.group(3))
        by_pkg.setdefault(name, []).append(
            (path, version, release, path.stat().st_mtime),
        )

    if unmatched:
        # Don't touch files whose names we can't parse — could be
        # third-party content or a partial download from the Q6 retry
        # layer. WARN once so the operator can investigate.
        emit_warn(
            f"{len(unmatched)} file(s) in {REPO_PKG_CACHE} did not "
            f"match the <name>-<version>-<release>.igos.tar.gz shape; "
            f"leaving them untouched."
        )

    to_remove = []
    keep_n = getattr(args, "cache_keep_n", None)
    cache_all = getattr(args, "cache_all", False)

    if cache_all:
        # --all wins everything in by_pkg + every parseable archive.
        for entries in by_pkg.values():
            to_remove.extend(e[0] for e in entries)
    elif keep_n is not None:
        if keep_n < 0:
            emit_error("--keep N must be >= 0")
            return 1
        for entries in by_pkg.values():
            entries.sort(key=lambda e: e[3], reverse=True)
            to_remove.extend(e[0] for e in entries[keep_n:])
    else:
        # Default: --keep-current.
        for name, entries in by_pkg.items():
            installed = db.get_installed(name)
            if installed:
                installed_ver = installed["version"]
                matching = [e for e in entries if e[1] == installed_ver]
                if matching:
                    matching.sort(key=lambda e: e[3], reverse=True)
                    keep_path = matching[0][0]
                    to_remove.extend(
                        e[0] for e in entries if e[0] != keep_path
                    )
                else:
                    # No archive matches installed version (installed
                    # via --archive then archive evicted, perhaps).
                    # Keep the most-recent archive in case the operator
                    # wants to roll forward to it.
                    entries.sort(key=lambda e: e[3], reverse=True)
                    keep_path = entries[0][0]
                    to_remove.extend(
                        e[0] for e in entries if e[0] != keep_path
                    )
            else:
                # Package not installed — no rollback target to preserve.
                to_remove.extend(e[0] for e in entries)

    if not to_remove:
        emit_info("Nothing to clean (cache state matches policy).")
        return 0

    total_bytes = sum(p.stat().st_size for p in to_remove)
    emit_info(
        f"Removing {len(to_remove)} archive(s) "
        f"({total_bytes / (1024 * 1024):.1f} MiB):"
    )
    for p in sorted(to_remove):
        print(f"    {p.name}")
    any_failed = False
    for p in to_remove:
        try:
            p.unlink()
        except OSError as e:
            emit_warn(f"failed to remove {p}: {e}")
            any_failed = True
    return 1 if any_failed else 0


def _cache_clean_rollback(db):
    """Prune /var/cache/pkm/rollback/ to one archive per installed package.

    Each `pkm upgrade` writes a fresh archive to REPO_ROLLBACK_DIR via
    _save_rollback_archive (pkm/cli.py:1321) so the old version can be
    restored on install failure. Without periodic cleanup, the directory
    grows unbounded -- every upgrade ever performed leaves a stale
    rollback target behind.

    Policy (matches the spirit of --keep-current on the pkg cache):
      - For installed packages: keep the most-recent archive by mtime
        (the freshest pre-upgrade snapshot); remove older entries.
      - For packages no longer installed: remove all their rollback
        archives -- no install state left to roll back to.

    Implementation note (db-driven longest-prefix-match): the rollback
    filename shape is `<name>-<version>-<release>.igos.tar.gz` where
    both `<name>` and `<version>` may contain hyphens (9 in-tree
    packages today including dialog `1.3-20260107` and imagemagick
    `7.1.2-13` plus 7 desktop themes with date-shaped versions). A
    single regex cannot disambiguate the boundary, so this routine
    asks the DB for the set of known installed package names + uses
    longest-prefix-match against each rollback file to assign it to a
    package. Files that match no installed name are orphans + removed.
    """
    from .repo import REPO_ROLLBACK_DIR

    if not REPO_ROLLBACK_DIR.exists():
        emit_info(f"Rollback directory {REPO_ROLLBACK_DIR} does not exist; "
                  "nothing to clean.")
        return 0

    archives = sorted(REPO_ROLLBACK_DIR.glob("*.igos.tar.gz"))
    if not archives:
        emit_info("Rollback cache is empty; nothing to clean.")
        return 0

    # Sort installed names longest-first so longest-prefix-match wins
    # (e.g. `dialog-tui` wins over `dialog` for a `dialog-tui-1.0-1...`
    # file when both packages are installed).
    installed_names = sorted(
        (row["name"] for row in db.list_installed()),
        key=len,
        reverse=True,
    )

    by_pkg = {}      # installed_name -> list of (path, mtime)
    orphans = []     # files whose name-prefix matches no installed package
    for path in archives:
        fname = path.name
        matched = None
        for name in installed_names:
            if fname.startswith(name + "-"):
                matched = name
                break
        if matched is None:
            orphans.append(path)
        else:
            by_pkg.setdefault(matched, []).append(
                (path, path.stat().st_mtime),
            )

    to_remove = list(orphans)
    for name, entries in by_pkg.items():
        # Keep the most-recent (freshest pre-upgrade snapshot); remove
        # older entries for the same installed package.
        entries.sort(key=lambda e: e[1], reverse=True)
        to_remove.extend(e[0] for e in entries[1:])

    if not to_remove:
        emit_info("Rollback cache state matches policy "
                  "(one archive per installed package); nothing to clean.")
        return 0

    total_bytes = sum(p.stat().st_size for p in to_remove)
    emit_info(
        f"Removing {len(to_remove)} rollback archive(s) "
        f"({total_bytes / (1024 * 1024):.1f} MiB):"
    )
    for p in sorted(to_remove):
        print(f"    {p.name}")
    any_failed = False
    for p in to_remove:
        try:
            p.unlink()
        except OSError as e:
            emit_warn(f"failed to remove {p}: {e}")
            any_failed = True
    return 1 if any_failed else 0


if __name__ == "__main__":
    main()
