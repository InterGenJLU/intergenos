"""InterGen setup — interactive model download and configuration.

Called by:
  - 'intergen setup' CLI command
  - Forge installer during OS installation
  - Post-install script

Flow:
  1. Detect hardware tier
  2. Show recommended model + download size
  3. Ask user to confirm (or accept --yes flag for Forge)
  4. Download model via model_manager
  5. SHA256 verify
  6. Report success

If the user declines, InterGen still works for basic system queries
(keyword matching + template synthesis) — just no LLM inference.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


def run_setup(*, auto_yes: bool = False, tier_override: int | None = None) -> bool:
    """Run the interactive setup flow.

    Args:
        auto_yes: Skip confirmation prompt (for Forge installer).
        tier_override: Force a specific tier (1, 2, or 3) instead of auto-detect.

    Returns:
        True if setup completed successfully.
    """
    print("InterGen Setup")
    print("=" * 50)
    print()

    # Step 1: Hardware detection
    print("Detecting hardware...")
    try:
        from intergen.hardware import HardwareDetector
        detector = HardwareDetector()
        tier = detector.detect()
    except Exception as e:
        print(f"Hardware detection failed: {e}")
        return False

    print(f"  RAM:   {tier.ram_gb:.1f} GB")
    print(f"  GPU:   {tier.gpu_vendor or 'None detected'}"
          f"{' (' + tier.gpu_model + ')' if tier.gpu_model else ''}")
    print(f"  Tier:  {tier.tier.value}")
    print()

    # Step 2: Determine model
    if tier_override:
        from intergen.interfaces.types import HardwareTierLevel
        effective_tier = HardwareTierLevel(tier_override)
        print(f"  (Tier overridden to {tier_override})")
    else:
        effective_tier = tier.tier

    try:
        from intergen.model_manager import ModelManager
        mm = ModelManager()
        model = mm.get_model_for_tier(effective_tier)
    except Exception as e:
        print(f"Model selection failed: {e}")
        return False

    if model is None:
        print("No model available for this tier.")
        return False

    # Check if already downloaded
    if model.downloaded:
        print(f"Model already downloaded: {model.name}")
        print(f"  Path: {model.local_path}")
        print()
        print("InterGen is ready.")
        return True

    # Step 3: Confirm download
    print(f"Recommended model: {model.name}")
    print(f"  Quantization:    {model.quant}")
    print(f"  Download size:   ~{model.size_gb:.1f} GB")
    print(f"  Source:          Hugging Face ({model.repo_id})")
    print()

    if not auto_yes:
        print("InterGen needs this model for AI-powered responses.")
        print("Without it, InterGen can still handle basic system queries")
        print("(hostname, disk space, services) but capability will be limited")
        print("until the model is available.")
        print()
        response = input("Download now? [Y/n] ").strip().lower()
        if response in ("n", "no"):
            print()
            print("Setup skipped. You can run 'intergen setup' anytime to download.")
            print("InterGen will still handle basic system requests.")
            return True  # Not a failure — user chose to skip

    # Step 4: Download
    print()
    print(f"Downloading {model.name} ({model.size_gb:.1f} GB)...")

    def progress(downloaded: int, total: int) -> None:
        if total > 0:
            pct = downloaded / total * 100
            bar_width = 40
            filled = int(bar_width * downloaded / total)
            bar = "█" * filled + "░" * (bar_width - filled)
            mb = downloaded / (1024 * 1024)
            total_mb = total / (1024 * 1024)
            print(f"\r  [{bar}] {pct:.0f}% ({mb:.0f}/{total_mb:.0f} MB)", end="", flush=True)

    try:
        success = mm.download_model(model, progress_callback=progress)
    except Exception as e:
        print(f"\nDownload failed: {e}")
        return False

    print()

    if not success:
        print("Download failed — SHA256 verification did not pass.")
        print("Please try again: intergen setup")
        return False

    # Step 5: Verify
    print(f"SHA256 verified: {model.sha256[:16]}...")
    print(f"Model saved to: {model.local_path}")
    print()
    print("InterGen is ready. The AI assistant will use this model")
    print("on the next startup.")
    print()
    print("To start InterGen now:")
    print("  systemctl --user start intergen")

    return True


def main() -> None:
    """CLI entry point for 'intergen setup'."""
    auto_yes = "--yes" in sys.argv or "-y" in sys.argv
    tier_override = None

    for arg in sys.argv:
        if arg.startswith("--tier="):
            try:
                tier_override = int(arg.split("=")[1])
            except ValueError:
                pass

    success = run_setup(auto_yes=auto_yes, tier_override=tier_override)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
