# Forge: The InterGenOS Installer

This document details `Forge`, the install orchestrator for InterGenOS. Forge transitions the system from a live environment (where `igos-build` constructs packages) to a deployed, bootable, user-configured operating system on a target disk.

Forge resides primarily in `installer/` and follows a split frontend/backend architecture, united by a declarative state model.

## Architectural Design

Forge operates on three foundational principles:
1.  **Declarative Intent**: Frontends collect user choices and emit a serialized YAML state representing the desired system configuration. The backend consumes *only* this YAML, along with necessary security credentials (passwords), to execute the install.
2.  **Anti-Supply-Chain Integrity**: A strict, un-bypassable verification gate (`PHASE_VERIFY`) runs before any disk writes occur. This checks cryptographic signatures on all package archives to detect tampering.
3.  **Idempotent Phasing**: The install pipeline is broken into distinct, linear phases. Failures halt the pipeline and execute best-effort unmounts, surfacing the exact point of failure.

## The Dual Frontends

Forge ships with two user-facing frontends that execute identically against the backend.

### The TUI (`installer/frontend/tui.py`)
A `dialog`-based Text User Interface. 
*   Ideal for SSH-based installs, headless servers, or users preferring keyboard navigation.
*   Emits an `install.yaml` and a parallel `install-io.yaml` (containing local paths and interactive state) that the backend orchestrator consumes.

### The GUI (`installer/frontend/gui/`)
A modern GTK4 / `libadwaita` application.
*   Built around a multi-screen wizard flow (`welcome`, `keyboard_locale`, `disk`, `user`, `confirm`, `progress`, `done`).
*   Implements `installer/frontend/gui/state.py` to accumulate selections as the user navigates screens.
*   Handles security-critical interactions, such as the `integrity_dialog.py`, which prevents copy-pasting of override phrases to ensure deliberate user consent during hash mismatches.

## The Backend Orchestrator (`installer/backend/install.py`)

The `run_install()` function is the entry point for the backend. It consumes the YAML state and executes the following 11-phase pipeline (`PHASE_ORDER`):

1.  **Validate (`PHASE_VALIDATE`)**: Verifies the YAML configuration structure and executes deep validations (e.g., regex validation of the requested hostname via `_validators.py`).
2.  **Verify (`PHASE_VERIFY`)**: *Crucial security gate*. Computes the SHA-256 hash of every `.igos.tar.gz` package archive intended for deployment and verifies it against the `intergenos-archive-manifest.txt`, which is cryptographically signed by the release keys. A mismatch halts the installer *before* partitioning begins, unless the user provides explicit, typed confirmation to override.
3.  **Partition (`PHASE_PARTITION`)**: Wipes the target disk, creates a new partition table (EFI or BIOS), and formats the filesystems.
4.  **Mount (`PHASE_MOUNT`)**: Mounts the newly created root and boot partitions at a staging path (e.g., `/mnt/target`).
5.  **Virtual FS (`PHASE_VIRTUAL_FS`)**: Bind-mounts the host's `/dev`, `/proc`, and `/sys` into the staging path, preparing for `chroot` operations.
6.  **Packages (`PHASE_PACKAGES`)**: Delegates to `pkm` (via `packages.py`). Thread-queued execution extracts and registers every package specified in the YAML `package_groups`. Package installation failures are tracked but *do not* abort the install, surfacing a partial state instead.
7.  **Config (`PHASE_CONFIG`)**: Generates system-level configuration files (`/etc/fstab`, locale settings, timezone links) based on the YAML intent.
8.  **Users (`PHASE_USERS`)**: Sets the root password and creates the initial unprivileged user account.
9.  **MOK / Secure Boot (`PHASE_MOK`)**: If on an EFI system, generates a Machine Owner Key (MOK) keypair within the target environment.
10. **Bootloader (`PHASE_BOOTLOADER`)**: Installs GRUB to the target disk. If EFI, GRUB is signed using the previously generated MOK keypair.
11. **Hooks (`PHASE_HOOKS`)**: Runs any package-specific post-install scripts (e.g., updating font caches, compiling gsettings schemas).
12. **Services (`PHASE_SERVICES`)**: Enables necessary `systemd` services for the target environment.
13. **Cleanup (`PHASE_CLEANUP`)**: Unmounts virtual filesystems, unmounts the target disk, copies the integrity audit log, and reports success.

## Error Handling and Resilience

Forge prioritizes safe failure modes. 
*   **Context Managers**: Resource-heavy operations (like mounts) are wrapped in context managers (`installer/backend/hooks.py`) to ensure unmounting occurs even on exception.
*   **Phase Tracking**: The `InstallResult` object tracks `phase_completed`. If an exception occurs, the orchestrator executes a cleanup block (`_PHASES_NEEDING_UNMOUNT`) depending on how far the install progressed. 
*   **User Transparency**: Errors, especially those relating to the trust chain (like audit log copy failures), emit non-fatal warnings directly to `InstallResult.warnings` to be surfaced on the frontend's final screen, ensuring the user maintains full visibility over the deployment state.
