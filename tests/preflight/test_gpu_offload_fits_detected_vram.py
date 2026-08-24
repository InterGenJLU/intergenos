# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Offload is decided by whether the model fits the detected video memory.

THE DEFECT THIS PINS. Two rules written under different premises composed into a
machine that detects a graphics card and then never uses it. A card is called
discrete at 3072 MB. The second model tier needs 7168 MB. A card between those
two numbers is therefore correctly recognised as discrete, assigned tier 1, and
then served ``--n-gpu-layers 0``, because the offload resolver treated a tier-1
machine as one with no inference-capable card. The model that tier serves is
about 1.8 GB with its vision projector and fits a 4 GB card with room to spare.
Field record: four recorded starts on a 4 GB card, every one of them
``--n-gpu-layers 0`` and ``--device none``, replies taking 4 to 11 seconds.

THE RULE THIS ASSERTS (decided 2026-08-24). Key offload on whether the RESOLVED
MODEL FITS THE DETECTED VIDEO MEMORY, independent of tier; the tier keeps
choosing which model is served. Offload every layer when the whole model fits;
otherwise the largest number of layers that does fit; only zero when not one
layer fits, or when the machine cannot be measured well enough to say.

WHERE THE NUMBERS COME FROM. Model and projector sizes are read from the SIGNED
models manifest — the same record the download path uses — never from a constant
in a test. The headroom the fit calculation reserves for the key/value cache and
the engine's compute buffers is a MEASURED number, and this file re-derives the
measurement's arithmetic so a future change to the constant has to face the
evidence: on this project's own hardware, a full offload of the 9B model at the
shipped 16384-token context allocated 512 MiB of key/value cache, 50.25 MiB of
recurrent state and 501 MiB of compute buffers beyond the model weights.

WHAT A GREEN RUN HERE DOES NOT PROVE. This is the simulated half. It supplies
video-memory figures rather than probing hardware, so it runs anywhere. The real
4 GB card leg belongs to the machine that has one.

Nothing here writes to the tree, reads the network, starts a server, or needs
privilege.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

MIB = 1024 * 1024

# The card sizes that matter, in MB as the detector reports them.
DEAD_BAND_CARD_MB = 4096      # the field GTX 1650: discrete, under the tier-2 floor
SMALL_DISCRETE_MB = 6144      # also inside the dead band
TIER2_CARD_MB = 8192          # clears the tier-2 floor
BIG_CARD_MB = 24576           # clears every floor


@pytest.fixture(scope="module")
def manifest_sizes():
    """Model + projector bytes per tier, from the shipped signed manifest."""
    from intergen.model_choice import tier_download_sizes
    sizes = tier_download_sizes()
    assert sizes, (
        "the shipped models manifest yielded no per-tier sizes; the fit rule has "
        "nothing to measure a card against")
    for tier in (1, 2, 3):
        assert tier in sizes, f"the manifest declares no tier {tier} model"
    return sizes


@pytest.fixture(scope="module")
def plan():
    from intergen.gpu_offload import plan_offload
    return plan_offload


