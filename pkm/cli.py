"""pkm CLI — Natural-language command interface for InterGenOS package management."""

import argparse
import contextlib
import sys
from pathlib import Path

try:
    import fcntl
    _HAS_FLOCK = True
except ImportError:
    fcntl = None
    _HAS_FLOCK = False

from . import __version__
from .database import PackageDB
from .installer import PackageInstaller
from .remover import PackageRemover
from .verifier import PackageVerifier
from .repo import RepoManager


# H-023: serialize concurrent pkm mutations via fcntl.flock on /var/lock/pkm.lock.
# Mutating subcommands acquire LOCK_EX | LOCK_NB at top of dispatch; second
# concurrent mutator gets immediate failure with a hint, not a silent wait
# (Holy-Grail-aligned posture: prefer fail-loud over queue-and-hope).
PKM_LOCK_PATH = Path("/var/lock/pkm.lock")
PKM_MUTATING_COMMANDS = frozenset({
    "install", "install-helper", "remove", "reinstall", "update", "upgrade", "import",
    "restart-services",
    # Q9: hold/unhold/mark mutate DB state; autoremove mutates filesystem +
    # DB. All four go through the flock gate.
    "hold", "unhold", "mark", "autoremove",
    # O-013: cache subcommand mutates /var/cache/pkm/packages/.
    "cache",
})


@contextlib.contextmanager
def _pkm_mutation_lock(command):
    """Acquire fcntl.flock on PKM_LOCK_PATH for the duration of a mutating
    subcommand. No-op for read-only commands or platforms without fcntl
    (e.g. test runs on Windows where fcntl is unavailable; production pkm
    only runs on Linux). Raises sys.exit(1) on lock-contention.
    """
    if command not in PKM_MUTATING_COMMANDS or not _HAS_FLOCK:
        yield
        return
    try:
        PKM_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"  ERROR: cannot create lock-file parent dir {PKM_LOCK_PATH.parent}: {e}",
              file=sys.stderr)
        sys.exit(1)
    fd = open(str(PKM_LOCK_PATH), "w")
    try:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            fd.close()
            print(
                f"  ERROR: another pkm operation is in progress (lock held on "
                f"{PKM_LOCK_PATH}). Wait for it to complete, or check for stale "
                f"pkm processes (`fuser {PKM_LOCK_PATH}` or `lsof {PKM_LOCK_PATH}`).",
                file=sys.stderr,
            )
            sys.exit(1)
        yield
    finally:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fd.close()


