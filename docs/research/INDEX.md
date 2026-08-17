# InterGenOS Research Archive

> **📜 PRESERVED HISTORICAL RECORDS.** Every document catalogued here is a dated
> snapshot, kept as written rather than edited or removed, and quoted material —
> including verbatim evaluation transcripts — is reproduced unmodified. These
> records are not maintained; the living truth is the tree itself and the current
> `docs/`. See [README.md](README.md).

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
| [HP Laptop Hardware Briefing](kernel/intergenos-kernel-hp-briefing.md) | First bare metal target — Ice Lake audio, WiFi, GPU, touchpad |
| [Kernel Convergence Analysis](kernel_configs/convergence_analysis.md) | 5-distro kernel config comparison (Ubuntu, Fedora, Arch, Debian, openSUSE) |
| [Hardware Test Report — HP Laptop 15-dw0xxx](hardware_tests/hp_laptop_15_dw0xxx_2026-04-10.md) | Bare-metal test report |
| [Hardware Test Report — HP Laptop 17-ak0xx](hardware_tests/hp_laptop_17_ak0xx_2026-04-10.md) | Bare-metal test report |
| [Hardware Test Report — Lenovo ThinkCentre 3306G3U](hardware_tests/lenovo_thinkcentre_3306g3u_2026-04-10.md) | Bare-metal test report |
| [Full System Eval](hardware_tests/system_eval_2026-04-10.md) | Cross-hardware system evaluation |

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
| [Desktop BLFS Audit](desktop_audit/desktop_blfs_audit_2026-04-05.md) | BLFS compliance check for desktop tier |
| [Distro Package Organization](package_management/distro_package_organization_2026-04-05.md) | How other distros organize their package sets |

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
| [Nano and Vim Feature Research](chapter8_build/nano_and_vim_features_2026-04-01.md) | Feature/compile-flag research for the two chapter-8 editors |
| [Code Review Findings — 2026-04-02](build_system/code_review_findings_2026-04-02.md) | Early build-system code review findings |
| [Full Build Remediation Plan](build_system/remediation_plan_2026-04-04.md) | All-tiers remediation plan |
| [Desktop Build.sh Audit — BLFS 13.0 Compliance](build_system/desktop_build_audit_2026-04-03.md) | Desktop build.sh compliance pass |
| [Unbuilt Desktop Package Audit](build_system/desktop_audit_unbuilt_2026-04-04.md) | 166 packages vs BLFS 13.0 |
| [Dependency Audit — Desktop Tier](build_system/desktop_dep_audit_2026-04-08.md) | 382 packages audited, gap/unmatched-BLFS-entry counts |
| [Bare Metal Boot Issues — 2026-04-08](build_system/bare_metal_boot_issues_2026-04-08.md) | HP laptop USB-boot issues |
| [Dependency Cycle Break Audit — 2026-04-08](build_system/cycle_break_audit_2026-04-08.md) | Build-order cycle analysis |
| [InterGenOS Full Systems Audit (Parts 1-5)](build_system/full_systems_audit_2026-04-08_part1.md) | Whole-system audit, 2026-04-08 (see part1 through part5 in `build_system/`) |
| [Approved Application List](build_system/approved_application_list_2026-04-08.md) | Governance list of approved extra-tier applications |
| [Security Remediation Plan](build_system/security_remediation_plan_2026-04-08.md) | Build-system security remediation plan |
| [Must-Have Apps — Dependency Analysis](build_system/must_have_apps_dependency_analysis_2026-04-09.md) | Complete dependency analysis for must-have apps |
| [Package Repository Infrastructure Research](build_system/package_repo_infrastructure_2026-04-09.md) | Package repo infrastructure options |
| [Single-Package Build Mode — Design Proposal](build_system/single_package_build_mode_2026-04-09.md) | Design for building one package in isolation |
| [Clang + Custom GCC Target Triple](build_system/clang_custom_triple_2026-04-10.md) | Cross-toolchain research and solution |
| [Extra Tier Build Fixes](build_system/extra_tier_build_fixes_2026-04-10.md) | Comprehensive extra-tier build fix log |
| [IGR — InterGenRepo Specification](build_system/igr_specification_2026-04-10.md) | Package repository format specification |
| [LibreOffice Offline Build](build_system/libreoffice_offline_build_2026-04-10.md) | Offline build research and implementation |
| [Clean Automated Build — Remediation Checklist](build_system/clean_build_remediation_2026-04-06.md) | Checklist for a clean automated build |
| [Build-Order + Silent-Feature-Loss Audit Methodology (Scan A + Scan B)](build_system/audit_methodology_scan_a_scan_b_2026-05-11.md) | Audit methodology design |
| [Preflight Scanners v1](build_system/preflight_scanners_v1.md) | Build-order + silent-feature-loss preflight gates |
| [preflight-undeclared-deps (Scan A.2)](build_system/preflight_undeclared_deps_v1.md) | Design + operator runbook |
| [cargo-vendor-gen.sh Helper (v1)](build_system/cargo_vendor_helper_v1.md) | Host-side vendor tarball helper |
| [pkm Supersedes-RFC Migration — Lessons](build_system/pkm_supersedes_migration_lessons_2026-05-02.md) | Phase 5 post-mortem |
| [Round 1 Findings — Crypto, Audio, Image](desktop_audit/round1_findings.md) | 39-package desktop audit findings |
| [InterGenOS System Audit — April 9, 2026](desktop_audit/system_audit_2026-04-09.md) | System-wide audit |