def _mb(byte_count: int) -> int:
    return int(byte_count // MIB)


# ── the rule ─────────────────────────────────────────────────────────────────

def test_the_dead_band_card_offloads_the_whole_tier_one_model(plan, manifest_sizes):
    """THE defect row: a 4 GB discrete card serving the 1.8 GB tier-1 model."""
    from intergen.gpu_offload import OFFLOAD_ALL_LAYERS
    tier1 = manifest_sizes[1]
    result = plan(vram_mb=DEAD_BAND_CARD_MB,
                  model_bytes=tier1["model_bytes"],
                  projector_bytes=tier1["projector_bytes"],
                  total_layers=29)
    assert result.layers == OFFLOAD_ALL_LAYERS, (
        f"\nA {DEAD_BAND_CARD_MB} MB discrete card serving a "
        f"{_mb(tier1['total_bytes'])} MiB model was planned "
        f"{result.layers} layers.\n  reason: {result.reason}\n"
        "The model fits with the measured headroom; every layer belongs on the card.")
    assert result.fits is True


@pytest.mark.parametrize("vram_mb", [DEAD_BAND_CARD_MB, SMALL_DISCRETE_MB,
                                     TIER2_CARD_MB, BIG_CARD_MB])
def test_no_card_that_fits_its_model_is_ever_served_zero_layers(plan, manifest_sizes,
                                                                vram_mb):
    tier1 = manifest_sizes[1]
    result = plan(vram_mb=vram_mb, model_bytes=tier1["model_bytes"],
                  projector_bytes=tier1["projector_bytes"], total_layers=29)
    assert result.layers != 0, (
        f"a {vram_mb} MB card that fits its model was planned 0 layers "
        f"({result.reason})")


def test_a_model_too_large_for_the_card_gets_the_layers_that_do_fit(plan,
                                                                    manifest_sizes):
    """The middle case the rule names: partial offload, not a silent fall to zero."""
    tier3 = manifest_sizes[3]          # ~22 GB model
    total_layers = 48
    result = plan(vram_mb=TIER2_CARD_MB, model_bytes=tier3["model_bytes"],
                  projector_bytes=tier3["projector_bytes"],
                  total_layers=total_layers)
    assert result.fits is False, "a 22 GB model must not read as fitting an 8 GB card"
    assert 0 < result.layers < total_layers, (
        f"\nAn {TIER2_CARD_MB} MB card asked to serve a "
        f"{_mb(tier3['total_bytes'])} MiB model was planned {result.layers} of "
        f"{total_layers} layers.\n  reason: {result.reason}\n"
        "Some layers fit. Serving none of them wastes the card entirely; serving "
        "all of them asks for memory that is not there.")


def test_a_card_too_small_for_even_one_layer_is_planned_zero_with_a_reason(plan,
                                                                           manifest_sizes):
    tier3 = manifest_sizes[3]
    result = plan(vram_mb=512, model_bytes=tier3["model_bytes"],
                  projector_bytes=tier3["projector_bytes"], total_layers=48)
    assert result.layers == 0
    assert result.fits is False
    assert result.reason, "a zero-layer plan must say why in words"


def test_unreadable_video_memory_never_becomes_a_silent_zero(plan, manifest_sizes):
    """Memory that cannot be read is not memory that is absent.

    Some drivers export no total-video-memory figure. Reading that as "no usable
    card" is the same class of defect as the dead band and is failed separately.
    """
    from intergen.gpu_offload import OFFLOAD_ALL_LAYERS
    tier1 = manifest_sizes[1]
    result = plan(vram_mb=None, model_bytes=tier1["model_bytes"],
                  projector_bytes=tier1["projector_bytes"], total_layers=29)
    assert result.layers == OFFLOAD_ALL_LAYERS, (
        f"unreadable video memory was planned {result.layers} layers "
        f"({result.reason})")
    assert result.fits is None, "an unmeasurable fit must be unknown, not False"
    assert result.reason


def test_an_unreadable_layer_count_cannot_produce_a_made_up_partial(plan,
                                                                    manifest_sizes):
    """A partial count needs a layer count. Without one, say so; do not invent one."""
    tier3 = manifest_sizes[3]
    result = plan(vram_mb=TIER2_CARD_MB, model_bytes=tier3["model_bytes"],
                  projector_bytes=tier3["projector_bytes"], total_layers=None)
    assert result.fits is False
    assert result.layers == 0
    assert "layer" in result.reason.lower(), (
        "the plan must name the missing layer count as the reason it could not "
        f"offload partially; it said: {result.reason!r}")


# ── the headroom constant is the measured number, not a guess ────────────────

def test_the_reserved_headroom_covers_the_measured_load(plan):
    """The constant must cover what a real load was measured to need.

    Measured on this project's hardware with the shipped engine and the shipped
    9B model at the shipped 16384-token context: key/value cache 512.00 MiB,
    recurrent state 50.25 MiB, compute buffers 501.00 MiB on the serving card,
    beyond the model weights. A headroom smaller than that sum would let the
    planner call a load "fitting" that then does not fit.
    """
    from intergen.gpu_offload import KV_AND_COMPUTE_HEADROOM_MB
    measured = 512.00 + 50.25 + 501.00
    assert KV_AND_COMPUTE_HEADROOM_MB >= math.ceil(measured), (
        f"the reserved headroom is {KV_AND_COMPUTE_HEADROOM_MB} MiB but a real "
        f"full offload was measured to need {measured} MiB beyond the weights")


# ── the shipped resolver no longer lets the tier decide offload ──────────────

def test_the_tier_alone_no_longer_forces_processor_only_serving():
    """Tier chooses the model. It does not decide whether the card is used."""
    from intergen.llama_manager import AUTO_GPU_LAYERS, resolve_gpu_layers
    for tier in (1, 2, 3, None):
        assert resolve_gpu_layers(AUTO_GPU_LAYERS, tier_level=tier) != 0, (
            f"tier {tier} still resolves the shipped default to 0 layers — the "
            "tier is deciding offload, which is the defect this branch closes")


@pytest.mark.parametrize("pin", [0, 7, 999])
def test_an_explicit_layer_count_is_still_honoured_verbatim(pin):
    """The user's pin stays the last word, including a deliberate 0."""
    from intergen.llama_manager import resolve_gpu_layers
    for tier in (1, 2, 3, None):
        assert resolve_gpu_layers(pin, tier_level=tier) == pin


# ── the health surface must not call a failed offload complete ───────────────

def test_zero_offloaded_layers_is_never_reported_as_fully_offloaded():
    from intergen.llama_manager import LlamaManager
    assert LlamaManager._fully_offloaded(0, 0, 29) is not True, (
        "the health surface reports a complete offload when nothing was "
        "offloaded; whoever reads that field while diagnosing a slow machine is "
        "told the graphics card is in use")


def test_processor_only_serving_has_its_own_field(plan):
    """A deliberate processor-only configuration must not borrow the offload field."""
    from intergen.llama_manager import LlamaManager
    report = LlamaManager.describe_offload(requested_layers=0, offloaded=0,
                                                 total=29, backend="CPU")
    assert report["fully_offloaded"] is False
    assert report["cpu_only_by_request"] is True
    report = LlamaManager.describe_offload(requested_layers=999, offloaded=29,
                                                 total=29, backend="Vulkan")
    assert report["fully_offloaded"] is True
    assert report["cpu_only_by_request"] is False


# ── negative controls ────────────────────────────────────────────────────────

def test_control_the_fit_calculation_rejects_a_model_that_cannot_fit(plan):
    """Control: the comparison detects a true non-fit, so a green above means fit."""
    result = plan(vram_mb=2048, model_bytes=40 * 1024 * MIB, projector_bytes=0,
                  total_layers=64)
    assert result.fits is False
    assert result.layers < 64


def test_control_the_fit_calculation_accepts_a_model_that_plainly_fits(plan):
    from intergen.gpu_offload import OFFLOAD_ALL_LAYERS
    result = plan(vram_mb=24576, model_bytes=1024 * MIB, projector_bytes=0,
                  total_layers=32)
    assert result.fits is True
    assert result.layers == OFFLOAD_ALL_LAYERS
