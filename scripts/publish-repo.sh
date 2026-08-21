#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# publish-repo.sh — Publish binary repository to repo.intergenos.org
#
# E1.B.7 publish orchestrator. Wraps pkm.repo.generate_index() +
# sign_index() library functions and rsyncs to the remote repo.
#
# Usage:
#   scripts/publish-repo.sh                          # default archive dir + remote
#   scripts/publish-repo.sh --dry-run                # check what WOULD be published
#   scripts/publish-repo.sh --archive-dir /path/to/  # custom archive dir
#   scripts/publish-repo.sh --gpg-key S2             # sign with backup key
#
# Environment overrides:
#   PUBLISH_REMOTE_USER       (default: intergenos)
#   PUBLISH_REMOTE_HOST       (default: origin.intergenstudios.com)
#   PUBLISH_REMOTE_PORT       (default: 2200 — the VPS sshd port, not 22)
#   PUBLISH_REMOTE_PATH       (default: /home/intergenos/repo/x86_64)
#   PUBLISH_SOURCES_DIR       (default: build/sources-archives — where
#                              build-source-archives.py emits .igos.src.tar.gz
#                              corresponding-source archives for each package)
#
# Prerequisites:
#   - Packages built and archived at /var/lib/igos/archives/
#   - SSH key auth to origin.intergenstudios.com configured
#   - NK#1 (or NK#2) release key available to GPG
#   - Source archives generated via scripts/build-source-archives.py
#     (the source-availability commitment in SOURCES.md §6d depends on
#     these landing in <host>/x86_64/current/sources/ alongside the binaries)
set -e -o pipefail

ARCHIVE_DIR="/var/lib/igos/archives"
SOURCES_DIR="${PUBLISH_SOURCES_DIR:-build/sources-archives}"
REMOTE_USER="${PUBLISH_REMOTE_USER:-intergenos}"
REMOTE_HOST="${PUBLISH_REMOTE_HOST:-origin.intergenstudios.com}"
REMOTE_PORT="${PUBLISH_REMOTE_PORT:-2200}"
REMOTE_PATH="${PUBLISH_REMOTE_PATH:-/home/intergenos/repo/x86_64}"
GPG_KEY="NK1"
DRY_RUN=false
SKIP_SOURCES=false
SKIP_TRANSPARENCY=false
SKIP_SIGN=false
CHROOT_MANIFEST=""

# Retention: how many archived snapshots to keep under _previous/ after a
# promote. The promote moves the outgoing current/ target into _previous/;
# without a prune those accumulate forever. Each generation costs roughly a
# full source tree (~24 G) once its hardlinks stop being shared, which is how
# a 150 G volume reached 78% used with only two live publishes on it
# (2026-07-24). 1 = keep exactly one rollback generation.
KEEP_PREVIOUS="${PUBLISH_KEEP_PREVIOUS:-1}"

# Capacity gate: refuse to start a publish that would land the remote below
# this free-space percentage. The scheduled cPanel backup refuses to run under
# its own MIN_FREE_SPACE (25% as configured), so a publish that silently eats
# past it converts into a failed-backup mail days later, far from its cause.
# Fail-closed by design; --accept-capacity-risk is the explicit override.
MIN_FREE_PCT="${PUBLISH_MIN_FREE_PCT:-25}"
ACCEPT_CAPACITY_RISK=false

# Transparency log substrate (L-024). Each publish appends a structured
# commit to this git repo containing the signed-index sha256, size, and
# signer fingerprint. Git provides the append-only property assuming the
# remote master branch is configured for force-push protection. The
# audit row L-024 proposed git-repo-as-append-only-log as the minimum
# viable implementation; Sigstore Rekor v2 integration is queued as
# v1.1 enhancement (second attestation target, not a replacement).
TRANSPARENCY_GIT_REMOTE="${PUBLISH_TRANSPARENCY_REMOTE:-git@github.com:InterGenJLU/intergenos-mirror-backup.git}"
TRANSPARENCY_LOCAL="${PUBLISH_TRANSPARENCY_LOCAL:-$HOME/.intergenos-transparency-log}"

# Release key fingerprints
declare -A GPG_KEY_FPS
GPG_KEY_FPS[NK1]="D7AA641D81ACD690C5AD865E7276E14DD8886BFE"
GPG_KEY_FPS[NK2]="81DD223F9BA9B3F2AFBFFC5AFA24B042975F775E"
GPG_KEY_FPS[S1]="D7AA641D81ACD690C5AD865E7276E14DD8886BFE"
GPG_KEY_FPS[S2]="81DD223F9BA9B3F2AFBFFC5AFA24B042975F775E"

usage() {
    cat <<EOF
Usage: $0 [--dry-run] [--archive-dir DIR] [--gpg-key NK1|NK2] [--skip-sources]

  --dry-run        Show what would be uploaded; don't actually publish. Generates
                   the index locally for preview but SIGNS NOTHING and uploads
                   nothing — it never invokes the Nitrokey (no PIN/touch).
  --archive-dir    Override binary archive directory (default: $ARCHIVE_DIR).
  --gpg-key        Sign with NK1 (primary) or NK2 (backup). Default: NK1.
  --skip-sources   Emergency override — publish binaries without their
                   corresponding-source archives. Use only when source
                   generation is a known follow-on (not normal flow);
                   defaults to fail-closed so binary publish always
                   accompanies its SOURCES.md §6d source-availability
                   commitment.
  --skip-transparency
                   Emergency override — publish without appending to the
                   transparency-log git repo. Use only when the log
                   substrate is genuinely unavailable; defaults to
                   fail-closed so every published index is recorded in
                   the append-only public log (L-024).
  --skip-sign      Resume an interrupted publish: skip index generation +
                   signing and REUSE the existing signed InterGenOS.db +
                   .sig in the archive dir (verified before upload). For
                   recovering from a post-sign failure (rsync/promote)
                   WITHOUT re-doing the Nitrokey ceremony.
  --keep-previous N
                   Archived snapshots to retain under _previous/ after the
                   promote (default: $KEEP_PREVIOUS). Older ones are pruned.
                   0 disables retention entirely; the live current/ target is
                   never touched by the prune.
  --accept-capacity-risk
                   Proceed even when the projected post-publish free space
                   falls below ${MIN_FREE_PCT}% on the remote. Without this the
                   capacity preflight fails closed — publishing past the
                   backup threshold surfaces days later as a failed backup.
  --chroot-manifest FILE
                   REQUIRED for a signing publish. sha256sum output taken
                   inside the build chroot's archive dir — the corpus gate
                   proves every staged archive byte-identical to the built
                   corpus, both directions, before the index is generated
                   (decided 2026-08-21: staging derives from the evaluated
                   corpus; the persistent-staging overlay is retired). No
                   bypass exists. --skip-sign resumes reuse the already-
                   gated index and skip the re-check.
EOF
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)         DRY_RUN=true; shift ;;
        --archive-dir)     ARCHIVE_DIR="$2"; shift 2 ;;
        --gpg-key)         GPG_KEY="$2"; shift 2 ;;
        --skip-sources)    SKIP_SOURCES=true; shift ;;
        --skip-transparency) SKIP_TRANSPARENCY=true; shift ;;
        --skip-sign)       SKIP_SIGN=true; shift ;;
        --keep-previous)   KEEP_PREVIOUS="$2"; shift 2 ;;
        --accept-capacity-risk) ACCEPT_CAPACITY_RISK=true; shift ;;
        --chroot-manifest) CHROOT_MANIFEST="$2"; shift 2 ;;
        -h|--help)         usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

