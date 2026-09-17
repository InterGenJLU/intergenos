# Package Management on InterGenOS

InterGenOS uses its own package manager, **pkm**, to securely install, update, query, and remove software. It is designed to be fast, predictable, and rigidly aligned with our security-only posture.

## 1. At A Glance

Every time you interact with pkm, it adheres to these principles:
- **Trust by verification**: pkm fetches the InterGenOS.db index from the official mirror (repo.intergenos.org), cryptographically verifies its PGP signature against the bundled release key, and hashes every downloaded `.igos.tar.gz` archive before installation.
- **Silence is golden**: pkm does not phone home in the background. Nothing downloads or installs package updates on a schedule, and there is no anonymous telemetry or usage analytics. One pkm timer ships enabled — `pkm-check-updates.timer`, which fires `pkm check-updates` once a day with a randomized delay of up to four hours. It reads the index already cached on your machine from your last `pkm sync` and compares it against the installed database; it makes no network connection and it never installs anything. Its only output is a summary file at `/var/lib/pkm/available-updates.json` that the desktop notifier and the login message read. Turn it off with `sudo systemctl disable --now pkm-check-updates.timer`.
- **Explicit consent**: Packages that download proprietary vendor software under its own end-user license (for example Chrome, VS Code, Discord, or Spotify) pause with a banner naming the vendor license, and pkm will not accept it on your behalf; a non-interactive install refuses outright rather than silently agreeing to it.

## 2. Daily Commands

Here are the standard commands you will use to manage your system:

### Syncing the Index
```bash
sudo pkm sync
```
Fetches the latest package metadata (InterGenOS.db) from the mirror and verifies the cryptographic signature. It does not install updates.

### Searching for Packages
```bash
pkm search database
```
Queries your *local* synced index for packages matching the term. Because the index is local, this command is fast and does not require a network connection.

### Installing Software
```bash
sudo pkm install podman
```
Downloads and installs the specified package. pkm automatically calculates and fetches all prerequisites (for example, installing podman will automatically pull down crun, conmon, and netavark). Before extracting, the archive's SHA-256 hash is verified against the signed index.

### Upgrading Installed Software
```bash
sudo pkm upgrade --all
```
Compares your installed packages against the synced index and installs newer versions for any packages that have them available. `pkm sync` (above) only refreshes the index; this is the command that actually changes what's on disk. A bare `pkm upgrade` with no package names and no `--all` refuses to run rather than silently mass-modifying the system — name specific packages instead if you only want to upgrade those (for example, `sudo pkm upgrade firefox`).

When the package manager itself is among the packages with a newer version, `--all` upgrades pkm **first** and then re-runs itself under the new release for the rest of the queue (it says so before asking for your confirmation, which covers both halves). The remaining packages are therefore always installed by the code that ships with the new pkm, never by the code that is being replaced.

To move an installed package forward from a local archive — a build the mirror does not serve yet — name the package and the file:
```bash
sudo pkm upgrade forge --archive /path/to/forge-1.0.igos.tar.gz --archive-trust loose
```
The archive's own metadata must name that package; an older build is refused unless you pass `--allow-downgrade`; every dependency the archive declares must already be installed (this command fetches nothing from the repository). The same restore point, rollback copy and configuration-file protection as a repository upgrade apply. `--archive-trust strict` (the default) requires the signed index to carry that exact archive, so a build ahead of the mirror needs `loose`, which says so in its output.

### Restarting Services After an Upgrade
```bash
pkm restart-services --list
sudo pkm restart-services --all
```
An upgraded service keeps running its old code until it is restarted. `--list` classifies the packages installed or upgraded **since this boot** (anything installed before the boot was loaded fresh at boot and is only counted) and prints the ones that need a restart or a reboot. `--all` restarts the running units of those packages — and only the units that started before their package was upgraded; a unit already restarted after the upgrade is reported as current and left alone. The units that carry your login session (the system bus, logind, the journal, the display manager) are **never** restarted by `--all`; a package whose only running units are such is reported as REBOOT REQUIRED instead, because a live restart would end your session. Naming a unit explicitly (`sudo pkm restart-services sshd.service`) restarts exactly that unit, with a warning first if it carries the session. `--all` refuses to act when the boot time cannot be read, rather than guessing.

### Removing Software
```bash
sudo pkm remove firefox
```
Uninstalls the named package. Unless you pass `--force`, pkm refuses when an
installed package still depends on it. Orphaned packages are a separate,
explicit operation: preview them with `pkm autoremove --dry-run`, then run
`sudo pkm autoremove` and confirm the list if you want them removed.

