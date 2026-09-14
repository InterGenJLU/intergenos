<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- Copyright (C) 2015-2016, 2026 InterGenJLU -->

# First-Publish Runbook — InterGenOS binary mirror

How to perform the **first signed publish** of the InterGenOS binary package mirror
(`repo.intergenos.org`), and every publish after. This is the operational how-to for
publishing; for the *design* (layout, trust model, atomic-promote rationale) see
[`../mirror/design.md`](../mirror/design.md).

The canonical script is [`../../scripts/publish-repo.sh`](../../scripts/publish-repo.sh),
wired into [`../../scripts/build-intergenos.sh`](../../scripts/build-intergenos.sh) as the
optional `publish` phase. This runbook documents running it.

> **Trust note.** The integrity boundary is the GPG signature on `InterGenOS.db`, not TLS.
> The signing material lives on hardware tokens; the master key is offline and never
> touches the mirror host. See [`../signing-key.md`](../signing-key.md).

---

## 1. Prerequisites

- **A completed build** with per-package `.igos.tar.gz` archives in the build-output
  archive directory (the build pipeline emits these). Default `--archive-dir` is the
  build-output directory; pass it explicitly if publishing a saved set.
- **The release-signing hardware token present + unlocked:**
  - `NK1` → subkey **S1** `D7AA641D 81ACD690 C5AD865E 7276E14D D8886BFE` (primary signer).
  - `NK2` → subkey **S2** `81DD223F 9BA9B3F2 AFBFFC5A FA24B042 975F775E` (backup signer).
  - Both are certified by the offline master `5597A3E0 587B2530 06D0DD7B 8C508261 82083050`.
  - Pre-check: `gpg --list-secret-keys <subkey-fp>` must succeed.
- **SSH publish access** to the mirror host: `ssh -p 2200 intergenos@origin.intergenstudios.com`
  (an ed25519 key authorized for publishing). The script writes directly into the
  docroot under the `intergenos` account — no root step, no control-panel UI.
- **The source archive set on the mirror** at `/home/intergenos/repo/sources/` (served at
  `https://repo.intergenos.org/sources/`). Populate/refresh via
  `scripts/download-sources.py --all --mirror-upload` before publishing if `--skip-sources`
  is not used.
- **The release image checksum record** (`intergenos-<release>.iso.sha256`) and the
  current Markdown source of the wiki switching page. A new index is not signed until
  the public-document currency gate binds the README and changelog to the checksum
  record, and every package named by that page to the generated mirror index.

---

## 2. Dry-run first (always)

```bash
/usr/bin/bash /mnt/intergenos/scripts/publish-repo.sh --dry-run \
  --iso-sha256 /absolute/path/intergenos-r001.3.iso.sha256 \
  --wiki-switching-page /absolute/path/wiki/src/start-here/switching.md
```

This runs the remote, capacity, advancement, source and public-document preflights,
generates the index in-place, and prints the rsync and promote it *would* perform —
without signing, touching the hardware token, entering the signing hold, or writing to
the mirror host. The installed-release record and evaluated-corpus byte gate remain
release-only checks and run on the real publish. Confirm the archive count and staging
path before that run.

---

## 3. The publish (what `publish-repo.sh` does)

```bash
/usr/bin/bash /mnt/intergenos/scripts/publish-repo.sh \
  --iso-sha256 /absolute/path/intergenos-r001.3.iso.sha256 \
  --wiki-switching-page /absolute/path/wiki/src/start-here/switching.md

/usr/bin/bash /mnt/intergenos/scripts/publish-repo.sh --gpg-key NK2 \
  --iso-sha256 /absolute/path/intergenos-r001.3.iso.sha256 \
  --wiki-switching-page /absolute/path/wiki/src/start-here/switching.md
```

1. **Pre-checks** — release validation, SSH reachability, remote snapshot layout,
   capacity, strict version/release advancement, source correspondence, and evaluated-
   corpus byte identity all run before the index or signing ceremony. The archive
   directory must exist and be non-empty. The capacity calculation projects genuinely
   new bytes against remote free space and fails closed if post-publish free space would
   fall below 25 percent. `--accept-capacity-risk` is the explicit override; the estimate
   is a documented lower bound because it matches candidates by name and size while
   rsync later matches by content.
2. **Generate index** — `pkm.repo.generate_index(<archive-dir>, arch='x86_64')` writes
   `InterGenOS.db` (gzipped JSON; the format `pkm/repo.py` parses), with each package's
   SHA-256.
