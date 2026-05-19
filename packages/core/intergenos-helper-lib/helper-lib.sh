#!/bin/bash
# intergenos-helper-lib — Sourceable bash library for pkm install helpers
#
# Closes audit row H-007: install helper path never registers files or
# dependencies. Helpers (chrome, vscode, edge, brave, discord, spotify,
# claude-code) deposit files into /opt/, /usr/bin/, /usr/share/ etc., and
# without this library pkm has zero record of those files. Consequence:
# pkm files <pkg> returns nothing, pkm verify <pkg> reports total: 0 OK
# even when the helper-installed files have been deleted, pkm remove
# <pkg> leaves orphaned binaries on disk.
#
# This library gives helpers a small bash API for recording the
# install footprint as the helper runs; on helper success pkm reads
# the recorded manifest and threads the file list through the package
# database's add_files / add_depends paths, so pkm files/verify/remove
# work as users expect.
#
# Source this library at the top of an /usr/bin/igos-install-<name>
# helper script:
#
#   source /usr/share/igos/helpers/helper-lib.sh
#
# Then call the API as the helper performs its work:
#
#   igos_helper_init "chrome"
#   igos_helper_set_version "138.0.7204.49"
#
#   # ... wget + extract + cp -a opt/google /opt/ ...
#   igos_helper_record_file /opt/google/chrome/google-chrome
#   igos_helper_record_file /opt/google/chrome/chrome
#   # ... record each installed file path ...
#
#   ln -sf /opt/google/chrome/google-chrome /usr/bin/google-chrome
#   igos_helper_record_symlink /usr/bin/google-chrome /opt/google/chrome/google-chrome
#
#   igos_helper_record_dep glibc
#   igos_helper_record_post_install_action "gtk-update-icon-cache /usr/share/icons/hicolor"
#
#   igos_helper_commit
#
# On commit, the library writes /var/lib/igos/helpers/<name>.manifest
# as a JSON document; pkm._run_helper at pkm/installer.py reads it,
# validates the schema + path allowlist + DoS cap, and threads the
# file list through PackageDB.add_files and PackageDB.add_depends.
# pkm remove <name> then iterates db.get_files(name) and unlinks each
# entry — symlinks too (os.remove unlinks the symlink itself, not its
# target).
#
# Stable API surface: do NOT break the function shapes below in a
# patch release. Breaking changes go through SUPERSEDES per RFC §11
# (helper-lib-v2 supersedes helper-lib-v1; the v1 library stays
# available during the supersede overlap window). See
# docs/architecture/helper-manifest-spec-v1.md for the manifest
# schema spec + docs/architecture/helper-lib-abi-policy.md for the
# full ABI stability policy.

# API version marker. Monotonically incremented across major API
# versions (v1=1, v2=2, ...). Helpers MAY assert against this
# constant immediately after sourcing to refuse incompatible majors.
# Within a major version, additive changes (new optional functions
# or trailing-defaulted parameters) do NOT bump the constant.
IGOS_HELPER_LIB_API_VERSION=1
export IGOS_HELPER_LIB_API_VERSION

# ---- Internal state -----------------------------------------------------
#
# A single helper invocation accumulates state into a per-invocation
# tmpdir at $IGOS_HELPER_STAGING. Subsequent function calls in the
# same shell read + append to that staging area. igos_helper_commit
# JSON-assembles + atomic-mv's the manifest into place + cleans the
# staging tmpdir.

