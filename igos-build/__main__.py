"""Entry point for igos-build: python -m igos-build

Usage:
    python -m igos-build                            Parse templates, show build order
    python -m igos-build --dry-run                  Show what commands would run
    python -m igos-build --build                    Actually build packages
    python -m igos-build --build --tracked          Build with package tracking
    python -m igos-build --build --skip-built       Skip packages with existing manifests
    python -m igos-build --only <name>              Build only one package
    python -m igos-build --sources-dir /sources     Override sources directory
"""

import sys
from pathlib import Path

from .parser import load_all_packages, TemplateError
from .graph import build_graph, CycleError, MissingDependencyError
from .styles import get_style
from .builder import BuildExecutor


# Default paths (relative to project root)
PROJECT_ROOT = Path(__file__).parent.parent
PACKAGES_DIR = PROJECT_ROOT / "packages"
WORK_DIR = PROJECT_ROOT / "build" / "work"
LOG_DIR = PROJECT_ROOT / "build" / "logs"
SOURCES_DIR = PROJECT_ROOT / "build" / "sources"
PATCHES_DIR = PROJECT_ROOT / "build" / "patches"
SYSTEM_ROOT = PROJECT_ROOT / "build" / "system"


def main():
    args = sys.argv[1:]
    verbose = "--verbose" in args or "-v" in args
    dry_run = "--dry-run" in args
    do_build = "--build" in args
    tracked = "--tracked" in args
    skip_built = "--skip-built" in args
    only_pkg = None
    sources_dir = SOURCES_DIR
    if "--only" in args:
        idx = args.index("--only")
        if idx + 1 < len(args):
            only_pkg = args[idx + 1]
    if "--sources-dir" in args:
        idx = args.index("--sources-dir")
        if idx + 1 < len(args):
            sources_dir = Path(args[idx + 1])

    print("igos-build v0.1.0")
    print(f"Scanning: {PACKAGES_DIR}\n")

    # --- Parse all templates ---
    try:
        packages = load_all_packages(PACKAGES_DIR)
    except TemplateError as e:
        print(f"TEMPLATE ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Parsed {len(packages)} package template(s):\n")
    for pkg in packages:
        sources = ", ".join(s.url.split("/")[-1] for s in pkg.source)
        deps_count = (len(pkg.dependencies.build)
                      + len(pkg.dependencies.host)
                      + len(pkg.dependencies.runtime))
        flags_count = len(pkg.configure_flags)
        checks_count = len(pkg.validation)

        print(f"  [{pkg.tier}] {pkg.name} {pkg.version}-{pkg.release}")
        print(f"    style: {pkg.build_style}  |  deps: {deps_count}  |  flags: {flags_count}  |  checks: {checks_count}")
        print(f"    source: {sources}")
        if pkg.pass_number:
            print(f"    pass: {pkg.pass_number}  |  target: {pkg.target_triple}")
        print()

    # --- Build dependency graph ---
    print("=" * 60)
    print("Building dependency graph...\n")

    try:
        graph = build_graph(packages, strict=False)
        order = graph.build_order()
    except CycleError as e:
        print(f"CYCLE ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except MissingDependencyError as e:
        print(f"DEPENDENCY ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Filter to single package if --only
    if only_pkg:
        order = [p for p in order if p.name == only_pkg]
        if not order:
            print(f"ERROR: no package named '{only_pkg}'", file=sys.stderr)
            sys.exit(1)

    graph.print_order(order)

    # --- Show build phases (dry run or verbose) ---
    if dry_run or verbose:
        print("\n" + "=" * 60)
        print("Build phases (dry run):\n")

        for pkg in order:
            style = get_style(pkg.build_style)
            phases = style.all_phases(pkg)

            print(f"--- {pkg.name} {pkg.version} ({pkg.build_style}) ---")
            for phase in phases:
                if not phase.commands:
                    continue
                print(f"  [{phase.name}]")
                for cmd in phase.commands:
                    for line in cmd.split("\n"):
                        print(f"    $ {line}")
            print()

    # --- Execute build ---
    if do_build:
        print("\n" + "=" * 60)
        print("EXECUTING BUILD\n")

        executor = BuildExecutor(
            work_dir=WORK_DIR,
            log_dir=LOG_DIR,
            sources_dir=sources_dir,
            patches_dir=PATCHES_DIR,
            system_root=SYSTEM_ROOT,
            tracked=tracked,
            skip_built=skip_built,
        )

        success = executor.build_all(order, halt_on_failure=True)
        sys.exit(0 if success else 1)

    if not do_build:
        print(f"\nAll {len(order)} templates validated. Build order resolved. No cycles.")
        if not dry_run:
            print("\nRun with --build to execute, or --dry-run to preview commands.")


if __name__ == "__main__":
    main()
