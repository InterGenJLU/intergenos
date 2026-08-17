# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Answer-responsiveness — the deterministic question/answer coherence check.

Every structural assertion in the harness grades the answer against the *trace*
(did the right route fire, did the right tool run, is the claim backed). None of
them compares the answer to the QUESTION. That gap produced a real false pass:
a turn asking ``"search for a pdf editor"`` was answered ``"Disk usage is
available."`` and graded PASS, because every assertion it carried — route
source, dispatched tool, tool argument — was individually satisfied. The answer
was simply about something else.

This module closes that gap for the class where the answer's subject is
DETERMINABLE, with no model in the loop.

The determinable class: router-owned template answers
-----------------------------------------------------
The deterministic (no-LLM) answer path emits a FIXED set of single-line
templates — ``intergen/router.py`` ``_template_synthesis`` and the
``_summarize_*`` helpers it calls. Each template states exactly one subject:
disk, memory, cpu, gpu, usb, block devices, os, kernel, hostname, uptime, time,
ip, architecture. A template answer is therefore self-identifying: its shape
names its subject, independent of any trace.

The invariant this module enforces
----------------------------------
**A delivered system-state template must be topically LICENSED by the question.**
If the answer states the disk, the question has to have asked about the disk. A
question that never names the delivered subject did not get an answer to what it
asked, whatever the trace says about routing.

Fail-closed within the determinable domain: once the reply is identified as a
topic-bearing template, the turn FAILS unless the question POSITIVELY licenses
that topic. Absence of a licence is a failure, never a pass.

Why the licence cues are word-anchored (this is load-bearing)
-------------------------------------------------------------
The selector this checks is itself substring-matched, and that is how the
observed defect was produced: ``_template_synthesis`` chooses the disk summary
when ``any(k in lower for k in ("disk", "storage", "space", "df"))`` — and
``"df"`` is a substring of ``"pdf"``. So ``"search for a pdf editor"`` selects
the disk template for whatever output it is handed. Reproduced directly:

    >>> from intergen.router import ConversationRouter as R          # doctest: +SKIP
    >>> R._template_synthesis("search for a pdf editor", "a\\nmulti-line output")
    'Disk usage is available.'

If this module matched its licence cues the same unanchored way, ``"pdf"`` would
license the disk topic and the gate would wave through the exact defect it
exists to catch. Every cue here is therefore matched on WORD boundaries. This is
a deliberate divergence from the selector's matching, not an oversight.

What this catches / what it does NOT
------------------------------------
CATCHES: a single-line deterministic system-state template whose subject the
question never names — including the substring-collision class above, and the
general case of a cached or mis-selected reading being delivered under an
unrelated question.

DOES NOT CATCH (stated plainly, no silent coverage claim):

* free-form / model-synthesized answers — they carry no template shape, so the
  subject is not determinable without a judge, and a judge is not admissible as
  this gate (a model screen over the same corpus composed the proven false pass
  to flag while over-failing correct refusals);
* an on-topic template that is factually WRONG or stale — e.g. a cached disk
  reading re-delivered for a genuine disk question is licensed here and must be
  caught by the grounding assertions instead;
* multi-line / raw output deliveries (an explicit "raw"/"verbatim" ask hands
  back the unsummarized command output, which is deliberately not a template);
* an answer on the right subject that answers a different sub-question;
* a question that names the subject incidentally while asking something else —
  the licence is granted and the turn is not flagged.

