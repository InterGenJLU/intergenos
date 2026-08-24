# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The model-header reader returns the layer count, or says it could not.

WHY IT MATTERS. The offload plan uses the layer count to work out how many
layers fit a card that cannot hold the whole model. A wrong count produces a
confident partial offload that does not fit; an invented count is worse than no
count, because the plan's own rule is to decline a partial answer when the
number is unknown. So this file pins both halves: the number is read correctly
from a well-formed header, and every malformed, truncated, foreign or hostile
input returns None rather than a number.

EVERY FIXTURE IS BUILT HERE. The test writes the headers it reads, so the result
does not depend on which model files happen to exist on the machine running it.
The reader's behaviour against the real shipped model files is proven
separately, at authoring time, and recorded in this branch's evidence — a suite
that reads whatever is installed is not a test of the reader.

Nothing here writes outside a temporary directory or needs privilege.
"""
from __future__ import annotations

import struct

import pytest

from intergen.gguf import (MAX_METADATA_ENTRIES, MAX_STRING_BYTES,
                           block_count, read_metadata)

_UINT32, _STRING, _ARRAY = 4, 8, 9


def _string(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack("<Q", len(raw)) + raw


def _kv_string(key: str, value: str) -> bytes:
    return _string(key) + struct.pack("<I", _STRING) + _string(value)


def _kv_uint32(key: str, value: int) -> bytes:
    return _string(key) + struct.pack("<I", _UINT32) + struct.pack("<I", value)


def _kv_string_array(key: str, values: list[str]) -> bytes:
    body = b"".join(_string(v) for v in values)
    return (_string(key) + struct.pack("<I", _ARRAY)
            + struct.pack("<I", _STRING) + struct.pack("<Q", len(values)) + body)


def _header(entries: list[bytes], *, version: int = 3,
            declared: int | None = None, magic: bytes = b"GGUF") -> bytes:
    count = len(entries) if declared is None else declared
    return (magic + struct.pack("<I", version) + struct.pack("<Q", 0)
            + struct.pack("<Q", count) + b"".join(entries))


def _write(tmp_path, payload: bytes, name: str = "model.gguf"):
    path = tmp_path / name
    path.write_bytes(payload)
    return path


# ── the number is read ───────────────────────────────────────────────────────

def test_the_layer_count_is_read_from_a_well_formed_header(tmp_path):
    path = _write(tmp_path, _header([
        _kv_string("general.architecture", "qwen35"),
        _kv_uint32("qwen35.block_count", 32),
        _kv_uint32("qwen35.attention.head_count", 40),
    ]))
    assert block_count(path) == 32


def test_a_vocabulary_sized_array_is_walked_and_not_returned(tmp_path):
    """The large keys must not break the walk, and must not come back either."""
    path = _write(tmp_path, _header([
        _kv_string("general.architecture", "gemma3"),
        _kv_string_array("tokenizer.ggml.tokens", [f"tok{i}" for i in range(512)]),
        _kv_uint32("gemma3.block_count", 34),
    ]))
    assert block_count(path) == 34
    metadata = read_metadata(path)
    assert "tokenizer.ggml.tokens" not in metadata
    assert metadata["general.architecture"] == "gemma3"


def test_a_single_unqualified_layer_count_is_accepted(tmp_path):
    """A file whose architecture string is absent but which states one count."""
    path = _write(tmp_path, _header([_kv_uint32("llama.block_count", 26)]))
    assert block_count(path) == 26


def test_two_disagreeing_layer_counts_are_refused(tmp_path):
    """Ambiguity is not resolved by picking one; it is reported as unknown."""
    path = _write(tmp_path, _header([
        _kv_uint32("llama.block_count", 26),
        _kv_uint32("clip.block_count", 27),
    ]))
    assert block_count(path) is None


def test_the_architecture_qualified_key_wins_over_a_sibling(tmp_path):
    path = _write(tmp_path, _header([
        _kv_string("general.architecture", "qwen35"),
        _kv_uint32("qwen35.block_count", 32),
        _kv_uint32("clip.block_count", 27),
    ]))
    assert block_count(path) == 32


# ── everything else says it could not ────────────────────────────────────────

def test_an_absent_file_is_none(tmp_path):
    assert block_count(tmp_path / "nothing-here.gguf") is None


def test_a_file_that_is_not_a_model_is_none(tmp_path):
    assert block_count(_write(tmp_path, b"this is not a model file\n")) is None


def test_an_unsupported_header_version_is_refused(tmp_path):
    path = _write(tmp_path, _header([_kv_uint32("llama.block_count", 26)],
                                    version=99))
    assert block_count(path) is None


def test_a_truncated_header_is_none(tmp_path):
    whole = _header([_kv_string("general.architecture", "qwen35"),
                     _kv_uint32("qwen35.block_count", 32)])
    assert block_count(_write(tmp_path, whole[:len(whole) // 2])) is None


def test_a_header_claiming_more_entries_than_it_has_is_none(tmp_path):
    path = _write(tmp_path, _header([_kv_uint32("llama.block_count", 26)],
                                    declared=9))
    assert block_count(path) is None


def test_a_header_with_no_layer_count_is_none(tmp_path):
    path = _write(tmp_path, _header([_kv_string("general.architecture", "qwen35"),
                                     _kv_uint32("qwen35.attention.head_count", 40)]))
    assert block_count(path) is None


def test_a_zero_layer_count_is_not_a_layer_count(tmp_path):
    path = _write(tmp_path, _header([_kv_string("general.architecture", "qwen35"),
                                     _kv_uint32("qwen35.block_count", 0)]))
    assert block_count(path) is None


def test_an_unknown_value_type_stops_the_read(tmp_path):
    entry = _string("weird.key") + struct.pack("<I", 250) + b"\x00" * 8
    path = _write(tmp_path, _header([entry, _kv_uint32("llama.block_count", 26)]))
    assert block_count(path) is None


# ── the bounds are real, not decorative ──────────────────────────────────────

def test_an_absurd_entry_count_is_refused_without_allocating_for_it(tmp_path):
    path = _write(tmp_path, _header([], declared=MAX_METADATA_ENTRIES + 1))
    assert block_count(path) is None


def test_an_absurd_string_length_is_refused_without_allocating_for_it(tmp_path):
    entry = struct.pack("<Q", MAX_STRING_BYTES + 1) + b"x" * 16
    path = _write(tmp_path, _header([entry], declared=1))
    assert block_count(path) is None


@pytest.mark.parametrize("magic", [b"GGML", b"\x00\x00\x00\x00", b"GGU"])
def test_control_a_wrong_magic_is_always_refused(tmp_path, magic):
    """Control: the magic check is what rejects a foreign file, and it fires."""
    payload = _header([_kv_uint32("llama.block_count", 26)], magic=magic[:4])
    assert block_count(_write(tmp_path, payload)) is None
