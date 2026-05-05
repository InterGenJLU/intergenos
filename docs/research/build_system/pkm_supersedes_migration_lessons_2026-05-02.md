# pkm Supersedes-RFC Migration — Engineering Lessons (Phase 5 post-mortem)

**Date:** 2026-05-02 (event); 2026-05-05 (post-mortem written)
**Status:** Migration script (v1 → v5) developed but never run end-to-end against a real chroot. The `Supersedes RFC v1` schema landed in master via clean rebuild rather than via this migration. This document preserves the design knowledge for the next pkm schema migration.

---

## What this is

When the `Supersedes RFC v1` (master `c9534f7`, 2026-05-01) added two new columns (`superseded_by`, `superseded_at`) to pkm's SQLite DB plus a new `SUPERSEDES:` manifest header, two upgrade paths existed:

- **Path A — migrate.** Take an already-built system, run a one-time script that adds the columns and back-populates them from existing manifest content + a known list of supersede pairs.
- **Path D — rebuild.** Throw the chroot away. Restore from a pre-RFC snapshot. Build from scratch with the new code. Schema is created fresh.

Path A produced a five-iteration script (`scripts/migrate-pkm-supersedes.sh` v1 → v5) developed across 2026-05-01 and 2026-05-02. **Path D was authorized at 14:22Z on 2026-05-02** after the chroot's pre-state probe revealed `Apr-10-era state in chroot + 0-byte chroot pkm.db incoherent with 261 manifests on disk`. The migration script was kept on a working branch as forward-facing capability, but never validated end-to-end against a real chroot.

Owner's framing of the trust calculus at decision time: *"clean rebuild = trust the build system itself; migration = trust a script + assume manifest coherence."*

This document captures the engineering knowledge from the migration work, so the next schema migration in pkm doesn't re-derive every footgun from scratch.

---

## Five lessons distilled (ordered by leverage)

### 1. Don't call chroot's python from the host shell

**The trap.** Invoking `${CHROOT}/usr/bin/python3` from the HOST shell looks like it should work — same architecture, executable bit set, ELF interpreter present. But when the host kernel `execve()`'s the binary, dynamic-linker resolution uses the chroot python's `RPATH` / `RUNPATH`, which points to chroot-local library paths (`/usr/lib/...` interpreted from the host's POV, NOT chroot's). Result: `libpython3.14.so.1.0: cannot open shared object file`.

**The fix.** Use HOST python3 directly. The migration script needs to read/write files inside the chroot's filesystem, not run inside the chroot. `sqlite3` stdlib doesn't care about chroot context — it just opens files at given paths. Pass the chroot DB's path as a file argument; the host python3 process reads/writes it through normal filesystem syscalls.

```bash
PYTHON="${PYTHON:-/usr/bin/python3}"   # default to host
"${PYTHON}" -c "import sqlite3; db = sqlite3.connect('${CHROOT}/path/to/pkm.db'); ..."
```

If you genuinely need code to RUN inside the chroot (e.g., to invoke chroot-installed binaries with their PATH/env), use `chroot ${CHROOT} /usr/bin/python3 ...` — that does a proper `chroot()` syscall first so the dynamic linker resolves correctly.

### 2. Detect stubs by content shape, not file size

**The trap.** "If a manifest is < 100 bytes, treat it as a stub" sounds reasonable. Reality: manifest metadata headers (PACKAGE NAME, VERSION, BUILD DATE, BUILT BY, etc.) can push a logically-empty manifest (one with a 65-byte file-list body that should classify as a stub) to 300+ bytes. The size heuristic produces both false negatives (real stubs missed because their headers are verbose) and false positives (legitimate small packages flagged as stubs).

**The fix.** Parse the manifest content. Count entries in the `FILE LIST:` section.

```python
in_files = False
count = 0
for line in body.splitlines():
    if 'FILE LIST:' in line:
        in_files = True
        continue
    if in_files and line.strip():
        count += 1
is_stub = count <= 1
```

This generalizes: any time you're tempted to use a size threshold as a proxy for "structural emptiness," parse the structure instead. Sizes drift; structure is invariant.

### 3. Idempotency by per-step pre-check, not by transaction wrapping

**The pattern.** Every step checks current state before acting:

| Step | Pre-check |
|---|---|
| 1. Verify prerequisites | `[[ -d "${CHROOT}/usr" ]]`, `[[ -f "${DB_PATH}" ]]` |
| 2. Import manifests → SQLite | `SELECT id FROM installed WHERE name=?` — skip if already-present |
| 3. Handle linux-kernel-pass2 stub | Parse FILE LIST count — skip if not a stub |
| 4. Apply supersede transactions | `WHERE superseded_by IS NULL` — skip pairs already transitioned |
| 5. Regenerate text manifests | `grep -q 'sha256:'` — skip manifests already with hash columns |

Re-running the script on a partially-completed migration is safe by construction. Each step is a no-op if its target state is already achieved.

This is preferable to a single-transaction wrapper because:
- Failure mid-migration leaves the DB in a known-good intermediate state, not "rolled all the way back to nothing."
- Operator can inspect state between steps without breaking atomicity.
- Re-runs after a fix are fast (most steps no-op).

### 4. Pre-state probe ALWAYS before any destructive migration

