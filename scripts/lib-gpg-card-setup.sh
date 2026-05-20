#!/usr/bin/env bash
# lib-gpg-card-setup.sh — Sourceable library for GPG smartcard setup discipline.
#
# Detects the host's smartcard stack (pcscd-based vs scdaemon-built-in-CCID)
# and applies the appropriate setup discipline. Two host classes are supported:
#
#   Class A: pcscd-based stack (e.g. Tails ceremony hosts, some Linux distros
#     where the offline-debs bundle for the 2026-04-30 ceremony added pcscd).
#     pcscd-binary present at $PATH. scdaemon talks to the card via pcscd.
#     Requires the canonical 4-line scdaemon.conf (disable-ccid + pcsc-shared
#     + debug-level guru + log-file) to avoid the scdaemon-vs-pcscd USB-
#     interface conflict that bit us during the 2026-04-30 ceremony.
#
#   Class B: scdaemon-built-in-CCID stack (operator's IGOS laptop, most
#     vanilla Linux desktops). pcscd-binary NOT present. scdaemon talks
#     to the card directly via its internal CCID driver. DO NOT write
#     'disable-ccid' to scdaemon.conf on this class -- that BREAKS the
#     working setup. Leave the existing config alone; let the system
#     default behavior continue.
#
# The library auto-detects which class applies via `command -v pcscd`.
#
# Both classes still need: GPG_TTY exported, gpg --card-status sanity check,
# secret-key-in-keyring verification.
#
# Usage (sourced into a signing wrapper):
#   export GPG_CARD_DEBUG=1                            # opt-in verbose
#   export GPG_CARD_LOG="/home/<user>/tmp/script.log"  # debug log target
#   export GPG_CARD_DRY_RUN=0                          # 1 = no mutations
#   source "${SCRIPT_DIR}/lib-gpg-card-setup.sh"       # SCRIPT_DIR = caller's dir
#   gpg_card_setup_init                                # idempotent setup
#   gpg_card_verify_key "<40-hex-fingerprint>"         # verify key present
#
# Usage (direct invocation for diagnostics):
#   bash scripts/lib-gpg-card-setup.sh [--debug] [--dry-run]
#
# Lives at scripts/lib-gpg-card-setup.sh (in-repo canonical path; promoted
# from /home/<user>/tmp/ scratch path at commit f6e5f44f).

# Library does not `set -e` on its own — caller is expected to set its own
# safety options. All public functions use explicit `return N` for control
# flow + `|| return 1` fallbacks so a non-strict-mode caller still gets
# failure propagation.

# ============================================================
# DEFAULT ENV STATE (caller can override before sourcing)
# ============================================================
: "${GPG_CARD_DEBUG:=0}"
: "${GPG_CARD_LOG:=}"
: "${GPG_CARD_DRY_RUN:=0}"

# ============================================================
# INTERNAL HELPERS (prefix __gpg_card_ for namespacing)
# ============================================================

# pcscd present in $PATH?
__gpg_card_pcscd_present() {
    command -v pcscd >/dev/null 2>&1
}

# Class-A canonical scdaemon.conf (pcscd-aware). Used ONLY when pcscd is
# installed. The log-file path is composed at setup time under $HOME/tmp/
# per D-016.
__gpg_card_scdaemon_conf_class_a() {
    local logfile="${HOME}/tmp/scdaemon.log"
    cat <<EOF
# Managed by lib-gpg-card-setup.sh (Class A pcscd-aware) -- TEMPORARY.
# Written at script start; original config (if any) restored at script exit.
disable-ccid
pcsc-shared
debug-level guru
log-file ${logfile}
EOF
}

# Canonical gpg-agent.conf content (both classes use this for clean pinentry).
__gpg_card_gpg_agent_conf_content() {
    cat <<EOF
# Managed by lib-gpg-card-setup.sh -- TEMPORARY.
# Written at script start; original config (if any) restored at script exit.
pinentry-program /usr/bin/pinentry-tty
EOF
}

# Suffix appended to <conf-path> when backing up operator's existing file.
__GPG_CARD_BACKUP_SUFFIX=".sign-with-gpg-backup"

