# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Which model tiers this box can run, and letting the user pick one.

Design (decided 2026-07-31): setup does not silently pick a model for the user
and it does not make hardware prove itself before being used. It reports what
the box can run and lets the user choose:

    detect the GPU
      can it run the 35B?  -> offer 35B, 9B or 2B
      else can it run 9B?  -> offer 9B or 2B
      else                 -> install the 2B

The 2B is a legitimate choice at every rung, not a fallback. A user on a 32 GB
card who wants the small fast model gets it without arguing with the installer.

The one case where capability cannot be TRUSTED is an NVIDIA card running a
non-proprietary kernel driver: the open driver exports no VRAM figure through
sysfs, and its ability to actually offload a model has not been proven on this
hardware. There — and only there — setup says so and offers a real choice:
install NVIDIA's drivers to unlock the higher tiers, or proceed now with the
2B. A card already on the proprietary driver reads its VRAM normally and runs
the ladder with no advisory at all.

Corrected 2026-08-03: the card's SIZE is usually knowable even on the open
driver, from the serving stack's own device enumeration, so the advisory now
states it instead of saying capability cannot be determined. Knowing the size
does not unlock a rung — see :func:`_advisory_text_for`.

Capability is decided by the SAME per-tier VRAM fit gates the hardware detector
already uses (:data:`intergen.hardware.TIER2_VRAM_MB`,
:data:`intergen.hardware.TIER3_RESIDENT_VRAM_MB`) — this module adds no new
threshold and no new measurement mechanism.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from intergen.interfaces.types import HardwareTierLevel
from intergen.private_state import private_dir, private_write_text

log = logging.getLogger(__name__)

# Kernel drivers that mean "NVIDIA's own driver stack is loaded for this card".
# nvidia-drm/nvidia-modeset are the same stack's companion modules; any of them
# bound to the card means VRAM is readable through the driver's own surfaces
# (/proc/driver/nvidia, nvidia-smi) which intergen.hardware already reads.
NVIDIA_PROPRIETARY_DRIVERS = frozenset({"nvidia", "nvidia-drm", "nvidia-modeset"})

# The PCI vendor id whose cards this advisory is about.
_NVIDIA_VENDOR_ID = "0x10de"

_CHOICE_FILENAME = "model-tier-choice.json"


@dataclass(frozen=True)
class GpuDriverState:
    """What is bound to the box's GPU, as read from sysfs.

    ``nvidia_present`` is true when any card carries NVIDIA's PCI vendor id.
    ``driver`` is the kernel driver bound to that card (``nouveau``,
    ``nvidia``, ``amdgpu``, …) or None when it could not be read.
    ``proprietary_nvidia`` is true only when an NVIDIA card is bound to
    NVIDIA's own driver stack.
    """
    nvidia_present: bool = False
    driver: str | None = None
    proprietary_nvidia: bool = False

    @property
    def needs_driver_advisory(self) -> bool:
        """True when an NVIDIA card is present but not on NVIDIA's driver.

        That is exactly the state in which the box's real capability cannot be
        read, so the ladder cannot honestly offer the higher tiers.
        """
        return self.nvidia_present and not self.proprietary_nvidia


def detect_driver_state(drm_root: Path | str = "/sys/class/drm") -> GpuDriverState:
    """Read the GPU driver binding from sysfs. Never raises.

    Walks ``/sys/class/drm/card*/device``: ``vendor`` gives the PCI vendor id
    and the ``driver`` symlink's target basename gives the bound kernel driver
    (this is the ``.../device/driver -> ../../../bus/pci/drivers/nouveau``
    shape). ``drm_root`` is injectable so the three cases are testable without
    the hardware.
    """
    root = Path(drm_root)
    nvidia_present = False
    driver: str | None = None
    proprietary = False
    try:
        card_dirs = sorted(root.glob("card[0-9]*"))
    except OSError:
        return GpuDriverState()
    for card_dir in card_dirs:
        device = card_dir / "device"
        try:
            vendor_file = device / "vendor"
            if not vendor_file.exists():
                continue
            vendor_id = vendor_file.read_text().strip().lower()
        except OSError:
            continue
        bound: str | None = None
        try:
            link = device / "driver"
            if link.exists():
                bound = os.path.basename(os.path.realpath(link))
        except OSError:
            bound = None
        if vendor_id == _NVIDIA_VENDOR_ID:
            nvidia_present = True
            driver = bound
            if bound in NVIDIA_PROPRIETARY_DRIVERS:
                proprietary = True
                # An NVIDIA card on NVIDIA's driver settles it; no other card
                # can make this box need the advisory.
                break
        elif driver is None:
            driver = bound
    return GpuDriverState(nvidia_present=nvidia_present, driver=driver,
                          proprietary_nvidia=proprietary)


