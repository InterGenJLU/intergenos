# Frequently Asked Questions (FAQ)

## 1. General Questions

**What is InterGenOS?**
InterGenOS is an AI-integrated, privacy-respecting Linux distribution built entirely from source. It combines a hardened, verifiable boot path with a deeply integrated local AI assistant that never phones home without permission.

**Why a custom Linux distribution?**
Mainstream distributions often trade security for convenience or tie AI features to cloud telemetry. InterGenOS exists to give users a high-security environment where they explicitly own and understand their trust boundaries. For more details on the project philosophy, see our [Contributor Guide](contributor-guide.md).

**What does "Security ONLY, not Security First" mean in practice?**
It means that when a security control conflicts with convenience, security always wins. The system deliberately imposes friction—such as strict AppArmor boundaries, hardware-token signing, and explicit permission gates for system changes—rather than trading a verified trust boundary for a smoother user experience.

**What about telemetry?**
None. InterGenOS collects zero telemetry, usage statistics, or crash reports by default. The system operates locally and stays quiet.

## 2. Security and Verification

**How do I install software?**
Use our custom package manager, pkm. You can install software using sudo pkm install <package>. For a full walkthrough, check the [Getting Started Guide](getting-started.md).

**How does signature verification work?**
When you run pkm sync, your machine fetches the InterGenOS.db index and verifies its cryptographic signature against the bundled InterGenOS release key. If the signature matches, the system trusts the hashes in the index. When you download a package, pkm hashes it locally and ensures it matches the index perfectly before installing.

**What if pkm sync fails?**
If you encounter a signature verification failure or a persistent hash mismatch, **halt installations**. This indicates either a corrupted download or a compromised upstream source (a security incident). Check your network, and if the issue persists, contact the maintainers per the [SECURITY.md](../SECURITY.md) guidelines. For more details, see the [Repository Trust Model](repository-trust.md).

**How do I keep my system secure?**
Regularly run sudo pkm sync followed by sudo pkm update. See the "Keeping Your System Secure and Up To Date" section in the [Getting Started Guide](getting-started.md).

## 3. Boot and Kernel Security

**How does Secure Boot work in InterGenOS?**
InterGenOS uses a Microsoft-signed shim to anchor the trust chain. The shim validates our own InterGenOS-signed grubx64.efi bootloader, which in turn loads our signed vmlinuz kernel. If Secure Boot is enabled, the kernel automatically triggers lockdown=integrity mode.

**Do I need to enroll a Machine Owner Key (MOK) to boot the OS?**
No. The core OS components (shim, grub, kernel, and in-tree modules) are trusted automatically by the system. The kernel modules are signed with an ephemeral key generated dynamically during the build and embedded into the kernel image.

**How do I add my own kernel modules (e.g., DKMS, proprietary drivers)?**
If you need to load out-of-tree modules (like proprietary graphics drivers), you must enroll a Machine Owner Key (MOK). During installation, the Forge installer prompts you to enroll the InterGenOS MOK. Once enrolled, DKMS modules built locally will be trusted.

## 4. The InterGen AI Assistant

**How do I use the built-in AI?**
The InterGen assistant is available natively via the intergen CLI and its background D-Bus daemon. It is tightly integrated into the GNOME desktop via a conversational overlay. For detailed component documentation, read the [InterGen component reference](components/intergen.md).

**Is the AI tracking what I do?**
No. The AI's state caching and diagnostic tools are strictly confined locally. There is no cloud-syncing or background telemetry.

**Can the AI break my system?**
No. Any action the AI proposes that modifies your system state must pass through the **InterGen Sentinel**. The Sentinel intercepts the action and requires explicit authorization from you via a secure Polkit prompt before proceeding.

**Can I connect the AI to Claude, ChatGPT, or other cloud providers?**
Yes. While the default is local-only, the InterGen Sentinel architecture supports opt-in "Phone a Friend" cloud escalation via standard API keys. You can configure providers like Anthropic or Google, but these require your explicit API keys and opt-in configuration.

## 5. Support and Community

**Where do I report a bug or contribute?**
We welcome contributions! Please review the [Contributor Guide](contributor-guide.md) to learn how to set up your environment, build packages, and submit patches.
