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
    p_upgrade.add_argument("packages", nargs="*", metavar="package", help="Specific packages (default: all)")
    p_upgrade.add_argument(
        "--allow-downgrade", action="store_true",
        help="Treat any version mismatch as upgradable, including repo-older-than-installed "
             "(used to roll back after a bad release).",
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

            for dep_name in deps:
                dl_ok, dl_result = repo.download_package(dep_name)
                if not dl_ok:
                    print(f"  ERROR: {dl_result}", file=sys.stderr)
                    sys.exit(1)

                # L-021: extract expected sha256 from repo index for the
                # installer-side TOCTOU re-verification gate.
                dep_pkg = repo.get_package(dep_name)
                dep_sha = dep_pkg.get("sha256") if dep_pkg else None
                inst_ok, inst_msg = installer.install(
                    dep_name, archive_path=dl_result,
                    expected_sha256=dep_sha,
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

    repo = RepoManager()
    installer = PackageInstaller(db)
    allow_downgrade = getattr(args, "allow_downgrade", False)

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

    if args.packages:
        # Filter to requested packages
        names = set(args.packages)
        upgradable = [(i, r) for i, r in upgradable if i["name"] in names]

    if not upgradable:
        print("  Everything is up to date.")
        return

    print(f"  {len(upgradable)} package(s) to upgrade:")
    for installed_pkg, remote_pkg in upgradable:
        print(f"    {installed_pkg['name']}: {installed_pkg['version']} → {remote_pkg['version']}")

    for installed_pkg, remote_pkg in upgradable:
        dl_ok, dl_result = repo.download_package(remote_pkg["name"])
        if not dl_ok:
            print(f"  ERROR downloading {remote_pkg['name']}: {dl_result}", file=sys.stderr)
            continue

        # Remove old, install new
        from .remover import PackageRemover
        remover = PackageRemover(db)
        remover.remove(installed_pkg["name"], force=True)
        ok, msg = installer.install(remote_pkg["name"], archive_path=dl_result)
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


if __name__ == "__main__":
    main()
