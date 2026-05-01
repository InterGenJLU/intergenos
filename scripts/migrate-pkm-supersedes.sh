#!/usr/bin/env bash
# migrate-pkm-supersedes-v1.sh — InterGenOS pkm supersede migration
# Phase 5 prototype — DS, 2026-05-01
# Idempotent. Re-run safe. Run on build VM with chroot active.
#
# Usage:
#   sudo bash migrate-pkm-supersedes-v1.sh /mnt/igos
#
# Performs 5 idempotent steps:
#   1. Verify prerequisites (chroot, DB, tracker)
#   2. Import manifests into SQLite if not yet populated
#   3. Handle linux-kernel-pass2 65-byte stub (delete + flag for rebuild)
#   4. Apply supersede transactions for known pairs
#   5. Regenerate text manifests with hash columns (parity with DB)
#   6. Report completion + next steps

set -euo pipefail

CHROOT="${1:-/mnt/igos}"
DB_PATH="${CHROOT}/var/lib/igos/packages/pkm.db"
MANIFEST_DIR="${CHROOT}/var/lib/igos/packages"
SUPERSEDE_PAIRS="lame|lame-pass2 linux-kernel|linux-kernel-pass2 libtiff|libtiff-pass2 gdk-pixbuf|gdk-pixbuf-pass2 gst-plugins-base|gst-plugins-base-pass2 freetype2-pass1|freetype2 pyyaml|pyyaml-pass2 systemd|systemd-pass2"
PYTHON="${CHROOT}/usr/bin/python3"

log() { echo "[migrate $(date +%H:%M:%S)] $*"; }
fail() { log "ERROR: $*"; exit 1; }

# ----------------------------------------------------------------------
# Step 1: Prerequisites
# ----------------------------------------------------------------------
log "Step 1/5: Verifying prerequisites..."

[[ -d "${CHROOT}/usr" ]]      || fail "chroot not found at ${CHROOT}"
[[ -f "${DB_PATH}" ]]         || fail "pkm DB not found at ${DB_PATH}"

log "  chroot: ${CHROOT}"
log "  pkm DB: ${DB_PATH}"

# ----------------------------------------------------------------------
# Step 2: Import manifests → SQLite (idempotent)
# ----------------------------------------------------------------------
log "Step 2/5: Importing manifests into SQLite..."

PENDING=""
for manifest in "${MANIFEST_DIR}"/*.igo_manifest; do
    pkg_name=$(grep "^PACKAGE NAME:" "${manifest}" 2>/dev/null | head -1 | awk '{print $3}' | sed 's/-[0-9].*//')
    if [ -z "${pkg_name}" ]; then
        log "  SKIP ${manifest} — no PACKAGE NAME header found"
        continue
    fi

    if "${PYTHON}" -c "
import sqlite3, sys
db = sqlite3.connect('${DB_PATH}')
cur = db.execute('SELECT id FROM installed WHERE name=?', ('${pkg_name}',))
row = cur.fetchone()
sys.exit(0 if row else 1)
" 2>/dev/null; then
        log "  SKIP ${pkg_name} — already in SQLite"
        continue
    fi
    PENDING="${PENDING} ${manifest}"
done

if [[ -n "${PENDING}" ]]; then
    log "  Importing $(echo ${PENDING} | wc -w) pending manifests..."
    for manifest in ${PENDING}; do
        pkg_name=$(basename "${manifest}" .igo_manifest)
        pkg_name="${pkg_name%-*}"

        "${PYTHON}" -c "
import sqlite3
db = sqlite3.connect('${DB_PATH}')
# Read text manifest
with open('${manifest}') as f:
    lines = f.read().strip().split('\n')
db.execute('INSERT OR IGNORE INTO installed (name, version) VALUES (?, ?)',
           ('${pkg_name}', '0.0.0'))
pkg_id = db.execute('SELECT id FROM installed WHERE name=?', ('${pkg_name}',)).fetchone()[0]
for line in lines:
    if not line or line.startswith('#') or line.startswith('SUPERSEDES:'):
        continue
    parts = line.split()
    path = parts[0]
    chksum = parts[1].replace('sha256:', '') if len(parts) > 1 and 'sha256:' in parts[1] else None
    db.execute('INSERT OR REPLACE INTO files (package_id, path, checksum) VALUES (?, ?, ?)',
               (pkg_id, path, chksum))
