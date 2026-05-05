# InterGen & Installer Architectural Code Audit

**Date:** 2026-04-29
**Author:** chris-windows-codium-gemini_pro
**Scope:** `intergen/` (AI assistant), `installer/` (Forge SB installer), `docs/` (documentation accuracy & completeness).
**Master tip:** `4d1adc1`

---

## 1. `intergen/` - Stale Assumptions & Unbuilt Scope

### Finding I.1: Cloud Escalation architecture is a stub ()
- **Problem statement:** The "Phone a friend" cloud-escalation feature is documented as having pre-built adapters for 7 providers. However, the shipped code is purely abstract interfaces and a dict scaffold; zero provider modules actually exist to execute an API call. Any user attempting to configure escalation will find it fails silently or returns an error.
- **Evidence:** `intergen/interfaces/cloud.py` defines `CloudProviderAdapter` and `EscalationManagerInterface`. `intergen/llm.py:96` initializes `self._cloud_providers = {}`. There is no implementation of `anthropic.py`, `openai.py`, etc., in the repository.
- **Severity:** HIGH (Functional breakage / False advertising)
- **Proposed fix:** Implement the cloud provider adapters as designed, or explicitly document the feature as "v1.1 Roadmap" in the user-facing documentation and disable the escalation hooks in the router until the adapters are shipped.

### Finding I.2: Priority Chain mismatch between plan, spec, and code ()
- **Problem statement:** `intergen/interfaces/router.py` documents an 8-priority routing chain (P1: System commands through P8: LLM free response). However, the actual implementation in `intergen/router.py` executes a 5-priority chain preceded by 4 short-circuits (empty input, state cache, identity templates, memory ops). 
- **Evidence:** `intergen/interfaces/router.py:16-24` lists the 8 priorities. `intergen/router.py:67-177` implements the 5+4 logic.
- **Severity:** LOW (Documentation / Interface drift)
- **Proposed fix:** Update the docstring in `intergen/interfaces/router.py` to accurately reflect the 9 functional layers (4 short-circuits + 5 priorities) implemented in `ConversationRouter`.

### Finding I.3: Passive memory extraction stub
- **Problem statement:** The router explicitly states it skips passive memory extraction by design ("explicit-storage-only by design"), but leaves a dangling comment block suggesting it might be implemented.
- **Evidence:** `intergen/router.py:314-317`.
- **Severity:** LOW (Code cleanliness)
- **Proposed fix:** Remove the comment block or formally document the architectural decision in a docstring to prevent future developers from mistakenly "fixing" the stub.

### Finding I.4: Hardware Tier definitions are functionally 4-tier ()
- **Problem statement:** The documentation and enums assume a 3-tier hardware model, but the implementation actively splits Tier 2 based on GPU presence (9B for discrete GPU, 2B for CPU-only) due to latency constraints.
- **Evidence:** `intergen/hardware.py` (per TRACKER ) splits Tier 2 logic.
- **Severity:** MEDIUM (Documentation inaccuracy)
- **Proposed fix:** Update the `HardwareTierLevel` enum and surrounding documentation to explicitly define 4 tiers (e.g., Tier 2a/2b) to reflect the material reality of the model dispatch logic.

---

## 2. `installer/` - Forge SB Installer

### Finding F.1: Fallback bootloader registration relies on unverified user action
- **Problem statement:** If `efibootmgr` fails during EFI installation (e.g., read-only efivars on a quirky firmware), the installer logs a warning and instructs the user to run a specific `efibootmgr` command manually post-install. This leaves the system in a potentially unbootable state if the firmware's default auto-discovery (`/EFI/BOOT/bootx64.efi`) fails, and relies on the user reading install logs.
- **Evidence:** `installer/backend/bootloader.py:214-232`.
- **Severity:** MEDIUM (UX / Reliability)
- **Proposed fix:** If `efibootmgr` fails, the installer TUI should explicitly pause and display the failure and the required manual remediation steps to the user, rather than burying it in the logs.

---

## 3. `docs/` - Pre-Shim-Review Audit

### Finding D.1: Documentation does not reflect the InterGen Sentinel Vendor-Neutral pivot ()
- **Problem statement:** The Glasswing MCP guard is documented as a single-vendor (Anthropic) architecture in older documentation, and the actual MCP guard implementation in `mcp_client.py` is missing the schema-pinning, audit-log, and seccomp sandbox enforcement promised by the plan.
- **Evidence:** `intergen/mcp_client.py` (stub guard). 
- **Severity:** HIGH (Reviewer perception / Unbuilt scope)
- **Proposed fix:** **Design decision needed from owner/InterGenOS maintainer.** The code needs to be updated to implement the Sentinel architecture, OR the shim-review documentation needs to explicitly scope out the MCP guard as a v1.1 feature to avoid reviewers probing an unbuilt defense mechanism.

### Finding D.2: GTK4 Panel / GNOME Extension unbuilt scope ()
- **Problem statement:** The project claims "InterGen lives in the GNOME panel", but zero code exists for this in the repository. It is a CLI/DBus only tool currently.
- **Evidence:** `intergen/` directory contains no GUI code.
- **Severity:** MEDIUM (Documentation drift)
- **Proposed fix:** Scrub the "InterGen lives in the GNOME panel" claims from user-facing `README.md` and feature lists until Phase 2.5 is built. Frame it as a CLI/DBus daemon.