if [ ! -d "$ARCHIVE_DIR" ]; then
    echo "ERROR: Archive directory does not exist: $ARCHIVE_DIR" >&2
    exit 1
fi

GPG_FP="${GPG_KEY_FPS[$GPG_KEY]}"
if [ -z "$GPG_FP" ]; then
    echo "ERROR: Unknown GPG key: $GPG_KEY (valid: NK1, NK2)" >&2
    exit 1
fi

COUNT=$(ls "$ARCHIVE_DIR"/*.igos.tar.gz 2>/dev/null | wc -l)

echo "=== InterGenOS Repository Publish ==="
echo "Archive dir: $ARCHIVE_DIR"
echo "Packages:    $COUNT .igos.tar.gz files"
echo "GPG key:     $GPG_KEY ($GPG_FP)"
echo "Remote:      $REMOTE_USER@$REMOTE_HOST:$REMOTE_PATH"
echo ""

# Preflight checks — fail-fast before expensive operations
echo "[preflight] Checking SSH connectivity..."
ssh -p "$REMOTE_PORT" -o BatchMode=yes -o ConnectTimeout=10 \
    "${REMOTE_USER}@${REMOTE_HOST}" true \
    || { echo "ERROR: SSH auth to ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PORT} failed" >&2; exit 1; }
echo "  OK — SSH reachable"

echo "[preflight] Checking GPG key availability..."
gpg --list-secret-keys "$GPG_FP" >/dev/null 2>&1 \
    || { echo "ERROR: GPG key $GPG_KEY ($GPG_FP) not available" >&2; exit 1; }
echo "  OK — GPG key available"

# ---------------------------------------------------------------------------
# Remote snapshot layout — resolved ONCE, consumed by both the capacity
# preflight below and the --link-dest hardlink sources at upload time.
#
# Every published snapshot on the remote is a hardlink farm: current/, the
# archived generations under _previous/, and any staging dir left behind by an
# interrupted run all hold the same archive bytes. rsync accepts up to 20
# --link-dest directories and takes the first match, so feeding it every
# snapshot — not just current/ — means an archive already anywhere on that
# volume is hardlinked instead of re-sent.
#
# This is not a micro-optimisation. On 2026-07-24 the live current/sources
# held 5 archives while a _previous/ generation on the SAME disk held 1,229,
# and a current/-only --link-dest re-sent ~24 G that was already there.
# ---------------------------------------------------------------------------
echo "[preflight] Resolving remote snapshot layout..."
REMOTE_LAYOUT=$(ssh -p "$REMOTE_PORT" -o BatchMode=yes "${REMOTE_USER}@${REMOTE_HOST}" \
    bash -s -- "$REMOTE_PATH" <<'SSHEOF' || true
LIVE="$1"
if [ -L "$LIVE/current" ]; then readlink -f "$LIVE/current" 2>/dev/null || true; fi
for d in "$LIVE"/_previous/*/ "$LIVE"/_staging-*/; do
    [ -d "$d" ] && printf '%s\n' "${d%/}"
done 2>/dev/null || true
SSHEOF
)

