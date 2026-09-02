# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""pkm database layer — SQLite operations for package metadata.

Handles the hybrid approach: SQLite as primary database for speed,
text manifests generated alongside for human inspection.
"""

import errno
import fnmatch
import hashlib
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Forensic-trace shim — defensive import.
try:
    from . import _trace
    _TRACE_AVAILABLE = True
except ImportError:
    _trace = None
    _TRACE_AVAILABLE = False


def _emit_db_event(operation, **fields):
    """Emit a pkm_db_write event when forensic-trace is loaded.

    Safe to call at every SQLite mutation site: short-circuits to no-op
    when verbose mode is off. Failures are swallowed silently so a sink
    write failure never affects the database operation.
    """
    if _TRACE_AVAILABLE:
        try:
            _trace.trace_event("pkm_db_write", operation=operation, **fields)
        except Exception:
            pass

class _OneTimeUpdateReport:
    """Console reporting for a one-time database update that can take a while.

    A database update that works for minutes while printing nothing is
    indistinguishable from a hang, and a package manager that looks hung is a
    package manager the user cannot trust. This gives the update three things
    it did not have: it says what it is about to do before it starts, it says
    which step it is on, and it keeps saying so while a step is still running.

    The heartbeat rides SQLite's own progress callback rather than a thread,
    so liveness is reported from inside the statement that is actually
    working. A step that finishes quickly never prints one — the first
    heartbeat waits HEARTBEAT_AFTER seconds, which is longer than any step
    takes on a database small enough for the question not to arise.

    Verbosity: the opening and closing lines go out at EVERY level, including
    -q. A user who asked for quiet asked for less chatter, not for a silent
    multi-minute pause with no way to tell work from a freeze. The per-step
    detail in between is ordinary prose and is suppressed at -q as usual.
    """

    HEARTBEAT_AFTER = 2.0      # seconds a step may run before it must report
    HEARTBEAT_EVERY = 3.0      # seconds between heartbeats after the first
    _PROGRESS_OPS = 100_000    # SQLite VM steps between callback invocations

    def __init__(self, conn):
        self.conn = conn
        self._out = None
        self._step = None
        self._step_started = None
        self._last_beat = 0.0
        self._started = time.monotonic()

    # -- output plumbing ------------------------------------------------
    #
    # pkm.output is imported lazily, inside the call, for two reasons: this
    # module is the bottom of pkm's import graph and must not gain a
    # top-level dependency on a module that may later want to read the
    # database, and a database opened by a non-pkm caller (a test, a script)
    # must not fail to open because a console module could not be imported.
    def _emitters(self):
        if self._out is None:
            try:
                from . import output as _o
                self._out = (_o.emit_done, _o.emit_info, _o.process_level,
                             _o.VERBOSE)
            except Exception:
                noop = lambda *_a, **_k: None       # noqa: E731
                self._out = (noop, noop, lambda: 0, 99)
        return self._out

    # Every write to the console is guarded. This reporting exists to make a
    # database update visible; it must never be able to STOP one. A closed
    # stdout, a broken pipe or a console module that raises would otherwise
    # propagate out of a progress callback or out of announce() and abort the
    # update — turning a reporting improvement into the more serious failure
    # it was added to prevent. Same reasoning as _emit_db_event above.
    def _say_always(self, text):
        try:
            self._emitters()[0](text)
        except Exception:
            pass

    def _say(self, text):
        try:
            self._emitters()[1](text)
        except Exception:
            pass

    def _verbose(self):
        try:
            done, info, level, verbose = self._emitters()
            return level() >= verbose
        except Exception:
            return False

    # -- the report -----------------------------------------------------

    def announce(self, records):
        self._started = time.monotonic()
        self._say_always(
            "pkm: one-time update to this system's package database — "
            f"{records:,} file-ownership records to check."
        )
        self._say(
            "This adds a rule that keeps a single ownership record for each "
            "file in each package. It runs once on this system, changes no "
            "installed file, and can take a few minutes on a large system."
        )

    def step(self, text):
        """Begin a reported step, arming the heartbeat for its duration."""
        self.end_step()
        self._step = text
        self._step_started = time.monotonic()
        self._last_beat = self._step_started
        self._say(f"  {text}")
        try:
            self.conn.set_progress_handler(self._beat, self._PROGRESS_OPS)
        except Exception:
            pass

    def end_step(self):
        if self._step is None:
            return
        try:
            self.conn.set_progress_handler(None, 0)
        except Exception:
            pass
        if self._verbose():
            self._say(
                f"    {self._step} took "
                f"{time.monotonic() - self._step_started:.1f}s"
            )
        self._step = None

    def _beat(self):
        """SQLite progress callback. Never raises and never returns non-zero:
        a raised exception or a true return value here ABORTS the statement in
        progress, which would turn a reporting nicety into a failed database
        update."""
        try:
            now = time.monotonic()
            if (now - self._step_started >= self.HEARTBEAT_AFTER
                    and now - self._last_beat >= self.HEARTBEAT_EVERY):
                self._last_beat = now
                self._say(
                    f"    still working — {now - self._step_started:.0f}s "
                    "on this step"
                )
        except Exception:
            pass
        return 0

    def finish(self, duplicates):
        self.end_step()
        elapsed = time.monotonic() - self._started
        if duplicates:
            outcome = (f"{duplicates:,} duplicated ownership records were "
                       "collapsed to one each")
        else:
            outcome = "no duplicated ownership records were found"
        self._say_always(
            f"pkm: package-database update finished in {elapsed:.1f}s — "
            f"{outcome}."
        )

    def failed(self):
        """Close the report when the update did not complete. The exception
        itself is raised by the caller; this only makes sure the user is not
        left looking at a step line that never ends."""
        self.end_step()
        self._say_always(
            "pkm: the one-time package-database update did not complete. The "
            "database was rolled back to exactly its previous state and no "
            "installed file was touched."
        )


DB_PATH = Path(os.environ.get("IGOS_PKM_DB", "/var/lib/igos/pkm.db"))
MANIFEST_DIR = Path("/var/lib/igos/packages")
ARCHIVE_DIR = Path("/var/lib/igos/archives")

# PKM-E2 (verify Class 1): files that post-install hooks legitimately regenerate,
# so their on-disk content diverges from the recorded hash on a clean, untampered
# install. verify checks their existence but not their content — dpkg/rpm treat
# the GNU info dir exactly this way. Paths are install-root-relative (no leading
# slash). Conservative + evidence-based: extend only when a clean-install
# `verify --all` proves another generated path false-positives. Generated files
# under /etc (e.g. ld.so.cache) are already covered by the is_config exemption.
GENERATED_VERIFY_EXEMPT = frozenset({
    "usr/share/info/dir",   # GNU info index — install-info rewrites it per pkg
})


# PI-E4 (Class 4): files a package's OWN post_install hook removes or relocates
# AFTER the build's filesystem-diff snapshot recorded them as owned, so they are
# recorded as owned yet legitimately absent on every clean install (the same
# hook re-runs at install time). verify reports these as a distinct
# "expected-absent" status, NOT "missing", so the trust signal stays clean while
# ANY OTHER absent owned file still surfaces. Keyed package -> fnmatch patterns
# over the install-root-relative path (no leading slash), version-globbed so a
# package version bump does not silently re-introduce the false-missing. Scoped
# to (package, path): an absent file is excused only for the package known to
# remove it. EVIDENCE-BASED — each pattern traces to a specific post_install
# line; extend only on the same evidence. (The durable form has packages DECLARE
# their hook-removed paths via the recipe -> manifest -> verify; this focused
# static map is the interim until that lands.)
EXPECTED_ABSENT = {
    # rust post_install: `rm *.old` (stale license/readme backups in the doc dir)
    "rust": ("opt/rustc-*/share/doc/rustc-*/*.old",),
    # ghostscript post_install: relocates its versioned doc dir out of share/doc
    "ghostscript": (
        "usr/share/doc/ghostscript/*/COPYING",
        "usr/share/doc/ghostscript/*/GS9_Color_Management.pdf",
        "usr/share/doc/ghostscript/*/Ghostscript.pdf",
        "usr/share/doc/ghostscript/*/News.html",
    ),
    # pulseaudio post_install: drops the X11/autostart launch entries
    "pulseaudio": (
        "etc/xdg/Xwayland-session.d/00-pulseaudio-x11",
        "etc/xdg/autostart/pulseaudio.desktop",
    ),
    # vte post_install: `rm /etc/profile.d/vte.*`
    "vte": ("etc/profile.d/vte.*",),
}


# Component B — CLASS-level expected-absent registry (named, auditable,
# evidence-traced exemptions that apply across MANY packages, beside the
# per-package PI-E4 map above). Each entry is keyed by a stable class_id and
# carries:
#   patterns    — fnmatch globs over the install-root-relative path (no leading
#                 slash). fnmatch `*` spans `/`, so `usr/lib/*.la` covers any
#                 depth under usr/lib while keeping the prefix scope (the
#                 narrowest cover for the evidence — never a bare `*.la`).
#   provenance  — the EXACT producing script (+ the mechanism), so every
#                 exemption traces to a resolvable in-tree line (the citation
#                 gate). No pattern lands without its provenance.
#   applies_to  — "all" (a system-wide removal/volatility class) or a tuple of
#                 package names (scoped, so the class cannot blind an unrelated
#                 package's verify).
# Honesty invariant: an exemption NAMES and SURFACES the absence, never masks
# it. A genuinely-missing file outside every pattern still flags; a missing file
# inside a pattern but for a package the class does not apply to still flags.
EXPECTED_ABSENT_CLASSES = {
    "ch8-la-sweep": {
        "patterns": ("usr/lib/*.la", "usr/libexec/*.la"),
        "provenance": (
            "scripts/chroot-build-ch8.sh — global "
            "`find /usr/lib /usr/libexec -name \\*.la -delete` runs AFTER each "
            "package's DB registration recorded its .la files (LFS-standard, "
            "in-tree, deliberate)"
        ),
        "applies_to": "all",
    },
    "volatile-run": {
        "patterns": ("run/*",),
        "provenance": (
            "scripts/build-squashfs.sh — /run is mounted tmpfs and shipped "
            "empty in the sealed image; any owned run/ path is recreated at "
            "runtime (systemd RuntimeDirectory / tmpfiles), so it is "
            "legitimately absent on every clean system"
        ),
        "applies_to": "all",
    },
}


def _is_expected_absent(package, path):
    """Return a class_id (truthy str) that legitimately excuses `path` being
    absent for `package`, or None (falsy) if the absence is NOT expected — a
    real missing file that must flag.

    `path` is install-root-relative (no leading slash). The per-package PI-E4
    map (a package's OWN post_install hook removes/relocates the file) is
    consulted first, then the class-level registry. Returning the class_id — not
    a bare bool — lets verify NAME which class excused each path (Component B).
    fnmatch keeps versioned globs robust across version bumps; both layers are
    scoped so an absence is excused only where the evidence applies.
    """
    for pat in EXPECTED_ABSENT.get(package, ()):
        if fnmatch.fnmatch(path, pat):
            # PI-E4 per-package hook removal — named per package so the report
            # distinguishes e.g. post-install:rust from post-install:vte.
            return "post-install:" + package
    for class_id, spec in EXPECTED_ABSENT_CLASSES.items():
        applies = spec["applies_to"]
        if applies != "all" and package not in applies:
            continue
        if any(fnmatch.fnmatch(path, pat) for pat in spec["patterns"]):
            return class_id
    return None


# Regex for sha256 suffix in manifest FILE LIST entries (RFC v1, 2026-05-01).
# Anchored at end-of-line with a leading space + literal "sha256:" + 64 hex
# chars. This handles paths containing whitespace correctly (e.g.,
# linux-firmware files like "brcmfmac43455-sdio.Raspberry Pi Foundation-...txt.xz")
# where naive whitespace-split parsers truncate the path.
# The optional backslash before the digest tolerates rows already in the
# field: the bash lane's writer once carried sha256sum's escaped-output
# backslash for file names containing a backslash (systemd's
# system-systemd\x2d*.slice units), and installs made from those manifests
# must still verify (tests/pkm/test_manifest_hash_backslash_names.py).
_SHA256_SUFFIX_RE = re.compile(r' sha256:\\?([0-9a-f]{64})$')


def _parse_manifest_line(line):
    """Parse a manifest FILE LIST entry into (path, sha256_or_none).

    Manifest format (RFC v1, 2026-05-01):
      "path/"           → directory entry, no hash
      "path"            → file entry without hash (typically symlink)
      "path sha256:HEX" → file entry with sha256 hash (HEX is 64 hex chars)

    Paths may contain whitespace; anchoring the hash suffix at end-of-line
    via regex is correct, splitting on first whitespace is not.
    """
    line = line.rstrip("\n")
    m = _SHA256_SUFFIX_RE.search(line)
    if m:
        return line[:m.start()], m.group(1)
    return line, None

SCHEMA = """
CREATE TABLE IF NOT EXISTS installed (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    release INTEGER DEFAULT 1,
    tier TEXT,
    description TEXT,
    license TEXT,
    build_date TEXT,
    install_date TEXT,
    install_method TEXT,
    archive_path TEXT,
    uncompressed_size INTEGER,
    compressed_size INTEGER,
    superseded_by TEXT,
    superseded_at TEXT,
    -- Q9 (O-026 + O-015):
    --   held: 1 → package excluded from `pkm upgrade` without explicit
    --   --ignore-holds flag (emergency security override).
    --   install_reason: 'manual' (user explicitly invoked `pkm install <name>`)
    --   or 'dependency' (pulled in by another package's runtime deps).
    --   `pkm autoremove` removes only install_reason='dependency' packages
    --   that have no reverse-deps.
    held INTEGER DEFAULT 0,
    install_reason TEXT DEFAULT 'manual',
    -- PKM-A25: NULL = healthy; else the comma-joined critical post-install
    -- hook ids that FAILED during install. The package is fully deployed +
    -- registered, but a critical hook (e.g. UKI rebuild/sign, depmod) failed,
    -- so the live system may diverge from package metadata. Surfaced by
    -- `pkm list` + `pkm verify`; cleared by a successful reinstall (fresh row).
    degraded TEXT,
    -- Component A (deploy-layer content-keyed re-register): sha256 of the TEXT
    -- MANIFEST BYTES this row was imported from. import_manifests re-registers
    -- a package whenever the current manifest file's hash differs from this
    -- stored value (so a same-version, content-only rebuild — the standard
    -- release-bump iteration — refreshes the row + its file checksums instead
    -- of leaving them stale, which then fails `pkm verify` on the shipped
    -- system). NULL = provenance unproven (a pre-column DB row, or a row
    -- written by a path that predates this column) → re-register once, which
    -- backfills the hash. An identical hash is a true no-op (no row-id churn).
    manifest_sha256 TEXT,
    -- 3.0-F28: 1 => this package ships a payload that cannot activate on the
    -- running system until reboot (out-of-tree kernel module behind a
    -- blacklist, kernel image, boot-path component). Sourced from the archive
    -- .PKGINFO `reboot_required=true` (parser.py -> tracker.py). Drives the
    -- loud post-transaction reboot banner + services.classify_restart_requirement
    -- so a just-installed-but-inactive component is never silent. 0/NULL => the
    -- payload activates live (the common case).
    reboot_required INTEGER DEFAULT 0,
    -- The version of the PROPRIETARY PAYLOAD a download-helper fetched, as the
    -- helper itself reported it (helper manifest `version_installed`). It is
    -- recorded separately from `version` because the two answer different
    -- questions: `version` is the identity of the pkm package (the stub that
    -- ships the helper and the vendor keyring), while this column is the build
    -- of the vendor application the machine actually runs. Writing only
    -- `version` conflated them, so any later operation that re-registers the
    -- row from package metadata — an import from the stub's text manifest, a
    -- stub upgrade — silently replaced the payload build with the stub's
    -- version and pkm then reported a payload the box does not run. NULL =
    -- no helper payload (the ordinary archive-installed package).
    payload_version TEXT,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES installed(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    is_dir BOOLEAN DEFAULT 0,
    is_config BOOLEAN DEFAULT 0,
    checksum TEXT,
    -- D-9 (hook-output recording): 1 → this row was not deployed from the
    -- archive payload, it was CREATED on this machine by the package's own
    -- archive lifecycle hook (see pkm/hookrecord.py). The distinction is
    -- load-bearing for verify: a generated file's content is regenerated
    -- whenever the same cache/index refresh runs again — another package's
    -- install firing the same canonical hook is the ordinary case — so a
    -- content check against the hash recorded at creation reports a clean
    -- system as tampered. Existence IS still checked, and the file is
    -- reported in its own named bucket, never silently skipped. Ownership
    -- is full ownership: provides, remove and the squashfs ownership gate
    -- all read this table and make no distinction.
    is_generated INTEGER DEFAULT 0,
    -- Which install path deposited this row: 'helper' → an install helper's
    -- footprint manifest (the proprietary payload a download-helper fetched);
    -- 'archive' or NULL → the package archive itself, including every row
    -- written before this column existed. The distinction is what lets a
    -- helper re-run REPLACE the payload footprint it owns without disturbing
    -- the archive rows recorded under the same package (the helper binary and
    -- vendor keyring the stub ships). Without it, ingestion could only add,
    -- so a payload that dropped files left the vanished paths recorded with
    -- their old checksums and `pkm verify` reported a correct install as
    -- damaged.
    source TEXT,
    -- One ownership row per path per package. Two comments in add_files
    -- already reasoned from this constraint — its INSERT OR REPLACE was
    -- written to "absorb the benign duplicate case" and its IntegrityError
    -- handler to catch what survives that — but the constraint itself was
    -- never declared, so OR REPLACE had no conflict target and degraded to a
    -- plain INSERT. Every re-registration of an already-owned path appended
    -- ANOTHER row: a helper re-run duplicated its whole payload, and remove,
    -- verify and the ownership gate each counted the same file more than once.
    UNIQUE(package_id, path)
);

CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
CREATE INDEX IF NOT EXISTS idx_files_package ON files(package_id);

CREATE TABLE IF NOT EXISTS depends (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES installed(id) ON DELETE CASCADE,
    dep_name TEXT NOT NULL,
    dep_type TEXT NOT NULL,
    UNIQUE(package_id, dep_name, dep_type)
);

CREATE TABLE IF NOT EXISTS available (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    release INTEGER DEFAULT 1,
    tier TEXT,
    description TEXT,
    archive_url TEXT,
    source_url TEXT,
    checksum TEXT,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    operation TEXT NOT NULL,
    package_name TEXT NOT NULL,
    old_version TEXT,
    new_version TEXT,
    method TEXT,
    success BOOLEAN
);

CREATE TABLE IF NOT EXISTS config_files (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    package_id INTEGER REFERENCES installed(id) ON DELETE SET NULL,
    original_checksum TEXT
);
"""


class PackageDB:
    """Package database interface."""

    def __init__(self, db_path=None, root="/", create_if_missing=True,
                 read_only=False):
        # NOTE: manifest paths are stored POSIX-relative ("usr/bin/bash",
        # not "/usr/bin/bash"). Do not pass leading-slash paths through
        # self.root / path constructions — pathlib's absolute-right-operand
        # rule silently drops self.root, breaking install-target scenarios.
        #
        # B7/S-D 2 (USA-1 audit closure): create_if_missing controls whether
        # the DB file + parent dir are auto-materialized. Default True
        # preserves existing call-site behavior (pkm install / first-run /
        # production paths). Audit-tools + verify-tools that need to
        # distinguish "DB never populated" from "DB freshly auto-created"
        # MUST pass create_if_missing=False — this raises FileNotFoundError
        # instead of silently masking the diagnostic signal. Per Prime
        # Directive: transparency wins over convenience for audit surface.
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.root = Path(root)
        self.read_only = read_only
        # What the last import_manifests run REFUSED to act on, one line each.
        # A refusal is a finding: a manifest claiming a file it does not own
        # is exactly what an operator needs told, so `pkm import` prints these
        # rather than returning a count that looks like an ordinary success.
        self.import_refusals = []

        # Read-only open (the installed-system inspection case). The normal
        # path below issues `PRAGMA journal_mode = WAL`, `executescript(SCHEMA)`
        # and ALTER-TABLE migrations — all WRITES. On an installed system
        # /var/lib/igos/pkm.db is root-owned, so a regular user running a pure-
        # read command (`pkm list`, `info`, `files`, ...) hit "attempt to write
        # a readonly database" at the WAL pragma and could not inspect their
        # own machine's packages without sudo. Prime Directive: a user must be
        # able to read their own system state without root. `immutable=1` opens
        # without touching the db, its -wal/-shm sidecars, or the parent dir —
        # the only mode that works for a non-root reader of a root-owned WAL
        # db. cli.py selects this for the read-only subcommands.
        if read_only:
            if not self.db_path.exists():
                raise FileNotFoundError(
                    f"pkm database does not exist at {self.db_path}."
                )
            self.conn = sqlite3.connect(
                f"file:{self.db_path}?immutable=1", uri=True
            )
            self.conn.execute("PRAGMA foreign_keys = ON")
            # PKM-A31: a read-only (immutable) open CANNOT run the ALTER-TABLE
            # migrations below — so a DB created by an older pkm can be missing
            # columns this pkm added (e.g. `degraded` from A25). Snapshot the
            # actual columns so read queries select schema-tolerantly (via
            # _col) instead of raising "no such column" on an un-migrated DB.
            self._installed_cols = self._table_columns("installed")
            self._files_cols = self._table_columns("files")
            return

        if not create_if_missing and not self.db_path.exists():
            # The manifest directory named here is the one belonging to THIS
            # database's root, not the live system's: pointed at an install
            # root, a message about /var/lib/igos/packages would send the
            # reader to the wrong filesystem.
            raise FileNotFoundError(
                f"pkm database does not exist at {self.db_path} and "
                f"create_if_missing=False. This usually means pkm install "
                f"has never run on this system (text manifests at "
                f"{self.root / 'var/lib/igos/packages'}/ may still be present "
                f"from an LFS-era bootstrap install)."
            )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self._migrate_supersedes_columns()
        self._migrate_q9_columns()
        self._migrate_a25_degraded_column()
        self._migrate_manifest_sha256_column()
        self._migrate_reboot_required_column()
        self._migrate_files_generated_column()
        self._migrate_files_source_column()
        self._migrate_payload_version_column()
        self._migrate_files_unique_path()
        # PKM-A31: snapshot columns AFTER migrations (read-write path is fully
        # migrated, so this is the complete set). Keeps the read-tolerant _col
        # helper correct on both open paths.
        self._installed_cols = self._table_columns("installed")
        self._files_cols = self._table_columns("files")

    # Reclaimable-space reporting and the explicit compaction command.
    #
    # A database that has collapsed hundreds of thousands of duplicate rows,
    # or removed a large package, leaves the freed pages inside the file:
    # SQLite reuses them but does not hand them back to the filesystem. The
    # decision here (2026-08-06) is that pkm NEVER compacts on its own. An
    # automatic VACUUM would rewrite the entire database as a side effect of
    # an unrelated command, needs as much free disk again as the file is
    # big, and cannot be interrupted safely — exactly the class of surprise
    # a user is entitled not to get from a package manager. So a migration
    # that leaves reclaimable space ADVISES, in one line, and the user runs
    # `pkm vacuum` when it suits them.
    #
    # The threshold below is the point at which advising is worth a line of
    # a user's attention. Under it the freed pages are noise next to the
    # file itself and SQLite will reuse them long before anyone notices.
    VACUUM_ADVICE_MIN_BYTES = 8 * 1024 * 1024

    def reclaimable_bytes(self):
        """Bytes held by free pages inside the database file.

        This is what a VACUUM would return to the filesystem: SQLite's own
        free-page count multiplied by its page size. Read through PRAGMAs
        rather than estimated from the file size, so the figure a user is
        shown is the database's own answer. Returns 0 if either PRAGMA
        cannot be read — an unreadable figure advises nothing, which is the
        honest outcome, never a guess.
        """
        try:
            free_pages = self.conn.execute("PRAGMA freelist_count").fetchone()
            page_size = self.conn.execute("PRAGMA page_size").fetchone()
            if not free_pages or not page_size:
                return 0
            return int(free_pages[0]) * int(page_size[0])
        except Exception:
            return 0

    def vacuum_space_check(self):
        """Whether the filesystem can hold what a VACUUM needs, fail-closed.

        SQLite's VACUUM rebuilds the database into a NEW file beside the
        old one and swaps it, so for the duration BOTH exist: the peak
        requirement is the current file plus the rebuilt one, and the
        rollback journal on top. Refusing when that will not fit is the
        whole point of the preflight — a VACUUM that runs out of disk
        halfway leaves the user with a full filesystem and no compaction,
        which is strictly worse than not starting.

        Returns the dict from pkm.preflight.check_free_space, with
        ``db_bytes`` added, or None when the database file cannot be
        measured (in which case the caller must refuse rather than guess).
        """
        from . import preflight
        try:
            db_bytes = self.db_path.stat().st_size
        except OSError:
            return None
        # Twice the file, which covers the rebuilt copy standing beside the
        # original, and preflight's own 10% margin covers the journal and
        # the ordinary slack every write needs. estimate_required_space is
        # deliberately NOT used: its 1.5x extraction multiplier describes
        # archives expanding, which is a different question.
        check = preflight.check_free_space(db_bytes * 2, self.db_path.parent)
        check["db_bytes"] = db_bytes
        return check

    def vacuum(self):
        """Compact the database, returning (bytes_before, bytes_after).

        Caller-driven only — see the note above VACUUM_ADVICE_MIN_BYTES for
        why nothing in pkm calls this on its own. The caller is responsible
        for having run vacuum_space_check first; this method does the work
        and reports the sizes it actually observed rather than the ones a
        preflight predicted.
        """
        before = self.db_path.stat().st_size
        # A VACUUM cannot run inside a transaction. Any open one is the
        # caller's, so it is committed rather than rolled back — discarding
        # a caller's uncommitted work to run a maintenance command would be
        # a far worse surprise than the maintenance itself.
        if self.conn.in_transaction:
            self.conn.commit()
        self.conn.execute("VACUUM")
        self.conn.commit()
        # Checkpoint before measuring. The database runs in write-ahead-log
        # mode, so a VACUUM's result lands in the log first and the main file
        # keeps its old size until the log is folded back into it — which
        # happens on its own, later, usually when the connection closes.
        # Reading the size straight after the VACUUM therefore reported the
        # size the file HAD, and the command told the user it had returned
        # nothing while the file was in fact about to shrink by 97%.
        # Measured on this machine 2026-08-06: reported "29.5 MiB → 29.5 MiB;
        # 0 B returned" for a rebuild that took the file from 30,883,840 to
        # 798,720 bytes. A figure that is wrong is worse than no figure, so
        # the checkpoint is part of the operation and not an optimisation.
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            # A checkpoint that cannot run leaves the numbers below
            # understated rather than wrong in the dangerous direction (it
            # can only report LESS reclaimed than actually was). The rebuild
            # itself has already committed and is unaffected.
            pass
        after = self.db_path.stat().st_size
        _emit_db_event("vacuum", bytes_before=before, bytes_after=after)
        return before, after

    def _table_columns(self, table):
        """Set of column names present in `table`. Used so read queries tolerate
        a behind-schema DB (a read-only open can't run the ALTER-TABLE
        migrations, so an older DB may lack newer columns). PKM-A31."""
        try:
            return {row[1] for row in
                    self.conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.OperationalError:
            return set()

    def _col(self, name):
        """A SELECT term for an `installed` column that yields NULL when the
        column is absent on a behind-schema (un-migrated, read-only) DB, instead
        of raising 'no such column'. PKM-A31."""
        return name if name in getattr(self, "_installed_cols", ()) \
            else f"NULL AS {name}"

    def _migrate_supersedes_columns(self):
        """Idempotent migration: add superseded_by + superseded_at columns
        to pre-existing `installed` tables that predate the supersedes RFC."""
        for col in ("superseded_by", "superseded_at"):
            try:
                self.conn.execute(f"ALTER TABLE installed ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e):
                    raise
        self.conn.commit()

    def _migrate_q9_columns(self):
        """Idempotent migration: add Q9 hold + install_reason columns to
        pre-existing `installed` tables. Q9 (O-026 + O-015) adds hold/pin
        and autoremove primitives.

        Defaults match the SCHEMA: held=0 (not held), install_reason=
        'manual' (existing packages are treated as user-installed for
        autoremove safety — autoremove only touches 'dependency' rows).
        """
        migrations = [
            ("held", "INTEGER DEFAULT 0"),
            ("install_reason", "TEXT DEFAULT 'manual'"),
        ]
        for col, decl in migrations:
            try:
                self.conn.execute(
                    f"ALTER TABLE installed ADD COLUMN {col} {decl}"
                )
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e):
                    raise
        self.conn.commit()

    def _migrate_a25_degraded_column(self):
        """Idempotent migration (PKM-A25): add the `degraded` column to a
        pre-existing installed table. NULL = healthy; else the critical-hook
        ids that failed at install."""
        try:
            self.conn.execute(
                "ALTER TABLE installed ADD COLUMN degraded TEXT"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e):
                raise
        self.conn.commit()

    def _migrate_manifest_sha256_column(self):
        """Idempotent migration (Component A): add the `manifest_sha256` column
        to a pre-existing installed table. The SCHEMA's `CREATE TABLE IF NOT
        EXISTS` does NOT alter an already-present table, so a DB created before
        this column existed needs the ALTER. MUST work on substrate DBs shipped
        before the column: a NULL stored hash means "provenance unproven" and
        import_manifests re-registers the row once to backfill it."""
        try:
            self.conn.execute(
                "ALTER TABLE installed ADD COLUMN manifest_sha256 TEXT"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e):
                raise
        self.conn.commit()

    def _migrate_reboot_required_column(self):
        """Idempotent migration (3.0-F28): add the `reboot_required` column to a
        pre-existing installed table. The SCHEMA's `CREATE TABLE IF NOT EXISTS`
        does NOT alter an already-present table, so a DB created before this
        column existed needs the ALTER. Substrate DBs shipped before the column
        default to 0 (activates live) — a conservative default: a package that
        genuinely needs a reboot re-declares it via its archive .PKGINFO on the
        next install/upgrade, which refreshes the row."""
        try:
            self.conn.execute(
                "ALTER TABLE installed ADD COLUMN reboot_required INTEGER DEFAULT 0"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e):
                raise
        self.conn.commit()

    def _migrate_files_generated_column(self):
        """Idempotent migration (D-9): add `is_generated` to a pre-existing
        files table. The SCHEMA's `CREATE TABLE IF NOT EXISTS` does NOT alter
        an already-present table. The default 0 is the honest value for every
        pre-existing row: before hook-output recording existed, nothing a hook
        generated was registered at all, so no existing row can be one."""
        try:
            self.conn.execute(
                "ALTER TABLE files ADD COLUMN is_generated INTEGER DEFAULT 0"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e):
                raise
        self.conn.commit()

    def _migrate_files_source_column(self):
        """Idempotent migration: add `source` to a pre-existing files table.
        The SCHEMA's `CREATE TABLE IF NOT EXISTS` does not alter a table that
        already exists. NULL is the honest value for every pre-existing row —
        before this column, nothing recorded which install path deposited a
        row, and reading NULL as 'archive' is the safe direction: a helper
        re-run leaves an unlabelled row alone rather than deleting a row it
        cannot prove it owns."""
        try:
            self.conn.execute("ALTER TABLE files ADD COLUMN source TEXT")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e):
                raise
        self.conn.commit()

    def _migrate_payload_version_column(self):
        """Idempotent migration: add `payload_version` to a pre-existing
        installed table. NULL means no helper payload is recorded for the row,
        which is the truth for every package installed before the column
        existed — including helper packages, whose payload build was only ever
        written into `version`. The first helper ingestion after this
        migration fills it in."""
        try:
            self.conn.execute(
                "ALTER TABLE installed ADD COLUMN payload_version TEXT"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e):
                raise
        self.conn.commit()

    def _migrate_files_unique_path(self):
        """Idempotent migration: give a pre-existing files table its
        UNIQUE(package_id, path) constraint, collapsing whatever duplicates it
        already carries.

        SQLite cannot add a table constraint with ALTER TABLE, so the table is
        rebuilt. That is the expensive kind of migration, and it runs on a
        LIVE installed database, so the order here is deliberate:

          1. Detect first. If the constraint is already present, return before
             touching anything — this runs on every database open.
          2. Collapse duplicates INSIDE the same transaction as the rebuild,
             keeping the HIGHEST rowid for each (package_id, path). The newest
             row is the one the most recent install wrote, so its checksum
             matches what is on disk now; an older row's checksum describes
             bytes that were replaced. Where the newest row is unlabelled and
             an older one carries a source label, the label is carried forward
             rather than lost.
          3. Rebuild, copy, drop, rename, recreate the indexes, all in one
             transaction, with foreign-key enforcement switched off across the
             swap. A failure at any point rolls back to the original table
             rather than leaving a package system with half a files table.

        The whole point is that after this runs, add_files' INSERT OR REPLACE
        finally means what its comment has always claimed.

        HOW THE SURVIVING ROWS ARE SELECTED, and why it is not the obvious
        shape. The obvious shape asks, for every row, "are you the newest row
        for your (package_id, path)?" — a correlated subquery per row. That
        was measured against a real installed database of 881,959 rows in
        1,006 packages and did not finish: the only usable index for the
        lookup is on package_id alone, so each of those subqueries rescans its
        package's whole row set, and one package in that database holds
        118,775 rows. The work is the sum of each package's row count SQUARED
        — 89 billion row visits for a table of under a million rows, and it
        grows with the square of the largest package rather than with the size
        of the database.

        This version asks the question once instead of once per row. A single
        grouped pass records, for each (package_id, path), the id to keep, how
        many rows the group had, and any source label present anywhere in it;
        the rebuild then joins that summary to the table by INTEGER PRIMARY
        KEY, which is a direct rowid lookup. One sort plus one scan, and the
        duplicate count that used to need its own second grouped pass falls
        out of the same summary.

        One meaning changes, deliberately and for the better: where duplicates
        carried DIFFERENT source labels the old form took whichever the
        database happened to return first, which was arbitrary and could
        differ between two runs over the same rows. This takes MAX(source),
        which is deterministic and, for the two labels that exist, prefers
        'helper' over 'archive' — the label whose loss the inheritance rule
        exists to prevent.
        """
        if self._files_has_unique_path():
            return 0

        cols = [r[1] for r in self.conn.execute(
            "PRAGMA table_info(files)").fetchall()]
        keep_source = "source" in cols
        keep_generated = "is_generated" in cols
        total_rows = self.conn.execute(
            "SELECT COUNT(*) FROM files").fetchone()[0]

        report = _OneTimeUpdateReport(self.conn)
        report.announce(total_rows)

        self.conn.execute("PRAGMA foreign_keys=OFF")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            # One grouped pass over the table. `keep_id` is the row that
            # survives, `n` is how many rows the group had (so the duplicate
            # count needs no second pass), and `any_source` is any label the
            # group carried, so inheritance needs no per-row lookup either.
            src_agg = ", MAX(source) AS any_source" if keep_source else ""
            self.conn.execute("DROP TABLE IF EXISTS temp.files_collapse")
            report.step(
                f"Checking {total_rows:,} file-ownership records for "
                "duplicates..."
            )
            self.conn.execute(f"""
                CREATE TEMP TABLE files_collapse AS
                SELECT package_id, path, MAX(id) AS keep_id,
                       COUNT(*) AS n{src_agg}
                FROM files
                GROUP BY package_id, path
            """)
            dupes = self.conn.execute(
                "SELECT COUNT(*) FROM temp.files_collapse WHERE n > 1"
            ).fetchone()[0]
            survivors = self.conn.execute(
                "SELECT COUNT(*) FROM temp.files_collapse").fetchone()[0]

            self.conn.execute("""
                CREATE TABLE files_rebuild (
                    id INTEGER PRIMARY KEY,
                    package_id INTEGER NOT NULL
                        REFERENCES installed(id) ON DELETE CASCADE,
                    path TEXT NOT NULL,
                    is_dir BOOLEAN DEFAULT 0,
                    is_config BOOLEAN DEFAULT 0,
                    checksum TEXT,
                    is_generated INTEGER DEFAULT 0,
                    source TEXT,
                    UNIQUE(package_id, path)
                )
            """)

            gen = "f.is_generated" if keep_generated else "0"
            # A surviving row keeps its own source label when it has one, and
            # otherwise inherits any label its duplicates carried — dropping a
            # 'helper' label would make that payload row unreplaceable by the
            # next helper run.
            src = "COALESCE(f.source, k.any_source)" if keep_source else "NULL"
            report.step(f"Rewriting {survivors:,} records...")
            self.conn.execute(f"""
                INSERT INTO files_rebuild
                    (id, package_id, path, is_dir, is_config, checksum,
                     is_generated, source)
                SELECT f.id, f.package_id, f.path, f.is_dir, f.is_config,
                       f.checksum, {gen}, {src}
                FROM temp.files_collapse k
                JOIN files f ON f.id = k.keep_id
            """)

            self.conn.execute("DROP TABLE files")
            self.conn.execute("ALTER TABLE files_rebuild RENAME TO files")
            report.step("Rebuilding the lookup indexes...")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_path ON files(path)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_package "
                "ON files(package_id)")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            report.failed()
            raise
        finally:
            # Cleanup must not be able to REPLACE the failure that brought us
            # here. A statement that raises inside `finally` discards the
            # exception in flight, so a database left in a state where the
            # temp table cannot be dropped would report that instead of the
            # rollback-worthy failure the caller needs to see.
            for cleanup in ("DROP TABLE IF EXISTS temp.files_collapse",
                            "PRAGMA foreign_keys=ON"):
                try:
                    self.conn.execute(cleanup)
                except sqlite3.Error:
                    pass

        report.finish(dupes)
        # ADVISE, NEVER AUTO-FIRE. Collapsing hundreds of thousands of
        # duplicate rows frees a great deal of space INSIDE the database
        # file, and SQLite keeps it. Compacting is a whole-file rewrite that
        # needs as much disk again — a decision that belongs to the person
        # whose disk it is, not a side effect of an unrelated command.
        self.advise_vacuum_if_reclaimable()
        _emit_db_event("migrate_files_unique_path", duplicate_groups=dupes)
        return dupes

    def advise_vacuum_if_reclaimable(self):
        """Print ONE line advising `pkm vacuum` when it would return real space.

        Called by a migration that has just freed pages. Never called on an
        ordinary database open: a package manager that repeats housekeeping
        advice on every single invocation teaches its users to read past its
        output, which costs more than the advice is worth.

        Returns the reclaimable byte count it acted on (0 when it said
        nothing), so a caller and a test can ask what it decided without
        parsing the line. Guarded like every other console write in this
        module: advice must never be able to break the operation that
        produced it.
        """
        try:
            reclaimable = self.reclaimable_bytes()
            if reclaimable < self.VACUUM_ADVICE_MIN_BYTES:
                return 0
            from . import output as _o
            _o.emit_done(
                f"pkm: about {_o.human_size(reclaimable)} inside the package "
                f"database is now unused. Run `pkm vacuum` to return it to "
                f"the filesystem — nothing does this on its own, and the "
                f"database works normally either way."
            )
            return reclaimable
        except Exception:
            return 0

    def _files_has_unique_path(self):
        """True when the files table already carries UNIQUE(package_id, path).

        Read from the table's own SQL rather than from a version counter: a
        database that has been through an older pkm, a restore, or a hand
        repair is described by what it actually contains, not by what a
        counter claims about it.
        """
        row = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='files'"
        ).fetchone()
        if not row or not row[0]:
            return False
        sql = " ".join(row[0].split()).lower()
        return "unique(package_id, path)" in sql or "unique (package_id, path)" in sql

    def replace_helper_footprint(self, package_id, new_paths, commit=True):
        """Clear the file rows a previous helper run recorded for this package.

        Called immediately before re-recording a helper footprint, so ingestion
        REPLACES what the helper owns instead of unioning old and new. Two
        classes of row are removed:

          1. every row this package carries with source='helper' — the previous
             payload. Paths the new payload dropped disappear with it, which is
             the whole point: left behind, they stay recorded with the old
             payload's checksums and `pkm verify` calls a correct install
             damaged.
          2. any remaining row for a path the incoming footprint also claims,
             whatever its source. The files table has no UNIQUE(package_id,
             path), so `INSERT OR REPLACE` has no conflict target to absorb and
             a repeat install appended a SECOND row for every shared path.

        Rows the package's own archive deposited (source='archive' or NULL) on
        paths the payload does not claim are untouched — a download-helper
        package owns both its stub files and its payload, and only the payload
        half is being replaced.

        Returns (payload_rows_removed, shared_rows_removed).
        """
        cur = self.conn.execute(
            "DELETE FROM files WHERE package_id = ? AND source = 'helper'",
            (package_id,),
        )
        payload_removed = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

        shared_removed = 0
        for path in new_paths:
            cur = self.conn.execute(
                "DELETE FROM files WHERE package_id = ? AND path = ?",
                (package_id, path.rstrip("/")),
            )
            if cur.rowcount and cur.rowcount > 0:
                shared_removed += cur.rowcount

        if commit:
            self.conn.commit()
        _emit_db_event(
            "replace_helper_footprint", pkg_id=package_id,
            payload_rows_removed=payload_removed,
            shared_rows_removed=shared_removed,
        )
        return payload_removed, shared_removed

    def mark_degraded(self, name, reason):
        """PKM-A25: flag the live installed row as degraded — a critical
        post-install hook failed, so the package is registered but the live
        system may diverge from its metadata. `reason` is the comma-joined
        failed-hook ids. Surfaced by `pkm list` + `pkm verify`; a successful
        reinstall replaces the row and clears it."""
        self.conn.execute(
            "UPDATE installed SET degraded = ? "
            "WHERE name = ? AND superseded_by IS NULL",
            (reason, name),
        )
        self.conn.commit()
        _emit_db_event("mark_degraded", pkg=name, reason=reason)

    def update_helper_merge(self, pkg_id, name, version, release, commit=True,
                            payload_version=None):
        """PKM-A26: update the existing <app> row with the helper-merged
        version + release + install_method='helper'. The unified
        `pkm install <app>` helper path must UPDATE (not INSERT OR REPLACE,
        which would orphan the infra file rows by minting a new id) — but a
        bare UPDATE bypassed add_installed, the only db-write that emits a
        forensic trace event, so a helper merge was INVISIBLE in the trace
        (asymmetric with the INSERT branch). Emit the db-write event here too.

        payload_version records the vendor build the helper actually fetched,
        in its own column rather than only in `version`. See the column comment
        on installed.payload_version for why the two are kept apart. None
        leaves any previously recorded payload build in place rather than
        blanking it — a caller that does not know the payload version must not
        erase the answer."""
        if payload_version:
            self.conn.execute(
                "UPDATE installed SET version = ?, release = ?, "
                "install_method = 'helper', payload_version = ? WHERE id = ?",
                (version, release, payload_version, pkg_id),
            )
        else:
            self.conn.execute(
                "UPDATE installed SET version = ?, release = ?, "
                "install_method = 'helper' WHERE id = ?",
                (version, release, pkg_id),
            )
        if commit:
            self.conn.commit()
        _emit_db_event(
            "update_helper_merge", pkg=name, version=version,
            release=release, pkg_id=pkg_id, payload_version=payload_version,
        )

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # ------------------------------------------------------------------
    # Installed packages
    # ------------------------------------------------------------------

    def get_installed(self, name):
        """Get an installed package by name. Returns dict or None."""
        row = self.conn.execute(
            "SELECT * FROM installed WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute("SELECT * FROM installed LIMIT 0").description]
        return dict(zip(cols, row))

    def list_installed(self, tier=None):
        """List all installed packages. Optionally filter by tier.

        Returns dicts with name/version/release/tier/description. release
        is included so callers doing version-aware compare (see pkm.version)
        can disambiguate same-version-different-release upgrades without
        an extra get_installed roundtrip per package.
        """
        # PKM-A31: build the column list schema-tolerantly so a read-only open
        # of a DB created by an older pkm (missing e.g. `degraded`) yields NULL
        # for the absent column rather than crashing every read command.
        sel = ", ".join(self._col(c) for c in (
            "name", "version", "release", "tier", "description",
            "install_reason", "degraded", "reboot_required"))
        if tier:
            rows = self.conn.execute(
                f"SELECT {sel} FROM installed WHERE tier = ? ORDER BY name",
                (tier,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                f"SELECT {sel} FROM installed ORDER BY name"
            ).fetchall()
        return [
            {"name": r[0], "version": r[1], "release": r[2], "tier": r[3],
             "description": r[4], "install_reason": r[5] or "manual",
             "degraded": r[6],  # PKM-A25: NULL = healthy
             "reboot_required": r[7]}  # 3.0-F28: NULL/0 = activates live
            for r in rows
        ]

    def add_installed(self, name, version, release=1, tier=None, description=None,
                      license_=None, build_date=None, install_method="archive",
                      archive_path=None, uncompressed_size=0, compressed_size=0,
                      install_reason="manual", commit=True, manifest_sha256=None,
                      reboot_required=0, replace_existing=False,
                      payload_version=None):
        """Register a package as installed.

        commit: when True (default), commit immediately. Set to False when
        called inside an outer transaction (e.g. atomic supersede in the
        installer), so the caller manages BEGIN/COMMIT/ROLLBACK.
        install_reason: 'manual' (user-requested) or 'dependency'
        (dep-resolution-pulled). Default 'manual' — explicit dep installs
        must pass install_reason='dependency'. Q9 autoremove only touches
        'dependency' rows that have zero reverse-deps.

        replace_existing: THE DESTRUCTIVE CONTRACT. The statement below is an
        `INSERT OR REPLACE` on `UNIQUE(name)`, and `files` + `depends` both
        declare `REFERENCES installed(id) ON DELETE CASCADE` — so registering
        a name that is already installed does not update its row, it DELETES
        the row and every file-ownership and dependency row attached to it,
        then inserts a fresh one under a new id. Measured against a copy of a
        real 972-package system database: re-registering `mariadb` dropped
        17,806 file rows and 9 depends rows in that one statement.

        Every re-registering caller re-adds both sets immediately, which is
        why the corpus is intact — but the destruction was invisible at the
        call site and reported nothing, so a caller that registered without
        re-adding would deregister a package's whole payload while it stayed
        listed as installed and its files stayed on disk. `pkm verify` would
        then have nothing to check and `pkm remove` could not unlink what it
        no longer owned, with no error anywhere.

        So the replace is refused unless the caller declares it. Pass
        replace_existing=True at a site that re-registers, and re-add the
        files and depends inside the same transaction. Leave it at the
        default at a site that registers a name the database has not seen;
        if that assumption is ever wrong the call raises instead of silently
        deregistering. `config_files` is deliberately NOT cascaded — it
        declares ON DELETE SET NULL, so a baseline is orphaned rather than
        deleted and `add_files` re-links it by path, which is what preserves
        config-protect state across a plain reinstall.
        """
        existing = self.conn.execute(
            "SELECT id FROM installed WHERE name = ?", (name,)).fetchone()
        replaced_pkg_id = existing[0] if existing is not None else None
        cascaded_files = cascaded_depends = 0
        if replaced_pkg_id is not None:
            if not replace_existing:
                raise ValueError(
                    f"add_installed('{name}') would REPLACE the installed row "
                    f"(id {replaced_pkg_id}), cascading away its files and "
                    f"depends rows. Pass replace_existing=True and re-add both "
                    f"in the same transaction if that is intended."
                )
            # Counted before the statement runs: once the cascade has fired,
            # nothing in the database records that these rows existed, so the
            # trace event is the only place an audit can see a caller that
            # replaced and never re-added.
            cascaded_files = self.conn.execute(
                "SELECT COUNT(*) FROM files WHERE package_id = ?",
                (replaced_pkg_id,)).fetchone()[0]
            cascaded_depends = self.conn.execute(
                "SELECT COUNT(*) FROM depends WHERE package_id = ?",
                (replaced_pkg_id,)).fetchone()[0]
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT OR REPLACE INTO installed
               (name, version, release, tier, description, license,
                build_date, install_date, install_method, archive_path,
                uncompressed_size, compressed_size, install_reason,
                manifest_sha256, reboot_required, payload_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, version, release, tier, description, license_,
             build_date, now, install_method, archive_path,
             uncompressed_size, compressed_size, install_reason,
             manifest_sha256, 1 if reboot_required else 0, payload_version)
        )
        if commit:
            self.conn.commit()
        pkg_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        _emit_db_event(
            "add_installed",
            pkg=name, version=version, release=release, tier=tier,
            install_method=install_method, install_reason=install_reason,
            pkg_id=pkg_id, committed=commit,
            replaced_pkg_id=replaced_pkg_id,
            cascaded_files=cascaded_files,
            cascaded_depends=cascaded_depends,
        )
        return pkg_id

    def remove_installed(self, name):
        """Remove an installed package record and its files."""
        pkg = self.get_installed(name)
        if pkg:
            self.conn.execute("DELETE FROM files WHERE package_id = ?", (pkg["id"],))
            self.conn.execute("DELETE FROM depends WHERE package_id = ?", (pkg["id"],))
            # PKM-A20: drop this package's config_files baselines too. The FK is
            # ON DELETE SET NULL, so deleting the installed row below would
            # ORPHAN these rows (package_id=NULL, stale original_checksum) — and
            # since path is UNIQUE, a later reinstall's baseline INSERT collides
            # with the orphan and keeps the OLD stock checksum, silently
            # mis-baselining edit-detection across the reinstall. Delete here,
            # before the installed row, while package_id still matches.
            self.conn.execute(
                "DELETE FROM config_files WHERE package_id = ?", (pkg["id"],))
            self.conn.execute("DELETE FROM installed WHERE id = ?", (pkg["id"],))
            self.conn.commit()
            _emit_db_event(
                "remove_installed", pkg=name, pkg_id=pkg["id"],
            )
        return pkg

    # ------------------------------------------------------------------
    # File ownership
    # ------------------------------------------------------------------

    def add_files(self, package_id, file_list, hashes=None, commit=True,
                  source=None):
        """Register files owned by a package.

        file_list: list of relative paths (e.g., "usr/bin/bash")
        hashes: optional dict mapping path → sha256 hex. When provided, used
                as the authoritative checksum (e.g., from the package's
                manifest); otherwise, computed from the live filesystem
                if the file exists at install time.
        commit: when True (default), commit immediately. Set to False when
                called inside an outer transaction.
        source: which install path deposited these rows — 'helper' for an
                install helper's payload footprint, 'archive' (or None, the
                legacy value) for the package archive. Only rows labelled
                'helper' are replaced when a helper re-runs, so mislabelling
                an archive row as a helper row would let a later payload
                fetch delete it.
        """
        for path in file_list:
            is_dir = path.endswith("/")
            is_config = path.startswith("etc/") and not is_dir
            checksum = (hashes or {}).get(path)
            if not is_dir and not is_config and checksum is None:
                abs_path = str(self.root / path)
                if os.path.isfile(abs_path):
                    try:
                        checksum = _sha256(abs_path)
                    except (OSError, PermissionError):
                        pass
            try:
                self.conn.execute(
                    """INSERT OR REPLACE INTO files
                       (package_id, path, is_dir, is_config, checksum, source)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (package_id, path.rstrip("/"), is_dir, is_config, checksum,
                     source)
                )
            except sqlite3.IntegrityError as e:
                # PKM-A16: do NOT swallow. INSERT OR REPLACE already absorbs the
                # benign duplicate case, and files.package_id is NOT NULL
                # REFERENCES installed(id) (FKs enforced) — so any IntegrityError
                # that still surfaces here is a GENUINE constraint violation
                # (FK to a non-existent package / NOT NULL). The old WARN+continue
                # left the file deployed on disk with NO ownership row while the
                # package still committed as installed -> orphaned-after-remove
                # (pkm can't unlink what it doesn't know it owns). Raise so the
                # installer's atomic BEGIN/COMMIT (installer.py:833-905) rolls the
                # whole install back fail-closed, and standalone callers abort
                # before the commit below. Never register a package over a file
                # row that could not be recorded.
                raise sqlite3.IntegrityError(
                    f"failed to record ownership row for '{path}' "
                    f"(package_id={package_id}): {e}"
                ) from e
        if commit:
            self.conn.commit()

        # Track config files separately for protection. The Q4 (O-021) fix:
        # original_checksum must be recorded ONCE at first install of the
        # config path and never ratcheted forward by subsequent re-registrations
        # (which would silently re-baseline the user-edited-detection check
        # against new stock — defeating preservation across upgrades).
        #
        # On INSERT (first install): all three values are recorded.
        # On CONFLICT (re-register during upgrade): only package_id updates;
        # original_checksum is preserved from the first install. The upgrade
        # orchestration explicitly ratchets the baseline forward via
        # update_original_checksum(path, new_sha) for files the user has not
        # edited (see pkm.configprotect.ratchet_baselines).
        config_paths = [p for p in file_list if p.startswith("etc/") and not p.endswith("/")]
        for cp in config_paths:
            # H-021: use self.root / cp so config_files baselines hash
            # the live file under the actual install root (matters for
            # Forge installer running with root=/mnt/target before chroot
            # pivot) rather than the build host's /etc/.
            abs_path = str(self.root / cp)
            checksum = (hashes or {}).get(cp)
            if checksum is None and os.path.isfile(abs_path):
                # Same tolerance as the regular-file branch above (:626-632).
                # Without it an /etc file the caller cannot read — 0600
                # sudoers.dist under an unprivileged `pkm import` is the
                # measured case — raised PermissionError out of add_files,
                # aborting the whole operation mid-package and leaving the
                # rest of the manifest directory unprocessed. A baseline we
                # cannot compute is recorded as NULL (unknown), which the
                # config-protect classifier already treats as unattributable
                # and therefore protects; that is the honest fail-safe
                # direction, and it never masks the condition.
                try:
                    checksum = _sha256(abs_path)
                except (OSError, PermissionError):
                    checksum = None
            self.conn.execute(
                """INSERT INTO config_files (path, package_id, original_checksum)
                   VALUES (?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET package_id = excluded.package_id""",
                (cp, package_id, checksum)
            )
        if commit:
            self.conn.commit()

    # ------------------------------------------------------------------
    # Hook-generated file ownership (D-9)
    # ------------------------------------------------------------------

    def all_owned_paths(self):
        """Every path any installed package owns, as a set.

        Directory rows are stored without their trailing slash (add_files
        strips it), so this set is directly comparable to the rstrip("/")
        form pkm/hookrecord.claimable expects.
        """
        return {row[0] for row in self.conn.execute("SELECT path FROM files")}

    def record_generated_files(self, package_id, paths, commit=True):
        """Register paths a package's own lifecycle hook created.

        paths: root-relative paths as pkm stores them, directories carrying
            a trailing "/". Checksums are read from the live filesystem —
            these files have no archive bytes to compare against, because
            the machine that runs the hook is where they come into being.

        Deliberately NOT routed through add_files. add_files treats every
        etc/ path as a config file and records a config-protect baseline for
        it; a hook-generated file under /etc is generated state, not a
        conffile the user is expected to own and edit, and giving it a
        baseline would make the next upgrade offer a .pkmnew sidecar for a
        cache. The rows written here are ordinary ownership rows in every
        other respect: provides, remove and the squashfs ownership gate read
        this table and make no distinction.

        Returns the number of rows written.
        """
        written = 0
        for path in paths:
            is_dir = path.endswith("/")
            stored = path.rstrip("/")
            checksum = None
            if not is_dir:
                abs_path = str(self.root / stored)
                if os.path.isfile(abs_path) and not os.path.islink(abs_path):
                    try:
                        checksum = _sha256(abs_path)
                    except (OSError, PermissionError):
                        # Recorded as NULL (unknown) rather than skipped:
                        # ownership is the point of this row, and a row
                        # without a hash still answers provides/remove and
                        # satisfies the ownership gate. verify treats a
                        # generated row as existence-checked anyway.
                        checksum = None
            try:
                self.conn.execute(
                    """INSERT OR REPLACE INTO files
                       (package_id, path, is_dir, is_config, checksum,
                        is_generated)
                       VALUES (?, ?, ?, 0, ?, 1)""",
                    (package_id, stored, is_dir, checksum),
                )
            except sqlite3.IntegrityError as e:
                # Same fail-closed stance as add_files: INSERT OR REPLACE
                # already absorbs the benign duplicate, so anything that
                # still raises is a genuine constraint violation and must
                # abort the caller's transaction rather than leave a file
                # on disk with no ownership row.
                raise sqlite3.IntegrityError(
                    f"failed to record hook-generated ownership row for "
                    f"'{path}' (package_id={package_id}): {e}"
                ) from e
            written += 1
        if commit:
            self.conn.commit()
        return written

    def get_generated_paths(self, name):
        """Root-relative paths recorded as hook-generated for a package.

        Directory rows come back without the trailing slash, as stored.
        Used by import_manifests to carry the flag across a re-register: a
        text manifest cannot state generated-ness, and under the carry rule
        an unstated column keeps what the row already knows.
        """
        if "is_generated" not in getattr(self, "_files_cols", ()):
            return set()
        return {
            row[0] for row in self.conn.execute(
                "SELECT f.path FROM files f JOIN installed i "
                "ON f.package_id = i.id "
                "WHERE i.name = ? AND f.is_generated = 1",
                (name,),
            )
        }

    def mark_files_generated(self, package_id, paths, commit=True):
        """Set is_generated on existing rows for the given paths.

        Restores the flag after a re-register has rewritten a package's file
        rows from its text manifest. Only touches rows that exist; a path
        the manifest no longer carries is simply not restored.
        """
        stored = [p.rstrip("/") for p in paths]
        for path in stored:
            self.conn.execute(
                "UPDATE files SET is_generated = 1 "
                "WHERE package_id = ? AND path = ?",
                (package_id, path),
            )
        if commit:
            self.conn.commit()

    # ------------------------------------------------------------------
    # Config-file baseline tracking (Q4 .pkmnew sidecar support)
    # ------------------------------------------------------------------

    def get_original_checksum(self, path):
        """Return the recorded original_checksum for a config-file path.

        Args:
            path: relative path (no leading slash, e.g. "etc/foo.conf")

        Returns:
            sha256 hex string of the stock content as last recorded baseline,
            or None if the path is not tracked as a config file.
        """
        row = self.conn.execute(
            "SELECT original_checksum FROM config_files WHERE path = ?",
            (path.lstrip("/"),)
        ).fetchone()
        return row[0] if row else None

    def update_original_checksum(self, path, new_checksum, commit=True):
        """Update the recorded original_checksum for a config-file path.

        Called by the upgrade orchestration after verifying the live file's
        pre-upgrade sha256 matched the recorded baseline (i.e., user did not
        edit). The upgrade replaces the live file with new stock, and this
        records the new stock's sha256 as the baseline going forward.

        For user-edited files, the upgrade orchestration writes a .pkmnew
        sidecar instead of replacing the live file, and the recorded
        baseline is NOT updated (so subsequent upgrades continue to detect
        the user's edits).

        Args:
            path: relative path (no leading slash, e.g. "etc/foo.conf")
            new_checksum: sha256 hex string of the new stock content
            commit: when True (default), commit immediately. Set False when
                    called inside an outer transaction.

        Returns:
            int — number of rows updated. 0 if the path is not tracked.
        """
        cur = self.conn.execute(
            "UPDATE config_files SET original_checksum = ? WHERE path = ?",
            (new_checksum, path.lstrip("/"))
        )
        if commit:
            self.conn.commit()
        return cur.rowcount

    def refresh_baseline(self, path, commit=True):
        """Re-record the original_checksum from the current live file.

        User-facing operation: after the user manually accepts a .pkmnew
        sidecar (typically via `mv /etc/foo.conf.pkmnew /etc/foo.conf`),
        this records the new live content's sha256 as the baseline so
        subsequent upgrades treat the new content as the "original" for
        the user-edited detection check.

        Args:
            path: relative path (no leading slash, e.g. "etc/foo.conf") or
                  absolute path (leading slash stripped).
            commit: when True (default), commit immediately.

        Returns:
            (success: bool, message: str) — success indicates the live
            file exists and the config_files row was updated.
        """
        rel = path.lstrip("/")
        abs_path = str(self.root / rel)
        if not os.path.isfile(abs_path):
            return False, f"{abs_path}: file not found"
        new_sum = _sha256(abs_path)
        rowcount = self.update_original_checksum(rel, new_sum, commit=commit)
        if rowcount == 0:
            return False, f"{path}: not tracked as a config file"
        return True, f"refreshed baseline for {path} → {new_sum[:16]}..."

    def get_files(self, name):
        """Get all files owned by a package."""
        pkg = self.get_installed(name)
        if not pkg:
            return []
        rows = self.conn.execute(
            "SELECT path, is_dir FROM files WHERE package_id = ? ORDER BY path",
            (pkg["id"],)
        ).fetchall()
        return [{"path": r[0], "is_dir": bool(r[1])} for r in rows]

    def get_file_checksums(self, name):
        """Recorded checksum per owned file path: {path: sha256 or None}.

        Directories are excluded (they carry no content hash). Used where a
        writer has to render a text manifest from the committed rows rather
        than from its own in-flight file list — the manifest then mirrors
        exactly what the database owns, so a later re-register from those
        bytes cannot contradict the rows it came from.
        """
        pkg = self.get_installed(name)
        if not pkg:
            return {}
        rows = self.conn.execute(
            "SELECT path, checksum FROM files "
            "WHERE package_id = ? AND is_dir = 0",
            (pkg["id"],),
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def find_owner(self, filepath, include_superseded=False):
        """Find which package owns a file path.

        By default, returns only the active (non-superseded) owner. Set
        include_superseded=True to also see retired records (e.g., to
        audit the chain of supersedes for a path).
        """
        path = filepath.lstrip("/")
        if include_superseded:
            sql = """SELECT i.name, i.version, f.path, i.superseded_by
                     FROM files f JOIN installed i ON f.package_id = i.id
                     WHERE f.path = ?"""
        else:
            sql = """SELECT i.name, i.version, f.path, i.superseded_by
                     FROM files f JOIN installed i ON f.package_id = i.id
                     WHERE f.path = ? AND i.superseded_by IS NULL"""
        row = self.conn.execute(sql, (path,)).fetchone()
        if row:
            return {
                "name": row[0],
                "version": row[1],
                "path": row[2],
                "superseded_by": row[3],
            }
        return None

    # ------------------------------------------------------------------
    # Supersedes
    # ------------------------------------------------------------------

    def mark_superseded(self, predecessor_name, successor_name):
        """Mark predecessor as superseded by successor; record timestamp.

        The predecessor's `installed` record is preserved (audit trail);
        ownership of overlapping file paths is transferred separately via
        transfer_file_ownership(). Both operations should run inside the
        same SQLite transaction at the supersede gate (RFC §4b).
        """
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE installed SET superseded_by = ?, superseded_at = ? WHERE name = ?",
            (successor_name, now, predecessor_name),
        )
        _emit_db_event(
            "mark_superseded",
            predecessor=predecessor_name, successor=successor_name,
        )

    def is_superseded(self, name):
        """Return successor name if package was superseded, else None."""
        row = self.conn.execute(
            "SELECT superseded_by FROM installed WHERE name = ?", (name,)
        ).fetchone()
        return row[0] if row and row[0] else None

    def transfer_file_ownership(self, predecessor_name, successor_id, paths, hashes=None):
        """Transfer file records from predecessor to successor for given paths.

        Used during atomic supersede: paths the successor wrote that overlap
        the predecessor's manifest get re-pointed to the successor's package_id
        (with the successor's content hash). Paths the predecessor owned but
        the successor did not touch remain with the predecessor — they are
        retired alongside the predecessor's marker record but do not move.

        hashes: optional dict mapping path → sha256 hex; updates checksum column.
        """
        pred = self.get_installed(predecessor_name)
        if not pred:
            return 0
        normalized = [p.lstrip("/") for p in paths]
        moved = 0
        for path in normalized:
            new_checksum = (hashes or {}).get(path)

            # The successor usually already owns this path: it is an OVERLAP,
            # so the successor's own install wrote a row for it moments ago.
            # Re-pointing the predecessor's row on top of that used to create a
            # SECOND ownership row for one file — silently, because the files
            # table had no uniqueness constraint to refuse it. Now that it has
            # one, the honest resolution is to keep the successor's own row,
            # refresh its checksum from what the successor actually wrote, and
            # retire the predecessor's row.
            successor_row = self.conn.execute(
                "SELECT id FROM files WHERE package_id = ? AND path = ?",
                (successor_id, path),
            ).fetchone()

            if successor_row is not None:
                if new_checksum is not None:
                    self.conn.execute(
                        "UPDATE files SET checksum = ? WHERE id = ?",
                        (new_checksum, successor_row[0]),
                    )
                cur = self.conn.execute(
                    "DELETE FROM files WHERE package_id = ? AND path = ?",
                    (pred["id"], path),
                )
            elif new_checksum is not None:
                cur = self.conn.execute(
                    """UPDATE files SET package_id = ?, checksum = ?
                       WHERE package_id = ? AND path = ?""",
                    (successor_id, new_checksum, pred["id"], path),
                )
            else:
                cur = self.conn.execute(
                    """UPDATE files SET package_id = ?
                       WHERE package_id = ? AND path = ?""",
                    (successor_id, pred["id"], path),
                )
            # rowcount, not conn.total_changes: total_changes is cumulative for
            # the whole connection, so the old accounting grew by every write
            # the connection had ever made and the returned count was not a
            # count of transferred paths at all.
            moved += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        return moved

    # ------------------------------------------------------------------
    # Dependencies
    # ------------------------------------------------------------------

    def add_depends(self, package_id, deps, commit=True):
        """Add dependency records. deps: list of (dep_name, dep_type).

        commit: when True (default), commit immediately. Set to False when
        called inside an outer transaction (e.g. atomic install in the
        installer), so the caller manages BEGIN/COMMIT/ROLLBACK.
        """
        for dep_name, dep_type in deps:
            try:
                self.conn.execute(
                    "INSERT OR IGNORE INTO depends (package_id, dep_name, dep_type) VALUES (?, ?, ?)",
                    (package_id, dep_name, dep_type)
                )
            except sqlite3.IntegrityError:
                pass
        if commit:
            self.conn.commit()

    def get_depends(self, name):
        """Get dependencies for a package."""
        pkg = self.get_installed(name)
        if not pkg:
            return []
        rows = self.conn.execute(
            "SELECT dep_name, dep_type FROM depends WHERE package_id = ? ORDER BY dep_type, dep_name",
            (pkg["id"],)
        ).fetchall()
        return [{"name": r[0], "type": r[1]} for r in rows]

    def get_reverse_depends(self, name):
        """Get packages that depend on this package."""
        rows = self.conn.execute(
            """SELECT i.name, i.version, d.dep_type, i.release
               FROM depends d JOIN installed i ON d.package_id = i.id
               WHERE d.dep_name = ?
               ORDER BY i.name""",
            (name,)
        ).fetchall()
        # PKM-A30: include release so callers can show the full version-release
        # identity (additive; existing name/version/type consumers unaffected).
        return [{"name": r[0], "version": r[1], "type": r[2], "release": r[3]}
                for r in rows]

    # ------------------------------------------------------------------
    # Q9 hold + autoremove (O-026 + O-015)
    # ------------------------------------------------------------------

    def set_held(self, name, held=True, commit=True):
        """Mark a package held (or release the hold).

        Args:
            name: package name.
            held: True to hold (exclude from `pkm upgrade --all`), False
                to release the hold.
            commit: pass-through commit semantics.

        Returns:
            int — rowcount. 1 on success, 0 if the package is not installed.
        """
        cur = self.conn.execute(
            "UPDATE installed SET held = ? WHERE name = ?",
            (1 if held else 0, name),
        )
        if commit:
            self.conn.commit()
        return cur.rowcount

    def list_held(self):
        """Return list of held package names (held=1). Sorted by name."""
        rows = self.conn.execute(
            "SELECT name FROM installed WHERE held = 1 ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]

    def is_held(self, name):
        """Return True iff the named package is currently held."""
        row = self.conn.execute(
            "SELECT held FROM installed WHERE name = ?", (name,)
        ).fetchone()
        return bool(row and row[0])

    def set_install_reason(self, name, reason, commit=True):
        """Update install_reason for a package.

        Args:
            name: package name.
            reason: 'manual' or 'dependency'.
            commit: pass-through commit semantics.

        Returns:
            int — rowcount. 1 on success, 0 if not installed.

        Raises:
            ValueError on invalid reason.
        """
        if reason not in ("manual", "dependency"):
            raise ValueError(
                f"install_reason must be 'manual' or 'dependency'; got {reason!r}"
            )
        cur = self.conn.execute(
            "UPDATE installed SET install_reason = ? WHERE name = ?",
            (reason, name),
        )
        if commit:
            self.conn.commit()
        return cur.rowcount

    def find_orphan_packages(self):
        """Return packages eligible for `pkm autoremove`.

        Eligibility: install_reason='dependency' AND no currently-installed
        package has this one in its runtime/build deps list. Superseded
        rows are excluded — they don't contribute to live rev-dep state.

        Returns:
            list[dict] — each row {name, version, tier} of orphan packages
            sorted by name.
        """
        rows = self.conn.execute(
            """SELECT i.name, i.version, i.tier
               FROM installed i
               WHERE i.install_reason = 'dependency'
                 AND i.superseded_by IS NULL
                 AND NOT EXISTS (
                   SELECT 1 FROM depends d
                   JOIN installed o ON d.package_id = o.id
                   WHERE d.dep_name = i.name AND o.superseded_by IS NULL
                 )
               ORDER BY i.name"""
        ).fetchall()
        return [{"name": r[0], "version": r[1], "tier": r[2]} for r in rows]

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, term):
        """Search installed packages by name or description."""
        rows = self.conn.execute(
            """SELECT name, version, tier, description FROM installed
               WHERE name LIKE ? OR description LIKE ?
               ORDER BY name""",
            (f"%{term}%", f"%{term}%")
        ).fetchall()
        return [{"name": r[0], "version": r[1], "tier": r[2], "description": r[3]} for r in rows]

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def log_operation(self, operation, package_name, old_version=None,
                      new_version=None, method=None, success=True, commit=True):
        """Log a package operation.

        commit: when True (default), commit immediately. Set to False when
        called inside an outer transaction (e.g. atomic supersede in the
        installer), so the caller manages BEGIN/COMMIT/ROLLBACK.
        """
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO history
               (timestamp, operation, package_name, old_version, new_version, method, success)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (now, operation, package_name, old_version, new_version, method, success)
        )
        if commit:
            self.conn.commit()
        _emit_db_event(
            "log_operation",
            pkg=package_name, history_operation=operation,
            old_version=old_version, new_version=new_version,
            method=method, success=success, committed=commit,
        )

    def get_history(self, package_name=None, limit=50):
        """Get operation history."""
        if package_name:
            rows = self.conn.execute(
                "SELECT * FROM history WHERE package_name = ? ORDER BY timestamp DESC LIMIT ?",
                (package_name, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM history ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        cols = ["id", "timestamp", "operation", "package_name",
                "old_version", "new_version", "method", "success"]
        return [dict(zip(cols, r)) for r in rows]

    # ------------------------------------------------------------------
    # Migration — import existing text manifests
    # ------------------------------------------------------------------

    def import_manifests(self, manifest_dir=None, on_manifest=None):
        """Import existing text manifests into SQLite.

        Reads /var/lib/igos/packages/* and populates the installed and files tables.

        ``on_manifest`` is an optional callback invoked as
        ``on_manifest(index, total, name)`` before each manifest is read.
        A corpus-wide import walks every installed package's manifest and
        rewrites the file rows of each one that changed — on a full system
        that is a thousand manifests and hundreds of thousands of file rows,
        and it ran to completion printing nothing (S2 of the silent-loop
        trio). The callback is the seam that lets the CLI report progress
        without this layer growing a console of its own. A callback that
        raises is not allowed to abort the import: reporting must never be
        able to stop the work it describes.
        """
        # Reset per run: the refusals describe THIS import, and a caller that
        # imports twice must not be shown the first run's findings again.
        self.import_refusals = []

        mdir = Path(manifest_dir) if manifest_dir else MANIFEST_DIR
        if not mdir.exists():
            return 0

        manifest_files = sorted(mdir.iterdir())
        manifest_total = len(manifest_files)
        imported = 0
        for manifest_index, manifest_file in enumerate(manifest_files, start=1):
            if on_manifest is not None:
                try:
                    on_manifest(manifest_index, manifest_total,
                                manifest_file.name)
                except Exception:
                    pass
            if not manifest_file.is_file():
                continue

            raw = manifest_file.read_bytes()
            manifest_sha256 = hashlib.sha256(raw).hexdigest()
            content = raw.decode("utf-8", "surrogateescape")
            meta = _parse_manifest(content)
            if not meta:
                continue

            # Component A — content-keyed re-register. `pkm import` runs at
            # end-of-build and is what phase_squashfs relies on to register the
            # install set. It USED to skip ANY name already in the DB (leaving a
            # stale row on a version bump); a prior fix re-registered on a VERSION
            # change, but a SAME-version, content-only rebuild (a release bump —
            # the standard iteration shape — or any content change) still slipped
            # through: the on-disk files + text manifest were rewritten, the DB
            # row was not, and `pkm verify` then failed on the shipped system
            # (on-disk = new bytes, DB hashes = old). This is a prior build
            # cycle's bash-tier staleness class (large packages like the kernel
            # and python recovered MANUALLY via DB-drop + reinstall).
            #
            # The honest criterion: the DB row must mirror exactly the manifest
            # bytes it was built from. Re-register whenever the current manifest
            # file's sha256 differs from the stored one — this subsumes version
            # bumps, release bumps, checksum changes, and file-set changes in one
            # rule. An IDENTICAL hash is a true no-op (no row-id churn, preserving
            # the re-import idempotency the reregister tests pin). A NULL stored
            # hash (pre-column substrate DB, or a row written before this column)
            # is "provenance unproven" → re-register once, which backfills the
            # hash; correct, not churn. The version-equality belt is retained:
            # PACKAGE VERSION is inside the hashed bytes, so a version mismatch
            # cannot coexist with an identical hash — the check documents intent.
            # (framework §3.5 root cause: metadata-not-updated-on-re-register. The
            # cosmetic tier label is still re-derived by a from-scratch build.)
            existing = self.get_installed(meta["name"])
            if existing:
                stored = existing.get("manifest_sha256")
                if (stored is not None and stored == manifest_sha256
                        and existing["version"] == meta["version"]):
                    continue

            # Release precedence — an import must CARRY a release, never invent
            # one. Ranked: (1) the manifest's own PACKAGE RELEASE header, (2) the
            # release already recorded on the row being re-registered, (3) the
            # schema default, which applies only to a package this DB has never
            # seen.
            #
            # Rule (2) is the load-bearing one. `pkm import` is corpus-wide —
            # scripts/pkg-functions.sh:pkg_install runs it after EVERY package
            # build, over every manifest in MANIFEST_DIR, not just the package
            # just built. Meanwhile every writer that records a TRUTHFUL release
            # (installer.py archive install from .PKGINFO pkgrel, installer.py
            # helper install, igos-build/tracker.py source build from pkg.release)
            # registers its row with manifest_sha256 unset, and the text manifests
            # they emit carried no PACKAGE RELEASE header. A NULL stored hash is
            # "provenance unproven" above → re-register → and the header-less
            # manifest then supplied nothing, so `meta.get("release", 1)` wrote
            # release=1 over the truth. One package rebuild anywhere in the chroot
            # therefore reset the whole corpus to r1 (measured 2026-07-29: a
            # linux-kernel-pass2 rebuild reset 215 healed rows; the squashfs
            # Step-2.7 metadata/payload sync gate caught the split it produced).
            # A default silently overwriting a known value is precisely the
            # mask-don't-verify shape; the row keeps what it knows unless the
            # manifest states otherwise.
            #
            # Rule (2) is scoped to a SAME-VERSION re-register. A release counts
            # rebuilds of one version and conventionally restarts at 1 when the
            # version moves, so carrying a predecessor's release across a version
            # bump would be a different falsehood. A header-less manifest at a new
            # version therefore falls to the default — the honest answer when
            # nothing authoritative states otherwise.
            release = meta.get("release")
            if (release is None and existing is not None
                    and existing.get("version") == meta["version"]):
                release = existing.get("release")
            if release is None:
                release = 1

            # A re-register REFRESHES a row; it must never DOWNGRADE one.
            #
            # A text manifest is a narrow artifact: it states name, version,
            # release, description, build date, size and the file list, and
            # nothing else. Every other column on the row was written by a
            # richer path — the archive installer reads tier, license,
            # archive_path, install_reason and reboot_required out of the
            # archive's .PKGINFO (installer.py add_installed), the source
            # builder writes tier and license from the recipe
            # (tracker.py _write_pkm_db), and both record runtime deps.
            # Passing only what the manifest states meant every one of those
            # columns was silently reset to its schema default the first time
            # `pkm import` walked the corpus: tier and license to NULL,
            # install_method to 'source', archive_path to NULL,
            # install_reason to 'manual', reboot_required to 0 — which
            # silences the F28 reboot banner for exactly the packages that
            # need it — and every depends row deleted with no replacement.
            #
            # The rule that fixes it is the same one release already follows:
            # the manifest wins where it states something, the row keeps what
            # it already knows everywhere else, and the schema default applies
            # only to a package this database has never seen. So the
            # unstated-column values are read off `existing` and carried
            # through the re-register.
            carried = existing or {}
            prior_depends = (
                self.get_depends(meta["name"]) if existing else []
            )
            # D-9: which of this package's file rows were recorded as
            # hook-generated. Same carry rule as the columns above — a text
            # manifest cannot state generated-ness, so the row keeps what it
            # already knows. Without this, the corpus-wide import that runs
            # after every bash-tier package build would quietly demote every
            # hook-generated file to a normal owned file, and the next
            # `pkm verify` would report a healthy machine as modified.
            prior_generated = (
                self.get_generated_paths(meta["name"]) if existing else set()
            )

            # D-9b across a from-scratch import. The carry above can only
            # restore what a row already knew, so it does nothing for a
            # package this database has never seen — the source-built chroot
            # case, where every row is written for the first time by this very
            # loop. The manifest's own HOOK-MANAGED headers are the record
            # that survives having no row at all.
            #
            # SCOPED TO WHAT THIS MANIFEST DECLARES. A manifest may classify
            # only paths in its own FILE LIST; anything else is refused and
            # REPORTED rather than dropped quietly. Two different things are
            # being refused, and only the first is exotic: a manifest that
            # names a path it does not own, and — because mark_files_generated
            # below is scoped to this package's rows — any attempt to reach
            # the row another package holds for the same path. Neither may
            # succeed: downgrading a row to existence-only stops the byte
            # check on that file, so an import that could move the class
            # between packages would be a way to launder a tampered file into
            # a classification that no longer looks at its contents.
            declared = {p.rstrip("/") for p in meta["files"]}
            manifest_generated = set()
            for claimed in meta.get("generated", []):
                stored_claim = claimed.rstrip("/")
                if stored_claim in declared:
                    manifest_generated.add(stored_claim)
                else:
                    self.import_refusals.append(
                        f"{manifest_file.name}: package '{meta['name']}' "
                        f"marks '{claimed}' hook-managed but does not declare "
                        f"it in its own FILE LIST — REFUSED, the path keeps "
                        f"whatever classification its owner already has"
                    )

            # Per-package transaction. add_installed's INSERT OR REPLACE
            # deletes the conflicting row (cascading its files and depends)
            # before inserting the new one, so a failure anywhere between
            # that statement and the last add_files leaves the package
            # half-deregistered: no row, no files, and the manifest still on
            # disk claiming otherwise. Wrapping the whole per-package
            # sequence means an error rolls back to the previous good state
            # and the loop's own error path decides what to do next, rather
            # than the database carrying the damage.
            if self.conn.in_transaction:
                self.conn.commit()
            self.conn.execute("BEGIN")
            try:
                pkg_id = self.add_installed(
                    name=meta["name"],
                    version=meta["version"],
                    release=release,
                    tier=carried.get("tier"),
                    description=(meta.get("description")
                                 or carried.get("description") or ""),
                    license_=carried.get("license"),
                    build_date=meta.get("build_date"),
                    install_method=carried.get("install_method") or "source",
                    archive_path=carried.get("archive_path"),
                    uncompressed_size=meta.get("size", 0),
                    install_reason=carried.get("install_reason") or "manual",
                    manifest_sha256=manifest_sha256,
                    reboot_required=carried.get("reboot_required") or 0,
                    commit=False,
                    # Declared per the add_installed destructive contract: when
                    # `existing` is set this call cascades the package's files
                    # and depends away, and both are re-added below (files from
                    # the manifest, depends from `prior_depends`) inside this
                    # same transaction. A name this database has not seen keeps
                    # the safe default.
                    replace_existing=existing is not None,
                )

                # `held` and `degraded` have no add_installed parameter and
                # are not manifest-stated, so the INSERT OR REPLACE above
                # resets them too — releasing a user's hold and clearing a
                # failed-critical-hook marker without a word. Same carry rule.
                if carried.get("held") or carried.get("degraded"):
                    self.conn.execute(
                        "UPDATE installed SET held = ?, degraded = ? "
                        "WHERE id = ?",
                        (1 if carried.get("held") else 0,
                         carried.get("degraded"), pkg_id),
                    )

                if meta.get("files"):
                    # The manifest's own per-file sha256 columns are the
                    # record this re-register is rebuilding from, and
                    # _parse_manifest has already parsed them into
                    # file_hashes. Dropping them made add_files fall through
                    # to hashing the LIVE file (:626-632), which turns a
                    # corpus-wide import into an unscoped reconcile: any file
                    # that had diverged from its recorded bytes — including a
                    # tampered one — got its divergence written in as the new
                    # truth, and `pkm verify` reported clean afterwards. The
                    # hazard of the unscoped form is spelled out on
                    # reconcile_checksums_from_live and guarded there by
                    # scoping; this path is guarded by using the manifest.
                    # Entries the manifest carries no hash for (directories,
                    # symlinks, pre-annotation manifests) still fall back to
                    # the live read, which is the only reference available
                    # for them.
                    self.add_files(
                        pkg_id, meta["files"],
                        hashes=meta.get("file_hashes") or None,
                        commit=False,
                    )
                    # The manifest states; the row keeps what it already knows
                    # where the manifest is silent — the same precedence every
                    # other carried column above follows.
                    to_flag = manifest_generated | prior_generated
                    if to_flag:
                        self.mark_files_generated(
                            pkg_id, to_flag, commit=False,
                        )

                if prior_depends:
                    self.add_depends(
                        pkg_id,
                        [(d["name"], d["type"]) for d in prior_depends],
                        commit=False,
                    )
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

            imported += 1

        return imported

    def set_manifest_sha256(self, name, manifest_sha256, commit=True):
        """Record the sha256 of the text-manifest bytes a row was written from.

        Component A keys re-registration on this column: import_manifests
        re-registers whenever the manifest on disk hashes differently from the
        value stored here, and treats NULL as "provenance unproven" — which
        means re-register once. Every path that writes BOTH a row and its text
        manifest therefore has to stamp it, or its rows carry NULL and the
        first corpus-wide import re-registers the entire install set for no
        reason. (Before this existed, that was the state of every row on a
        real installed system: 972 of 972 measured 2026-07-30.)

        Called after the manifest file is written, since the hash is over the
        finished bytes. Returns the number of rows updated; 0 means the
        package is not registered, which the caller can treat as a no-op.
        """
        cur = self.conn.execute(
            "UPDATE installed SET manifest_sha256 = ? WHERE name = ?",
            (manifest_sha256, name),
        )
        if commit:
            self.conn.commit()
        _emit_db_event(
            "set_manifest_sha256", pkg=name, manifest_sha256=manifest_sha256,
        )
        return cur.rowcount

    # ------------------------------------------------------------------
    # Verify
    # ------------------------------------------------------------------

    def verify_package(self, name, strict=True):
        """Verify all files for a package exist on the filesystem.

        Args:
            name: Package name.
            strict: If True (default), check both file existence AND content
                    hash (SHA-256). If False, check existence only — faster
                    but cannot detect tampering or stale content.

        Returns:
            None if package not installed.
            Otherwise dict with:
              - total: file count
              - missing: paths that don't exist on FS
              - modified: paths whose content differs from manifest hash
                          (always empty when strict=False)
              - unverifiable: regular non-config files whose recorded
                          checksum is NULL — a content check was expected
                          but no reference exists, so they can only be
                          existence-checked (always empty when
                          strict=False)
              - undeterminable: owned paths whose state this process was not
                          permitted to establish. Either the path could not
                          be stat'd (a parent directory it may not search,
                          so existence itself is unknown) or the file is
                          present but its bytes could not be read for the
                          content check. NOT missing and NOT verified: the
                          check could not run. A non-root verify over
                          root-only files lands here, and that is the point —
                          it used to report those files as missing
              - expected_absent: owned paths the package's own post_install
                          hook removes/relocates — legitimately absent on a
                          clean install (PI-E4 / Class 4), not "missing"
              - generated: owned paths the package's own archive lifecycle
                          hook CREATED on this machine (D-9). Existence is
                          checked like any other owned path — an absent one
                          still reports missing — but content is not, because
                          the file is regenerated whenever the same cache or
                          index refresh runs again. Reported as its own named
                          bucket so "not content-checked" is visible rather
                          than assumed (always empty when strict=False)
              - superseded_by: name of successor if this package was
                               superseded; None otherwise
        """
        pkg = self.get_installed(name)
        if not pkg:
            return None

        # When superseded, the predecessor's file records were transferred
        # to the successor at supersede time. Surface that explicitly so
        # callers can route queries to the active owner.
        superseded_by = pkg.get("superseded_by")

        # is_generated is selected schema-tolerantly: a read-only open on a
        # DB predating the D-9 migration cannot have run the ALTER, and
        # every row on such a DB is a non-generated row anyway.
        gen_col = ("is_generated"
                   if "is_generated" in getattr(self, "_files_cols", ())
                   else "0")
        rows = self.conn.execute(
            f"SELECT path, is_dir, is_config, checksum, {gen_col} FROM files "
            f"WHERE package_id = ? AND is_dir = 0",
            (pkg["id"],)
        ).fetchall()

        total = len(rows)
        missing = []
        modified = []
        unverifiable = []
        undeterminable = []
        expected_absent = []
        generated = []
        expected_absent_by_class = {}  # Component B: class_id -> [paths]

        for path, is_dir, is_config, expected_checksum, is_generated in rows:
            abs_path = str(self.root / path)
            probe = _probe_path(abs_path)
            if probe == PATH_UNDETERMINABLE:
                # The filesystem refused the question rather than answering
                # it — typically a parent directory this process may not
                # search. The file may be perfectly healthy; we cannot see.
                # Reporting it as missing would be a false alarm about a
                # sound system, which is the whole reason this branch exists.
                undeterminable.append(path)
                continue
            if probe == PATH_ABSENT:
                # A file the build snapshot recorded as owned but that is
                # legitimately absent on a clean install — a package's OWN
                # post_install hook removed/relocated it (PI-E4), or a named,
                # evidence-traced system class removed it (Component B: the ch8
                # .la sweep, volatile /run). Surface it as a distinct
                # "expected-absent" status NAMED with its class, NOT "missing".
                # Any absent owned file that matches NO class still flags
                # missing (no blind drop, never a silent mask).
                cls = _is_expected_absent(name, path)
                if cls:
                    expected_absent.append(path)
                    expected_absent_by_class.setdefault(cls, []).append(path)
                else:
                    missing.append(path)
                continue
            if not strict:
                continue
            # PKM-E3 (Class 2): config files under /etc legitimately diverge after
            # install (the installer/admin edits passwd/shadow/group, os-release,
            # ld.so.cache, …). Like debsums/rpm, verify confirms existence but does
            # NOT content-check conffiles — otherwise every configured system
            # "fails" verify.
            if is_config:
                continue
            # D-9: a file this package's own lifecycle hook created on this
            # machine. Its bytes are regenerated by the next run of the same
            # cache/index refresh — which another package's install triggers
            # routinely — so the hash recorded at creation stops describing
            # a healthy system almost immediately. Existence was already
            # checked above (an absent one still reports missing); content
            # is reported as generated rather than checked, so the exemption
            # is named on the output instead of being a silent skip. This is
            # the durable, per-row form of the static GENERATED_VERIFY_EXEMPT
            # list below, which stays for the pre-D-9 canonical-hook cases.
            if is_generated:
                generated.append(path)
                continue
            # PKM-E2 (Class 1): generated index files post-install hooks rewrite.
            if path in GENERATED_VERIFY_EXEMPT:
                continue
            if not expected_checksum:
                # Hash expected but NULL. A non-config, non-exempt REGULAR
                # file carries a content expectation under strict — passing
                # it on existence alone is indistinguishable from verified,
                # so surface it as unverifiable instead of silently skipping.
                # Symlinks are existence-checked only (no link-target hash is
                # recorded; the target's bytes are its owner's row).
                if os.path.isfile(abs_path) and not os.path.islink(abs_path):
                    unverifiable.append(path)
                continue
            try:
                actual = _sha256(abs_path)
            except OSError:
                # The file is there and a content check was expected, but its
                # bytes could not be read — an unreadable mode, a parent that
                # cannot be searched, an I/O error. This used to `continue`,
                # which counted the file as verified and made a check that
                # never ran indistinguishable from one that passed. Suppressing
                # the unknown is the same dishonesty as calling it missing, so
                # it joins the could-not-determine bucket.
                undeterminable.append(path)
                continue
            if actual == expected_checksum:
                continue
            # PKM-E1 (Class 3, Option 1): a file can be legitimately owned by more
            # than one package — a `-pass2` bootstrap intermediate and its base
            # package both ship the same file, and only the last-installed bytes
            # land on disk, so THIS package's recorded hash can be stale. Accept
            # the file when its live hash matches ANY owning package's recorded
            # checksum for the same path; flag "modified" only when it matches
            # none (genuine tamper/drift — a malicious file matches no owner).
            owned_match = self.conn.execute(
                "SELECT 1 FROM files WHERE path = ? AND is_dir = 0 AND checksum = ? LIMIT 1",
                (path, actual),
            ).fetchone()
            if owned_match:
                continue
            modified.append(path)

        return {
            "total": total,
            "missing": missing,
            "modified": modified,
            "unverifiable": unverifiable,
            "undeterminable": undeterminable,
            "expected_absent": expected_absent,
            "expected_absent_by_class": expected_absent_by_class,
            "generated": generated,
            "superseded_by": superseded_by,
        }

    def reconcile_checksums_from_live(self, commit=True, paths=None):
        """PKM-E (post-install reconcile): re-record non-config file
        checksums from the live filesystem.

        Called by the installer AFTER post-install hooks + signing run, so a file
        mutated *after* its archive hash was recorded — the MOK-signed kernel/UKI,
        a hook-edited .desktop/header/XML-catalog — carries its TRUE installed
        hash and `verify` validates reality. This preserves tamper detection
        (unlike a content exemption, which would blind verify to e.g. kernel
        tampering) and matches pkm's design intent that files.checksum is the
        authoritative live hash — without the reconcile it is recorded too early
        (from the archive manifest, before hooks/signing).

        paths: optional iterable of relative paths restricting the reconcile.
        The installer passes the just-installed package's file list: those
        files were deployed from a verified archive moments ago, so their
        live state is trustworthy NOW — re-recording is safe. An unscoped
        (paths=None) reconcile re-blesses every file on the system as truth,
        which would launder any pre-existing tamper into the DB; reserve it
        for contexts where the whole live tree is known-good (fresh image
        assembly, tests).

        Config files (is_config) are left untouched (content-exempt + routinely
        edited). Absent files are skipped (verify surfaces them). EVERY owning row
        for a path is updated, so a `-pass2` intermediate and its base both
        reflect the final on-disk bytes. Returns the number of file rows updated.
        """
        if paths is None:
            rows = self.conn.execute(
                "SELECT id, path FROM files WHERE is_dir = 0 AND is_config = 0"
            ).fetchall()
        else:
            wanted = [p.rstrip("/") for p in paths if not p.endswith("/")]
            rows = []
            # Chunk the IN clause well under SQLite's default 999-var limit.
            for i in range(0, len(wanted), 500):
                chunk = wanted[i:i + 500]
                marks = ",".join("?" * len(chunk))
                rows.extend(self.conn.execute(
                    f"SELECT id, path FROM files WHERE is_dir = 0 "
                    f"AND is_config = 0 AND path IN ({marks})",
                    chunk,
                ).fetchall())
        updated = 0
        for fid, path in rows:
            abs_path = str(self.root / path)
            if not os.path.isfile(abs_path):  # absent / broken symlink — verify surfaces it
                continue
            try:
                live = _sha256(abs_path)
            except (OSError, PermissionError):
                continue
            self.conn.execute(
                "UPDATE files SET checksum = ? WHERE id = ?", (live, fid)
            )
            updated += 1
        if commit:
            self.conn.commit()
        return updated


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _sha256(filepath):
    """Compute SHA256 of a file."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


# Path-probe outcomes. Three, not two: a check that cannot run must say so
# rather than answer "absent" — see _probe_path.
PATH_PRESENT = "present"
PATH_ABSENT = "absent"
PATH_UNDETERMINABLE = "undeterminable"


def _probe_path(abs_path):
    """Establish whether a path exists, or report that it could not be told.

    os.path.lexists() collapses two very different answers into False: the
    path is not there, and the caller is not permitted to look. A non-root
    verify walking a package whose files live under a mode-700 directory
    gets False for every one of them and reports a healthy system as having
    missing files. Callers therefore need the third answer, so this returns
    one of PATH_PRESENT / PATH_ABSENT / PATH_UNDETERMINABLE.

    ENOENT and ENOTDIR are the only errors that prove absence: the final
    component is not there, or a parent is not a directory, and both are
    answers the kernel gives with full knowledge. Every other error means
    the question was refused, not answered — EACCES/EPERM when a parent
    directory is not searchable, and anything else the filesystem raises —
    so all of them report undeterminable. Guessing "absent" from a refusal
    is the defect this function exists to remove.
    """
    try:
        os.lstat(abs_path)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return PATH_ABSENT
        return PATH_UNDETERMINABLE
    return PATH_PRESENT


def _parse_manifest(content):
    """Parse a text manifest into a dict.

    Tolerates both the original format (path-only file entries) and the
    extended format introduced with the supersedes RFC:

      - Optional "SUPERSEDES: <name>-<version>" header (this package
        replaces another at install time)
      - Optional "SUPERSEDED_BY: <name>-<version>" header (this package
        was retired in favor of another)
      - Optional "<path><SP>sha256:<hex>" file entries

    Old manifests without these fields parse cleanly and return
    file_hashes={}. The hashes here serve as redundant verification —
    pkm's SQLite `files.checksum` column is the authoritative source,
    populated from the live filesystem at install time.
    """
    meta = {}
    files = []
    file_hashes = {}
    in_files = False

    for line in content.splitlines():
        if line.startswith("PACKAGE NAME:"):
            # Format: "PACKAGE NAME: <name>-<version>". The split must land on
            # the LAST hyphen-before-version, not the first. A NON-greedy
            # `(.+?)-(\d.*)` splits "ntfs-3g-2026.2.25" at the "-3g" digit and
            # mis-derives name="ntfs" — the K21.B audit then looks up the wrong
            # /usr/share/licenses/ntfs (vs the real .../ntfs-3g bundle) and the
            # build fails at squashfs. Greedy `(.+)` takes the last
            # hyphen-before-digit = the true version boundary. ntfs-3g is the
            # lone package with a digit immediately after a hyphen in its name;
            # every -pass1/-pass2 variant already parsed correctly.
            full = line.split(":", 1)[1].strip()
            meta["_name_version"] = full
            match = re.match(r'^(.+)-(\d.*)$', full)
            if match:
                meta["name"] = match.group(1)
                meta["version"] = match.group(2)
        elif line.startswith("PACKAGE VERSION:"):
            ver = line.split(":", 1)[1].strip()
            meta["version"] = ver
            # Authoritative name derivation: strip the EXACT known version
            # suffix from the combined PACKAGE NAME field. Belt-and-suspenders
            # over the greedy regex above — robust even to a future hyphenated
            # version, where the regex alone could still guess wrong.
            nv = meta.get("_name_version")
            if nv and ver and nv.endswith("-" + ver):
                meta["name"] = nv[: -(len(ver) + 1)]
        elif line.startswith("PACKAGE RELEASE:"):
            # Component A (release honesty): additive header. Legacy manifests
            # written before this field omit it → meta has no "release" key and
            # import_manifests falls back to the schema default (1). When present
            # it round-trips into installed.release so the DB row is truthful
            # about the release, not silently defaulted.
            rel = line.split(":", 1)[1].strip()
            if rel.isdigit():
                meta["release"] = int(rel)
        elif line.startswith("UNCOMPRESSED SIZE:"):
            size_str = line.split(":", 1)[1].strip()
            # Extract bytes from "12.5M (13107200 bytes)"
            m = re.search(r'\((\d+) bytes\)', size_str)
            if m:
                meta["size"] = int(m.group(1))
        elif line.startswith("BUILD DATE:"):
            meta["build_date"] = line.split(":", 1)[1].strip()
        elif line.startswith("SUPERSEDES:"):
            meta["supersedes"] = line.split(":", 1)[1].strip()
        elif line.startswith("SUPERSEDED_BY:"):
            meta["superseded_by"] = line.split(":", 1)[1].strip()
        elif line.startswith("HOOK-MANAGED:"):
            # D-9b carried into the text manifest. One header line per path:
            # this package's own sealed lifecycle hook CREATED or rewrote the
            # file, so its live content legitimately differs from the payload
            # bytes the manifest records beside it.
            #
            # The row itself could only ever be the SECOND place this is
            # known: `pkm import` rebuilds rows FROM these bytes, so a class
            # recorded only in SQLite is recoverable just for a package the
            # database has already seen. A source-built chroot registers every
            # package for the first time, which is why its rows came out
            # unflagged and the ISO metadata-sync gate byte-compared a correct
            # image against pre-hook bytes and refused it.
            #
            # A HEADER rather than a FILE LIST column on purpose: it sits
            # above `FILE LIST:`, where a reader written before this field
            # existed skips it as an unrecognized header. Marking the entries
            # inline would have appended text to path lines that older parsers
            # read as part of the path.
            claimed = line.split(":", 1)[1].strip()
            if claimed:
                meta.setdefault("generated", []).append(claimed)
        elif line.startswith("DESCRIPTION:"):
            pass  # Next line has the description
        elif ":" in line and not in_files and line.strip().startswith(meta.get("name", "\x00")):
            # Description line: "bash: The GNU Bourne Again shell"
            meta["description"] = line.split(":", 1)[1].strip()
        elif line.strip() == "FILE LIST:":
            in_files = True
        elif in_files and line.strip():
            # Each file entry is "<path>", "<path>/", or "<path> sha256:<hex>".
            # _parse_manifest_line anchors the hash suffix at end-of-line so
            # paths containing whitespace parse correctly.
            path, h = _parse_manifest_line(line)
            files.append(path)
            if h is not None:
                file_hashes[path] = h

    meta["files"] = files
    meta["file_hashes"] = file_hashes
    meta.setdefault("generated", [])
    return meta if "name" in meta else None
