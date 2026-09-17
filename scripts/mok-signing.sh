# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# mok-signing.sh — the one place that answers "may this machine sign, and with
# what". Sourced, never executed: every shipped script that signs with the
# machine owner's key uses these functions rather than inventing its own answer.
#
# WHY IT EXISTS. The machine owner key signs this machine's boot images and its
# driver modules. Until 2026-09-17 it was stored without a passphrase and every
# signing step ran unattended, so a process running as root could sign a boot
# image the firmware trusts with nobody at the gate — the boot chain resisted an
# attacker without root and no one with it. The key is now encrypted at rest and
# every signing step asks the owner for the passphrase.
#
# THE RULES, in the order they matter:
#   1. A signing step that cannot get the passphrase REFUSES. It does not sign
#      with nothing, and it does not skip signing and report success. The
#      previous release's signed image stays where it is and stays bootable.
#   2. The passphrase is never printed, never written to a file, and never put
#      on a command line. It reaches openssl and the module signer through the
#      environment and the boot-image signer through its standard input,
#      because that is what each of those tools actually reads — measured on
#      the installed tools, not assumed.
#   3. Every candidate passphrase is checked against the key before it is used,
#      including one handed over by the installer. A value that does not open
#      the key is refused rather than passed on to a tool that would fail
#      further down with a page of library errors.
#   4. An absent key, a plain key and a protected key are three different
#      answers and are never collapsed into two.

# Where the key lives. Overridable so the tests can run against a scratch copy;
# on a machine these are the real paths.
MOK_DIR="${MOK_DIR:-/var/lib/intergen/mok}"
MOK_KEY="${MOK_KEY:-${MOK_DIR}/mok.key}"
MOK_CERT="${MOK_CERT:-${MOK_DIR}/mok.crt}"

# How the owner is asked. systemd-ask-password reaches a person at the console
# and a person in a desktop session through the same call, which is why it is
# the mechanism rather than a bare `read`: a kernel upgrade can be started from
# either place.
MOK_ASK_PROGRAM="${MOK_ASK_PROGRAM:-systemd-ask-password}"

# Three attempts, then a refusal. The number is stated here, in the messages
# below, and in the documents; a preflight test asserts the count so the three
# cannot drift apart.
MOK_ASK_ATTEMPTS="${MOK_ASK_ATTEMPTS:-3}"

# How long the prompt waits for a person before giving up. A kernel upgrade run
# from a script with nobody watching must not hang for ever; it must refuse, so
# the operator sees a failed transaction rather than a stuck one.
MOK_ASK_TIMEOUT="${MOK_ASK_TIMEOUT:-120}"

# The variable the installer uses to hand the passphrase to a hook during an
# install, when the person is in front of the installer rather than a console
# prompt. Named for what it holds so the install trace's redaction scrubs it.
MOK_PASS_ENV="IGOS_MOK_PASSPHRASE"

# Set by mok_resolve_passphrase on success. Never printed by anything here.
MOK_PASSPHRASE=""

mok_log() {
    echo "[mok-signing] $*" >&2
}

# 0 = the key is encrypted, 1 = it is a plain key, 2 = there is no key.
#
# The test is the read itself: openssl is asked to open the key with an EMPTY
# passphrase, and a key that opens is a key with no passphrase on it. Reading
# the PEM header instead would be guessing at the file's shape rather than
# asking the tool that has to use it.
mok_key_is_encrypted() {
    local key="${1:-$MOK_KEY}"
    [ -f "$key" ] || return 2
    if openssl rsa -in "$key" -check -noout -passin pass: >/dev/null 2>&1; then
        return 1
    fi
    return 0
}

# 0 when this passphrase opens this key. Nothing is printed either way.
mok_passphrase_opens_key() {
    local key="$1" candidate="$2"
    [ -n "$candidate" ] || return 1
    MOK_PASS_CHECK="$candidate" openssl rsa -in "$key" -check -noout \
        -passin env:MOK_PASS_CHECK >/dev/null 2>&1
}

# Whether the firmware is enforcing Secure Boot right now.
#
# 0 = on, 1 = off, 2 = cannot tell. The third answer is a real one: a machine
# whose firmware state cannot be read is not the same as a machine with Secure
# Boot off, and a caller that treats "cannot tell" as "off" would skip signing
# on exactly the machine that needs it most.
mok_secure_boot_enabled() {
    local var
    for var in /sys/firmware/efi/efivars/SecureBoot-*; do
        [ -f "$var" ] || continue
        # The variable is four bytes of attributes followed by one byte of
        # state. od is used rather than a helper program so this works on a
        # machine where mokutil is not installed.
        local last
        last=$(od -An -t u1 "$var" 2>/dev/null | tr -s ' ' | tr ' ' '\n' \
               | grep -v '^$' | tail -1)
        case "$last" in
            1) return 0 ;;
            0) return 1 ;;
        esac
    done
    return 2
}