def main():
    parser = argparse.ArgumentParser(
        prog="pkm",
        description="InterGenOS Package Manager",
    )
    parser.add_argument("--version", action="version", version=f"pkm {__version__}")
    parser.add_argument("--db", help="Database path override")

    sub = parser.add_subparsers(dest="command", metavar="command")

    # -- install --
    p_install = sub.add_parser("install", help="Install a package")
    p_install.add_argument("packages", nargs="+", metavar="package")
    p_install.add_argument("--archive", help="Path to .igos.tar.gz archive")
    p_install.add_argument("--archive-trust", choices=["strict","loose","repo-only"],
                           default="strict",
                           help="Trust mode for --archive installs (default: strict)")

    # -- install-helper --
    p_helper = sub.add_parser("install-helper", help="Install proprietary software via download helper")
    p_helper.add_argument("package", help="Package to install (e.g., chrome, vscode, claude-code)")

    # -- remove --
    p_remove = sub.add_parser("remove", aliases=["uninstall"], help="Remove a package")
    p_remove.add_argument("package")
    p_remove.add_argument("--force", action="store_true", help="Remove even if others depend on it")

    # -- reinstall --
    p_reinstall = sub.add_parser("reinstall", help="Remove + reinstall a package (repo-fetched)")
    p_reinstall.add_argument("packages", nargs="+", metavar="package")

    # -- list --
    p_list = sub.add_parser("list", aliases=["ls"], help="List packages")
    p_list.add_argument("what", choices=["installed", "available", "upgradable"], nargs="?", default="installed")
    p_list.add_argument("--tier", help="Filter by tier")

    # -- update --
    sub.add_parser("update", aliases=["sync", "refresh"], help="Sync package index from repositories")

    # -- upgrade --
    p_upgrade = sub.add_parser("upgrade", help="Upgrade installed packages")
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
             "the repository index (Q7 hand-curated; F-002 v1.1 will replace "
             "with automated CVE feed ingestion).",
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
            "per the operator-greenlit Q8 design."
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

    # -- cache -- (O-013 cache GC)
    p_cache = sub.add_parser(
        "cache",
        help="Manage the pkm download cache",
        description=(
            "Inspect and prune /var/cache/pkm/packages/. The cache grows "
            "unbounded otherwise — every upgrade adds a new archive and "
            "the old one stays. Three subcommands: clean (remove archives "
            "per policy)."
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

    if not args.command:
        parser.print_help()
        return

    db = PackageDB(args.db)

    try:
        with _pkm_mutation_lock(args.command):
            if args.command == "install":
                cmd_install(db, args)
            elif args.command == "install-helper":
                cmd_install_helper(db, args)
            elif args.command == "remove":
                cmd_remove(db, args)
            elif args.command == "reinstall":
                cmd_reinstall(db, args)
            elif args.command == "update":
                cmd_update(db, args)
            elif args.command == "upgrade":
                cmd_upgrade(db, args)
            elif args.command == "list":
                cmd_list(db, args)
            elif args.command == "search":
                cmd_search(db, args)
            elif args.command == "info":
                cmd_info(db, args)
            elif args.command == "files":
                cmd_files(db, args)
            elif args.command == "provides":
                cmd_provides(db, args)
            elif args.command == "verify":
                cmd_verify(db, args)
            elif args.command == "depends":
                cmd_depends(db, args)
            elif args.command == "history":
                cmd_history(db, args)
            elif args.command == "import":
                cmd_import(db, args)
            elif args.command == "refresh-baseline":
                cmd_refresh_baseline(db, args)
            elif args.command == "restart-services":
                cmd_restart_services(db, args)
            elif args.command == "hold":
                cmd_hold(db, args)
            elif args.command == "unhold":
                cmd_unhold(db, args)
            elif args.command == "mark":
                cmd_mark(db, args)
            elif args.command == "autoremove":
                cmd_autoremove(db, args)
            elif args.command == "check-updates":
                cmd_check_updates(db, args)
            elif args.command == "cache":
                cmd_cache(db, args)
    finally:
        db.close()


# ------------------------------------------------------------------
# Command implementations
# ------------------------------------------------------------------

def cmd_install(db, args):
    installer = PackageInstaller(db)
    repo = RepoManager()

    if args.archive and len(args.packages) > 1:
        print(
            f"  ERROR: --archive {args.archive} cannot be used with multiple "
            f"packages ({', '.join(args.packages)}). The archive is a "
            f"single-package artifact. Run separately per package, or omit "
            f"--archive to fetch all packages from the repo.",
            file=sys.stderr,
        )
        sys.exit(1)

    for pkg_name in args.packages:
        archive = args.archive if len(args.packages) == 1 else None

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
                print(f"  ERROR: cannot read archive: {e}")
                sys.exit(1)

            print(f"  Archive: {archive}")
            print(f"  SHA256:  {archive_sha}")

            # Cross-check against repo index
            repo_match = False
            repo_pkg = None
            try:
                repo_pkg = repo.get_package(pkg_name)
                if repo_pkg and repo_pkg.get("sha256") == archive_sha:
                    repo_match = True
                    print(f"  Trust:   SHA256 matches repository index for {pkg_name} {repo_pkg.get('version','?')}")
            except Exception:
                repo_pkg = None

            if not repo_match and repo_pkg and repo_pkg.get("sha256"):
                print(f"  MISMATCH: archive SHA256 does not match repository index!")
                print(f"    archive: {archive_sha}")
                print(f"    repo:    {repo_pkg['sha256']}")

            # Trust gate (H3)
            if trust_mode == "repo-only" and not repo_match:
                print(f"  REJECTED: --archive-trust=repo-only requires archive SHA256")
                print(f"  to match the repository index. Use --archive-trust=loose to override.")
                continue
            elif trust_mode == "strict" and not repo_match:
                print(f"  REJECTED: --archive-trust=strict requires SHA256 match against")
                print(f"  repository index. Use --archive-trust=loose to override.")
                continue
            elif trust_mode == "loose":
                print(f"  WARNING: --archive-trust=loose — skipping repo verification.")
                print(f"  Verify SHA256 independently before trusting this archive.")
        # L-021: pass the SHA256 we computed for --archive path through
        # to installer.install for the TOCTOU re-verification gate.
        ok, msg = installer.install(
            pkg_name, archive_path=archive,
            expected_sha256=(archive_sha if archive else None),
        )
        if ok:
            print(f"  {msg}")
            continue

        # If no local archive, try fetching from repo
        if "No archive found" in msg or "not found" in msg.lower():
            print(f"  Checking repositories for '{pkg_name}'...")

            # Resolve dependencies
            dep_ok, deps = repo.resolve_dependencies(pkg_name, db)
            if not dep_ok:
                print(f"  ERROR: {deps}", file=sys.stderr)
                sys.exit(1)

            if len(deps) > 1:
                print(f"  Will install {len(deps)} packages: {', '.join(deps)}")

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
                    print(
                        f"  ERROR: {preflight.format_preflight_failure(check, REPO_PKG_CACHE)}",
                        file=sys.stderr,
                    )
                    sys.exit(1)

            for dep_name in deps:
                dl_ok, dl_result = repo.download_package(dep_name)
                if not dl_ok:
                    print(f"  ERROR: {dl_result}", file=sys.stderr)
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
                )
                if inst_ok:
                    print(f"  {inst_msg}")
                else:
                    print(f"  ERROR installing {dep_name}: {inst_msg}", file=sys.stderr)
                    sys.exit(1)
        else:
            print(f"  ERROR: {msg}", file=sys.stderr)
            sys.exit(1)


def cmd_install_helper(db, args):
    installer = PackageInstaller(db)
    name = args.package
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
        if available:
            print(f"  No install helper found for '{name}'")
            print(f"  Available helpers: {', '.join(sorted(available))}")
        else:
            print(f"  No install helpers found on this system")
        sys.exit(1)

    ok, msg = installer._run_helper(name, helper)
    if ok:
        print(f"  {msg}")
    else:
        print(f"  ERROR: {msg}", file=sys.stderr)
        sys.exit(1)


def cmd_reinstall(db, args):
    """Remove + reinstall packages from the repo (O-001).

    Closes audit row O-001 (subcommand never existed) + H-010 (the error
    messages at installer.py:83 + :88 directed users to a non-existent
    `pkm reinstall` subcommand; that guidance is now correct).

    Atomic-ish caveat: filesystem deploy + DB are separate concerns.
    If install fails after remove succeeds, the package is GONE; recovery
    is `pkm install <name>`. Matches the existing cmd_upgrade gap (H-019);
    a future btrfs-snapshot pre-mutation snapshot pass (O-007 scope) will
    address both paths together.
    """
    installer = PackageInstaller(db)
    remover = PackageRemover(db)

    for pkg_name in args.packages:
        existing = db.get_installed(pkg_name)
        if not existing:
            print(
                f"  ERROR: '{pkg_name}' is not installed; nothing to reinstall. "
                f"Use 'pkm install {pkg_name}' instead.",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"  Reinstalling {pkg_name} ({existing['version']})...")

        ok, rmsg = remover.remove(pkg_name, force=True)
        if not ok:
            print(f"  ERROR: remove step failed for {pkg_name}: {rmsg}", file=sys.stderr)
            sys.exit(1)
        print(f"    {rmsg}")

        ok, imsg = installer.install(pkg_name)
        if not ok:
            print(
                f"  ERROR: install step failed after remove for {pkg_name}: "
                f"{imsg}",
                file=sys.stderr,
            )
            print(
                f"  System is in degraded state — {pkg_name} is removed but "
                f"not reinstalled. Run 'pkm install {pkg_name}' to recover.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"    {imsg}")


def cmd_remove(db, args):
    remover = PackageRemover(db)
    ok, msg = remover.remove(args.package, force=args.force)
    if ok:
        print(f"  {msg}")
    else:
        print(f"  ERROR: {msg}", file=sys.stderr)
        sys.exit(1)


def cmd_update(db, args):
    repo = RepoManager()
    print("  Syncing package indexes...")
    results = repo.sync()
    for name, ok, msg in results:
        status = "OK" if ok else "FAIL"
        print(f"    [{status}] {name}: {msg}")


def cmd_upgrade(db, args):
    from .version import is_upgradable, VersionParseError

    # Q3 (O-027): refuse bare `pkm upgrade` invocations. Bare = no
    # positional packages AND no --all. Default-deny on destructive
    # mass-mutate — silent mass-modify is the opposite of "when in
    # doubt, deny."
    upgrade_all = getattr(args, "upgrade_all", False)
    if not args.packages and not upgrade_all:
        print(
            "  ERROR: bare `pkm upgrade` refuses to mass-modify the system. "
            "Pass --all to upgrade everything (a plan summary + "
            "confirmation prompt follows), or name specific packages to "
            "upgrade. Add --dry-run to preview without modifying anything.",
            file=sys.stderr,
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

    for pkg in installed:
        remote = repo.get_package(pkg["name"])
        if not remote:
            continue
        try:
            if is_upgradable(pkg, remote, allow_downgrade=allow_downgrade):
                upgradable.append((pkg, remote))
        except VersionParseError as e:
            print(
                f"  WARN: cannot compare versions for {pkg['name']}: {e}",
                file=sys.stderr,
            )
            continue

    # Q7 (O-030): --security-only restricts candidates to repo entries
    # flagged security=true (set by generate-repodb.py from docs/
    # governance/security-advisories.yml). Applied before held-filter so
    # the held-skip notice only mentions held packages that WOULD have
    # been security-eligible — keeps the user signal sharp.
    security_only = getattr(args, "upgrade_security_only", False)
    if security_only:
        upgradable = [(i, r) for i, r in upgradable if r.get("security")]
        if not upgradable:
            print(
                "  No security-flagged upgrades available. The repository "
                "index has no entries with security=true matching installed "
                "packages."
            )
            return

    held_excluded_names = []
    if args.packages:
        # Filter to requested packages
        names = set(args.packages)
        # Q9: explicit-named upgrade of a held package fails loud unless
        # --ignore-holds. Avoids the "I asked for nginx and got nothing"
        # silent skip.
        if not ignore_holds:
            held_requested = names & held_set
            if held_requested:
                listed = ", ".join(sorted(held_requested))
                verb = "is" if len(held_requested) == 1 else "are"
                print(
                    f"  ERROR: {listed} {verb} held. Run `pkm unhold <name>` "
                    f"first, or pass --ignore-holds to override (intended "
                    f"for emergency security upgrades only).",
                    file=sys.stderr,
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
            print(
                f"  Nothing to upgrade — the only candidates "
                f"({', '.join(held_excluded_names)}) are held. Run "
                f"`pkm unhold <name>` to release, or pass --ignore-holds."
            )
        else:
            print("  Everything is up to date.")
        return

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
            print(
                f"  ERROR: {preflight.format_preflight_failure(check, REPO_PKG_CACHE)}",
                file=sys.stderr,
            )
            sys.exit(1)

    # Q3: plan summary + Q5 service-restart classification integration.
    _print_upgrade_plan_summary(upgradable, held_excluded_names, db)

    # Q3: confirmation gate.
    if not _confirm_upgrade(args):
        return

    # Q1 (O-002): kernel-replace gate. Refuse `linux-kernel` upgrades
    # without --allow-kernel-replace. The running kernel image stays
    # loaded in memory until reboot, so a partial-failure kernel
    # upgrade can leave the system unbootable (modules deleted, new
    # vmlinuz not yet signed/installed, etc.). Explicit operator
    # intent required.
    allow_kernel_replace = getattr(args, "upgrade_allow_kernel_replace", False)
    KERNEL_REPLACE_GATED = frozenset({"linux-kernel"})
    if not allow_kernel_replace:
        gated_in_queue = [
            p["name"] for p, _ in upgradable if p["name"] in KERNEL_REPLACE_GATED
        ]
        if gated_in_queue:
            print(
                f"  ERROR: refusing to upgrade {', '.join(gated_in_queue)} "
                f"without --allow-kernel-replace. Kernel upgrades can leave "
                f"the system unbootable on partial failure; pass the flag "
                f"to confirm intent. Other packages in the queue are not "
                f"upgraded.",
                file=sys.stderr,
            )
            sys.exit(1)

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
                print(
                    f"  WARN: cannot resolve new dep '{direct_dep}' for "
                    f"{remote_pkg['name']}: {chain}",
                    file=sys.stderr,
                )
                dep_resolution_failed = True
                break
            for d in chain:
                if d not in seen_new:
                    seen_new.add(d)
                    new_deps_to_install.append(d)
        if dep_resolution_failed:
            print(
                f"  Skipping upgrade of {remote_pkg['name']}: new "
                f"dependency resolution failed (see WARN above).",
                file=sys.stderr,
            )
            continue

        dep_install_failed = False
        for new_dep in new_deps_to_install:
            dl_ok, dl_result = repo.download_package(new_dep)
            if not dl_ok:
                print(
                    f"  ERROR downloading new dep {new_dep}: {dl_result}",
                    file=sys.stderr,
                )
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
                print(
                    f"  ERROR installing new dep {new_dep}: {dep_msg}",
                    file=sys.stderr,
                )
                dep_install_failed = True
                break
            print(f"  Installed new dep for {remote_pkg['name']}: {new_dep}")
        if dep_install_failed:
            print(
                f"  Skipping upgrade of {remote_pkg['name']} due to new "
                f"dependency install failure.",
                file=sys.stderr,
            )
            continue

        dl_ok, dl_result = repo.download_package(remote_pkg["name"])
        if not dl_ok:
            print(f"  ERROR downloading {remote_pkg['name']}: {dl_result}", file=sys.stderr)
            continue

        # Q1 (O-007): save the old archive to the rollback cache BEFORE
        # remove. The current pkg-cache archive at REPO_PKG_CACHE/<name>-
        # <oldver>-<rel>.igos.tar.gz becomes the restore source on
        # install-failure (covered below). Missing archive (cache was
        # cleared) → rollback unavailable → WARN but proceed.
        rollback_archive = _save_rollback_archive(
            installed_pkg["name"],
            installed_pkg["version"],
            installed_pkg.get("release", 1),
        )
        if rollback_archive is None:
            print(
                f"  WARN: no cached archive for {installed_pkg['name']} "
                f"{installed_pkg['version']}; rollback unavailable if the "
                f"install fails. Run `pkm cache clean --keep-current` to "
                f"keep installed-version archives in future.",
                file=sys.stderr,
            )

        # Remove old, install new
        from .remover import PackageRemover
        remover = PackageRemover(db)
        remover.remove(installed_pkg["name"], force=True)
        ok, msg = installer.install(
            remote_pkg["name"], archive_path=dl_result,
            # Q9: preserve install_reason across the upgrade so an
            # autoremove-eligible dependency stays dependency-marked.
            install_reason=installed_pkg.get("install_reason", "manual"),
        )
        # Q1 (O-007): install-failure rollback. If installer.install
        # returned not-ok, the old package is gone (removed) and the new
        # package didn't land. Reinstall from the rollback archive to
        # leave the system at its pre-upgrade state.
        if not ok and rollback_archive is not None and rollback_archive.exists():
            print(
                f"  Install of {remote_pkg['name']} {remote_pkg['version']} "
                f"failed; restoring {installed_pkg['name']} "
                f"{installed_pkg['version']} from rollback cache...",
                file=sys.stderr,
            )
            rb_ok, rb_msg = installer.install(
                installed_pkg["name"], archive_path=str(rollback_archive),
                install_reason=installed_pkg.get("install_reason", "manual"),
            )
            if rb_ok:
                print(
                    f"  Rollback succeeded: {installed_pkg['name']} "
                    f"{installed_pkg['version']} restored."
                )
            else:
                print(
                    f"  CRITICAL: rollback of {installed_pkg['name']} also "
                    f"failed: {rb_msg}. System may be in a partially-upgraded "
                    f"state. Manual recovery: `pkm install "
                    f"{installed_pkg['name']} --archive={rollback_archive}`",
                    file=sys.stderr,
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
            print(f"  Upgraded {remote_pkg['name']} to {remote_pkg['version']}")
        else:
            print(f"  ERROR upgrading {remote_pkg['name']}: {msg}", file=sys.stderr)


def cmd_list(db, args):
    if args.what == "installed":
        packages = db.list_installed(tier=args.tier)
        if not packages:
            print("  No packages installed" + (f" in tier '{args.tier}'" if args.tier else ""))
            return
        print(f"  Installed packages ({len(packages)}):")
        for pkg in packages:
            tier = f" [{pkg['tier']}]" if pkg["tier"] else ""
            desc = f" — {pkg['description'][:50]}" if pkg.get("description") else ""
            print(f"    {pkg['name']:30s} {pkg['version']:15s}{tier}{desc}")
    elif args.what == "available":
        repo = RepoManager()
        packages = repo.list_available(tier=args.tier)
        if not packages:
            print("  No packages available. Run 'pkm update' first.")
            return
        print(f"  Available packages ({len(packages)}):")
        for pkg in packages:
            tier = f" [{pkg.get('tier', '')}]" if pkg.get("tier") else ""
            desc = f" — {pkg.get('description', '')[:50]}" if pkg.get("description") else ""
            print(f"    {pkg['name']:30s} {pkg['version']:15s}{tier}{desc}")
    elif args.what == "upgradable":
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
                    print(f"    {pkg['name']:30s} {pkg['version']:15s} → {remote['version']}")
                    count += 1
            except VersionParseError as e:
                print(
                    f"  WARN: cannot compare versions for {pkg['name']}: {e}",
                    file=sys.stderr,
                )
                continue
        if count == 0:
            print("  Everything is up to date.")


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
        print(f"  No packages matching '{args.term}'")
        return
    print(f"  Search results for '{args.term}' ({len(all_results)} matches):")
    for pkg in all_results:
        tier = f" [{pkg.get('tier', '')}]" if pkg.get("tier") else ""
        desc = f" — {pkg.get('description', '')[:50]}" if pkg.get("description") else ""
        status = " [installed]" if pkg["name"] in installed_names else ""
        print(f"    {pkg['name']:30s} {pkg['version']:15s}{tier}{desc}{status}")


def cmd_info(db, args):
    pkg = db.get_installed(args.package)
    if not pkg:
        print(f"  Package '{args.package}' is not installed")
        return

    print(f"  {'=' * 50}")
    print(f"  {pkg['name']} {pkg['version']}")
    print(f"  {'=' * 50}")
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
            print(f"    {d['name']} {d['version']}")

    files = db.get_files(args.package)
    file_count = len([f for f in files if not f["is_dir"]])
    print(f"\n  Files: {file_count}")
    print()


def cmd_files(db, args):
    files = db.get_files(args.package)
    if not files:
        print(f"  Package '{args.package}' not found or has no tracked files")
        return
    print(f"  Files in {args.package} ({len(files)}):")
    for f in files:
        prefix = "d " if f["is_dir"] else "  "
        print(f"  {prefix}/{f['path']}")


def cmd_provides(db, args):
    result = db.find_owner(args.file)
    if result:
        print(f"  /{result['path']} is owned by {result['name']} {result['version']}")
    else:
        print(f"  No package owns '{args.file}'")


def cmd_verify(db, args):
    # Exit codes (CLI-local; differ from verifier.py API EXIT_* dict codes):
    #   0 = OK (incl. single-pkg routed-to-superseded-successor message)
    #   1 = verification problems found (missing or modified files)
    #   2 = usage error (no package + no --all)
    # API-level EXIT_SUPERSEDED=2 is a dict-level informational code and is
    # NEVER propagated as sys.exit; CLI translates it to message + exit 0.
    verifier = PackageVerifier(db)
    mode = getattr(args, "verify_mode", "strict")

    if args.verify_all or not args.package:
        if not args.verify_all and not args.package:
            print("  Usage: pkm verify <package> or pkm verify --all")
            sys.exit(2)
        results = verifier.verify_all(mode=mode)
        ok_count = 0
        problem_count = 0
        for name, version, result in results:
            if result["missing"] or result["modified"]:
                problem_count += 1
                print(f"  PROBLEM: {name} {version} — {len(result['missing'])} missing, {len(result['modified'])} modified")
            else:
                ok_count += 1
        print(f"\n  Verified ({mode}): {ok_count} OK, {problem_count} with issues")
        if problem_count > 0:
            sys.exit(1)
        return

    result = verifier.verify(args.package, mode=mode)
    if result is None:
        print(f"  Package '{args.package}' is not installed")
        return
    if result.get("superseded_by"):
        print(f"  {result['message']}")
        return
    if not result["missing"] and not result["modified"]:
        suffix = "files verified" if mode == "strict" else "files present (existence-only)"
        print(f"  {args.package}: OK ({result['total']} {suffix})")
        return
    if result["missing"]:
        print(f"  MISSING ({len(result['missing'])}):")
        for f in result["missing"][:20]:
            print(f"    /{f}")
        if len(result["missing"]) > 20:
            print(f"    ... and {len(result['missing']) - 20} more")
    if result["modified"]:
        print(f"  MODIFIED ({len(result['modified'])}):")
        for f in result["modified"][:20]:
            print(f"    /{f}")
    sys.exit(1)


def cmd_depends(db, args):
    if args.reverse:
        rdeps = db.get_reverse_depends(args.package)
        if not rdeps:
            print(f"  No packages depend on '{args.package}'")
            return
        print(f"  Packages that depend on {args.package} ({len(rdeps)}):")
        for d in rdeps:
            print(f"    {d['name']} {d['version']} ({d['type']})")
    else:
        deps = db.get_depends(args.package)
        if not deps:
            pkg = db.get_installed(args.package)
            if not pkg:
                print(f"  Package '{args.package}' is not installed")
            else:
                print(f"  {args.package} has no tracked dependencies")
            return
        print(f"  Dependencies of {args.package} ({len(deps)}):")
        for d in deps:
            installed = db.get_installed(d["name"])
            status = f" [installed: {installed['version']}]" if installed else " [not installed]"
            print(f"    [{d['type']:8s}] {d['name']}{status}")


def cmd_history(db, args):
    entries = db.get_history(package_name=args.package)
    if not entries:
        print("  No history recorded")
        return
    print(f"  Package history ({len(entries)} entries):")
    for e in entries:
        status = "OK" if e["success"] else "FAILED"
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
    print("  Importing existing text manifests...")
    count = db.import_manifests()
    print(f"  Imported {count} package(s) into pkm database")


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
        print(f"  {msg}")
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
            classification = classify_restart_requirement(pkg["name"], file_list)
            if classification["requirement"] == "none":
                continue
            any_action = True
            print(f"  {pkg['name']}:")
            summary = format_service_summary(classification)
            if summary:
                print(summary)
        if not any_action:
            print("  No services need restart and no reboot is required.")
        return 0

    if args.restart_all:
        installed = db.list_installed()
        all_services = []
        reboot_reasons = []
        for pkg in installed:
            files = db.get_files(pkg["name"])
            file_list = [f["path"] + ("/" if f["is_dir"] else "") for f in files]
            classification = classify_restart_requirement(pkg["name"], file_list)
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
                print("  No active services to restart.")
            return 0
        print(f"  Restarting {len(unique_services)} service(s): "
              f"{', '.join(unique_services)}")
        results = run_restart_services(unique_services)
        return _render_restart_results(results)

    if args.services:
        print(f"  Restarting {len(args.services)} service(s): "
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
        Path to the rollback archive on success, or None when the
        old archive is not in REPO_PKG_CACHE (cache was cleared,
        --archive install never cached, etc.). Caller treats None
        as "rollback unavailable; proceed with WARN."
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
        return dest
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
        print(f"  Restarted: {', '.join(successes)}")
    if failures:
        print(f"  ERROR: failed to restart: {', '.join(failures)}",
              file=sys.stderr)
        return 1
    return 0


def _print_upgrade_plan_summary(upgradable, held_excluded_names, db):
    """Q3: structured plan summary printed before the confirmation gate.

    Args:
        upgradable: list of (installed_pkg, remote_pkg) tuples.
        held_excluded_names: list of held package names that were filtered.
        db: PackageDB for per-package file_list lookups (Q5 classification).
    """
    from .services import classify_restart_requirement

    n = len(upgradable)
    print(f"  Upgrade plan: {n} package(s)")
    for installed_pkg, remote_pkg in upgradable:
        print(
            f"    {installed_pkg['name']:30s} "
            f"{installed_pkg['version']:15s} -> {remote_pkg['version']}"
        )

    # Download size summary — sum repo-declared sizes for packages where
    # the repo index provides one. Missing size is treated as 0 (no warn).
    total_size = sum(int(r.get("size") or 0) for _, r in upgradable)
    if total_size > 0:
        mb = total_size / (1024 * 1024)
        print(f"  Download size: ~{mb:.1f} MiB")

    if held_excluded_names:
        print(
            f"  Excluded (held): {', '.join(held_excluded_names)} "
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
        print(
            f"  New dependencies to install: {', '.join(sorted(new_dep_set))}"
        )

    # Q5 integration: classify each upgrade target against the currently-
    # installed file_list. This is approximate (the new file_list may differ
    # post-supersede) but service-unit paths rarely change between versions,
    # so this gives a good pre-upgrade estimate of what'll need restart.
    reboot_pkgs = []
    restart_services_combined = []
    for installed_pkg, _ in upgradable:
        files = db.get_files(installed_pkg["name"])
        file_list = [
            f["path"] + ("/" if f["is_dir"] else "") for f in files
        ]
        classification = classify_restart_requirement(
            installed_pkg["name"], file_list,
        )
        if classification["requirement"] == "reboot":
            reboot_pkgs.append(installed_pkg["name"])
        elif classification["requirement"] == "restart":
            restart_services_combined.extend(classification["services"])

    if reboot_pkgs:
        print(
            f"  REBOOT REQUIRED after upgrade for: {', '.join(reboot_pkgs)}"
        )
    if restart_services_combined:
        # Dedupe preserving discovery order.
        seen = set()
        unique = [
            s for s in restart_services_combined
            if not (s in seen or seen.add(s))
        ]
        print(
            f"  Services needing restart after upgrade: {', '.join(unique)}"
        )
        print(f"    (after upgrade: pkm restart-services --all)")

    # Q4 sidecar reminder — exact count requires staging-extraction which
    # happens inside installer.install; surfaced per-package in the install
    # output. Plan summary lets the user know to watch for sidecars.
    print(
        "  Configuration-file changes (.pkmnew sidecars) are reported "
        "per-package at install time; review them at end of upgrade."
    )


def _confirm_upgrade(args):
    """Q3 confirmation gate.

    Returns True if the upgrade should proceed; False if the user
    declined OR --dry-run was passed (preview only). Calls sys.exit(1)
    on non-tty stdin without --yes (hard error per dispatch text).
    """
    if getattr(args, "upgrade_dry_run", False):
        print("  --dry-run: plan only; nothing modified.")
        return False
    if getattr(args, "upgrade_yes", False):
        return True
    if not sys.stdin.isatty():
        print(
            "  ERROR: stdin is not a tty. Pass --yes to confirm "
            "non-interactively, or --dry-run to preview without changes.",
            file=sys.stderr,
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
            print(f"  ERROR: {name} is not installed", file=sys.stderr)
            any_failed = True
            continue
        db.set_held(name, held=True)
        print(f"  Held: {name}")
    return 1 if any_failed else 0


def cmd_unhold(db, args):
    """pkm unhold <pkg>... — release a hold."""
    any_failed = False
    for name in args.packages:
        if not db.get_installed(name):
            print(f"  ERROR: {name} is not installed", file=sys.stderr)
            any_failed = True
            continue
        db.set_held(name, held=False)
        print(f"  Released: {name}")
    return 1 if any_failed else 0


def cmd_mark(db, args):
    """pkm mark auto|manual <pkg>... — update install_reason."""
    reason = "dependency" if args.reason == "auto" else "manual"
    any_failed = False
    for name in args.packages:
        if not db.get_installed(name):
            print(f"  ERROR: {name} is not installed", file=sys.stderr)
            any_failed = True
            continue
        db.set_install_reason(name, reason)
        print(f"  Marked {name} as {args.reason} ({reason})")
    return 1 if any_failed else 0


def cmd_autoremove(db, args):
    """pkm autoremove [--yes] [--dry-run] — remove orphan dep-installed pkgs.

    Eligibility: install_reason='dependency' AND no currently-installed
    package depends on them. Manual-installed packages are NEVER touched.
    """
    orphans = db.find_orphan_packages()
    if not orphans:
        print("  No orphan packages to remove.")
        return 0

    print(f"  {len(orphans)} orphan package(s) eligible for removal:")
    for o in orphans:
        tier = f" [{o['tier']}]" if o.get("tier") else ""
        print(f"    {o['name']:30s} {o['version']:15s}{tier}")

    if getattr(args, "autoremove_dry_run", False):
        print("  --dry-run: nothing removed.")
        return 0

    if not getattr(args, "autoremove_yes", False):
        if not sys.stdin.isatty():
            print(
                "  ERROR: stdin is not a tty; pass --yes to confirm "
                "non-interactively, or --dry-run to preview.",
                file=sys.stderr,
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
            print(f"  Removed {o['name']}")
        else:
            print(f"  ERROR removing {o['name']}: {msg}", file=sys.stderr)
            any_failed = True
    return 1 if any_failed else 0


# Q8 Phase A: notification-surface substrate. The systemd timer +
# GNOME extension + MOTD line (Phases B/C/D, landing in a follow-on
# bundle commit) all read this JSON file. Atomic write via tmp +
# rename so concurrent readers never see a partial JSON document.
# Path is module-level for production use; cmd_check_updates accepts
# an `output_path` kwarg so tests can target a temp directory without
# touching system state.
AVAILABLE_UPDATES_PATH = Path("/var/lib/pkm/available-updates.json")


def cmd_check_updates(db, args, output_path=None):
    """Compare installed packages against repos; write JSON summary.

    Substrate for the Q8 notification surface. NEVER auto-upgrades —
    informational only per the operator-greenlit Q8 design. The
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
            ]
        }

    Returns 0 on success, exits with 1 on write failure (so the
    systemd timer's Restart=on-failure policy sees the error). The
    --quiet flag suppresses stdout output for unattended timer runs;
    the JSON is always written regardless of --quiet.
    """
    import json
    import os
    import time
    from .version import is_upgradable, VersionParseError

    if output_path is None:
        output_path = AVAILABLE_UPDATES_PATH
    output_path = Path(output_path)

    repo = RepoManager()
    installed = db.list_installed()
    packages = []

    for pkg in installed:
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
        except VersionParseError:
            # WARN-and-skip per the O-010 cmd_upgrade pattern — corrupted
            # version on a single package doesn't block the whole check.
            # Silent in --quiet mode; visible otherwise.
            if not getattr(args, "quiet", False):
                print(
                    f"  WARN: cannot compare versions for {pkg['name']}; skipping",
                    file=sys.stderr,
                )
            continue

    # Sort packages alphabetically by name so JSON output is stable
    # across runs against the same install state — readers can diff
    # successive JSONs to see which packages newly appeared.
    packages.sort(key=lambda p: p["name"])

    now = time.time()
    summary = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "checked_at": int(now),
        "count": len(packages),
        "packages": packages,
    }

    # Atomic write: write to .tmp sibling then os.replace (atomic on POSIX
    # within the same filesystem). Readers never observe a partial JSON.
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(
            f"  ERROR: cannot create {output_path.parent}: {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    tmp_path = output_path.with_name(output_path.name + ".tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(summary, f, indent=2, sort_keys=True)
        os.replace(str(tmp_path), str(output_path))
    except (OSError, IOError) as e:
        print(
            f"  ERROR: cannot write {output_path}: {e}",
            file=sys.stderr,
        )
        try:
            tmp_path.unlink()
        except OSError:
            pass
        sys.exit(1)

    if not getattr(args, "quiet", False):
        if summary["count"] == 0:
            print("  Everything is up to date.")
        else:
            print(f"  {summary['count']} package(s) have updates available:")
            for p in packages:
                print(
                    f"    {p['name']:30s} {p['installed_version']:15s} "
                    f"→ {p['remote_version']}"
                )
            print(f"  Run `pkm upgrade --all` to install.")
        print(f"  Wrote {output_path}")


def cmd_cache(db, args):
    """pkm cache <action> — manage the /var/cache/pkm/packages/ archive cache.

    Subcommands:
      clean   Remove cached archives by policy (--keep-current default,
              --keep N, or --all).

    No subcommand: print usage hint.
    """
    if getattr(args, "cache_action", None) == "clean":
        return cmd_cache_clean(db, args)
    print("  Usage: pkm cache <action>")
    print("    clean   Remove cached archives by policy")
    return 0


def cmd_cache_clean(db, args):
    """pkm cache clean [--keep-current | --keep N | --all].

    Walks /var/cache/pkm/packages/, parses each archive filename into
    (name, version, release), groups by package name, and applies the
    selected policy. Default behavior (no flag) is --keep-current.

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
    """
    import re
    from .repo import REPO_PKG_CACHE

    if not REPO_PKG_CACHE.exists():
        print(f"  Cache directory {REPO_PKG_CACHE} does not exist; nothing to clean.")
        return 0

    archives = sorted(REPO_PKG_CACHE.glob("*.igos.tar.gz"))
    if not archives:
        print("  Cache is empty; nothing to clean.")
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
        print(
            f"  WARN: {len(unmatched)} file(s) in {REPO_PKG_CACHE} did not "
            f"match the <name>-<version>-<release>.igos.tar.gz shape; "
            f"leaving them untouched.",
            file=sys.stderr,
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
            print("  ERROR: --keep N must be >= 0", file=sys.stderr)
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
        print("  Nothing to clean (cache state matches policy).")
        return 0

    total_bytes = sum(p.stat().st_size for p in to_remove)
    print(
        f"  Removing {len(to_remove)} archive(s) "
        f"({total_bytes / (1024 * 1024):.1f} MiB):"
    )
    for p in sorted(to_remove):
        print(f"    {p.name}")
    any_failed = False
    for p in to_remove:
        try:
            p.unlink()
        except OSError as e:
            print(f"  WARN: failed to remove {p}: {e}", file=sys.stderr)
            any_failed = True
    return 1 if any_failed else 0


if __name__ == "__main__":
    main()
