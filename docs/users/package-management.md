# Package Management on InterGenOS

InterGenOS uses its own package manager, **pkm**, to securely install, update, query, and remove software. It is designed to be fast, predictable, and rigidly aligned with our security-only posture.

## 1. At A Glance

Every time you interact with pkm, it adheres to these principles:
- **Trust by verification**: pkm fetches the InterGenOS.db index from the official mirror (epo.intergenos.org), cryptographically verifies its PGP signature against the bundled release key, and hashes every downloaded .igos.tar.gz archive before installation.
- **Silence is golden**: pkm does not phone home in the background. There is no auto-updater running on a timer, no anonymous telemetry, and no usage analytics.
- **Explicit consent**: If a package ships under a non-OSI license (like MongoDB or Redis), pkm will halt and display a banner requiring your explicit consent before downloading.

## 2. Daily Commands

Here are the standard commands you will use to manage your system:

### Syncing the Index
\\\ash
sudo pkm sync
\\\
Fetches the latest package metadata (InterGenOS.db) from the mirror and verifies the cryptographic signature. It does not install updates.

### Searching for Packages
\\\ash
pkm search database
\\\
Queries your *local* synced index for packages matching the term. Because the index is local, this command is fast and does not require a network connection.

### Installing Software
\\\ash
sudo pkm install cassandra
\\\
Downloads and installs the specified package. pkm automatically calculates and fetches all prerequisites (for example, installing cassandra will automatically pull down openjdk17 and nt). Before extracting, the archive's SHA-256 hash is verified against the signed index.

### Updating the System
\\\ash
sudo pkm update
\\\
Compares your installed packages against the synced index and upgrades any packages with newer versions available.

### Removing Software
\\\ash
sudo pkm remove firefox
\\\
Uninstalls the package. pkm will also identify and cleanly remove any orphaned dependencies that are no longer required by other software on your system.

### Inspecting Installed Software
\\\ash
pkm list --installed
\\\
Outputs a complete list of everything currently installed on your machine.

### Verifying System Integrity
\\\ash
sudo pkm verify openssl
\\\
Checks the installed files of a package against the expected SHA-256 hashes recorded in the manifest. This allows you to quickly detect configuration drift or tampered files.

### Package Metadata
\\\ash
pkm info valkey
\\\
Displays detailed metadata about a package, including its version, dependencies, architecture, and license.

## 3. The Trust Chain

Security is foundational to pkm. The trust model works seamlessly behind the scenes:
1. The repository index (InterGenOS.db) is signed by the offline InterGenOS master release key.
2. The expected keys are pinned locally in pkm/release-keys.json.
3. When you run pkm sync, the signature is checked. If it fails, pkm immediately halts.
4. When you install an archive, its SHA-256 hash is checked against the verified index. A mismatch results in a hard rejection.

For a deeper dive into this trust model, read the [Repository Trust Model](../repository-trust.md) and the [Per-Archive Signature Decision](../architecture/per-archive-sig-decision.md).

## 4. What pkm Does NOT Do

Consistent with our [Security Defaults](security-defaults.md):
- **No Background Refresh**: The system will not reach out to the network until you explicitly run pkm sync or pkm install.
- **No Telemetry**: We do not know what packages you install.
- **No Unattended Upgrades**: Your system remains exactly as it is until you explicitly command an update.

## 5. Comparison to Other Package Managers

pkm borrows concepts from other ecosystems but makes different security trade-offs:

| Feature | pkm (InterGenOS) | pt (Debian/Ubuntu) | pacman (Arch) | dnf (Fedora) |
|---|---|---|---|---|
| **Trust Model** | Signed Index Only | Signed Index (InRelease) | Signed Index / Per-Package Sigs | Signed Metadata / Per-Package Sigs |
| **Auto-Updates** | Never | Configurable (Unattended-Upgrades) | Configurable | Configurable |
| **Telemetry** | None | Popcon (Opt-in) | None | DNF countme (Opt-out/Opt-in) |
| **License Banners** | Halts on Non-OSI (SSPL/RSAL) | Relies on repo separation (non-free) | Relies on AUR / user discretion | Relies on repo separation |

## 6. Under the Hood

For the curious:
- pkm is written in Python (see the pkm/ directory in our source tree).
- Package archives use the .igos.tar.gz format.
- The local installation database is a standard SQLite file stored at /var/lib/igos/packages/.
- Package upgrades are treated as atomic "supersedes" transactions, ensuring your system is never left in a broken, half-installed state if an operation fails or loses power.

## 7. Further Reading
- Looking for a list of powerful applications to install? Check out the [Databases on InterGenOS](databases.md) overview.
- Just installed the OS? See the [Getting Started Guide](../getting-started.md).
