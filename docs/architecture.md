# InterGenOS Architecture Overview

Welcome to InterGenOS. This document provides a high-level architectural overview of the system, designed to orient new contributors to the repository structure, build pipeline, packaging system, AI-runtime stack, and installation flow.

InterGenOS is an AI-integrated Linux distribution built entirely from source. It emphasizes a strict "user-control posture" and robust "anti-supply-chain" defense-in-depth mechanisms. Every component is designed to ensure the user retains ultimate authority over their machine and data.

## Repository Structure

The repository is organized into distinct domain-specific directories:

*   **`packages/`**: Contains the build recipes (YAML and shell scripts) for all software included in the distribution, organized into tiers.
*   **`igos-build/`**: The Python-based build system responsible for parsing recipes, resolving dependencies, and orchestrating isolated package builds.
*   **`pkm/`**: The InterGenOS package manager, handling installation, removal, and integrity verification of binary archives.
*   **`installer/`**: The `Forge` installer system, comprising a declarative Python backend and dual frontend interfaces (TUI and GTK4 GUI).
*   **`intergen/`**: The core AI assistant runtime, including model management, priority routing, safety classification, and D-Bus integration.
*   **`scripts/`**: Build orchestrators, environment health checks, chroot management, and release ceremony utilities.
*   **`tests/`**: The test suite covering `igos-build`, `pkm`, `installer`, and other critical paths.

## Package Tier System

InterGenOS organizes its 700+ packages into a strict tier system. This hierarchy dictates the build order and ensures a clean separation of concerns from the foundational compiler tools up to the user-facing desktop applications.

1.  **Toolchain (`packages/toolchain/`)**: The bootstrap compilers and core utilities (e.g., GCC, binutils, glibc, bash). These are cross-compiled in multiple passes to establish an isolated environment independent of the host OS.
2.  **Core (`packages/core/`)**: The essential components of the operating system, including the Linux kernel, systemd, networking tools, and critical libraries. This tier yields a minimal, bootable CLI system.
3.  **Base (`packages/base/`)**: Standard system utilities and services that form the expected POSIX-compliant environment but aren't strictly required for boot.
4.  **Desktop (`packages/desktop/`)**: The graphical stack, including Wayland, GNOME/GTK components, X11 compatibility layers, and essential desktop libraries.
5.  **Extra (`packages/extra/`)**: User applications, web browsers, media players, and additional development tools.
6.  **AI (`packages/ai/`)**: The specialized runtime components for the InterGen assistant, including `llama.cpp` and the `intergen` daemon itself.

## Build Pipeline

The InterGenOS build pipeline is orchestrated primarily by `igos-build/`. The system constructs the OS from source code in a highly controlled manner.

### 1. Host Preparation and Source Fetching
The build begins on a host system. `scripts/host-check.py` verifies that the host meets the necessary prerequisites. `scripts/download-sources.py` retrieves all upstream source tarballs and verifies their SHA256 checksums against the package definitions.

### 2. Toolchain Bootstrap
The build orchestrator (`scripts/build-intergenos.sh`) sets up an isolated environment (often referred to as LFS - Linux From Scratch pattern). The toolchain is built in `/mnt/intergenos` (or similar staging area). This isolates the resulting binaries from the host's libraries.

### 3. Chroot Execution
Once the initial toolchain is viable, the orchestrator chroots into the staging environment (`scripts/chroot-enter.sh`). From this point onward, the build relies exclusively on the newly compiled toolchain.

### 4. Dependency Graph Resolution
The Python module `igos-build/graph.py` parses all `package.yml` files, resolving build, host, and runtime dependencies into a directed acyclic graph (DAG) to determine the exact build order.

### 5. Package Compilation (`igos-build/builder.py`)
For each package, `builder.py`:
*   Extracts the source archive using hardened extraction flags (preventing path traversal).
*   Executes the build phases (configure, build, check, install) defined by the package's build style (e.g., Autotools, Meson, CMake, or Custom shell functions).
*   Stages the output into a `DESTDIR`.