## Code Reviews

| Document | Topic |
|----------|-------|
| [Code Review Request](code_review_request_20260406.md) | Project context and scope packet used to request full external source review |

*Note: the detailed per-area review responses (build system, package manager, installer, orchestration, utilities, package templates, post-remediation response) are internal audit material and are not part of the public documentation set; the request packet above is the surviving public artifact.*

## Application Planning

| Document | Topic |
|----------|-------|
| [Essential Desktop Apps Research](applications/essential_desktop_apps_research_2026-04-06.md) | Data-driven analysis from Flathub, Snap, Arch survey, DistroWatch |
| [Application Roadmap](applications/application_roadmap_2026-04-06.md) | 4-phase plan for extra tier applications |
| [VS Code & Claude Code](applications/vscode_claude_code_proposal_2026-04-05.md) | Integration plan for development tools |
| [VS Code Linux Requirements](applications/vscode_linux_requirements_2026-04-05.md) | System requirements for VS Code on InterGenOS |

## Branding & Visual Design

| Document | Topic |
|----------|-------|
| [FLUX Branding Plan](branding/flux_branding_plan.md) | 8 visual assets, prompt library, photorealism techniques |
| [Branding Opportunities](branding/branding_opportunities_2026-04-05.md) | Visual touchpoints across the boot-to-desktop chain |
| [Boot Animation — Phase 2 Implementation Notes](branding/boot_animation_phase2_2026-04-08.md) | Boot animation implementation |
| [First-Boot Greeter / Welcome Experience Research](branding/first_boot_greeter_research_2026-04-09.md) | First-boot welcome UX research |
| [Branded Consent Dialog — Design Proposal](branding/consent-dialog/consent-dialog-design-proposal.md) | Consent dialog visual design |
| [Window Controls: Oval Buttons Problem](branding/CSS_Notes/windowcontrols_ovals_review_2026-04-11.md) | GTK4/libadwaita window-control theming issue |
| [Window Controls Ovals — Review Synthesis & Solution](branding/CSS_Notes/windowcontrols_synthesis_2026-04-11.md) | Resolution of the ovals problem above |
| [InterGenOS Icon Design Brief](branding/icons/design_packet/BRIEF.md) | Icon design brief |
| [InterGenOS Visual Language](branding/icons/design_packet/VISUAL_LANGUAGE.md) | Visual language spec |
| [Request: Production Anchor Icon Renders](branding/icons/design_packet/ANCHOR_ICON_REQUEST.md) | Icon render request |

## Theming Audits

| Document | Topic |
|----------|-------|
| [GNOME Shell Theme Architecture — Exhaustive Research](theming/gnome_shell_theming_research_2026-04-09.md) | Shell theming architecture research |
| [GNOME Extensions Audit — April 9, 2026](theming/gnome_extensions_audit_2026-04-09.md) | Extensions compatibility/audit |
| [Theme, Icon, and Cursor Audit — April 9, 2026](theming/theming_audit_2026-04-09.md) | Theme/icon/cursor compliance audit |
| [Icon Design Research — April 12, 2026](theming/icon_design_research_2026-04-12.md) | Icon design research |
| [HP Laptop GNOME Configuration Capture](theming/hp_laptop_gnome_config_2026-04-08.md) | GNOME config snapshot on HP hardware |
| [Pre-ISO Theming Audit — Fine-Tooth Comb Mandate](theming/2026-05-22-pre-iso-theming-audit-prep.md) | Pre-release theming audit prep |

## Installer & Live Session

| Document | Topic |
|----------|-------|
| [Live Session & Installer Architecture Research](installer/live_session_and_installer_architecture_2026-04-09.md) | Initial live-session/installer architecture research |
| [Live Session & Installer Architecture — Review & Decisions](installer/live_session_architecture_review_2026-04-10.md) | Review pass on the above |
| [Live Session & Installer Architecture — FINALIZED](installer/live_session_and_installer_FINAL_2026-04-10.md) | Finalized architecture |
| [InterGenOS Signing-Key Custody — v2](installer/signing_key_custody_2026-04-18.md) | Secure Boot signing-key custody design |
| [InterGenOS Secure Boot — MS UEFI CA Shim Signing via shim-review](installer/ms_shim_sponsorship_2026-04-18.md) | Microsoft shim-review sponsorship process |

## Firstboot