__gpg_card_debug() {
    [[ "${GPG_CARD_DEBUG}" == "1" ]] || return 0
    local msg
    msg="[gpg-card-setup $(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
    echo "${msg}" >&2
    [[ -n "${GPG_CARD_LOG}" ]] && echo "${msg}" >> "${GPG_CARD_LOG}"
    return 0
}

__gpg_card_info() { echo "[gpg-card-setup] $*"; }
__gpg_card_warn() { echo "[gpg-card-setup WARN] $*" >&2; }
__gpg_card_ok()   { echo "[gpg-card-setup OK] $*"; }
__gpg_card_die()  { echo "[gpg-card-setup FATAL] $*" >&2; return 1; }

# ============================================================
# SAVE / RESTORE HELPERS (config-file swap mechanism)
# ============================================================
# The library temporarily replaces ~/.gnupg/{gpg-agent,scdaemon}.conf with
# canonical content for the duration of a signing run, then restores the
# original (if any) at script exit via an EXIT trap. Goal: host state is
# unchanged after the script regardless of how it ends -- no operator
# look-see or manual restore required between runs.

# Swap-in: back up existing config to <path>.sign-with-gpg-backup (if any),
# then write our canonical content at the original path.
# Args: $1 = original path, $2 = desired content (as a string)
__gpg_card_swap_in_config() {
    local original="$1"
    local content="$2"
    local backup="${original}${__GPG_CARD_BACKUP_SUFFIX}"

    # Stale-backup detection: a backup file exists from a previous run.
    # If the current original looks like one of ours (matches our temp
    # content), the previous run crashed before restoring; auto-recover.
    # Otherwise the operator may have made manual changes; bail safely.
    if [[ -f "${backup}" ]]; then
        if [[ -f "${original}" ]] && diff -q <(echo "${content}") "${original}" >/dev/null 2>&1; then
            __gpg_card_info "Recovering from previous crashed run (restoring backup at ${backup} -> ${original})"
            rm -f "${original}"
            mv "${backup}" "${original}" || { __gpg_card_die "Failed to restore stale backup ${backup}"; return 1; }
        else
            __gpg_card_die "Stale backup at ${backup}, but current ${original} is not auto-generated. Inspect both files + reconcile manually + remove ${backup} + re-run."
            return 1
        fi
    fi

    if [[ "${GPG_CARD_DRY_RUN}" == "1" ]]; then
        __gpg_card_info "[DRY-RUN] would: back up ${original} (if present) + write temp content for this run"
        return 0
    fi

    # Move existing original to backup (if any)
    if [[ -f "${original}" ]]; then
        mv "${original}" "${backup}" || { __gpg_card_die "Failed to back up ${original}"; return 1; }
        __gpg_card_debug "Backed up ${original} -> ${backup}"
    fi

    # Write our temporary content
    echo "${content}" > "${original}"
    chmod 600 "${original}"
    __gpg_card_debug "Wrote temporary ${original}"
}

# Restore-one: remove our temp config, restore backup (if any), so the
# original host state is reinstated. Idempotent.
__gpg_card_restore_one_config() {
    local original="$1"
    local backup="${original}${__GPG_CARD_BACKUP_SUFFIX}"

    if [[ -f "${backup}" ]]; then
        # Backup exists -> remove our temp + restore backup
        rm -f "${original}"
        mv "${backup}" "${original}" 2>/dev/null || true
    elif [[ -f "${original}" ]]; then
        # No backup -> the original wasn't there before we ran; the file
        # currently at the path was written by us. Remove it so the host
        # returns to "no file" state.
        rm -f "${original}"
    fi
}

# EXIT trap target: restore all configs + kill gpg-agent so the restored
# state takes effect immediately on the next gpg invocation.
__gpg_card_cleanup_on_exit() {
    __gpg_card_restore_one_config "${HOME}/.gnupg/gpg-agent.conf"
    __gpg_card_restore_one_config "${HOME}/.gnupg/scdaemon.conf"
    gpgconf --kill gpg-agent 2>/dev/null || true
}

# Register the EXIT trap (also catches INT + TERM signals).
# Caller should not overwrite this trap; if a future caller needs its own
# trap, it should chain with __gpg_card_cleanup_on_exit.
__gpg_card_register_cleanup_trap() {
    trap '__gpg_card_cleanup_on_exit' EXIT INT TERM
    __gpg_card_debug "EXIT trap registered for config save/restore"
}

