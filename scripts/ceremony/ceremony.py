#!/usr/bin/env python3
"""
ceremony.py — InterGenOS Signing Ceremony, Python+pexpect automation.

Drives the air-gapped Tails session that produces the InterGenOS distro
release-signing keys (PGP master + four sign subkeys + EFI X.509 PIV slot 9c
vendor cert).

Uses pexpect for reliable interactive gpg --card-edit driving; SCD APDU via
gpg-connect-agent for deterministic card primitives; subprocess for the rest.
Validates state after every card mutation via gpg --card-status. Aborts loud
on any unexpected state — no silent failures.

Single command on Tails:
    python3 /media/amnesia/OFFLINEDEBS/scripts/ceremony.py

For procedure context, trust-chain framing, and reviewer-facing summary, see
docs/ceremony/signing-key-ceremony-procedure.md in the InterGenOS repo. For
the post-mortem covering 24+ ratified bug fixes that informed this script's
defensive guards, see docs/research/ceremony/lessons-learned-2026-05-05.md.

Hardcoded canonical values:
    Master UID:    InterGenOS Project Signing Key (primary) <intergenos-primary@intergenstudios.com>
    Master expiry: 0 (does not expire)
    PIV Cert CN:   InterGenOS Secure Boot CA
    PIV Validity:  730 days (~2 years)
"""

import argparse
import os
import sys
import re
import shlex
import subprocess
import getpass
import time
import shutil
import hashlib
from datetime import datetime, timezone
from pathlib import Path

try:
    import pexpect
except ImportError:
    print("ERROR: python3-pexpect not installed. Run Stage 2 first or install offline-debs.")
    sys.exit(2)


# ============================================================================
# Constants
# ============================================================================
NAME = "InterGenOS Project Signing Key"
EMAIL = "intergenos-primary@intergenstudios.com"
COMMENT = "primary"
EXPIRY = "0"
CERT_CN = "InterGenOS Secure Boot CA"
CERT_DAYS = "730"

CEREMONY = Path.home() / "ceremony"
DRIVE2 = Path("/media/amnesia/OFFLINEDEBS")
DRIVE3 = Path("/media/amnesia/CEREMONY")
PKCS11_MOD = "/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so"

# Comprehensive trace log — every subprocess call, every pexpect exchange,
# every card state transition. Append-only. Cleared at start of each run.
TRACE_LOG_PATH = None  # set in main() based on CEREMONY