| Document | Topic |
|----------|-------|
| [Firstboot Architecture Rewrite — Chain vs Phase Matrix](firstboot/chain-vs-phase-matrix.md) | Sub-decision matrix for the firstboot rewrite |
| [Firstboot Python Rewrite — Test Plan](firstboot/test-plan.md) | Test plan for the Python rewrite |
| [InterGenOS Firstboot Animation — v7 Arc Closure](firstboot/v7-arc-closure.md) | Firstboot animation arc closure notes |

## Networking

| Document | Topic |
|----------|-------|
| [InterGenOS Firewall Architecture: nftables Only](networking/nftables_only_decision.md) | Decision to standardize on nftables |

## Driver Packaging

| Document | Topic |
|----------|-------|
| [NVIDIA driver-open Packaging Research](packaging/nvidia_driver_open_packaging_2026-04-20.md) | Packaging research for the open-source NVIDIA kernel driver |

## GNOME 49 Wayland Regression (May 2026)

| Document | Topic |
|----------|-------|
| [Research Dossier](2026-05-25-gnome49-wayland-regression/DOSSIER.md) | GNOME 49 Wayland popover + window-drag regression — full dossier |
| [Research Resolution](2026-05-25-gnome49-wayland-regression/RESOLUTION.md) | Primary-source verification / resolution of the dossier's findings |

*Six supporting per-component agent reports (mutter, GTK, libadwaita, distro cross-reference, pixman root cause, build-recipe comparison) are in the same directory and are cited from the dossier/resolution above; not individually indexed here.*

## AI Integration (InterGen Assistant)

| Document | Topic |
|----------|-------|
| [InterGen AI Assistant — Integration Plan](ai_integration/intergen-ai-integration-plan-2026-04-08.md) | Initial package/dependency/component integration plan |
| [Plan: InterGen AI Assistant — Complete Architecture & Implementation](ai_integration/intergen_architecture_plan_2026-04-09.md) | Full architecture and implementation plan |
| [Prior Assistant — Complete Capability Inventory for InterGen Porting](ai_integration/prior_assistant_capability_inventory_2026-04-09.md) | Capability inventory carried over from a prior internal assistant project |
| [InterGen AI Assistant — Semantic Matching & Architecture Research](ai_integration/intergen_semantic_matching_research_2026-04-09.md) | Semantic-matching research |
| [InterGen Panel — UI Design Research](ai_integration/intergen_panel_ui_design_2026-04-09.md) | GNOME panel UI design research |
| [InterGen — LLM Landscape Analysis (April 2026)](ai_integration/llm_landscape_analysis_2026-04-09.md) | Local/cloud LLM landscape survey |
| [InterGen — MCP Architecture & Sentinel Security Model](ai_integration/mcp_security_architecture_2026-04-09.md) | MCP integration and security model |
| [InterGen Competitive Landscape Analysis](ai_integration/competitive_landscape_2026-04-14.md) | Competitive analysis of comparable assistants |
| [InterGen Compound Query Decomposition Research](ai_integration/compound_query_research_2026-04-14.md) | Compound-query decomposition research |
| [InterGen Latency Optimization Research](ai_integration/latency_optimization_research_2026-04-14.md) | Latency optimization research |
| [InterGen Messy Input Research](ai_integration/messy_input_research_2026-04-14.md) | Robustness research for messy/malformed input |
| [InterGen Testing Methodology Research](ai_integration/testing_methodology_research_2026-04-14.md) | Test methodology research |
| [Qwen3.5 Thinking/Reasoning Mode Research](ai_integration/qwen35_thinking_mode_2026-04-14.md) | Reasoning-mode model research |
| [Phase 3: Joint Recommendations for InterGen AI Assistant](ai_integration/phase3_recommendations.md) | Joint recommendations, phase 3 |
| [Cross-Comparison: Round 10 vs Baseline A vs Baseline B](ai_integration/cross_comparison.md) | Eval cross-comparison |
| [InterGen AI Integration — Code Review Packet](ai_integration/code_review_packet/INTERGEN_CODE_REVIEW_PACKET.md) | Router/LLM/grader code review packet (router.py, llm.py, grader.py) — see `code_review_packet/` for the A1-A4, B1-B5 supporting sections |

*The `ai_integration/` directory also holds an extensive round-by-round iterative review/audit trail (`round10_clean_review.md` through `round28_9B_review.md`, plus baseline logs), documenting incremental accuracy/regression work; not individually indexed here.*

## Process & Ceremony

| Document | Topic |
|----------|-------|
| [Ceremony Lessons Learned — 2026-04-30 → 2026-05-05](ceremony/lessons-learned-2026-05-05.md) | Process retrospective |

## Infrastructure

| Document | Topic |
|----------|-------|
| [VM Configurations](virtualization/vm_configurations_2026-04-02.md) | Disk locations, resource allocations, virtiofs setup |
| [BLFS Package Database Plan](package_management/blfs_package_database_plan_2026-04-05.md) | SQLite database design for BLFS metadata |
| [Installer Design Plan](installer/installer_design_plan_2026-04-05.md) | Forge installer architecture |
| [BLFS 13.0 Package Reference](blfs_package_data/blfs_13_package_reference.md) | Generated BLFS 13.0 package reference data |