# Deduplicate, preserving order: current/ first (most likely to match), then
# the archived + leftover snapshots. Cap at 20 (rsync's --link-dest limit).
LINK_DEST_CANDS=()
declare -A _seen_cand=()
while IFS= read -r _cand; do
    [ -n "$_cand" ] || continue
    [ -n "${_seen_cand[$_cand]:-}" ] && continue
    _seen_cand[$_cand]=1
    [ ${#LINK_DEST_CANDS[@]} -ge 20 ] && continue
    LINK_DEST_CANDS+=("$_cand")
done <<< "$REMOTE_LAYOUT"
echo "  OK — ${#LINK_DEST_CANDS[@]} snapshot(s) available as hardlink sources"

# ---------------------------------------------------------------------------
# Capacity preflight (fail-closed) — turns "check the disk first" from a
# runbook instruction a human may skip into a gate the tool enforces.
#
# Projection method: a local archive is assumed to hardlink (cost 0) when a
# file of the SAME BASENAME AND SIZE already exists in some snapshot; anything
# else is counted as a full transfer. This is an ESTIMATE — rsync matches on
# content under --checksum, so a same-name/same-size but byte-different
# archive is counted as free here when it will actually transfer. The
# estimate is therefore a LOWER bound on bytes moved, and it is stated as
# such rather than presented as exact. It reliably catches the failure mode
# that matters (a snapshot missing whole archives → full re-upload), which is
# what put the volume under its backup threshold.
# ---------------------------------------------------------------------------
echo "[preflight] Projecting post-publish remote capacity..."
_CAP_INV=$(mktemp); _CAP_DF=$(mktemp)
trap 'rm -f "$_CAP_INV" "$_CAP_DF"' EXIT
{
    ssh -p "$REMOTE_PORT" -o BatchMode=yes "${REMOTE_USER}@${REMOTE_HOST}" \
        bash -s -- "$REMOTE_PATH" "${LINK_DEST_CANDS[@]}" <<'SSHEOF' > "$_CAP_INV"
LIVE="$1"; shift
df -Pk "$LIVE" | tail -1 | awk '{print "DF\t" $2 "\t" $4}'
for d in "$@"; do
    [ -d "$d" ] && find "$d" -maxdepth 2 -type f -printf 'F\t%f\t%s\n' 2>/dev/null
done
SSHEOF
} || { echo "ERROR: capacity preflight could not read remote state" >&2; exit 1; }

_CAP_RC=0
python3 - "$_CAP_INV" "$ARCHIVE_DIR" "$SOURCES_DIR" "$MIN_FREE_PCT" "$SKIP_SOURCES" <<'PYEOF' || _CAP_RC=$?
import os, sys

inv_path, archive_dir, sources_dir, min_free_pct, skip_sources = sys.argv[1:6]
min_free_pct = float(min_free_pct)
skip_sources = (skip_sources == "true")

total_kb = avail_kb = 0
have = set()                      # (basename, size) already on the remote volume
with open(inv_path) as fh:
    for line in fh:
        parts = line.rstrip("\n").split("\t")
        if parts[0] == "DF" and len(parts) == 3:
            total_kb, avail_kb = int(parts[1]), int(parts[2])
        elif parts[0] == "F" and len(parts) == 3:
            have.add((parts[1], int(parts[2])))

if not total_kb:
    print("  ERROR: could not read remote df", file=sys.stderr)
    sys.exit(2)

def local_files():
    yield from (os.path.join(archive_dir, f) for f in os.listdir(archive_dir)
                if f.endswith(".igos.tar.gz"))
    if not skip_sources and os.path.isdir(sources_dir):
        yield from (os.path.join(sources_dir, f) for f in os.listdir(sources_dir)
                    if f.endswith(".igos.src.tar.gz"))

xfer = linked = 0
for path in local_files():
    try:
        size = os.path.getsize(path)
    except OSError:
        continue
    if (os.path.basename(path), size) in have:
        linked += size
    else:
        xfer += size

G = 1024 ** 3
avail_b = avail_kb * 1024
total_b = total_kb * 1024
post_b = avail_b - xfer
post_pct = (post_b / total_b) * 100 if total_b else 0.0

print(f"  hardlinkable: {linked/G:6.1f} G   projected transfer: {xfer/G:6.1f} G (lower bound)")
print(f"  remote free:  {avail_b/G:6.1f} G → {post_b/G:6.1f} G post-publish "
      f"({post_pct:.1f}% of {total_b/G:.0f} G)")

if post_b < 0:
    print("  PROJECTION: publish would EXHAUST the remote volume", file=sys.stderr)
    sys.exit(1)
if post_pct < min_free_pct:
    print(f"  PROJECTION: {post_pct:.1f}% free < {min_free_pct:.0f}% threshold",
          file=sys.stderr)
    sys.exit(1)
print(f"  OK — projected {post_pct:.1f}% free clears the {min_free_pct:.0f}% threshold")
PYEOF

if [ "$_CAP_RC" -ne 0 ]; then
    if [ "$ACCEPT_CAPACITY_RISK" = true ]; then
        echo "  OVERRIDE — --accept-capacity-risk set; proceeding past the capacity gate." >&2
    else
        echo "  HALT — projected free space lands below ${MIN_FREE_PCT}% (or could not be" >&2
        echo "         measured). Reclaim space (prune _previous/, remove stale _staging-*" >&2
        echo "         dirs) or re-run with --accept-capacity-risk to proceed anyway." >&2
        exit 1
    fi
fi
rm -f "$_CAP_INV" "$_CAP_DF"; trap - EXIT

# Preflight: monotonic version-release gate (fail-closed; turns the
# "did we remember to bump release" assumption into a checked gate).
# A staged package whose bytes DIFFER from the live current/ entry MUST be
# strictly newer in (version, release) than what is live — otherwise a client
# at the live (version,release) will never see it via `pkm upgrade` (the
# comparison is pkm.version.compare: version then release), so the fix is
# silently undeliverable; and a same-(version,release)-different-bytes publish
# is a provenance hazard. The remedy is to bump `release:` and rebuild. This
# gate enforces it with pkm's OWN compare(), so it matches client behavior
# exactly, instead of relying on the operator to remember. Runs BEFORE index
# generation + the signing ceremony so it fails fast and cheap. (Requires the
# index to carry `release` — fixed in pkm/repo.py _parse_pkginfo, the GBC003.3
# finding: previously the index dropped release, so NO same-version update could
# ever reach a client. --skip-sign reuses an already-vetted index → gate skipped.)
if [ "$SKIP_SIGN" != true ]; then
    echo "[preflight] Checking staged packages strictly advance vs live current/..."
    LIVE_INDEX_TMP=$(mktemp)
    if ssh -p "$REMOTE_PORT" -o BatchMode=yes -o ConnectTimeout=10 \
           "${REMOTE_USER}@${REMOTE_HOST}" "cat '${REMOTE_PATH}/current/InterGenOS.db'" \
           > "$LIVE_INDEX_TMP" 2>/dev/null && [ -s "$LIVE_INDEX_TMP" ]; then
        python3 - "$ARCHIVE_DIR" "$LIVE_INDEX_TMP" <<'PYGATE' || { rm -f "$LIVE_INDEX_TMP"; echo "ERROR: version-release gate failed — bump release(s) and re-run" >&2; exit 1; }
import sys, gzip, json, hashlib, tarfile, glob, os
sys.path.insert(0, ".")
from pkm.version import compare, VersionParseError
archive_dir, live_index = sys.argv[1], sys.argv[2]
with gzip.open(live_index, "rt", encoding="utf-8") as f:
    live = json.load(f).get("packages", {})
def pkginfo(tar_path):
    with tarfile.open(tar_path, "r:gz") as t:
        for cand in ("./.PKGINFO", ".PKGINFO"):
            try:
                m = t.getmember(cand)
            except KeyError:
                continue
            kv = {}
            for line in t.extractfile(m).read().decode().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    kv[k.strip()] = v.strip()
            return kv
    return {}
def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
problems = []
for arc in sorted(glob.glob(os.path.join(archive_dir, "*.igos.tar.gz"))):
    info = pkginfo(arc)
    name, ver, rel = info.get("pkgname"), info.get("pkgver"), info.get("pkgrel")
    if not name or name not in live:
        continue  # brand-new package — nothing live to advance past
    lv = live[name]
    if lv.get("sha256") == sha256(arc):
        continue  # unchanged bytes — no advancement required
    # bytes differ → must be strictly newer than the live (version, release)
    staged = {"version": ver, "release": rel}
    livev = {"version": lv.get("version"), "release": lv.get("release", 1)}
    try:
        if compare(staged, livev) <= 0:
            problems.append(
                (name,
                 f"{ver}-{rel}",
                 f"{livev['version']}-{livev['release']}"))
    except VersionParseError as e:
        problems.append((name, f"{ver}-{rel}", f"UNPARSEABLE: {e}"))
if problems:
    print("ERROR: changed package(s) do NOT strictly advance version-release vs live current/:", file=sys.stderr)
    for n, staged_vr, live_vr in problems:
        print(f"  {n}: staged {staged_vr} is not newer than live {live_vr} (bytes changed)", file=sys.stderr)
    print("", file=sys.stderr)
    print("  A changed package MUST be strictly newer (version, then release) or clients at the", file=sys.stderr)
    print("  live version-release will never see it via `pkm upgrade`. Bump `release:` in each", file=sys.stderr)
    print("  package.yml, rebuild, re-stage, then re-publish.", file=sys.stderr)
    sys.exit(1)
print(f"  OK — all changed packages strictly advance ({len(live)} live entries checked)")
PYGATE
    else
        echo "  (no live current/ index reachable — first publish or offline; advance gate skipped)"
    fi
    rm -f "$LIVE_INDEX_TMP"
fi

# Preflight: binary↔source correspondence gate (fail-closed; decided
# 2026-08-04). Every staged binary archive must have its matching
# <name>-<version>-<release>.igos.src.tar.gz staged beside it, or the package's
# recipe must declare no upstream source (the pure-data class, derived from the
# packages tree at gate time with every exempted name printed). Refusal names
# every shortfall. Runs BEFORE index generation and the signing ceremony so a
# correspondence gap costs seconds, not a ceremony. --skip-sources is the same
# explicit operator escape hatch it is for the sources upload below — with it
# set, this snapshot is knowingly not SOURCES.md-compliant until republished.
if [ "$SKIP_SOURCES" != true ]; then
    echo "[preflight] Checking binary<->source archive correspondence..."
    python3 scripts/check-source-correspondence.py \
        --archive-dir "$ARCHIVE_DIR" \
        --sources-archive-dir "$SOURCES_DIR" \
        --packages-root packages \
        || { echo "ERROR: source-correspondence gate failed — generate the missing" >&2
             echo "       source archives (scripts/build-source-archives.py) and re-run" >&2
             exit 1; }
fi

# Corpus-correspondence gate (decided 2026-08-21, fail-closed, no bypass):
# the staging corpus must be byte-identical to the evaluated build corpus in
# BOTH directions before the index it feeds is generated and signed. Origin:
# the overlay-onto-persistent-staging model silently served pre-rebuild bytes
# for 796 of 842 published components after the first full-rebuild release.
# --skip-sign resumes reuse an index this gate already vetted, so the manifest
# is not re-required there; every signing publish must present one.
if [ "$SKIP_SIGN" != true ] && [ "$DRY_RUN" != true ]; then
    if [ -z "$CHROOT_MANIFEST" ]; then
        echo "ERROR: --chroot-manifest is required for a signing publish." >&2
        echo "  Generate it inside the build chroot's archive dir, e.g.:" >&2
        echo "  ssh <builder>@<build-vm> 'cd /mnt/igos/var/lib/igos/archives && sudo sha256sum *.igos.tar.gz' > /tmp/chroot-archives.sha256" >&2
        exit 1
    fi
    echo "[preflight] Checking staged corpus <-> built corpus byte correspondence..."
    python3 scripts/check-corpus-correspondence.py \
        --staging "$ARCHIVE_DIR" \
        --chroot-manifest "$CHROOT_MANIFEST" \
        --packages packages \
        || { echo "ERROR: corpus-correspondence gate failed — re-stage from the" >&2
             echo "       evaluated chroot and re-run. There is no bypass." >&2
             exit 1; }
fi

INDEX_PATH="$ARCHIVE_DIR/InterGenOS.db"
SIG_PATH="${INDEX_PATH}.sig"

if [ "$SKIP_SIGN" = true ]; then
    # Resume: reuse the existing signed index (post-sign failure recovery —
    # no re-ceremony). Verify both artifacts are present and the detached
    # signature validates (uses the public key from the keyring; no card).
    echo "[1-2/4] --skip-sign: reusing existing signed index..."
    [ -f "$INDEX_PATH" ] && [ -f "$SIG_PATH" ] \
        || { echo "ERROR: --skip-sign set but $INDEX_PATH or .sig missing" >&2; exit 1; }
    gpg --verify "$SIG_PATH" "$INDEX_PATH" >/dev/null 2>&1 \
        || { echo "ERROR: existing signature does not verify against the index" >&2; exit 1; }
    echo "  OK — existing index ($(stat -c%s "$INDEX_PATH") b) + signature verified"
else
    # Step 1: Generate InterGenOS.db index
    echo "[1/4] Generating InterGenOS.db..."
    # A --dry-run must not destroy signed state. generate_index() writes into
    # ARCHIVE_DIR — which is exactly where a signed InterGenOS.db + .sig may be
    # sitting awaiting a --skip-sign resume — and index generation is NOT
    # byte-stable, so a "preview" silently invalidates that signature and the
    # recovery path with it. Preserve the existing index across the preview and
    # restore it unconditionally (trap covers an interrupt mid-generation).
    # Found live 2026-07-24: a validating dry-run regenerated the index
    # 132791 → 132790 bytes and turned the staged signature BAD.
    _DR_SAVED_INDEX=""
    if [ "$DRY_RUN" = true ] && [ -f "$INDEX_PATH" ]; then
        _DR_SAVED_INDEX=$(mktemp)
        cp -p "$INDEX_PATH" "$_DR_SAVED_INDEX"
        # shellcheck disable=SC2064
        trap "cp -p '$_DR_SAVED_INDEX' '$INDEX_PATH' 2>/dev/null; rm -f '$_DR_SAVED_INDEX'" EXIT
    fi

    python3 -c "
import sys
sys.path.insert(0, '.')
from pkm.repo import generate_index
path = generate_index('$ARCHIVE_DIR', arch='x86_64')
print(f'Index written: {path}')
" || { echo "ERROR: Index generation failed" >&2; exit 1; }

    if [ ! -f "$INDEX_PATH" ]; then
        echo "ERROR: Index not found after generation: $INDEX_PATH" >&2
        exit 1
    fi
    echo "  OK — $(stat -c%s "$INDEX_PATH") bytes"

    if [ -n "$_DR_SAVED_INDEX" ]; then
        cp -p "$_DR_SAVED_INDEX" "$INDEX_PATH"
        rm -f "$_DR_SAVED_INDEX"
        _DR_SAVED_INDEX=""
        trap - EXIT
        echo "  (dry-run: pre-existing signed index restored untouched)"
    fi

    # Step 2: PGP-sign the index — SKIPPED under --dry-run.
    # A dry run must NEVER invoke the Nitrokey; it signs nothing. (2026-06-17:
    # --dry-run previously fell through to sign_index here and produced a REAL
    # card signature — PIN + touch — despite "not publishing"; caught during the
    # GBC003.4 mirror republish. Generation above is local/cardless, so it stays.)
    if [ "$DRY_RUN" = true ]; then
        echo "[2/4] (dry-run) skipping index signing — a real publish signs with $GPG_KEY (Nitrokey PIN + touch)"
    else
        # Card hygiene: the OpenPGP/scdaemon path to the Nitrokey goes stale between
        # operations and throws "gpg: signing failed: Card error". Refresh scdaemon
        # immediately before signing so gpg opens a fresh card connection (operator-
        # confirmed necessity from the signing ceremonies; the PIV/sbsign bootloader
        # path doesn't hit this, only the gpg/OpenPGP path does).
        echo "[2/4] Signing InterGenOS.db..."
        gpgconf --kill scdaemon >/dev/null 2>&1 || true
        # Display hygiene: the gpg-agent must point at a live graphical session or it
        # cannot launch pinentry-gnome3, and the OpenPGP sign falls back to a terminal
        # pinentry -> "gpg: cannot open '/dev/tty'". That binding drifts when the agent
        # restarts or a non-GUI client connects between publishes (it is NOT tied to
        # foreground/background). Re-point the agent at the current session (idempotent),
        # then warn loudly if it still has no display so a failure is self-explanatory
        # instead of a cryptic tty error mid-ceremony. (2026-06-15; see the publish
        # runbook "Troubleshooting — cannot open /dev/tty".)
        gpg-connect-agent updatestartuptty /bye >/dev/null 2>&1 || true
        if ! gpg-connect-agent 'GETINFO std_session_env' /bye 2>/dev/null | grep -qi 'DISPLAY='; then
            echo "  WARN: gpg-agent has no DISPLAY binding — the GUI PIN prompt may not appear." >&2
            echo "        If signing fails with \"cannot open '/dev/tty'\", export your graphical" >&2
            echo "        session's DISPLAY + DBUS_SESSION_BUS_ADDRESS + XDG_RUNTIME_DIR, run" >&2
            echo "        'gpg-connect-agent updatestartuptty /bye', then re-run — or sign" >&2
            echo "        InterGenOS.db directly (subkey FINGERPRINT, not the NK1 alias) and" >&2
            echo "        resume with --skip-sign." >&2
        fi
        python3 -c "
import sys
sys.path.insert(0, '.')
from pkm.repo import sign_index
path = sign_index('$INDEX_PATH', gpg_key_id='$GPG_FP')
print(f'Signature written: {path}')
" || { echo "ERROR: Index signing failed" >&2; exit 1; }

        echo "  OK — $(stat -c%s "$SIG_PATH") bytes"
    fi
fi

if [ "$DRY_RUN" = true ]; then
    echo ""
    echo "=== DRY RUN — not publishing ==="
    echo "Would rsync:"
    echo "  $ARCHIVE_DIR/*.igos.tar.gz  →  $REMOTE_HOST:staging/"
    echo "  $INDEX_PATH                 →  $REMOTE_HOST:staging/InterGenOS.db"
    echo "  $SIG_PATH                   →  $REMOTE_HOST:staging/InterGenOS.db.sig  (produced by the real publish's sign step — dry-run signs nothing)"
    if [ "$SKIP_SOURCES" = true ]; then
        echo "  (source archives intentionally omitted via --skip-sources)"
    else
        SRC_GLOB_DRY=( "$SOURCES_DIR"/*.igos.src.tar.gz )
        if [ -e "${SRC_GLOB_DRY[0]}" ]; then
            echo "  $SOURCES_DIR/*.igos.src.tar.gz  →  $REMOTE_HOST:staging/sources/  (${#SRC_GLOB_DRY[@]} archives)"
        else
            echo "  (NO source archives in $SOURCES_DIR/ — real publish would fail-closed)"
        fi
    fi
    echo "  (partial push: unchanged archives hardlink from current/ via"
    echo "   rsync --checksum --link-dest; only new/changed archives transfer.)"
    echo "  Then: promote staging/ → live/"
    exit 0
fi

# Step 3: Rsync to timestamped staging directory on remote
# Timestamped staging prevents M2 race condition (concurrent invocations).
STAGING_DIR="_staging-$(date -u +%Y%m%dT%H%M%SZ)"
STAGING_PATH="${REMOTE_PATH}/${STAGING_DIR}"
# rsync native remote form (user@host:/abs/path) with an explicit ssh port —
# rsync has NO `ssh://` scheme; it would parse that as host="ssh".
STAGING_RSYNC="${REMOTE_USER}@${REMOTE_HOST}:${STAGING_PATH}"

echo "[3/4] Uploading to ${STAGING_DIR}..."
# The VPS rsync (3.1.3) predates --mkpath (added in rsync 3.2.3), so create the
# destination dirs over ssh first and rsync without --mkpath.
ssh -p "$REMOTE_PORT" "${REMOTE_USER}@${REMOTE_HOST}" \
    "mkdir -p '${STAGING_PATH}' '${STAGING_PATH}/sources'" \
    || { echo "ERROR: could not create remote staging dir ${STAGING_PATH}" >&2; exit 1; }

# Partial push (framework §11): seed the new staging from the CURRENT live tree
# so UNCHANGED archives hardlink in place of a re-upload — only new/changed
# package archives actually move over the wire. The index + signature always
# change (full regenerate + NK1 sign every publish — a whole-repo signed manifest
# cannot be partial), so those always transfer. --checksum makes the match
# content-based: a rebuilt-but-byte-identical archive has a fresh mtime that the
# default size+mtime quick-check would needlessly re-send. Hardlinks across
# staging dirs also avoid duplicating unchanged archives on the VPS disk. First
# publish (no current/ yet) → no --link-dest, full transfer. Channel-agnostic: a
# future dev channel reuses this verbatim with its own REMOTE_PATH/current.
# Hardlink sources = every snapshot resolved in the preflight (current/ first,
# then the _previous/ generations and any leftover staging dir), NOT just
# current/. rsync takes the first match across all --link-dest dirs, so an
# archive present anywhere on the remote volume costs nothing to "upload".
LINK_DEST_OPTS=()
CURRENT_TARGET="${LINK_DEST_CANDS[0]:-}"
if [ ${#LINK_DEST_CANDS[@]} -gt 0 ]; then
    LINK_DEST_OPTS=(--checksum)
    for _cand in "${LINK_DEST_CANDS[@]}"; do
        LINK_DEST_OPTS+=("--link-dest=${_cand}")
    done
    echo "  partial push: hardlinking unchanged archives from ${#LINK_DEST_CANDS[@]} snapshot(s)"
fi
# Release key rides every publish (decided 2026-08-19): clients fetch the
# signing key beside the packages they verify, so it is served at
# <repo>/x86_64/current/intergenos-release-key.asc. The promote is an atomic
# generation swap — anything not staged here vanishes from current/ at the
# next publish, which is exactly what happened before this line existed
# (the key 404'd at that path while /keys/ served it). Source of truth is
# the tracked docs/signing-key.asc; fail-closed on absence or emptiness —
# a publish that would drop the served key is refused.
RELEASE_KEY_SRC="docs/signing-key.asc"
[ -s "$RELEASE_KEY_SRC" ] \
    || { echo "ERROR: release key ${RELEASE_KEY_SRC} missing or empty — refusing to publish without the served key" >&2; exit 1; }
cp "$RELEASE_KEY_SRC" "$ARCHIVE_DIR/intergenos-release-key.asc"

rsync -av "${LINK_DEST_OPTS[@]}" -e "ssh -p ${REMOTE_PORT}" \
    "$ARCHIVE_DIR"/*.igos.tar.gz \
    "$ARCHIVE_DIR/intergenos-release-key.asc" \
    "$INDEX_PATH" \
    "$SIG_PATH" \
    "$STAGING_RSYNC/" \
    || { echo "ERROR: rsync to staging failed" >&2; exit 1; }
echo "  OK — packages + index + signature uploaded"

# Source archives — deliver against the SOURCES.md §6d corresponding-source
# commitment. Land them at <staging>/sources/ so they're reachable at
# repo.intergenos.org/x86_64/current/sources/ post-promote. Fail-closed
# if absent — publishing binaries without their corresponding source
# violates the SOURCES.md binding commitment. The --skip-sources flag
# is the operator escape hatch for known follow-on cases.
if [ "$SKIP_SOURCES" = true ]; then
    echo "  SKIP — --skip-sources flag set; source archives intentionally omitted."
    echo "         Re-run scripts/build-source-archives.py + publish again before"
    echo "         considering this snapshot SOURCES.md-compliant."
else
    SRC_GLOB=( "$SOURCES_DIR"/*.igos.src.tar.gz )
    if [ ! -e "${SRC_GLOB[0]}" ]; then
        echo "ERROR: no .igos.src.tar.gz in $SOURCES_DIR/" >&2
        echo "       publishing binaries without their corresponding-source archives" >&2
        echo "       violates the SOURCES.md §6d commitment. Run:" >&2
        echo "         scripts/build-source-archives.py" >&2
        echo "       to generate them, then re-run this publish. Override:" >&2
        echo "         scripts/publish-repo.sh --skip-sources  (emergency only)" >&2
        exit 1
    fi
    SRC_COUNT=${#SRC_GLOB[@]}
    echo "  uploading $SRC_COUNT source archives to ${STAGING_DIR}/sources/..."
    # Same partial-push for the corresponding-source archives, against the live
    # current/sources tree (CURRENT_TARGET resolved above for the binary rsync).
    # Same multi-snapshot hardlinking for the corresponding-source tree. This
    # is the leg that matters most: a current/sources gutted by an earlier
    # partial publish would otherwise force a full ~24 G re-upload of archives
    # still present in a _previous/ generation on the same volume.
    SRC_LINK_DEST_OPTS=()
    if [ ${#LINK_DEST_CANDS[@]} -gt 0 ]; then
        SRC_LINK_DEST_OPTS=(--checksum)
        for _cand in "${LINK_DEST_CANDS[@]}"; do
            SRC_LINK_DEST_OPTS+=("--link-dest=${_cand}/sources")
        done
    fi
    rsync -av "${SRC_LINK_DEST_OPTS[@]}" -e "ssh -p ${REMOTE_PORT}" \
        "${SRC_GLOB[@]}" \
        "$STAGING_RSYNC/sources/" \
        || { echo "ERROR: source archive rsync failed" >&2; exit 1; }
    echo "  OK — $SRC_COUNT source archives uploaded to sources/"
fi

# Step 4: Atomic promote — symlink swap (M1 fix, owner-picked option b)
# Since M2 already creates a per-invocation timestamped staging dir,
# the promote is a single atomic symlink rename: $LIVE/current → the new
# _staging-YYYYMMDDTHHMMSSZ/ directory. The mechanism is `ln -sfn` to a
# `.new` symlink + `mv -T` of the .new symlink over `current` (see step
# detail in the SSHEOF block lines 247-258 below). POSIX mv is atomic
# for symlinks within the same filesystem; clients reading via the
# `current/` symlink see EITHER the old target OR the new target —
# never a mixed state. This matches docs/mirror/design.md symlink-swap
# canonical promote shape. (Closes audit row L-017: prior comment at
# this site mis-described the mechanism as "directory swap" which did
# not match the actual implementation.)
echo "[4/4] Promoting ${STAGING_DIR} → live (atomic symlink swap)..."
ssh -p "$REMOTE_PORT" "${REMOTE_USER}@${REMOTE_HOST}" bash -s -- \
    "$REMOTE_PATH" "$STAGING_DIR" "$KEEP_PREVIOUS" << 'SSHEOF' || { echo "ERROR: atomic promote failed" >&2; exit 1; }
set -e -o pipefail
LIVE="$1"
STAGING_DIR="$2"
KEEP_PREVIOUS="$3"
STAGING="$LIVE/$STAGING_DIR"

if [ ! -d "$STAGING" ]; then
    echo "ERROR: staging directory not found on remote: $STAGING" >&2
    exit 1
fi

# Verify staging has the minimum required files
if [ ! -f "$STAGING/InterGenOS.db" ] || [ ! -f "$STAGING/InterGenOS.db.sig" ]; then
    echo "ERROR: staging directory missing index or signature" >&2
    exit 1
fi

# Atomic promote: symlink-swap pattern (rename-the-symlink-not-the-directory).
# The symlink always points at a valid target; no 404 window for clients.
# 1. Point current.new at the new staging dir (staging is already complete)
# 2. Atomically rename current.new over current
# 3. Archive the now-unreferenced old snapshot (if any) — no rush, clients
#    are already fetching from the new staging dir through current/
# Only capture a PRE-EXISTING current symlink's target. Guard with -L:
# `readlink -f` on a NON-existent current (first publish) returns the
# (non-existent) path string with rc 0, which then — after the swap makes
# current a live symlink to a dir — passes the `[ -d "$PREVIOUS" ]` test and
# the archive step moves the just-created `current` into _previous/, leaving
# the mirror with NO current/ (the 2026-06-14 first-publish bug).
PREVIOUS=""
if [ -L "$LIVE/current" ]; then
    PREVIOUS=$(readlink -f "$LIVE/current" 2>/dev/null || echo "")
fi

ln -sfn "$STAGING_DIR" "$LIVE/current.new"
mv -T "$LIVE/current.new" "$LIVE/current"
echo "  current → ${STAGING_DIR}/ symlink swapped"

# Archive the prior snapshot now that clients are on the new one
if [ -n "$PREVIOUS" ] && [ -d "$PREVIOUS" ]; then
    ARCHIVE_TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
    mkdir -p "$LIVE/_previous"
    mv "$PREVIOUS" "$LIVE/_previous/${STAGING_DIR}-prev-${ARCHIVE_TIMESTAMP}"
    echo "  Archived previous snapshot → _previous/"
fi

# Retention prune. Without this, _previous/ grows by one full generation per
# publish and is never reclaimed: on 2026-07-24 a single stale generation held
# 26.1 G of unshared bytes and drove the volume under its backup threshold.
# Guards, in order: only ever inside $LIVE/_previous; only entries matching the
# archive naming shape; never the live current/ target; newest-N kept by the
# embedded timestamps (lexical sort is chronological for this name form).
if [ "$KEEP_PREVIOUS" -ge 0 ] 2>/dev/null && [ -d "$LIVE/_previous" ]; then
    CURRENT_REAL=$(readlink -f "$LIVE/current" 2>/dev/null || echo "")
    PRUNED=0
    mapfile -t GENERATIONS < <(find "$LIVE/_previous" -mindepth 1 -maxdepth 1 -type d \
        -name '_staging-*-prev-*' -printf '%f\n' 2>/dev/null | sort -r)
    IDX=0
    for GEN in "${GENERATIONS[@]}"; do
        IDX=$((IDX + 1))
        [ "$IDX" -le "$KEEP_PREVIOUS" ] && continue
        VICTIM="$LIVE/_previous/$GEN"
        # Refuse to touch anything the live symlink resolves to.
        if [ -n "$CURRENT_REAL" ] && [ "$(readlink -f "$VICTIM")" = "$CURRENT_REAL" ]; then
            echo "  SKIP prune: $GEN is the live current/ target" >&2
            continue
        fi
        rm -rf "$VICTIM"
        PRUNED=$((PRUNED + 1))
    done
    if [ "$PRUNED" -gt 0 ]; then
        echo "  Pruned $PRUNED superseded snapshot(s) from _previous/ (retention: $KEEP_PREVIOUS)"
    fi
fi

echo "Publish complete: $(date -u)"
echo "Packages: $(ls "$LIVE/$STAGING_DIR"/*.igos.tar.gz 2>/dev/null | wc -l)"
echo "Index size: $(stat -c%s "$LIVE/$STAGING_DIR/InterGenOS.db") bytes"
SSHEOF
echo "  OK — promoted via current/ symlink swap"

# Step 5: Transparency log append (L-024)
# Push the signed index + signature to the InterGenOS transparency-log git
# repo with a structured commit message. Git provides the append-only
# property (master branch is force-push-protected on the remote); every
# clone of the repo gives an independent verifier the full publication
# history. Audit row L-024 explicitly proposed this shape ("publish each
# signed index's sha256 to public append-only log; git repo with
# co-maintainer pushes works; rekor is the proper tool"). Rekor v2
# integration is queued as v1.1 enhancement — adds a second attestation
# target alongside this git log, doesn't replace it.
echo "[5/5] Transparency log append..."
if [ "$SKIP_TRANSPARENCY" = true ]; then
    echo "  SKIP — --skip-transparency flag set; this publish is NOT in the"
    echo "         append-only transparency log. Re-publish with the flag"
    echo "         removed before considering the snapshot fully attested."
else
    if [ ! -d "$TRANSPARENCY_LOCAL/.git" ]; then
        echo "  cloning transparency-log repo to $TRANSPARENCY_LOCAL..."
        git clone --depth 100 "$TRANSPARENCY_GIT_REMOTE" "$TRANSPARENCY_LOCAL" \
            || { echo "ERROR: transparency-log clone failed ($TRANSPARENCY_GIT_REMOTE)" >&2; exit 1; }
    else
        git -C "$TRANSPARENCY_LOCAL" pull --ff-only origin master \
            || { echo "ERROR: transparency-log pull failed (likely diverged remote — investigate before next publish)" >&2; exit 1; }
    fi

    # Ensure a committer identity for this repo — a freshly-cloned transparency
    # repo inherits none, and the host's git identity is per-repo (not global),
    # so `git commit` would die "Author identity unknown". Pin it locally.
    git -C "$TRANSPARENCY_LOCAL" config user.name "InterGenJLU"
    git -C "$TRANSPARENCY_LOCAL" config user.email "InterGenJLU@users.noreply.github.com"

    LOG_DIR="$TRANSPARENCY_LOCAL/x86_64/current"
    mkdir -p "$LOG_DIR"
    cp "$INDEX_PATH" "$LOG_DIR/InterGenOS.db"
    cp "$SIG_PATH"   "$LOG_DIR/InterGenOS.db.sig"

    INDEX_SHA=$(sha256sum "$INDEX_PATH" | awk '{print $1}')
    SIG_SHA=$(sha256sum "$SIG_PATH" | awk '{print $1}')
    INDEX_SIZE=$(stat -c%s "$INDEX_PATH")
    SIG_SIZE=$(stat -c%s "$SIG_PATH")
    PREV_ENTRY=$(git -C "$TRANSPARENCY_LOCAL" log -1 --format=%H 2>/dev/null || echo INIT)

    # L-022 extension: also log the archive manifest + its detached
    # signature when present. The manifest is the per-package SHA-256
    # ledger emitted by the build pipeline + signed by sign-release.sh
    # (master + S1 multi-sig). Including it in the transparency-log
    # entry makes the DR mirror tamper-evident at the package layer,
    # not just the index layer — recovery from VPS loss can reconstruct
    # both "which packages existed" (manifest) and "what their canonical
    # bytes were" (index + per-archive sigs already covered via repo
    # archives themselves). Manifest inclusion is conditional because
    # the manifest is produced only during signed-release publishes,
    # not on incremental staging.
    MANIFEST_PATH="${ARCHIVE_DIR}/intergenos-archive-manifest.txt"
    MANIFEST_SIG_PATH="${MANIFEST_PATH}.sig"
    MANIFEST_LOG_LINES=""
    GIT_ADD_MANIFEST_ARGS=""
    if [ -f "$MANIFEST_PATH" ]; then
        cp "$MANIFEST_PATH" "$LOG_DIR/intergenos-archive-manifest.txt"
        MANIFEST_SHA=$(sha256sum "$MANIFEST_PATH" | awk '{print $1}')
        MANIFEST_SIZE=$(stat -c%s "$MANIFEST_PATH")
        MANIFEST_LOG_LINES="
x86_64/current/intergenos-archive-manifest.txt
  sha256 = ${MANIFEST_SHA}
  size   = ${MANIFEST_SIZE}
"
        GIT_ADD_MANIFEST_ARGS="x86_64/current/intergenos-archive-manifest.txt"
    fi
    if [ -f "$MANIFEST_SIG_PATH" ]; then
        cp "$MANIFEST_SIG_PATH" "$LOG_DIR/intergenos-archive-manifest.txt.sig"
        MANIFEST_SIG_SHA=$(sha256sum "$MANIFEST_SIG_PATH" | awk '{print $1}')
        MANIFEST_SIG_SIZE=$(stat -c%s "$MANIFEST_SIG_PATH")
        MANIFEST_LOG_LINES="${MANIFEST_LOG_LINES}
x86_64/current/intergenos-archive-manifest.txt.sig
  sha256 = ${MANIFEST_SIG_SHA}
  size   = ${MANIFEST_SIG_SIZE}
"
        GIT_ADD_MANIFEST_ARGS="${GIT_ADD_MANIFEST_ARGS} x86_64/current/intergenos-archive-manifest.txt.sig"
    fi

    COMMIT_MSG=$(cat <<EOFCM
publish: ${STAGING_DIR} InterGenOS.db transparency-log entry

x86_64/current/InterGenOS.db
  sha256 = ${INDEX_SHA}
  size   = ${INDEX_SIZE}

x86_64/current/InterGenOS.db.sig
  sha256 = ${SIG_SHA}
  size   = ${SIG_SIZE}
${MANIFEST_LOG_LINES}
signed-by-fingerprint = ${GPG_FP}
prev-entry            = ${PREV_ENTRY}
log-version           = 2
EOFCM
)
    git -C "$TRANSPARENCY_LOCAL" add x86_64/current/InterGenOS.db x86_64/current/InterGenOS.db.sig $GIT_ADD_MANIFEST_ARGS
    if git -C "$TRANSPARENCY_LOCAL" diff --cached --quiet; then
        echo "  WARN — transparency-log working tree showed no changes; skipping commit"
        echo "         (this snapshot may have been previously logged — investigate)"
    else
        git -C "$TRANSPARENCY_LOCAL" commit -m "$COMMIT_MSG" \
            || { echo "ERROR: transparency-log commit failed" >&2; exit 1; }
        git -C "$TRANSPARENCY_LOCAL" push origin master \
            || { echo "ERROR: transparency-log push failed (resolve + push manually before next publish)" >&2; exit 1; }
        NEW_ENTRY=$(git -C "$TRANSPARENCY_LOCAL" log -1 --format=%H)
        echo "  OK — logged at $TRANSPARENCY_GIT_REMOTE entry=${NEW_ENTRY} prev=${PREV_ENTRY}"
    fi
fi

echo ""
echo "=== Publish Complete ==="
echo "Repository:    https://repo.intergenos.org/x86_64/current/"
echo "Index:         InterGenOS.db ($(stat -c%s "$INDEX_PATH") bytes)"
echo "Signature:     InterGenOS.db.sig ($(stat -c%s "$SIG_PATH") bytes)"
echo "Packages:      $COUNT published"
if [ "$SKIP_TRANSPARENCY" != true ]; then
    echo "Transparency:  $TRANSPARENCY_GIT_REMOTE master HEAD"
fi