# ============================================================
# PUBLIC: gpg_card_setup_init — idempotent host-class-aware setup
# ============================================================
gpg_card_setup_init() {
    local gnupg_home="${HOME}/.gnupg"
    local scratch_dir="${HOME}/tmp"

    __gpg_card_debug "gpg_card_setup_init entered; DRY_RUN=${GPG_CARD_DRY_RUN}"

    # --- Ensure ~/tmp exists (D-016 canonical scratch)
    if [[ ! -d "${scratch_dir}" ]]; then
        if [[ "${GPG_CARD_DRY_RUN}" == "1" ]]; then
            __gpg_card_info "[DRY-RUN] would: mkdir -p ${scratch_dir}"
        else
            mkdir -p "${scratch_dir}" || { __gpg_card_die "Cannot create ${scratch_dir}"; return 1; }
        fi
    fi

    # --- Ensure ~/.gnupg exists with 700 perms (gpg requires this)
    if [[ ! -d "${gnupg_home}" ]]; then
        if [[ "${GPG_CARD_DRY_RUN}" == "1" ]]; then
            __gpg_card_info "[DRY-RUN] would: mkdir -p ${gnupg_home} && chmod 700"
        else
            mkdir -p "${gnupg_home}" || { __gpg_card_die "Cannot create ${gnupg_home}"; return 1; }
            chmod 700 "${gnupg_home}"
        fi
    fi

    # --- Host class detection
    local host_class
    if __gpg_card_pcscd_present; then
        host_class="A"
        __gpg_card_info "Host class: A (pcscd-based stack detected)"
    else
        host_class="B"
        __gpg_card_info "Host class: B (scdaemon built-in CCID; pcscd not installed)"
    fi
    __gpg_card_debug "host_class=${host_class}"

    # --- Register EXIT trap NOW, before any config swaps, so any
    # subsequent failure (including signal interrupts) still restores
    # whatever we swap in. If the script is SIGKILL'd or the OS panics,
    # the next invocation detects the stale backup + auto-recovers via
    # __gpg_card_swap_in_config's stale-backup logic.
    __gpg_card_register_cleanup_trap

    # --- Class-A only: pcscd active + canonical scdaemon.conf swap-in.
    # Class B skips pcscd ops entirely (no pcscd installed; scdaemon's
    # built-in CCID driver does the work).
    if [[ "${host_class}" == "A" ]]; then
        if ! systemctl is-active --quiet pcscd 2>/dev/null; then
            if [[ "${GPG_CARD_DRY_RUN}" == "1" ]]; then
                __gpg_card_info "[DRY-RUN] would: systemctl start pcscd"
            else
                __gpg_card_info "pcscd inactive; attempting start..."
                systemctl start pcscd 2>>"${GPG_CARD_LOG:-/dev/null}" || \
                    __gpg_card_warn "systemctl start pcscd did not succeed; continuing (gpg --card-status will reveal if this matters)"
            fi
        else
            __gpg_card_debug "pcscd already active"
        fi

        # Swap in canonical scdaemon.conf for the duration of this run
        local scdaemon_content
        scdaemon_content="$(__gpg_card_scdaemon_conf_class_a)"
        __gpg_card_swap_in_config "${gnupg_home}/scdaemon.conf" "${scdaemon_content}"
        if [[ "${GPG_CARD_DRY_RUN}" == "0" ]]; then
            gpgconf --kill scdaemon 2>/dev/null || true
            __gpg_card_debug "scdaemon killed (will respawn with new conf)"
            sleep 1
        fi
    else
        __gpg_card_debug "Class B: skipping pcscd ops + scdaemon.conf swap (system default config + built-in CCID driver are doing the job)"
    fi

    # --- gpg-agent.conf swap: write our canonical content (pinentry-tty)
    # for the duration of this run. Original is restored at exit via the
    # trap registered above. Avoids terminal-mode garbling on PIN entry
    # that the default pinentry-curses can leave behind.
    local pinentry_tty_bin="/usr/bin/pinentry-tty"
    if [[ ! -x "${pinentry_tty_bin}" ]]; then
        __gpg_card_warn "pinentry-tty not installed at ${pinentry_tty_bin}; cannot configure clean PIN prompt. Terminal may garble on PIN entry. Skipping gpg-agent.conf swap."
    else
        local gpg_agent_content
        gpg_agent_content="$(__gpg_card_gpg_agent_conf_content)"
        __gpg_card_swap_in_config "${gnupg_home}/gpg-agent.conf" "${gpg_agent_content}"
        if [[ "${GPG_CARD_DRY_RUN}" == "0" ]]; then
            gpgconf --kill gpg-agent 2>/dev/null || true
            sleep 1
            __gpg_card_debug "gpg-agent killed (will respawn with new pinentry config)"
        fi
    fi

    # --- Ensure GPG_TTY is exported (both classes; gpg-agent needs it for PIN prompts)
    if [[ -z "${GPG_TTY:-}" ]]; then
        local tty_value
        if tty_value="$(tty 2>/dev/null)"; then
            export GPG_TTY="${tty_value}"
            __gpg_card_debug "GPG_TTY set to ${GPG_TTY}"
        else
            __gpg_card_warn "tty unavailable; GPG_TTY left unset. PIN prompts may fail in this shell."
        fi
    else
        __gpg_card_debug "GPG_TTY already set to ${GPG_TTY}"
    fi

    # --- Final sanity: gpg --card-status must succeed (both classes)
    if [[ "${GPG_CARD_DRY_RUN}" == "1" ]]; then
        __gpg_card_info "[DRY-RUN] would: gpg --card-status (verify token present)"
    else
        if gpg --card-status >/dev/null 2>&1; then
            __gpg_card_ok "gpg --card-status succeeds (token present + readable)"
            if [[ "${GPG_CARD_DEBUG}" == "1" && -n "${GPG_CARD_LOG}" ]]; then
                {
                    echo "--- gpg --card-status output $(date -u +%Y-%m-%dT%H:%M:%SZ) ---"
                    gpg --card-status 2>&1 || true
                    echo "--- end ---"
                } >> "${GPG_CARD_LOG}"
            fi
        else
            __gpg_card_die "gpg --card-status FAILED. Is the Nitrokey plugged in? Run: gpg --card-status -- diagnose."
            return 1
        fi
    fi

    return 0
}

