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

# ---- Output margin ------------------------------------------------------
#
# Emit a diagnostic line with a 2-space left margin + hanging indent, wrapped
# to the terminal width (capped), so nothing emitted by a helper or this
# library ever prints to column 0 (matching pkm's own output convention).
# Diagnostics go to stderr. This is presentation only — callers' control flow
# and return codes are unchanged.
igos_helper_emit() {
    local msg="$*" cols
    cols=$(tput cols 2>/dev/null) || cols=80
    case "$cols" in ''|*[!0-9]*) cols=80 ;; esac
    [ "$cols" -gt 100 ] && cols=100
    [ "$cols" -lt 40 ] && cols=40
    printf '%s\n' "$msg" | fold -s -w "$((cols - 4))" \
        | awk 'NR==1 {print "  " $0; next} {print "    " $0}' >&2
}

# ---- Refusals a user can do nothing about -------------------------------
#
# Some refusals in this library can only be reached if the installer script
# that called it is itself faulty — a recording call made before the manifest
# was opened, a required argument missing, a path passed in a shape the
# manifest format cannot carry. The person at the terminal did nothing wrong
# and can change nothing to get past it.
#
# Those refusals used to print only the internal detail, e.g. the name of the
# library function that was called out of order. That is a note to ourselves on
# somebody else's screen: it tells the reader nothing about what happened to
# their machine, whether anything was installed, or what to do next.
#
# This helper prints the part the reader needs FIRST, in plain words, and keeps
# the internal detail on its own line marked as such, so a maintainer receiving
# a screenshot still gets everything. Decided 2026-08-06.
#
# Usage:  igos_helper_internal_fault "<detail for a maintainer>"
igos_helper_internal_fault() {
    igos_helper_emit "This package's installer stopped because of a fault in the installer itself."
    igos_helper_emit "Nothing was installed and nothing on this machine was changed. This is not something you can fix from here — please report the package as broken, and include the line below."
    igos_helper_emit "detail for the maintainer: $*"
}

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
        igos_helper_internal_fault "igos_helper_init was called with no package name"
        return 1
    fi
    # Reject names with shell-special chars to defend against accidental
    # injection (the name lands in JSON + filesystem paths).
    case "$name" in
        *[^a-zA-Z0-9._-]*)
            igos_helper_internal_fault "igos_helper_init was given the package name '$name', which contains characters the manifest cannot carry (allowed: letters, digits, dot, underscore, hyphen)"
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
    # retried and the partial state is superseded.
    local dest_dir="${IGOS_HELPER_MANIFEST_DIR:-/var/lib/igos/helpers}"
    rm -f "$dest_dir/$name.manifest.partial" 2>/dev/null || true

    # Install the EXIT trap. _igos_helper_emit_partial is a no-op when
    # commit has already cleared IGOS_HELPER_COMMITTED.
    trap '_igos_helper_emit_partial' EXIT

    _igos_helper_warn_if_direct "$name"
}