def trace(msg, also_print=False):
    """Append a single line to the trace log. Optionally also print to stdout."""
    if TRACE_LOG_PATH is None:
        return
    try:
        with open(TRACE_LOG_PATH, "a") as f:
            f.write(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S.%f')[:-3]}] {msg}\n")
    except Exception:
        pass
    if also_print:
        print(msg, flush=True)


def trace_block(label, body):
    """Append a multi-line labeled block to the trace log."""
    if TRACE_LOG_PATH is None:
        return
    try:
        with open(TRACE_LOG_PATH, "a") as f:
            f.write(f"--- {label} ---\n")
            for line in (body or "").splitlines():
                f.write(f"  {line}\n")
            f.write(f"--- /{label} ---\n")
    except Exception:
        pass


def _scdaemon_log_tail(n=200):
    """Return the last N lines of /tmp/scdaemon-ceremony.log if it exists."""
    p = Path("/tmp/scdaemon-ceremony.log")
    if not p.exists():
        return "(scdaemon log absent)"
    lines = p.read_text(errors="replace").splitlines()
    return "\n".join(lines[-n:])


def _scdaemon_log_size():
    """Return current size of scdaemon log (for delta-tracking)."""
    p = Path("/tmp/scdaemon-ceremony.log")
    return p.stat().st_size if p.exists() else 0


def _scdaemon_log_delta(start_size):
    """Return scdaemon log content added since start_size."""
    p = Path("/tmp/scdaemon-ceremony.log")
    if not p.exists():
        return "(scdaemon log absent)"
    with open(p, "rb") as f:
        f.seek(start_size)
        return f.read().decode(errors="replace")

# Optional pre-fill values file. If present, ceremony.py reads PINs/passphrases
# from it instead of prompting interactively. Expected format:
#   key=value      (one per line)
#   # comment      (lines starting with # ignored)
# Required keys: master_pass, luks_pass, nk{1,2,3,4}_{upin,apin,ppin,puk}.
# DELETE this file after ceremony completes (it contains every secret).
VALUES_PATH = DRIVE2 / "scripts" / "values.txt"
_values_cache = None


def load_values():
    """Load values.txt into a dict if present, else return empty dict."""
    global _values_cache
    if _values_cache is not None:
        return _values_cache
    _values_cache = {}
    if not VALUES_PATH.is_file():
        return _values_cache
    for line in VALUES_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        # Strip inline comments after value (everything after ' #')
        if " #" in v:
            v = v.split(" #")[0]
        _values_cache[k.strip()] = v.strip()
    return _values_cache


def get_value(key):
    """Return value from values.txt, or None if not present (or empty string)."""
    v = load_values().get(key)
    return v if v else None


def secret_or_value(values_key, prompt_msg, confirm=True, pattern=None, min_len=None):
    """If values.txt has values_key, validate and return it (no prompt).
    Otherwise prompt interactively via ask_secret. Validation (pattern, min_len)
    is enforced for both paths.
    """
    v = get_value(values_key)
    if v is not None:
        # Validate prefilled value
        if pattern and not re.match(pattern, v):
            fatal(f"values.txt key {values_key!r} doesn't match required pattern {pattern}")
        if min_len and len(v) < min_len:
            fatal(f"values.txt key {values_key!r} is shorter than {min_len} chars")
        info(f"using values.txt value for {values_key} (length: {len(v)})")
        return v
    return ask_secret(prompt_msg, confirm=confirm, pattern=pattern, min_len=min_len)


# ============================================================================
# UI helpers — printed to stdout, also tee'd to ceremony.log via main()
# ============================================================================
def banner(s):
    print()
    print("=" * 64)
    print(f"  {s}")
    print("=" * 64)
    sys.stdout.flush()


def step(s):
    print()
    print(f">>> {s}")
    sys.stdout.flush()


def info(s):
    print(f"    {s}")
    sys.stdout.flush()


def ok(s):
    print(f"    OK  {s}")
    sys.stdout.flush()


def fail(s):
    print(f"    !!  {s}")
    sys.stdout.flush()


def fatal(s):
    print()
    print("=" * 64)
    print(f"  ABORT: {s}")
    print("=" * 64)
    sys.stdout.flush()
    sys.exit(2)


def ask_enter(s):
    input(f">>> {s} (Enter to continue, Ctrl-C to abort): ")


def ask_text(s):
    return input(f">>> {s}: ")


def ask_secret(prompt_msg, confirm=True, pattern=None, min_len=None):
    """Read a secret from owner with optional confirm + regex validation."""
    while True:
        v1 = getpass.getpass(f">>> {prompt_msg}: ")
        info(f"(length: {len(v1)})")
        if pattern and not re.match(pattern, v1):
            fail(f"doesn't match pattern {pattern}. Try again.")
            continue
        if min_len and len(v1) < min_len:
            fail(f"shorter than {min_len} chars. Try again.")
            continue
        if confirm:
            v2 = getpass.getpass(">>> Confirm: ")
            if v1 != v2:
                fail("mismatch. Try again.")
                continue
        return v1


def record_paper(label):
    print()
    print("*** RECORD ON PAPER NOW ***")
    print(label)
    input("*** Press Enter ONLY after recording on paper: ")
    sys.stdout.flush()


# ============================================================================
# Subprocess wrappers
# ============================================================================
def run(cmd, check=True, **kw):
    """Run a subprocess. Returns CompletedProcess. Raises on non-zero unless check=False."""
    return subprocess.run(cmd, check=check, **kw)


def cap(cmd, check=False, **kw):
    """Run + capture stdout/stderr as text."""
    return subprocess.run(cmd, capture_output=True, text=True, check=check, **kw)


# ============================================================================
# Bootloader detection — Nitrokey 3 USB IDs
#   42b1, 42b2 = normal CCID smartcard mode
#   42dd       = bootloader mode (smartcard interface absent)
# ============================================================================
def assert_not_bootloader():
    r = cap(["lsusb"])
    if "20a0:42dd" in r.stdout:
        fatal(
            "Nitrokey is in BOOTLOADER mode (USB ID 20a0:42dd).\n"
            "  Recovery:\n"
            "    1. Unplug the Nitrokey\n"
            "    2. Wait 30 seconds (let bootloader auto-timeout)\n"
            "    3. Plug back in\n"
            "    4. Verify: lsusb | grep 20a0 -> 42b1/42b2 (NOT 42dd)\n"
            "    5. Re-run ceremony.py"
        )


def assert_nk_present():
    r = cap(["lsusb"])
    if "20a0:" not in r.stdout:
        fatal("No Nitrokey enumerated on USB (no 20a0:* device found).")


# ============================================================================
# pcscd / scdaemon
# ============================================================================
def revive_pcscd():
    """Kill scdaemon, restart pcscd. Call after every card swap."""
    cap(["gpgconf", "--kill", "scdaemon"])
    time.sleep(0.5)
    run(["sudo", "-n", "systemctl", "restart", "pcscd"])
    time.sleep(2)


SCDAEMON_CONF = (
    "disable-ccid\n"
    "pcsc-shared\n"
    "debug-level guru\n"
    "log-file /tmp/scdaemon-ceremony.log\n"
)
GPG_AGENT_CONF = (
    "allow-loopback-pinentry\n"
    "debug-level guru\n"
    "log-file /tmp/gpg-agent-ceremony.log\n"
)


def write_gpg_configs_to(gnupghome):
    """Write scdaemon.conf + gpg-agent.conf into a specific GNUPGHOME directory.
    Called both for ~/.gnupg (during stage_install_debs) and for the ceremony's
    own GNUPGHOME (CEREMONY/gnupg-master) once it exists. WITHOUT these configs
    in the ceremony's gnupghome, scdaemon defaults to CCID-direct mode which
    conflicts with pcscd holding the USB readers — every card op reports
    'No such device' indefinitely. This is THE root cause of the keytocard /
    key-attr cascade of failures.
    """
    p = Path(gnupghome)
    p.mkdir(mode=0o700, exist_ok=True)
    (p / "scdaemon.conf").write_text(SCDAEMON_CONF)
    (p / "scdaemon.conf").chmod(0o600)
    (p / "gpg-agent.conf").write_text(GPG_AGENT_CONF)
    (p / "gpg-agent.conf").chmod(0o600)


def setup_scdaemon_conf():
    """Initial install of scdaemon.conf + gpg-agent.conf in ~/.gnupg.
    The ceremony's own GNUPGHOME (CEREMONY/gnupg-master) gets its copy via
    write_gpg_configs_to() once it's created in stage_master_keypair or detected
    in main()'s resume path.
    """
    write_gpg_configs_to(Path.home() / ".gnupg")
    # Truncate logs at ceremony start so the trace is scoped to this run
    Path("/tmp/scdaemon-ceremony.log").write_text("")
    Path("/tmp/gpg-agent-ceremony.log").write_text("")


# ============================================================================
# Card status — parse gpg --card-status output
# ============================================================================
def gpg_card_status():
    """Return dict of parsed fields plus _raw stdout+stderr.
    gpg --card-status uses dot-padding for short field names
    ('Reader ...........: foo') but no dots for long field names
    ('PIN retry counter : 3 0 3'). Split on first ':' and strip
    trailing dots/spaces from the field name to handle both.
    """
    r = cap(["gpg", "--card-status"])
    out = r.stdout + r.stderr
    fields = {"_raw": out, "_returncode": r.returncode}
    for line in out.splitlines():
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        name = name.rstrip(" .").strip()
        value = value.strip()
        if name and name[0].isalpha():
            fields[name] = value
    return fields


def assert_card_factory_openpgp():
    """Verify card OpenPGP applet is factory state. Raises RuntimeError if not."""
    s = gpg_card_status()
    if s.get("_returncode") != 0 or "Application type" not in s:
        raise RuntimeError(f"gpg --card-status failed:\n{s.get('_raw', '')}")
    if s.get("Application type") != "OpenPGP":
        raise RuntimeError(f"Card not in OpenPGP mode: {s.get('Application type')}")
    retry = s.get("PIN retry counter", "")
    if not retry.startswith("3 ") or not retry.endswith(" 3"):
        raise RuntimeError(f"PIN retry counter not factory (expected '3 X 3', got '{retry}')")
    if "[none]" not in s.get("Signature key", ""):
        raise RuntimeError(f"Signature key already set: {s.get('Signature key')}")
    if "[none]" not in s.get("Encryption key", ""):
        raise RuntimeError(f"Encryption key already set: {s.get('Encryption key')}")


def get_card_serial():
    s = gpg_card_status()
    return s.get("Serial number", "?")


# ============================================================================
# pexpect-driven gpg primitives
#
# gpg --command-fd 0 reads commands from stdin without emitting interactive
# prompts. So we don't expect prompts — we blast all commands in order and
# let gpg consume them. PTY (pexpect.spawn) is required because gpg --card-edit
# refuses to operate without a TTY. We validate the result afterward via
# gpg --card-status / gpg --list-keys parsing — the source of truth is the
# card state, not gpg's stdout.
# ============================================================================
def _blast(cmd, lines, timeout=60, env=None, label="blast"):
    """Spawn cmd in a PTY, send the full command stream as one chunk, wait for EOF.
    Mimics the proven `printf '...' | gpg --command-fd 0` pattern from the bash version.
    Captures the full pty output AND the scdaemon log delta for the duration of the call.
    """
    sd_start = _scdaemon_log_size()
    trace(f"BLAST[{label}] spawn: {cmd}")
    # Don't trace the actual lines — they may contain PINs/passphrases. Trace count + lengths.
    trace(f"BLAST[{label}] sending {len(lines)} input lines, total {sum(len(l) for l in lines)} chars")
    p = pexpect.spawn(cmd, encoding="utf-8", timeout=timeout, env=env)
    captured = []
    try:
        p.send("\n".join(lines) + "\n")
        try:
            p.expect(pexpect.EOF, timeout=timeout)
        except pexpect.TIMEOUT:
            trace(f"BLAST[{label}] EOF TIMEOUT after {timeout}s")
        captured.append(p.before or "")
    finally:
        p.close(force=True)
        trace(f"BLAST[{label}] exit status: {p.exitstatus}")
    output = "".join(captured)
    trace_block(f"BLAST[{label}] PTY OUTPUT", output)
    trace_block(f"BLAST[{label}] SCDAEMON LOG DELTA", _scdaemon_log_delta(sd_start))
    return output


def gpg_card_factory_reset():
    _blast(
        "gpg --command-fd 0 --pinentry-mode=loopback --card-edit",
        ["admin", "factory-reset", "y", "yes", "quit"],
        timeout=60,
    )


def gpg_card_change_user_pin(old_pin, new_pin):
    _blast(
        "gpg --command-fd 0 --pinentry-mode=loopback --card-edit",
        ["admin", "passwd", "1", old_pin, new_pin, new_pin, "q", "quit"],
        timeout=60,
    )


def gpg_card_change_admin_pin(old_pin, new_pin):
    _blast(
        "gpg --command-fd 0 --pinentry-mode=loopback --card-edit",
        ["admin", "passwd", "3", old_pin, new_pin, new_pin, "q", "quit"],
        timeout=60,
    )


def gpg_card_set_url(url, admin_pin):
    _blast(
        "gpg --command-fd 0 --pinentry-mode=loopback --card-edit",
        ["admin", "url", url, admin_pin, "quit"],
        timeout=30,
    )


def gpg_card_uif_on(slot, admin_pin):
    _blast(
        "gpg --command-fd 0 --pinentry-mode=loopback --card-edit",
        ["admin", f"uif {slot} on", admin_pin, "quit"],
        timeout=30,
    )


def gpg_card_unblock_user_pin(admin_pin, new_user_pin, env=None):
    """Reset the OpenPGP User PIN to a known value using the Admin PIN.

    Uses gpg --card-edit > admin > passwd > 2 (unblock PIN). Unlike option 1
    (change PIN), option 2 doesn't require the OLD User PIN — Admin PIN alone
    authorizes setting a new User PIN to any value. Idempotent: the new PIN is
    set regardless of what the current value was.

    Used in Stage 7 after keytocard succeeds to defensively reset the User PIN
    to the values.txt value, in case Phase 0 §0.3 silently failed to rotate it.
    Without this, test-sign fails with 'Bad PIN' because the values.txt PIN
    doesn't match the card's actual PIN.

    Sequence: admin > passwd > 2 (unblock) > admin_pin > new_user_pin >
              new_user_pin (confirm) > q (exit passwd menu) > quit.
    """
    out = _blast(
        "gpg --command-fd 0 --pinentry-mode=loopback --card-edit",
        ["admin", "passwd", "2", admin_pin, new_user_pin, new_user_pin, "q", "quit"],
        timeout=30, env=env, label="user-pin-unblock",
    )
    return out


def gpg_card_set_key_attr_rsa4096(admin_pin, env=None):
    """Set the OpenPGP card's algorithm attributes to RSA-4096 for all 3 slots.

    Default Nitrokey 3 OpenPGP attribute is RSA-2048. Stage 7 imports a 4096-bit
    sign-subkey via keytocard. The card refuses imports whose algorithm doesn't
    match the slot's configured attribute — symptom is 'KEYTOCARD failed:
    Invalid time' (a misleading error code mapped from the card's wrong-params SW).

    The key-attr menu walks all 3 slots; we set all to RSA-4096 even though
    only the signature slot will hold a key — keeps state uniform. Admin PIN is
    requested once per slot (3 times total). Idempotent: safe to call repeatedly.
    """
    out = _blast(
        "gpg --command-fd 0 --pinentry-mode=loopback --card-edit",
        [
            "admin",
            "key-attr",
            "1", "4096", admin_pin,    # Sig slot:  RSA-4096
            "1", "4096", admin_pin,    # Enc slot:  RSA-4096
            "1", "4096", admin_pin,    # Auth slot: RSA-4096
            "quit",
        ],
        timeout=60, env=env, label="key-attr-rsa4096",
    )
    return out


def gpg_addkey_sign_subkey(master_fp, master_pass, env, timeout=300):
    """addkey for a sign-only RSA-4096 subkey valid 2y."""
    cmd = (
        f"gpg --command-fd 0 --pinentry-mode=loopback "
        f"--passphrase {shlex.quote(master_pass)} --edit-key {master_fp}"
    )
    return _blast(
        cmd,
        ["addkey", "4", "4096", "2y", "y", "y", "save"],
        timeout=timeout, env=env, label="addkey",
    )


def find_disk_resident_sign_subkeys(master_fp, env):
    """Return 1-based indices of sign-capable subkeys whose full secret is on
    the master keyring (NOT keytocard'd to a card).

    Uses the human-readable `gpg --list-secret-keys` output (NOT colon format,
    because modern gpg's --with-colons doesn't reliably mark on-card stubs).
    For each ssb entry, checks if any continuation line contains 'card-no:'.
    If yes → on-card stub, NOT an orphan. If no → disk-resident, IS an orphan.

    These orphans accumulate when a prior run's addkey succeeded but the
    follow-up keytocard failed (or the slot was already occupied and gpg
    aborted). They have no functional purpose — no card holds the matching
    secret material. Safe to delete.
    """
    r = cap(["gpg", "--list-secret-keys", "--keyid-format", "LONG", master_fp], env=env)
    orphan_indices = []
    sub_idx = 0
    current_is_sign = False
    current_has_card_no = False

    def finalize_current():
        if sub_idx > 0 and current_is_sign and not current_has_card_no:
            orphan_indices.append(sub_idx)

    for ln in r.stdout.splitlines():
        stripped = ln.lstrip()
        # ssb lines start at column 0 (gpg formats them that way)
        if ln.startswith("ssb"):
            # Finalize previous sub before moving on
            finalize_current()
            sub_idx += 1
            current_is_sign = "[S]" in ln
            current_has_card_no = False
        elif "card-no:" in stripped:
            # Continuation line of the current sub — it's on a card
            current_has_card_no = True
    finalize_current()
    return orphan_indices


def cleanup_disk_resident_sign_subkeys(master_fp, master_pass, env):
    """Delete sign-capable subkeys with secret material still on the master
    keyring (per find_disk_resident_sign_subkeys()). Runs at the start of each
    stage_keytocard_one() so orphans from prior partial runs get cleaned up
    before this card's addkey adds a fresh sub.

    Safe — only deletes subs with NO card-no annotation. Stubs for already-
    keytocard'd cards are kept untouched.
    """
    orphans = find_disk_resident_sign_subkeys(master_fp, env)
    if not orphans:
        return
    info(f"Cleaning up {len(orphans)} disk-resident sign subkey(s) at indices {orphans} "
         f"(no card-no annotation — these are orphans from prior failed keytocard runs)")
    for idx in sorted(orphans, reverse=True):
        cmd = (
            f"gpg --command-fd 0 --pinentry-mode=loopback "
            f"--passphrase {shlex.quote(master_pass)} --edit-key {master_fp}"
        )
        _blast(cmd, [f"key {idx}", "delkey", "y", "save"],
               timeout=30, env=env, label=f"delkey-orphan-{idx}")
    remaining = find_disk_resident_sign_subkeys(master_fp, env)
    if remaining:
        fatal(f"Orphan cleanup incomplete: {len(remaining)} disk-resident sign sub(s) "
              f"still on master keyring at indices {remaining}")
    ok(f"Disk-resident sign subkey cleanup complete (deleted {len(orphans)} orphan(s))")


def list_unmoved_sign_subkeys(master_fp, env):
    """Return 1-based indices of sign-capable subkeys whose secret material is
    still on the master keyring (NOT keytocard'd to a card).

    gpg --list-secret-keys --with-colons format:
      ssb:u:KEYSIZE:ALGO:KEYID:CREATE:EXPIRE::::::CAPS::...   (full secret on disk)
      ssb#:u:KEYSIZE:ALGO:KEYID:CREATE:EXPIRE::::::CAPS::...  (stub — secret on card)

    The '#' immediately after 'ssb' indicates the secret has been moved off-disk
    (keytocard). Subkey indices are 1-based: 'ssb' lines come after the 'sec' line,
    indexed 1, 2, 3 in the order gpg --edit-key 'key N' uses.

    Capability is in field 12 (zero-indexed 11). 's' = sign-capable.
    Encryption-only subs (capability 'e') are excluded.
    """
    r = cap(["gpg", "--list-secret-keys", "--with-colons", master_fp], env=env)
    indices = []
    sub_idx = 0
    for ln in r.stdout.splitlines():
        if not ln.startswith("ssb"):
            continue
        sub_idx += 1
        if not ln.startswith("ssb:"):
            continue  # ssb#: = stub on card, not orphan
        fields = ln.split(":")
        if len(fields) > 11 and "s" in fields[11].lower():
            indices.append(sub_idx)
    return indices


def cleanup_orphan_sign_subkeys(master_fp, master_pass, env):
    """Remove any orphan (unmoved) sign subkeys from the master keyring — left
    behind by prior partial Stage-7 runs where addkey succeeded but keytocard
    failed. Uses gpg --edit-key > key N > delkey > y > save, iterating in
    descending index order so removals don't shift remaining indices.

    delkey removes BOTH public and secret material for the subkey from the local
    keyring. Since these orphans were never exported (Stage 8 hadn't run when
    they were created), this fully erases them — no revocation cert needed.
    """
    orphans = list_unmoved_sign_subkeys(master_fp, env)
    if not orphans:
        return
    info(f"Cleaning up {len(orphans)} orphan sign subkey(s) at indices {orphans} "
         f"(from prior partial Stage-7 runs)")
    last_out = ""
    for idx in sorted(orphans, reverse=True):
        cmd = (
            f"gpg --command-fd 0 --pinentry-mode=loopback "
            f"--passphrase {shlex.quote(master_pass)} --edit-key {master_fp}"
        )
        last_out = _blast(cmd, [f"key {idx}", "delkey", "y", "save"], timeout=30, env=env)

    remaining = list_unmoved_sign_subkeys(master_fp, env)
    if remaining:
        info("Last delkey output:")
        for line in (last_out or "").splitlines():
            info(f"  | {line}")
        fatal(f"Orphan cleanup failed — {len(remaining)} orphan(s) still on master keyring "
              f"at indices {remaining}")
    ok(f"Orphan cleanup complete (deleted {len(orphans)} unmoved sign subkey(s))")


def cleanup_dead_card_refs(card_num, master_fp, master_pass, env):
    """Remove master-keyring sign sub stubs that reference this card's serial
    but have a keyid different from the card's CURRENT on-card sig keyid.

    Dead references are left behind when a prior run did keytocard with
    replace-y on a slot that already had a key — the on-card key got
    overwritten with the new keypair, but the original stub on the master
    keyring still annotates this card's serial. The original stub's keyid
    no longer corresponds to anything on the card. If left in place,
    Stage 8 pubkey export publishes the dead ref alongside the live stub.

    Idempotent: no-op if there are no dead refs (typical for a fresh
    Phase-0'd card with no prior stubs, or a card whose only stub matches
    the on-card key).
    """
    # DEFENSIVE DOUBLE-READ: gpg --card-status can return transient partial
    # data right after revive_pcscd (scdaemon-warmup race we've seen with the
    # serial '?' issue). Acting on a partial read here would delete legitimate
    # stubs. Read twice with a 3s gap; only proceed if both reads agree on
    # serial AND signature key. On disagreement, skip cleanup defensively —
    # the next per-NK iteration will re-attempt.
    s1 = gpg_card_status()
    time.sleep(3)
    s2 = gpg_card_status()
    serial1 = s1.get("Serial number", "?")
    serial2 = s2.get("Serial number", "?")
    sig1 = s1.get("Signature key", "").strip()
    sig2 = s2.get("Signature key", "").strip()
    if serial1 != serial2 or sig1 != sig2:
        info(f"NK#{card_num}: card-status reads disagreed "
             f"(read1: serial={serial1!r} sig={sig1[:24]!r} | "
             f"read2: serial={serial2!r} sig={sig2[:24]!r}). "
             f"Skipping cleanup — refusing to act on transient state.")
        return
    card_serial = serial1
    current_sig = sig1
    if card_serial == "?" or "[none]" in current_sig or not current_sig:
        return  # nothing to clean — card has no sig key
    # gpg --card-status formats Signature key as space-separated 4-char hex
    # groups (e.g., "ABCD EFGH IJKL MNOP ..."). Last 16 hex chars unspaced
    # is the keyid (matches --with-colons field 4).
    current_keyid = current_sig.replace(" ", "").upper()[-16:]
    # Validate keyid format — refuse to act on a garbled read that happened
    # to agree with another garbled read. A real keyid is 16 hex chars.
    if not re.match(r"^[0-9A-F]{16}$", current_keyid):
        info(f"NK#{card_num}: parsed keyid {current_keyid!r} is not 16 hex chars. "
             f"Skipping cleanup — refusing to act on suspect read.")
        return

    r = cap(["gpg", "--list-secret-keys", "--with-colons", master_fp], env=env)
    sub_idx = 0
    dead_indices = []
    for ln in r.stdout.splitlines():
        if not ln.startswith("ssb"):
            continue
        sub_idx += 1
        fields = ln.split(":")
        sub_keyid = fields[4].upper() if len(fields) > 4 else ""
        # Field 14 in --with-colons holds the card AID/serial for stubs
        # (e.g., D276000124010304000FB97534810000). Empty for disk-resident
        # subs without a card-no annotation.
        ssb_serial_field = fields[14].upper() if len(fields) > 14 else ""
        if card_serial.upper() in ssb_serial_field and sub_keyid != current_keyid:
            dead_indices.append((sub_idx, sub_keyid))

    if not dead_indices:
        return

    info(f"NK#{card_num}: found {len(dead_indices)} dead-reference stub(s) "
         f"{[d[1] for d in dead_indices]} (current on-card keyid: {current_keyid})")
    for idx, _kid in sorted(dead_indices, reverse=True):
        cmd = (
            f"gpg --command-fd 0 --pinentry-mode=loopback "
            f"--passphrase {shlex.quote(master_pass)} --edit-key {master_fp}"
        )
        _blast(cmd, [f"key {idx}", "delkey", "y", "save"],
               timeout=30, env=env, label=f"cleanup-dead-ref-nk{card_num}-key{idx}")

    # Verify cleanup
    r2 = cap(["gpg", "--list-secret-keys", "--with-colons", master_fp], env=env)
    remaining = []
    sub_idx = 0
    for ln in r2.stdout.splitlines():
        if not ln.startswith("ssb"):
            continue
        sub_idx += 1
        fields = ln.split(":")
        sub_keyid = fields[4].upper() if len(fields) > 4 else ""
        ssb_serial_field = fields[14].upper() if len(fields) > 14 else ""
        if card_serial.upper() in ssb_serial_field and sub_keyid != current_keyid:
            remaining.append(sub_keyid)
    if remaining:
        fatal(f"NK#{card_num}: dead-ref cleanup incomplete — {len(remaining)} dead "
              f"ref(s) still present: {remaining}")
    ok(f"NK#{card_num}: removed {len(dead_indices)} dead reference(s) from master keyring")


def gpg_keytocard_subkey(master_fp, master_pass, admin_pin, env, timeout=180):
    """keytocard the latest sign-subkey to slot 1 (signing).

    Two cases handled:
    - Empty slot: blast 'key N -> keytocard -> 1 -> master_pass -> admin_pin -> save'
    - Already-occupied slot: blast 'key N -> keytocard -> 1 -> y -> master_pass -> admin_pin -> save'
      (gpg prompts 'Replace existing key? (y/N)' before pinentry when slot is occupied)

    Detect via gpg --card-status: if Signature key is set, slot is occupied.

    Pinentry order: master_pass FIRST, admin_pin SECOND (confirmed via gpg-agent
    debug log: KEYTOCARD command issues two inquiries — first for master keyring
    unprotect passphrase, second for card Admin PIN).
    """
    r = cap(["gpg", "--list-keys", "--with-colons", master_fp], env=env)
    new_key_idx = sum(1 for ln in r.stdout.splitlines() if ln.startswith("sub:"))

    # Check if card slot 1 is already occupied — if so, gpg will prompt for
    # replace confirmation before firing pinentry. Need 'y' before master_pass.
    s = gpg_card_status()
    slot_occupied = "[none]" not in s.get("Signature key", "")

    cmd = f"gpg --command-fd 0 --pinentry-mode=loopback --edit-key {master_fp}"
    if slot_occupied:
        info(f"Card slot 1 already has a key — keytocard will replace it (sending 'y' to confirm)")
        lines = [f"key {new_key_idx}", "keytocard", "1", "y", master_pass, admin_pin, "save"]
    else:
        lines = [f"key {new_key_idx}", "keytocard", "1", master_pass, admin_pin, "save"]
    return _blast(cmd, lines, timeout=timeout, env=env, label=f"keytocard-key{new_key_idx}")


# ============================================================================
# PIV operations (opensc-tool APDU + pkcs11-tool + openssl)
# ============================================================================
PIV_FAC_MGM_HEX = "01:02:03:04:05:06:07:08:01:02:03:04:05:06:07:08:01:02:03:04:05:06:07:08"


def piv_factory_reset():
    """Reset PIV applet via opensc-tool APDU sequence (validated against Nitrokey 3 PIV applet)."""
    cmd = [
        "opensc-tool",
        "-s", "00:A4:04:00:0B:A0:00:00:03:08:00:00:10:00:01:00",
        "-s", "00:20:00:80:08:33:33:33:33:33:33:33:33",
        "-s", "00:20:00:80:08:33:33:33:33:33:33:33:33",
        "-s", "00:20:00:80:08:33:33:33:33:33:33:33:33",
        "-s", "00:FB:00:00",
    ]
    r = cap(cmd)
    if "Received (SW1=0x90, SW2=0x00)" not in r.stdout.splitlines()[-1]:
        # Final APDU should succeed
        raise RuntimeError(f"PIV reset final APDU did not return SW=9000:\n{r.stdout}")


def piv_change_user_pin(old_pin, new_pin):
    """Change PIV User PIN via pkcs11-tool."""
    r = cap([
        "pkcs11-tool", "--module", PKCS11_MOD,
        "--login", "--login-type", "user",
        "--pin", old_pin,
        "--change-pin", "--new-pin", new_pin,
    ])
    if "PIN successfully changed" not in (r.stdout + r.stderr):
        # Some versions of pkcs11-tool don't print explicitly on success; check rc
        if r.returncode != 0:
            raise RuntimeError(f"PIV PIN change failed: {r.stdout}\n{r.stderr}")


def piv_keygen_9c(fac_path, out_pubkey_der, timeout=30):
    """Generate RSA-2048 keypair on PIV slot 9c using factory mgmt key."""
    env = os.environ.copy()
    env["PIV_EXT_AUTH_KEY"] = fac_path
    r = cap(
        ["piv-tool", "-A", "M:9B:03", "-G", "9C:07", "-o", out_pubkey_der],
        timeout=timeout, env=env,
    )
    # piv-tool returns non-zero on success (known quirk); check file presence
    if not Path(out_pubkey_der).exists() or Path(out_pubkey_der).stat().st_size == 0:
        raise RuntimeError(f"PIV keygen produced no pubkey:\n{r.stdout}\n{r.stderr}")


def piv_write_cert_9c(fac_path, cert_pem, timeout=30, verify=True):
    """Write a cert (PEM) to PIV slot 9c. piv-tool exit codes are unreliable
    (returns non-zero on quirky-success), so we always verify via pkcs11-tool
    that the Certificate Object actually appears on the slot. This catches
    silent piv-tool failures BEFORE the selfsign step (which needs cert on
    slot to expose the on-card private key via PKCS#11 emulation).
    """
    env = os.environ.copy()
    env["PIV_EXT_AUTH_KEY"] = fac_path
    r1 = cap(
        ["piv-tool", "-A", "M:9B:03", "-C", "9C", "-i", cert_pem],
        timeout=timeout, env=env,
    )
    if not verify:
        return

    # Force pcscd/scdaemon refresh so PKCS#11 sees the updated slot state.
    cap(["gpgconf", "--kill", "scdaemon"])
    time.sleep(1.5)

    # Initialize PKCS#11 token state. Empirically the openssl-via-pkcs11 selfsign
    # only succeeds reliably after these four pkcs11-tool warm-up queries:
    #   -L (list slots), -O (list objects), --list-objects --type privkey,
    #   --list-objects --type cert.
    # Even the privkey listing (which returns 0 objects on PIV — by design,
    # privkey enumeration is restricted) appears to fully initialize the token's
    # internal state in a way the cert-only listing doesn't. Without all four
    # calls, opensc PIV emulation may refuse to expose the on-card private key
    # to subsequent PKCS#11 URI lookups, producing "Could not find private key".
    cap(["pkcs11-tool", "--module", PKCS11_MOD, "-L"], timeout=15)
    cap(["pkcs11-tool", "--module", PKCS11_MOD, "-O"], timeout=15)
    cap(["pkcs11-tool", "--module", PKCS11_MOD, "--list-objects", "--type", "privkey"], timeout=15)

    # Verify by listing certs via PKCS#11
    r2 = cap(
        ["pkcs11-tool", "--module", PKCS11_MOD, "--list-objects", "--type", "cert"],
        timeout=15,
    )
    if "Certificate Object" not in (r2.stdout + r2.stderr):
        info("piv-tool cert-write stdout/stderr:")
        for line in (r1.stdout + r1.stderr).splitlines():
            info(f"  | {line}")
        info("pkcs11-tool list-cert stdout/stderr:")
        for line in (r2.stdout + r2.stderr).splitlines():
            info(f"  | {line}")
        raise RuntimeError(
            "PIV cert write verification failed — no Certificate Object visible to "
            "PKCS#11 after piv-tool returned. Slot 9c keypair may exist but the "
            "chicken-egg-fix cert is missing; subsequent openssl req would fail with "
            "'Could not find private key'."
        )


def piv_selfsign_9c(subj_cn, days, user_pin, out_cert_pem, timeout=120):
    """Self-sign a cert with PIV slot 9c on-card private key via openssl + pkcs11-provider.
    Uses Python pexpect directly on openssl (no Tcl expect intermediary). pkcs11-provider 1.0
    has buggy non-interactive PIN handling, so it requires PIN entry via the PTY prompt loop.
    """
    env = os.environ.copy()
    env["PKCS11_MODULE_PATH"] = PKCS11_MOD
    args = [
        "req", "-x509", "-new",
        "-key", "pkcs11:id=%02;type=private",
        "-provider", "pkcs11", "-provider", "default",
        "-subj", f"/CN={subj_cn}",
        "-days", str(days),
        "-set_serial", "1",
        "-out", out_cert_pem,
    ]
    p = pexpect.spawn("openssl", args=args, encoding="utf-8", timeout=timeout, env=env)
    captured = []
    try:
        # openssl + pkcs11-provider may prompt twice (once for "pass phrase",
        # once for "PIN") or once. Loop until EOF or timeout.
        while True:
            idx = p.expect(
                [r"PIN.*:\s*$", r"pass phrase.*:\s*$", pexpect.EOF, pexpect.TIMEOUT],
                timeout=30,
            )
            captured.append(p.before or "")
            if idx in (0, 1):
                p.sendline(user_pin)
                continue
            break
    finally:
        try:
            captured.append(p.before or "")
        except Exception:
            pass
        p.close(force=True)

    if not Path(out_cert_pem).exists() or Path(out_cert_pem).stat().st_size == 0:
        # Print the captured openssl output so the failure mode is visible
        info("openssl output (for diagnosis):")
        for chunk in captured:
            for line in (chunk or "").splitlines():
                info(f"  | {line}")
        raise RuntimeError("PIV selfsign did not produce a cert")


def piv_change_mgm_key(old_fac_path, new_mgm_hex_colons):
    """Change PIV mgmt key via raw APDU. Returns nothing, raises on failure."""
    env = os.environ.copy()
    env["PIV_EXT_AUTH_KEY"] = old_fac_path
    apdu = f"00:FF:FF:FE:23:0C:9B:20:{new_mgm_hex_colons}"
    cap(["piv-tool", "-A", "M:9B:03", "-s", apdu], env=env)


# ============================================================================
# Stages
# ============================================================================
def stage_pre_flight():
    banner("Pre-flight")
    if not DRIVE2.is_dir():
        fatal(f"Drive #2 not mounted at {DRIVE2}")
    info(f"Drive #2 found at {DRIVE2}")

    # Sudo bootstrap (Tails Defaults: amnesia !authenticate via /etc/sudoers.d/zzzz-...)
    if cap(["sudo", "-n", "true"]).returncode != 0:
        step("Sudo setup — enter admin password ONCE")
        info("Sets passwordless sudo for the rest of the ceremony.")
        info("Scoped to this Tails session — wiped on reboot (amnesic).")
        run([
            "sudo", "bash", "-c",
            'rm -f /etc/sudoers.d/always-ask-password 2>/dev/null; '
            'echo "amnesia ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/zzzz-ceremony-nopasswd; '
            'chmod 0440 /etc/sudoers.d/zzzz-ceremony-nopasswd; '
            'echo "Defaults:amnesia !authenticate" >> /etc/sudoers.d/zzzz-ceremony-nopasswd'
        ])
        if cap(["sudo", "-n", "true"]).returncode != 0:
            fatal("Sudo setup failed")
    ok("Passwordless sudo configured")

    CEREMONY.mkdir(exist_ok=True)


def stage_install_debs():
    banner("Stage 2 — Install offline-debs + scdaemon.conf")
    # Skip the (slow ~1min) dpkg install if the bundle's tools are already present
    # from a prior run in the same Tails session. Tails is amnesic so reboot wipes
    # everything and the install runs again on the next boot — no persistence risk.
    needed = ["opensc-tool", "piv-tool", "pkcs11-tool", "expect", "paperkey"]
    missing = [t for t in needed if not shutil.which(t)]
    if missing:
        debian13 = DRIVE2 / "debian13"
        if not debian13.is_dir():
            fatal(f"{debian13} missing")
        debs = sorted(debian13.glob("*.deb"))
        info(f"Installing {len(debs)} debs from {debian13} (missing: {', '.join(missing)})")
        run(["sudo", "-n", "dpkg", "-i"] + [str(d) for d in debs])
    else:
        info(f"Bundle tools already on PATH (skipping dpkg install)")
    setup_scdaemon_conf()
    revive_pcscd()
    ok("Bundle ready, scdaemon.conf written, pcscd alive")


def stage_collect_secrets():
    banner("Stage 1 — Front-loaded common secrets")
    info("Tails offline can't print directly. Paperkey transits via Drive #2 to workstation per runbook §C2.5.")
    master_pass = secret_or_value("master_pass", "Master passphrase (24+ chars, RECORD ON PAPER)", min_len=24)
    record_paper("Master passphrase set. Confirm you've written it on paper.")
    luks_pass = secret_or_value("luks_pass", "LUKS passphrase (24+ chars, MUST DIFFER from master)", min_len=24)
    if master_pass == luks_pass:
        fatal("LUKS and master passphrases must differ")
    record_paper("LUKS passphrase set. Confirm you've written it on paper.")
    return master_pass, luks_pass


def stage_phase_0_one_card(card_num, do_piv_dryrun=False):
    """Run Phase 0 for one NK. Returns dict with {upin, apin, ppin, puk, serial}."""
    banner(f"Phase 0 — Nitrokey #{card_num}")
    step(f"PLUG NK#{card_num} into the laptop now (USB-C port)")
    ask_enter(f"Press Enter when NK#{card_num} is plugged")

    revive_pcscd()
    assert_nk_present()
    assert_not_bootloader()

    # SAFETY: refuse to wipe a card that already has OpenPGP keys present (don't
    # accidentally destroy a real signing key by plugging the wrong card).
    s = gpg_card_status()
    if s.get("Application type") == "OpenPGP":
        if "[none]" not in s.get("Signature key", "") or "[none]" not in s.get("Encryption key", ""):
            fatal(
                f"NK#{card_num} has keys present:\n"
                f"  Signature key:  {s.get('Signature key')}\n"
                f"  Encryption key: {s.get('Encryption key')}\n"
                f"This card is not safe to use as a fresh ceremony card. "
                f"Use a different NK or factory-reset this one manually."
            )

    # COMPLETE factory reset — both OpenPGP applet AND PIV applet. Eliminates any
    # residue from prior failed runs: stuck PIN counters, leftover slot 9c keypairs,
    # cached PIN sessions, lingering mgmt-key state, etc. Idempotent — safe even on
    # already-factory cards. Without this, §0.5 PIV dry-run can fail with "Could not
    # find private key" because the PIV applet has stale state from prior attempts.
    info(f"Factory-resetting NK#{card_num} (OpenPGP + PIV) for clean ceremony start...")
    gpg_card_factory_reset()
    revive_pcscd()
    piv_factory_reset()
    revive_pcscd()

    # Validate OpenPGP factory state now
    try:
        assert_card_factory_openpgp()
    except RuntimeError as e:
        fail(str(e))
        fatal(f"NK#{card_num} OpenPGP applet did not return to factory state after reset.")

    serial = get_card_serial()
    ok(f"NK#{card_num} OpenPGP applet factory state confirmed (serial {serial})")

    # Collect PINs (from values.txt if present, else interactive prompts) — written on paper either way
    upin = secret_or_value(f"nk{card_num}_upin", f"OpenPGP User PIN for NK#{card_num} (6-8 digits)", pattern=r"^[0-9]{6,8}$")
    apin = secret_or_value(f"nk{card_num}_apin", f"OpenPGP Admin PIN for NK#{card_num} (8+ printable chars)", pattern=r"^[\x20-\x7e]{8,}$")
    ppin = secret_or_value(f"nk{card_num}_ppin", f"PIV User PIN for NK#{card_num} (6-8 digits)", pattern=r"^[0-9]{6,8}$")
    puk = secret_or_value(f"nk{card_num}_puk", f"PIV PUK for NK#{card_num} (8 digits)", pattern=r"^[0-9]{8}$")

    # §0.3 OpenPGP User PIN rotation (factory 123456 -> upin)
    step(f"§0.3a Rotate OpenPGP User PIN (NK#{card_num})")
    revive_pcscd()
    gpg_card_change_user_pin("123456", upin)
    s = gpg_card_status()
    if not s.get("PIN retry counter", "").startswith("3 "):
        fatal(f"User PIN rotation may have failed; PIN retry counter: {s.get('PIN retry counter')}")
    ok("User PIN rotated")

    # §0.3b OpenPGP Admin PIN rotation (factory 12345678 -> apin)
    step(f"§0.3b Rotate OpenPGP Admin PIN (NK#{card_num})")
    revive_pcscd()
    gpg_card_change_admin_pin("12345678", apin)
    ok("Admin PIN rotated")

    # §0.4 URL placeholder
    step(f"§0.4 Set URL placeholder (NK#{card_num})")
    revive_pcscd()
    url = "https://keys.openpgp.org/vks/v1/by-fingerprint/PLACEHOLDER_REPLACE_POST_C3"
    gpg_card_set_url(url, apin)
    ok(f"URL set to {url}")

    # §0.5 PIV dry-run (NK#4 only, validates the PIV chain end-to-end)
    if do_piv_dryrun:
        step(f"§0.5 PIV dry-run (NK#{card_num})")
        revive_pcscd()
        fac = "/tmp/factory-mgm.key"
        Path(fac).write_text(PIV_FAC_MGM_HEX + "\n")
        # 1. keygen
        piv_keygen_9c(fac, "/tmp/test-9c.der")
        ok("§0.5 keygen produced pubkey DER")
        # 2. dummy cert (chicken-egg fix for opensc PIV PKCS#11 emulation)
        run([
            "openssl", "genrsa", "-out", "/tmp/dummy.key", "2048"
        ], stderr=subprocess.DEVNULL)
        run([
            "openssl", "req", "-x509", "-new", "-key", "/tmp/dummy.key",
            "-subj", "/CN=dummy-stub-overwrite", "-days", "1",
            "-out", "/tmp/dummy-cert.pem",
        ], stderr=subprocess.DEVNULL)
        piv_write_cert_9c(fac, "/tmp/dummy-cert.pem")
        time.sleep(1)
        # Force a clean pcscd session before openssl-via-pkcs11 selfsign — clears any
        # stuck applet-selection state from prior OpenPGP work or pkcs11-tool activity.
        revive_pcscd()
        # 3. real selfsign via on-card key + pkcs11-provider (PIN driven via pexpect)
        piv_selfsign_9c(
            "intergen-test-9c-throwaway", 30, "123456", "/tmp/test-9c-cert.pem",
        )
        if not Path("/tmp/test-9c-cert.pem").exists() or Path("/tmp/test-9c-cert.pem").stat().st_size == 0:
            fatal("§0.5 selfsign produced no cert")
        ok(f"§0.5 selfsign PASS ({Path('/tmp/test-9c-cert.pem').stat().st_size} bytes)")
        # 4. cleanup PIV
        for f in [fac, "/tmp/test-9c.der", "/tmp/dummy.key", "/tmp/dummy-cert.pem", "/tmp/test-9c-cert.pem"]:
            cap(["shred", "-uvz", f])

    # §0.6 PIV factory-reset
    step(f"§0.6 PIV factory-reset (NK#{card_num})")
    revive_pcscd()
    piv_factory_reset()
    ok("PIV reset (slot 9c cleared, mgmt key back to factory)")

    # §0.7 PIV PIN rotation (factory 123456 -> ppin)
    step(f"§0.7 Rotate PIV User PIN (NK#{card_num})")
    piv_change_user_pin("123456", ppin)
    info("(PIV PUK rotation skipped — Nitrokey 3 PIV doesn't expose PUK via pkcs11-tool SO-login. "
         "PUK stays factory 12345678; rotate post-ceremony via raw APDU.)")
    ok("PIV User PIN rotated")

    # §0.8 Identity capture
    s = gpg_card_status()
    log_entry = (
        f"## NK#{card_num}\n"
        f"**Captured:** {datetime.now(timezone.utc).isoformat()}\n\n"
        f"### gpg --card-status (excerpts)\n```\n"
        f"Application ID: {s.get('Application ID', '?')}\n"
        f"Manufacturer:   {s.get('Manufacturer', '?')}\n"
        f"Serial:         {s.get('Serial number', '?')}\n"
        f"Version:        {s.get('Version', '?')}\n"
        f"URL:            {s.get('URL of public key', '?')}\n"
        f"```\n"
        f"### PINs (recorded on paper)\n"
        f"- OpenPGP User PIN:  {upin}\n"
        f"- OpenPGP Admin PIN: {apin}\n"
        f"- PIV User PIN:      {ppin}\n"
        f"- PIV PUK:           {puk}\n\n---\n\n"
    )
    with (CEREMONY / "identity-log.md").open("a") as f:
        f.write(log_entry)

    record_paper(
        f"NK#{card_num} PINs (record ALL FOUR on paper):\n"
        f"  OpenPGP User PIN:  {upin}\n"
        f"  OpenPGP Admin PIN: {apin}\n"
        f"  PIV User PIN:      {ppin}\n"
        f"  PIV PUK:           {puk}"
    )

    step(f"Phase 0 NK#{card_num} complete. UNPLUG NK#{card_num} and place in pouch.")
    ask_enter(f"Press Enter when NK#{card_num} is unplugged + pouched")

    return {"upin": upin, "apin": apin, "ppin": ppin, "puk": puk, "serial": serial}


def stage_master_keypair(master_pass):
    banner("Stage 4 — Master keypair generation (no NK plugged)")
    gnupghome = CEREMONY / "gnupg-master"
    if gnupghome.exists():
        shutil.rmtree(gnupghome)
    gnupghome.mkdir(mode=0o700)

    # CRITICAL: set GNUPGHOME globally so gpgconf --kill scdaemon (and other
    # subprocess calls without explicit env) target the ceremony's scdaemon,
    # not default ~/.gnupg. See comment in main()'s resume path.
    os.environ["GNUPGHOME"] = str(gnupghome)
    env = os.environ.copy()

    # Write scdaemon.conf + gpg-agent.conf into the ceremony's GNUPGHOME so the
    # scdaemon spawned for THIS keyring uses disable-ccid + pcsc-shared. Without
    # this, scdaemon defaults to direct CCID, conflicts with pcscd, and reports
    # "No such device" on every card operation downstream.
    write_gpg_configs_to(gnupghome)
    # Kill any scdaemon spawned earlier under default GNUPGHOME so the next
    # gpg call spawns a fresh one with the new config.
    cap(["gpgconf", "--kill", "scdaemon"])
    time.sleep(1)

    keygen_input = f"""%echo Generating InterGenOS master keypair (per runbook C2.2)
Key-Type: RSA
Key-Length: 4096
Key-Usage: sign,cert
Subkey-Type: RSA
Subkey-Length: 4096
Subkey-Usage: encrypt
Name-Real: {NAME}
Name-Comment: {COMMENT}
Name-Email: {EMAIL}
Expire-Date: {EXPIRY}
%commit
%echo Master keypair generated
"""
    info("Generating 4096-bit RSA master + 4096-bit RSA encryption subkey (this takes 30-90 seconds)...")
    p = subprocess.run(
        ["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", master_pass, "--gen-key"],
        input=keygen_input, capture_output=True, text=True, env=env, timeout=300,
    )
    if p.returncode != 0:
        fatal(f"Master keypair generation failed:\n{p.stdout}\n{p.stderr}")
    ok("Master keypair generated")

    r = cap(["gpg", "--list-keys", "--with-colons", EMAIL], env=env)
    master_fp = None
    for ln in r.stdout.splitlines():
        if ln.startswith("fpr:"):
            master_fp = ln.split(":")[9]
            break
    if not master_fp:
        fatal(f"Could not extract master FP from gpg --list-keys:\n{r.stdout}")
    ok(f"Master fingerprint: {master_fp}")

    record_paper(f"MASTER FINGERPRINT (record on paper):\n  {master_fp}")

    # Save revocation certificate (gpg auto-creates it during keygen)
    revoc = gnupghome / "openpgp-revocs.d" / f"{master_fp}.rev"
    if revoc.exists():
        target = CEREMONY / "revocation.asc"
        shutil.copy(revoc, target)
        target.chmod(0o600)
        ok(f"Revocation cert saved to {target}")

    log = CEREMONY / "identity-log.md"
    with log.open("a") as f:
        f.write(
            f"## Master keypair (C2)\n"
            f"**Fingerprint:** `{master_fp}`\n"
            f"**UID:** {NAME} ({COMMENT}) <{EMAIL}>\n"
            f"**Expiry:** {EXPIRY} (does not expire)\n"
            f"**Created:** {datetime.now(timezone.utc).isoformat()}\n\n---\n\n"
        )

    return master_fp, env


def stage_paperkey(master_fp, master_pass, env):
    banner("Stage 5 — Paperkey export + Drive #2 transit")
    paperkey_path = CEREMONY / f"paperkey-{master_fp[:8]}.txt"

    p1 = subprocess.Popen(
        ["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", master_pass,
         "--export-secret-keys", master_fp],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
    )
    p2 = subprocess.Popen(
        ["paperkey", "--output-type", "base16", "--output", str(paperkey_path)],
        stdin=p1.stdout,
    )
    p1.stdout.close()
    p2.wait(timeout=60)
    p1.wait(timeout=60)
    if p2.returncode != 0:
        fatal("paperkey export failed")
    if not paperkey_path.exists() or paperkey_path.stat().st_size < 1000:
        fatal(f"paperkey output too small: {paperkey_path}")
    ok(f"Paperkey: {paperkey_path.stat().st_size} bytes (base16, printable)")

    step("PLUG Drive #2 (transit USB) for paperkey transport")
    ask_enter(f"Press Enter when Drive #2 is plugged + auto-mounted at {DRIVE2}")
    if not DRIVE2.is_dir():
        fatal(f"Drive #2 not mounted at {DRIVE2}")
    target = DRIVE2 / paperkey_path.name
    shutil.copy(paperkey_path, target)
    cap(["sync"])
    ok(f"Paperkey copied to {target}")

    print()
    print("*** PAPERKEY TRANSIT ***")
    print("1. Eject Drive #2")
    print("2. Take to workstation, print 2 paper copies (gnome-text-editor, monospace font)")
    print("3. Verify both legible")
    print("4. Bring Drive #2 back, plug in")
    ask_enter("Press Enter ONLY after both paper copies are confirmed legible + you're back")

    return paperkey_path


def stage_luks_backup(master_fp, master_pass, luks_pass, env):
    banner("Stage 6 — LUKS USB backup (Drive #3)")
    step("PLUG Drive #3 (CEREMONY) into the laptop now")
    ask_enter(f"Press Enter when Drive #3 is plugged + auto-mounted at {DRIVE3}")
    if not DRIVE3.is_dir():
        fatal(f"Drive #3 not mounted at {DRIVE3}")

    luks_file = DRIVE3 / "master-backup.luks"
    run(["truncate", "-s", "50M", str(luks_file)])
    p = subprocess.run(
        ["sudo", "-n", "cryptsetup", "luksFormat", "--type", "luks2", str(luks_file),
         "--key-file=-", "--batch-mode"],
        input=luks_pass.encode(), check=True,
    )
    luks_name = f"master-luks-{os.getpid()}"
    p = subprocess.run(
        ["sudo", "-n", "cryptsetup", "luksOpen", str(luks_file), luks_name, "--key-file=-"],
        input=luks_pass.encode(), check=True,
    )
    run(["sudo", "-n", "mkfs.ext4", "-q", "-L", "master-backup", f"/dev/mapper/{luks_name}"])
    mnt = Path(f"/tmp/luks-mnt-{os.getpid()}")
    run(["sudo", "-n", "mkdir", "-p", str(mnt)])
    run(["sudo", "-n", "mount", f"/dev/mapper/{luks_name}", str(mnt)])
    run(["sudo", "-n", "chown", f"{os.getuid()}:{os.getgid()}", str(mnt)])

    secret_target = mnt / "master-secret.asc"
    p = subprocess.run(
        ["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", master_pass,
         "--armor", "--export-secret-keys", master_fp],
        capture_output=True, text=True, env=env, check=True,
    )
    secret_target.write_text(p.stdout)

    revoc = CEREMONY / "revocation.asc"
    if revoc.exists():
        shutil.copy(revoc, mnt / "revocation.asc")

    run(["sudo", "-n", "cryptsetup", "luksHeaderBackup", str(luks_file),
         "--header-backup-file", str(mnt / "master-backup.luks.headerbak")])
    run(["sudo", "-n", "chown", f"{os.getuid()}:{os.getgid()}", str(mnt / "master-backup.luks.headerbak")])
    cap(["sync"])
    info(f"LUKS contents: {sorted(p.name for p in mnt.iterdir())}")
    run(["sudo", "-n", "umount", str(mnt)])
    run(["sudo", "-n", "cryptsetup", "luksClose", luks_name])
    run(["sudo", "-n", "rmdir", str(mnt)])
    ok(f"LUKS backup: {luks_file.stat().st_size} bytes on Drive #3")


def stage_keytocard_one(card_num, master_fp, master_pass, pins, env):
    """Stage 7 for one NK: addkey + keytocard + UIF + test sign."""
    banner(f"Stage 7 — keytocard [S{card_num}] -> NK#{card_num}")
    step(f"PLUG NK#{card_num} for keytocard")
    ask_enter(f"Press Enter when NK#{card_num} is plugged")
    revive_pcscd()
    assert_nk_present()
    assert_not_bootloader()

    # Confirm we're talking to the right card via serial. In resume mode the
    # expected serial is "?" (placeholder — Phase 0 didn't run this session, so
    # we don't have the actual serial recorded). Skip the check in that case.
    s = gpg_card_status()
    serial = s.get("Serial number", "?")
    if pins["serial"] == "?":
        info(f"NK#{card_num} serial: {serial} (resume mode — no Phase 0 capture to compare against)")
    elif pins["serial"] != serial:
        fail(f"Card serial mismatch: expected {pins['serial']}, got {serial}")
        ask_enter("Continue anyway (maybe you swapped NKs)?")

    apin = pins["apin"]
    upin = pins["upin"]

    # PRE-STAGE-7 DIAGNOSTIC SNAPSHOT — captured to trace log
    trace_block(f"NK#{card_num} PRE-STAGE-7 gpg --card-status",
                cap(["gpg", "--card-status"], env=env).stdout)
    trace_block(f"NK#{card_num} PRE-STAGE-7 gpg --list-keys",
                cap(["gpg", "--list-keys", "--with-colons", master_fp], env=env).stdout)
    trace_block(f"NK#{card_num} PRE-STAGE-7 gpg --list-secret-keys",
                cap(["gpg", "--list-secret-keys", "--with-colons", master_fp], env=env).stdout)

    # Card slot attributes default to rsa2048 on Nitrokey 3. We import RSA-4096
    # subkeys, so the slot must be configured to accept that. Set explicitly
    # before keytocard — without this, keytocard fails with 'Invalid time' on
    # any algorithm mismatch.
    s = gpg_card_status()
    current_attr = s.get("Key attributes", "")
    trace(f"NK#{card_num} pre-keyattr Key attributes: {current_attr!r}")
    if current_attr != "rsa4096 rsa4096 rsa4096":
        step(f"Set NK#{card_num} key-attr to RSA-4096 (currently: {current_attr})")
        # Kill scdaemon BEFORE the blast — the prior gpg --card-status calls
        # leave scdaemon in a state where the next gpg subprocess gets
        # "selecting card failed: No such device". Fresh scdaemon spawn fixes this.
        revive_pcscd()
        keyattr_out = gpg_card_set_key_attr_rsa4096(apin, env=env)
        revive_pcscd()
        s = gpg_card_status()
        if s.get("Key attributes") != "rsa4096 rsa4096 rsa4096":
            # Surface the captured PTY output of the gpg --card-edit > key-attr blast
            # so we can see exactly what went wrong (Bad PIN / wrong menu input / etc.)
            info("================================================================")
            info(f"  KEY-ATTR CHANGE FAILED for NK#{card_num} — full diagnostic dump:")
            info("================================================================")
            info("--- gpg --card-edit > key-attr PTY output ---")
            for line in (keyattr_out or "").splitlines():
                info(f"  | {line}")
            info("--- post-attempt card-status ---")
            for line in s.get("_raw", "").splitlines():
                info(f"  | {line}")
            info(f"--- FULL TRACE LOG: {TRACE_LOG_PATH} ---")
            info(f"--- scdaemon log: /tmp/scdaemon-ceremony.log ---")
            info(f"--- gpg-agent log: /tmp/gpg-agent-ceremony.log ---")
            info("================================================================")
            fatal(f"key-attr change failed — card still reports: {s.get('Key attributes')}")
        ok(f"NK#{card_num} key-attr set to RSA-4096")
    else:
        info(f"NK#{card_num} key-attr already RSA-4096")

    # CLEANUP DEAD REFS: remove stubs on the master keyring that annotate this
    # card's serial but whose keyid no longer matches what's currently on the
    # card (e.g., a prior keytocard-replace overwrote the on-card key, leaving
    # the original stub orphaned). Must run BEFORE skip-keytocard check so the
    # check only sees live stubs. Idempotent — no-op if no dead refs exist.
    cleanup_dead_card_refs(card_num, master_fp, master_pass, env)

    # RESUME-FRIENDLY: if NK already has a Signature key AND there's a matching
    # subkey on the master keyring (with card-no annotation, indicating it's a
    # stub for THIS card), the addkey + keytocard already happened in a prior
    # run. Skip ahead to UIF + PIN reset + test-sign.
    #
    # Use --with-colons format for the substring check: the card AID/serial
    # appears unspaced in field 14 of stub lines (e.g.,
    # D276000124010304000FB97534810000). The non-colon human-readable format
    # prints serials with spaces ('B975 3481'), which breaks any contiguous
    # substring match.
    # DEFENSIVE DOUBLE-READ: a transient card-status read after revive_pcscd
    # can return Signature key=[none] or Serial=? even when the card is fine.
    # Acting on that bypasses skip-keytocard and triggers an unnecessary
    # addkey, accumulating subs on the master keyring. If the first read
    # looks unhealthy, confirm with a second read 3s later — only believe
    # the unhealthy reading if both reads agree. Skips the wait when the
    # first read is healthy (the common case).
    s = gpg_card_status()
    sig_key_on_card = s.get("Signature key", "")
    card_serial = s.get("Serial number", "?")
    if "[none]" in sig_key_on_card or card_serial == "?":
        info(f"NK#{card_num}: first card-status read looked unhealthy "
             f"(sig={sig_key_on_card[:24]!r}, serial={card_serial!r}) — "
             f"confirming with second read in 3s")
        time.sleep(3)
        s2 = gpg_card_status()
        sig2 = s2.get("Signature key", "")
        serial2 = s2.get("Serial number", "?")
        # If second read is healthier, prefer it
        if ("[none]" not in sig2 and serial2 != "?") and \
           ("[none]" in sig_key_on_card or card_serial == "?"):
            info(f"NK#{card_num}: second read healthy (sig={sig2[:24]!r}, "
                 f"serial={serial2!r}) — using second read for skip-check")
            s = s2
            sig_key_on_card = sig2
            card_serial = serial2
    skip_keytocard = False
    if "[none]" not in sig_key_on_card and card_serial != "?":
        sec_r = cap(["gpg", "--list-secret-keys", "--with-colons", master_fp], env=env)
        if card_serial.upper() in sec_r.stdout.upper():
            skip_keytocard = True
            ok(f"NK#{card_num} already has signing subkey {sig_key_on_card[:20]}... "
               f"(matched by card serial {card_serial} in master keyring) — "
               f"skipping addkey + keytocard, resuming at UIF/PIN-reset/test-sign")

    if not skip_keytocard:
        # NOTE: cleanup_disk_resident_sign_subkeys() removed here. The card-no
        # detection in find_disk_resident_sign_subkeys is unreliable across gpg
        # versions — it deleted legitimate on-card stubs as orphans, leading to
        # a master keyring with master+enc+(1 surviving stub) state. Don't run
        # cleanup until detection is bulletproof. Any orphans accumulating from
        # partial runs are harmless metadata; can be revoked post-ceremony.

        # Pre-check: how many subkeys does master keyring have already?
        r = cap(["gpg", "--list-keys", "--with-colons", master_fp], env=env)
        sub_count_before = sum(1 for ln in r.stdout.splitlines() if ln.startswith("sub:"))

        step(f"addkey new sign-subkey on master keyring (NK#{card_num})")
        revive_pcscd()
        addkey_out = gpg_addkey_sign_subkey(master_fp, master_pass, env)

        r = cap(["gpg", "--list-keys", "--with-colons", master_fp], env=env)
        sub_count_after = sum(1 for ln in r.stdout.splitlines() if ln.startswith("sub:"))
        trace_block(f"NK#{card_num} POST-ADDKEY gpg --list-keys",
                    cap(["gpg", "--list-keys", "--with-colons", master_fp], env=env).stdout)
        if sub_count_after <= sub_count_before:
            info("gpg --edit-key addkey output:")
            for line in (addkey_out or "").splitlines():
                info(f"  | {line}")
            info(f"FULL TRACE LOG: {TRACE_LOG_PATH}")
            fatal(f"addkey appears to have failed — sub count {sub_count_before} -> {sub_count_after} "
                  f"(expected to increase by 1)")
        ok(f"Master keyring now has {sub_count_after} subkey(s) ({sub_count_before} -> {sub_count_after})")

        step(f"keytocard latest sign-subkey to NK#{card_num}")
        revive_pcscd()
        sd_before_keytocard = _scdaemon_log_size()
        trace(f"=== STARTING keytocard for NK#{card_num} ===")
        keytocard_out = gpg_keytocard_subkey(master_fp, master_pass, apin, env)
        trace(f"=== keytocard call returned for NK#{card_num} ===")

        # Validate keytocard worked: --card-status Signature key should now be set
        s = gpg_card_status()
        trace_block(f"NK#{card_num} POST-KEYTOCARD gpg --card-status", s.get("_raw", ""))
        if "[none]" in s.get("Signature key", ""):
            info("================================================================")
            info(f"  KEYTOCARD FAILED for NK#{card_num} — full diagnostic dump:")
            info("================================================================")
            info("--- gpg --edit-key keytocard PTY output ---")
            for line in (keytocard_out or "").splitlines():
                info(f"  | {line}")
            info("--- scdaemon log delta during keytocard ---")
            for line in _scdaemon_log_delta(sd_before_keytocard).splitlines()[-200:]:
                info(f"  | {line}")
            info("--- gpg-agent log tail (last 80 lines) ---")
            agent_log = Path("/tmp/gpg-agent-ceremony.log")
            if agent_log.exists():
                for line in agent_log.read_text(errors="replace").splitlines()[-80:]:
                    info(f"  | {line}")
            info("--- post-keytocard card-status ---")
            for line in s.get("_raw", "").splitlines():
                info(f"  | {line}")
            info(f"--- FULL TRACE LOG: {TRACE_LOG_PATH} ---")
            info(f"--- scdaemon log: /tmp/scdaemon-ceremony.log ---")
            info(f"--- gpg-agent log: /tmp/gpg-agent-ceremony.log ---")
            info("================================================================")
            # ATOMIC UNDO: remove the just-added orphan sub before exit so a
            # subsequent --from-stage 7 retry starts from a clean keyring.
            # addkey appends at the highest index, so the orphan is at index
            # sub_count_after (1-based).
            new_sub_idx = sub_count_after
            info(f"Removing orphan sub at index {new_sub_idx} to keep keyring clean for retry")
            undo_cmd = (
                f"gpg --command-fd 0 --pinentry-mode=loopback "
                f"--passphrase {shlex.quote(master_pass)} --edit-key {master_fp}"
            )
            _blast(undo_cmd, [f"key {new_sub_idx}", "delkey", "y", "save"],
                   timeout=30, env=env, label=f"undo-addkey-{new_sub_idx}")
            r_post = cap(["gpg", "--list-keys", "--with-colons", master_fp], env=env)
            post_undo_count = sum(1 for ln in r_post.stdout.splitlines() if ln.startswith("sub:"))
            if post_undo_count != sub_count_before:
                fatal(f"keytocard failed AND orphan undo failed — keyring sub count "
                      f"{post_undo_count} (expected {sub_count_before}). Master keyring "
                      f"in unknown state — investigate before retry.")
            ok(f"Orphan removed; keyring restored to {sub_count_before} subs")
            fatal(f"keytocard failed — Signature key still [none] on NK#{card_num}")
        ok(f"Signature key on NK#{card_num}: {s.get('Signature key')[:30]}...")

    # UIF on. If skip_keytocard, this card was already keytocard'd in a prior run.
    # UIF set is idempotent (SETATTR UIF-1 0x01) so safe to re-run.
    step(f"UIF on for signing slot (NK#{card_num})")
    revive_pcscd()
    gpg_card_uif_on(1, apin)
    ok("UIF=on for signing slot")

    # Replace Phase 0's PLACEHOLDER_REPLACE_POST_C3 URL with the canonical
    # keys.openpgp.org URL that points to the actual master fingerprint.
    # The pubkey will resolve at this URL once published to the public keyserver.
    canonical_url = f"https://keys.openpgp.org/vks/v1/by-fingerprint/{master_fp}"
    step(f"Update URL on NK#{card_num} to canonical master-FP form")
    revive_pcscd()
    gpg_card_set_url(canonical_url, apin)
    revive_pcscd()
    s = gpg_card_status()
    if master_fp.upper() not in s.get("URL of public key", "").upper():
        fail(f"URL update may have failed — current URL: {s.get('URL of public key')}")
    else:
        ok(f"URL on NK#{card_num} now points to master FP")

    # Defensive User PIN reset: Phase 0 §0.3 may have silently failed to rotate
    # the User PIN to values.txt's nk{N}_upin. Test-sign with the wrong PIN
    # produces "Bad PIN" with no card touch (card never reaches UIF-blink state
    # because PIN auth fails first). Reset to known value via Admin PIN unblock.
    # Admin PIN is verified correct by virtue of having just succeeded for
    # keytocard + UIF, so this reset is reliable.
    step(f"Reset User PIN on NK#{card_num} to values.txt value (defensive)")
    revive_pcscd()
    pin_reset_out = gpg_card_unblock_user_pin(apin, upin, env=env)
    revive_pcscd()
    s = gpg_card_status()
    pin_counter = s.get("PIN retry counter", "")
    if not pin_counter.startswith("3 "):
        info("User PIN reset output:")
        for line in (pin_reset_out or "").splitlines():
            info(f"  | {line}")
        fatal(f"User PIN reset failed on NK#{card_num} — counter is {pin_counter} "
              f"(expected '3 X 3'). Likely Admin PIN auth failed.")
    ok(f"User PIN on NK#{card_num} set to values.txt value")

    step(f"Test-sign — TOUCH NK#{card_num} when it blinks (UIF requires touch)")
    Path("/tmp/tin.txt").write_text(f"test-{card_num}-{datetime.now(timezone.utc).isoformat()}\n")
    p = subprocess.run(
        ["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", upin,
         "--detach-sign", "/tmp/tin.txt"],
        capture_output=True, text=True, env=env,
    )
    if p.returncode != 0 or not Path("/tmp/tin.txt.sig").exists():
        info("gpg --detach-sign stdout/stderr:")
        for line in (p.stdout + "\n" + p.stderr).splitlines():
            info(f"  | {line}")
        fatal(f"Test-sign FAILED on NK#{card_num}")
    ok("Detach-sign produced /tmp/tin.txt.sig")

    p = subprocess.run(
        ["gpg", "--verify", "/tmp/tin.txt.sig", "/tmp/tin.txt"],
        capture_output=True, text=True, env=env,
    )
    if p.returncode != 0 or "Good signature" not in (p.stdout + p.stderr):
        fatal(f"Test-verify FAILED on NK#{card_num}: {p.stderr}")
    ok("Verify confirms Good signature")

    cap(["shred", "-uvz", "/tmp/tin.txt", "/tmp/tin.txt.sig"])

    step(f"[S{card_num}] keytocard'd to NK#{card_num}, UIF=on, test-sign verified. UNPLUG NK#{card_num}.")
    ask_enter(f"Press Enter when NK#{card_num} is unplugged")


def stage_pubkey_export(master_fp, luks_pass, env):
    banner("Stage 8 — Final pubkey export + LUKS update")
    pubkey_path = CEREMONY / "intergenos-release-key.asc"
    r = cap(["gpg", "--armor", "--export", master_fp], env=env)
    pubkey_path.write_text(r.stdout)
    shutil.copy(pubkey_path, DRIVE3 / "intergenos-release-key.asc")
    cap(["sync"])
    ok(f"Pubkey exported: {pubkey_path}")

    luks_name = f"luks-update-{os.getpid()}"
    p = subprocess.run(
        ["sudo", "-n", "cryptsetup", "luksOpen", str(DRIVE3 / "master-backup.luks"),
         luks_name, "--key-file=-"],
        input=luks_pass.encode(), check=True,
    )
    mnt = Path(f"/tmp/luks-update-{os.getpid()}")
    run(["sudo", "-n", "mkdir", "-p", str(mnt)])
    run(["sudo", "-n", "mount", f"/dev/mapper/{luks_name}", str(mnt)])
    run(["sudo", "-n", "chown", f"{os.getuid()}:{os.getgid()}", str(mnt)])
    shutil.copy(pubkey_path, mnt / "intergenos-release-key.asc")
    cap(["sync"])
    run(["sudo", "-n", "umount", str(mnt)])
    run(["sudo", "-n", "cryptsetup", "luksClose", luks_name])
    run(["sudo", "-n", "rmdir", str(mnt)])
    ok("LUKS updated with public key")


def stage_c6_piv(pins_nk1):
    banner("Stage 9 — PIV slot 9c keypair (vendor cert) on NK#1")

    # IDEMPOTENCY: if a previous run already completed Stage 9, the vendor cert
    # is on Drive #3 AND the PIV mgmt key on NK#1 has been rotated to a random
    # value. Re-running with the factory mgmt key would fail (mgmt key auth
    # rejected) AND would overwrite the existing valid cert. Detect by file
    # presence and skip if already done.
    cert_on_drive3 = DRIVE3 / "intergenos-vendor-cert.pem"
    if cert_on_drive3.exists() and cert_on_drive3.stat().st_size >= 500:
        info(f"Stage 9 already complete — vendor cert exists at {cert_on_drive3} "
             f"({cert_on_drive3.stat().st_size} bytes). Skipping to preserve PIV state.")
        info("(If you need to redo Stage 9, factory-reset NK#1 PIV first via "
             "opensc-tool APDU sequence to restore factory mgmt key.)")
        return

    step("PLUG NK#1 for C6")
    ask_enter("Press Enter when NK#1 is plugged")
    revive_pcscd()
    assert_not_bootloader()

    cert_pem = CEREMONY / "intergenos-vendor-cert.pem"
    pubkey_der = CEREMONY / "efi-9c-pubkey.der"
    fac = "/tmp/c6-fac.key"
    Path(fac).write_text(PIV_FAC_MGM_HEX + "\n")

    step("PIV keygen on slot 9c")
    piv_keygen_9c(fac, str(pubkey_der))

    step("Write dummy cert (chicken-egg fix), then real selfsign")
    run(["openssl", "genrsa", "-out", "/tmp/c6-dummy.key", "2048"], stderr=subprocess.DEVNULL)
    run([
        "openssl", "req", "-x509", "-new", "-key", "/tmp/c6-dummy.key",
        "-subj", "/CN=dummy-overwrite", "-days", "1",
        "-out", "/tmp/c6-dummy-cert.pem",
    ], stderr=subprocess.DEVNULL)
    piv_write_cert_9c(fac, "/tmp/c6-dummy-cert.pem")
    time.sleep(1)

    piv_selfsign_9c(CERT_CN, CERT_DAYS, pins_nk1["ppin"], str(cert_pem))
    if not cert_pem.exists() or cert_pem.stat().st_size == 0:
        fatal("C6 selfsign produced no cert")
    ok(f"Vendor cert: {cert_pem.stat().st_size} bytes")

    step("Overwrite dummy with real vendor cert")
    piv_write_cert_9c(fac, str(cert_pem))

    shutil.copy(cert_pem, DRIVE3 / "intergenos-vendor-cert.pem")
    cap(["sync"])
    cap(["shred", "-uvz", "/tmp/c6-dummy.key", "/tmp/c6-dummy-cert.pem"])

    step("Rotate PIV mgmt key (factory -> random AES-256)")
    new_mgm_bytes = os.urandom(32)
    new_mgm = new_mgm_bytes.hex()
    new_mgm_col = ":".join(new_mgm[i:i+2] for i in range(0, len(new_mgm), 2))
    piv_change_mgm_key(fac, new_mgm_col)

    # Verify new mgmt key works
    new_file = "/tmp/c6-new.key"
    Path(new_file).write_text(new_mgm_col + "\n")
    env = os.environ.copy()
    env["PIV_EXT_AUTH_KEY"] = new_file
    cap(["piv-tool", "-A", "M:9B:0C"], env=env)

    cert_sha = hashlib.sha256(cert_pem.read_bytes()).hexdigest()
    log = CEREMONY / "identity-log.md"
    with log.open("a") as f:
        f.write(
            f"## C6 — PIV slot 9c (NK#1)\n"
            f"**Captured:** {datetime.now(timezone.utc).isoformat()}\n"
            f"**CN:** {CERT_CN}\n"
            f"**Validity:** {CERT_DAYS} days\n"
            f"**Vendor cert SHA-256:** `{cert_sha}`\n"
            f"**NEW PIV mgmt key (AES-256):** `{new_mgm}`\n\n---\n\n"
        )

    cap(["shred", "-uvz", fac, new_file])

    record_paper(
        f"PIV vendor cert + mgmt key (record BOTH on paper):\n"
        f"  Vendor cert SHA-256:  {cert_sha}\n"
        f"  NEW PIV mgmt key:     {new_mgm}\n"
        f"This mgmt key is the ONLY way to modify slot 9c. Without it, slot 9c is read-only "
        f"(signing still works via PIV User PIN)."
    )

    step("C6 complete. UNPLUG NK#1.")
    ask_enter("Press Enter when unplugged")


def stage_handoff_pack(master_fp):
    """Build a 'handoff-pack' on Drive #3 — pubkey + README — for shipping with NK#3 + NK#4.

    Goes to Drive #3 (alongside identity-log.md). After ceremony, the primary
    maintainer copies the handoff-pack/ directory from Drive #3 to a fresh USB
    and ships that USB + NK#3 + NK#4 + the secondary maintainer's PIN paper
    (separate channel, defense-in-depth) to the secondary maintainer.

    Contains: master pubkey + README explaining the signing workflow, prerequisites,
    troubleshooting, and what the secondary maintainer does NOT have (master pass
    / LUKS / paperkey). No secrets — safe to ship via standard postal channels.
    """
    banner("Stage 9.5 — Build handoff-pack")
    pack_dir = DRIVE3 / "handoff-pack"
    pack_dir.mkdir(exist_ok=True)

    # Master pubkey
    pubkey_src = DRIVE3 / "intergenos-release-key.asc"
    if not pubkey_src.exists():
        # Stage 8 should have placed it; if not, something earlier went wrong
        fatal(f"Expected pubkey at {pubkey_src} for handoff-pack — missing")
    shutil.copy(pubkey_src, pack_dir / "intergenos-release-key.asc")

    # README — substituted with the actual master fingerprint at runtime
    readme = f"""# InterGenOS Release Signing — Notes for the Secondary Maintainer

You're holding two Nitrokey 3 hardware tokens (NK#3 and NK#4) and a paper with
PINs. These are signing keys for the InterGenOS Linux distribution. Each token
holds one working signing subkey of the InterGenOS master keypair. You sign
release files with them; end-users verify against the master public key (which is
published on the InterGenOS distribution mirrors).

## What's in this package

- `intergenos-release-key.asc` — the master public key (no secrets — safe to share).
- `README-for-secondary-maintainer.md` — this file.
- (mailed separately as paper) Your PIN ledger: User PIN, Admin PIN, PIV PIN, and PUK
  for each NK. **Do not store these digitally.**

## What you do NOT have (intentional)

- The master passphrase
- The LUKS USB backup of the master secret
- The paperkey

These are emergency-recovery materials held by the primary maintainer. They are
released to you only under the conditions of the InterGenOS succession plan.
Under normal operations, you only need your two NKs and your paper PINs.

## Master fingerprint

```
{master_fp}
```

Verify any imported public key matches this fingerprint before trusting it. The
fingerprint is also published at the InterGenOS public signing-key page.

## Software prerequisites

On any modern Linux:

- gnupg 2.x
- pcscd (running)
- scdaemon (ships with gnupg)

Debian/Ubuntu:
```
sudo apt install gnupg pcscd scdaemon
```

Fedora/RHEL/openSUSE:
```
sudo dnf install gnupg2 pcsc-lite ccid     # or zypper / pacman equivalent
```

macOS:
```
brew install gnupg
# pcscd ships with macOS via the SmartCard.framework
```

Verify pcscd is alive:
```
systemctl is-active pcscd      # Linux — should say 'active'
```

## One-time setup

1. Import the master public key:
   ```
   gpg --import intergenos-release-key.asc
   ```

2. Verify the fingerprint matches what's on this README and what's on the
   InterGenOS public signing-key page:
   ```
   gpg --fingerprint {master_fp}
   ```

3. Mark the key as ultimately trusted so gpg accepts your signing operations:
   ```
   gpg --edit-key {master_fp}
   gpg> trust
   Your decision? 5
   Do you really want to set this key to ultimate trust? y
   gpg> save
   ```

4. (Optional, only if other smartcard software is on this host.) Configure
   scdaemon for shared PCSC access:
   ```
   mkdir -p ~/.gnupg && chmod 700 ~/.gnupg
   echo "pcsc-shared" > ~/.gnupg/scdaemon.conf
   chmod 600 ~/.gnupg/scdaemon.conf
   gpgconf --kill scdaemon
   ```

## Signing a release file

1. Plug ONE of your NKs (NK#3 or NK#4 — either works, they hold different subkeys
   ([S3] and [S4]) of the same master). Plug only one at a time.

2. Confirm gpg sees the card:
   ```
   gpg --card-status
   ```
   You should see `Application type: OpenPGP`, the card's serial number, and a
   `Signature key` field showing a fingerprint. That fingerprint is the subkey
   on this card.

3. Sign the file:
   ```
   gpg --detach-sign release.tar.gz
   ```
   gpg prompts for the User PIN. Enter the value from your paper records (UPIN[3]
   if NK#3 is plugged, UPIN[4] if NK#4 is plugged). The NK's LED will blink — touch
   it to confirm the signature (the signing slot has UIF=on, requiring physical
   touch per signature).

4. Result: `release.tar.gz.sig` written alongside the input.

## Verifying a signature

```
gpg --verify release.tar.gz.sig release.tar.gz
```

Expected output includes `Good signature from "InterGenOS Project Signing Key
(primary) <intergenos-primary@intergenstudios.com>"`.

## Troubleshooting

### "Bad PIN" — three wrong attempts blocks the User PIN

Unblock with the Admin PIN:
```
gpg --card-edit
gpg/card> admin
gpg/card> passwd
Your selection? 2
(enter Admin PIN, then a new User PIN twice)
gpg/card> quit
```

### Card not detected

- `systemctl is-active pcscd` — should be 'active'. If not: `sudo systemctl start pcscd`
- Unplug NK, wait 5 seconds, replug.
- `lsusb | grep 20a0` — if the product ID is `20a0:42dd` the NK is in bootloader
  mode. Unplug for 30 seconds, replug. Should auto-recover to `42b1` or `42b2`.

### gpg picks the wrong subkey

If both NK#3 and NK#4 are plugged simultaneously, gpg may pick either subkey
ambiguously. Plug ONLY ONE at signing time.

To force a specific subkey, use:
```
gpg --detach-sign -u '<subkey-fp>!' release.tar.gz
```
The exact fingerprint of [S3] and [S4] is shown in `gpg --card-status` when
each card is plugged. The trailing `!` tells gpg to use exactly that subkey.

### Both User PIN and Admin PIN blocked

OpenPGP doesn't have a fallback past Admin PIN. If both are blocked, the card's
on-card subkey is recoverable only by factory-reset (which destroys the subkey
on the card). Contact the primary maintainer to provision a fresh subkey from
the master keypair.

## Distinguishing your signatures from the primary maintainer's

Cryptographically your signatures are equivalent to the primary maintainer's —
both verify against the same master public key. Verifiers can tell who signed
by inspecting the subkey ID in the signature packet:

| Subkey | Card | Holder |
|---|---|---|
| [S1] | NK#1 | Primary maintainer |
| [S2] | NK#2 | Primary maintainer |
| [S3] | NK#3 | Secondary maintainer (you) |
| [S4] | NK#4 | Secondary maintainer (you) |

```
gpg --list-packets release.tar.gz.sig | grep keyid
```

For routine release distribution, the distinction doesn't matter — verifiers just
confirm "signed by InterGenOS master". For audit trails, the subkey ID identifies
the human who pressed touch.

## Storage and operational hygiene

- Keep both NKs in their pouches when not in use.
- Don't store PIN paper in the same package as the NKs (compartmentalize).
- If an NK is lost or stolen: contact the primary maintainer. The master can
  revoke the corresponding subkey, then issue a replacement.
- Re-key cycle: subkeys are valid 2 years. The primary maintainer will run a
  re-roll ceremony before expiry and ship you new NKs. Old NKs will be revoked.

---

Ceremony date: {datetime.now(timezone.utc).strftime("%Y-%m-%d")}
Ceremony automation: InterGenOS ceremony.py
"""
    (pack_dir / "README-for-secondary-maintainer.md").write_text(readme)

    # Small convenience wrapper for signing
    sign_sh = """#!/bin/bash
# sign.sh — convenience wrapper. Plug one NK first, then: bash sign.sh <file>
set -e
[ $# -eq 1 ] || { echo "Usage: $0 <file-to-sign>"; exit 2; }
echo "Confirming card is detected..."
gpg --card-status | grep -E "Application type|Signature key|Serial number" || {
    echo "ERROR: no card detected. Plug NK#3 or NK#4."
    exit 1
}
echo
echo "Signing $1 — enter User PIN when prompted, then TOUCH the NK when it blinks."
gpg --detach-sign "$1"
echo "Done: $1.sig"
"""
    (pack_dir / "sign.sh").write_text(sign_sh)
    (pack_dir / "sign.sh").chmod(0o755)

    verify_sh = """#!/bin/bash
# verify.sh — verify a signed file against the imported master pubkey.
set -e
[ $# -eq 1 ] || { echo "Usage: $0 <file>"; echo "(reads <file>.sig automatically)"; exit 2; }
gpg --verify "$1.sig" "$1"
"""
    (pack_dir / "verify.sh").write_text(verify_sh)
    (pack_dir / "verify.sh").chmod(0o755)

    cap(["sync"])
    ok(f"handoff-pack assembled at {pack_dir}")
    info(f"  contents: {sorted(p.name for p in pack_dir.iterdir())}")
    info("  After ceremony: copy handoff-pack/ from Drive #3 to a fresh USB,")
    info("  ship that USB + NK#3 + NK#4 + the secondary maintainer's PIN paper")
    info("  (separate channel) to the secondary maintainer.")


def stage_wrap(master_fp):
    banner("Stage 10 — Wrap-up")
    log = CEREMONY / "identity-log.md"
    shutil.copy(log, DRIVE3 / "identity-log.md")
    cap(["sync"])
    info(f"Identity log saved to Drive #3: {DRIVE3 / 'identity-log.md'}")
    info(f"Drive #3 contents: {sorted(p.name for p in DRIVE3.iterdir())}")

    info("Ejecting Drive #3...")
    cap(["sync"])
    cap(["udisksctl", "unmount", "-b", "/dev/" + cap(["findmnt", "-no", "SOURCE", str(DRIVE3)]).stdout.strip().split("/")[-1]])
    # Best-effort umount fallback
    cap(["sudo", "-n", "umount", str(DRIVE3)])

    banner("CEREMONY COMPLETE")
    info(f"Master fingerprint: {master_fp}")
    info(f"Identity log:       {log}")
    info("Reboot Tails to wipe master secret from RAM.")


# ============================================================================
# Main
# ============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="InterGenOS v2 Signing Ceremony",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Stages:\n"
            "  1 = collect secrets (master pass + LUKS pass)\n"
            "  3 = Phase 0 per-NK (factory-reset, PIN rotation, URL, PIV dry-run on NK#4)\n"
            "  4 = master keypair generation\n"
            "  5 = paperkey export + Drive #2 transit\n"
            "  6 = LUKS backup to Drive #3\n"
            "  7 = keytocard sign-subkeys to all 4 NKs\n"
            "  8 = pubkey export + LUKS update\n"
            "  9 = C6 PIV vendor cert on NK#1\n"
            " 10 = wrap (identity log + handoff-pack + eject Drive #3)\n"
            "\n"
            "Resume mode (--from-stage > 1): requires values.txt populated AND existing\n"
            "master keyring at ~/ceremony/gnupg-master (Tails not rebooted since previous\n"
            "run). Stages 1-(N-1) are skipped; values reconstructed from values.txt and\n"
            "existing keyring.\n"
        ),
    )
    parser.add_argument(
        "--from-stage", type=int, default=1, metavar="N",
        help="Resume from stage N (default: 1 = full run).",
    )
    args = parser.parse_args()

    CEREMONY.mkdir(exist_ok=True)

    # Initialize trace log for this run
    global TRACE_LOG_PATH
    TRACE_LOG_PATH = CEREMONY / f"trace-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.log"
    TRACE_LOG_PATH.write_text(
        f"=== InterGenOS Ceremony Trace ===\n"
        f"started: {datetime.now(timezone.utc).isoformat()}\n"
        f"from_stage: {args.from_stage}\n"
        f"=================================\n\n"
    )

    banner(f"InterGenOS v2 Signing Ceremony (ceremony.py){'' if args.from_stage <= 1 else f' — RESUME from stage {args.from_stage}'}")
    print(f"Trace log: {TRACE_LOG_PATH}")
    print(f"scdaemon log: /tmp/scdaemon-ceremony.log")
    print(f"gpg-agent log: /tmp/gpg-agent-ceremony.log")
    print()
    print("Hardcoded values:")
    print(f"  Master UID:    {NAME} ({COMMENT}) <{EMAIL}>")
    print(f"  Master expiry: {EXPIRY} (does not expire)")
    print(f"  PIV Cert CN:   {CERT_CN}")
    print(f"  PIV Validity:  {CERT_DAYS} days")
    print()
    # values.txt detection
    vals = load_values()
    if vals:
        non_empty = [k for k, v in vals.items() if v]
        print(f"values.txt detected: {VALUES_PATH}")
        print(f"  {len(non_empty)} non-empty values loaded — interactive prompts skipped for those.")
        print(f"  REMEMBER: delete this file after ceremony completes (contains every secret in plaintext).")
        print()
    else:
        print("(no values.txt found — all secrets entered interactively)")
        print()
    if args.from_stage <= 1:
        print("You will be asked to record on paper:")
        print("  - Master passphrase (24+ chars)")
        print("  - LUKS passphrase (24+ chars, different from master)")
        print("  - 4 NK PINs each (User, Admin, PIV PIN, PIV PUK) x 4 NKs = 16 PINs total")
        print("  - Master fingerprint (40 hex chars) generated by Stage 4")
        print("  - PIV vendor cert SHA-256 + new PIV mgmt key (Stage 9)")
    else:
        print(f"RESUME MODE: skipping stages 1-{args.from_stage - 1}.")
        print(f"  master_pass, luks_pass: read from values.txt")
        print(f"  per-NK PINs: read from values.txt (no Phase 0 re-run)")
        print(f"  master keypair: re-loaded from existing keyring at {CEREMONY / 'gnupg-master'}")
        print(f"  Stages {args.from_stage} onward will run normally.")
    print()
    ask_enter("Press Enter to begin")

    # Pre-flight + bundle install always run (idempotent — fast on subsequent runs)
    stage_pre_flight()
    stage_install_debs()

    # ---- Stage 1: collect secrets (or read from values.txt on resume) ----
    if args.from_stage <= 1:
        master_pass, luks_pass = stage_collect_secrets()
    else:
        master_pass = get_value("master_pass")
        luks_pass = get_value("luks_pass")
        if not master_pass:
            fatal(f"--from-stage > 1 requires master_pass in values.txt ({VALUES_PATH})")
        if not luks_pass:
            fatal(f"--from-stage > 1 requires luks_pass in values.txt ({VALUES_PATH})")
        if master_pass == luks_pass:
            fatal("master_pass and luks_pass must differ")
        info(f"Using values.txt: master_pass (length {len(master_pass)}), luks_pass (length {len(luks_pass)})")

    # Initialize identity log only on full run; resume mode appends to existing
    log = CEREMONY / "identity-log.md"
    if args.from_stage <= 1 or not log.exists():
        log.write_text(
            f"# InterGenOS v2 Signing Ceremony — Identity Log\n"
            f"**Date:** {datetime.now(timezone.utc).isoformat()}\n"
            f"**Tails:** {Path('/etc/debian_version').read_text().strip() if Path('/etc/debian_version').exists() else 'unknown'}\n\n"
        )

    # ---- Stage 3: Phase 0 per NK (or reconstruct pins dict from values.txt) ----
    pins = {}
    if args.from_stage <= 3:
        pins[4] = stage_phase_0_one_card(4, do_piv_dryrun=True)
        pins[1] = stage_phase_0_one_card(1)
        pins[2] = stage_phase_0_one_card(2)
        pins[3] = stage_phase_0_one_card(3)
    else:
        for n in [4, 1, 2, 3]:
            pins[n] = {
                "upin": get_value(f"nk{n}_upin"),
                "apin": get_value(f"nk{n}_apin"),
                "ppin": get_value(f"nk{n}_ppin"),
                "puk": get_value(f"nk{n}_puk"),
                "serial": "?",  # not validated on resume
            }
            for k in ("upin", "apin", "ppin", "puk"):
                if not pins[n][k]:
                    fatal(f"--from-stage > 3 requires nk{n}_{k} in values.txt")
        info("Reconstructed all 4 NK PIN dicts from values.txt (no Phase 0 re-run)")

    # ---- Stage 4: master keypair (or reload existing) ----
    if args.from_stage <= 4:
        master_fp, env = stage_master_keypair(master_pass)
    else:
        gnupghome = CEREMONY / "gnupg-master"
        if not gnupghome.is_dir():
            fatal(
                f"--from-stage > 4 requires existing master keyring at {gnupghome}.\n"
                f"This directory is in RAM only (Tails amnesic). If Tails has rebooted\n"
                f"since the previous ceremony.py run, the master keyring is gone — you\n"
                f"must restart from --from-stage 1 (full run). To restore from LUKS\n"
                f"backup instead, that's a separate procedure not yet automated."
            )
        # CRITICAL: set GNUPGHOME in os.environ (not just the local env dict) so
        # ALL subprocess calls — including gpgconf --kill scdaemon, gpg --card-edit
        # spawned by pexpect, and any unscoped cap() call — use the ceremony's
        # keyring + its scdaemon socket. Otherwise gpgconf --kill kills the
        # default ~/.gnupg scdaemon while the ceremony's scdaemon stays stuck,
        # causing every blast to see "No such device" indefinitely.
        os.environ["GNUPGHOME"] = str(gnupghome)
        env = os.environ.copy()
        # Ensure ceremony's GNUPGHOME has scdaemon.conf + gpg-agent.conf with
        # disable-ccid + pcsc-shared. Without these, scdaemon for THIS keyring
        # defaults to direct CCID mode, conflicts with pcscd holding the readers,
        # and every card op reports "No such device". Idempotent — safe to call
        # whether the configs are already present or not.
        write_gpg_configs_to(gnupghome)
        # Kill any scdaemon that was running with the WRONG (default ~/.gnupg)
        # config so the next card op spawns a fresh one using the right config.
        cap(["gpgconf", "--kill", "scdaemon"])
        time.sleep(1)
        r = cap(["gpg", "--list-keys", "--with-colons", EMAIL], env=env)
        master_fp = None
        for ln in r.stdout.splitlines():
            if ln.startswith("fpr:"):
                master_fp = ln.split(":")[9]
                break
        if not master_fp:
            fatal(f"Could not find master fingerprint in existing keyring at {gnupghome}")
        ok(f"Resumed master keypair: {master_fp}")
        info(f"Compare to your paper record — they must match. Ctrl+C now if they don't.")

    # ---- Stage 5: paperkey ----
    if args.from_stage <= 5:
        stage_paperkey(master_fp, master_pass, env)

    # ---- Stage 6: LUKS backup ----
    if args.from_stage <= 6:
        stage_luks_backup(master_fp, master_pass, luks_pass, env)

    # ---- Stage 7: keytocard for all 4 NKs ----
    if args.from_stage <= 7:
        # NOTE: cleanup_orphan_sign_subkeys() removed here — its detection logic
        # treats on-card stubs (which appear as `ssb:` not `ssb#:` in modern gpg's
        # --with-colons output) as orphans and deletes them, breaking resume.
        # Each stage_keytocard_one() now decides per-card whether to skip (card
        # already has a sig key matching a master-keyring stub) or to addkey +
        # keytocard (overwriting any prior card-side key). End state is consistent
        # whether resuming from a partial prior run or running fresh.
        stage_keytocard_one(1, master_fp, master_pass, pins[1], env)
        stage_keytocard_one(2, master_fp, master_pass, pins[2], env)
        stage_keytocard_one(3, master_fp, master_pass, pins[3], env)
        stage_keytocard_one(4, master_fp, master_pass, pins[4], env)

    # ---- Stage 8: pubkey export + LUKS update ----
    if args.from_stage <= 8:
        stage_pubkey_export(master_fp, luks_pass, env)

    # ---- Stage 9: C6 PIV vendor cert on NK#1 + handoff-pack ----
    if args.from_stage <= 9:
        stage_c6_piv(pins[1])
        stage_handoff_pack(master_fp)

    # ---- Stage 10: wrap ----
    if args.from_stage <= 10:
        stage_wrap(master_fp)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n!!  Interrupted by user. Master keyring may still be in RAM at "
              f"{CEREMONY}/gnupg-master. Reboot Tails to wipe.\n")
        sys.exit(130)