# Resolve the passphrase for one signing step. Sets MOK_PASSPHRASE on success.
#
#   $1 — the key it must open
#   $2 — what the machine is about to sign, said in plain words; it goes in the
#        prompt so the owner knows what they are authorising
#
# Returns non-zero when there is no passphrase to be had, which every caller
# treats as a refusal to sign.
mok_resolve_passphrase() {
    local key="${1:-$MOK_KEY}"
    local purpose="${2:-signing}"
    MOK_PASSPHRASE=""

    if [ ! -f "$key" ]; then
        mok_log "no machine owner key at $key — nothing can be signed with it"
        return 2
    fi

    # An install supplies it. Checked against the key like any other candidate:
    # a wrong value here is a bug in the installer, and letting it through would
    # surface as an unexplained signing failure three layers down.
    local supplied="${!MOK_PASS_ENV:-}"
    if [ -n "$supplied" ]; then
        if mok_passphrase_opens_key "$key" "$supplied"; then
            MOK_PASSPHRASE="$supplied"
            return 0
        fi
        mok_log "the passphrase supplied for this operation does not open $key"
        return 1
    fi

    # Nobody supplied one, so the owner is asked. A key with no passphrase on it
    # is not asked about — that machine is handled by the migration path, which
    # the caller runs before reaching here.
    if ! mok_key_is_encrypted "$key"; then
        mok_log "$key has no passphrase on it; it must be protected before it signs again"
        return 1
    fi

    if ! command -v "$MOK_ASK_PROGRAM" >/dev/null 2>&1; then
        mok_log "cannot ask for the signing passphrase: $MOK_ASK_PROGRAM is not on this system."
        mok_log "  Nothing will be signed. This is a refusal, not a skipped step."
        return 1
    fi

    local attempt answer
    for (( attempt = 1; attempt <= MOK_ASK_ATTEMPTS; attempt++ )); do
        answer=$("$MOK_ASK_PROGRAM" \
                    --timeout="$MOK_ASK_TIMEOUT" \
                    --icon=changes-prevent \
                    --id="intergenos-mok:${purpose}" \
                    "Machine owner signing key passphrase (${purpose}), attempt ${attempt} of ${MOK_ASK_ATTEMPTS}:" \
                 2>/dev/null)
        if [ -z "$answer" ]; then
            mok_log "no passphrase was given (attempt ${attempt} of ${MOK_ASK_ATTEMPTS})"
            break
        fi
        if mok_passphrase_opens_key "$key" "$answer"; then
            MOK_PASSPHRASE="$answer"
            answer=""
            return 0
        fi
        mok_log "that passphrase does not open the machine owner key (attempt ${attempt} of ${MOK_ASK_ATTEMPTS})"
    done
    answer=""

    mok_log "no usable passphrase after ${MOK_ASK_ATTEMPTS} attempts — refusing to sign ${purpose}."
    return 1
}

