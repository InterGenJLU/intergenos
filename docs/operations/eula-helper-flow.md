# EULA install-helper flow

**Status:** Active as of v1.0-dev; first instance shipped 2026-05-28 (`nvidia`).
**Owner:** InterGenJLU | **License:** GPL-3.0-or-later

## Why this exists

Some upstream packages bundle proprietary userspace under a vendor EULA
that must be accepted before installation. Examples include the NVIDIA
proprietary userspace libraries (`libGLX_nvidia`, `libEGL_nvidia`,
`nvidia-smi`), the Adobe-style "click-through accept" terms attached to
certain font packs, and any vendor that ships a `LICENSE` blob
alongside their distribution tarball with a "by installing you accept"
clause.

InterGenOS does not maintain these EULA texts in-tree; a vendor's
license text inside the InterGenOS repository is not the project's
responsibility, and a hand-maintained copy can drift from the vendor's
terms. Instead, the package's `package.yml` declares an
`eula_helper: <name>` field, and the package's `build.sh` stages the
vendor's OWN license text — extracted from the vendor's own sha-pinned
release artifact at package build time — alongside the helper. `pkm`
runs the named helper BEFORE the package install proceeds. The helper
presents the bundled EULA in a `prompt_toolkit` full-screen pager,
captures ACCEPT or DECLINE, and either writes a system-wide acceptance
marker (ACCEPT) or aborts the install (DECLINE).

> **Design supersession (PI-Z15, operator GO 2026-07-06):** the
> original design fetched the EULA live from the vendor at install
> time. It proved unrunnable-by-construction on the first live gate run
> (Zephyrus GE-02): the assumed
> `download.nvidia.com/.../<ver>/LICENSE` URL pattern never existed
> (HTTP 404 across driver generations). Bundling from the vendor's own
> versioned installer keeps the no-maintenance property (the text is
> never authored or updated by us — it re-stages automatically on every
> version bump), matches what the vendor's own installer displays at
> its interactive accept, and removes a network dependency from a gate
> that must work on offline installs (a fresh Forge target installs
> from local archives).

This is a hybrid model: the flow blends the proprietary download-helper
pattern (`/usr/bin/igos-install-<name>` for software such as Chrome,
VS Code, and Claude Code) with the regular archive-install pipeline. The
package ships a real archive (signed userspace blobs, GSP firmware,
kernel-module source, systemd units, and udev rules) AND wires a
pre-install EULA gate. Neither pattern alone covers the case.

## Pieces