### Inspecting Installed Software
```bash
pkm list installed
```
Outputs a complete list of everything currently installed on your machine. `installed` is the default, so a bare `pkm list` produces the same result. You can also pass `available` or `upgradable` to list the packages in the synced index or only those with a newer version waiting.

### Verifying System Integrity
```bash
sudo pkm verify openssl
```
Checks the package's recorded filesystem state. Owned paths must exist,
ordinary payload files are compared by SHA-256, and configuration or
hook-generated files follow separately reported existence-only rules.
Unreadable or missing reference data is reported as undetermined rather than
as a pass.

### Package Metadata
```bash
pkm info valkey
```
Displays detailed metadata about a package. For an installed package, that is
its version, description, license, install date, dependencies and reverse
dependencies (what else on your system depends on it). For a package that is
available but not installed, `info` answers from the synced repository index —
version, tier, description, license, download size and checksum — and says
plainly that it is not installed, so you can read what a package is before
deciding to add it.

`info` (and its alias `show`) exits with status `0` when the package is
registered as installed, or `1` when it is not installed. Available package
metadata still prints with status `1`; an unknown name or unreadable index
keeps the plain not-installed message. This status describes package
registration, not file integrity or completion of a vendor download.

### Operation History

`pkm history` shows the 50 most recent operations, newest first. Use
`pkm history --limit 100` for a larger window or `pkm history --all` for the
complete record, including the earlier entries from an installation.
Add a package name to filter the record, for example
`pkm history firefox --all`. `--limit` takes a positive integer and cannot
be combined with `--all`. These commands only read the package database.

### Natural-Language Aliases

pkm accepts the command names you'd naturally reach for from other distros. Whichever feels right works:

| Canonical | Aliases |
|---|---|
| `sync` | `update`, `refresh` |
| `remove` | `uninstall` |
| `search` | `find` |
| `info` | `show` |
| `list` | `ls` |
| `files` | `contents` |
| `depends` | `deps` |

`pkm --help` renders each alias next to its canonical name. The dispatch resolves aliases to the canonical command, so the behavior is identical regardless of which name you type.

## 3. The Trust Chain

Security is foundational to pkm. The trust model has four checked steps:
1. The repository index (InterGenOS.db) is signed by the hardware-held release signing subkey.
2. The expected keys are pinned locally in `pkm/release-keys.json`.
3. When you run `pkm sync`, the signature is checked. If it fails, pkm immediately halts.
4. When you install an archive, its SHA-256 hash is checked against the verified index. A mismatch results in a hard rejection.

For a deeper dive into this trust model, read the [Repository Trust Model](../repository-trust.md) and the [Per-Archive Signature Decision](../architecture/per-archive-sig-decision.md).

## 4. What pkm Does NOT Do

Consistent with our [Security Defaults](security-defaults.md):
- **No Background Refresh**: pkm reaches the network only for an explicit command that needs remote metadata or payloads, such as `pkm sync`, `pkm install`, `pkm reinstall`, or an upgrade whose archive is not already cached.
- **No Telemetry**: We do not know what packages you install.
- **No Unattended Upgrades**: Your system remains exactly as it is until you explicitly command an update.

## 5. Comparison to Other Package Managers

pkm borrows concepts from other ecosystems but makes different security trade-offs:

| Feature | pkm (InterGenOS) | apt (Debian/Ubuntu) | pacman (Arch) | dnf (Fedora) |
|---|---|---|---|---|
| **Trust Model** | Signed Index Only | Signed Index (InRelease) | Signed Index / Per-Package Sigs | Signed Metadata / Per-Package Sigs |
| **Auto-Updates** | Never | Configurable (Unattended-Upgrades) | Configurable | Configurable |
| **Telemetry** | None | Popcon (Opt-in) | None | DNF countme (Opt-out/Opt-in) |
| **License Banners** | Halts on proprietary vendor EULA | Relies on repo separation (non-free) | Relies on AUR / user discretion | Relies on repo separation |

## 6. Under the Hood

For the curious:
- pkm is written in Python (see the `pkm/` directory in our source tree).
- Package archives use the `.igos.tar.gz` format.
- The local installation database is a standard SQLite file stored at `/var/lib/igos/pkm.db`. Per-package text manifests live alongside it at `/var/lib/igos/packages/`.
- Package replacement metadata is committed in one SQLite supersedes transaction. Filesystem deployment happens before that database commit and is not atomic across a power loss. Before an upgrade, pkm states whether a pre-transaction restore point or a cached rollback archive is available; do not infer rollback protection when it says neither is available.

## 7. Further Reading
- Looking for the database options? Check out the [Databases on InterGenOS](databases.md) overview.
- Just installed the OS? See the [Getting Started Guide](../getting-started.md).
