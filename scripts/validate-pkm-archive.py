#!/usr/bin/env python3
"""pkm Archive Validator — Detect silent build failures from archive shapes.

Walks /var/lib/igos/archives/ *.igos.tar.gz files and companion pkm
manifests at /var/lib/igos/packages/<name>-<version>. Applies heuristic
checks against package.yml to flag archives that might represent a
silent build failure (e.g., apparmor-3.1.7 where only profile files
landed but libapparmor.so was never compiled).

Usage:
    python3 scripts/validate-pkm-archive.py
    python3 scripts/validate-pkm-archive.py --archives-dir /path/
    python3 scripts/validate-pkm-archive.py --config custom.yaml

Exit codes: 0 = all pass, 1 = suspect archives found, 2 = invalid config.
"""

import argparse
import json
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ARCHIVES_DIR = "/var/lib/igos/archives"
DEFAULT_MANIFEST_DIR = "/var/lib/igos/packages"
DEFAULT_SIZE_MIN_BYTES = 200_000  # 200KB — apparmor's 188KB failed build was below this

# Directories that signal a real payload exists
PAYLOAD_DIRS = ["usr/lib", "usr/lib64", "usr/bin", "usr/sbin"]

# Build styles that imply a compiled payload (binary or library)
COMPILED_STYLES = {"autotools", "meson", "cmake"}

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def load_config(config_path=None):
    cfg = {"min_size_bytes": DEFAULT_SIZE_MIN_BYTES, "payload_dirs": PAYLOAD_DIRS}
    if config_path and Path(config_path).exists():
        with open(config_path) as f:
            import yaml
            user = yaml.safe_load(f)
            if "min_size_bytes" in user:
                cfg["min_size_bytes"] = user["min_size_bytes"]
            if "payload_dirs" in user:
                cfg["payload_dirs"] = user["payload_dirs"]
    return cfg


def get_build_style(name):
    for tier_dir in sorted(Path("packages").iterdir()):
        if not tier_dir.is_dir():
            continue
        pkg_yml = tier_dir / name / "package.yml"
        if pkg_yml.exists():
            with open(pkg_yml) as f:
                import yaml
                data = yaml.safe_load(f)
                return data.get("build_style", "")
    return ""


def has_real_payload(tar, payload_dirs):
    """Check if archive has real files in at least one payload directory.

    Real file = not a directory, not a symlink targeting something that
    doesn't exist inside the archive.
    """
    members = tar.getmembers()
    for pd in payload_dirs:
        for m in members:
            if m.name.startswith(pd + "/") and m.isfile():
                return True
    return False


def validate_archive(archive_path, cfg):
    """Return None if pass, or a dict describing the failure."""
    issues = []

    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            archive_size = archive_path.stat().st_size
            name = archive_path.stem.split(".igos")[0]  # e.g., "apparmor-3.1.7"
            pkg_name = name.rsplit("-", 1)[0]  # e.g., "apparmor"

            # Check 1: size sanity
            build_style = get_build_style(pkg_name)
            if build_style in COMPILED_STYLES and archive_size < cfg["min_size_bytes"]:
                issues.append(f"size={archive_size}B < min={cfg['min_size_bytes']}B (build_style={build_style})")

            # Check 2: payload directories
            if not has_real_payload(tar, cfg["payload_dirs"]):
                issues.append("no real files in payload dirs")

            if issues:
                return {"name": name, "pkg_name": pkg_name, "archive": str(archive_path),
                        "size": archive_size, "build_style": build_style, "issues": issues}
    except tarfile.ReadError as e:
        return {"name": archive_path.stem, "archive": str(archive_path),
                "size": archive_path.stat().st_size() if archive_path.exists() else 0,
                "issues": [f"corrupt archive: {e}"]}
    except Exception as e:
        return {"name": archive_path.stem, "archive": str(archive_path),
                "issues": [f"error: {e}"]}

    return None


def main():
    parser = argparse.ArgumentParser(description="pkm Archive Validator — detect silent build failures")
    parser.add_argument("--archives-dir", default=DEFAULT_ARCHIVES_DIR,
                        help=f"Directory of .igos.tar.gz files (default: {DEFAULT_ARCHIVES_DIR})")
    parser.add_argument("--config", help="YAML config for thresholds")
    parser.add_argument("-o", "--output-dir", default="build",
                        help="Output directory for reports (default: build/)")
    args = parser.parse_args()

    archives_dir = Path(args.archives_dir)
    if not archives_dir.is_dir():
        print(f"ERROR: {archives_dir} not found", file=sys.stderr)
        sys.exit(2)

    cfg = load_config(args.config)
    archives = sorted(archives_dir.glob("*.igos.tar.gz"))

    if not archives:
        print(f"WARNING: no .igos.tar.gz files in {archives_dir}", file=sys.stderr)
        sys.exit(0)

    print(f"Validating {len(archives)} archives...", file=sys.stderr)

    suspects = []
    passed = 0
    errors = 0

    for arc in archives:
        result = validate_archive(arc, cfg)
        if result is None:
            passed += 1
            print(f"  OK: {arc.name}", file=sys.stderr)
        elif result:
            suspects.append(result)
            print(f"  SUSPECT: {arc.name} — {'; '.join(result['issues'])}", file=sys.stderr)

    # Write reports
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = output_dir / f"pkm-archive-validation-{ts}"

    # TSV
    with open(f"{base}.tsv", "w") as f:
        f.write("name\tpkg_name\tsize\tbuild_style\tissues\n")
        for s in suspects:
            f.write(f"{s['name']}\t{s['pkg_name']}\t{s['size']}\t{s.get('build_style','')}\t{'|'.join(s['issues'])}\n")

    # JSON
    with open(f"{base}.json", "w") as f:
        json.dump({
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "archives_dir": str(archives_dir),
            "total": len(archives),
            "passed": passed,
            "suspects": len(suspects),
            "errors": errors,
            "findings": suspects,
        }, f, indent=2)

    print(f"\n{passed}/{len(archives)} passed, {len(suspects)} suspect, {errors} errors", file=sys.stderr)
    print(f"Reports: {base}.tsv + {base}.json", file=sys.stderr)

    # Exit code for CI gate
    if suspects:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