def runnable_tiers(*, is_discrete: bool,
                   vram_mb: int | None) -> tuple[HardwareTierLevel, ...]:
    """The tiers this box can actually run, most capable first.

    Tier 1 (the 2B) is always runnable — it is the model a box with no discrete
    GPU serves, and it stays a legitimate choice on every other box too. The
    higher rungs use the hardware module's own fit gates; unknown VRAM clears
    neither, which is the same fail-down rule tier assignment already applies.
    """
    from intergen.hardware import TIER2_VRAM_MB, TIER3_RESIDENT_VRAM_MB

    tiers: list[HardwareTierLevel] = []
    if is_discrete and vram_mb is not None:
        if vram_mb >= TIER3_RESIDENT_VRAM_MB:
            tiers.append(HardwareTierLevel.TIER_3)
        if vram_mb >= TIER2_VRAM_MB:
            tiers.append(HardwareTierLevel.TIER_2)
    tiers.append(HardwareTierLevel.TIER_1)
    return tuple(tiers)


@dataclass(frozen=True)
class SetupOffer:
    """What setup should put in front of the user.

    ``tiers`` is the choice list, most capable first; it always contains at
    least Tier 1. ``advisory`` is true when an NVIDIA card is present without
    NVIDIA's driver, in which case ``tiers`` holds only what can be run RIGHT
    NOW and ``advisory_text`` explains how to unlock the rest.
    """
    tiers: tuple[HardwareTierLevel, ...]
    advisory: bool
    driver_state: GpuDriverState
    advisory_text: str = ""

    @property
    def is_choice(self) -> bool:
        """True when there is more than one rung to pick from."""
        return len(self.tiers) > 1

    def to_status(self, *, pins_path: Path | None = None) -> dict:
        """Compact dict for the daemon's Status surface and the Welcomer.

        ``download_bytes`` maps each offered tier to the total number of bytes
        setup will fetch for it, projector included, read from the signed
        models manifest. It is here so the first-run page can STATE that number
        instead of carrying a constant: what is fetched depends on the rung, and
        a constant was wrong on every rung. A tier the manifest does not
        describe is simply absent from the map — the page then says nothing
        about size for it, which is the honest answer.
        """
        sizes = tier_download_sizes(
            **({"pins_path": pins_path} if pins_path is not None else {}))
        return {
            "tiers": [t.value for t in self.tiers],
            "advisory": self.advisory,
            "advisory_text": self.advisory_text,
            "gpu_driver": self.driver_state.driver,
            "nvidia_present": self.driver_state.nvidia_present,
            "proprietary_nvidia": self.driver_state.proprietary_nvidia,
            "download_bytes": {t.value: sizes[t.value]["total_bytes"]
                               for t in self.tiers if t.value in sizes},
        }


ADVISORY_TEXT = (
    "An NVIDIA card is installed but is running the open-source driver, which "
    "does not report how much video memory the card has. InterGen cannot tell "
    "from here whether this box can run the larger models.\n"
    "Install NVIDIA's drivers with the package manager and reboot; setup will "
    "then detect the card and offer the models it can run. You can also "
    "continue now with the 2B model, which runs on any box."
)


def _advisory_text_for(open_driver_vram_mb: int | None) -> str:
    """The advisory, told as precisely as the box allows.

    The kernel driver reports nothing, but the serving stack's own device
    enumeration usually does (see
    :func:`intergen.hardware.open_driver_vram_mb`). When it
    does, saying "InterGen cannot tell" is no longer true, and on a card too
    small for the larger models it is actively misleading: a user reads it as
    "install the driver and you may get more", installs a proprietary driver
    for that reason, and gets the same 2B — which is the outcome that made this
    worth fixing. So:

    * size unknown  -> the original text, unchanged and still true;
    * size known and below the Tier-2 fit gate -> say the size, and say plainly
      that the driver will not change which model this card runs;
    * size known and at or above the gate -> say the size, and say that the
      driver is what proves the card can serve it. The rung is still not
      offered here, because offload through the open driver has not been proven
      on this hardware (measured on the Zephyrus: no usable offload on nouveau
      until NVIDIA's driver went on). Reporting a size is not the same as
      trusting it to serve.
    """
    from intergen.hardware import TIER2_VRAM_MB

    if open_driver_vram_mb is None:
        return ADVISORY_TEXT
    size = (f"The serving stack reports this card as {open_driver_vram_mb} MiB "
            f"of video memory.")
    if open_driver_vram_mb >= TIER2_VRAM_MB:
        return (
            "An NVIDIA card is installed but is running the open-source "
            f"driver. {size} That is enough memory for a larger model, but "
            "InterGen has not proven that this driver can actually run one "
            "here, so only the 2B is offered right now.\n"
            "Install NVIDIA's drivers with the package manager and reboot; "
            "setup will then read the card through its own driver and offer "
            "the models it can run."
        )
    return (
        "An NVIDIA card is installed but is running the open-source driver. "
        f"{size} The larger models need at least {TIER2_VRAM_MB} MiB, so the "
        "2B is the model this card runs.\n"
        "Installing NVIDIA's drivers is still worth doing for graphics and for "
        "serving speed, but it will not make a larger model available on this "
        "card."
    )


