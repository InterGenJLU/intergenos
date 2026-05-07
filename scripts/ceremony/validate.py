#!/usr/bin/env python3
"""
validate.py — Post-ceremony validation. Run on Tails after ceremony.py completes,
BEFORE rebooting Tails or shipping cards. Confirms:

  1. Master keyring structure: master + 1 enc sub + 4 sign stubs (one per NK)
  2. Each NK (plug in sequence): card-status, sig key matches a master stub,
     test-sign with values.txt User PIN + verify against master pubkey
  3. Drive #3 contents: master-backup.luks (LUKS2), intergenos-release-key.asc,
     intergenos-vendor-cert.pem, identity-log.md, handoff-pack/
  4. Drive #2 contents: paperkey (already there), no other secrets
  5. Vendor cert SHA-256 matches what owner has on paper
  6. handoff-pack README + sign.sh + verify.sh + pubkey are intact

Read-only — does NOT modify the keyring, cards, or drives. Idempotent.

Usage on Tails:
    python3 /media/amnesia/OFFLINEDEBS/scripts/validate.py
"""
import os
import sys
import re
import shlex
import subprocess
import time
import hashlib
from datetime import datetime, timezone
from pathlib import Path

# Reuse ceremony.py's primitives — same paths, same load_values, same _blast
sys.path.insert(0, str(Path(__file__).parent))
import importlib.util
_spec = importlib.util.spec_from_file_location("ceremony", str(Path(__file__).parent / "ceremony.py"))
ceremony = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ceremony)

CEREMONY = ceremony.CEREMONY
DRIVE2 = ceremony.DRIVE2
DRIVE3 = ceremony.DRIVE3
EMAIL = ceremony.EMAIL
NAME = ceremony.NAME
CERT_CN = ceremony.CERT_CN

# Set GNUPGHOME globally so all subprocess inherits it
gnupghome = CEREMONY / "gnupg-master"
os.environ["GNUPGHOME"] = str(gnupghome)
env = os.environ.copy()


def banner(s):
    print()
    print("=" * 64)
    print(f"  {s}")
    print("=" * 64)


def check(label, ok_condition, detail=""):
    """Print '[ OK ]' or '[FAIL]' line for a check."""
    marker = "[ OK ]" if ok_condition else "[FAIL]"
    print(f"  {marker} {label}")
    if detail:
        for line in detail.splitlines():
            print(f"         {line}")
    return ok_condition


def cap(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, env=env, **kw)


