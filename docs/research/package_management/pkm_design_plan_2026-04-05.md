# pkm — InterGenOS Package Manager

## Context

InterGenOS has a working package tracking system (manifests, archives, deploy) but no user-facing package manager. Users can't install, remove, query, or upgrade packages on a running system. The build system creates `.igos.tar.gz` archives and text manifests, but these are build-time artifacts with no runtime management layer.

`pkm` fills this gap — a Python CLI tool that reads like natural language, uses SQLite for fast queries while keeping human-readable manifests, and serves as the foundation for the future installer, CI/CD pipeline, and remote repository system.

## Design Principles

1. **Natural language CLI** — `pkm list installed`, `pkm search audio`, not cryptic flags
2. **Hybrid database** — SQLite primary (speed, queries, atomic ops) + text manifests generated on demand (transparency, grep-ability)
3. **Archive-first install** — pre-built `.igos.tar.gz` by default, source build on request
4. **Safety** — refuse to break dependency chains, protect config files, verify integrity
5. **Prime Directive** — the user understands what `pkm` is doing at every step

## CLI Interface

```bash
# Repository metadata
pkm update                          # Refresh available package metadata
pkm upgrade                         # Upgrade all packages with newer versions
pkm upgrade --dry-run               # Show what would change

# Package operations
pkm install <pkg> [pkg...]          # Install from archive (or download)
pkm remove <pkg>                    # Remove, checking reverse deps
pkm remove <pkg> --force            # Remove even if others depend on it
pkm build <pkg>                     # Build from source (even if archive exists)
pkm build <pkg> --custom            # Build from source with interactive configure
pkm reinstall <pkg>                 # Reinstall (from archive or rebuild)

# Queries
pkm list installed                  # All installed packages
pkm list installed --tier desktop   # Filter by tier
pkm list available                  # Available but not installed
pkm list upgradable                 # Packages with newer versions
pkm search <term>                   # Search by name/description
pkm info <pkg>                      # Full package details
pkm depends <pkg>                   # Show dependency tree
pkm depends <pkg> --reverse         # What depends on this package?
pkm provides <file>                 # Which package owns this file?
pkm files <pkg>                     # List files in a package
pkm verify <pkg>                    # Check all files match manifest
pkm verify --all                    # Verify entire system

# History
pkm history                         # Show install/remove/upgrade log
pkm history <pkg>                   # History for one package
```

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  pkm CLI                         │
│  (Python — argument parsing, output formatting)  │
└──────────────┬──────────────────────┬────────────┘
               │                      │
    ┌──────────▼──────────┐  ┌───────▼────────┐
    │   Package Database  │  │  Build Engine   │
    │   (SQLite)          │  │  (igos-build)   │
    │                     │  │                 │
    │  - installed pkgs   │  │  - source build │
    │  - available pkgs   │  │  - configure    │
    │  - dependencies     │  │  - DESTDIR      │
    │  - file ownership   │  │  - archive      │
    │  - history log      │  │                 │
    └──────────┬──────────┘  └───────┬─────────┘
               │                      │
    ┌──────────▼──────────────────────▼────────┐
    │          Package Operations               │
    │                                           │
    │  - archive extraction (install)           │
    │  - manifest generation                    │
    │  - file deployment (tar --keep-dir-sym)   │
    │  - file removal (from manifest)           │
    │  - config file protection                 │
    │  - dependency resolution                  │
    │  - integrity verification                 │
    └──────────────────┬───────────────────────┘
                       │
    ┌──────────────────▼───────────────────────┐
    │          Repository Layer                 │
    │  (Phase 2 — future)                      │
    │                                          │
    │  - remote metadata sync (pkm update)     │
    │  - archive download                      │
    │  - source mirror                         │
    │  - signature verification                │
    └──────────────────────────────────────────┘
```

## Database Schema

```sql
-- Installed packages (replaces text manifests as primary source of truth)
CREATE TABLE installed (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    release INTEGER DEFAULT 1,
    tier TEXT,                        -- core, base, desktop, extra
    description TEXT,
    license TEXT,
    build_date TEXT,                  -- ISO 8601
    install_date TEXT,                -- ISO 8601
    install_method TEXT,              -- 'archive', 'source', 'helper'
    archive_path TEXT,               -- path to .igos.tar.gz if exists
    uncompressed_size INTEGER,
    compressed_size INTEGER,
    UNIQUE(name)
);

-- Files owned by installed packages (enables fast `pkm provides`)
CREATE TABLE files (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES installed(id),
    path TEXT NOT NULL,               -- relative path (e.g., "usr/bin/bash")
    is_dir BOOLEAN DEFAULT FALSE,
    is_config BOOLEAN DEFAULT FALSE,  -- protected during remove/upgrade
    checksum TEXT,                    -- SHA256 of file content (not dirs)
    UNIQUE(path)
);

-- Dependencies (populated from package.yml + BLFS data)
CREATE TABLE depends (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES installed(id),
    dep_name TEXT NOT NULL,
    dep_type TEXT NOT NULL,           -- 'build', 'runtime', 'optional'
    UNIQUE(package_id, dep_name, dep_type)
);

