# PKM: The InterGenOS Package Manager

This document details `pkm` (Package Manager), the component responsible for managing the state of the installed operating system. `pkm` handles installation, removal, querying, and integrity verification of pre-compiled binary archives (`.igos.tar.gz`). 

Unlike `igos-build`, which constructs packages from source code in an isolated environment, `pkm` operates on the live filesystem. 

## Architectural Design

`pkm` utilizes a hybrid data model:
1.  **SQLite Database**: The primary source of truth for fast queries and transaction management. Located at `/var/lib/igos/pkm.db`.
2.  **Text Manifests**: Human-readable text files generated alongside database records for inspection and transparency. Located at `/var/lib/igos/packages/`.

This duality ensures high performance without sacrificing auditability. 

## SQLite Database Schema

The SQLite schema (`pkm/database.py`) consists of several interconnected tables:

*   **`installed`**: Tracks the metadata of active and superseded packages.
    *   Fields: `name` (UNIQUE), `version`, `tier`, `description`, `install_date`, `superseded_by`, `superseded_at`.
*   **`files`**: Maps every deployed file to its owning package.
    *   Fields: `package_id` (foreign key), `path`, `is_dir`, `is_config`, `checksum` (SHA-256).
    *   Indexed by `path` for rapid owner lookups.
*   **`depends`**: Tracks package relationships (runtime dependencies).
*   **`available`**: A cache of remote repository metadata (synced via `pkm update`).
*   **`history`**: An append-only transaction log of all operations (installs, removals, supersedes) for audit trails.
*   **`config_files`**: Specialized tracking for files in `/etc/` to manage user modifications.

## Core Operations

### Installation (`pkm/installer.py`)

The installation pipeline follows a strict sequence:
1.  **Staging Extraction**: The archive is extracted to a temporary staging directory using hardened `tar` flags (`--no-same-owner`, `--no-same-permissions`).
2.  **Manifest Reading**: The staged archive's manifest is parsed to extract the file list, `SUPERSEDES` declarations, and embedded file hashes.
3.  **Invariant Checks**: `pkm` checks for directory collisions (e.g., preventing a package from replacing the `/lib` symlink with a directory) and verifies that predecessor packages declared in `SUPERSEDES` exist and are correctly ordered in the install queue.
4.  **Filesystem Deploy (Gate 3)**: The archive is deployed to the root filesystem using `tar --no-overwrite-dir --keep-directory-symlink`. Hardened flags drop setuid/setgid bits, which are then explicitly restored by parsing the `tarfile` header metadata.
5.  **Atomic DB Transaction**: The database is updated inside a single `BEGIN/COMMIT` block:
    *   An `installed` record is created.
    *   `files` are registered with their content hashes.
    *   If the package declares a `SUPERSEDES` dependency, overlapping file ownership is transferred, and the predecessor is marked `superseded_by`.
    *   A `history` log entry is appended.
6.  **Text Manifest Generation**: The text manifest (`/var/lib/igos/packages/{name}-{version}`) is generated reflecting the final database state.

### Removal (`pkm/remover.py`)

Removals process files cautiously:
1.  The package's file list is retrieved from the database.
2.  Configuration files (`/etc/`) are skipped to preserve user data unless the `--force` flag is supplied.
3.  Regular files are unlinked. If a file's hash on disk differs from the database hash (indicating it was modified post-install), `pkm` emits a warning but proceeds with removal.
4.  Directories are removed only if they become completely empty.
5.  The `installed`, `files`, and `depends` records are deleted.

### Upgrades and Supersede Semantics

`pkm` handles package upgrades via a powerful "supersede" model. When package `B` supersedes package `A`:
1.  Files that exist in *both* `A` and `B` are overwritten on disk by `B`. In the database, the ownership of these files is explicitly transferred from `A` to `B`, along with updated SHA-256 hashes.
2.  Files that existed in `A` but are *not* present in `B` are left alone. They remain owned by `A`'s historical record.
3.  Package `A`'s `installed` record receives a `superseded_by` timestamp pointing to `B`.

This allows for fine-grained package splits (e.g., `util-linux` splitting into `util-linux-core` and `util-linux-extra`) without orphaning files or breaking dependencies.

## Integrity and Security

### Content-Hash Verification
The `pkm verify` command recalculates the SHA-256 hash of every installed file on disk and compares it against the expected hash stored in the `files` table. This provides a robust mechanism to detect accidental corruption or unauthorized modification of binaries. 

### Archive Verification
Before installation, the `cli.py` layer provides a `--archive-trust` flag. In `strict` or `repo-only` modes, the incoming `.igos.tar.gz` archive's SHA-256 hash is computed and checked against the trusted `available` index synced from the central repository.

### Content vs. Config
`pkm` explicitly tracks `is_config` state for files residing in `/etc/`. The `config_files` table stores the original hash of the file as deployed. If an upgrade ships a new config file, `pkm` will attempt a safe merge or leave a `.new` file if it detects the user has modified the active configuration on disk.

## Relationship to `igos-build`

`igos-build` is the factory; `pkm` is the consumer.
1.  `igos-build` compiles source code and produces a `.igos.tar.gz` archive.
2.  During the tracking phase, `igos-build` generates the initial file list and hashes.
3.  If `igos-build` uses `direct_install` (deploying straight to the chroot instead of `DESTDIR`), it calls `pkm` internally to register the resulting files into the SQLite database.
4.  For end-users, `pkm` is the CLI tool used to download and install those `.igos.tar.gz` archives from the network repository.