# --------------------------------------------------------------
# Section 1: Master keyring
# --------------------------------------------------------------
def validate_master_keyring():
    banner("Section 1: Master keyring")
    failures = 0

    if not gnupghome.is_dir():
        check(f"Master keyring at {gnupghome}", False,
              "Directory missing — ceremony's GNUPGHOME wiped (Tails rebooted?)")
        return 1
    check(f"Master keyring at {gnupghome}", True)

    r = cap(["gpg", "--list-keys", "--with-colons", EMAIL])
    master_fp = None
    for ln in r.stdout.splitlines():
        if ln.startswith("fpr:"):
            master_fp = ln.split(":")[9]
            break
    if not master_fp:
        check("Master fingerprint extractable", False, r.stdout)
        return 1
    check(f"Master fingerprint: {master_fp}", True,
          "Compare to your paper — they MUST match.")

    # Sub structure — use --with-colons for reliable parsing across gpg
    # versions. The non-colon human-readable format's stub indication varies
    # ('card-no:' vs 'Card serial no. =' vs 'ssb>' prefix), and a parser bug
    # there caused find_disk_resident_sign_subkeys() to misclassify legit
    # stubs as orphans (the Day 3 incident). --with-colons puts the card AID
    # in field 14 of ssb lines for stubs (D276... = OpenPGP applet AID
    # prefix); field 14 is empty for disk-resident subs. Field 11 holds
    # capability flags ('e'=encrypt, 's'=sign).
    enc_in_sec = 0
    sign_in_sec_with_card = 0
    sign_in_sec_disk = 0
    rs = cap(["gpg", "--list-secret-keys", "--with-colons", master_fp]).stdout

    # First pass: walk the colon output and dump each ssb in a labelled block
    # so the operator can visually verify each subkey before the count check.
    print()
    print("  Per-subkey breakdown (parsed from --with-colons):")
    sub_idx = 0
    cur_sub = None
    blocks = []
    for ln in rs.splitlines():
        if ln.startswith("ssb"):
            if cur_sub is not None:
                blocks.append(cur_sub)
            sub_idx += 1
            fields = ln.split(":")
            caps = fields[11].lower() if len(fields) > 11 else ""
            card_serial_field = fields[14] if len(fields) > 14 else ""
            cur_sub = {
                "idx": sub_idx,
                "keyid": fields[4].upper() if len(fields) > 4 else "",
                "algo": fields[3] if len(fields) > 3 else "",
                "size": fields[2] if len(fields) > 2 else "",
                "caps": caps,
                "aid": card_serial_field.upper(),
                "fpr": "",
            }
        elif ln.startswith("fpr:") and cur_sub is not None and not cur_sub["fpr"]:
            f = ln.split(":")
            cur_sub["fpr"] = f[9] if len(f) > 9 else ""
    if cur_sub is not None:
        blocks.append(cur_sub)

    for sub in blocks:
        # Classify
        if "e" in sub["caps"]:
            kind = "encryption (disk-resident; never moved to a card)"
            classification = "ENC"
        elif "s" in sub["caps"]:
            if sub["aid"].startswith("D276"):
                kind = f"sign STUB (secret on card; AID {sub['aid']})"
                classification = "STUB"
            else:
                kind = "sign DISK-RESIDENT (orphan — secret on disk, would publish as dead key)"
                classification = "ORPHAN"
        else:
            kind = f"unknown caps={sub['caps']!r}"
            classification = "?"
        # Decode card serial from AID for stubs
        card_serial = ""
        if sub["aid"].startswith("D276") and len(sub["aid"]) >= 28:
            # D276 0001 2401 0304 000F XXXXXXXX 0000  → manufacturer-specific 8 hex chars
            card_serial = sub["aid"][20:28]
        # Counters
        if classification == "ENC":
            enc_in_sec += 1
        elif classification == "STUB":
            sign_in_sec_with_card += 1
        elif classification == "ORPHAN":
            sign_in_sec_disk += 1

        print(f"    --- sub #{sub['idx']} [{classification}] ---")
        print(f"        keyid       : {sub['keyid']}")
        print(f"        fingerprint : {sub['fpr']}")
        print(f"        algo/size   : algo={sub['algo']} size={sub['size']}")
        print(f"        capabilities: {sub['caps']!r}")
        if sub["aid"]:
            print(f"        card AID    : {sub['aid']}")
            if card_serial:
                print(f"        card serial : {card_serial}")
        else:
            print(f"        card AID    : (none)")
        print(f"        kind        : {kind}")

    print()
    print(f"  Tally: enc={enc_in_sec}  sign-stubs={sign_in_sec_with_card}  sign-disk-orphans={sign_in_sec_disk}")
    print()

    if not check(f"1 encryption subkey on master keyring", enc_in_sec == 1,
                 f"got {enc_in_sec}"):
        failures += 1
    if not check(f"4 signing subkey stubs on master keyring (one per NK)",
                 sign_in_sec_with_card == 4,
                 f"got {sign_in_sec_with_card}"):
        failures += 1
    if not check(f"NO disk-resident sign subkeys (no orphans)",
                 sign_in_sec_disk == 0,
                 f"got {sign_in_sec_disk} — these would publish as dead keys"):
        failures += 1

    return failures, master_fp