### 6. Archiving and Tracking
After staging, `builder.py` captures a file manifest and creates a `.igos.tar.gz` archive. This archive represents the deployable binary artifact for that package.

## The Package Manager (`pkm`)

`pkm` is responsible for managing the state of the installed system.

*   **SQLite Database**: It maintains a local SQLite database tracking installed packages, file ownership, file hashes, and dependency relationships.
*   **Supersede Semantics**: `pkm` supports atomic "supersedes" operations. When a package is upgraded, the transaction cleanly transfers file ownership from the old record to the new record. Overlapping files are reassigned, and the old package is marked as superseded.
*   **Integrity Verification**: `pkm` can verify the integrity of the installed system by hashing files on disk and comparing them against the authoritative hashes stored in the database.
*   **Content vs. Config**: `pkm` distinguishes between standard binaries/data and configuration files (typically in `/etc`). Configuration files are protected and handled differently during upgrades to prevent silent overwrites of user modifications.

## Installer Flow (`Forge`)

The `Forge` installer (`installer/`) is the mechanism for deploying the compiled artifacts onto a target drive. It supports a dual-frontend architecture over a unified backend.

*   **Frontends**: A text-based UI (`installer/frontend/tui.py`) and a graphical GTK4 UI (`installer/frontend/gui/`) capture user intents (disk selection, locale, usernames, passwords). Both frontends produce a declarative YAML state.
*   **Verification Gate (PHASE_VERIFY)**: Before touching the disk, the backend executes an anti-supply-chain integrity gate. It verifies the cryptographic signatures of the package archives against a trusted manifest. This ensures the install media hasn't been tampered with.
*   **Backend Execution (`installer/backend/install.py`)**: The backend consumes the YAML intent and executes a 13-phase pipeline:
    1.  Validate inputs.
    2.  Verify archive integrity.
    3.  Partition and format the target disk.
    4.  Mount the target filesystems.
    5.  Set up virtual filesystem bind-mounts (`/dev`, `/proc`, `/sys`).
    6.  Install packages via `pkm` (queue-threaded).
    7.  Generate system configuration (fstab, locale, timezone).
    8.  Configure users and passwords.
    9.  Generate MOK keys (for Secure Boot, if EFI).
    10. Install the bootloader (GRUB).
    11. Run post-install hooks.
    12. Enable system services.
    13. Cleanup — unmount virtual + target filesystems.

## InterGen AI-Runtime Stack

The distinguishing feature of InterGenOS is its deeply integrated AI assistant, designed to operate entirely locally.

*   **Hardware-Tier Model Catalog**: The system provisions Qwen3.5 models based on local hardware capabilities (canonical catalog in `intergen/model_manager.py`):
    *   Tier 1 (Qwen3.5-2B): Optimized for basic interactions and constrained memory (<8 GB RAM).
    *   Tier 2 (Qwen3.5-9B): The standard capable model for coding and reasoning (8-15 GB RAM).
    *   Tier 3 (Qwen3.5-35B-A3B MoE): High-end model for advanced analysis (16 GB+ RAM, discrete GPU).
*   **Llama Manager (`intergen/llama_manager.py`)**: Handles the lifecycle (start, stop, load, unload) of the `llama.cpp` inference engine.
*   **Priority Router (`intergen/router.py`)**: A critical component managing concurrent demands on the AI engine. It utilizes an 8-priority queue system to arbitrate requests (e.g., user-interactive chat vs. background log summarization).
*   **Safety Classifier**: All prompts and outputs pass through a safety classifier that determines actionability (AUTO, CONFIRM, BLOCKED) based on the user-control posture.
*   **D-Bus Integration**: The `dbus_daemon.py` exposes the assistant's capabilities to the broader desktop environment via D-Bus, allowing other applications to request completions or semantic searches securely.
*   **MCP Client**: Facilitates communication via the Model Context Protocol, allowing the AI to interact with specialized external tools or services when authorized.

This architecture ensures that from the lowest-level compiler flag to the user-facing AI chat, InterGenOS remains auditable, secure, and under the user's control.