**The 2026-05-02 lesson.** The pre-state probe revealed `Apr-10-era state in chroot + 0-byte chroot pkm.db incoherent with 261 manifests on disk`. Without that probe, the migration would have run on garbage state and produced a still-garbage outcome that LOOKED correct (the script reports success because each step's no-op-or-act logic doesn't validate that the resulting DB is coherent overall).

**The pattern.** Before any schema migration on a real system, run a probe script that reports:
- DB schema version (which columns exist?)
- Row count in each table
- Manifest count on disk
- Coherence check: every manifest on disk has a matching `installed` row, and vice versa
- Last-modified date of the DB vs the most recent manifest

If the pre-state shows incoherence (DB row count != manifest count, etc.), STOP. Don't migrate; investigate. The 5/2 chroot-state mismatch was the canary that surfaced the fact that the chroot was a stale Apr-10 snapshot, not the working build state. Migration would have buried that.

### 5. The trust calculus: when to rebuild instead of migrate

For complex schema migrations on already-built systems, ask:
- Is the rebuild cost bounded? (For pkm/InterGenOS: yes — a snapshot restore + clean build is a known sequence.)
- Is the migration script validated? (For Phase 5 v5: no — never run end-to-end against real coherent state.)
- Does the migration assume manifest coherence? (Yes — the import-manifests step assumes manifests-on-disk == ground truth.)
- Is the source-of-truth side (manifests) authoritative, or does the DB hold information not derivable from manifests? (For supersedes-RFC: manifests are authoritative for file-level data, but the supersede pairs themselves were a hardcoded list in the script.)

When the rebuild path is bounded AND the migration introduces a new attack surface (untested script + assumption-laden state reconciliation), rebuild wins on a security-only-alignment project. The 5/2 decision was: rebuild is one moving part (the build system, well-exercised); migration is two moving parts (script logic + state assumptions).

For end-user systems where rebuild ISN'T bounded (multiple installed machines, user data risk, no snapshot), migration becomes the only path. The lesson: validate it on a clean test bench BEFORE shipping. Untested migrations applied to user systems are how distros lose user trust in one bad release.

---

## Capability snapshot (what the v5 script does)

For future reference, the v5 final-form script's behavior, in case the next schema migration needs to start from this template:

| Step | Function |
|---|---|
| 1 | Verify prereqs: chroot exists at given path, pkm DB exists, manifest dir exists. |
| 2 | Walk `${MANIFEST_DIR}/*.igo_manifest`, extract package name from `PACKAGE NAME:` header, INSERT OR IGNORE into `installed` table. For each file line in manifest, INSERT OR REPLACE into `files` table with `(package_id, path, checksum)`. Skips packages already in DB. |
| 3 | Detect `linux-kernel-pass2` stub via FILE LIST count parser; if stub: delete the stub manifest, write the package name to `/tmp/migrate-pkm-rebuilds.txt` for operator follow-up. |
| 4 | For each known supersede pair (hardcoded list of 8 pairs covering pass1/pass2 cycles): inside a SQLite `BEGIN TRANSACTION` block, transfer file ownership from pass1's `package_id` to pass2's `package_id` for any path overlap, then `UPDATE installed SET superseded_by=?, superseded_at=? WHERE id=?` for pass1's row. |
| 5 | For each manifest without `sha256:` entries, regenerate the file-list section from DB rows so manifest matches DB content. |

Code source: never landed on master; lived on a working branch (retired 2026-05-05). If a future migration needs the SQL transaction shape or the manifest-parsing helpers, reach via `git log --all` history from the May 1–5 window.

---

## Iteration trail (for the historical record)

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-01 | Initial 5-step prototype. |
| v2 | 2026-05-01 | Owner reviewer (post-quorum) caught a bash quoting bug; v2 issued same day. |
| v3 | 2026-05-01 | v2 ratified version; this is what landed on the working branch. |
| v4 | 2026-05-02 | Attempted production run; halted at 14:06Z on chroot-python `libpython3.14.so.1.0` linker bug. |
| v5 | 2026-05-02 14:20Z | Host-python3 fix + per-file-count stub detection (replacing size threshold). 14 min from halt-alert to fix push. Never run; superseded by Path D authorization. |

---

## Implications for future pkm schema work

The next time pkm's schema needs to evolve (new column, table, format change), expect to need:

1. **A migration script.** Same shape as v5 — idempotent steps, pre-state probe, host-python invocation, content-not-size detection. Use the v5 structure as a template; do not assume the v5 code itself is correct for the new schema.
2. **Validation against a clean test bench.** Build a test pkm DB, run migration, verify schema and data. This is the step v5 never got. If you're not validating, you're shipping potential damage.
3. **Pre-state probe as a separate script.** Detached from the migration so operators can run "is my system in a state where migration will work?" without committing to running the migration. The 5/2 lesson is that incoherent pre-state will silently swallow a migration's "success."
4. **A rebuild fallback path documented up-front.** If migration validation fails or the user's system is too divergent, what's the rebuild story? For build-VM scenarios, snapshot restore. For installed user systems, it's harder; budget for that explicitly.

The Phase 5 work was good engineering that landed at the wrong moment. The lessons survive even though the script doesn't.
