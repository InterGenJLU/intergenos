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

THE CACHE IS THE SERVED MODEL'S OWN (2026-10-09). The reserve beyond the
weights used to be one constant measured on the 9B model, whose key/value cache
is 512 MiB at the shipped context, on the premise that no shipped model's cache
is larger. A smaller model is not always a smaller cache: the 2B model's is
1792 MiB at that context. On a 4 GB
card the constant called that load a fit, every layer was planned onto the
card, and the engine ran out of device memory allocating the vision projector,
at every start. The plan now reads the cache from the model's own header at the
configured context and charges it, with a reserve for the compute buffers and
the margin the engine itself keeps free; a header that does not give the
figures keeps the measured constant.
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
# WHEN THIS ONE NUMBER IS USED. Only when the model's own cache cannot be read
# from its header (see :func:`kv_cache_mb`). It was once reserved for every
# model on the premise that a smaller model needs a smaller cache; measured on
# the 2B model, that premise does not hold (1792 MiB at the same context).
KV_AND_COMPUTE_HEADROOM_MB = 1088

# The compute buffers and the fixed recurrent state of the measured load above,
# in MiB: 501.00 + 50.25, rounded up to the next 64 MiB. With that model's own
# cache (512 MiB at the shipped context) it is the constant above, so the model
# the constant was measured on is planned on the same figures either way.
COMPUTE_AND_STATE_RESERVE_MB = 576

# The memory the engine keeps free on a card when it fits a model itself, in
# MiB; its fitting step reports "cannot meet free memory target of 1024 MiB"
# when it cannot, and an explicit layer count switches that step off, so the
# plan keeps the same margin when it charges the model's own cache. The figures
# the engine names are not everything a load places on the card: measured on a
# 4 GB card, the engine projected 3143 MiB of weights, cache and compute buffer
# against 3937 MiB free, and the 607 MiB projector allocated after them failed.
ENGINE_FREE_MEMORY_TARGET_MB = 1024

# Bytes per stored element in the engine's default (16-bit) key/value cache.
KV_CACHE_BYTES_PER_ELEMENT = 2

# The context the launch passes the engine when the configuration names none.
DEFAULT_CONTEXT_SIZE = 16384


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


def kv_cache_mb(metadata: dict | None, context_size: int | None) -> int | None:
    """The key/value cache the engine allocates for a model at ``context_size``
    cells, in MiB, read from the model's own header, or ``None``.

    ``metadata`` is the header as :func:`intergen.gguf.read_metadata` returns
    it. Every layer that attends keeps, for each cell, a key and a value for
    each key/value head, at the engine's default two bytes per element.
    MEASURED against the engine's own figure: the 2B model's header (28 blocks,
    8 key/value heads, 128-wide keys and values) gives 1792 MiB at 16384 cells,
    the size the engine prints when it allocates that cache.

    A hybrid model whose header names its attention interval keeps a cache on
    one block in that many. A sliding-window model is charged as if every layer
    kept the whole context, which errs toward fewer layers on the card.

    ``None`` means the header does not give a figure this needs — a missing
    count or width, or a recurrent hybrid that does not say which of its layers
    attend — and the caller keeps the measured constant rather than charge a
    cache the header never described.
    """
    if not isinstance(metadata, dict):
        return None
    if isinstance(context_size, bool) or not isinstance(context_size, int) \
            or context_size <= 0:
        return None
    architecture = metadata.get("general.architecture")
    if not isinstance(architecture, str) or not architecture:
        return None

    def figure(name: str) -> int | None:
        value = metadata.get(f"{architecture}.{name}")
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return None
        return value

    blocks = figure("block_count")
    heads_kv = figure("attention.head_count_kv")
    if blocks is None or heads_kv is None:
        return None
    key_width = figure("attention.key_length")
    value_width = figure("attention.value_length")
    if key_width is None or value_width is None:
        embedding = figure("embedding_length")
        heads = figure("attention.head_count")
        if embedding is None or heads is None or embedding % heads:
            return None
        key_width = key_width or embedding // heads
        value_width = value_width or embedding // heads
    interval = figure("full_attention_interval")
    if interval is not None:
        attending = blocks // interval
    elif any(key.startswith(f"{architecture}.ssm.") for key in metadata):
        return None
    else:
        attending = blocks
    cache_bytes = (attending * context_size * heads_kv * (key_width + value_width)
                   * KV_CACHE_BYTES_PER_ELEMENT)
    return int(math.ceil(cache_bytes / MIB))