3. **Public-document currency gate** — `scripts/check-doc-claims.py` checks the four
   release-identity files, image checksum record, README download and Upcoming sections,
   SECURITY status, release-policy version rows, changelog head, and the switching page's
   package commands against the generated index. Every stale claim is printed as
   `file:line`; any finding exits 2 before a signing approval is requested. The
   README's human-rounded image size and the changelog date have no second truth
   source in the tree and are stated as outside this mechanical gate.
4. **Signing hold** — the script prints one brief: the absolute index path and SHA-256,
   archive-row count, every version/release row changed from the served index, signing-key
   fingerprint, and the fact that exactly one PIN and one token touch follow. It then
   prints a one-use approval-file command and waits for the exact word `sign`. The path is
   unique to this process and index, must be a 0600 regular file owned by the publisher,
   and is consumed after a safe read. Any other content or the bounded timeout aborts
   before the key is used, leaves the archive staging and generated index intact, and
   prints the exact command to resume with the same environment.
5. **Sign the index** — only after approval, the script verifies the chosen secret subkey
   is available and runs `gpg --yes --detach-sign --armor --local-user <subkey-fp>` over
   `InterGenOS.db` → `InterGenOS.db.sig`. **The hardware token prompts for exactly one PIN
   and one touch on the workstation.** It first runs
   `gpg-connect-agent updatestartuptty /bye` to re-point the agent at the live graphical
   session, so `pinentry-gnome3` reliably opens; `sign_index` passes `--yes` so a
   re-publish overwrites the prior signature without an extra terminal prompt.

   > **Troubleshooting — `gpg: cannot open '/dev/tty'` at signing.** Two distinct causes,
   > both now handled in code but worth knowing:
   > 1. **gpg-agent lost its `DISPLAY` binding** (drifts on agent restart or a non-GUI
   >    client connecting), so it can't launch the GUI pinentry and falls back to a terminal
   >    one. Confirm with `gpg-connect-agent 'GETINFO std_session_env' /bye | grep -i display`
   >    (empty = broken). Fix: export the session env (`DISPLAY`, `DBUS_SESSION_BUS_ADDRESS`,
   >    `XDG_RUNTIME_DIR` — from `loginctl` / the gnome-shell `/proc/<pid>/environ`) + run
   >    `gpg-connect-agent updatestartuptty /bye` (the preflight does this).
   > 2. **The prior `InterGenOS.db.sig` exists and gpg prompted "overwrite? (y/N)" on
   >    /dev/tty** — looks like a pinentry failure but is just an unanswered overwrite prompt.
   >    Fixed by `sign_index` passing `--yes`. This bites a *re-publish*, after cause 1 is
   >    cleared.
   >
   > If a publish still dies at signing after the index is generated, sign `InterGenOS.db`
   > directly with the subkey **fingerprint** (not the `NK1` alias) + `--yes`, then resume
   > with `--skip-sign` (no regenerate — index generation is not byte-stable, so a fresh
   > `InterGenOS.db` would void the signature).
6. **Rsync to staging, incrementally** — the signed tree rsyncs into a per-publish
   `_staging-<UTC_ISO_TS>/` directory directly under `/home/intergenos/repo/x86_64/`
   (plus the source archives unless `--skip-sources`). The transfer is content-addressed
   (`rsync --checksum`) and hardlinks against **every** snapshot already on the volume —
   `current/`, each `_previous/` generation, and any staging directory an interrupted run
   left behind — so an archive that already exists anywhere on the volume is hardlinked
   rather than re-sent. The index and its signature always change, so they always transfer.
   A first publish, with no `current/` to link against, falls back to a full transfer.
7. **Atomic promote** — `ln -sfn <staging> current.new` then `mv -T current.new current`
   (a single atomic syscall on ext4). The prior target is archived to `_previous/`. No
   404 window; in-flight clients complete against the old target or restart against the
   new one. No httpd restart needed — Apache serves the swapped symlink on the next
   request.
8. **Retention prune** — after the promote, `_previous/` is pruned to the most recent
   `--keep-previous N` generations (default **1**). Each retained generation costs roughly
   a full unshared copy of the source tree, so without the prune the volume leaks a
   generation per publish. The prune acts only inside `_previous/`, only on entries whose
   names match the archive-snapshot shape, and never on whatever `current/` resolves to.
   `--keep-previous 0` disables retention entirely.