db.commit()
" 2>/dev/null || log "  WARN: import failed for ${manifest} (may need manual triage)"
    done
else
    log "  All manifests already in SQLite — skip"
fi

# ----------------------------------------------------------------------
# Step 3: Handle linux-kernel-pass2 stub (idempotent)
# ----------------------------------------------------------------------
log "Step 3/5: Checking linux-kernel-pass2 state..."

KP2_STUB="${MANIFEST_DIR}/linux-kernel-pass2*"
if compgen -G "${KP2_STUB}" >/dev/null 2>&1; then
    KP2_MANIFEST=$(compgen -G "${KP2_STUB}" | head -1)
    KP2_SIZE=$(stat -c%s "${KP2_MANIFEST}" 2>/dev/null || echo 0)

    if [[ "${KP2_SIZE}" -lt 100 ]]; then
        log "  linux-kernel-pass2 manifest is ${KP2_SIZE}-byte stub — removing"
        rm -f "${KP2_MANIFEST}"
        "${PYTHON}" -c "
import sqlite3
db = sqlite3.connect('${DB_PATH}')
db.execute('DELETE FROM files WHERE package_id = (SELECT id FROM installed WHERE name=\"linux-kernel-pass2\")')
db.execute('DELETE FROM installed WHERE name=\"linux-kernel-pass2\"')
db.commit()
" 2>/dev/null || log "  WARN: DB cleanup for linux-kernel-pass2 failed (may need manual triage)"
        log "  linux-kernel-pass2: DELETED. Must be rebuilt under new tracker."
        echo "REBUILD_NEEDED=linux-kernel-pass2" > /tmp/migrate-pkm-rebuilds.txt
    else
        log "  SKIP linux-kernel-pass2 — manifest is ${KP2_SIZE} bytes (not a stub)"
    fi
else
    log "  SKIP linux-kernel-pass2 — no manifest found"
fi

# ----------------------------------------------------------------------
# Step 4: Apply supersede transactions (idempotent)
# ----------------------------------------------------------------------
log "Step 4/5: Applying supersede transactions..."

# Check if superseded_by column exists; add if not
"${PYTHON}" -c "
import sqlite3
db = sqlite3.connect('${DB_PATH}')
try:
    db.execute('SELECT superseded_by FROM installed LIMIT 1')
except sqlite3.OperationalError:
    db.execute('ALTER TABLE installed ADD COLUMN superseded_by TEXT')
    db.execute('ALTER TABLE installed ADD COLUMN superseded_at TEXT')
    db.commit()
    print('  Added superseded_by + superseded_at columns')
" 2>/dev/null || true

