# Forge: The InterGenOS Installer

Forge is the install orchestrator for InterGenOS. It transitions the system from a live environment, where the `igos-build` build system constructs packages, to a deployed, bootable, user-configured operating system on a target disk.

Forge resides primarily in `installer/` and follows a split frontend/backend architecture united by a declarative state model.

## Architectural Design

Forge operates on three foundational principles:
1.  **Declarative intent**: Frontends collect user choices and emit a serialized YAML state representing the desired system configuration. The backend consumes that YAML, along with the necessary credentials (passwords), to execute the install.
2.  **Supply-chain integrity**: A verification gate (`PHASE_VERIFY`) runs before any disk writes occur. It checks the cryptographic integrity of every package archive against the signed release manifest to detect tampering, and halts the install before partitioning on any mismatch.
3.  **Phased execution**: The install pipeline is broken into distinct, linear phases. A failure halts the pipeline and performs best-effort unmounts, surfacing the exact point of failure.

## The Dual Frontends

Forge ships with two user-facing frontends that execute identically against the backend.

### The TUI (`installer/frontend/tui.py`)
A `dialog`-based text user interface.
*   Suited to SSH-based installs, headless servers, or users who prefer keyboard navigation.
*   Emits the declarative `install.yaml` (written to `/var/lib/forge/install.yaml`) and, separately, an interactive-state structure (`install_io`, holding the target disk and credentials) that the backend orchestrator consumes alongside the YAML.

### The GUI (`installer/frontend/gui/`)
A GTK4 / `libadwaita` application.
*   Built around a multi-screen wizard flow (`welcome`, `keyboard_locale`, `disk`, `user`, `packages`, `confirm`, `progress`, `done`).
*   Uses `installer/frontend/gui/state.py` to accumulate selections as the user navigates screens.
*   Handles security-critical interactions such as `integrity_dialog.py`, which disables paste (clipboard, drag-and-drop, and the right-click menu) on the override-phrase entry. The phrase must be typed in full, ensuring deliberate user consent before any integrity-check override is accepted.

## The Backend Orchestrator (`installer/backend/install.py`)

The `run_install()` function is the entry point for the backend. It consumes the YAML state and the interactive-state structure, then executes the following 13-phase pipeline (`PHASE_ORDER`):

1.  **Validate (`PHASE_VALIDATE`)**: Verifies the YAML configuration structure and executes deep validations (e.g., regex validation of the requested hostname via `_validators.py`).
2.  **Verify (`PHASE_VERIFY`)**: The core security gate. It computes the SHA-256 hash of every package archive intended for deployment and verifies it against `intergenos-archive-manifest.txt`, which is cryptographically signed by the release keys. A mismatch halts the installer *before* partitioning begins, unless the user provides explicit, typed confirmation to override.
3.  **Partition (`PHASE_PARTITION`)**: Wipes the target disk, creates a new partition table (EFI or BIOS), and formats the filesystems.
4.  **Mount (`PHASE_MOUNT`)**: Mounts the newly created root and boot partitions at a staging path (e.g., `/mnt/target`).
5.  **Virtual FS (`PHASE_VIRTUAL_FS`)**: Bind-mounts the host's `/dev`, `/proc`, and `/sys` into the staging path, preparing for `chroot` operations.
6.  **Packages (`PHASE_PACKAGES`)**: Delegates to `pkm` (via `packages.py`). Thread-queued execution extracts and registers every package specified in the YAML `package_groups`. Individual package failures are tracked but do not abort the install; Forge surfaces a partial state instead.
7.  **Config (`PHASE_CONFIG`)**: Generates system-level configuration files (`/etc/fstab`, locale settings, timezone links) based on the YAML intent. If the live session has an active Wi-Fi connection, the user is offered the option to carry it onto the installed system so the first boot arrives already connected; system-stored and open profiles carry over, while keyring-scoped and enterprise (802.1X) profiles are skipped since their secrets don't exist on the target.
8.  **Users (`PHASE_USERS`)**: Sets the root password and creates the initial unprivileged user account.
9.  **MOK / Secure Boot (`PHASE_MOK`)**: If on an EFI system, generates a Machine Owner Key (MOK) keypair within the target environment.
10. **Bootloader (`PHASE_BOOTLOADER`)**: Installs GRUB to the target disk. If EFI, GRUB is signed using the previously generated MOK keypair.
11. **Hooks (`PHASE_HOOKS`)**: Runs any package-specific post-install scripts (e.g., updating font caches, compiling gsettings schemas).
12. **Services (`PHASE_SERVICES`)**: Enables necessary `systemd` services for the target environment.
13. **Cleanup (`PHASE_CLEANUP`)**: Unmounts virtual filesystems, unmounts the target disk, copies the integrity audit log, and reports success.

## Error Handling and Resilience

Forge prioritizes safe failure modes.
*   **Context managers**: Resource-heavy operations such as mounts are wrapped in context managers (`installer/backend/hooks.py`) so that unmounting occurs even when an exception is raised.
*   **Phase tracking**: The `InstallResult` object tracks `phase_completed`. If an exception occurs, the orchestrator runs a cleanup block (`_PHASES_NEEDING_UNMOUNT`) scoped to how far the install progressed.
*   **User transparency**: Errors, especially those relating to the trust chain such as audit-log copy failures, emit non-fatal warnings to `InstallResult.warnings`. These appear on the frontend's final screen, keeping the user fully informed about the deployment state.
