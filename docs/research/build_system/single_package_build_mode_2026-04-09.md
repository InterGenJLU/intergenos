# Single-Package Build Mode — Design Proposal

**Date:** April 9, 2026  
**Status:** Proposal — awaiting owner review

## Problem

Currently, building a new package requires running the full tier build script (`chroot-build-extra.sh`), which:
1. Parses ALL package templates
2. Resolves the full dependency graph
3. Iterates through ALL packages in the tier (skipping already-built ones)

For adding 1-2 packages (e.g., user requests "package X"), this is overkill. We need a focused build mode.

## What Already Exists

The builder already has `--only <name>` which filters the build order to a single package. **But it doesn't resolve unbuilt dependencies** — it just builds the named package and fails if deps aren't installed.

## Proposed Solution: `--package <name> [name2 ...]`

A new flag that:

1. **Resolves the named package(s) and ALL their unbuilt dependencies**
2. **Builds them in correct dependency order** (just like a tier build, but scoped)
3. **Optionally creates a standalone package archive** (`--archive` flag)

### Usage Examples

```bash
# Build a single package + any missing deps
python3 igos-build.py --build --tracked --skip-built --package gimp

# Build two packages (resolves combined dep tree)
python3 igos-build.py --build --tracked --skip-built --package gimp inkscape

# Build + create distributable archive
python3 igos-build.py --build --tracked --skip-built --package gimp --archive

# Dry-run to see what would be built
python3 igos-build.py --dry-run --package gimp
```

### Implementation

**Changes to `__main__.py`:**

```python
# New argument parsing (after existing --only handling)
packages_to_build = None
if "--package" in args:
    idx = args.index("--package")
    packages_to_build = []
    for a in args[idx+1:]:
        if a.startswith("--"):
            break
        packages_to_build.append(a)

create_archive = "--archive" in args
```

**New graph method in `graph.py`:**

```python
def build_order_for(self, package_names: list[str], skip_built_fn=None) -> list[Package]:
    """Compute build order for specific packages + their unbuilt deps.
    
    Args:
        package_names: Names of packages to build.
        skip_built_fn: Optional callable(name) -> bool, returns True if 
                       the package is already built and can be skipped.
    
    Returns:
        Ordered list of packages to build (deps first, targets last).
    """
    needed = set()
    
    def collect_deps(name):
        if name in needed:
            return
        if name not in self.packages:
            raise MissingDependencyError("<requested>", name)
        # If already built and not a target, skip
        if skip_built_fn and skip_built_fn(name) and name not in package_names:
            return
        needed.add(name)
        for dep in self.depends_on.get(name, set()):
            collect_deps(dep)
    
    for name in package_names:
        collect_deps(name)
    
    # Get full build order, then filter to only needed packages
    full_order = self.build_order()
    return [p for p in full_order if p.name in needed]
```

**New `--archive` handling in `builder.py`:**

After a successful build with `--archive`, create a standalone package archive:

```python
def create_package_archive(self, pkg: Package) -> Path:
    """Create a distributable .pkg.tar.zst from the staged DESTDIR."""
    staging = self.pkg_staging / f"{pkg.name}-{pkg.version}"
    archive_name = f"{pkg.name}-{pkg.version}-{pkg.release}.pkg.tar.zst"
    archive_path = self.pkg_archives / archive_name
    
    # Include metadata
    metadata = {
        "name": pkg.name,
        "version": pkg.version,
        "release": pkg.release,
        "description": pkg.description,
        "dependencies": list(pkg.dependencies.runtime),
        "build_date": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    meta_file = staging / ".PKGINFO"
    with open(meta_file, "w") as f:
        for k, v in metadata.items():
            f.write(f"{k} = {v}\n")
    
    # Create archive
    subprocess.run(
        ["tar", "--zstd", "-cf", str(archive_path), "-C", str(staging), "."],
        check=True
    )
    return archive_path
```

### Wrapper Script (for convenience)

A simple `igos-build-package` wrapper for running inside the chroot:

```bash
#!/bin/bash
# Build one or more packages with dependency resolution
# Usage: igos-build-package <name> [name2 ...]
set -e
cd /mnt/intergenos
python3 igos-build.py --build --tracked --skip-built --package "$@" --sources-dir /sources
```

## Integration with pkm

This pairs naturally with the future `pkm build` workflow:
- `pkm install <package>` — install from pre-built archive
- `pkm build <package>` — build from template + install
- Under the hood, `pkm build` would call `igos-build.py --build --tracked --package <name>`

## Effort Estimate

~50-80 lines of new code across 3 files:
- `__main__.py`: ~15 lines (argument parsing + skip_built_fn)
- `graph.py`: ~20 lines (build_order_for method)
- `builder.py`: ~30 lines (archive creation, optional)

## Risk Assessment

Low risk — this adds a new code path but doesn't modify existing build logic. The `build_order_for` method produces a subset of the same `build_order()` output, so all existing build phases, tracking, and validation apply identically.
