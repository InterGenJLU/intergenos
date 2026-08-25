#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Join a run of added lines so a gate can match a term across a line wrap.

WHY THIS EXISTS, measured before it was written. The push-time language gate and
the writing-register gate both matched each ADDED LINE on its own. A term whose
spelling is two words therefore passed whenever the author's editor wrapped the
line between them — the two halves landed on different lines and neither line
carried the whole term. Reproduced on the real gate against the real term list:
a paragraph carrying one multi-word term wrapped across two lines and the same
term again on a single line reported ONE hit, the single-line one.

WHAT THIS MODULE PROVIDES, and nothing more: the text of a run of lines joined
by a single space, plus the map back from an offset in that joined text to the
line the offset came from. Deciding what to match, what is exempt and what to
print stays with each gate — this file has no opinion about any of it and
carries no vocabulary of its own.

TWO RULES THE JOIN OBEYS.

  1. ONLY CONSECUTIVE LINES ARE JOINED. Runs are split wherever the line numbers
     stop being consecutive, so two lines that merely both changed do not become
     neighbours. Joining line 10 to line 12 would invent an adjacency the file
     does not have, and a gate that refuses on invented text is worse than one
     that misses a real hit: the first teaches people to route around it.
  2. THE JOIN IS ONE SPACE, and the wrap's own whitespace is normalized into
     it: each line is stripped of its leading and trailing whitespace before
     the join, so a continuation line that an editor indented still reads as
     one space after the word before it. This matters for a matcher whose
     multi-word patterns are spelled with a single literal space — the writing-
     register gate's tiers are — and costs the other matcher nothing, since it
     already accepts a separator run between the words of a term. Whitespace
     inside a line is left exactly as the author wrote it.

A match that begins and ends inside one line reports exactly what it reported
before this module existed. Only a match that crosses a join is new.
"""
from __future__ import annotations


def consecutive_runs(lines):
    """Split [(lineno, text), ...] into runs of consecutive line numbers.

    The input is taken in the order given; a line number that is not one more
    than its predecessor starts a new run. A gate that also drops lines for its
    own reasons — a trailer, a line outside a prose zone — must drop them BEFORE
    calling this, so the drop shows up here as a break rather than as a silent
    join across the gap.
    """
    runs = []
    current = []
    expected = None
    for lineno, text in lines:
        if expected is not None and lineno != expected:
            if current:
                runs.append(current)
            current = []
        current.append((lineno, text))
        expected = lineno + 1
    if current:
        runs.append(current)
    return runs


class JoinedText:
    """One run of lines as a single string, with the map back to the lines."""

    __slots__ = ("text", "_entries")

    def __init__(self, run):
        parts = []
        entries = []
        pos = 0
        for lineno, text in run:
            stripped = text.strip()     # the wrap's whitespace becomes the join
            entries.append((pos, pos + len(stripped), lineno, stripped))
            parts.append(stripped)
            pos += len(stripped) + 1    # the one joining space
        self.text = " ".join(parts)
        self._entries = entries

    def iter_lines(self):
        """(start_offset, lineno, text) for each line, in order.

        A caller that computes spans on a single line — an exemption, say —
        shifts them by start_offset to place them in joined coordinates.
        """
        for start, _end, lineno, text in self._entries:
            yield start, lineno, text

    def locate(self, offset):
        """(lineno, text) of the line an offset falls in.

        An offset landing on a joining space belongs to the line BEFORE it: the
        space was added by the join and is not the author's byte. This is the
        function that answers "which line does a match START on", which is what
        both gates report.
        """
        last = self._entries[0]
        for start, end, lineno, text in self._entries:
            if start <= offset < end:
                return lineno, text
            if offset < start:
                return last[2], last[3]
            last = (start, end, lineno, text)
        return self._entries[-1][2], self._entries[-1][3]

    def crosses_a_line(self, start, end):
        """True when a match does not begin and end inside one line."""
        return self.locate(start)[0] != self.locate(max(start, end - 1))[0]