9. **Transparency-log append** — the publish appends the signed index to the append-only
   transparency log for cold-read auditability. `--skip-transparency` is an emergency
   override for a genuinely unavailable log substrate; the default is fail-closed so every
   published index is recorded.

**Launch it as a detached unit, not as a child of your shell.** A publish moves tens of
gigabytes and runs for the better part of an hour; run it under `systemd-run --user` so a
client or editor restart cannot kill it mid-transfer, carrying the session's
`DISPLAY` / `DBUS_SESSION_BUS_ADDRESS` / `XDG_RUNTIME_DIR` into the unit with `--setenv` so
the PIN dialog still appears. The unit stops at the file-backed signing hold; the key holder
runs the exact approval command printed in its journal from a separate terminal. Note that
a `--user` unit survives a client restart but not a logout or reboot unless lingering is
enabled for the account.

**Full flag surface:** `--dry-run`, `--archive-dir`, `--gpg-key NK1|NK2` (the subkey
aliases `S1`/`S2` are accepted too), `--skip-sources`, `--skip-transparency`, `--skip-sign`,
`--keep-previous N`, `--accept-capacity-risk`, `--iso-sha256 FILE`,
`--wiki-switching-page FILE`, `--sign-approval-file FILE`, and
`--sign-hold-timeout SECONDS`. Run `/usr/bin/bash
/mnt/intergenos/scripts/publish-repo.sh --help` for the authoritative list.

---

## 3a. Republishing a CHANGED package — bump `release:` (enforced)

When you republish a package whose **`version` is unchanged** but whose **content
changed** (a recipe fix rebuilt at the same upstream version), you MUST bump
`release:` in its `package.yml` before rebuilding. The index carries
`version` + `release`, and the client upgrade decision (`pkm.version.compare`)
orders by version then release — so a same-version republish that does **not**
bump release is **invisible to `pkm upgrade`** (the client sees the remote
`(version, release)` as equal to what's installed → "not upgradable", and the
fix never reaches it) and silently overwrites the published bytes under an
identifier clients already trust.

`publish-repo.sh` **enforces this with a fail-closed preflight**: every staged
archive whose bytes differ from the live `current/` entry must be **strictly
newer** in `(version, release)`; otherwise the publish aborts with a "bump
`release:`" message before the signing ceremony. (The gate is skipped on
`--skip-sign`, which reuses an already-vetted index.) This depends on the index
carrying `release` — `pkm/repo.py` emits it from each archive's `.PKGINFO`
`pkgrel`.

## 4. Post-publish — switch clients on

The first signed publish has landed, so this is now the steady state, not a
switch: repo-index signature verification is **mandatory** and unconditionally
on in `pkm/repo.py` regardless of `gpg_verify`. The key is accepted only as
`true` (or omitted); an explicit `gpg_verify = false` is **refused** at config
load (PKM-A21) — pkm will not silently verify while telling the user it is off.
`/etc/pkm/repos.conf` (shipped by
[`../../packages/core/pkm/build.sh`](../../packages/core/pkm/build.sh)) may
leave `gpg_verify` commented out (default on) or set it `true`; both verify.

---

## 5. Smoke test

From a client (live ISO or installed system) against the live mirror:

```bash
pkm sync          # fetch + GPG-verify InterGenOS.db.sig, then trust per-package SHA-256
pkm install <pkg> # downloads an archive, verifies SHA-256 against the trusted index
```

A clean `pkm sync` that verifies the signature against `/etc/pkm/trusted.gpg` and a
verified install is the end-to-end confirmation.

---

## 6. Ongoing verification

`scripts/mirror-verify.sh` runs daily on the mirror host (signature check → per-file SHA-256 walk
→ stray-file scan; MAILTO alerts on drift). It fails preflight until `current/` exists, so
it goes green only after the first publish. Re-run it manually right after the first
publish to confirm the live set verifies.

---

## 7. Manual verification by anyone

Security is not first. It is only. Every step is reproducible by hand against the
published master public key, with no opaque steps:

```bash
curl -O https://repo.intergenos.org/x86_64/current/InterGenOS.db
curl -O https://repo.intergenos.org/x86_64/current/InterGenOS.db.sig
gpg --verify InterGenOS.db.sig InterGenOS.db          # signed by S1/S2, certified by master
# then sha256sum any archive against its entry in the index
```
