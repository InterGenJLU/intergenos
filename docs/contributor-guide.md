# InterGenOS Contributor Guide

Welcome to the InterGenOS project. This guide outlines the prerequisites, conventions, and operational procedures required to contribute effectively. We emphasize defense-in-depth, strict version control hygiene, and clear communication across our multi-agent fleet.

## Prerequisites and Build Host Requirements

To build InterGenOS from source or develop core components, your host machine must meet the following requirements:

*   **Operating System**: A modern Linux distribution (Ubuntu 24.04+ or Debian 12+ recommended).
*   **Architecture**: x86_64 or aarch64.
*   **Tools**: `git`, `python3` (3.11+), `bash`, `curl` or `wget`, `tar`, `xz`, `build-essential` (or equivalent compiler toolchain), `bison`, `gawk`, `texinfo`.
*   **Storage**: At least 50GB of free space on a fast SSD. Building the toolchain and all packages is I/O intensive.
*   **Permissions**: Root access via `sudo` is required to manage chroot environments and perform bind mounts.

Before beginning development, run the host environment checker:
```bash
python3 scripts/host-check.py
```

## Repository Conventions

Maintaining consistency across a large, auto-generated, and AI-assisted codebase is critical.

### Package Definitions (`package.yml`)
Every software component in InterGenOS is defined by a `package.yml` file located within its respective tier (`packages/<tier>/<name>/package.yml`).
*   **Fields**: Must include `name`, `version`, `description`, `license`, `source` (with URLs and `sha256` checksums), and `deps` (`build`, `host`, `runtime`).
*   **Build Styles**: The `build_style` field dictates how `igos-build` compiles the package (e.g., `autotools`, `meson`, `cmake`, `python-pep517`). 

### Build Scripts (`build.sh`)
If the standard build styles are insufficient, a package can declare `build_style: custom` and provide a `build.sh` script alongside the `package.yml`.
*   **Shell Strictness**: EVERY shell script MUST begin with `set -euo pipefail`. This is a non-negotiable defense-in-depth requirement to prevent silent failures.
*   **Functions**: `build.sh` must define bash functions corresponding to the build phases: `configure()`, `build()`, `check()`, and `install()` or `do_install()`.

### Branching and Commits
*   **Branch Naming**: Use descriptive names, ideally prefixed with your role or feature area (e.g., `docs/add-contributor-guide`, `fix/pkm-hash-check`).
*   **Commit Message Format**: We enforce the Conventional Commits specification.
    *   Format: `<type>(<scope>): <description>`
    *   Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `infra`, `build`, `ci`, `revert`, `phase<N>`.
    *   Example: `fix(installer): handle missing audit log gracefully`
*   **Co-Authored-By**: Substantive commits (>25 lines changed) authored or significantly assisted by an AI agent must include a `Co-Authored-By` trailer for provenance.

## Pre-Push Gates

The repository is protected by a strict set of client-side git hooks (`.githooks/pre-push`). You must run `scripts/setup-githooks.sh` after cloning the repository. The pre-push hook enforces the following gates:

1.  **Force-Push Block**: Absolute block on force-pushing to the `master` branch. Master history is sacred.
2.  **Public-Content Audit**: Scans the to-be-pushed bytes (`HEAD`, not the working tree) to ensure internal vocabulary (like "Holy Grail" or "Prime Directive") and agent abbreviations are not leaked into published documentation or code comments. Use public-safe terms like "anti-supply-chain" or "user-control posture".
3.  **Stale-Master Check**: Rejects pushes if your local master is behind `origin/master`. You must rebase first.
4.  **Syntax Checks**: Runs `bash -n` on modified shell scripts and `python3 -m py_compile` on modified Python files to prevent pushing broken syntax.
5.  **Documentation Scope Gate**: If a commit changes more than 50 lines in a file, that file *must* be mentioned in the commit message body to prevent under-documented architectural shifts.
6.  **Conventional Commits**: Enforces the subject line format.
7.  **Co-Authored-By Enforcement**: Enforces the provenance trailer on large commits.

### The NO-GATE Escape Hatch
If you are performing a legitimate bulk-mechanical change (e.g., mass renaming, applying formatting tools) where gates 5, 6, or 7 are inappropriate, you may bypass them by including the text `NO-GATE: <reason>` anywhere in the commit message body. Use this sparingly.

## Common Contributor Tasks

### How to Add a New Package
1.  Determine the appropriate tier (`core`, `base`, `desktop`, `extra`, `ai`).
2.  Create a new directory: `packages/<tier>/<package-name>/`.
3.  Create a `package.yml` detailing the source URL, SHA-256 checksum, dependencies, and build style.
4.  If using `build_style: custom`, create a `build.sh` script containing the build functions and starting with `set -euo pipefail`.
5.  Test the build locally using the orchestrator: `python3 igos-build/builder.py <package-name>`.

### How to Add a Test
*   Top-level integration tests live in `tests/`. 
*   Unit tests for Python components generally live alongside the code (e.g., `installer/tests/`).
*   Use `pytest` for all Python test suites. Ensure new logic is covered, especially failure paths.

## Fleet Communication

InterGenOS development is coordinated across a fleet of AI agents and human operators using an MCP (Model Context Protocol) message bus.
*   When executing tasks, agents post status updates and deliverables to the `broadcast` channel.
*   Agents must verify their identity and synchronize with recent bus activity before undertaking tasks.
*   Cross-host operations (e.g., transferring files between Windows and Linux environments) are coordinated via the bus using structured path-claims and SHA-256 verifications. 

Adhering to these guidelines ensures a stable, secure, and collaborative development environment for InterGenOS.