igos_helper_init() {
    # Initialize a helper manifest staging area for package <name>.
    # Must be called once at the top of a helper script before any
    # record_* call. Exports IGOS_HELPER_STAGING + IGOS_HELPER_NAME.
    #
    # H-007 orphan-file recovery (Decision D, 2026-05-19): installs an
    # EXIT trap that writes a `<name>.manifest.partial` sidecar if the
    # helper aborts before igos_helper_commit runs. pkm's reader at
    # _run_helper detects the sidecar + surfaces the partial-recorded
    # file list so the user knows what was deposited but never
    # tracked. igos_helper_commit clears IGOS_HELPER_COMMITTED + untraps
    # EXIT before its work so the sidecar is only written on crash.
    local name="$1"
    if [ -z "$name" ]; then
        echo "igos_helper_init: usage: igos_helper_init <package-name>" >&2
        return 1
    fi
    # Reject names with shell-special chars to defend against accidental
    # injection (the name lands in JSON + filesystem paths).
    case "$name" in
        *[^a-zA-Z0-9._-]*)
            echo "igos_helper_init: invalid package name '$name' (only [a-zA-Z0-9._-] allowed)" >&2
            return 1
            ;;
    esac
    IGOS_HELPER_STAGING=$(mktemp -d -t igos-helper-XXXXXXXX)
    IGOS_HELPER_NAME="$name"
    IGOS_HELPER_COMMITTED=0
    export IGOS_HELPER_STAGING IGOS_HELPER_NAME IGOS_HELPER_COMMITTED
    : > "$IGOS_HELPER_STAGING/files"
    : > "$IGOS_HELPER_STAGING/symlinks"
    : > "$IGOS_HELPER_STAGING/depends"
    : > "$IGOS_HELPER_STAGING/post_install_actions"
    : > "$IGOS_HELPER_STAGING/version"

    # Clean any pre-existing .partial sidecar for this package -- a
    # fresh init means we're starting over; a prior crash is being
    # retried + the operator wants the partial-state superseded.
    local dest_dir="${IGOS_HELPER_MANIFEST_DIR:-/var/lib/igos/helpers}"
    rm -f "$dest_dir/$name.manifest.partial" 2>/dev/null || true

    # Install the EXIT trap. _igos_helper_emit_partial is a no-op when
    # commit has already cleared IGOS_HELPER_COMMITTED.
    trap '_igos_helper_emit_partial' EXIT
}

