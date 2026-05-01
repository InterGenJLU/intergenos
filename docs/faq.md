# Frequently Asked Questions (FAQ)

## 1. General Questions

**What is the difference between InterGenOS and other LFS-derived distros?**
InterGenOS is built strictly around a "Security-Only Alignment" doctrine. While LFS (Linux From Scratch) is an educational tool for understanding Linux, InterGenOS takes full responsibility for a hardened, immutable-core, and verifiable boot path (Secure Boot out of the box), specifically geared toward high-security workloads and local AI agent operation.

**What does "Security ONLY, not Security First" mean in practice?**
It means that when a security control conflicts with convenience or an "AI feature," security always wins. The system drops capabilities and imposes friction (e.g., hardware-token signing, zero-telemetry, strict AppArmor boundaries) deliberately. We never trade a verified trust boundary for a smoother UX.

**What about telemetry?**
None. InterGenOS collects zero telemetry, usage statistics, or crash reports by default. The system operates locally and stays quiet.

**What about voice features for the AI?**
InterGenOS is text-only by design. Previous plans for voice integration (whisper.cpp, Piper TTS) were retracted because real-time voice introduces latency and security scopes that contradict our strict text-driven, verifiable log rules. 

## 2. Boot and Kernel Security

**How does Secure Boot work in InterGenOS?**
InterGenOS uses a Microsoft-signed `shim` to anchor the trust chain. The shim validates our own InterGenOS-signed `grubx64.efi` bootloader, which in turn loads our signed `vmlinuz` kernel. If Secure Boot is enabled, the kernel automatically triggers `lockdown=integrity` mode.

**Do I need to enroll a Machine Owner Key (MOK) to boot the OS?**
No. The core OS components (shim, grub, kernel, and in-tree modules) are trusted automatically by the system. The kernel modules are signed with an ephemeral key generated dynamically during the build and embedded into the kernel image.

**How do I add my own kernel modules (e.g., DKMS, proprietary drivers)?**
If you need to load out-of-tree modules (like proprietary graphics drivers), you must enroll a Machine Owner Key (MOK). During installation, the Forge installer prompts you to enroll the InterGenOS MOK. Once enrolled, DKMS modules built locally will be trusted.

**What happens if I disable Secure Boot?**
The OS will still boot, but the kernel will not automatically enter `lockdown=integrity` mode, and module signature enforcement may be relaxed. We strongly recommend keeping Secure Boot enabled for intended operation.

## 3. Package Management and Software

**Can I install proprietary software on InterGenOS?**
Yes. While the core system is fully open-source and verifiable, you can install necessary proprietary packages using `pkm install-helper`. This command handles fetching and confining proprietary tools (e.g., Chrome, VS Code) securely.

**How are packages verified?**
The `pkm` package manager enforces strict integrity checks. All packages are verified against the repository's cryptographic index, and any metadata or payload tampering will result in a hard failure.

**Does InterGenOS support multilib (32-bit applications)?**
Currently, InterGenOS is strictly 64-bit (`x86-64-v2`). Tools like Steam that require a 32-bit ecosystem are not supported at this time, pending a future multilib architecture decision.

## 4. The InterGen AI Assistant

**How do I use the built-in AI?**
The InterGen assistant is available natively via the `intergen` CLI and its background D-Bus daemon. It operates using a tiered model approach based on your hardware capabilities (e.g., running Qwen 9B locally on GPUs, or 2B on CPUs). See the `intergen(1)` man page for invocation details.

**Is the AI tracking what I do?**
No. The AI's state caching and diagnostic tools are strictly confined locally. There is no cloud-syncing or background telemetry.

**Can I connect the AI to Claude, ChatGPT, or other cloud providers?**
Yes. The InterGen Sentinel architecture supports opt-in "Phone a Friend" cloud escalation via standard API keys. You can configure providers like Anthropic or Google, but these require your explicit API keys and opt-in configuration.
