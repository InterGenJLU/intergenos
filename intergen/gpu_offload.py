# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""How much of a model goes on the graphics card.

THE RULE (decided 2026-08-24). Offload is keyed on whether the RESOLVED MODEL
FITS THE DETECTED VIDEO MEMORY, independent of which capability tier the machine
was assigned. The tier still chooses WHICH model is served; it no longer decides
whether the card is used to serve it.

WHAT THAT REPLACED, AND WHY. Discreteness was decided at 3072 MB of video
memory and the second model tier needs 7168 MB, so a card between those two
numbers was recognised as discrete, assigned tier 1, and then served
``--n-gpu-layers 0``, because the resolver read "tier 1" as "no inference-capable
card". The model that tier serves is about 1.8 GB with its vision projector and
fits a 4 GB card with room to spare. Measured in the field on a 4 GB card: four
recorded starts, every one at zero layers and ``--device none``, replies taking
4 to 11 seconds while the card sat idle.

THE THREE OUTCOMES, in the order they are tried:
  * the whole model fits the card with headroom → every layer;
  * it does not fit → the largest number of layers that does;
  * not one layer fits, or the machine cannot be measured well enough to say
    → zero, WITH the reason in words.

Zero is never reached by inference from a tier. It is reached only by a
measurement that says the layers do not fit, or by an honest statement that
something needed could not be read.

WHEN SOMETHING CANNOT BE READ, THE ANSWER IS TO OFFLOAD, NOT TO REFUSE. A card
whose driver exports no memory figure is a card of unknown size, not a card of
no size. Refusing to offload there is the same silent processor-only serving
this rule exists to end, and it fails in the direction the user cannot see. An
over-optimistic offload fails LOUDLY at the engine instead, where the existing
offload-mismatch warning and the served-capability guard both see it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MIB = 1024 * 1024

# llama.cpp's "put every layer on the accelerator" value.
OFFLOAD_ALL_LAYERS = 999

# The video memory a real load needs BEYOND the model weights, in MiB.
#
# MEASURED, not estimated (2026-08-24, this project's own hardware: a Radeon RX
# 7900 XT, the shipped llama-server, the shipped 9B model with its vision
# projector, at the shipped 16384-token context). The engine's own allocation
# lines, on the serving card and beyond the weights:
#
#     key/value cache      512.00 MiB      (128.00 MiB at a 4096 context —
#                                           linear in context, as expected)
#     recurrent state       50.25 MiB      (fixed)
#     compute buffers      501.00 MiB      (493.00 MiB at 4096 — nearly fixed)
#                        ----------
#     total                1063.25 MiB
#
# Reserved here as 1088 MiB: the measured sum rounded up to the next 64 MiB.
#
# WHY ONE NUMBER RATHER THAN A PER-MODEL CALCULATION. The key/value figure above
# is the LARGEST of the three shipped models' — a smaller model has fewer and
# narrower layers and so needs less. Reserving the largest for every model errs
# toward declaring that something does NOT fit, which costs some speed on a card
# that could have taken more; the opposite error declares a fit that is not
# there and the load fails. Only the first of those is safe to be wrong about.
KV_AND_COMPUTE_HEADROOM_MB = 1088


@dataclass(frozen=True)
class OffloadPlan:
    """What to pass as ``--n-gpu-layers``, and the arithmetic behind it.

    ``fits`` is True when the whole model fits, False when it measurably does
    not, and None when the machine could not be measured well enough to say —
    the three are genuinely different and are never collapsed.
    """

    layers: int
    fits: bool | None
    reason: str
    vram_mb: int | None
    required_mb: int | None
    total_layers: int | None
    per_layer_mb: float | None

    @property
    def offloads_everything(self) -> bool:
        return self.layers == OFFLOAD_ALL_LAYERS


def offloadable_units(block_count: int) -> int:
    """How many units ``--n-gpu-layers`` counts for a model of ``block_count``.

    MEASURED, not assumed: the shipped 9B model's header declares
    ``qwen35.block_count = 32`` and the engine's own banner for a full offload of
    that file reads ``offloaded 33/33 layers``, having reported the repeating
    layers and the output layer separately. The output layer is the extra unit.
    """
    return int(block_count) + 1