_igos_helper_emit_partial() {
    # Internal: invoked by the EXIT trap installed in igos_helper_init.
    # Two responsibilities (BLOCKING-D fix 2026-05-19):
    #
    #   1. Always run the helper-set IGOS_HELPER_USER_CLEANUP command
    #      (typically `rm -rf "$TMPDIR"`). This is how helpers register
    #      cleanup WITHOUT installing their own `trap ... EXIT`, which
    #      would collide with init's trap via bash trap-replace
    #      semantics (one trap per signal -- no native composition).
    #
    #   2. On crash (IGOS_HELPER_COMMITTED != 1), write a
    #      `<name>.manifest.partial` sidecar capturing the staged
    #      state so pkm's reader can surface the orphan file list.
    #      On success commit clears the staging tmpdir but leaves
    #      IGOS_HELPER_COMMITTED=1 + the trap installed so this
    #      function still runs and executes user cleanup on exit.
    #
    # Best-effort: failures suppressed so a crashing helper is not
    # further obscured by the trap.
    if [ -n "${IGOS_HELPER_USER_CLEANUP:-}" ]; then
        eval "$IGOS_HELPER_USER_CLEANUP" 2>/dev/null || true
    fi
    if [ "${IGOS_HELPER_COMMITTED:-0}" = "1" ]; then
        return 0
    fi
    if [ -z "${IGOS_HELPER_STAGING:-}" ] || [ -z "${IGOS_HELPER_NAME:-}" ]; then
        return 0
    fi
    if [ ! -d "$IGOS_HELPER_STAGING" ]; then
        return 0
    fi
    local dest_dir="${IGOS_HELPER_MANIFEST_DIR:-/var/lib/igos/helpers}"
    mkdir -p "$dest_dir" 2>/dev/null || return 0
    local partial="$dest_dir/$IGOS_HELPER_NAME.manifest.partial"
    if ! command -v python3 >/dev/null 2>&1; then
        return 0
    fi
    IGOS_HELPER_STAGING="$IGOS_HELPER_STAGING" \
    IGOS_HELPER_NAME="$IGOS_HELPER_NAME" \
    python3 - "$partial" 2>/dev/null <<'PYEOF' || true
import json, os, sys
staging = os.environ["IGOS_HELPER_STAGING"]
name = os.environ["IGOS_HELPER_NAME"]
out_path = sys.argv[1]

def read_lines(rel):
    p = os.path.join(staging, rel)
    if not os.path.exists(p):
        return []
    with open(p, "r", encoding="utf-8") as f:
        return [line.rstrip("\n") for line in f if line.strip()]

version = ""
vfile = os.path.join(staging, "version")
if os.path.exists(vfile):
    with open(vfile, "r", encoding="utf-8") as f:
        version = f.read().strip()

symlinks = []
for line in read_lines("symlinks"):
    if "\t" in line:
        link_path, target = line.split("\t", 1)
        symlinks.append({"path": link_path, "target": target})

partial = {
    "version": 1,
    "name": name,
    "version_installed": version,
    "files": read_lines("files"),
    "symlinks": symlinks,
    "depends": read_lines("depends"),
    "post_install_actions_log": read_lines("post_install_actions"),
    "partial": True,
    "build_date": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(partial, f, indent=2)
PYEOF
    return 0
}

igos_helper_set_version() {
    # Record the installed version (free-form; the upstream package's
    # version string, e.g. "138.0.7204.49" for chrome).
    if [ -z "$IGOS_HELPER_STAGING" ]; then
        echo "igos_helper_set_version: igos_helper_init not called yet" >&2
        return 1
    fi
    printf '%s\n' "$1" > "$IGOS_HELPER_STAGING/version"
}

igos_helper_record_file() {
    # Append a file path to the manifest's files[] array. The path
    # MUST be absolute (start with /) and SHOULD live under one of
    # /usr/, /opt/, /etc/, /var/lib/ — pkm's manifest reader enforces
    # the path-prefix allowlist at install time and refuses the
    # wire-up (but does NOT remove the deposited file) on allowlist
    # violation, so the helper script can be debugged.
    if [ -z "$IGOS_HELPER_STAGING" ]; then
        echo "igos_helper_record_file: igos_helper_init not called yet" >&2
        return 1
    fi
    local path="$1"
    case "$path" in
        /*) ;;
        *)
            echo "igos_helper_record_file: path must be absolute (got '$path')" >&2
            return 1
            ;;
    esac
    printf '%s\n' "$path" >> "$IGOS_HELPER_STAGING/files"
}

igos_helper_record_symlink() {
    # Append a symlink entry to the manifest's symlinks[] array.
    # <link_path> is the symlink itself (the path that pkm tracks +
    # unlinks on remove); <target> is the path the symlink points at
    # (informational; pkm does NOT delete the target on remove unless
    # it is independently in files[]).
    if [ -z "$IGOS_HELPER_STAGING" ]; then
        echo "igos_helper_record_symlink: igos_helper_init not called yet" >&2
        return 1
    fi
    local link_path="$1"
    local target="$2"
    case "$link_path" in
        /*) ;;
        *)
            echo "igos_helper_record_symlink: link_path must be absolute (got '$link_path')" >&2
            return 1
            ;;
    esac
    # tab-separated so igos_helper_commit can split into JSON fields
    # without quoting ambiguity on paths containing spaces.
    printf '%s\t%s\n' "$link_path" "$target" >> "$IGOS_HELPER_STAGING/symlinks"
}

igos_helper_record_dep() {
    # Append a dependency package name to the manifest's depends[]
    # array. pkm reads this on install + threads through add_depends
    # so reverse-dependency tracking works for helper-installed
    # packages (e.g. removing glibc warns that chrome depends on it).
    if [ -z "$IGOS_HELPER_STAGING" ]; then
        echo "igos_helper_record_dep: igos_helper_init not called yet" >&2
        return 1
    fi
    printf '%s\n' "$1" >> "$IGOS_HELPER_STAGING/depends"
}

igos_helper_record_post_install_action() {
    # Append a descriptive action string to the manifest's
    # post_install_actions_log[] array. v1.0 stores these as a
    # transparency artifact — pkm logs them to the operation history
    # so users can see what the helper did beyond file deposits
    # (icon-cache refresh, mime-db update, etc.) — but does NOT
    # replay them on remove. Teardown lives in a separate pre-remove
    # hook surface (deferred to a future audit row).
    if [ -z "$IGOS_HELPER_STAGING" ]; then
        echo "igos_helper_record_post_install_action: igos_helper_init not called yet" >&2
        return 1
    fi
    printf '%s\n' "$1" >> "$IGOS_HELPER_STAGING/post_install_actions"
}

igos_helper_commit() {
    # Assemble the staging state into a JSON manifest at
    # /var/lib/igos/helpers/<name>.manifest. Atomic-mv from a sibling
    # .tmp path so a helper that aborts mid-record never leaves a
    # half-finished manifest visible to pkm. Cleans up the staging
    # tmpdir on success.
    if [ -z "$IGOS_HELPER_STAGING" ] || [ -z "$IGOS_HELPER_NAME" ]; then
        echo "igos_helper_commit: igos_helper_init not called yet" >&2
        return 1
    fi
    # Manifest destination directory. Override via IGOS_HELPER_MANIFEST_DIR
    # env var for test harnesses or alternate-root installer scenarios
    # (e.g. Forge installer building into a target chroot). Production
    # use leaves the var unset; the default /var/lib/igos/helpers is the
    # path pkm/installer.py:HELPER_MANIFEST_DIR reads from.
    local dest_dir="${IGOS_HELPER_MANIFEST_DIR:-/var/lib/igos/helpers}"
    mkdir -p "$dest_dir"
    local final="$dest_dir/$IGOS_HELPER_NAME.manifest"
    local tmp="$dest_dir/$IGOS_HELPER_NAME.manifest.tmp"

    # Use python for JSON assembly so we don't have to handle quoting
    # edge cases in shell + so the schema field shape stays in lock-step
    # with pkm's reader (both are Python-side). InterGenOS commits to
    # Linux-only dev/test (2026-05-19); python3 is always available on
    # InterGenOS systems.
    if ! command -v python3 >/dev/null 2>&1; then
        echo "igos_helper_commit: python3 not found in PATH" >&2
        rm -rf "$IGOS_HELPER_STAGING"
        unset IGOS_HELPER_STAGING IGOS_HELPER_NAME
        return 1
    fi
    IGOS_HELPER_STAGING="$IGOS_HELPER_STAGING" \
    IGOS_HELPER_NAME="$IGOS_HELPER_NAME" \
    python3 - "$tmp" <<'PYEOF'
import json, os, sys
staging = os.environ["IGOS_HELPER_STAGING"]
name = os.environ["IGOS_HELPER_NAME"]
out_path = sys.argv[1]

def read_lines(rel):
    p = os.path.join(staging, rel)
    if not os.path.exists(p):
        return []
    with open(p, "r", encoding="utf-8") as f:
        return [line.rstrip("\n") for line in f if line.strip()]

version = ""
vfile = os.path.join(staging, "version")
if os.path.exists(vfile):
    with open(vfile, "r", encoding="utf-8") as f:
        version = f.read().strip()

symlinks = []
for line in read_lines("symlinks"):
    if "\t" in line:
        link_path, target = line.split("\t", 1)
        symlinks.append({"path": link_path, "target": target})

manifest = {
    "version": 1,
    "name": name,
    "version_installed": version,
    "files": read_lines("files"),
    "symlinks": symlinks,
    "depends": read_lines("depends"),
    "post_install_actions_log": read_lines("post_install_actions"),
    "build_date": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
PYEOF
    local rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "igos_helper_commit: JSON assembly failed (rc=$rc); manifest NOT written" >&2
        rm -rf "$IGOS_HELPER_STAGING"
        unset IGOS_HELPER_STAGING IGOS_HELPER_NAME
        return "$rc"
    fi

    # Atomic mv .tmp -> final. A helper that aborts after init but
    # before commit leaves nothing at the final path; pkm will not see
    # a stale half-finished manifest. (Decision D, 2026-05-19: if the
    # helper crashes between init + commit, the EXIT trap installed
    # by init writes a `<name>.manifest.partial` sidecar so pkm can
    # surface the orphan file list to the user.)
    mv -f "$tmp" "$final"
    chmod 644 "$final"

    # Decision D BLOCKING-D fix 2026-05-19: mark committed but KEEP
    # the EXIT trap installed. `_igos_helper_emit_partial` sees
    # IGOS_HELPER_COMMITTED=1 + short-circuits the sidecar write, but
    # still runs the helper's IGOS_HELPER_USER_CLEANUP (e.g., TMPDIR
    # removal). This is how user cleanup runs on BOTH success and
    # crash paths without colliding with the helper installing its
    # own native `trap ... EXIT` (bash trap-replace semantics would
    # otherwise break one or the other depending on call ordering).
    IGOS_HELPER_COMMITTED=1

    # Clean any pre-existing .partial sidecar for this package (e.g.
    # from a prior crashed run) so successful retry leaves no stale
    # signal for the pkm reader.
    rm -f "$dest_dir/$IGOS_HELPER_NAME.manifest.partial" 2>/dev/null || true

    rm -rf "$IGOS_HELPER_STAGING"
    unset IGOS_HELPER_STAGING IGOS_HELPER_NAME
    # NOTE: IGOS_HELPER_COMMITTED + IGOS_HELPER_USER_CLEANUP are
    # intentionally NOT unset -- the EXIT trap reads both at script
    # termination to drive the user-cleanup pass.
}
