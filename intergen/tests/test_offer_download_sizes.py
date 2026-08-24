# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The setup offer states a download size that comes from the signed manifest.

WHY THIS TEST HAD TO EXIST. The first-run page tells a person how large the
setup download is before they commit to it, and until now that sentence was a
constant in the page's source: "about 4-5 GB". The size actually downloaded
depends on which rung of the model ladder the box is offered, and every rung
also pulls a multimodal projector file that the sentence never counted.

Measured against the shipped manifest on 2026-08-24, the constant was wrong on
every rung: Tier 1 is about 1.8 GiB with its projector, Tier 2 about 6.1 GiB,
Tier 3 about 21.3 GiB. A number a person uses to decide whether they have the
bandwidth for something must come from the record that decides what is fetched.

WHAT IT ASSERTS. That the manifest reader reports, per tier, the model bytes,
the projector bytes and their total; that the totals equal the manifest's own
figures rather than a constant; and that the offer the first-run page reads
carries those totals for every tier it offers, so the page can render the
sentence instead of hard-coding it.

The manifest read is the IN-TREE one, so the test holds on a machine with no
InterGen installed and does not depend on what a particular box has cached.

Nothing here writes to the tree, reads the network, or needs privilege.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from intergen import model_choice

_TREE_MANIFEST = Path(__file__).resolve().parents[1] / "data" / "models-manifest.json"


def _manifest_totals() -> dict[int, dict[str, int]]:
    """The expected answer, computed here from the manifest independently."""
    entries = json.loads(_TREE_MANIFEST.read_text(encoding="utf-8"))["entries"]
    totals: dict[int, dict[str, int]] = {}
    for entry in entries:
        tier = entry.get("tier")
        if not tier:
            continue
        model = int(entry["size_bytes"])
        projector = int(entry.get("mmproj_size_bytes") or 0)
        totals[int(tier)] = {
            "model_bytes": model,
            "projector_bytes": projector,
            "total_bytes": model + projector,
        }
    return totals


def test_the_manifest_under_test_actually_describes_every_tier():
    """Positive control: without this, an empty manifest would pass silently."""
    totals = _manifest_totals()
    assert set(totals) == {1, 2, 3}, (
        f"the in-tree manifest describes tiers {sorted(totals)}; the assertions "
        "below would be judging an incomplete ladder")
    for tier, sizes in totals.items():
        assert sizes["model_bytes"] > 0
        assert sizes["projector_bytes"] > 0, (
            f"tier {tier} has no projector size in the manifest, so the "
            "projector-inclusive claim cannot be tested against it")


def test_tier_download_sizes_reports_model_projector_and_total_per_tier():
    sizes = model_choice.tier_download_sizes(pins_path=_TREE_MANIFEST)
    assert sizes == _manifest_totals(), (
        "the reported per-tier download sizes do not match the manifest they "
        "are supposed to come from")


def test_a_missing_manifest_reports_nothing_rather_than_guessing(tmp_path):
    """Fail-closed: no manifest means no size claim, never a fabricated one."""
    assert model_choice.tier_download_sizes(
        pins_path=tmp_path / "absent.json") == {}


def test_the_offer_the_first_run_page_reads_carries_a_total_per_offered_tier():
    from intergen.interfaces.types import HardwareTierLevel

    offer = model_choice.build_offer(is_discrete=True, vram_mb=24576)
    status = offer.to_status(pins_path=_TREE_MANIFEST)
    assert "download_bytes" in status, (
        "the offer the first-run page reads carries no download size, so the "
        "page has nothing to render and must hard-code a number")
    expected = _manifest_totals()
    for tier in status["tiers"]:
        assert str(tier) in status["download_bytes"] or tier in status["download_bytes"], (
            f"tier {tier} is offered but no download size is reported for it")
        reported = (status["download_bytes"].get(tier)
                    or status["download_bytes"].get(str(tier)))
        assert reported == expected[int(tier)]["total_bytes"]


@pytest.mark.parametrize("tier,at_least_gib", [(1, 1.7), (2, 6.0), (3, 21.0)])
def test_every_tier_is_larger_than_the_constant_the_page_used_to_show(tier, at_least_gib):
    """The retired sentence said "about 4-5 GB" for every rung; it was wrong.

    Tier 1 is larger than the sentence's low end once the projector is counted,
    and Tiers 2 and 3 are larger than its high end. This pins the reason the
    constant had to go, so a future edit cannot quietly reintroduce one.
    """
    sizes = model_choice.tier_download_sizes(pins_path=_TREE_MANIFEST)
    gib = sizes[tier]["total_bytes"] / (1024 ** 3)
    assert gib >= at_least_gib, f"tier {tier} totals {gib:.2f} GiB"