# Give a key that has no passphrase one, in place.
#
# Every machine installed before 2026-09-17 holds a key with no passphrase on
# it. This is how those machines move: at the next kernel or driver update the
# owner is asked to set one, the key is rewritten encrypted, the previous bytes
# are destroyed, and the result is read back before anything signs with it.
#
# What it does NOT do, in each case for a reason:
#   - it never invents a passphrase. A key protected by something the owner was
#     never told is not protected from the owner's point of view.
#   - it never leaves a second copy of the unprotected key anywhere. The
#     rewrite goes to a new file in the same directory, the original is
#     destroyed in place before the new one takes its name, and the temporary
#     file is removed on every exit path.
#   - a refusal changes nothing at all. The machine still boots on what it has;
#     the owner can set the passphrase at the next update.
mok_migrate_plain_key() {
    local key="${1:-$MOK_KEY}"

    if [ ! -f "$key" ]; then
        mok_log "no machine owner key at $key — nothing to protect"
        return 2
    fi

    # Already protected: nothing to do, and nobody is asked. An update on a
    # machine that has already migrated must not prompt twice.
    if mok_key_is_encrypted "$key"; then
        return 0
    fi

    mok_log "the machine owner signing key at $key has NO passphrase on it."
    mok_log "  This key signs this machine's boot images and driver modules, so"
    mok_log "  any process running as root can sign one today. Set a passphrase"
    mok_log "  now and it will be asked for each time something is signed."

    if ! command -v "$MOK_ASK_PROGRAM" >/dev/null 2>&1; then
        mok_log "cannot ask for a new passphrase: $MOK_ASK_PROGRAM is not on this system. The key is unchanged."
        return 1
    fi

    local first second
    first=$("$MOK_ASK_PROGRAM" --timeout="$MOK_ASK_TIMEOUT" \
                --icon=changes-prevent --id=intergenos-mok:set \
                "Set a passphrase for this machine's signing key:" 2>/dev/null)
    if [ -z "$first" ]; then
        mok_log "no passphrase was set; the key is unchanged and nothing will be signed."
        return 1
    fi
    second=$("$MOK_ASK_PROGRAM" --timeout="$MOK_ASK_TIMEOUT" \
                 --icon=changes-prevent --id=intergenos-mok:confirm \
                 "Type it again to confirm:" 2>/dev/null)
    if [ "$first" != "$second" ]; then
        first=""; second=""
        mok_log "the two entries do not match; the key is unchanged and nothing will be signed."
        return 1
    fi
    second=""

    if [ "${#first}" -lt 8 ]; then
        first=""
        mok_log "the passphrase must be at least 8 characters; the key is unchanged."
        return 1
    fi

    local tmp="${key}.migrating.$$"
    rm -f "$tmp"
    if ! MOK_PASS_NEW="$first" openssl rsa -in "$key" -aes256 \
            -passout env:MOK_PASS_NEW -out "$tmp" >/dev/null 2>&1; then
        rm -f "$tmp"
        first=""
        mok_log "the key could not be rewritten; it is unchanged and nothing will be signed."
        return 1
    fi
    chmod 600 "$tmp" 2>/dev/null || true

    # Read the NEW file back before the old one is destroyed. Both directions:
    # it must not open with an empty passphrase, and it must open with the one
    # the owner just set. The first assertion is the one that matters — a
    # rewrite that silently produced another plain key would pass the second.
    if openssl rsa -in "$tmp" -check -noout -passin pass: >/dev/null 2>&1; then
        rm -f "$tmp"
        first=""
        mok_log "the rewritten key still opens with no passphrase; the original is unchanged."
        return 1
    fi
    if ! mok_passphrase_opens_key "$tmp" "$first"; then
        rm -f "$tmp"
        first=""
        mok_log "the rewritten key does not open with the passphrase just set; the original is unchanged."
        return 1
    fi

    # Destroy the unprotected bytes before the new file takes the name. shred
    # is best effort by nature — on a copy-on-write or flash-translated device
    # it cannot promise the old blocks are gone — so the sequence does not
    # depend on it: the file is overwritten with the new key either way.
    if command -v shred >/dev/null 2>&1; then
        shred -n 1 -z "$key" >/dev/null 2>&1 || true
    fi
    if ! mv -f "$tmp" "$key"; then
        rm -f "$tmp"
        first=""
        mok_log "the protected key could not replace the original at $key."
        return 1
    fi
    chmod 600 "$key" 2>/dev/null || true

    MOK_PASSPHRASE="$first"
    first=""

    # The first-login page runs as the person and cannot read the key, so the
    # fact that it is now protected is recorded where the person can read it.
    # Written only here, after the read-back above, so the record cannot claim
    # a protection that did not happen.
    mok_record_protection yes

    mok_log "the machine owner signing key is now protected by your passphrase."
    return 0
}

# Where the protection state is recorded for the first-login page. Same path the
# installer writes; overridable for the tests.
MOK_PROTECTION_RECORD="${MOK_PROTECTION_RECORD:-/etc/intergenos/mok-key-protection}"

mok_record_protection() {
    local state="$1"
    local dir
    dir=$(dirname "$MOK_PROTECTION_RECORD")
    mkdir -p "$dir" 2>/dev/null || true
    {
        echo "# Whether this machine's signing key is protected by a passphrase."
        echo "# That key signs this machine's boot images and driver modules. This"
        echo "# file is a record, written when the key was made or when it was"
        echo "# protected; the key itself is readable only by root."
        echo "protected=${state}"
        echo "recorded=$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
    } > "$MOK_PROTECTION_RECORD" 2>/dev/null || {
        mok_log "could not record the key's protection state at $MOK_PROTECTION_RECORD"
        return 1
    }
    chmod 644 "$MOK_PROTECTION_RECORD" 2>/dev/null || true
    return 0
}


# Build and sign a unified kernel image with the resolved passphrase.
#
# The builder passes its own standard input through to the boot-image signer,
# which reads the passphrase as one line — measured on the installed tools. The
# output path is the caller's; the caller is responsible for not putting an
# unsigned image where the boot chain would pick it up, and this function never
# produces one, because the signer writes nothing when it cannot open the key.
mok_build_signed_uki() {
    printf '%s\n' "$MOK_PASSPHRASE" | ukify "$@"
}

# Sign a kernel module with the resolved passphrase.
#
# The module signer reads one named environment variable and does not read
# standard input; giving it the passphrase any other way silently falls back to
# prompting on a terminal that is not there.
mok_sign_kernel_module() {
    local sign_file="$1" hash_algo="$2" key="$3" cert="$4" ko="$5"
    KBUILD_SIGN_PIN="$MOK_PASSPHRASE" "$sign_file" "$hash_algo" "$key" "$cert" "$ko"
}