-- Available packages (populated by pkm update)
CREATE TABLE available (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    release INTEGER DEFAULT 1,
    tier TEXT,
    description TEXT,
    archive_url TEXT,                 -- remote archive location (Phase 2)
    source_url TEXT,
    checksum TEXT,
    UNIQUE(name)
);

-- Operation history
CREATE TABLE history (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,          -- ISO 8601
    operation TEXT NOT NULL,          -- 'install', 'remove', 'upgrade', 'build'
    package_name TEXT NOT NULL,
    old_version TEXT,
    new_version TEXT,
    method TEXT,                      -- 'archive', 'source', 'helper'
    success BOOLEAN
);

-- Config files to protect during upgrades
CREATE TABLE config_files (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    package_id INTEGER REFERENCES installed(id),
    original_checksum TEXT            -- checksum at install time
);
```

## File Layout

```
/usr/bin/pkm                          # CLI entry point
/usr/lib/pkm/                         # Python package
    __init__.py
    cli.py                            # Argument parsing, output
    database.py                       # SQLite operations
    installer.py                      # Archive extraction, deployment
    builder.py                        # Source build (wraps igos-build)
    resolver.py                       # Dependency resolution
    verifier.py                       # Integrity checking
    remover.py                        # Safe package removal
    repository.py                     # Remote repo sync (Phase 2)
/var/lib/igos/
    pkm.db                            # SQLite database
    packages/                         # Text manifests (generated from DB)
    archives/                         # Pre-built .igos.tar.gz packages
/var/log/igos/
    pkm.log                           # Operation log
/etc/pkm/
    pkm.conf                          # Configuration
    repos.conf                        # Repository URLs (Phase 2)
```

## Implementation Phases

### Phase 1: Core Operations (implement now)

The minimum viable `pkm` that works on a running InterGenOS system.

**Files to create:**
- `/usr/lib/pkm/` — Python package (6 modules ~800 lines total)
- `/usr/bin/pkm` — CLI wrapper
- `/etc/pkm/pkm.conf` — Configuration

**Capabilities:**
- `pkm install <pkg>` — install from local archive
- `pkm remove <pkg>` — remove with dependency checking
- `pkm list installed` / `pkm list available`
- `pkm info <pkg>` / `pkm search <term>`
- `pkm files <pkg>` / `pkm provides <file>`
- `pkm verify <pkg>` / `pkm verify --all`
- `pkm depends <pkg>` / `pkm depends --reverse <pkg>`
- `pkm history`

**Migration:** On first run, `pkm` imports existing text manifests from
`/var/lib/igos/packages/` into SQLite. Text manifests continue to be
generated alongside SQLite for transparency.

### Phase 2: Build Integration

- `pkm build <pkg>` — invoke igos-build for a single package
- `pkm build <pkg> --custom` — interactive configure flag editing
- `pkm upgrade` — compare installed vs available, rebuild from source

### Phase 3: Remote Repository

- `pkm update` — download package index from remote server
- `pkm install <pkg>` — download archive from repo if not local
- Repository server on VPS (intergenstudios.com)
- Signed package metadata

### Phase 4: Installer Integration

- `pkm` as the engine behind the InterGenOS installer
- `pkm install --root /mnt/target <group>` — install to a target filesystem
- Package groups: `core`, `desktop`, `extra`

## Reusable Infrastructure

| Existing Code | Location | Reuse In |
|---------------|----------|----------|
| Manifest format | `pkg-functions.sh:pkg_manifest()` | `installer.py` — generate text manifests |
| Archive extraction | `pkg-functions.sh:pkg_deploy()` | `installer.py` — tar deploy logic |
| Safety checks | `builder.py:pkg_deploy()` | `installer.py` — symlink collision detection |
| Package dataclass | `igos-build/parser.py:Package` | `database.py` — populate available table |
| BLFS database | `build/blfs-packages.db` | `database.py` — seed dependency data |
| Dependency graph | `igos-build/graph.py` | `resolver.py` — reuse graph algorithm |
| Build executor | `igos-build/builder.py` | `builder.py` — wrap for `pkm build` |

## Config File Protection

During `pkm remove` and `pkm upgrade`:
- Files in `/etc/` are treated as config files by default
- If the user modified a config file (checksum differs from install-time),
  the old file is preserved as `<file>.pkm-old` and the new one installed
  as `<file>.pkm-new`
- `pkm` reports: "Configuration file /etc/foo.conf has been modified.
  Keeping your version. New default saved as /etc/foo.conf.pkm-new."

## Verification

1. **Install test:** `pkm install bash` from archive, verify files deployed
2. **Query test:** `pkm info bash`, `pkm files bash`, `pkm provides /usr/bin/bash`
3. **Dependency test:** `pkm remove zlib` should refuse (many packages depend on it)
4. **Verify test:** `pkm verify bash` should report all files present
5. **History test:** `pkm history` shows install operation
6. **Migration test:** Import existing manifests, verify counts match
7. **Build test:** `pkm build nano` rebuilds from source, updates manifest