def plan_offload(*, vram_mb: int | None, model_bytes: int | None,
                 projector_bytes: int = 0, total_layers: int | None = None,
                 headroom_mb: int = KV_AND_COMPUTE_HEADROOM_MB,
                 kv_mb: int | None = None,
                 context_size: int | None = None) -> OffloadPlan:
    """Decide the offload for one model on one card.

    ``vram_mb`` is the detected card memory as the hardware detector reports it.
    ``model_bytes`` and ``projector_bytes`` come from the SIGNED models manifest
    — the same record the download path uses. ``total_layers`` is what
    ``--n-gpu-layers`` counts (see :func:`offloadable_units`); None means it
    could not be read, which forbids a partial answer rather than inventing one.

    ``kv_mb`` is the model's own key/value cache at the context it will be
    launched with (:func:`kv_cache_mb`), and ``context_size`` that context, for
    the words. When the cache is given, the reserve beyond the weights is that
    cache, :data:`COMPUTE_AND_STATE_RESERVE_MB` and
    :data:`ENGINE_FREE_MEMORY_TARGET_MB`, and a partial plan charges each layer
    its share of the cache with its weights; when it is not, ``headroom_mb``
    (the measured constant) is the reserve, as before.
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
    if kv_mb is None:
        fixed_mb = headroom_mb
        reserve_words = (f"{headroom_mb} MiB reserved for the key/value cache "
                         "and compute buffers")
        fixed_words = f"the {headroom_mb} MiB reserve"
        one_share_words = each_share_words = ""
    else:
        fixed_mb = COMPUTE_AND_STATE_RESERVE_MB + ENGINE_FREE_MEMORY_TARGET_MB
        headroom_mb = kv_mb + fixed_mb
        cache_words = f"{kv_mb} MiB of key/value cache" + (
            f" at {context_size} cells" if context_size else "")
        reserve_words = (f"{cache_words}, {COMPUTE_AND_STATE_RESERVE_MB} MiB "
                         "reserved for the compute buffers and fixed state, "
                         f"{ENGINE_FREE_MEMORY_TARGET_MB} MiB the engine keeps "
                         "free")
        fixed_words = (f"the {COMPUTE_AND_STATE_RESERVE_MB} MiB reserve, the "
                       f"{ENGINE_FREE_MEMORY_TARGET_MB} MiB the engine keeps free")
        one_share_words = " with its share of the cache"
        each_share_words = " with their share of the cache"
    required_mb = model_mb + projector_mb + headroom_mb
    # The constant's words stay exactly as they were; a plan that charged the
    # model's own cache says what that cache is wherever it states the need.
    needs_words = (f"{required_mb} MiB" if kv_mb is None
                   else f"{required_mb} MiB ({model_mb} MiB of weights, "
                        f"{projector_mb} MiB of vision projector, {reserve_words})")

    if vram_mb >= required_mb:
        return OffloadPlan(
            layers=OFFLOAD_ALL_LAYERS, fits=True,
            reason=(f"the model needs {required_mb} MiB "
                    f"({model_mb} MiB of weights, {projector_mb} MiB of vision "
                    f"projector, {reserve_words}) and the card has {vram_mb} MiB"),
            vram_mb=vram_mb, required_mb=required_mb, total_layers=total_layers,
            per_layer_mb=None)

    if not total_layers or total_layers <= 0:
        return OffloadPlan(
            layers=0, fits=False,
            reason=(f"the model needs {needs_words} and the card has "
                    f"{vram_mb} MiB, and the model's layer count could not be "
                    "read, so the number of layers that would fit cannot be "
                    "worked out; serving on the processor"),
            vram_mb=vram_mb, required_mb=required_mb, total_layers=None,
            per_layer_mb=None)

    per_layer_mb = model_mb / total_layers
    if kv_mb is not None:
        # The output layer is the extra unit (offloadable_units); the cache is
        # kept by the blocks.
        per_layer_mb += kv_mb / max(total_layers - 1, 1)
    budget_mb = vram_mb - fixed_mb - projector_mb
    layers = int(math.floor(budget_mb / per_layer_mb)) if budget_mb > 0 else 0
    layers = max(0, min(layers, total_layers - 1))
    if layers == 0:
        reason = (f"the model needs {needs_words}, the card has {vram_mb} "
                  f"MiB, and after {fixed_words} and the "
                  f"{projector_mb} MiB projector there is not room for one "
                  f"{per_layer_mb:.0f} MiB layer{one_share_words}; serving on the "
                  "processor")
    else:
        reason = (f"the whole model needs {needs_words} and the card has "
                  f"{vram_mb} MiB, so {layers} of {total_layers} layers go on "
                  f"the card at about {per_layer_mb:.0f} MiB each"
                  f"{each_share_words} and the rest "
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


def _configured_context_size() -> int | None:
    """The context the launch will pass the engine: the configuration's
    ``llm.context_size``, read through the same key and default the launch
    path reads, or ``None`` when it cannot be read as a positive count."""
    try:
        from intergen.config import Config
        value = Config().get("llm.context_size", DEFAULT_CONTEXT_SIZE)
    except Exception:                           # noqa: BLE001 — never block a launch
        return None
    if isinstance(value, bool):
        return None
    try:
        size = int(value)
    except (TypeError, ValueError):
        return None
    return size if size > 0 else None


def plan_for_model(*, vram_mb: int | None, model_path, mmproj_path=None,
                   headroom_mb: int = KV_AND_COMPUTE_HEADROOM_MB,
                   context_size: int | None = None) -> OffloadPlan:
    """The offload plan for the model that is about to be launched.

    Composes the inputs the decision needs: the detected video memory, the
    model's and projector's sizes (signed manifest first, the file on disk as
    the fallback), and the model's layer count and key/value cache read from
    its own GGUF header, the cache at ``context_size`` cells — the configured
    context the launch passes the engine when the caller names none. Any of
    them being unreadable is carried through honestly rather than substituted
    for.
    """
    manifest = _manifest_sizes_by_filename()
    model_bytes = _size_of(model_path, manifest)
    projector_bytes = _size_of(mmproj_path, manifest) or 0
    total_layers = None
    kv_mb = None
    if model_path:
        try:
            from intergen.gguf import block_count
            blocks = block_count(model_path)
            if blocks:
                total_layers = offloadable_units(blocks)
        except Exception:                       # noqa: BLE001 — never block a launch
            total_layers = None
        if context_size is None:
            context_size = _configured_context_size()
        try:
            from intergen.gguf import read_metadata
            kv_mb = kv_cache_mb(read_metadata(model_path), context_size)
        except Exception:                       # noqa: BLE001 — never block a launch
            kv_mb = None
    return plan_offload(vram_mb=vram_mb, model_bytes=model_bytes,
                        projector_bytes=projector_bytes,
                        total_layers=total_layers, headroom_mb=headroom_mb,
                        kv_mb=kv_mb, context_size=context_size)