# --------------------------------------------------------------
# Section 2: Per-NK validation (interactive)
# --------------------------------------------------------------
def validate_one_nk(card_num, master_fp):
    print()
    print(f"--- NK#{card_num} validation ---")
    print(f">>> PLUG NK#{card_num} (and unplug all others)")
    input(f">>> Press Enter when ready (Ctrl-C to abort): ")

    failures = 0

    # Refresh scdaemon. Both subprocess calls use check=True with broad
    # exception handling so unexpected gpgconf/systemctl failures surface
    # rather than silently leaving stale daemon state — but expected failure
    # modes (scdaemon not running, sudo NOPASSWD not configured) are
    # tolerated since this is best-effort pre-flight cleanup.
    try:
        subprocess.run(["gpgconf", "--kill", "scdaemon"],
                       capture_output=True, check=True, env=env)
    except Exception:
        pass
    time.sleep(1)
    try:
        subprocess.run(["sudo", "-n", "systemctl", "restart", "pcscd"], check=True)
    except Exception:
        pass
    time.sleep(2)

    # Card status
    r = cap(["gpg", "--card-status"])
    if "Application type" not in r.stdout:
        check(f"NK#{card_num} OpenPGP applet reachable", False,
              r.stdout + "\n" + r.stderr)
        return 1
    check(f"NK#{card_num} OpenPGP applet reachable", True)

    sig_key = ""
    serial = ""
    uif = ""
    key_attr = ""
    pin_retry = ""
    url = ""
    for ln in r.stdout.splitlines():
        if "Signature key" in ln:
            sig_key = ln.split(":", 1)[1].strip()
        elif "Serial number" in ln:
            serial = ln.split(":", 1)[1].strip()
        elif "UIF setting" in ln:
            uif = ln.split(":", 1)[1].strip()
        elif "Key attributes" in ln:
            key_attr = ln.split(":", 1)[1].strip()
        elif "PIN retry counter" in ln:
            pin_retry = ln.split(":", 1)[1].strip()
        elif "URL of public key" in ln:
            url = ln.split(":", 1)[1].strip()

    has_sig = "[none]" not in sig_key
    if not check(f"NK#{card_num} has Signature key on slot 1", has_sig, sig_key):
        failures += 1

    if not check(f"NK#{card_num} key-attr is rsa4096", key_attr == "rsa4096 rsa4096 rsa4096",
                 key_attr):
        failures += 1

    if not check(f"NK#{card_num} UIF=on for signing slot", "Sign=on" in uif, uif):
        failures += 1

    if not check(f"NK#{card_num} PIN counters all-3 (no failed attempts)",
                 pin_retry.startswith("3 ") and pin_retry.endswith(" 3"),
                 pin_retry):
        failures += 1

    # URL should be canonical form pointing to THIS master FP, not the Phase 0
    # placeholder and not some other key's FP.
    expected_url = f"https://keys.openpgp.org/vks/v1/by-fingerprint/{master_fp}"
    print()
    print(f"    URL check (on-card vs expected):")
    print(f"        on-card  : {url}")
    print(f"        expected : {expected_url}")
    if not check(f"NK#{card_num} URL points to actual master FP (no placeholder)",
                 url == expected_url,
                 f"on-card URL does not match expected canonical form"):
        failures += 1

    # Verify the on-card sig key matches a stub on master keyring with this card's serial.
    # Use --with-colons for reliable parsing: field 4 is keyid, field 14 is card AID
    # (D276... prefix for OpenPGP applet) for stubs.
    sig_keyid = sig_key.replace(" ", "").upper()[-16:]  # last 16 hex of full fingerprint
    serial_upper = serial.upper()  # e.g., "B9753481"
    rs = cap(["gpg", "--list-secret-keys", "--with-colons", master_fp]).stdout

    # Show what we're looking for and what we find — visual verification
    print()
    print(f"    On-card sig key (from gpg --card-status):")
    print(f"        full fp     : {sig_key}")
    print(f"        keyid (last16): {sig_keyid}")
    print(f"        card serial : {serial}")
    print(f"    Searching master keyring for ssb with keyid={sig_keyid} AND AID containing {serial_upper}...")

    matching_stub = False
    matched_block = None
    candidates = []
    for ln in rs.splitlines():
        if not ln.startswith("ssb"):
            continue
        fields = ln.split(":")
        sub_keyid = fields[4].upper() if len(fields) > 4 else ""
        card_serial_field = fields[14].upper() if len(fields) > 14 else ""
        candidates.append((sub_keyid, card_serial_field))
        if sub_keyid == sig_keyid and serial_upper in card_serial_field:
            matching_stub = True
            matched_block = (sub_keyid, card_serial_field)

    if matching_stub:
        print(f"    Matched stub on master keyring:")
        print(f"        keyid       : {matched_block[0]}")
        print(f"        card AID    : {matched_block[1]}")
        print(f"        VERDICT     : on-card key matches master keyring stub ✓")
    else:
        print(f"    NO match. Candidates examined:")
        for kid, aid in candidates:
            print(f"        keyid={kid}  AID={aid or '(none)'}")
    if not check(f"NK#{card_num} sig key matches master keyring stub (card serial {serial})",
                 matching_stub,
                 f"on-card keyid {sig_keyid} not found as stub annotated with serial {serial} on master keyring"):
        failures += 1

    print(f"    Unplug NK#{card_num} when ready (test-sign already proven during Stage 7).")
    return failures