| Piece                                                       | Role                                                                |
| ----------------------------------------------------------- | ------------------------------------------------------------------- |
| `package.yml: eula_helper: <name>`                          | Declares the helper to run before install                           |
| `igos-build/parser.py`                                      | Reads `eula_helper` into the `Package` dataclass                    |
| `igos-build/tracker.py`                                     | Emits `eula_helper=<name>` into `.PKGINFO` inside the archive       |
| `pkm/repo.py: _parse_pkginfo`                               | Reads `eula_helper` from `.PKGINFO` into the meta dict              |
| `pkm/installer.py: PackageInstaller.install`                | Pre-install gate: read `.PKGINFO`, run the helper, check exit code  |
| `pkm/installer.py: _find_eula_helper / _run_eula_helper`    | Helper resolution + execution                                       |
| `/usr/lib/intergen/eula-helpers/<name>`                     | The helper executable (the package's `build.sh` installs it)        |
| `/var/lib/intergen/eula/<suffix>.accepted`                  | Marker JSON file (presence = accepted)                              |
| `/var/lib/intergen/eula/<suffix>-<timestamp>-<sha>.txt`     | Verbatim text of the accepted EULA (audit transcript)               |

## End-to-end flow

1. User runs `pkm install nvidia`.
2. `pkm` resolves dependencies + downloads the archive.
3. `pkm.installer.PackageInstaller.install` opens the archive and
   reads `.PKGINFO`. It finds `eula_helper=nvidia-eula`.
4. `pkm` runs `/usr/lib/intergen/eula-helpers/nvidia-eula`. Subprocess
   env is stripped to `HELPER_ENV_ALLOWLIST` (same H-024 defense the
   regular helper path applies). The helper inherits stdin / stdout /
   stderr unchanged so the pager renders correctly.
5. Helper checks the system-wide marker
   `/var/lib/intergen/eula/nvidia-userspace.accepted`:
   * Present + valid JSON -> exit 0 immediately. Acceptance is captured
     once, on the first install, and is not re-prompted thereafter.
   * Absent -> continue.
6. TTY gate. `stdin` AND `stdout` must both be TTYs. Non-TTY -> exit 4
   with a clear message telling the user to run from an interactive
   shell.
7. Helper prints the banner (read from
   `/usr/lib/intergen/eula-helpers/banner.txt`).
8. Helper reads the bundled EULA sidecar staged next to it at package
   build time (e.g.
   `/usr/lib/intergen/eula-helpers/nvidia-eula.LICENSE`, extracted from
   the NVIDIA `.run`'s own LICENSE file). Missing / empty / unreadable
   sidecar -> exit 2 with a clear message naming the install media as
   corrupted (fail-closed — the sidecar ships in the same
   signature-verified archive as the driver bits).
9. Helper presents the EULA in a `prompt_toolkit` full-screen pager:
   * Scrollable body (Up/Down/PgUp/PgDn/Home/End).
   * ACCEPT button (pre-focused, so the selection is obvious and no one
     has to search for how to make the selector appear).
   * DECLINE button.
   * Tab cycles focus between ACCEPT, DECLINE, and the EULA body.
   * Enter activates the focused button.
   * Esc maps to DECLINE (consistent with Forge installer UX).
10. ACCEPT path: helper writes
    `/var/lib/intergen/eula/nvidia-userspace.accepted` (marker JSON with
    `accepted_at`, `eula_sha256`, `eula_source`,
    `eula_version_string`, `transcript_path`, `captured_pii: "none
    (no username, hostname, or machine-id)"`) AND
    `/var/lib/intergen/eula/nvidia-userspace-<ts>-<sha>.txt` (verbatim
    EULA text). Both writes are atomic (tempfile + `os.replace`).
    Helper exits 0.
11. DECLINE path: helper prints
    "NVIDIA EULA declined — InterGenOS will not install the NVIDIA
    proprietary userspace. The open-source nouveau driver remains your
    active GPU driver." and exits 1.
12. `pkm.installer.PackageInstaller.install` reads the helper's exit
    code:
    * 0 -> proceed with staging extract + deploy + DB transaction +
      hooks. No on-disk changes happened before this point.
    * non-zero -> abort with a plain-English message. No on-disk
      changes happened at all. The package is not registered.

## Exit-code contract (consumed by `pkm.installer._run_eula_helper`)

| Exit code | Meaning                                                                                |
| --------: | -------------------------------------------------------------------------------------- |
| 0         | Marker present OR newly accepted; pkm proceeds with install.                           |
| 1         | User declined the EULA in the pager.                                                   |
| 2         | Could not read the bundled EULA text (missing/empty sidecar — corrupted install media). |
| 3         | Could not write the marker / transcript (filesystem error).                            |
| 4         | Interactive TTY required (helper invoked from cron / scripted install / non-tty shell).|

Any other non-zero code is treated as "unexpected" and surfaced to the
user with the code and the helper's stderr.

## First-install resolution + the Forge boundary (PI-Z6, 2026-07-06)

Two wiring gaps surfaced on the first NVIDIA-hardware Forge install:

1. **First-install chicken-and-egg.** The helper ships inside the very
   package it gates, so on a machine that never had the package there is no
   filesystem copy for `_find_eula_helper` to resolve — the gate was
   unrunnable-by-construction on every first install. `pkm` now falls back
   to extracting the `usr/lib/intergen/eula-helpers/` subtree out of the
   archive being installed (same PEP 706 'data' filter as every other pkm
   extraction, private tempdir, removed after the run). The archive is the
   same signature-verified artifact the install is about to deploy, so
   running its bundled helper crosses no trust boundary the install itself
   does not already cross. A package that declares `eula_helper` but ships
   no helper in its archive is refused as broken/tampered.

2. **Forge never runs EULA-gated installs.** The helper requires an
   interactive TTY (exit 4 without one), which a Forge install can never
   provide — so Forge OMITS any `eula_helper`-declaring package from its
   non-interactive install set wholesale, logs + traces each omission with
   the post-boot command (`pkm install <name>`), and the install-set
   silent-loss audit treats the omission as intentional policy, not loss.
   The user accepts the EULA in a real terminal after first boot, exactly
   as this document's flow describes.

## Direct invocation for testing

`pkm install-helper nvidia-eula` runs the helper directly without
going through a full `pkm install nvidia` first. Useful for verifying
the pager renders correctly on a given terminal + confirming the
marker write succeeds. `pkm install-helper` resolves the name through
`_find_eula_helper` first, then falls back to the
`/usr/bin/igos-install-<name>` proprietary-helper surface.

## Marker file format

```json
{
  "accepted_at": "2026-05-28T19:30:00Z",
  "captured_pii": "none (no username, hostname, or machine-id)",
  "eula_sha256": "<full hex>",
  "eula_source": "bundled: LICENSE from NVIDIA-Linux-x86_64-<ver>.run (staged at package build)",
  "eula_version_string": "NVIDIA-Linux-x86_64-580.159.04",
  "transcript_path": "/var/lib/intergen/eula/nvidia-userspace-2026-05-28T19:30:00Z-<short-sha>.txt"
}
```

## Security alignment

Security is not first. It is only. The EULA helper reflects that posture:

* Marker carries `sha256` and `eula_source` provenance so
  post-acceptance verification is possible: a third party can extract
  LICENSE from the same versioned vendor artifact (sha-pinned in the
  package's `package.yml`), hash it, and compare.
* No PII in the marker beyond the `accepted_at` timestamp (no
  username, no hostname, no MAC address, no machine-id). The
  `captured_pii` field explicitly enumerates the absence so a future
  reader can audit the schema and see that the gap is intentional.
* Atomic writes mean partial-write states are not observable to a
  subsequent install attempt.
* `HELPER_ENV_ALLOWLIST` is applied to the helper subprocess (the same
  defense `_run_helper` applies). Inherited `LD_PRELOAD`,
  `LD_LIBRARY_PATH`, `*_PROXY`, and `PYTHONPATH` cannot redirect the
  helper's execution.
* The EULA text ships inside the same signature-verified archive as
  the package payload itself — no network trust surface at accept
  time at all (PI-Z15 superseded the live TLS fetch).
* Sanity cap on EULA text size (1 MiB) — a larger sidecar means
  corrupted install media and the helper refuses to page it.
* No CLI flag overrides the marker check. Re-accepting requires the
  user to delete the marker manually, surfacing intent.

## User-control alignment

InterGenOS aims to give you a system you understand, can modify, and can
trust. The EULA helper keeps the user in control:

* Banner and pager footer enumerate every key binding so the user
  never has to guess what to press.
* ACCEPT is pre-focused so a user who reflexively hits Enter after
  reading does not accidentally cancel.
* The EULA text is the vendor's own document from the exact versioned
  installer the package was built from — the terms that actually
  govern the bits being installed, bit-identical to what the vendor's
  own installer displays, never authored or edited by InterGenOS.
* Every error message is plain English and explains what each choice
  leads to ("DECLINE = abort install and stay on nouveau").

## Cross-distro reference

| Distro    | Pattern                                                                       |
| --------- | ----------------------------------------------------------------------------- |
| Arch      | `nvidia-utils` ships the EULA inline; install does not re-prompt              |
| Fedora    | `akmod-nvidia` (RPMFusion) treats EULA acceptance as out-of-band              |
| Debian    | `nvidia-driver` uses `debconf` for one-shot click-through                     |
| Ubuntu    | `ubuntu-drivers` shows a checkbox in the GUI; CLI requires `--accept`         |
| InterGenOS| Bundled-from-vendor-installer + full-screen pager + ACCEPT-pre-highlighted + permanent transcript|

InterGenOS's deviations from the other distributions:
* The presented text is provably the vendor's own (staged from the
  sha-pinned vendor artifact at build time), and acceptance works
  offline — closest to Arch's inline-EULA shape, plus the transcript.
* A verbatim-text transcript captured at acceptance time means
  post-hoc verification is possible.
* No PII is captured, consistent with the project's security-only
  posture.
