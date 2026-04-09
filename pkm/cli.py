"""pkm CLI — Natural-language command interface for InterGenOS package management."""

import argparse
import sys

from . import __version__
from .database import PackageDB
from .installer import PackageInstaller
from .remover import PackageRemover
from .verifier import PackageVerifier
from .repo import RepoManager


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

    # -- install-helper --
    p_helper = sub.add_parser("install-helper", help="Install proprietary software via download helper")
    p_helper.add_argument("package", help="Package to install (e.g., chrome, vscode, claude-code)")

    # -- remove --
    p_remove = sub.add_parser("remove", help="Remove a package")
    p_remove.add_argument("package")
    p_remove.add_argument("--force", action="store_true", help="Remove even if others depend on it")

    # -- list --
    p_list = sub.add_parser("list", help="List packages")
    p_list.add_argument("what", choices=["installed", "available", "upgradable"], nargs="?", default="installed")
    p_list.add_argument("--tier", help="Filter by tier")

    # -- update --
    sub.add_parser("update", help="Sync package index from repositories")

    # -- upgrade --
    p_upgrade = sub.add_parser("upgrade", help="Upgrade installed packages")
    p_upgrade.add_argument("packages", nargs="*", metavar="package", help="Specific packages (default: all)")

    # -- search --
    p_search = sub.add_parser("search", help="Search packages")
    p_search.add_argument("term")

    # -- info --
    p_info = sub.add_parser("info", help="Show package details")
    p_info.add_argument("package")

    # -- files --
    p_files = sub.add_parser("files", help="List files in a package")
    p_files.add_argument("package")

    # -- provides --
    p_provides = sub.add_parser("provides", help="Find which package owns a file")
    p_provides.add_argument("file")

    # -- verify --
    p_verify = sub.add_parser("verify", help="Verify package integrity")
    p_verify.add_argument("package", nargs="?")
    p_verify.add_argument("--all", action="store_true", dest="verify_all")

    # -- depends --
    p_depends = sub.add_parser("depends", help="Show dependencies")
    p_depends.add_argument("package")
    p_depends.add_argument("--reverse", action="store_true", help="Show reverse dependencies")

    # -- history --
    p_history = sub.add_parser("history", help="Show operation history")
    p_history.add_argument("package", nargs="?")

    # -- import --
    p_import = sub.add_parser("import", help="Import existing text manifests into database")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    db = PackageDB(args.db)

    try:
        if args.command == "install":
            cmd_install(db, args)
        elif args.command == "install-helper":
            cmd_install_helper(db, args)
        elif args.command == "remove":
            cmd_remove(db, args)
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
    finally:
        db.close()


# ------------------------------------------------------------------
# Command implementations
# ------------------------------------------------------------------

def cmd_install(db, args):
    installer = PackageInstaller(db)
    repo = RepoManager()

    for pkg_name in args.packages:
        archive = args.archive if len(args.packages) == 1 else None

        # Try local archive first
        ok, msg = installer.install(pkg_name, archive_path=archive)
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

                inst_ok, inst_msg = installer.install(dep_name, archive_path=dl_result)
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
    repo = RepoManager()
    installer = PackageInstaller(db)

    # Compare installed versions against repo
    installed = db.list_installed()
    upgradable = []

    for pkg in installed:
        remote = repo.get_package(pkg["name"])
        if remote and remote["version"] != pkg["version"]:
            upgradable.append((pkg, remote))

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
        repo = RepoManager()
        installed = db.list_installed()
        count = 0
        for pkg in installed:
            remote = repo.get_package(pkg["name"])
            if remote and remote["version"] != pkg["version"]:
                print(f"    {pkg['name']:30s} {pkg['version']:15s} → {remote['version']}")
                count += 1
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
    verifier = PackageVerifier(db)

    if args.verify_all or not args.package:
        if not args.verify_all and not args.package:
            print("  Usage: pkm verify <package> or pkm verify --all")
            return
        results = verifier.verify_all()
        ok_count = 0
        problem_count = 0
        for name, version, result in results:
            if result["missing"] or result["modified"]:
                problem_count += 1
                print(f"  PROBLEM: {name} {version} — {len(result['missing'])} missing, {len(result['modified'])} modified")
            else:
                ok_count += 1
        print(f"\n  Verified: {ok_count} OK, {problem_count} with issues")
    else:
        result = verifier.verify(args.package)
        if result is None:
            print(f"  Package '{args.package}' is not installed")
            return
        if not result["missing"] and not result["modified"]:
            print(f"  {args.package}: OK ({result['total']} files verified)")
        else:
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


if __name__ == "__main__":
    main()