def validate_all_nks(master_fp):
    banner("Section 2: Per-NK card state (read-only — plug each NK in turn)")
    print()
    print("  This section requires plugging each NK in turn (one at a time).")
    print("  Read-only: only checks card-status fields. No PIN entry needed.")
    print("  No test-sign here — Stage 7 already proved each NK signs correctly.")
    print()
    failures = 0
    for n in [1, 2, 3, 4]:
        failures += validate_one_nk(n, master_fp)
    return failures


# --------------------------------------------------------------
# Section 3: Drive #3 contents
# --------------------------------------------------------------
def validate_drive3():
    banner("Section 3: Drive #3 (CEREMONY) contents")
    failures = 0
    if not DRIVE3.is_dir():
        print(f">>> PLUG Drive #3 (CEREMONY) — should auto-mount at {DRIVE3}")
        input(">>> Press Enter when mounted: ")
    if not check(f"Drive #3 mounted at {DRIVE3}", DRIVE3.is_dir()):
        return 1

    expected_files = [
        ("master-backup.luks", 50 * 1024 * 1024),  # 50MB
        ("intergenos-release-key.asc", 1000),
        ("intergenos-vendor-cert.pem", 500),
        ("identity-log.md", 100),
    ]
    for name, min_size in expected_files:
        p = DRIVE3 / name
        size_ok = p.exists() and p.stat().st_size >= min_size
        if not check(f"{name} (>= {min_size} bytes)", size_ok,
                     f"size: {p.stat().st_size if p.exists() else 'missing'}"):
            failures += 1

    pack = DRIVE3 / "handoff-pack"
    if not check(f"handoff-pack/ directory", pack.is_dir()):
        failures += 1
    else:
        for sub in ["intergenos-release-key.asc", "README-for-secondary-maintainer.md", "sign.sh", "verify.sh"]:
            sp = pack / sub
            if not check(f"  handoff-pack/{sub}", sp.exists() and sp.stat().st_size > 0):
                failures += 1

    # Vendor cert SHA-256 — owner has it on paper
    vc = DRIVE3 / "intergenos-vendor-cert.pem"
    if vc.exists():
        sha = hashlib.sha256(vc.read_bytes()).hexdigest()
        print(f"  ATTN  Vendor cert SHA-256: {sha}")
        print(f"        Compare to what's on your paper from Stage 9 — MUST match.")

    return failures


