# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Read the few model facts the serving decision needs, from the model file.

WHY THIS EXISTS. Deciding how much of a model fits on a graphics card needs the
model's LAYER COUNT, and nothing else in this tree knew it. The signed models
manifest records each model's size on disk but not its shape, and the engine
prints the layer count only in the banner it writes AFTER it has already been
told how many layers to offload — which is too late to be an input to that
decision. The number is sitting in the model file's own header, so this module
reads it there.

WHAT IT READS. The GGUF header's metadata section only: a table of key/value
pairs written before the tensor data. Two keys matter — ``general.architecture``,
and the ``<architecture>.block_count`` that names how many repeating transformer
layers the model has. No tensor data is read, and the file is never written.

FAIL-CLOSED AND QUIET. Every failure path returns ``None``: a file that is
absent, unreadable, not GGUF, of an unknown version, or whose header does not
carry a layer count. A caller that gets ``None`` must say so rather than
substituting a guess — a made-up layer count would produce a confident partial
offload that does not fit, which is worse than declining to offload partially.
The reader is also bounded: it refuses absurd counts and lengths rather than
allocating whatever a malformed or hostile file claims, and it stops at the end
of the metadata section without ever seeking into the payload.

Format reference: the GGUF specification, ggml/docs/gguf.md.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

log = logging.getLogger(__name__)

GGUF_MAGIC = b"GGUF"
# Versions this reader has been written against. A newer version may lay the
# header out differently, and reading it as if it had not would produce a
# confident wrong number — refuse instead.
SUPPORTED_VERSIONS = (2, 3)

# Bounds. These are not tuning knobs; they are the difference between reading a
# header and letting a file's own claims decide how much memory to allocate.
MAX_METADATA_ENTRIES = 8192
MAX_STRING_BYTES = 64 * 1024
MAX_ARRAY_ELEMENTS = 1 << 20
MAX_HEADER_BYTES = 64 * 1024 * 1024

# GGUF metadata value type codes.
(_UINT8, _INT8, _UINT16, _INT16, _UINT32, _INT32, _FLOAT32, _BOOL, _STRING,
 _ARRAY, _UINT64, _INT64, _FLOAT64) = range(13)

_FIXED = {
    _UINT8: ("<B", 1), _INT8: ("<b", 1),
    _UINT16: ("<H", 2), _INT16: ("<h", 2),
    _UINT32: ("<I", 4), _INT32: ("<i", 4),
    _FLOAT32: ("<f", 4), _BOOL: ("<B", 1),
    _UINT64: ("<Q", 8), _INT64: ("<q", 8),
    _FLOAT64: ("<d", 8),
}


class _Truncated(Exception):
    """The header ended, or went past its bound, before the value did."""


class _Reader:
    def __init__(self, handle) -> None:
        self._handle = handle
        self._consumed = 0

    def take(self, count: int) -> bytes:
        if count < 0 or self._consumed + count > MAX_HEADER_BYTES:
            raise _Truncated(f"header would exceed {MAX_HEADER_BYTES} bytes")
        chunk = self._handle.read(count)
        if len(chunk) != count:
            raise _Truncated(f"wanted {count} bytes, got {len(chunk)}")
        self._consumed += count
        return chunk

    def fixed(self, kind: int):
        fmt, size = _FIXED[kind]
        return struct.unpack(fmt, self.take(size))[0]

    def length(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def string(self) -> str:
        size = self.length()
        if size > MAX_STRING_BYTES:
            raise _Truncated(f"string of {size} bytes exceeds the bound")
        return self.take(size).decode("utf-8", errors="replace")

    def value(self, kind: int):
        if kind in _FIXED:
            return self.fixed(kind)
        if kind == _STRING:
            return self.string()
        if kind == _ARRAY:
            element_kind = struct.unpack("<I", self.take(4))[0]
            count = self.length()
            if count > MAX_ARRAY_ELEMENTS:
                raise _Truncated(f"array of {count} elements exceeds the bound")
            # The values this module needs are never arrays, so the elements are
            # walked to stay in step with the file and then discarded.
            for _ in range(count):
                self.value(element_kind)
            return None
        raise _Truncated(f"unknown metadata value type {kind}")


def read_metadata(path: Path | str) -> dict | None:
    """Every scalar metadata key in a GGUF header, or ``None``.

    Array-valued keys (the tokenizer vocabulary, chiefly) are walked so the read
    stays in step with the file, and are not returned — nothing here needs them
    and they are the large ones.
    """
    try:
        with open(path, "rb") as handle:
            reader = _Reader(handle)
            if reader.take(4) != GGUF_MAGIC:
                log.debug("%s is not a GGUF file", path)
                return None
            version = struct.unpack("<I", reader.take(4))[0]
            if version not in SUPPORTED_VERSIONS:
                log.debug("%s is GGUF version %d, which this reader does not "
                          "claim to understand", path, version)
                return None
            reader.length()                      # tensor count, unused here
            entries = reader.length()
            if entries > MAX_METADATA_ENTRIES:
                log.debug("%s declares %d metadata entries, past the bound",
                          path, entries)
                return None
            found: dict = {}
            for _ in range(entries):
                key = reader.string()
                kind = struct.unpack("<I", reader.take(4))[0]
                value = reader.value(kind)
                if value is not None:
                    found[key] = value
            return found
    except (OSError, struct.error, _Truncated, ValueError) as exc:
        log.debug("could not read GGUF metadata from %s: %s", path, exc)
        return None


def block_count(path: Path | str) -> int | None:
    """How many repeating layers the model at ``path`` has, or ``None``.

    ``None`` means the number could not be established from the file. It is not
    a zero and must never be treated as one.
    """
    metadata = read_metadata(path)
    if not metadata:
        return None
    architecture = metadata.get("general.architecture")
    keys = []
    if isinstance(architecture, str) and architecture:
        keys.append(f"{architecture}.block_count")
    # Some converters write the count under the architecture the file declares
    # and some under a sibling spelling; accept any single block_count key when
    # the architecture-qualified one is absent, and refuse when several disagree.
    fallback = {k: v for k, v in metadata.items() if k.endswith(".block_count")}
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, int) and value > 0:
            return int(value)
    values = {v for v in fallback.values() if isinstance(v, int) and v > 0}
    if len(values) == 1:
        return int(next(iter(values)))
    return None