for pair in ${SUPERSEDE_PAIRS}; do
    PASS1="${pair%%|*}"
    PASS2="${pair##*|}"

    # Idempotency: if pass1 already superseded, skip
    ALREADY=$("${PYTHON}" -c "
import sqlite3
db = sqlite3.connect('${DB_PATH}')
row = db.execute('SELECT superseded_by FROM installed WHERE name=?', ('${PASS1}',)).fetchone()
print(row[0] if row and row[0] else 'NONE')
" 2>/dev/null)

    if [[ "${ALREADY}" != "NONE" ]]; then
        log "  SKIP ${PASS1} — already superseded by ${ALREADY}"
        continue
    fi

    # Check both are in DB
    PASS1_ID=$("${PYTHON}" -c "
import sqlite3
db = sqlite3.connect('${DB_PATH}')
row = db.execute('SELECT id FROM installed WHERE name=?', ('${PASS1}',)).fetchone()
print(row[0] if row else 'NONE')
" 2>/dev/null)

    PASS2_ID=$("${PYTHON}" -c "
import sqlite3
db = sqlite3.connect('${DB_PATH}')
row = db.execute('SELECT id FROM installed WHERE name=?', ('${PASS2}',)).fetchone()
print(row[0] if row else 'NONE')
" 2>/dev/null)

    if [[ "${PASS1_ID}" == "NONE" ]]; then
        log "  SKIP ${PASS1}→${PASS2} — ${PASS1} not in DB"
        continue
    fi
    if [[ "${PASS2_ID}" == "NONE" ]]; then
        log "  SKIP ${PASS1}→${PASS2} — ${PASS2} not in DB"
        continue
    fi

    # Apply supersede transaction
    NOW=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    "${PYTHON}" -c "
import sqlite3
db = sqlite3.connect('${DB_PATH}')
db.execute('BEGIN TRANSACTION')

# Transfer file records pass1 wrote that pass2 also wrote (by path overlap)
paths = db.execute('''
    SELECT f1.path, f2.checksum
    FROM files f1
    JOIN files f2 ON f1.path = f2.path
    WHERE f1.package_id = ${PASS1_ID}
      AND f2.package_id = ${PASS2_ID}
''').fetchall()

for path, cksum in paths:
    db.execute('UPDATE files SET package_id = ?, checksum = COALESCE(?, checksum) WHERE path = ? AND package_id = ?',
               (${PASS2_ID}, cksum, path, ${PASS1_ID}))

# Mark pass1 superseded
db.execute('UPDATE installed SET superseded_by = ?, superseded_at = ? WHERE id = ?',
           ('${PASS2}', '${NOW}', ${PASS1_ID}))

db.commit()
print(f'  {len(paths)} paths transferred from {PASS1} to {PASS2}')
" 2>/dev/null || log "  WARN: supersede transaction for ${PASS1}→${PASS2} failed"
done

# ----------------------------------------------------------------------
# Step 5: Regenerate text manifests (parity with DB)
# ----------------------------------------------------------------------
log "Step 5/5: Regenerating text manifests with hash columns..."

for manifest in "${MANIFEST_DIR}"/*.igo_manifest; do
    pkg_name=$(basename "${manifest}" .igo_manifest)
    pkg_name="${pkg_name%-*}"

    # Skip if manifest already has sha256: entries
    if grep -q 'sha256:' "${manifest}" 2>/dev/null; then
        log "  SKIP ${pkg_name} — already has hash columns"
        continue
    fi

    "${PYTHON}" -c "
import sqlite3, os
db = sqlite3.connect('${DB_PATH}')
row = db.execute('SELECT id FROM installed WHERE name=?', ('${pkg_name}',)).fetchone()
if not row:
    exit(0)
pkg_id = row[0]
files = db.execute('SELECT path, checksum FROM files WHERE package_id=? AND checksum IS NOT NULL ORDER BY path', (pkg_id,)).fetchall()

# Read existing manifest header (everything before FILE LIST:)
header = []
with open('${manifest}') as f:
    for line in f:
        header.append(line)
        if 'FILE LIST:' in line:
            break

# Write header + file list with hashes
with open('${manifest}', 'w') as f:
    for h in header:
        f.write(h)
    for path, cksum in files:
        f.write(f'{path} sha256:{cksum}\n')
" 2>/dev/null && log "  Updated ${pkg_name} (${#files[@]} entries with hashes)" || true
done

# ----------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------
log "=== MIGRATION COMPLETE ==="

# Show supersede state
"${PYTHON}" -c "
import sqlite3
db = sqlite3.connect('${DB_PATH}')
rows = db.execute('SELECT name, superseded_by, superseded_at FROM installed WHERE superseded_by IS NOT NULL').fetchall()
if rows:
    for name, by, at in rows:
        print(f'  {name} was superseded by {by} at {at}')
else:
    print('  No supersede pairs recorded yet')
"

if [[ -f /tmp/migrate-pkm-rebuilds.txt ]]; then
    echo ""
    echo "  REBUILD REQUIRED: $(cat /tmp/migrate-pkm-rebuilds.txt)"
fi

echo ""
echo "  Next steps:"
echo "  1. Rebuild packages listed as REBUILD REQUIRED"
echo "  2. SPOC runs Phase 1-3 (tracker SQLite write-through)"
echo "  3. Rebuild linux-kernel-pass2 after tracker update lands"
echo "  4. Re-run this migration script after rebuilds complete"
echo "  5. Verify with: pkm verify --strict <package>"

exit 0
