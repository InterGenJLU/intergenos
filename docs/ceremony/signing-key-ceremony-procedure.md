# InterGenOS Signing-Key Ceremony — Procedure

**Last updated:** 2026-04-30
**Applies to:** the one-time generation of the InterGenOS distro release-signing keys (PGP master + signing subkeys + EFI-binary X.509 PIV slot 9c).
**Operator:** owner / primary maintainer, alone.
**Environment:** Tails 7.7 on the HP laptop, network-off, Nitrokey 3 NFC tokens present.
**Cross-check support:** the live workstation-side cross-check operator is on call throughout the ceremony. The workstation is **not** air-gapped — only the Tails laptop is. The workstation operator reads commands aloud, sanity-checks outputs, and surfaces discrepancies in real time.

This is the operational script. Read each step before you run it. Commands go in code blocks; expected output and failure mitigations follow each one. Air-gap discipline reminders sit inline at the points they matter.

For the design rationale behind the key hierarchy, see [`docs/research/installer/signing_key_custody_2026-04-18.md`](../research/installer/signing_key_custody_2026-04-18.md). For the post-ceremony release-signing runbook, see [`docs/signing-procedure.md`](../signing-procedure.md). For the cross-published fingerprint page, see [`docs/signing-key.md`](../signing-key.md).

---

## Table of Contents