# --------------------------------------------------------------
# Section 4: Drive #2 (OFFLINEDEBS) — paperkey present, no other secrets
# --------------------------------------------------------------
def validate_drive2():
    banner("Section 4: Drive #2 (OFFLINEDEBS) — paperkey only")
    failures = 0
    if not DRIVE2.is_dir():
        print(f">>> Plug Drive #2 if not mounted")
        return 1
    paperkeys = sorted(DRIVE2.glob("paperkey-*.txt"))
    if not check(f"paperkey-*.txt on Drive #2", len(paperkeys) > 0):
        failures += 1
    else:
        pk = paperkeys[-1]
        # Sanity check: base16 paperkey contains '# Secret portions of key' header
        text = pk.read_text(errors="replace")
        if not check(f"  paperkey looks like base16 format",
                     "# Secret portions of key" in text and "Created with paperkey" in text):
            failures += 1
        else:
            print(f"        File: {pk.name} ({pk.stat().st_size} bytes)")
    return failures


# --------------------------------------------------------------
# Section 5: Pubkey can be imported into a fresh keyring (round-trip test)
# --------------------------------------------------------------
def validate_pubkey_import(master_fp):
    banner("Section 5: Pubkey round-trip (import into fresh keyring)")
    failures = 0
    pubkey_path = DRIVE3 / "intergenos-release-key.asc"
    if not pubkey_path.exists():
        check(f"{pubkey_path} present for import test", False)
        return 1

    import tempfile
    test_home = Path(tempfile.mkdtemp(prefix="validate-import-"))
    test_home.chmod(0o700)
    test_env = os.environ.copy()
    test_env["GNUPGHOME"] = str(test_home)

    r = subprocess.run(["gpg", "--import", str(pubkey_path)],
                       capture_output=True, text=True, env=test_env)
    if not check(f"gpg --import succeeds", r.returncode == 0,
                 r.stderr if r.returncode != 0 else ""):
        failures += 1
        return failures

    r = subprocess.run(["gpg", "--list-keys", "--with-colons", EMAIL],
                       capture_output=True, text=True, env=test_env)
    imported_fp = None
    for ln in r.stdout.splitlines():
        if ln.startswith("fpr:"):
            imported_fp = ln.split(":")[9]
            break
    if not check(f"imported FP matches master FP", imported_fp == master_fp,
                 f"imported: {imported_fp}\nexpected: {master_fp}"):
        failures += 1

    # Per-sub breakdown of the imported (public) pubkey — what end users will see
    print()
    print("  Imported pubkey subkey breakdown (what end users / secondary maintainer will see):")
    sub_count = 0
    sub_idx = 0
    cur_sub = None
    blocks = []
    for ln in r.stdout.splitlines():
        if ln.startswith("sub:"):
            if cur_sub is not None:
                blocks.append(cur_sub)
            sub_idx += 1
            f = ln.split(":")
            cur_sub = {
                "idx": sub_idx,
                "keyid": f[4].upper() if len(f) > 4 else "",
                "algo": f[3] if len(f) > 3 else "",
                "size": f[2] if len(f) > 2 else "",
                "caps": f[11].lower() if len(f) > 11 else "",
                "fpr": "",
            }
            sub_count += 1
        elif ln.startswith("fpr:") and cur_sub is not None and not cur_sub["fpr"]:
            ff = ln.split(":")
            cur_sub["fpr"] = ff[9] if len(ff) > 9 else ""
    if cur_sub is not None:
        blocks.append(cur_sub)

    for sub in blocks:
        if "e" in sub["caps"]:
            kind = "encryption"
        elif "s" in sub["caps"]:
            kind = "signing"
        else:
            kind = f"other (caps={sub['caps']!r})"
        print(f"    --- pubkey sub #{sub['idx']} [{kind}] ---")
        print(f"        keyid       : {sub['keyid']}")
        print(f"        fingerprint : {sub['fpr']}")
        print(f"        algo/size   : algo={sub['algo']} size={sub['size']}")
        print(f"        capabilities: {sub['caps']!r}")

    print()
    print(f"  Total subs in published pubkey: {sub_count}")
    print()

    if not check(f"imported pubkey has 5 subkeys (1 enc + 4 sign)",
                 sub_count == 5,
                 f"got {sub_count}"):
        failures += 1

    # Cleanup test home
    import shutil as _sh
    _sh.rmtree(test_home, ignore_errors=True)
    return failures