_igos_helper_warn_if_direct() {
    # Say so when the helper was started by hand rather than by pkm.
    #
    # A helper writes its footprint manifest either way, but only pkm reads
    # that manifest into the package database — `pkm import` looks in
    # /var/lib/igos/packages, not /var/lib/igos/helpers. So a helper run
    # directly leaves the payload on disk with the database still describing
    # the PREVIOUS payload: `pkm verify` then reports files it cannot find,
    # `pkm files` lists paths that are gone, and `pkm remove` cannot clean up
    # what it was never told about.
    #
    # The advisory used to live in the helpers' own not-root abort path, which
    # is unreachable when the helper is run with sudo — that is, in exactly
    # the case that produces the untracked install. It prints here instead, on
    # every direct invocation including root. pkm sets PKM_HELPER_INVOCATION
    # when it runs a helper itself, and that variable is not in pkm's
    # environment allowlist, so it cannot arrive from a caller's environment.
    local name="$1"
    if [ -n "${PKM_HELPER_INVOCATION:-}" ]; then
        return 0
    fi
    igos_helper_emit "NOTE: this helper was started directly, not by pkm."
    igos_helper_emit "      The files it installs will NOT be recorded in the package"
    igos_helper_emit "      database, so pkm files/verify/remove will not see them and"
    igos_helper_emit "      the database keeps describing the previously installed"
    igos_helper_emit "      payload. Run 'pkm install $name' (or 'pkm reinstall $name')"
    igos_helper_emit "      instead to install the same payload with tracking."
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
        igos_helper_internal_fault "igos_helper_set_version was called before igos_helper_init"
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
        igos_helper_internal_fault "igos_helper_record_file was called before igos_helper_init"
        return 1
    fi
    local path="$1"
    case "$path" in
        /*) ;;
        *)
            igos_helper_emit "igos_helper_record_file: path must be absolute (got '$path')"
            return 1
            ;;
    esac
    # The staging record is line-oriented (one path per line), so a
    # path with an embedded newline would silently split into two bogus
    # manifest entries — the deposited file untracked, two phantom rows
    # tracked. No curated vendor payload legitimately ships such a
    # name; a payload that does is a red flag. Refuse loudly.
    case "$path" in
        *$'\n'*)
            igos_helper_emit "igos_helper_record_file: path contains an embedded newline — the manifest record is line-oriented and no curated payload legitimately names files this way; refusing"
            return 1
            ;;
    esac
    # Deposit-time ELF word-size audit (RT-1): helper-deposited binaries are
    # the one payload class no build-time gate ever sees (they arrive from
    # the vendor on the user's machine), so the width contract is enforced
    # here, at the moment the deposited file is recorded. Expected class
    # comes from IGOS_HELPER_ELF_CLASS (default 64; a helper that
    # legitimately deposits both widths declares "mixed" before recording).
    # ELF class is byte 5 of the file (magic 7f454c46 + EI_CLASS) — read
    # with od (coreutils, always present), no toolchain dependency.
    #
    # The ordering + laundering hardening (adversarial re-cert findings):
    #  * deposit-then-record is MECHANICALLY enforced — a plain path that
    #    does not exist at record time refuses (record-before-deposit was
    #    an audit bypass, not a convention violation);
    #  * a directory refuses (files[] entries are per-file; record each);
    #  * a symlink is RESOLVED and its target's bytes audited (chrome-class
    #    helpers legitimately record links; a link to a wrong-width target
    #    must not launder). A dangling link records — it carries no bytes —
    #    and pkm's manifest reader re-audits every entry at wire-up time,
    #    which also closes the record-then-swap window.
    #  * the per-run contract is write-once: a helper that flips
    #    IGOS_HELPER_ELF_CLASS mid-run refuses (one helper, one contract).
    local elf_expected="${IGOS_HELPER_ELF_CLASS:-64}"
    if [ -f "$IGOS_HELPER_STAGING/elf_class" ]; then
        local elf_prev
        elf_prev="$(cat "$IGOS_HELPER_STAGING/elf_class")"
        if [ "$elf_prev" != "$elf_expected" ]; then
            igos_helper_emit "igos_helper_record_file: IGOS_HELPER_ELF_CLASS changed mid-run ('${elf_prev}' -> '${elf_expected}') — one helper, one width contract; refusing"
            return 1
        fi
    else
        case "$elf_expected" in
            64|32|mixed) ;;
            *)
                igos_helper_emit "igos_helper_record_file: invalid IGOS_HELPER_ELF_CLASS '${elf_expected}' (valid: 64, 32, mixed)"
                return 1
                ;;
        esac
        printf '%s\n' "$elf_expected" > "$IGOS_HELPER_STAGING/elf_class"
    fi

    local audit_target=""
    if [ -L "$path" ]; then
        audit_target="$(readlink -f -- "$path" 2>/dev/null)" || audit_target=""
        [ -f "$audit_target" ] || audit_target=""   # dangling/dir link: no bytes here; wire-up re-audit covers it
    elif [ ! -e "$path" ]; then
        igos_helper_emit "igos_helper_record_file: ${path} does not exist at record time — deposit the file BEFORE recording it (record-before-deposit bypasses the deposit audit); refusing"
        return 1
    elif [ -d "$path" ]; then
        igos_helper_emit "igos_helper_record_file: ${path} is a directory — record each deposited file individually; refusing"
        return 1
    else
        audit_target="$path"
    fi

    if [ -n "$audit_target" ]; then
        if [ "$elf_expected" = "mixed" ]; then
            # A helper that deposits both widths waives the per-file width
            # check. That waiver is NOT silent: igos_helper_commit writes
            # elf_class into the package's recorded manifest, so the machine
            # keeps the fact and `pkm` can answer for it afterwards.
            #
            # It used to print to the terminal as well, once per run. That line
            # was removed on 2026-08-05: it told a person installing software
            # nothing they could act on, in vocabulary that only means anything
            # inside this project, and the record it duplicated is the durable
            # one. Install output is for the person at the terminal.
            :
        else
            local ident found want
            ident="$(od -An -tx1 -N5 "$audit_target" 2>/dev/null | tr -d ' \n')"
            if [ "${ident%??}" = "7f454c46" ]; then
                found="${ident#7f454c46}"
                want="02"
                [ "$elf_expected" = "32" ] && want="01"
                if [ "$found" != "$want" ]; then
                    local found_bits="64"
                    [ "$found" = "01" ] && found_bits="32"
                    igos_helper_emit "igos_helper_record_file: ${path} resolves to a ${found_bits}-bit ELF but this helper declares elf_class=${elf_expected} — refusing to record (a helper that legitimately deposits that width sets IGOS_HELPER_ELF_CLASS)"
                    return 1
                fi
            fi
        fi
    fi
    printf '%s\n' "$path" >> "$IGOS_HELPER_STAGING/files"
}

igos_helper_record_symlink() {
    # Append a symlink entry to the manifest's symlinks[] array.
    # <link_path> is the symlink itself (the path that pkm tracks +
    # unlinks on remove); <target> is the path the symlink points at
    # (informational; pkm does NOT delete the target on remove unless
    # it is independently in files[]).
    if [ -z "$IGOS_HELPER_STAGING" ]; then
        igos_helper_internal_fault "igos_helper_record_symlink was called before igos_helper_init"
        return 1
    fi
    local link_path="$1"
    local target="$2"
    case "$link_path" in
        /*) ;;
        *)
            igos_helper_emit "igos_helper_record_symlink: link_path must be absolute (got '$link_path')"
            return 1
            ;;
    esac
    # The symlink record is tab-separated + line-oriented, so an
    # embedded tab or newline in either field would corrupt the split
    # (same class as record_file's newline refuse). Refuse loudly.
    case "${link_path}${target}" in
        *$'\n'* | *$'\t'*)
            igos_helper_emit "igos_helper_record_symlink: link_path/target contains an embedded newline or tab — the record is tab-separated line-oriented; refusing"
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
        igos_helper_internal_fault "igos_helper_record_dep was called before igos_helper_init"
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
        igos_helper_internal_fault "igos_helper_record_post_install_action was called before igos_helper_init"
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
        igos_helper_internal_fault "igos_helper_commit was called before igos_helper_init"
        return 1
    fi

    # #5 (PI, .192): refresh the desktop + MIME caches so any .desktop the
    # helper just installed becomes resolvable IMMEDIATELY — including URI
    # x-scheme-handlers (e.g. code-url-handler.desktop's x-scheme-handler/vscode
    # for the VS Code "Continue with GitHub" callback). Without this, the
    # handler ships correct but isn't in mimeinfo.cache yet, so "Open With"
    # reports "No Apps Available" until a manual refresh. Done here (every app
    # helper installs .desktop files) rather than per-helper; idempotent and
    # harmless if none were installed. NOTE: record_post_install_action only
    # LOGS for transparency — it does not execute — so we RUN the commands here
    # and record them for the history.
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database /usr/share/applications 2>/dev/null || true
        igos_helper_record_post_install_action "update-desktop-database /usr/share/applications"
    fi
    if command -v update-mime-database >/dev/null 2>&1; then
        update-mime-database /usr/share/mime 2>/dev/null || true
        igos_helper_record_post_install_action "update-mime-database /usr/share/mime"
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
        igos_helper_emit "igos_helper_commit: python3 not found in PATH"
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

elf_class = "64"
ecfile = os.path.join(staging, "elf_class")
if os.path.exists(ecfile):
    with open(ecfile, "r", encoding="utf-8") as f:
        elf_class = f.read().strip() or "64"

manifest = {
    "version": 1,
    "name": name,
    "version_installed": version,
    "elf_class": elf_class,
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
        igos_helper_emit "igos_helper_commit: JSON assembly failed (rc=$rc); manifest NOT written"
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

# ---- K21.E: signed-Release verification for vendor .deb downloads -------
#
# Closes the apt-pool-listing sha256 hardening item flagged in K21.C
# landing report (3-helpers supply-chain hardening; brave/chrome/edge).
# Adds an apt-style integrity-chain verification function that helpers
# call before extracting any downloaded .deb. Security-aligned:
#
#   Rule 4 (every package decision is a security decision): 3 vendor
#       helpers ship closed-source binaries via vendor-controlled
#       update channels -- highest-attack-surface helper class.
#   Rule 9 (update infrastructure must be trustworthy; signed +
#       verified + reproducible where achievable): InRelease GPG
#       signature -> Packages sha256 -> .deb sha256 IS the canonical
#       apt-style integrity chain.
#   Rule 10 (default-deny on signature/verification failure): every
#       step fails closed; non-zero return forces caller to refuse
#       install with a loud error.

igos_helper_verify_deb_via_signed_release() {
    # Verify a downloaded .deb's integrity via the apt signed-Release
    # chain. Caller MUST refuse install on non-zero return.
    #
    # Args:
    #   $1 deb_filename     basename of the .deb (matched against
    #                       Filename: field in Packages, e.g.
    #                       "brave-browser_1.90.124_amd64.deb")
    #   $2 deb_path_on_disk absolute path to the downloaded .deb
    #   $3 apt_pool_base_url vendor's apt repo base URL, no trailing
    #                       slash (e.g. "https://brave-browser-apt-
    #                       release.s3.brave.com"). The function
    #                       appends /dists/<dist>/InRelease etc.
    #   $4 keyring_path     absolute path to GPG binary keyring (the
    #                       dearmored .gpg installed at build time
    #                       from the .asc shipped in repo)
    #   $5 dist_codename    apt distribution name (e.g. "stable")
    #   $6 component        OPTIONAL apt component (default "main";
    #                       Spotify uses "non-free", Valve uses "steam")
    #   $7 sha1_scoped_fpr  OPTIONAL full 40-hex GPG fingerprint that
    #                       enables the SCOPED weak-digest posture for
    #                       step 2 (steam helper ONLY -- operator
    #                       decision 2, 2026-07-02). Empty = strict,
    #                       byte-identical to the pre-7th-arg behavior.
    #
    # Six-step verification chain:
    #   1. Download InRelease from <base>/dists/<dist>/InRelease
    #   2. Verify InRelease GPG signature against pinned keyring
    #      (gpgv -- no trust db, no key import, no implicit fetch;
    #      SHA1 signature digests are REJECTED -- hardened 2026-07-02 --
    #      unless the caller supplies the scoped $7 exception)
    #   3. Parse Packages reference + sha256 from InRelease SHA256
    #      section (the MD5Sum / SHA1 / SHA512 sections list the
    #      same paths -- MUST restrict to SHA256 or the wrong hash
    #      lands)
    #   4. Download main/binary-amd64/Packages + verify its sha256
    #      against the InRelease-stated value
    #   5. Parse Packages for deb_filename + extract its sha256
    #   6. Compute sha256 of deb_path_on_disk + match against
    #      Packages-stated value
    #
    # Returns:
    #   0 -- all 6 steps PASSED; caller may proceed to install
    #   1 -- a verification step failed (network, signature, sha
    #         mismatch, missing entry); caller MUST refuse install
    #   2 -- caller-side usage error (missing arg, missing tool,
    #         missing file); caller should treat as misconfiguration
    local deb_filename="$1"
    local deb_path="$2"
    local apt_base="$3"
    local keyring="$4"
    local dist="$5"
    # K21.F: optional 6th arg `component`. apt repos can use components
    # other than "main" (Spotify's stable apt uses "non-free"); defaults
    # to "main" to preserve K21.E call-site behavior (brave/chrome/edge
    # all use main). Caller supplies the component name when calling
    # against repos that publish under a non-main component.
    local component="${6:-main}"

    # ---- Argument + environment validation (return 2 on misuse) ----
    if [ -z "$deb_filename" ] || [ -z "$deb_path" ] || [ -z "$apt_base" ] \
       || [ -z "$keyring" ] || [ -z "$dist" ]; then
        igos_helper_internal_fault "igos_helper_verify_deb_via_signed_release was called with a required argument missing (expects: deb filename, deb path, apt base URL, keyring, distribution, optional component, optional pinned fingerprint)"
        return 2
    fi
    if [ ! -f "$deb_path" ]; then
        igos_helper_emit "igos_helper_verify_deb_via_signed_release: .deb file not found: $deb_path"
        return 2
    fi
    if [ ! -f "$keyring" ]; then
        igos_helper_emit "igos_helper_verify_deb_via_signed_release: keyring file not found: $keyring"
        return 2
    fi
    local tool
    for tool in wget gpgv sha256sum awk mktemp; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            igos_helper_emit "igos_helper_verify_deb_via_signed_release: required tool not in PATH: $tool"
            return 2
        fi
    done

    # ---- Per-call workdir (caller cleanup via explicit rm at exit paths) ----
    local workdir
    workdir=$(mktemp -d -t igos-verify-deb-XXXXXXXX) || {
        igos_helper_emit "igos_helper_verify_deb_via_signed_release: mktemp failed"
        return 2
    }

    # Strip trailing slash from apt_base so URL composition is clean
    local base="${apt_base%/}"

    # ---- Step 1: Download InRelease ----
    local inrelease_url="${base}/dists/${dist}/InRelease"
    if ! wget -q -O "$workdir/InRelease" "$inrelease_url"; then
        igos_helper_emit "igos_helper_verify_deb_via_signed_release: STEP 1 FAIL -- could not download InRelease from $inrelease_url"
        rm -rf "$workdir"
        return 1
    fi
    if [ ! -s "$workdir/InRelease" ]; then
        igos_helper_emit "igos_helper_verify_deb_via_signed_release: STEP 1 FAIL -- InRelease is empty at $inrelease_url"
        rm -rf "$workdir"
        return 1
    fi

    # ---- Step 2: Verify InRelease GPG signature against pinned keyring ----
    # Optional 7th arg `sha1_scoped_fpr` (steam helper ONLY — operator
    # decision 2, 2026-07-02): when EMPTY (every existing caller —
    # chrome/brave/edge/spotify), step 2 is a single STRICT gpgv that
    # rejects SHA1 digests outright (hardened 2026-07-02, operator-
    # authorized — see the branch comment below) with NO retry.
    # When SET to a pinned key fingerprint, the
    # SCOPED weak-digest posture applies: a STRICT attempt that REJECTS
    # SHA1 runs first, and ONLY on the specific weak-digest failure does a
    # permissive retry run — gated on the InRelease being Good AND signed
    # by the pinned fingerprint, logged loudly. The sha256 chain (steps
    # 3-6) binds the bytes regardless, so a weak SIGNATURE digest never
    # weakens content integrity. Self-retiring: the day Valve's signature
    # digest is SHA256+ (it is SHA512 as of 2026-06-26), the strict attempt
    # passes and the permissive path never executes.
    local sha1_scoped_fpr="${7:-}"
    if [ -n "$sha1_scoped_fpr" ]; then
        # The scoped pin must be a FULL 40-hex fingerprint. A short or
        # malformed value would degrade the retry's stderr pin-match to
        # a substring accident -- checked gate, not an assumption.
        if ! printf '%s' "${sha1_scoped_fpr// /}" | grep -qiE '^[0-9a-f]{40}$'; then
            igos_helper_emit "igos_helper_verify_deb_via_signed_release: sha1_scoped_fpr must be a full 40-hex GPG fingerprint (got: ${sha1_scoped_fpr})"
            rm -rf "$workdir"
            return 2
        fi
    fi
    if [ -z "$sha1_scoped_fpr" ]; then
        # HARDENED 2026-07-02 (authorized): the unscoped path
        # rejects SHA1 signature digests outright. Default gpgv rejects
        # only MD5, so the pre-hardening path silently accepted SHA1 --
        # measured before landing: all four callers' vendors (brave/
        # chrome/edge/spotify) sign SHA256 today and their live InRelease
        # strict-verified identically under this flag in the shipped
        # chroot gpgv 2.5.17, so this ships behavior-identical and only
        # ever diverges by refusing a future vendor regression to SHA1
        # loudly. If a vendor DOES regress, that is an operator decision
        # (the scoped 7th-arg exception below is the sanctioned shape).
        if ! gpgv --weak-digest SHA1 --keyring "$keyring" "$workdir/InRelease" 2>"$workdir/gpgv.err"; then
            igos_helper_emit "igos_helper_verify_deb_via_signed_release: STEP 2 FAIL -- InRelease GPG signature verification FAILED. SHA-1 signature digests are refused here; if the message below reports a weak digest, this vendor has moved to SHA-1 and nothing will install from it until that is resolved:"
            igos_helper_emit "$(cat "$workdir/gpgv.err")"
            rm -rf "$workdir"
            return 1
        fi
    else
        # Strict-first: reject SHA1 (the hardened posture). LC_ALL=C on
        # every gpgv whose stderr is PARSED below (the weak-digest
        # classifier + the Good-signature/fingerprint pin) -- a localized
        # message would otherwise dodge the greps (fail-closed, but
        # wrongly refusing on non-English locales).
        if LC_ALL=C gpgv --weak-digest SHA1 --keyring "$keyring" "$workdir/InRelease" 2>"$workdir/gpgv.err"; then
            : # strict passed — the weak path is dormant (the expected case today)
        else
            # Strict failed. Only a SPECIFIC weak-digest rejection may
            # trigger the scoped retry; any other failure is a real bad
            # signature and hard-fails here.
            if ! grep -qiE 'weak|SHA1|digest algorithm' "$workdir/gpgv.err"; then
                igos_helper_emit "igos_helper_verify_deb_via_signed_release: STEP 2 FAIL -- InRelease signature invalid (not a weak-digest condition):"
                igos_helper_emit "$(cat "$workdir/gpgv.err")"
                rm -rf "$workdir"
                return 1
            fi
            # Permissive retry (default gpgv permits SHA1), pinned to the
            # Valve fingerprint: the InRelease must verify Good AND carry
            # the pinned key. Loud, self-documenting log.
            if ! LC_ALL=C gpgv --keyring "$keyring" "$workdir/InRelease" 2>"$workdir/gpgv.retry.err"; then
                igos_helper_emit "igos_helper_verify_deb_via_signed_release: STEP 2 FAIL -- permissive retry still could not verify InRelease:"
                igos_helper_emit "$(cat "$workdir/gpgv.retry.err")"
                rm -rf "$workdir"
                return 1
            fi
            local fpr_nospace="${sha1_scoped_fpr// /}"
            if ! grep -qi 'Good signature' "$workdir/gpgv.retry.err" \
               || ! tr -d ' ' < "$workdir/gpgv.retry.err" | grep -qi "$fpr_nospace"; then
                igos_helper_emit "igos_helper_verify_deb_via_signed_release: STEP 2 FAIL -- weak-digest retry did NOT resolve to the pinned Valve key ${sha1_scoped_fpr}:"
                igos_helper_emit "$(cat "$workdir/gpgv.retry.err")"
                rm -rf "$workdir"
                return 1
            fi
            igos_helper_emit "SECURITY NOTICE: Valve signs its package index with a SHA-1 signature digest, which is weak and is not accepted for any other vendor here (upstream report: steam-for-linux#12050). The signature was checked against Valve's pinned key ${sha1_scoped_fpr} and is good, and every downloaded byte is still bound by SHA-256 checksums from that signed index, which are verified below. This narrower acceptance applies to Steam only, and stops applying as soon as Valve moves to a stronger digest."
        fi
    fi

    # ---- Step 3: Parse Packages sha256 from InRelease SHA256 section ----
    local packages_relpath="${component}/binary-amd64/Packages"
    local expected_packages_sha256
    expected_packages_sha256=$(awk -v target="$packages_relpath" '
        /^MD5Sum:|^SHA1:|^SHA512:/ { p = 0; next }
        /^SHA256:/ { p = 1; next }
        /^[A-Z][a-zA-Z0-9-]*:/ { p = 0 }
        p && $3 == target { print $1; exit }
    ' "$workdir/InRelease")

    if [ -z "$expected_packages_sha256" ]; then
        igos_helper_emit "igos_helper_verify_deb_via_signed_release: STEP 3 FAIL -- could not find ${packages_relpath} sha256 in InRelease SHA256 section"
        rm -rf "$workdir"
        return 1
    fi

    # ---- Step 4: Download Packages + verify sha256 ----
    local packages_url="${base}/dists/${dist}/${packages_relpath}"
    if ! wget -q -O "$workdir/Packages" "$packages_url"; then
        igos_helper_emit "igos_helper_verify_deb_via_signed_release: STEP 4 FAIL -- could not download Packages from $packages_url"
        rm -rf "$workdir"
        return 1
    fi
    local actual_packages_sha256
    actual_packages_sha256=$(sha256sum "$workdir/Packages" | awk '{print $1}')
    if [ "$actual_packages_sha256" != "$expected_packages_sha256" ]; then
        igos_helper_emit "igos_helper_verify_deb_via_signed_release: STEP 4 FAIL -- Packages sha256 MISMATCH:"
        echo "  expected (per InRelease): $expected_packages_sha256" >&2
        echo "  actual:                   $actual_packages_sha256" >&2
        rm -rf "$workdir"
        return 1
    fi

    # ---- Step 5: Parse Packages for deb_filename + extract its sha256 ----
    # Packages is RFC 822-style stanzas separated by blank lines. Each stanza
    # has Filename: (path including pool/ prefix) and SHA256: fields, in NO
    # guaranteed order: Microsoft (vscode/edge) emit SHA256 BEFORE Filename.
    # Capture the Filename-basename match and the SHA256 INDEPENDENTLY within
    # each stanza and decide at the blank-line boundary — a Filename-gated
    # SHA256 capture misses the hash entirely when SHA256 comes first, failing
    # with "not found (or has no SHA256 field)" on a perfectly valid stanza.
    local expected_deb_sha256
    expected_deb_sha256=$(awk -v target="$deb_filename" '
        BEGIN { fn_match = 0; sha = ""; found = 0 }
        /^Filename:/ { n = split($2, parts, "/"); if (parts[n] == target) fn_match = 1 }
        /^SHA256:/ { sha = $2 }
        /^$/ {
            if (fn_match && sha != "" && !found) {
                print sha
                found = 1
                exit
            }
            fn_match = 0
            sha = ""
        }
        END { if (fn_match && sha != "" && !found) print sha }
    ' "$workdir/Packages")

    if [ -z "$expected_deb_sha256" ]; then
        igos_helper_emit "igos_helper_verify_deb_via_signed_release: STEP 5 FAIL -- $deb_filename not found in Packages (or has no SHA256 field)"
        rm -rf "$workdir"
        return 1
    fi

    # ---- Step 6: Verify the downloaded .deb's sha256 ----
    local actual_deb_sha256
    actual_deb_sha256=$(sha256sum "$deb_path" | awk '{print $1}')
    if [ "$actual_deb_sha256" != "$expected_deb_sha256" ]; then
        igos_helper_emit "igos_helper_verify_deb_via_signed_release: STEP 6 FAIL -- .deb sha256 MISMATCH for $deb_filename:"
        echo "  expected (per Packages): $expected_deb_sha256" >&2
        echo "  actual:                  $actual_deb_sha256" >&2
        rm -rf "$workdir"
        return 1
    fi

    # ---- All 6 steps PASSED ----
    echo "  Verified: $deb_filename (sha256 $expected_deb_sha256)"
    rm -rf "$workdir"
    return 0
}

# K21.E helper: find the latest version of a package in apt Packages and
# return its filename (basename only). Reads from <apt_base>/dists/<dist>/
# <component>/binary-amd64/Packages WITHOUT verification -- callers MUST
# treat the returned filename as untrusted input and re-verify via
# igos_helper_verify_deb_via_signed_release before installing the .deb.
#
# This separation (find-without-trust + verify-with-trust) keeps the
# verification function single-responsibility while letting helpers do
# the per-vendor version lookup uniformly.
#
# Args:
#   $1 pkg_name        apt package name (e.g. "brave-browser",
#                      "google-chrome-stable", "microsoft-edge-stable",
#                      "spotify-client")
#   $2 apt_pool_base   vendor apt base URL (no trailing slash)
#   $3 dist_codename   apt distribution name (e.g. "stable")
#   $4 component       OPTIONAL apt component name (defaults to "main";
#                      Spotify's stable apt uses "non-free"); K21.F
#                      addition preserves K21.E call-site behavior since
#                      the 4th arg defaults when omitted
#
# Prints to stdout: <deb_filename>|<version>|<filename_path_in_pool>
# Returns 0 on found; 1 on not-found / download failure / parse failure.

igos_helper_find_latest_deb_in_packages() {
    local pkg_name="$1"
    local apt_base="$2"
    local dist="$3"
    # K21.F: optional 4th arg `component`; defaults to "main" to preserve
    # K21.E call-site behavior.
    local component="${4:-main}"

    if [ -z "$pkg_name" ] || [ -z "$apt_base" ] || [ -z "$dist" ]; then
        igos_helper_internal_fault "igos_helper_find_latest_deb_in_packages was called with the wrong arguments (expects: package name, apt base URL, distribution, optional component)"
        return 1
    fi

    local workdir
    workdir=$(mktemp -d -t igos-find-deb-XXXXXXXX) || return 1
    local base="${apt_base%/}"
    local packages_url="${base}/dists/${dist}/${component}/binary-amd64/Packages"

    if ! wget -q -O "$workdir/Packages" "$packages_url"; then
        igos_helper_emit "igos_helper_find_latest_deb_in_packages: could not download $packages_url"
        rm -rf "$workdir"
        return 1
    fi

    # Parse stanzas matching the target package; emit version|filename
    # per match; sort by version (dpkg-style sort -V is good enough for
    # apt version comparison in this helper-class scope) + tail the
    # highest.
    local result
    result=$(awk -v target="$pkg_name" '
        /^Package:/ { found = ($2 == target); ver = ""; fn = "" }
        found && /^Version:/ { ver = $2 }
        found && /^Filename:/ { fn = $2 }
        found && /^$/ {
            if (ver != "" && fn != "") print ver "|" fn
            found = 0
        }
        END { if (found && ver != "" && fn != "") print ver "|" fn }
    ' "$workdir/Packages" | sort -t'|' -k1 -V | tail -1)

    rm -rf "$workdir"

    if [ -z "$result" ]; then
        igos_helper_emit "igos_helper_find_latest_deb_in_packages: no $pkg_name entry found in Packages at $packages_url"
        return 1
    fi

    local version="${result%%|*}"
    local filename_path="${result#*|}"
    local n
    n=$(awk -F'/' '{print NF}' <<< "$filename_path")
    local basename
    basename=$(awk -F'/' '{print $NF}' <<< "$filename_path")

    # Output: <basename>|<version>|<path_in_pool>
    echo "${basename}|${version}|${filename_path}"
    return 0
}