# ============================================================
# PUBLIC: gpg_card_verify_key — ensure pubkey imported + ownertrust set
# + secret-key stub registered for the named signing fingerprint
# ============================================================
# Args: $1 = fingerprint (40 hex chars, no spaces)
#
# On a fresh host (no prior gpg signing on this machine) the keyring is
# empty. For gpg --detach-sign and gpg --verify to work, three things
# must be present: (a) the master pubkey imported, (b) ownertrust set
# to ultimate (so verification of the operator's own sigs doesn't warn),
# (c) a secret-key stub linking the on-card private key to the imported
# pubkey. This function handles all three idempotently. Subsequent runs
# are no-ops if the state is already correct.
gpg_card_verify_key() {
    local fpr="${1:-}"
    if [[ -z "${fpr}" ]]; then
        __gpg_card_die "gpg_card_verify_key: missing fingerprint argument"
        return 1
    fi

    if [[ "${GPG_CARD_DRY_RUN}" == "1" ]]; then
        __gpg_card_info "[DRY-RUN] would: ensure pubkey ${fpr} imported + ownertrust ultimate + secret-key stub via card-status"
        return 0
    fi

    # Step 1: pubkey present in local keyring?
    if ! gpg --list-keys "${fpr}" >/dev/null 2>&1; then
        __gpg_card_info "Pubkey ${fpr} not in local keyring; fetching from keys.openpgp.org..."
        if ! gpg --keyserver hkps://keys.openpgp.org --recv-keys "${fpr}" 2>>"${GPG_CARD_LOG:-/dev/null}"; then
            __gpg_card_die "Failed to fetch pubkey ${fpr} from keys.openpgp.org. Check network connectivity."
            return 1
        fi
        if ! gpg --list-keys "${fpr}" >/dev/null 2>&1; then
            __gpg_card_die "Pubkey fetch reported success but ${fpr} still not in keyring."
            return 1
        fi
        __gpg_card_ok "Pubkey ${fpr} imported from keys.openpgp.org"
    else
        __gpg_card_debug "Pubkey ${fpr} already in keyring"
    fi

    # Step 2: ownertrust set to ultimate?
    # --with-colons output line format: pub:<trust>:<keylen>:...
    # trust values: -=unknown, n=never, m=marginal, f=full, u=ultimate
    local current_trust
    current_trust=$(gpg --list-keys --with-colons "${fpr}" 2>/dev/null | awk -F: '/^pub:/ {print $2; exit}')
    if [[ "${current_trust}" != "u" ]]; then
        __gpg_card_info "Setting ownertrust to ultimate for ${fpr} (current trust: '${current_trust:-unknown}')"
        if ! echo "${fpr}:6:" | gpg --import-ownertrust 2>>"${GPG_CARD_LOG:-/dev/null}"; then
            __gpg_card_warn "Failed to set ownertrust to ultimate. gpg --verify may show 'untrusted signature'."
        else
            __gpg_card_ok "Ownertrust set to ultimate"
        fi
    else
        __gpg_card_debug "Ownertrust already ultimate for ${fpr}"
    fi

    # Step 3: secret-key stub registered for the on-card key?
    if ! gpg --list-secret-keys "${fpr}" >/dev/null 2>&1; then
        __gpg_card_info "Secret-key stub for ${fpr} not present; running gpg --card-status to register on-card key..."
        gpg --card-status >/dev/null 2>&1 || true
        if ! gpg --list-secret-keys "${fpr}" >/dev/null 2>&1; then
            __gpg_card_die "Secret-key stub for ${fpr} still not present after gpg --card-status. The card may not contain a key matching this fingerprint."
            return 1
        fi
        __gpg_card_ok "Secret-key stub created for ${fpr} (on-card key registered)"
    else
        __gpg_card_debug "Secret-key stub for ${fpr} already present"
    fi

    __gpg_card_ok "Signing key ${fpr} ready (pubkey + ownertrust + secret-key stub all in place)"
    return 0
}