def plan_offload(*, vram_mb: int | None, model_bytes: int | None,
                 projector_bytes: int = 0, total_layers: int | None = None,
                 headroom_mb: int = KV_AND_COMPUTE_HEADROOM_MB) -> OffloadPlan:
    """Decide the offload for one model on one card.

    ``vram_mb`` is the detected card memory as the hardware detector reports it.
    ``model_bytes`` and ``projector_bytes`` come from the SIGNED models manifest
    — the same record the download path uses. ``total_layers`` is what
    ``--n-gpu-layers`` counts (see :func:`offloadable_units`); None means it
    could not be read, which forbids a partial answer rather than inventing one.
    """
    if vram_mb is None or model_bytes is None:
        missing = "video memory" if vram_mb is None else "the model's size"
        return OffloadPlan(
            layers=OFFLOAD_ALL_LAYERS, fits=None,
            reason=(f"{missing} could not be read, so whether the model fits is "
                    "unknown; offloading rather than serving on the processor "
                    "without having measured anything"),
            vram_mb=vram_mb, required_mb=None, total_layers=total_layers,
            per_layer_mb=None)

    model_mb = int(model_bytes // MIB)
    projector_mb = int(max(projector_bytes, 0) // MIB)
    required_mb = model_mb + projector_mb + headroom_mb

    if vram_mb >= required_mb:
        return OffloadPlan(
            layers=OFFLOAD_ALL_LAYERS, fits=True,
            reason=(f"the model needs {required_mb} MiB "
                    f"({model_mb} MiB of weights, {projector_mb} MiB of vision "
                    f"projector, {headroom_mb} MiB reserved for the key/value "
                    f"cache and compute buffers) and the card has {vram_mb} MiB"),
            vram_mb=vram_mb, required_mb=required_mb, total_layers=total_layers,
            per_layer_mb=None)

    if not total_layers or total_layers <= 0:
        return OffloadPlan(
            layers=0, fits=False,
            reason=(f"the model needs {required_mb} MiB and the card has "
                    f"{vram_mb} MiB, and the model's layer count could not be "
                    "read, so the number of layers that would fit cannot be "
                    "worked out; serving on the processor"),
            vram_mb=vram_mb, required_mb=required_mb, total_layers=None,
            per_layer_mb=None)

    per_layer_mb = model_mb / total_layers
    budget_mb = vram_mb - headroom_mb - projector_mb
    layers = int(math.floor(budget_mb / per_layer_mb)) if budget_mb > 0 else 0
    layers = max(0, min(layers, total_layers - 1))
    if layers == 0:
        reason = (f"the model needs {required_mb} MiB, the card has {vram_mb} "
                  f"MiB, and after the {headroom_mb} MiB reserve and the "
                  f"{projector_mb} MiB projector there is not room for one "
                  f"{per_layer_mb:.0f} MiB layer; serving on the processor")
    else:
        reason = (f"the whole model needs {required_mb} MiB and the card has "
                  f"{vram_mb} MiB, so {layers} of {total_layers} layers go on "
                  f"the card at about {per_layer_mb:.0f} MiB each and the rest "
                  "are served by the processor")
    return OffloadPlan(layers=layers, fits=False, reason=reason,
                       vram_mb=vram_mb, required_mb=required_mb,
                       total_layers=total_layers, per_layer_mb=per_layer_mb)


def _manifest_sizes_by_filename() -> dict:
    """``{filename: bytes}`` for every model and projector the manifest names.

    The signed manifest is the record that decides what the download path
    fetches, so it is the record the fit calculation measures against. A
    manifest that cannot be read yields an empty map and the caller falls back
    to the file on disk, which is the same bytes by a less authoritative route.
    """
    import json
    from pathlib import Path
    try:
        from intergen.model_manager import PINS_MANIFEST_PATH
        payload = json.loads(Path(PINS_MANIFEST_PATH).read_text(encoding="utf-8"))
        entries = payload["entries"]
    except (OSError, ValueError, KeyError, TypeError, ImportError):
        return {}
    sizes: dict = {}
    for entry in entries:
        try:
            if entry.get("filename") and entry.get("size_bytes"):
                sizes[str(entry["filename"])] = int(entry["size_bytes"])
            if entry.get("mmproj_filename") and entry.get("mmproj_size_bytes"):
                sizes[str(entry["mmproj_filename"])] = int(entry["mmproj_size_bytes"])
        except (AttributeError, TypeError, ValueError):
            continue
    return sizes


def _size_of(path, manifest: dict) -> int | None:
    from pathlib import Path
    if not path:
        return None
    name = Path(path).name
    if name in manifest:
        return manifest[name]
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


def plan_for_model(*, vram_mb: int | None, model_path, mmproj_path=None,
                   headroom_mb: int = KV_AND_COMPUTE_HEADROOM_MB) -> OffloadPlan:
    """The offload plan for the model that is about to be launched.

    Composes the three inputs the decision needs: the detected video memory, the
    model's and projector's sizes (signed manifest first, the file on disk as
    the fallback), and the model's layer count read from its own GGUF header.
    Any of them being unreadable is carried through honestly rather than
    substituted for.
    """
    manifest = _manifest_sizes_by_filename()
    model_bytes = _size_of(model_path, manifest)
    projector_bytes = _size_of(mmproj_path, manifest) or 0
    total_layers = None
    if model_path:
        try:
            from intergen.gguf import block_count
            blocks = block_count(model_path)
            if blocks:
                total_layers = offloadable_units(blocks)
        except Exception:                       # noqa: BLE001 — never block a launch
            total_layers = None
    return plan_offload(vram_mb=vram_mb, model_bytes=model_bytes,
                        projector_bytes=projector_bytes,
                        total_layers=total_layers, headroom_mb=headroom_mb)