def build_offer(*, is_discrete: bool, vram_mb: int | None,
                driver_state: GpuDriverState | None = None) -> SetupOffer:
    """Build the ladder for this box (the module docstring's decision tree)."""
    state = driver_state if driver_state is not None else detect_driver_state()
    if state.needs_driver_advisory:
        # Capability is not TRUSTED here, so the higher rungs are not offered as
        # if they were measured. The 2B is offered for real, right now. The card
        # SIZE, however, is usually knowable even on the open driver — read it
        # so the advisory can say something true and specific instead of "cannot
        # tell". This never changes which rungs are offered.
        from intergen import hardware
        try:
            reported = hardware.open_driver_vram_mb("nvidia")
        except Exception:  # a broken reader must never break setup
            reported = None
        return SetupOffer(tiers=(HardwareTierLevel.TIER_1,), advisory=True,
                          driver_state=state,
                          advisory_text=_advisory_text_for(reported))
    return SetupOffer(tiers=runnable_tiers(is_discrete=is_discrete,
                                           vram_mb=vram_mb),
                      advisory=False, driver_state=state)



# ── What each rung costs to download ──────────────────────────────────────────


def tier_download_sizes(pins_path: Path | None = None) -> dict[int, dict[str, int]]:
    """Per tier: the model bytes, the projector bytes, and their total.

    Read from the signed models manifest, which is the record that decides what
    setup actually fetches. The projector is counted because setup fetches it
    too — leaving it out understated Tier 1 by about 0.6 GiB.

    Fail-closed and quiet: a manifest that is missing, unreadable or malformed
    yields an EMPTY map, and every caller then declines to state a size rather
    than stating one it cannot support. A number shown to a person deciding
    whether they have the bandwidth for something is not a place to guess.
    """
    if pins_path is None:
        from intergen.model_manager import PINS_MANIFEST_PATH
        pins_path = PINS_MANIFEST_PATH
    try:
        payload = json.loads(Path(pins_path).read_text(encoding="utf-8"))
        entries = payload["entries"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    sizes: dict[int, dict[str, int]] = {}
    for entry in entries:
        try:
            tier = entry.get("tier")
            if not tier:
                continue
            model = int(entry["size_bytes"])
            projector = int(entry.get("mmproj_size_bytes") or 0)
        except (AttributeError, TypeError, ValueError, KeyError):
            continue
        sizes[int(tier)] = {"model_bytes": model,
                            "projector_bytes": projector,
                            "total_bytes": model + projector}
    return sizes


def format_download_size(total_bytes: int) -> str:
    """A download size in the units a person reads on their own connection.

    Binary units, one decimal place below 10 GiB and none above, because the
    difference between 1.8 and 2 GiB matters to somebody on a metered link and
    the difference between 21 and 21.3 does not.
    """
    gib = total_bytes / (1024 ** 3)
    if gib < 10:
        return f"{gib:.1f} GB"
    return f"{gib:.0f} GB"

# ── Remembering what the user picked ──────────────────────────────────────────

def choice_path(home: Path | None = None) -> Path:
    """Where the user's tier choice is recorded.

    Under the user's XDG data dir, beside the license-acceptance records that
    the model manager writes, so per-user setup state has one home. ``home``
    is the invoking user's home directory when setup runs under pkexec/sudo —
    the record must belong to the user, never to root.
    """
    if home is not None:
        return Path(home) / ".local" / "share" / "intergen" / _CHOICE_FILENAME
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else (Path.home() / ".local" / "share")
    return base / "intergen" / _CHOICE_FILENAME


def record_choice(tier: HardwareTierLevel, *, home: Path | None = None,
                  chosen_by: str = "", advisory_shown: bool = False) -> Path:
    """Persist the user's pick so setup does not ask again on every boot.

    Re-running setup deliberately re-offers the ladder — this record stops the
    repeat prompting, it does not lock the user out of changing their mind.
    Returns the path written. Never raises; a failure to persist is logged and
    the caller proceeds (a lost preference re-asks, it does not break setup).
    """
    path = choice_path(home)
    record = {
        "tier": int(tier.value),
        "chosen_at": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "chosen_by": chosen_by or os.environ.get("USER", "unknown"),
        "advisory_shown": bool(advisory_shown),
    }
    try:
        private_dir(path.parent)
        private_write_text(path, json.dumps(record, indent=2) + "\n")
        log.info("Model-tier choice recorded (tier %d) at %s", tier.value, path)
    except OSError as e:
        log.warning("Could not record the model-tier choice at %s: %s", path, e)
    return path


def load_choice(home: Path | None = None) -> HardwareTierLevel | None:
    """The tier the user previously picked, or None if they never have."""
    path = choice_path(home)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    try:
        return HardwareTierLevel(int(data["tier"]))
    except (KeyError, TypeError, ValueError):
        return None
