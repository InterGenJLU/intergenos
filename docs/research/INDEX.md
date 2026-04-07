# InterGenOS Research Archive

Research documents produced during the design and development of InterGenOS. These record the reasoning behind every major decision — from why we chose LFS over Gentoo to how we analyzed 5 distro kernels for convergence.

---

## Foundational Decisions (March 2026)

| Document | Topic |
|----------|-------|
| [Build System Survey](build_systems/survey_2026-03-31.md) | Evaluated 9 build systems (ALFS, Buildroot, Yocto, Gentoo, Void, Nix, CRUX, Arch, Alpine). Chose Python+YAML+Bash. |
| [LFS Alternatives Assessment](build_systems/lfs_alternatives_2026-03-31.md) | Why LFS 13.0 over Gentoo stage1, Buildroot, and other bases |
| [LFS 13.0 Version Pinning](lfs_versions/lfs_13_pinning_2026-03-31.md) | Evaluated LFS 12.3-13.0, pinned to 13.0 |
| [Package Template Formats](package_templates/template_formats_2026-03-31.md) | Template format analysis across 6 distros |
| [Package Management History](package_management/pm_history_and_approaches_2026-03-31.md) | 7 LFS approaches, 6 successful PMs, failure cases |
| [KVM Decision](virtualization/kvm_decision_2026-03-31.md) | KVM vs VirtualBox vs VMware — performance, scriptability |
| [Installer Frameworks](installer/installer_frameworks_2026-03-31.md) | Survey of existing Linux installer frameworks |
| [Installer UX & Design](installer/installer_ux_and_design_2026-03-31.md) | Installation experience design principles |
| [Custom Installer Examples](installer/custom_installer_examples_2026-03-31.md) | How other distros built custom installers |

## Kernel & Hardware

| Document | Topic |
|----------|-------|
| [Kernel Config Strategy](kernel/kernel_config_strategy_2026-04-01.md) | Fragment-based kernel configuration approach |
| [IoT & Virtualization](kernel/iot_and_virtualization_2026-04-01.md) | Kernel support for IoT devices and VM hosting |
| [HP Laptop Hardware Briefing](intergenos-kernel-hp-briefing.md) | First bare metal target — Ice Lake audio, WiFi, GPU, touchpad |
| [Kernel Convergence Analysis](kernel_configs/convergence_analysis.md) | 5-distro kernel config comparison (Ubuntu, Fedora, Arch, Debian, openSUSE) |

## Build System & Pipeline

| Document | Topic |
|----------|-------|
| [Full Pipeline Reference](build_system/full_pipeline_reference_2026-04-02.md) | 9 phases from VM setup to bootable image |
| [Build Gotchas Checklist](build_system/build_gotchas_2026-04-03.md) | Known issues and workarounds for each build tier |
| [Tier Split Analysis](build_system/tier_split_analysis_2026-04-02.md) | 19 base packages moved to core, with build order |
| [Offline Rust Builds](build_system/offline_rust_builds_2026-04-03.md) | Cargo vendor + crate pre-download for chroot builds |
| [Environment Declarations](build_system/environment_declarations_2026-04-04.md) | Build environment variables and their purposes |
| [VPS Source Mirror Design](build_system/vps_source_mirror_design_2026-04-02.md) | Mirror architecture for source tarballs and packages |
| [Deferred Hardening](build_system/deferred_hardening_2026-04-01.md) | Security items tracked for later implementation |
| [Deferred Features](build_system/deferred_features_2026-04-02.md) | Builder features tracked for later implementation |

## Package Management

| Document | Topic |
|----------|-------|
| [DESTDIR & Tracking Design](package_management/destdir_and_tracking_2026-04-01.md) | Staging, manifest generation, archive creation |
| [pkm Design Plan](package_management/pkm_design_plan_2026-04-05.md) | Package manager architecture and CLI design |
| [SQLite in Package Managers](package_management/sqlite_in_package_managers_2026-04-05.md) | Why SQLite for package database |

## Desktop & GNOME