Nothing here reaches into the daemon: the check reads only the question as sent
and the reply as delivered.
"""

from __future__ import annotations

import re

# ── the router's template families, keyed by the ONE subject each states ──
#
# Every entry is a shape emitted by intergen/router.py's deterministic answer
# path (``_template_synthesis`` + the ``_summarize_*`` helpers). Shapes are
# matched against the WHOLE stripped reply, which is always a single line for
# this family — that is also what keeps a multi-line raw-output delivery out of
# the check entirely.
#
# Each value is (prefixes, exact_sentences, regexes). A reply matches a topic
# when it starts with one of the prefixes, equals one of the sentences, or
# matches one of the regexes.
_TEMPLATE_SHAPES: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[re.Pattern[str], ...]]] = {
    # _summarize_disk
    "disk": (("disk: ",), ("disk usage is available.",), ()),
    # _summarize_memory
    "memory": (("ram: ",), ("memory usage is available.",), ()),
    # _summarize_cpu + the nproc core-count wrapper
    "cpu": (("cpu: ",), ("cpu information is available.",),
            (re.compile(r"^this machine has \d+ cpu cores?\.$"),)),
    # _summarize_gpu
    "gpu": (("gpu: ",), ("gpu information is available.",), ()),
    # _summarize_usb
    "usb": ((), ("no external usb devices detected.",),
            (re.compile(r"^\d+ usb devices?, including .+\.$"),)),
    # _summarize_block
    "block": ((), ("block device information is available.",),
              (re.compile(r"^\d+ disks?: .+\.$"),)),
    # _summarize_os
    "os": (("os: ",), ("operating system information is available.",), ()),
    # the single-line value wrappers
    "kernel": (("you're running kernel ",), (), ()),
    "hostname": (("your hostname is ",), (), ()),
    "uptime": (("system uptime: ",), (), ()),
    "time": (("it's currently ",), (), ()),
    "ip": (("your ip address is ",), (), ()),
    "architecture": (("architecture: ",), (),
                     (re.compile(r"^this is a (?:32|64)-bit system \(.+\)\.$"),)),
}

# ── per-topic licence cues, matched on WORD BOUNDARIES (see module docstring) ──
#
# A cue set answers one question only: "does the question name this subject at
# all?" It is not a router-selector replica and does not decide routing. Cues
# that collide across topics are deliberately omitted rather than shared: "free"
# is a memory selector key in the router but is dropped here because "free
# space" is a disk question, and a shared cue would license the wrong template.
_TOPIC_CUES: dict[str, tuple[str, ...]] = {
    "disk": ("disk", "disks", "df", "space", "storage", "drive", "drives",
             "filesystem", "filesystems", "partition", "partitions",
             "volume", "volumes", "capacity", "gb", "gigabytes", "terabytes"),
    "memory": ("memory", "ram", "swap", "meminfo"),
    "cpu": ("cpu", "cpus", "processor", "processors", "core", "cores",
            "lscpu", "load"),
    "gpu": ("gpu", "gpus", "graphics", "vga", "video", "nvidia", "amd",
            "radeon", "geforce", "nouveau", "card"),
    "usb": ("usb", "peripheral", "peripherals", "plugged", "lsusb",
            "attached", "dongle"),
    "block": ("disk", "disks", "drive", "drives", "block", "lsblk",
              "partition", "partitions", "storage", "nvme", "ssd"),
    "os": ("os", "distro", "distribution", "release", "version",
           "intergenos"),
    "kernel": ("kernel", "uname"),
    "hostname": ("hostname", "host", "called", "name", "machine", "computer",
                 "box", "system"),
    "uptime": ("uptime", "up", "running", "rebooted", "reboot", "boot",
               "long"),
    "time": ("time", "clock", "date", "day", "today", "now"),
    "ip": ("ip", "address", "network", "lan", "subnet"),
    "architecture": ("architecture", "arch", "bit", "bits", "x86_64", "amd64",
                     "aarch64", "arm64"),
}

# Multi-word cues have to be matched as phrases, not as a bag of words, or a
# single common word would license a topic on its own.
_TOPIC_PHRASES: dict[str, tuple[str, ...]] = {
    "os": ("operating system",),
    "usb": ("plugged in", "hooked up"),
    "gpu": ("video card", "graphics card", "display adapter"),
    "ip": ("ip address", "network address"),
    "architecture": ("32 bit", "64 bit", "32-bit", "64-bit", "32 or 64"),
    "uptime": ("been running", "been up", "how long"),
    "hostname": ("machine name", "computer name", "box name", "host name"),
}

# A system-overview ask licenses EVERY topic: one question legitimately draws
# several readings ("system info" resolves to `uname -a && free -h && df -h`),
# so a disk or memory template under it is responsive.
_UMBRELLA_PHRASES: tuple[str, ...] = (
    "system info", "system information", "system status", "system health",
    "system overview", "specs", "spec sheet", "hardware", "diagnostics",
    "about this system", "about this machine", "about this computer",
    "about my system", "about my machine", "about my computer",
    "tell me about my", "how is my system", "how's my system",
    "how is my machine", "how's my machine", "overview of",
    "everything about", "full report", "health check",
)

# An explicit raw/verbatim ask hands back the unsummarized command output rather
# than a template (the router's own first branch, which keys on exactly these).
# Such a turn is outside the determinable class, so the check makes no claim on
# it. Word cues and phrase cues are kept apart because the words must be
# word-anchored ("raw" must not match "drawer") while the phrases are literal.
_RAW_REQUEST_WORDS: frozenset[str] = frozenset({"raw", "verbatim"})
_RAW_REQUEST_PHRASES: tuple[str, ...] = (
    "full output", "full table", "exact output",
)


def _words(text: str) -> set[str]:
    """The question's word set, lowercased. Word extraction is what keeps a cue
    from matching INSIDE another word — the "pdf" contains "df" collision that
    produced the defect this gate catches."""
    return set(re.findall(r"[a-z0-9_]+", (text or "").lower()))


def answer_topic(reply: str) -> str | None:
    """The subject a deterministic template answer states, or None.

    None means the reply is not a recognized template — a free-form answer, a
    multi-line delivery, or anything else outside the determinable class. The
    check then makes no claim about it (see the module docstring's
    does-not-catch list).
    """
    text = (reply or "").strip()
    if not text or "\n" in text:
        return None
    low = text.lower()
    for topic, (prefixes, sentences, regexes) in _TEMPLATE_SHAPES.items():
        if low in sentences:
            return topic
        if any(low.startswith(p) for p in prefixes):
            return topic
        if any(rx.match(low) for rx in regexes):
            return topic
    return None


def question_licenses(question: str, topic: str) -> bool:
    """True when the question names ``topic`` (word-anchored) or is an overview ask."""
    low = (question or "").lower()
    if any(p in low for p in _UMBRELLA_PHRASES):
        return True
    if any(p in low for p in _TOPIC_PHRASES.get(topic, ())):
        return True
    return bool(_words(low) & set(_TOPIC_CUES.get(topic, ())))


def is_raw_request(question: str) -> bool:
    """True when the question explicitly asks for raw/verbatim output — the
    router hands those back unsummarized, so no template is expected."""
    low = (question or "").lower()
    return bool(_words(low) & _RAW_REQUEST_WORDS) or any(
        p in low for p in _RAW_REQUEST_PHRASES)


def responsiveness_finding(question: str, reply: str) -> str | None:
    """The incoherence finding for one turn, or None when nothing is determinable.

    Returns a human-readable reason string when the delivered answer states a
    subject the question never asked about — the fail case. Returns None when the
    reply is not a recognized template (nothing determinable) or when the
    question licenses the delivered subject (responsive).
    """
    topic = answer_topic(reply)
    if topic is None:
        return None
    if is_raw_request(question):
        return None
    if question_licenses(question, topic):
        return None
    cues = ", ".join(sorted(set(_TOPIC_CUES.get(topic, ()))
                            | set(_TOPIC_PHRASES.get(topic, ())))[:8])
    return (f"answer states {topic!r} state ({(reply or '').strip()[:80]!r}) but the "
            f"question names no {topic} subject (looked for: {cues}, …): "
            f"{(question or '').strip()[:80]!r}")