# ============================================================
# PUBLIC: gpg_card_logfile_path — return the scdaemon log path (Class A)
# ============================================================
# On Class B hosts (no pcscd, no scdaemon.conf managed by us) this returns
# the canonical Class-A path even though we didn't write the conf. Callers
# that want to log scdaemon output on Class B hosts should configure
# scdaemon.conf themselves out-of-band.
gpg_card_logfile_path() {
    if __gpg_card_pcscd_present; then
        echo "${HOME}/tmp/scdaemon.log"
    else
        echo "(no managed scdaemon log on this host; Class B uses system default scdaemon config)"
    fi
}

# ============================================================
# DIRECT-INVOCATION MODE
# ============================================================
# When this file is executed (not sourced), run setup + report state.
# Useful for standalone diagnostics: bash lib-gpg-card-setup.sh --debug
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    set -euo pipefail

    while [[ $# -gt 0 ]]; do
        case "${1}" in
            --debug)   GPG_CARD_DEBUG=1; shift ;;
            --dry-run) GPG_CARD_DRY_RUN=1; shift ;;
            --log)     GPG_CARD_LOG="${2}"; shift 2 ;;
            --help|-h)
                cat <<EOF
Usage: $(basename "$0") [--debug] [--dry-run] [--log <path>]

Runs gpg_card_setup_init() to set up the gpg smartcard stack appropriately
for the detected host class:
  Class A (pcscd present): pcscd activate + canonical scdaemon.conf + flush
  Class B (no pcscd): no system modifications; use built-in CCID

Idempotent; safe to re-run. --dry-run prints actions without executing.

When sourced from another script (source $(basename "$0")), use:
  gpg_card_setup_init            # idempotent setup
  gpg_card_verify_key <fpr>      # verify a signing key is in the keyring
  gpg_card_logfile_path          # echo the scdaemon log-file path (Class A)
EOF
                exit 0 ;;
            *)
                echo "Unknown argument: ${1}" >&2
                exit 2 ;;
        esac
    done

    gpg_card_setup_init
    __gpg_card_ok "Setup complete. scdaemon log: $(gpg_card_logfile_path)"
fi