| Document | Topic |
|----------|-------|
| [GNOME Dependency Chain](gnome_desktop_dependency_chain_2026-04-01.md) | ~370 packages from base to working GNOME desktop |
| [Xorg Packages Expanded](xorg_packages_expanded_2026-04-02.md) | 88 individual X11 packages from meta-packages |
| [GNOME Theming](theming/gnome_theming_complete_2026-04-02.md) | GTK, icons, cursors, Plymouth, GDM, GRUB theming |
| [Desktop BLFS Audit](desktop_blfs_audit_2026-04-05.md) | BLFS compliance check for desktop tier |
| [Distro Package Organization](distro_package_organization_2026-04-05.md) | How other distros organize their package sets |

## Build Audits

| Document | Topic |
|----------|-------|
| [Toolchain Audit](build_system/toolchain_audit_2026-04-04.md) | Toolchain tier verification |
| [Core Audit](build_system/core_audit_2026-04-04.md) | Core tier verification |
| [Base Audit](build_system/base_audit_2026-04-04.md) | Base tier verification |
| [Desktop Audit (Full)](build_system/desktop_audit_full_2026-04-04.md) | Complete desktop tier audit |
| [Desktop Build Fixes](build_system/desktop_build_fixes_2026-04-05.md) | Fixes applied during desktop build |
| [Desktop Version Audit](build_system/desktop_version_audit_2026-04-03.md) | Version comparison against BLFS |
| [Chapter 8 Build Issues](chapter8_build/build_issues_and_fixes_2026-04-01.md) | Core build problems and solutions |
| [Python PGO Failure](chapter8_build/python_pgo_failure_2026-04-01.md) | PGO test_generators failure analysis |
| [Chapter 9 Config](chapter9_config/intergenos_core_package_plan_2026-04-01.md) | System configuration decisions |
| [LSB & Standards](chapter9_config/lsb_and_standards_requirements_2026-04-01.md) | LSB conformance requirements |
| [Package Version Audit](chapter9_config/package_version_audit_2026-04-02.md) | Version comparison across all packages |

## Code Reviews

| Document | Topic |
|----------|-------|
| [Review 1 — Build System](code_reviews/review_1_build_system.md) | Full source review request for igos-build |
| [Review 2 — Package Manager](code_reviews/review_2_package_manager.md) | Full source review request for pkm |
| [Review 3 — Installer](code_reviews/review_3_installer.md) | Full source review request for Forge installer |
| [Review 4 — Orchestration](code_reviews/review_4_orchestration.md) | Full source review request for build scripts |
| [Review 5 — Utilities](code_reviews/review_5_utilities.md) | Full source review request for utility scripts |
| [Review 6 — Package Templates](code_reviews/review_6_package_templates.md) | Full source review request for package templates |
| [Review 7 — Post-Remediation Response](code_reviews/review_7_post_remediation_response.md) | Response to ChatGPT/DeepSeek post-remediation reviews |

## Application Planning

| Document | Topic |
|----------|-------|
| [Essential Desktop Apps Research](essential_desktop_apps_research_2026-04-06.md) | Data-driven analysis from Flathub, Snap, Arch survey, DistroWatch |
| [Application Roadmap](application_roadmap_2026-04-06.md) | 4-phase plan for extra tier applications |
| [VS Code & Claude Code](vscode_claude_code_proposal_2026-04-05.md) | Integration plan for development tools |
| [VS Code Linux Requirements](vscode_linux_requirements_2026-04-05.md) | System requirements for VS Code on InterGenOS |

## Branding & Visual Design

| Document | Topic |
|----------|-------|
| [FLUX Branding Plan](branding/flux_branding_plan.md) | 8 visual assets, prompt library, photorealism techniques |
| [Branding Opportunities](branding_opportunities_2026-04-05.md) | Visual touchpoints across the boot-to-desktop chain |

## Infrastructure

| Document | Topic |
|----------|-------|
| [VM Configurations](vm_configurations_2026-04-02.md) | Disk locations, resource allocations, virtiofs setup |
| [BLFS Package Database Plan](blfs_package_database_plan_2026-04-05.md) | SQLite database design for BLFS metadata |
| [Installer Design Plan](installer/installer_design_plan_2026-04-05.md) | Forge installer architecture |
