# SQLite in Linux Package Managers — Comprehensive Survey

**Date:** April 5, 2026
**Context:** Evaluating SQLite as the database backend for InterGenOS package manager
**Verdict:** SQLite is a strong, defensible choice — industry momentum is clearly in its favor

---

## 1. Who Uses SQLite?

### RPM (Fedora, RHEL, SUSE, CentOS)
- **Status:** SQLite is now the default backend
- **When:** RPM 4.16 (2020) introduced SQLite backend; Fedora 33 (Oct 2020) made it default; BerkeleyDB support dropped in Fedora 34; RHEL 9 uses SQLite exclusively
- **Why they switched:**
  - BerkeleyDB 5.x was unmaintained upstream for years
  - BerkeleyDB 6.x changed to AGPL license, incompatible with RPM's GPL
  - Oracle's AGPL relicensing was widely seen as a poison pill to force commercial licensing
  - BerkeleyDB-based rpmdb was "notoriously unreliable" — not transactional, no inconsistency detection
  - Forcibly killed transactions caused database corruption with BDB; SQLite handles this gracefully
- **Migration:** Automatic conversion on first boot after upgrade via `rpmdb-rebuild` systemd service
- **Also available:** NDB (RPM-native database), used by openSUSE Tumbleweed. A custom homebrew format — functional but less scrutinized than SQLite
- **Community reaction:** Generally positive. Migration hiccups were real (Silverblue fresh installs didn't trigger conversion, container cross-version issues, osquery breakage) but these were transition pains, not SQLite problems. Nobody wants to go back to BerkeleyDB.

### Nix (NixOS)
- **Status:** Uses SQLite for the Nix store database
- **Location:** `/nix/var/nix/db/db.sqlite`
- **Schema:** Simple — `ValidPaths` table with store paths, content hashes, timestamps, derivation references; plus dependency relation table
- **Why:** Performance and filesystem consistency. The store is content-addressed (hash-based paths), and SQLite tracks what's valid and what depends on what
- **Known issues:**
  - Corruption reports are **not rare** — users report "database disk image is malformed" errors
  - Root causes: interrupted operations, disk-full conditions, mixing Nix versions (2.3 vs 2.5+ use different SQLite VFS/locking mechanisms)
  - Recovery is painful — often requires manual sqlite3 intervention or rebuilding from scratch
  - GitHub issue #378 ("Get rid of sqlite dependency") exists but is essentially closed — SQLite is too deeply embedded
  - GitHub issue #11500 requests atomic protections for db.sqlite
- **Verdict on Nix's experience:** SQLite itself isn't the problem — Nix's *usage* of SQLite (mixing VFS backends, inadequate crash recovery, poor version compatibility) is the problem. This is a cautionary tale about *how* you use SQLite, not *whether* you should.

### GNU Guix
- **Status:** Uses SQLite (inherited from Nix's store design)
- **Same architecture:** Content-addressed store with SQLite tracking valid paths and dependencies
- **Metadata output:** Exports in recutils format for querying, but the store DB is SQLite

---

## 2. Who Does NOT Use SQLite?

### dpkg/apt (Debian, Ubuntu)
- **Format:** Flat text files in `/var/lib/dpkg/`
- **Structure:** `status` file (all package state), `info/*.list` (file manifests per package), `info/*.conffiles`
- **Why flat files:** Historical (dpkg dates to 1994). Simplicity, human-readable, grep-able
- **Known limitations:** Linear scaling with package count. The dpkg TODO list has long included "replace database with more powerful format" — but it hasn't happened in 30+ years
- **Will they switch?** Unlikely soon. Debian values stability and backward compatibility above all. The flat-file format is deeply embedded in tooling, scripts, and expectations. Any change would be an enormous undertaking for minimal perceived benefit

### pacman/libalpm (Arch Linux)
- **Format:** Flat-file directory hierarchy under `/var/lib/pacman/`
- **Structure:** One directory per package containing `desc` (metadata), `files` (file list), `mtree` (checksums). Sync databases are gzipped tar archives
- **Why:** Simplicity, transparency, easy to inspect and debug. Aligns with Arch's KISS philosophy
- **Performance:** Fast enough for Arch's single-repo-version model (no multi-version resolution needed)
- **Will they switch?** No indication. The flat-file approach works well for Arch's use case

### XBPS (Void Linux)
- **Format:** Property lists (plist) via NetBSD's proplib library
- **Structure:** XML-based property lists with dictionaries, arrays, strings, booleans, integers
- **Why:** XBPS author (xtraeme) came from NetBSD and brought proplib along. Provides structured data without needing a database engine
- **Internal fork:** XBPS now ships its own copy of proplib with `xbps_` prefixed symbols — no external dependency
- **Performance:** Fast. XBPS is known for speed

### Portage (Gentoo)
- **Format:** VDB (Var DB) — flat-file directory hierarchy at `/var/db/pkg/`
- **Structure:** `<category>/<package-version>/` directories containing individual files for each metadata field (CONTENTS, DEPEND, RDEPEND, USE, etc.)
- **Why:** Extreme transparency. Every piece of metadata is a separate readable file. Matches Gentoo's "understand everything" philosophy
- **Performance:** Adequate for Gentoo's source-based model where build time dwarfs DB query time
- **Criticism:** Scattered files are slow to traverse. Community has discussed changing the format (Gentoo bug #321317) but no action taken

### Alpine apk
- **Format:** Custom text-based format (v2), with v3 introducing a new binary format (ADB)
- **Structure:** Installed database is a single file with structured text records. Index files are gzipped tar archives
- **v3 redesign (2025):** After 5 years of development, apk-tools v3 shipped in Alpine 3.23. New binary format (ADB) with better security, performance, extensibility, Zstd compression support. Still NOT SQLite — they designed their own format
- **Why not SQLite:** apk targets embedded/container use where minimal footprint matters. A custom format tuned for the exact access patterns is smaller and faster than a general-purpose database

### eopkg (Solus)
- **Format:** GDBM (GNU Database Manager) — `dbm.gnu` format
- **Structure:** `/var/lib/eopkg/info/files.db` with versioned format (currently v4)
- **Why:** PiSi heritage (forked from Pardus Linux). GDBM is lightweight and fast for key-value lookups

### opkg (OpenWrt, embedded)
- **Format:** Flat text files (similar to dpkg)
- **Structure:** ipk packages (similar to .deb), text-based status files in `/var/lib/opkg/`
- **Why:** Extreme resource constraints. OpenWrt runs on routers with 16MB flash and 64MB RAM

### swupd (Clear Linux, Intel)
- **Format:** File-level versioning with manifests, not a traditional package database
- **Structure:** Bundle-based system using RPM internally but managing updates at the file level
- **Why:** Designed for stateless computing and atomic updates, not traditional package management

---

## 3. The BerkeleyDB Cautionary Tale

This is critical context for any database choice:

- BerkeleyDB was the standard embedded database for decades
- Oracle acquired Sleepycat Software in 2006
- In 2013, Oracle relicensed BDB 6.x under AGPL — a copyleft license incompatible with most open source projects
- This was widely interpreted as a deliberate poison pill to force commercial licensing
- Result: Every project using BDB had to either:
  - Stay on unmaintained BDB 5.x (what RPM did for years)
  - Switch to something else (what RPM eventually did with SQLite)
  - Relicense their own code (impractical for most)

**Lesson for InterGenOS:** SQLite uses a public domain dedication (no license at all). It cannot be relicensed, bought, or poisoned. This is a genuine advantage over any licensed database.

---

## 4. Known Criticisms of SQLite for Package Management

### Corruption Risks
- **Real but manageable.** SQLite is battle-tested with ~2 billion deployments
- **Power failure:** SQLite handles this well with WAL (Write-Ahead Logging) mode. RPM's whole reason for switching was that BerkeleyDB handled this poorly
- **NFS/network filesystems:** SQLite's locking doesn't work correctly on NFS. Not relevant for local package management
- **Nix corruption reports:** Real, but caused by version mismatches and improper VFS usage, not SQLite itself
- **Mitigation:** Use WAL mode, run integrity checks, keep backups. SQLite provides `PRAGMA integrity_check` built-in

### Performance
- **Not a concern for package management.** Package databases are small (thousands of entries, not millions). SQLite handles this trivially
- **SQLite is often faster than flat files** for random-access queries (finding which package owns a file, checking dependencies)
- **Flat files win for sequential scanning** (list all packages), but the difference is negligible at package-management scale
- **RPM saw no performance regression** moving from BerkeleyDB to SQLite

### Complexity / Dependencies
- **SQLite is a single C file** (amalgamation build). No external dependencies. No server process
- **Public domain** — can be statically linked without any licensing concerns
- **Well-tested:** SQLite has ~150,000 test cases and claims 100% branch coverage
- **Stable format:** SQLite's file format has been stable since 2004 and is guaranteed stable through at least 2050

### Portability
- **Non-issue.** SQLite runs on everything from embedded systems to mainframes
- **Cross-platform file format.** A database created on ARM can be read on x86_64

### Queryability
- **Major advantage over flat files.** "Which package owns /usr/lib/libfoo.so?" is a fast indexed lookup instead of grepping through every manifest
- **Schema migrations are well-understood** — `PRAGMA user_version` for tracking, `ALTER TABLE` for changes
- **Tools exist** — `sqlite3` CLI ships everywhere, making debugging straightforward

---

## 5. What the Community Says

### In Favor of SQLite
- **RPM's migration is the strongest endorsement.** The largest package management ecosystem in enterprise Linux chose SQLite after years of evaluation. Red Hat, SUSE, Fedora, CentOS all use it now
- **Nix uses it** despite being designed by academics who could have chosen anything
- **SQLite's own documentation** advocates for it as an "application file format" — this is literally the use case it was designed for
- **D. Richard Hipp (SQLite author)** has stated that SQLite is a replacement for `fopen()`, not for PostgreSQL. Package management is exactly this kind of use case

### Skeptics and Alternatives
- **Arch/Void/Alpine/Gentoo** all chose flat files or custom formats. Their reasoning is consistent: simplicity, transparency, human-readability, KISS
- **Alpine deliberately designed a new binary format (ADB)** rather than adopting SQLite for v3, prioritizing minimal footprint
- **The flat-file camp argues:** "If you can `cat` your database and understand it, that's worth something. SQLite requires tooling"
- **Counter-argument:** `sqlite3` is ubiquitous tooling, and a well-designed schema IS documentation

### Consensus View
There is no controversy about choosing SQLite for a new package manager. It would be seen as a **sensible, modern choice**. The only criticism would come from extreme minimalists who prefer flat files for philosophical reasons — and even they wouldn't call it a bad technical decision.

---

## 6. Relevance to InterGenOS

### Current Design
Per existing research (`destdir_and_tracking_2026-04-01.md`), InterGenOS currently plans:
- Slackware-style one-file-per-package manifests in `/var/lib/igos/packages/`
- Plain text format with package name, version, sizes, description, file list
- Shell functions during build, `igos-pkg` tools after

### If SQLite Were Adopted
- File ownership queries would be O(log n) instead of O(n) — grep through all manifests vs. indexed lookup
- Dependency graph queries become SQL joins instead of parsing text files
- Atomic transactions protect against corruption during install/remove
- Schema can evolve with `ALTER TABLE` instead of changing text parsers
- Single file (`/var/lib/igos/db/packages.db`) instead of hundreds of manifest files
- `sqlite3` CLI provides instant debugging/inspection capability
- No external dependency — SQLite amalgamation is ~250KB of C code, public domain

### What Would Be Lost
- Human-readable manifest files (mitigated by `igos-pkg query` commands and `sqlite3` CLI)
- Simplicity of implementation during bootstrap (shell functions can't easily write SQLite)
- The aesthetic of `cat /var/lib/igos/packages/bash-5.2.37` showing you everything

### Hybrid Approach (Worth Considering)
Some package managers maintain both:
- SQLite as the primary database for queries and transactions
- Text manifests generated on demand for human inspection (`igos-pkg info <package>`)
- This gives you speed AND transparency

---

## 7. Summary Table

| Package Manager | Database Format | Year Introduced | Why |
|----------------|----------------|-----------------|-----|
| **RPM** | **SQLite** (was BerkeleyDB) | 2020 | BDB unmaintained, AGPL poisoned, corruption-prone |
| **Nix** | **SQLite** | ~2012 | Performance, consistency for content-addressed store |
| **Guix** | **SQLite** | ~2013 | Inherited from Nix architecture |
| dpkg/apt | Flat text files | 1994 | Historical, simplicity, stability |
| pacman | Flat file directories + tar archives | 2002 | KISS philosophy |
| XBPS | proplib (plist/XML) | ~2009 | NetBSD heritage |
| Portage | VDB (flat file directories) | ~2000 | Extreme transparency |
| apk | Custom text (v2) / custom binary ADB (v3) | 2008/2025 | Minimal footprint for containers/embedded |
| eopkg | GDBM | ~2005 | PiSi heritage, fast key-value |
| opkg | Flat text files | ~2008 | Extreme resource constraints |
| swupd | File-level manifests | ~2016 | Stateless computing model |

---

## Sources

- [Fedora Sqlite Rpmdb Change Proposal](https://fedoraproject.org/wiki/Changes/Sqlite_Rpmdb)
- [Phoronix: Fedora RPMDB to SQLite](https://www.phoronix.com/news/Fedora-RPMDB-To-SQLite)
- [RPM 4.16.0 Release Notes](http://rpm.org/wiki/Releases/4.16.0)
- [RHEL 9 SQLite RPM Database](https://access.redhat.com/articles/6891951)
- [Nix Store SQLite DB Documentation Discussion](https://discourse.nixos.org/t/help-nix-store-sqlite-db-documentation/37729)
- [Nix SQLite Corruption Reports](https://discourse.nixos.org/t/nix-store-sqlite-db-corruption/22841)
- [Nix Issue #378: Get rid of sqlite dependency](https://github.com/NixOS/nix/issues/378)
- [Nix Issue #11500: db.sqlite atomic protection](https://github.com/NixOS/nix/issues/11500)
- [Nix SQLite VFS Corruption Bug #7396](https://github.com/NixOS/nix/issues/7396)
- [Repairing a Very Corrupt Nix Store](https://realjenius.com/2023/07/21/corrupt-store/)
- [dpkg Database — Debian System Concepts](https://www.halolinux.us/debian-system-concepts/interacting-with-the-package-database.html)
- [Arch Linux Pacman Database Documentation](https://bbs.archlinux.org/viewtopic.php?id=265018)
- [XBPS API Documentation](https://xbps-api-docs.voidlinux.org/)
- [Gentoo VDB Wiki](https://wiki.gentoo.org/wiki//var/db/pkg)
- [Gentoo VDB Format Change Bug #321317](https://bugs.gentoo.org/321317)
- [Alpine APK Spec](https://wiki.alpinelinux.org/wiki/Apk_spec)
- [Alpine Linux 3.23 (apk-tools v3)](https://www.phoronix.com/news/Alpine-Linux-3.23)
- [eopkg GitHub (GDBM format)](https://github.com/getsolus/eopkg)
- [BerkeleyDB AGPL License Change — LWN](https://lwn.net/Articles/557820/)
- [Oracle Berkeley DB Licensing](https://www.oracle.com/database/technologies/related/berkeleydb/berkeleydb-licensing.html)
- [SQLite as Application File Format](https://sqlite.org/appfileformat.html)
- [SQLite Appropriate Uses](https://sqlite.org/whentouse.html)
- [How to Corrupt an SQLite Database](https://sqlite.org/howtocorrupt.html)
- [Silverblue SQLite Migration Issue #118](https://github.com/fedora-silverblue/issue-tracker/issues/118)
- [osquery Fedora 33 RPM Format Issue #6895](https://github.com/osquery/osquery/issues/6895)
- [RPM Database Recovery Documentation](https://rpm.org/user_doc/db_recovery.html)
- [Fedora CoreOS BDB to SQLite Migration #623](https://github.com/coreos/fedora-coreos-tracker/issues/623)