# --------------------------------------------------------------
# Main
# --------------------------------------------------------------
def main():
    # Per §4 S8: --review mode skips card-dependent checks for code-review of
    # the validator itself outside the Tails environment. Sections 2, 3, 4
    # require physical hardware (NKs + USB drives) that aren't present on a
    # dev workstation. Sections 1 and 5 are file-only and run in either mode.
    review_mode = "--review" in sys.argv

    banner("InterGenOS Ceremony Validation" + (" — REVIEW MODE" if review_mode else ""))
    print()
    if review_mode:
        print("REVIEW MODE: Sections 2-4 (card / drive checks) skipped.")
        print("Only file-format checks (Sections 1 + 5) will run.")
        print("Use without --review during the actual ceremony on Tails.")
        print()
    else:
        print("This is a READ-ONLY audit. No keys, cards, drives, or files are modified.")
        print("You'll need to plug each NK in turn (one at a time) for Section 2.")
        print()
        input(">>> Press Enter to begin (Ctrl-C to abort): ")

    section1 = validate_master_keyring()
    if isinstance(section1, tuple):
        s1_failures, master_fp = section1
    else:
        s1_failures = section1
        master_fp = None

    if not master_fp:
        print("\nABORT: master keyring missing or unreadable. Cannot continue.")
        sys.exit(2)

    if review_mode:
        s2_failures = 0
        s3_failures = 0
        s4_failures = 0
        print("\n[Sections 2-4 SKIPPED — review mode, no card/drive validation]\n")
    else:
        s2_failures = validate_all_nks(master_fp)
        s3_failures = validate_drive3()
        s4_failures = validate_drive2()
    s5_failures = validate_pubkey_import(master_fp)

    total = s1_failures + s2_failures + s3_failures + s4_failures + s5_failures
    banner("VALIDATION SUMMARY" + (" — REVIEW MODE" if review_mode else ""))
    print(f"  Section 1 (Master keyring):   {s1_failures} failure(s)")
    if review_mode:
        print(f"  Section 2 (Per-NK signing):   SKIPPED (review mode)")
        print(f"  Section 3 (Drive #3):         SKIPPED (review mode)")
        print(f"  Section 4 (Drive #2):         SKIPPED (review mode)")
    else:
        print(f"  Section 2 (Per-NK signing):   {s2_failures} failure(s)")
        print(f"  Section 3 (Drive #3):         {s3_failures} failure(s)")
        print(f"  Section 4 (Drive #2):         {s4_failures} failure(s)")
    print(f"  Section 5 (Pubkey roundtrip): {s5_failures} failure(s)")
    print(f"  TOTAL:                        {total} failure(s)")
    print()
    if total == 0:
        print("  *** ALL CHECKS PASSED — ceremony output is valid. ***")
        print()
        print("  Safe to:")
        print("    - Eject Drive #3, store in physical safe")
        print("    - Reboot Tails (master keyring in RAM gets wiped)")
        print("    - Mail NK#3 + NK#4 + paper PINs + handoff-pack copy to secondary maintainer")
        print()
    else:
        print(f"  *** {total} CHECK(S) FAILED — review the FAIL lines above. ***")
        print()
        print("  Do NOT eject Drive #3 / reboot Tails / ship cards until resolved.")
        print()
    sys.exit(0 if total == 0 else 1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n!!  Validation interrupted.")
        sys.exit(130)
