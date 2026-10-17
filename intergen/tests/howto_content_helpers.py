# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Read the checkout's curated answers through their production loader."""

from functools import lru_cache
from pathlib import Path
import re
import shlex

from intergen.howto import HowtoCorpus

DATA = Path(__file__).resolve().parents[1] / "data" / "howto"


@lru_cache(maxsize=1)
def entries():
    corpus = HowtoCorpus(data_dir=DATA)
    result = {item.id: item for item in corpus._entries}
    assert result and len(result) == corpus.entry_count
    return result


def entry(identifier):
    assert identifier in entries(), f"missing curated entry: {identifier}"
    return entries()[identifier]


def answer(identifier):
    return entry(identifier).answer


def commands(text):
    """Executable examples, excluding prose, inline negations and comments."""
    result = []
    for block in re.findall(r"```[^\n]*\n(.*?)```", text, re.S):
        for line in block.splitlines():
            parts = shlex.split(line, comments=True)
            if parts:
                result.append(parts)
    return result
