# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Entry point for igos-build: python -m igos-build

Usage:
    python -m igos-build                            Parse templates, show build order
    python -m igos-build --dry-run                  Show what commands would run
    python -m igos-build --build                    Build packages (tracked: deploy + verify + register)
    python -m igos-build --build --stage-only       Build into the staging system root only —
                                                    NO deploy, NO archive, NO registration
    python -m igos-build --build --skip-built       Skip packages with existing manifests
    python -m igos-build --only <name>              Build only one package
    python -m igos-build --tier desktop             Build only one tier
    python -m igos-build --sources-dir /sources     Override sources directory

Tracked deployment is the DEFAULT for --build (--tracked is accepted as a
no-op for existing callers). The old opt-in default silently pointed
DESTDIR at build/system, skipped deploy/verify/registration, and exited 0
— the documented single-package commands omitted --tracked while claiming
a chroot install, so a "successful" build changed nothing in the chroot.
"""

import os
import sys
from pathlib import Path

from . import __version__
from .parser import load_all_packages, TemplateError
from .graph import build_graph, CycleError, MissingDependencyError
from .styles import get_style
from .builder import BuildExecutor

# Forensic-trace shim — when IGOS_BUILD_DEBUG_VERBOSE=1 (or the legacy
# IGOS_JSON_LOG=1, or the --debug-verbose / --json-log CLI flag), open the
# canonical orchestrator-style sink and emit the build-domain narration
# events from this top-level entry point. Defensive import so a packaging
# gap can never break the CLI.
try:
    from . import _trace
    _TRACE_AVAILABLE = True
except ImportError:
    _trace = None
    _TRACE_AVAILABLE = False


# Default paths (relative to project root)
PROJECT_ROOT = Path(__file__).parent.parent
PACKAGES_DIR = PROJECT_ROOT / "packages"
WORK_DIR = PROJECT_ROOT / "build" / "work"
LOG_DIR = PROJECT_ROOT / "build" / "logs"
SOURCES_DIR = PROJECT_ROOT / "build" / "sources"
PATCHES_DIR = PROJECT_ROOT / "build" / "patches"  # overridden to sources_dir below
SYSTEM_ROOT = PROJECT_ROOT / "build" / "system"


def main():
    args = sys.argv[1:]
    verbose = "--verbose" in args or "-v" in args
    dry_run = "--dry-run" in args
    do_build = "--build" in args
    # Tracked deployment is the default; --stage-only is the explicit,
    # clearly-named opt-out (build into the staging system root without
    # deploy/archive/verify/registration). --tracked remains accepted so
    # every existing driver invocation is unchanged.
    stage_only = "--stage-only" in args
    if stage_only and "--tracked" in args:
        print("error: --stage-only and --tracked are mutually exclusive")
        sys.exit(2)
    tracked = not stage_only
    # Tracked deployment writes to three ABSOLUTE paths — /var/lib/igos/packages,
    # /var/lib/igos/archives and /tmp/igos-staging (builder.py, BuildExecutor).
    # Inside the build chroot those are the chroot's own directories, which is
    # the design. Typed on a live installed machine they are the RUNNING
    # system's package database while the files deploy into build/system: the
    # completion-marker step unlinks the installed record the package manager
    # wrote, and a successful tracked build overwrites that record with a
    # manifest for files that are not installed. scripts/chroot-enter.sh is the
    # single entry point into the chroot and sets IGOS_BUILD_IN_CHROOT=1 in its
    # env -i list; without it, a tracked build refuses here, before any object
    # is constructed and before any directory is made.
    if do_build and not stage_only and os.environ.get("IGOS_BUILD_IN_CHROOT") != "1":
        print(
            "error: tracked deployment refuses to run outside the build chroot.\n"
            "  A tracked --build writes to /var/lib/igos/packages,\n"
            "  /var/lib/igos/archives and /tmp/igos-staging. Outside the chroot\n"
            "  those are the running system's own package database and staging\n"
            "  directories, and this build's files are not what is installed\n"
            "  there: the run would delete and overwrite installed package\n"
            "  records.\n"
            "  Tracked deployment is only valid inside the build chroot entered\n"
            "  through scripts/chroot-enter.sh, which sets IGOS_BUILD_IN_CHROOT=1.\n"
            "  On a live machine use --stage-only, which builds into the staging\n"
            "  system root and touches none of those paths.",
            file=sys.stderr,
        )
        sys.exit(2)
    skip_built = "--skip-built" in args
    # Verbose forensic mode: --debug-verbose (preferred) OR --json-log (legacy
    # alias, preserved for one release) OR IGOS_BUILD_DEBUG_VERBOSE=1 (the
    # canonical env-var, matches the bash side scripts/lib/trace.sh) OR
    # IGOS_JSON_LOG=1 (legacy env-var, preserved for one release). Any of
    # them opts in. The aliases are advertised as deprecated in the operator
    # runbook; both forms keep working for the v1.0 release.
    json_log = (
        "--debug-verbose" in args
        or "--json-log" in args
        or os.environ.get("IGOS_BUILD_DEBUG_VERBOSE", "").strip() in ("1", "true", "yes", "on")
        or os.environ.get("IGOS_JSON_LOG", "").strip() == "1"
    )
    # When the CLI sets verbose but the env-var isn't, propagate so the
    # _trace module's IGOS_BUILD_DEBUG_VERBOSE gate (which is read at module
    # import time inside the shared module) sees a consistent state for any
    # child processes (subprocess.run inside builder.py, etc.).
    if json_log:
        os.environ.setdefault("IGOS_BUILD_DEBUG_VERBOSE", "1")
    only_pkg = None
    tier_filter = None
    sources_dir = SOURCES_DIR
    if "--only" in args:
        idx = args.index("--only")
        if idx + 1 < len(args):
            only_pkg = args[idx + 1]
    if "--tier" in args:
        idx = args.index("--tier")
        tier_filter = []
        for a in args[idx+1:]:
            if a.startswith("--"):
                break
            tier_filter.append(a)
    if "--sources-dir" in args:
        idx = args.index("--sources-dir")
        if idx + 1 < len(args):
            sources_dir = Path(args[idx + 1])

    print(f"igos-build v{__version__}")
    print(f"Scanning: {PACKAGES_DIR}\n")

    # Open the canonical orchestrator-style sink when verbose. When
    # IGOS_TRACE_RUNID is inherited from a parent bash orchestrator, the
    # sink lands in the SAME `<startts>-<runid>` family as the bash trail;
    # standalone igos-build invocations get a fresh runid.
    if _TRACE_AVAILABLE:
        try:
            _trace.init_build_trace()
            _trace.trace_event(
                "build_start",
                runid=_trace.get_runid(),
                source="igos-build",
                argv=args,
                tier_filter=tier_filter,
                only_pkg=only_pkg,
                tracked=tracked,
                skip_built=skip_built,
            )
        except Exception:
            pass

    # --- Parse all templates ---
    try:
        all_packages = load_all_packages(PACKAGES_DIR)
    except TemplateError as e:
        print(f"error: template: {e}", file=sys.stderr)
        sys.exit(1)

    # When tier filtering, load ALL packages for the dependency graph
    # but only BUILD packages in the requested tier(s).
    # This ensures cross-tier dependencies are properly resolved.
    if tier_filter:
        packages = [p for p in all_packages if p.tier in tier_filter]
        print(f"Filtered to tier(s): {', '.join(tier_filter)}")
        print(f"  {len(packages)} packages to build (from {len(all_packages)} total)\n")
    else:
        packages = all_packages

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
    # Always use ALL packages for the graph so cross-tier deps are resolved
    print("==> Building dependency graph\n")

    try:
        graph = build_graph(all_packages, strict=True)
        order = graph.build_order()
        # Filter build order to only include requested tiers
        if tier_filter:
            order = [p for p in order if p.tier in tier_filter]
    except CycleError as e:
        print(f"error: dependency cycle: {e}", file=sys.stderr)
        sys.exit(1)
    except MissingDependencyError as e:
        print(f"error: missing dependency: {e}", file=sys.stderr)
        sys.exit(1)

    # Filter to single package if --only
    if only_pkg:
        order = [p for p in order if p.name == only_pkg]
        if not order:
            print(f"error: no package named '{only_pkg}'", file=sys.stderr)
            sys.exit(1)

    graph.print_order(order)

    # --- Show build phases (dry run or verbose) ---
    if dry_run or verbose:
        print("\n==> Build phases (dry run)\n")

        for pkg in order:
            style = get_style(pkg.build_style)
            phases = style.all_phases(pkg)

            print(f"  -> {pkg.name} {pkg.version} ({pkg.build_style})")
            for phase in phases:
                if not phase.commands:
                    continue
                print(f"     [{phase.name}]")
                for cmd in phase.commands:
                    for line in cmd.split("\n"):
                        print(f"       $ {line}")
            print()

    # --- Execute build ---
    if do_build:
        print("\n==> Executing build\n")

        executor = BuildExecutor(
            work_dir=WORK_DIR,
            log_dir=LOG_DIR,
            sources_dir=sources_dir,
            patches_dir=sources_dir,  # patches are co-located with sources in /sources/
            system_root=SYSTEM_ROOT,
            tracked=tracked,
            skip_built=skip_built,
            json_log=json_log,
        )

        success = executor.build_all(order, halt_on_failure=True)
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event("build_end",
                                   runid=_trace.get_runid(),
                                   success=success, source="igos-build")
                _trace.close_trace()
            except Exception:
                pass
        sys.exit(0 if success else 1)

    if not do_build:
        print(f"\nAll {len(order)} templates validated. Build order resolved. No cycles.")
        if not dry_run:
            print("\nRun with --build to execute, or --dry-run to preview commands.")


if __name__ == "__main__":
    main()