1. [Pre-ceremony checklist](#part-1--pre-ceremony-checklist)
2. [Boot Tails + offline-debs install (C1 / Phase 2 finish)](#part-2--boot-tails--offline-debs-install)
3. [Chapter C2: Master keypair generation + offline backups](#chapter-c2--master-keypair-generation--offline-backups) (includes C4 LUKS USB backup + C5 paperkey)
4. [Chapter C3: PGP signing subkeys to Nitrokey OpenPGP applets](#chapter-c3--pgp-signing-subkeys-to-nitrokey-openpgp-applets)
5. [Chapter C6: PIV slot 9c EFI-binary signing keypair](#chapter-c6--piv-slot-9c-efi-binary-signing-keypair)
6. [Verification + handoff](#part-6--verification--handoff)
7. [Post-ceremony (online side)](#part-7--post-ceremony-online-side)
8. [Recovery branches / known failure modes](#part-8--recovery-branches--known-failure-modes)
9. [Glossary + cross-references](#part-9--glossary--cross-references)

---

## Part 1 — Pre-ceremony checklist

Walk this list once **before** you boot Tails. Every item is either ready or surfaced to the workstation operator for resolution before the air-gap window opens.

### 1.1 Hardware kit

- [ ] **4× SanDisk Ultra 32GB USB drives**, sharpie-labeled #1 / #2 / #3 / #4. Staged by IGOSC on 2026-04-29.
  - Drive #1 = Tails 7.7 boot media (sha256 round-trip verified)
  - Drive #2 = OFFLINEDEBS dual-flavor (`/debian12/` + `/debian13/`)
  - Drive #3 = CEREMONY (fresh FAT32, label `CEREMONY`, UUID `E9B6-B5DC`) — will hold the Output artifacts AND act as LUKS backup #1
  - Drive #4 = sealed cold-spare. **STAYS SEALED** unless catastrophic mid-ceremony failure forces fallback.
- [ ] **4× Nitrokey 3 NFC tokens**, sharpie-labeled or pouch-labeled.
  - #1 = primary daily-driver. Lives with the maintainer post-ceremony.
  - #2 = home-safe backup.
  - #3 = bank safety-deposit-box backup.
  - #4 = spare / test card. PIV slot 9c test-cert dry-run already completed per `docs/signing-procedure.md` Appendix B.
- [ ] **Paper for paperkey backup.** Plain printer paper. One sheet should be enough; have a second on hand.
- [ ] **Pen.** New PINs are written on paper at the time they are set; do not store electronically.
- [ ] **Printer** connected to the workstation (NOT the Tails laptop) and tested. The paperkey output is plain text — print from any computer.
- [ ] **AC power** plugged into the laptop throughout. Battery is power-blip backup only.
- [ ] **Workstation phone** charged. This procedure document open in a browser as offline backup. Cross-check channel open to the workstation operator.

### 1.2 Software pre-flight (already done)

- [x] Tails 7.7 .img sha256 + PGP verified against canonical Tails master fingerprint.
- [x] Drive #1 imaged with Tails, sha256 round-trip on first 2,041,577,472 bytes verified bit-perfect.
- [x] Drive #2 staged with offline-debs. Both `/debian12/` and `/debian13/` SHA256SUMS verified on-disk.
- [x] Drive #3 freshly formatted FAT32 with label `CEREMONY`.
- [x] Phase 1 host evaluation: PASS verdict from the workstation operator on 2026-04-29.
- [ ] **Phase 2 test boot:** _________________________ (fill in PASS / FAIL / N/A before C2 begins). Note Tails-version-detected (`cat /etc/debian_version`) on first boot.

### 1.3 Air-gap environment pre-flight

- [ ] **Bluetooth radio off** on the workstation (firmware-level if possible). Ceremony-laptop Bluetooth will be hard-blocked by F12 once Tails is up.
- [ ] **No other USB devices** plugged into the ceremony laptop except those required by the current step. The laptop's built-in keyboard and trackpad are fine. External Logitech receiver and Dell USB keyboard are explicitly **unplugged** for the ceremony.
- [ ] **Workstation is not on the same LAN** as the ceremony laptop, OR if they are, the ceremony laptop's WiFi is hard-blocked via F12 and Ethernet is unplugged. The Tails laptop never touches the network during the ceremony, regardless of physical connectivity state.
- [ ] **Other workstations / phones / IoT devices** in the room with microphones can stay; ceremony does not involve speaking secrets aloud. The PINs are written, not spoken.
- [ ] **The workstation operator is reachable** for cross-checks. Each chapter's expected outputs are designed to be operator-readable, so the operator can confirm them in real time over whatever cross-check channel you've set up (channel post, voice call, in-person, etc.).

### 1.4 What is being generated today

Per [`signing_key_custody_2026-04-18.md`](../research/installer/signing_key_custody_2026-04-18.md) D1 decisions:

| Key | Purpose | Where it lives | Lifetime |
|---|---|---|---|
| Distro GPG master | Certifies signing subkeys, signs rollover announcements | **Tails RAM only during ceremony**; backed up to paperkey + 2× LUKS USB; **never on a network-connected machine** | 5 years |
| Distro GPG signing subkeys [S1] [S2] [S3] | Sign the pkm repo index + release artifacts | One subkey per Nitrokey OpenPGP applet (#1, #2, #3); touch-required for sign | 2 years |
| EFI-binary X.509 (PIV slot 9c) | Signs kernel `vmlinuz` and custom GRUB | Generated **on the Nitrokey's PIV applet**; private half never leaves the card | 2 years |

Out of scope for this ceremony:
- **Kernel module-signing key** — ephemeral per-build, generated and discarded inside the kernel build. Doesn't touch any of these tokens.
- **Machine Owner Key (MOK)** — generated per-install on the end-user machine by the Forge installer. Not distro-held.

---

## Part 2 — Boot Tails + offline-debs install

This is the Phase 2 test-boot procedure expanded into ceremony-day form. Run it once. If you already completed a Phase 2 test boot on a separate Tails session, you'll repeat these steps for the actual ceremony — the test boot is amnesic, nothing carries over.

### Step 2.1 — Plug Drive #1 only, reboot the laptop

**Air-gap state:** Drives #2, #3, #4 stay out. Nitrokeys all stay out. The ceremony laptop reboots into Tails clean.

1. Plug Drive #1 into a USB-A port directly on the laptop (avoid hubs).
2. **Reboot.** As the HP logo appears, repeatedly tap **F9**.

**Expected:** HP one-time-boot menu appears. The SanDisk USB shows up as a boot option (typically labeled `USB Hard Drive — SanDisk` or similar, depending on BIOS detection).

**Failure modes:**
- *Boot menu doesn't appear.* The F9 tap window is short. Try again — power off, power on, tap F9 immediately.
- *USB doesn't appear as a boot option.* Check BIOS settings: **F10** at boot for BIOS, confirm USB Boot is enabled, confirm Secure Boot is not blocking unsigned media (Tails self-bootloader). If still not visible, the dd-write may be corrupted — re-image Drive #1 from the verified Tails 7.7 `.img` staged on the workstation and retry.

3. Select the USB. Press Enter.

### Step 2.2 — GRUB cmdline edit

**Expected:** Tails GRUB menu appears (purple-and-white Tails branding).

1. Highlight `Tails` (the default entry). Do **not** press Enter yet.
2. Press **`e`** to edit the boot entry.
3. Find the line starting with `linux /live/vmlinuz...`. Use arrow keys to navigate.
4. Append to that line (use space to separate from existing args):

   ```
    modprobe.blacklist=rtw88_8821ce
   ```

   Optional: also append ` nomodeset` if the previous boot showed a blank screen past GRUB.

5. Press **Ctrl+X** to boot.

**Expected:** Tails boots through. Console scrolls; eventually the Tails Welcome Screen appears.

**Failure modes:**
- *Kernel panic during boot.* The rtw88_8821ce blacklist suppresses the broken Realtek driver — without it, expect a kernel oops. If panic occurs *with* the blacklist, also append `nomodeset` and reboot.
- *Display blank past GRUB.* Add `nomodeset` to the kernel cmdline. The HP 14-dq Ice Lake screen-flicker bug ([Ubuntu #1874010](https://bugs.launchpad.net/ubuntu/+source/linux/+bug/1874010)) is the likely cause.
- *Tails reaches the Welcome Screen but mouse/keyboard are unresponsive.* The internal keyboard and trackpad on this laptop should both work natively under Tails. If they don't, disconnect any external keyboard you forgot to remove and reboot.

### Step 2.3 — Welcome Screen: Offline Mode + admin password

1. **Language / keyboard:** accept defaults (English / US) unless you have a reason otherwise.
2. **Encrypted Persistent Storage:** **DO NOT enable.** This ceremony is amnesic by design; persistent storage defeats that.
3. Click the **gear icon** (Additional Settings).
4. **Network connection:** select **Disable all networking (Offline Mode)**. Click Add.
5. **Administration password:** click. Set a session admin password (any value — it's wiped on shutdown). Used for `sudo` inside this Tails session only. Click Add.
6. Click **Start Tails**.

**Expected:** Tails desktop loads. Top-right tray shows network icon with "Offline Mode" or no network — *not* Tor or any active connection.

**After desktop loads:** Press **F12** on the laptop's function row to assert the airplane-mode hard-block. The keyboard backlight should flash and an on-screen indicator should appear.

7. Open a terminal (Activities → search → Terminal).
8. Verify radio state:

   ```
   rfkill list
   ```

**Expected:** every device should show `Soft blocked: yes` AND `Hard blocked: yes`.

**Failure modes:**
- *Hard blocked: no.* The F12 key didn't take. Try again. If F12 still doesn't hard-block, the Bluetooth radio (`hci0` / Realtek 0bda:b00a) can be soft-blocked manually: `sudo rfkill block all`.
- *Networking icon shows active Wi-Fi or Tor.* Welcome Screen choice didn't take. Reboot and re-do the Welcome Screen — Offline Mode must be selected before clicking Start Tails.

### Step 2.4 — Plug Drive #2 (OFFLINEDEBS)

1. Plug Drive #2 into a free USB-A port.

**Expected:** GNOME auto-mounts it at `/media/amnesia/OFFLINEDEBS`. Files visible in Files app or via `ls /media/amnesia/OFFLINEDEBS/`.

2. Pick the right deb set:

   ```
   cat /etc/debian_version
   ```

   - If `13.x`: use `/debian13/`
   - If `12.x`: use `/debian12/`
   - Anything else: halt and consult the workstation operator.

### Step 2.5 — Install offline-debs

3. Install the matched set:

   ```
   cd /media/amnesia/OFFLINEDEBS/<debian12-or-debian13>
   sha256sum -c SHA256SUMS
   ```

**Expected:** every line ends with `: OK`. Any FAILED line is a stop — halt and consult the workstation operator, do not proceed.

4. Install:

   ```
   sudo dpkg -i *.deb
   ```

**Expected:** dpkg processes all packages. Most will print *"newer version already installed, skipping"* — that is fine. The user-facing 5 (`paperkey`, `opensc`, `opensc-pkcs11`, `libccid`, `pcscd`) should install or upgrade cleanly. Final exit code: `0`.

**Failure modes:**
- *dpkg refuses to downgrade a system lib.* Use the OTHER set. Eject Drive #2 and re-mount, or `cd ../<other-set>` and retry. If both fail, halt and consult the workstation operator.
- *Dependency conflicts on opensc-pkcs11 specifically.* Try `sudo dpkg -i --force-confnew *.deb` once. If still failing, halt and consult the workstation operator; the C6 chapter is blocked.

5. Start the smart-card daemon:

   ```
   sudo systemctl start pcscd
   systemctl is-active pcscd
   ```

**Expected:** the second command prints `active`. If `inactive` or `failed`, run `sudo journalctl -u pcscd -n 20` and surface the output to the workstation operator.

### Step 2.6 — Plug Nitrokey #4 (test) for sanity

This is the test card from Phase 1 first-touch. We poke it once to confirm the smart-card stack is alive, then unplug it. **Nitrokeys #1, #2, #3 stay out for now** — they get plugged at the chapter that uses them.

1. Plug Nitrokey #4 into a free USB-A port.

   ```
   lsusb | grep -i nitrokey
   ```

**Expected:** one line, vendor `20a0`, product `42b1`. If not visible, replug. If still not visible, the smart-card stack didn't pick it up — halt and consult the workstation operator.

2. Verify both applets are reachable:

   ```
   gpg --card-status
   pkcs11-tool --module /usr/lib/opensc-pkcs11.so --list-slots
   ```

**Expected:** `gpg --card-status` shows `Application ID`, `Manufacturer: Nitrokey`, the serial. `pkcs11-tool` shows at least one slot, typically `Nitrokey 3 [CCID Interface]`.

**Failure modes:**
- *gpg --card-status returns nothing.* `scdaemon` may not be running. Try `gpg-connect-agent 'scd serialno' /bye`. If still empty, halt and consult the workstation operator.
- *pkcs11-tool reports "0 slots".* The opensc-pkcs11 module path may differ. Try `find / -name 'opensc-pkcs11.so' 2>/dev/null` and substitute the result for `--module`.

3. Unplug Nitrokey #4. Set it aside in its labeled pouch.

> **State at end of Part 2:** Tails is up, offline-mode confirmed, smart-card stack working, opensc/paperkey installed, Drive #2 still mounted. **Plug Drive #3 into a free port now** — it will receive the Output artifacts as the ceremony progresses. **Nitrokeys #1/#2/#3 still in their labeled pouches.** Ready for Chapter C2.

---

## Chapter C2 — Master keypair generation + offline backups

This chapter generates the distro GPG master keypair, immediately captures backups (paperkey + 2× LUKS USB), and creates a revocation certificate. **The master private key never leaves Tails RAM** during this chapter except as encrypted backup material on Drive #3 + the LUKS backup USBs and as printed paperkey output.

### Step C2.1 — Set up scratch workspace in Tails RAM

Ceremony work happens in `~/Persistent/` if persistent storage is enabled (it isn't), or `~/` otherwise. Tails `~/` is `/home/amnesia` in tmpfs. Anything written there evaporates on shutdown.

```
mkdir -p ~/ceremony
cd ~/ceremony
umask 077
```

**Expected:** directory created. `ls -ld ~/ceremony` shows `drwx------` (mode 0700).

### Step C2.2 — Generate the master keypair

```
gpg --full-generate-key
```

Answer the prompts:

| Prompt | Answer |
|---|---|
| Please select what kind of key you want | **(1) RSA and RSA** |
| What keysize do you want? | **4096** |
| Key is valid for? | **0** (key does not expire) |
| Is this correct? | **y** |
| Real name | **InterGenOS Project Signing Key** |
| Email address | **intergenos-primary@intergenstudios.com** |
| Comment | **primary** |
| Change (N)ame, (C)omment, (E)mail or (O)kay/(Q)uit? | **O** |
| Passphrase | **enter a strong passphrase**, write it on paper at the same time |

> **UID rendering:** gpg composes the three fields above into the canonical UID `InterGenOS Project Signing Key (primary) <intergenos-primary@intergenstudios.com>`. The split-field convention avoids gpg's parser-rejection of literal parens in the Real name field (parens are reserved for the comment-field syntax).

GPG will say *"We need to generate a lot of random bytes."* It can take a while — moving the trackpad / typing in another terminal speeds it up by feeding the entropy pool.

**Expected output:** a block ending with:

```
pub   rsa4096 2026-04-30 [SC]
      <40-character primary fingerprint>
uid                      InterGenOS Project Signing Key (primary) <intergenos-primary@intergenstudios.com>
sub   rsa4096 2026-04-30 [E]
```

**Record the primary fingerprint** in your offline log immediately. This is the master fingerprint that gets cross-published post-ceremony.

```
gpg --list-secret-keys --keyid-format LONG
```

**Expected:** the master `sec` line plus the encryption subkey `ssb` line. **No signing subkey yet** — those are added in C3.

**Failure modes:**
- *"gpg: not enough random bytes available"* — move the trackpad more aggressively, type in another terminal, or run `cat /proc/sys/kernel/random/entropy_avail` to monitor. If pool is consistently below 100, install `rng-tools` if available (offline-debs USB does not include it; halt and consult the workstation operator if you hit this).
- *Wrong UID typed.* Run `gpg --edit-key <fp>` → `adduid` → fix → `save`.

### Step C2.3 — Generate the revocation certificate

The revocation certificate lets you invalidate the master key if it's ever compromised. **Generate it now, while you have the master available, and store it offline.** A future-you with no access to the master can still publish the revocation.

```
gpg --output ~/ceremony/revocation.asc --gen-revoke <primary-fingerprint>
```

Answer prompts:

| Prompt | Answer |
|---|---|
| Create a revocation certificate for this key? | **y** |
| Please select the reason for the revocation | **0** (no reason specified — generic) |
| Enter an optional description | (leave blank) — press Enter, then again |
| Is this okay? | **y** |
| Passphrase | (the master passphrase you set in C2.2) |

**Expected:** file `~/ceremony/revocation.asc` exists, ~700 bytes, starts with `-----BEGIN PGP PUBLIC KEY BLOCK-----`.

This file is paperkey-printed alongside the master in C2.5.

### Step C2.4 — Export the public key

```
gpg --armor --export <primary-fingerprint> > ~/ceremony/intergenos-release-key.asc
```

**Expected:** file ~3-4 KB, ASCII-armored, starts with `-----BEGIN PGP PUBLIC KEY BLOCK-----`.

This is the file you'll cross-publish post-ceremony to keys.openpgp.org, intergenstudios.com, and `docs/signing-key.md`.

Copy it to Drive #3 now (it's harmless — it's the public key):

```
cp ~/ceremony/intergenos-release-key.asc /media/amnesia/CEREMONY/
sync
```

### Step C2.5 — Paperkey master backup (covers C5)

Paperkey extracts only the secret key material (not redundant public bits), making the printout shorter and recoverable by hand if the LUKS backups ever fail.

```
gpg --export-secret-keys <primary-fingerprint> | paperkey -o ~/ceremony/master-paperkey.txt
```

**Expected:** file `~/ceremony/master-paperkey.txt` — text file, OCR-friendly hex blocks with line numbers and checksums. ~2-4 KB depending on key.

**Print it twice** from the workstation (the file is plain text — copy via Drive #3 over to the workstation, or `cat ~/ceremony/master-paperkey.txt` and the workstation operator pastes into the workstation print job — your call). Keep one copy in the home safe alongside the LUKS USBs. The second copy is a redundancy backup; storage location is owner-call (some maintainers shred it after a year and rely on the LUKS USBs as primary).

> **Air-gap discipline check:** the only thing leaving the Tails laptop here is **plain text destined for paper**. No private keys cross the air-gap in any form a network can intercept.

### Step C2.6 — LUKS USB backup (covers C4)

This step backs the master + revocation cert into LUKS-encrypted USB drives. Since our staged USB set has only Drive #3 still in play (Drive #4 is sealed cold-spare), this step writes the LUKS backup container **onto Drive #3's existing FAT32 partition as a file**, not onto a separately-formatted USB.

> **Decision (resolved):** Drive #3 carries the only LUKS backup today. The custody design at `signing_key_custody_2026-04-18.md` specified 2× separately-formatted LUKS USBs (home-safe + bank-SDB); the second carrier is deferred to a post-ceremony future-expansion step. The master + revocation + subkey-stub files in `master-backup.luks` are portable — `cryptsetup luksOpen` + copy to additional drives whenever the architecture wants more redundancy. No new key generation needed for that expansion. Today: write Drive #3 only and continue.

**Procedure (Drive #3):**

1. Create a 50 MB LUKS container file on Drive #3:

   ```
   dd if=/dev/zero of=/media/amnesia/CEREMONY/master-backup.luks bs=1M count=50
   sudo cryptsetup luksFormat /media/amnesia/CEREMONY/master-backup.luks
   ```

   When prompted, type **YES** (uppercase) to confirm wipe, then enter a strong passphrase. Write that passphrase on paper alongside the master passphrase.

2. Open + mount the container:

   ```
   sudo cryptsetup luksOpen /media/amnesia/CEREMONY/master-backup.luks master-backup
   sudo mkfs.ext4 /dev/mapper/master-backup
   sudo mkdir -p /mnt/master-backup
   sudo mount /dev/mapper/master-backup /mnt/master-backup
   ```

3. Copy the master + revocation cert into it:

   ```
   gpg --export-secret-keys --armor <primary-fingerprint> > /tmp/master-secret.asc
   sudo cp /tmp/master-secret.asc /mnt/master-backup/master-secret.asc
   sudo cp ~/ceremony/revocation.asc /mnt/master-backup/revocation.asc
   sudo cp ~/ceremony/intergenos-release-key.asc /mnt/master-backup/intergenos-release-key.asc
   sudo sync
   ```

   (Note: `gpg --export-secret-keys` runs as the amnesia user — without `sudo` — because the master keypair lives in `~amnesia/.gnupg`. Running it under `sudo` would fail with "No secret key" since root has its own empty keyring at `/root/.gnupg`. Only the `cp` into the LUKS-mounted directory needs `sudo`.)

4. Unmount + close:

   ```
   sudo umount /mnt/master-backup
   sudo cryptsetup luksClose master-backup
   sync
   ```

5. Delete the unencrypted master-secret.asc from `/tmp` (Tails wipes RAM at shutdown, but be explicit):

   ```
   shred -u /tmp/master-secret.asc
   ```

**Expected:** `master-backup.luks` exists on Drive #3, ~50 MB, opens with the LUKS passphrase. Inside: `master-secret.asc`, `revocation.asc`, `intergenos-release-key.asc`.

**Storage:** Drive #3 with this LUKS file goes into the home safe post-ceremony. If owner has a second physical USB drive available for the bank-SDB backup, repeat steps 1-4 onto that drive (insert it now, mount, write same files, eject).

**Failure modes:**
- *cryptsetup luksFormat returns "command not found"* — the offline-debs USB does not include cryptsetup. Tails 7.x ships cryptsetup by default; if it's missing, halt and consult the workstation operator.
- *No space on Drive #3* — drive is 28.7 GiB; 50 MB should fit easily. If "No space left", check `df /media/amnesia/CEREMONY` — something else is on the drive that shouldn't be.

### Step C2.7 — End-of-C2 sanity check

```
ls -la ~/ceremony/
ls -la /media/amnesia/CEREMONY/
gpg --list-secret-keys
```

**Expected:**
- `~/ceremony/` contains: `revocation.asc`, `intergenos-release-key.asc`, `master-paperkey.txt`
- `/media/amnesia/CEREMONY/` contains: `intergenos-release-key.asc` (public), `master-backup.luks` (encrypted master + revocation + public)
- `gpg --list-secret-keys` shows the master `sec` + encryption subkey `ssb` (no signing subkey yet — that's C3)

> **State at end of C2:** master keypair exists in Tails RAM and is backed up to paperkey (printed × 2) + LUKS container on Drive #3 (and optionally a second USB if owner staged one). Revocation cert created. Public key exported. **Master never leaves Tails RAM in plaintext.** Ready for C3.

---

## Chapter C3 — PGP signing subkeys to Nitrokey OpenPGP applets

This chapter generates three signing subkeys ([S1], [S2], [S3]) — one per backup Nitrokey — and uses `keytocard` to move each subkey onto the corresponding Nitrokey's OpenPGP applet. After each `keytocard`, the subkey's private material exists **only on that Nitrokey** (Tails RAM still has a stub but the actual signing material is on the card).

> **Why three subkeys instead of one copied to three cards?** OpenPGP keytocard moves the private key onto the card — it cannot then be moved a second time. To have signing capability on three independent cards, generate three separate subkeys (each certified by the same master), one per card.

### Step C3.1 — Plug Nitrokey #1 (primary)

Plug Nitrokey #1 into a free USB-A port.

```
gpg --card-status
```

**Expected:** the card responds. `Application ID` line, `Manufacturer: Nitrokey`, serial number visible. **Cross-check the serial against the offline log entry for Nitrokey #1 (created during the first-touch checklist per `signing-procedure.md` Appendix B step 6).**

**Failure modes:**
- *"No such device"* — replug. If still nothing, run `sudo systemctl restart pcscd` and retry.
- *Wrong serial.* The card you plugged in is not labeled #1, OR the offline log entry is wrong. Halt and consult the workstation operator.

### Step C3.2 — Add subkey [S1] to the master

```
gpg --edit-key <primary-fingerprint>
```

In the gpg prompt:

```
gpg> addkey
```

Answer:

| Prompt | Answer |
|---|---|
| Please select what kind of key you want | **(4) RSA (sign only)** |
| What keysize do you want? | **4096** |
| Key is valid for? | **2y** |
| Is this correct? | **y** |
| Really create? | **y** |
| Passphrase | (master passphrase) |

**Expected:** new `ssb` line appears in the listing with `[S]` capability and 2y expiry.

### Step C3.3 — Move [S1] to Nitrokey #1

Still inside `gpg --edit-key`:

```
gpg> key 2
```

(Selects the second subkey listed — the one we just added. The encryption subkey from C2 is `key 1`.)

**Expected:** the `ssb` line now shows an `*` next to it, marking it selected.

```
gpg> keytocard
```

Answer:

| Prompt | Answer |
|---|---|
| Signature key ([1]/[2]/[3]/[Q]) | **1** (the OpenPGP signing slot [S]) |
| Replace existing key? | **y** if the card already had one (it shouldn't — fresh card from first-touch) |
| Master passphrase | (enter) |
| Admin PIN | (Nitrokey #1 admin PIN — set during first-touch) |

**Expected:** `keytocard` succeeds without error. The subkey's private material now lives on Nitrokey #1's [S] slot. The subkey listing in `gpg --list-secret-keys` will show `ssb>` (note the `>`) indicating it's a stub — actual material on card.

```
gpg> save
```

**Expected:** returns to shell prompt. `gpg --card-status` now shows `Signature key` populated with the subkey fingerprint.

**Failure modes:**
- *"There is no public key for the user ID"* — wrong key selected. Restart the `gpg --edit-key` session and re-do `key 2`.
- *Admin PIN rejected.* The PIN was mistyped. Three wrong attempts will block the admin PIN, requiring PUK to unblock — halt and consult the workstation operator IMMEDIATELY if you mistype twice.

### Step C3.4 — Verify [S1] on Nitrokey #1

Unplug Nitrokey #1 and plug it back in (forces gpg-agent to re-read the card):

```
gpg --card-status
```

**Expected:** `Signature key ....: <fingerprint of subkey S1>`. Touch policy should already be `on` from first-touch.

Test sign:

```
echo "ceremony test sign" | gpg --clearsign --local-user <subkey-S1-fingerprint>
```

**Expected:** the Nitrokey blinks for touch confirmation. Touch the metal contact. Output is a signed clearsign block. **No errors.**

> **Back up Nitrokey #1's [S1] state** to your offline log: subkey S1 fingerprint, Nitrokey #1 serial, date/time of keytocard.

### Step C3.5 — Repeat for Nitrokey #2 ([S2])

Unplug Nitrokey #1, set aside in its pouch labeled #1.

Plug Nitrokey #2.

```
gpg --card-status
```

**Cross-check the serial against the #2 offline log entry.**

Repeat Steps C3.2 + C3.3 + C3.4, generating subkey [S2] and `keytocard`-ing it to Nitrokey #2.

### Step C3.6 — Repeat for Nitrokey #3 ([S3])

Unplug Nitrokey #2, set aside in its pouch labeled #2.

Plug Nitrokey #3. Same sequence.

### Step C3.7 — End-of-C3 sanity check

After all three subkeys are on their respective Nitrokeys:

```
gpg --list-secret-keys --keyid-format LONG
```

**Expected:**
- Master `sec` line
- Encryption subkey `ssb` (still in Tails — encryption stays in software for our use case)
- Three signing subkeys, each shown as `ssb>` (stub — material on card)

```
gpg --list-keys --keyid-format LONG <primary-fingerprint>
```

**Expected:** master `pub` + four `sub` lines (1 encryption, 3 signing — each with [S] capability and a 2y expiry).

> **State at end of C3:** three signing subkeys exist, one per Nitrokey, each touch-required for sign operations. Master + revocation cert backed up offline (paperkey + LUKS). **Time to refresh the LUKS backup with the now-updated master record:**

### Step C3.8 — Re-back up master + subkey records to LUKS

The master keychain now has subkey stub records that point at the Nitrokeys. Re-export and overwrite the LUKS-stored copy:

```
gpg --export-secret-keys --armor <primary-fingerprint> > /tmp/master-secret-with-subkeys.asc
sudo cryptsetup luksOpen /media/amnesia/CEREMONY/master-backup.luks master-backup
sudo mount /dev/mapper/master-backup /mnt/master-backup
sudo cp /tmp/master-secret-with-subkeys.asc /mnt/master-backup/master-secret.asc
sudo sync
sudo umount /mnt/master-backup
sudo cryptsetup luksClose master-backup
shred -u /tmp/master-secret-with-subkeys.asc
sync
```

**Expected:** LUKS container updated with the post-C3 master state.

> **State at end of C3 + C3.8:** master + revocation + public + subkey-stubs all backed up. Three Nitrokeys (#1/#2/#3) carry their respective signing subkeys. **Plug Nitrokey #1 back in for C6** — it's the one that will hold the EFI-binary signing key as well.

---

## Chapter C6 — PIV slot 9c EFI-binary signing keypair

This chapter generates the EFI-binary X.509 signing keypair **on Nitrokey #1's PIV applet, slot 9c**. The private key is generated on-card and never leaves it. We then self-sign a vendor cert that becomes the trust anchor for `sbsign` operations on the workstation post-ceremony.

> **Single-token PIV keypair.** The PIV slot 9c key on Nitrokey #1 is the canonical EFI signer. Backup story: if Nitrokey #1 is lost, a new keypair is generated on Nitrokey #2 (or #3) and a fresh MOK enrollment package is shipped to users. There is no PIV-key-clone path — the private material is hardware-bound by design.

### Step C6.1 — Plug Nitrokey #1, verify PIV applet

Plug Nitrokey #1 into a free USB-A port.

```
pkcs11-tool --module /usr/lib/opensc-pkcs11.so --list-slots
```

**Expected:** at least one slot, `Nitrokey 3 [CCID Interface]` or similar. Slot index (typically `0`) is what we'll reference.

```
pkcs11-tool --module /usr/lib/opensc-pkcs11.so --login --pin <piv-user-pin> --list-objects
```

**Expected:** any pre-existing objects listed (should be empty for a fresh-from-first-touch card; the test-cert from Nitrokey #4's first-touch was on #4, not #1).

### Step C6.2 — Generate keypair in PIV slot 9c

The Nitrokey 3 PIV applet supports key generation via `nitropy` or via vendor-specific PKCS#11 calls. Tails' `pkcs11-tool` does not expose key-generation directly for PIV slots; we use `yubico-piv-tool` which is the cross-vendor CLI for PIV.

> **Tooling note:** `yubico-piv-tool` works against Nitrokey 3 PIV via the standard PIV interface (yubico-piv-tool is brand-named after the YubiKey but the PIV protocol is vendor-neutral). Verified by Phase 1 first-touch step 7 (test-cert dry-run on Nitrokey #4).

```
yubico-piv-tool --action generate \
    --slot 9c --algorithm RSA2048 \
    --output ~/ceremony/efi-9c-pubkey.pem
```

**Expected:** prompts for PIV management key (Nitrokey factory default `010203040506070801020304050607080102030405060708`, OR the rotated value if first-touch step changed it — check offline log). After auth: file `~/ceremony/efi-9c-pubkey.pem` is written. Contents start with `-----BEGIN PUBLIC KEY-----`.

**Failure modes:**
- *"failed to authenticate"* — wrong management key. Try the factory default. If still failing, the management key was rotated during first-touch — recover from the offline log.
- *PIN-Always policy interaction.* PIV slot 9c on Nitrokey 3 enforces touch-and-PIN-on-every-use by default. Generation should not need user-PIN, only management-key. If user-PIN is prompted unexpectedly, halt and consult the workstation operator.

### Step C6.3 — Self-sign the vendor cert

```
yubico-piv-tool --action verify-pin \
    --pin <piv-user-pin> \
    --action selfsign-certificate \
    --slot 9c \
    --subject "/CN=InterGenOS Secure Boot CA" \
    --valid-days 730 \
    --input ~/ceremony/efi-9c-pubkey.pem \
    --output ~/ceremony/intergenos-vendor-cert.pem
```

**Expected:** prompts for PIV user PIN, then asks for **physical touch on the Nitrokey** (the slot-9c touch policy from first-touch step 5 enforces this). After touch: file `~/ceremony/intergenos-vendor-cert.pem` is written. Contents start with `-----BEGIN CERTIFICATE-----`. The cert's CN is `InterGenOS Secure Boot CA`, valid for 2 years.

**Failure modes:**
- *Touch timeout.* Default touch window is 15 seconds. Press the metal contact on the Nitrokey body firmly within that window. If timed out, retry the same command.
- *Subject string mismatch in output.* Re-run with corrected `--subject`. The CN must exactly match what `docs/signing-key.md` claims (`InterGenOS Secure Boot CA`).

### Step C6.4 — Verify cert + matching pubkey

```
openssl x509 -in ~/ceremony/intergenos-vendor-cert.pem -noout -text | head -20
```

**Expected:** Subject: `CN = InterGenOS Secure Boot CA`. Validity period ~2 years from today. Public-Key: RSA 2048 bit. Signature algorithm: `sha256WithRSAEncryption`.

```
openssl x509 -in ~/ceremony/intergenos-vendor-cert.pem -noout -fingerprint -sha256
```

**Record the cert SHA-256 fingerprint** in your offline log. This fingerprint gets cross-published in `docs/signing-key.md` post-ceremony.

### Step C6.5 — Read back from the card to confirm

```
yubico-piv-tool --action read-certificate --slot 9c > /tmp/cert-from-card.pem
diff ~/ceremony/intergenos-vendor-cert.pem /tmp/cert-from-card.pem
```

**Expected:** `diff` returns nothing (files are identical). If diff shows differences, the read-back didn't match — something went wrong with the certificate write. Halt and consult the workstation operator.

### Step C6.6 — Copy vendor cert to Drive #3 (Output)

```
cp ~/ceremony/intergenos-vendor-cert.pem /media/amnesia/CEREMONY/
sync
```

**Expected:** `intergenos-vendor-cert.pem` exists on Drive #3 alongside `intergenos-release-key.asc` and `master-backup.luks`.

> **State at end of C6:** Nitrokey #1 holds:
>  - OpenPGP applet [S]: signing subkey [S1] (from C3)
>  - PIV applet slot 9c: EFI signing keypair (from C6) — private on card, public available as `intergenos-vendor-cert.pem`

---

## Part 6 — Verification + handoff

### Step 6.1 — Final inventory check

```
ls -la /media/amnesia/CEREMONY/
gpg --card-status
gpg --list-keys --keyid-format LONG <primary-fingerprint>
```

**Expected files on Drive #3:**
| File | Purpose | Sensitivity |
|---|---|---|
| `intergenos-release-key.asc` | Public master + subkey announcement | Public, will publish |
| `intergenos-vendor-cert.pem` | EFI signing X.509 vendor cert (public part) | Public, will publish |
| `master-backup.luks` | LUKS container with master secret + revocation + public | **Encrypted master material** — protect like the LUKS passphrase |

### Step 6.2 — Record in the offline log

In a paper notebook (NOT typed anywhere electronic):

- [ ] Master primary fingerprint: ____________________
- [ ] Subkey [S1] fingerprint: ____________________ (Nitrokey #1)
- [ ] Subkey [S2] fingerprint: ____________________ (Nitrokey #2)
- [ ] Subkey [S3] fingerprint: ____________________ (Nitrokey #3)
- [ ] EFI vendor cert SHA-256: ____________________ (Nitrokey #1, PIV slot 9c)
- [ ] Master passphrase (set in C2.2): kept on a separate paper, stored separately
- [ ] LUKS-backup passphrase (set in C2.6): kept on a separate paper, stored separately
- [ ] Date / time: 2026-04-30, ____ UTC
- [ ] Tails version booted: 7.7
- [ ] Drive #3 (with master-backup.luks): going to home safe
- [ ] Optional: second LUKS USB → bank safety-deposit box (next bank-business-day)
- [ ] Paperkey copy #1: home safe
- [ ] Paperkey copy #2: storage location: ____________________
- [ ] Nitrokey #1: stays with maintainer (post-ceremony daily-driver)
- [ ] Nitrokey #2: home safe
- [ ] Nitrokey #3: bank safety-deposit box (next bank-business-day)
- [ ] Nitrokey #4: spare-pool (post-ceremony, with test material wiped)

### Step 6.3 — Tails shutdown

> **DO NOT skip this step.** Tails amnesia is what protects you. Shutdown wipes RAM (including the master + subkey material that briefly existed there during C2/C3 generation).

1. Eject Drive #2 (offline-debs) and Drive #3 (CEREMONY): right-click in Files app → Eject. Wait for activity LED to stop.
2. Unplug Nitrokey #1. Set in its labeled pouch.
3. **Power off Tails** (top-right system menu → Power → Power Off). Do **not** suspend.

**Expected:** laptop powers off cleanly. RAM wiping is automatic (Tails wipes RAM on shutdown).

4. Wait until the laptop is fully off (screen black, power LED off). Pull Drive #1 (Tails boot).

**State at end of Part 6:** Tails session destroyed. All ceremony-generated material lives on:
- Nitrokey #1 / #2 / #3 (signing subkeys + #1 also has PIV slot 9c)
- Drive #3 (LUKS-encrypted master backup + public artifacts)
- Paperkey printouts (× 2)
- The maintainer's offline log

---

## Part 7 — Post-ceremony (online side)

This is the workstation portion. Powers back the InterGenOS laptop or use a different daily-driver workstation.

### Step 7.1 — Mount Drive #3 to retrieve public artifacts

The CEREMONY USB has both public artifacts (release-key.asc + vendor-cert.pem) and the LUKS-encrypted master backup. Mount it on the workstation as a normal FAT32 USB; the LUKS file is just a file from FAT32's perspective.

```
mkdir -p ~/ceremony-output
cp /run/media/<user>/CEREMONY/intergenos-release-key.asc ~/ceremony-output/
cp /run/media/<user>/CEREMONY/intergenos-vendor-cert.pem ~/ceremony-output/
```

**Do NOT copy `master-backup.luks` to the workstation** — that file goes to home-safe with the USB, not to a network-connected machine.

### Step 7.2 — Update `docs/signing-key.md` with fingerprints

Edit `docs/signing-key.md`:

- Replace `*PENDING — ceremony scheduled for the week of 2026-04-28*` with the actual primary fingerprint
- Replace the release-signing subkey fingerprint placeholders with [S1] / [S2] / [S3] (or the [S1] only — the public-facing record can list one canonical signer)
- Replace the EFI-binary signing key fingerprint placeholder with the SHA-256 you recorded for the vendor cert

Commit + push as a normal repo change. Peer-review per project norm.

### Step 7.3 — Publish the public key to keys.openpgp.org

```
gpg --import ~/ceremony-output/intergenos-release-key.asc
gpg --keyserver keys.openpgp.org --send-keys <primary-fingerprint>
```

Confirm the email-verification flow at keys.openpgp.org if prompted. The role-UID convention (`InterGenOS Project Signing Key (primary) <intergenos-primary@intergenstudios.com>`) means the verification email goes to the project-role address — confirm it from whichever inbox that alias forwards to.

### Step 7.4 — Cross-publish on intergenstudios.com

Upload the public key + fingerprint to `intergenstudios.com/signing-key`. Convention is a static page with the fingerprint in plain text + the .asc file linked.

### Step 7.5 — Wire into `sign-release.sh`

Set environment variables (or pass flags) so the release-signing script knows which keys to use:

```
export INTERGENOS_GPG_KEY_ID=<subkey-S1-fingerprint>
export INTERGENOS_PKCS11_URI="pkcs11:object=<piv-slot-9c-key-label>;type=private"
```

Test-sign a sample artifact per `docs/signing-procedure.md` to confirm the pipeline works end-to-end.

### Step 7.6 — Append to sessions.php log

Per the InterGenOS project log convention:

```
echo "2026-04-30 / signing-key ceremony / primary fingerprint: <fp> / subkeys [S1][S2][S3] on Nitrokeys #1/#2/#3 / EFI cert sha256: <sha256> / location: home + bank-SDB" \
    | append-to-sessions-log
```

(The exact append mechanism is project-specific; check the latest sessions.php convention.)

### Step 7.7 — Physical storage actions

- [ ] Drive #3 (with master-backup.luks) → home safe
- [ ] Nitrokey #2 → home safe
- [ ] Paperkey printout #1 → home safe
- [ ] Paperkey printout #2 → owner-decided second location
- [ ] Nitrokey #3 → bank safety-deposit box (next bank-business-day)
- [ ] Optional: second LUKS USB → bank safety-deposit box (alongside Nitrokey #3)
- [ ] Nitrokey #4 → spare-pool storage
- [ ] Nitrokey #1 → stays with the maintainer (daily-driver post-ceremony)

### Step 7.8 — Drive #4 disposition

Drive #4 was the sealed cold-spare. Two options:

- **Keep sealed** — cold-spare for a future emergency re-imaging.
- **Open and re-stage as a third LUKS backup** — opens the home-safe / bank-SDB / off-site triangle. Owner-call.

---

## Part 8 — Recovery branches / known failure modes

### 8.1 — Tails won't boot (display blank past GRUB)

Add `nomodeset` to the kernel cmdline (Step 2.2). If still blank, try `nomodeset video=vesa:off` or fall back to `vga=normal`. If the laptop's iGPU is fundamentally incompatible with Tails 7.7, last resort is to image a Tails 6.x ISO onto Drive #4 (breaks cold-spare seal) and use the `/debian12/` deb set.

### 8.2 — pcscd won't start under Tails

```
sudo journalctl -u pcscd -n 50
```

Check for permission errors or USB enumeration failures. Try `sudo systemctl restart pcscd`. If `pcsc-tools` is needed for diagnosis (`pcsc_scan`), check whether the offline-debs USB has it; if not, halt and consult the workstation operator.

### 8.3 — Nitrokey not detected after `dpkg -i`

Replug. Check `lsusb` for the Nitrokey vendor:product. If lsusb sees it but `gpg --card-status` doesn't, run:

```
sudo systemctl restart pcscd
gpg-connect-agent 'scd serialno' /bye
gpg --card-status
```

If still nothing, the Nitrokey may need its USB port configured differently — try a different port or a USB 2.0 port specifically (some smart-card readers prefer 2.0 over 3.0).

### 8.4 — `keytocard` fails with "card not available"

The card was unplugged mid-operation, or another gpg-agent session is holding it. `pkill -f gpg-agent`, then retry `gpg --card-status` to re-establish, then re-do the keytocard step.

### 8.5 — Wrong subkey moved to wrong card

If you accidentally `keytocard`-ed [S2] to Nitrokey #1 (instead of [S1]):

- The subkey is now on the card. It's still signed by the master, so it's still cryptographically valid.
- Either: (a) accept and re-label the card #2 in your offline log, OR (b) factory-reset Nitrokey #1's OpenPGP applet (`gpg --card-edit` → `admin` → `factory-reset`) and start over.

Halt and consult the workstation operator; this is recoverable but the log needs to match physical reality before C6.

### 8.6 — PIV slot 9c key generation fails

`yubico-piv-tool --action generate` returns "PKCS#11 module error": typically a tooling quirk under offline-debs Tails. Alternate paths:

- Use `nitropy nk3 piv` if it's installed (it's not in the offline-debs USB; would need to be pre-staged on a follow-up USB).
- Use the OpenSC `piv-tool` directly (`piv-tool --serial --gen-key 9c`).

If neither works, halt and consult the workstation operator; the EFI signing chain is blocked until C6 completes successfully.

### 8.7 — Vendor cert read-back doesn't match write

`diff` shows differences after Step C6.5. The write may have been partial, or the cert has a different on-card representation than the host file. Re-do C6.3 (selfsign + write). If diff still differs after a second pass, halt and consult the workstation operator.

### 8.8 — Power loss mid-ceremony

Tails amnesia means a power loss before backups complete = master keypair is lost. Mitigations already taken:

- Backups happen **immediately** after master generation (C2.5 paperkey + C2.6 LUKS) — minimizes the window where master exists only in RAM.
- AC power throughout ceremony; battery is power-blip backup only.

If power loss occurs **before** C2.5 paperkey:
- Master is gone. Reboot Tails, restart from C2.1.
- If a partial master existed long enough to seed any external system: there shouldn't be — the master never leaves RAM until C2.5 paperkey export.

If power loss occurs **between C2.5 (paperkey) and C2.6 (LUKS)**:
- Paperkey is the only backup. Reboot Tails, restore master from paperkey (`paperkey --pubring intergenos-release-key.asc --secrets paperkey-typed-back-in.txt --output ...`) and continue from C3.

If power loss occurs **after C2.6 (LUKS) but before C3 completes**:
- Restore master from LUKS (or paperkey). Continue from where C3 left off, but be careful: if a `keytocard` was in flight, the card may have a partial subkey. Run `gpg --card-edit` on each Nitrokey, check its [S] slot, and either continue or `factory-reset` and re-do.

---

## Part 9 — Glossary + cross-references

### Glossary

- **Master keypair** — the long-lived RSA-4096 PGP key that certifies all signing subkeys. Lives in offline backups (paperkey + LUKS USB) only; never on a hardware token directly.
- **Signing subkey [S]** — a child key under the master, with `[S]` (sign) capability. Touch-required, lives on a Nitrokey OpenPGP applet. Each backup Nitrokey gets its own subkey.
- **Encryption subkey [E]** — generated by `gpg --full-generate-key` automatically alongside the master. Stays in software (Tails RAM during ceremony, LUKS backup after). Used for `security@intergenstudios.com` PGP-encrypted reports per `SECURITY.md`.
- **Revocation certificate** — a pre-generated revocation that can be published if the master is ever compromised. Generated **alongside** the master so a future-you with no master access can still revoke.
- **Paperkey** — a tool (`paperkey`) that extracts only the secret-key octets from a GPG export, producing a short OCR-friendly hex transcription. Recoverable by hand if the LUKS USBs are lost.
- **LUKS USB** — a USB drive (or file-on-USB) carrying a Linux Unified Key Setup encrypted volume. The master + revocation are stored inside; the LUKS passphrase is the only barrier between someone with the USB and the master keypair.
- **PIV slot 9c** — the X.509 signing slot on a PIV-capable smart card. On Nitrokey 3, slot 9c can hold an RSA-2048 keypair generated on-card. Used for `sbsign` operations (kernel + GRUB EFI binaries).
- **Vendor cert** — the X.509 self-signed certificate that pairs with the PIV slot 9c private key. Public, distributed via `docs/signing-key.md` + intergenstudios.com. End users enroll it via shim/MOK.
- **`keytocard`** — the `gpg --edit-key` command that moves a subkey's private material onto the OpenPGP applet of an inserted smart card. Irreversible — the private material no longer exists in the GPG keyring, only the stub does.

### Cross-references

- [`docs/signing-procedure.md`](../signing-procedure.md) — the post-ceremony release-signing operational runbook; assumes this ceremony has completed.
- [`docs/signing-key.md`](../signing-key.md) — the canonical fingerprint publication page; gets populated post-ceremony.
- [`docs/research/installer/signing_key_custody_2026-04-18.md`](../research/installer/signing_key_custody_2026-04-18.md) — design rationale, alternatives evaluated, D1 decisions.
- [`docs/research/installer/ms_shim_sponsorship_2026-04-18.md`](../research/installer/ms_shim_sponsorship_2026-04-18.md) — Microsoft shim-review path (post-ceremony parallel track).
- [`SECURITY.md`](../../SECURITY.md) — disclosure policy + trust-anchor compromise response.
- [`research/ceremony/hp_laptop_tails_host_eval_2026-04-29.md`](../../research/ceremony/hp_laptop_tails_host_eval_2026-04-29.md) — Phase 1 host evaluation (gitignored research dir; on the laptop).

### External references

- Tails Welcome Screen: https://tails.net/doc/first_steps/welcome_screen/
- Tails wireless / rfkill: https://tails.net/doc/advanced_topics/wireless_devices/
- Nitrokey 3 PIV: https://docs.nitrokey.com/nitrokeys/features/piv/certificate_management
- Nitrokey 3 OpenPGP: https://docs.nitrokey.com/nitrokeys/features/openpgp
- `paperkey`: http://www.jabberwocky.com/software/paperkey/
- `yubico-piv-tool` (cross-vendor PIV CLI, works against Nitrokey 3 PIV): https://developers.yubico.com/yubico-piv-tool/

---

**End of procedure. Run start-to-finish, in order. The workstation-side cross-check operator stands by throughout for sanity-checks and recovery branches.**
