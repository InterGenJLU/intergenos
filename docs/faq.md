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
Regularly run sudo pkm sync followed by sudo pkm upgrade --all. See the "Keeping Your System Secure and Up To Date" section in the [Getting Started Guide](getting-started.md).

## 3. Boot and Kernel Security

**How does Secure Boot work in InterGenOS?**
A Microsoft-signed shim anchors the trust chain (today that's Fedora's pre-signed shim; InterGenOS's own shim is in the shim-review process). The shim validates grubx64.efi — signed with the Machine Owner Key Forge generated for your machine at install — which in turn loads the signed vmlinuz kernel. The kernel boots in lockdown=integrity mode **unconditionally** — engaged from early boot on every boot, whether or not Secure Boot is active (we build the vanilla kernel and set `CONFIG_LOCK_DOWN_KERNEL_FORCE_INTEGRITY=y`, which is stronger and more deterministic than the downstream "auto-trigger lockdown when Secure Boot is detected" patch some distros carry).

**Do I need to enroll a Machine Owner Key (MOK) to boot the OS?**
Only if you run with Secure Boot enabled — which is the posture InterGenOS is built for, so in practice: yes. GRUB and the kernel's Unified Kernel Image are signed with a MOK that Forge generates for your machine at install time, and enrolling it via MokManager at first boot is what lets the shim trust them. With Secure Boot off, an installed system boots normally without enrolment; Forge treats a blank MOK password as a valid choice and skips staging, and says so before you commit. The catch is deferred rather than avoided: turning Secure Boot on later means enrolling the certificate by hand with `mokutil --import`, because nothing was staged for MokManager to pick up. (In-tree kernel *modules* are a separate case: they're signed with an ephemeral key generated during the kernel build and embedded in that kernel image, so they don't need MOK enrollment on their own.) InterGenOS currently boots via Fedora's pre-signed shim while its own Microsoft-signed shim goes through the shim-review process; once that lands, first-boot MOK enrollment is expected to go away for the base system.

**How do I add my own kernel modules (e.g., DKMS, proprietary drivers)?**
Out-of-tree modules (like a proprietary graphics driver) chain through the same per-machine MOK described above. During installation, the Forge installer prompts you to set the MOK enrollment password; once enrolled at first boot, DKMS modules built and signed locally will be trusted.

## 4. The InterGen AI Assistant

**How do I use the built-in AI?**
The InterGen assistant is available natively via the intergen CLI and its background D-Bus daemon. It is tightly integrated into the GNOME desktop via a conversational overlay. For detailed component documentation, read the [InterGen component reference](components/intergen.md).

**Can it see images, or is it text only?**
It can see images, on every hardware tier. Each model ships with a paired vision projector whose hash is pinned in the same signed manifest as the model itself, so you can show the assistant a screenshot or a photo and ask about it. The pinning is enforced, not advisory: a model that declares vision but whose projector is not pinned in the signed manifest is refused rather than served without it. Input is text and images — there is no voice input, which was evaluated and deliberately left out.

**Which model do I get, and does more RAM get me a bigger one?**
No — system RAM is never an input to that decision. The tier is chosen from your graphics hardware alone: no discrete GPU serves the 2-billion-parameter model, roughly 7 GB of VRAM or more serves the 9B, and roughly 22 GB or more serves the 35B mixture-of-experts model. If a discrete card is present but its VRAM cannot be read, selection fails *down* to the smallest tier rather than guessing upward. The reasoning is latency: a large model running out of system memory is slow enough to be the wrong answer no matter how much memory you have.

**Is the AI tracking what I do?**
No. The AI's state caching and diagnostic tools are strictly confined locally. There is no cloud-syncing or background telemetry.

**Can the AI break my system?**
No. Every tool call InterGen makes runs through a provenance gate first; the default escalation mode is `ask`, so any action that would modify your system state stops and shows you an on-screen confirmation prompt before it proceeds — a Deny refuses the action outright. InterGen Sentinel scans the surrounding traffic for risk on top of that gate. Privileged operations that need root (installing packages, managing services) go through an additional PolicyKit authentication step.

**Can I connect the AI to a cloud provider like Claude-Anthropic or ChatGPT-OpenAI?**
Yes. While the default is local-only, the InterGen Sentinel architecture supports opt-in "Phone a Friend" cloud escalation via standard API keys. You can configure providers like Claude-Anthropic or Gemini-Google, but these require your explicit API keys and opt-in configuration.

## 5. Support and Community

**Where do I report a bug or contribute?**
We welcome contributions! Please review the [Contributor Guide](contributor-guide.md) to learn how to set up your environment, build packages, and submit patches.
