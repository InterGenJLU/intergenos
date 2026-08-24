"""GATE 9 — the GPU/offload hardware matrix (section 9 line 8).

WHAT COMPOSITION PROPERTY THIS CATCHES. Two rules written under different premises
compose into a machine that detects a discrete graphics card and then never uses it.
Discreteness is decided at 3072 MB of video memory. The second performance tier needs
7168 MB. A card between those two numbers is therefore correctly recognised as
discrete, assigned tier 1, and then served ``--n-gpu-layers 0`` because the offload
resolver treats a tier-1 machine as one with no inference-capable card. The model that
tier serves is about 1.3 GB and fits a 4 GB card comfortably.

WHY A SIMULATED TABLE. The line item asks for a hardware matrix whose simulated rows
block promotion, with the real-card leg run on machines that have the cards. This gate
is the simulated half: it calls the SHIPPED detection and the SHIPPED offload resolver
over a table of (vendor, video memory) pairs. It runs on any machine, including one
with no discrete card, because the values are supplied rather than probed.

THIS GATE ALSO ASSERTS THE HEALTH SURFACE. A machine reporting
``fully_offloaded: true`` alongside ``offloaded_layers: 0`` tells whoever is
diagnosing it that offload succeeded. That is the observability half of the same
defect and is failed separately so a fix to one does not hide the other.

EXPECTED TO FAIL ON R001.1 AS SHIPPED.
"""

from __future__ import annotations

import pytest

# (label, vendor, video memory in MB, is the card one that can run the tier-1 model)
CARDS = [
    ("no GPU, CPU only",             None,     None,  False),
    ("Intel integrated graphics",    "intel",  None,  False),
    ("NVIDIA GTX 1650, 4096 MB",     "nvidia", 4096,  True),
    ("NVIDIA RTX 2060, 6144 MB",     "nvidia", 6144,  True),
    ("NVIDIA RTX 3070, 8192 MB",     "nvidia", 8192,  True),
    ("NVIDIA RTX 4090, 24576 MB",    "nvidia", 24576, True),
]

# The tier-1 model this release serves is InternVL3.5-2B Q4_K_M, about 1.3 GB on disk.
# A card is treated as able to run it when it has at least twice that in video memory.
TIER1_MODEL_MB = 1331
HEADROOM = 2.0


@pytest.fixture(scope="module")
def shipped_hardware(installed_intergen_dir):
    from intergen.hardware import (HardwareDetector, DISCRETE_VRAM_THRESHOLD_MB,
                                   TIER2_VRAM_MB)
    from intergen.llama_manager import resolve_gpu_layers
    return (HardwareDetector(), DISCRETE_VRAM_THRESHOLD_MB, TIER2_VRAM_MB,
            resolve_gpu_layers)


def test_a_detected_discrete_card_that_fits_the_model_is_actually_used(shipped_hardware):
    detector, discrete_floor, tier2_floor, resolve = shipped_hardware

    rows, unused = [], []
    for label, vendor, vram, _capable in CARDS:
        discrete = detector._is_discrete_capable(vendor, vram)
        tier = detector._assign_tier(discrete, gpu_vram_mb=vram)
        layers = resolve("auto", tier_level=tier.value)
        fits = vram is not None and vram >= TIER1_MODEL_MB * HEADROOM
        rows.append((label, vendor, vram, discrete, tier.value, layers, fits))
        if discrete and fits and (layers == 0):
            unused.append((label, vram, tier.value, layers))

    report = ["", "GPU/OFFLOAD MATRIX — shipped detection and shipped offload resolver",
              f"  discrete is decided at {discrete_floor} MB; tier 2 needs "
              f"{tier2_floor} MB; the tier-1 model is about {TIER1_MODEL_MB} MB", "",
              f"  {'card':32} {'discrete':9} {'tier':5} {'--n-gpu-layers':>15}  fits model",
              "  " + "-" * 76]
    for label, _v, vram, discrete, tier, layers, fits in rows:
        report.append(f"  {label:32} {str(discrete):9} {tier:<5} {layers:>15}  {fits}")
    report.append("")
    if unused:
        report.append(
            "Each row above with a discrete card, a model that fits, and 0 layers is a "
            "machine whose graphics card is detected, reported and then never used. "
            "Replies are served entirely on the processor.")

    assert not unused, "\n".join(report)


def test_a_card_that_cannot_report_its_memory_is_not_silently_demoted(shipped_hardware):
    """An unreadable memory figure must not read as "no usable card".

    Some NVIDIA drivers do not export a total-video-memory figure. The shipped
    detector treats an unknown figure the same as a small one, so a large card takes
    the processor-only path at any size. This is failed separately because it is a
    different fix from the dead band above.
    """
    detector, _floor, _tier2, resolve = shipped_hardware
    discrete = detector._is_discrete_capable("nvidia", None)
    tier = detector._assign_tier(discrete, gpu_vram_mb=None)
    layers = resolve("auto", tier_level=tier.value)
    assert layers != 0, (
        "\nAn NVIDIA card whose driver does not report total video memory is served "
        f"--n-gpu-layers {layers} (discrete={discrete}, tier={tier.value}).\n"
        "Video memory that cannot be read is not video memory that is absent. A card of "
        "any size takes the processor-only path on this branch, and the shipped "
        "detector has a driver-heap reader that is deliberately not wired in."
    )


def test_the_health_surface_does_not_report_a_failed_offload_as_complete(shipped_hardware):
    """``fully_offloaded`` must not be true when no layer reached the graphics card.

    The SHIPPED predicate is called directly with the values this box actually
    produces: 0 layers requested, 0 offloaded, 29 total. An earlier draft of this test
    tried to read the expression out of the source text and passed, because the field
    is assigned from a variable — a reminder that a gate reading source text can go
    green on a defect it was written to catch. It calls the function now.

    Corrected 2026-08-24: this test named ``LlamaServerManager``, which is not
    the shipped class (it is ``LlamaManager``), so it raised ImportError and had
    never once reached the predicate. It was red, which read as the defect being
    present, and it would have turned green the moment a class of that name
    existed rather than when the defect was fixed. A gate that fails for the
    wrong reason is not measuring anything.
    """
    from intergen.llama_manager import LlamaManager

    verdict = LlamaManager._fully_offloaded(0, 0, 29)
    assert verdict is not True, (
        "\nThe health surface reports a complete offload when nothing was offloaded.\n"
        "  requested layers : 0\n"
        "  offloaded layers : 0\n"
        "  total layers     : 29\n"
        f"  fully_offloaded  : {verdict}\n"
        "This box logs exactly these values. Whoever reads that field while diagnosing "
        "a slow machine is told the graphics card is in use. A machine that was "
        "deliberately configured for processor-only serving needs its own field saying "
        "so — it must not share a field with a successful offload."
    )
