#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""ge-composed-path-assert.py — RT-2 composed-path (pressure-vessel) proof.

Parses `steam-runtime-system-info --verbose` JSON (stdin or --file) captured
INSIDE Valve's pressure-vessel container and asserts the imported 32- AND
64-bit graphics stacks resolve there. Component canaries (vulkaninfo32 on the
bare host) prove the host stack only; games run against the host stack
capsule-captured into a foreign Debian-based runtime — THAT composition is
what this asserts, per the GE redteam's RT-2 CRITICAL finding.

ASSERTIONS (fail-closed — a missing key/arch/malformed input is a FAIL that
names what could not be seen, never a pass):
  * both architectures present and can-run: x86_64-linux-gnu AND
    i386-linux-gnu;
  * for each, the Vulkan graphics stack resolves in-container: the
    <arch>/vulkan graphics-details entry exists, reports a non-empty
    renderer/device set, and carries no issues;
  * library-issues-summary for each architecture is empty (the capsule
    import left no unresolved libraries).

GLX resolution is REPORTED (informational) but not asserted here — the
render-path-in-use assertion is RT-13's item, and over-asserting GL would
mask nothing while false-failing headless eval runs.

Exit codes: 0 = composed path proven; 1 = assertion failure (named);
2 = input unreadable/malformed (also a failure — the gate could not see).

Schema note: written against steam-runtime-tools' JSON as shipped in the
sniper runtime. If Valve drifts the schema, missing keys land as loud
FAILs — the correct fail-closed direction — and the fixture suite in
tests/installer/test_ge_composed_path_assert.py pins OUR parsing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import List

ARCHES = (
    ("x86_64-linux-gnu", "x86_64"),
    ("i386-linux-gnu", "i386"),
)

# Re-certification finding F3-a: a Vulkan stack that "resolves" to a SOFTWARE rasterizer
# means the GPU driver did NOT capsule-import — games would run on the CPU
# while the proof reads green. That is the exact silent degradation this
# gate exists to catch, so a software renderer (or a cpu-type device) is a
# FAILURE, not a resolution. No headless false-fail cost: a GPU-less box
# hits the check category's SKIP/WARN before this assertion ever runs.
SOFTWARE_RENDERERS = re.compile(
    r"llvmpipe|lavapipe|swrast|swiftshader|softpipe", re.IGNORECASE)


def assert_composed_path(doc: dict) -> List[str]:
    """Returns a list of failures; empty list == the composed path proved."""
    failures: List[str] = []

    archs = doc.get("architectures")
    if not isinstance(archs, dict):
        return ["no 'architectures' object in the report — cannot see the "
                "in-container stacks; refusing to assume they resolve"]

    for triplet, short in ARCHES:
        arch = archs.get(triplet)
        if not isinstance(arch, dict):
            failures.append(
                f"{triplet}: architecture missing from the in-container "
                f"report — the {short} stack was not imported or not probed")
            continue

        can_run = arch.get("can-run")
        if can_run is not True:
            failures.append(
                f"{triplet}: can-run is {can_run!r} — the container cannot "
                f"execute this architecture at all")

        # Library health is reported EITHER as a `library-issues-summary`
        # list (empty == clean; older/other steam-runtime-tools versions) OR,
        # in the sniper schema confirmed on-metal 2026-07-08, as a
        # `libraries-ok` bool (+ `library-details`). Accept whichever the
        # report carries; fail-closed only when NEITHER is present — the gate
        # cannot see the capsule-import library state.
        if "library-issues-summary" in arch:
            issues = arch.get("library-issues-summary")
            if issues:
                failures.append(
                    f"{triplet}: unresolved libraries after capsule import: "
                    f"{issues}")
        elif "libraries-ok" in arch:
            if arch.get("libraries-ok") is not True:
                failures.append(
                    f"{triplet}: libraries-ok={arch.get('libraries-ok')!r} — "
                    f"unresolved libraries after capsule import")
        else:
            failures.append(
                f"{triplet}: no library-issues-summary or libraries-ok — "
                f"cannot see the capsule-import library state; refusing to "
                f"assume clean")

        gd = arch.get("graphics-details")
        if not isinstance(gd, dict):
            failures.append(
                f"{triplet}: no graphics-details — cannot see whether the "
                f"{short} graphics stack resolves in-container")
            continue
        # steam-runtime-tools keys graphics-details by <window-system>/<api>
        # (e.g. "x11/vulkan"), NOT by arch — the "<arch>/vulkan" spelling never
        # matched a real sniper report (confirmed on-metal 2026-07-08, where it
        # false-failed every stack). Find the vulkan entry by its api suffix,
        # window-system-agnostic; this also matches the older "<arch>/vulkan"
        # spelling, so back-compat holds.
        vk_key = next(
            (k for k in gd
             if isinstance(k, str) and k.rsplit("/", 1)[-1] == "vulkan"),
            None)
        vk = gd.get(vk_key) if vk_key else None
        if not isinstance(vk, dict):
            failures.append(
                f"{triplet}: no '*/vulkan' entry in graphics-details — the "
                f"{short} Vulkan stack did not resolve in-container")
            continue
        vk_issues = vk.get("issues")
        if vk_issues:
            failures.append(f"{triplet}: {vk_key} issues: {vk_issues}")
        renderer = vk.get("renderer") or ""
        devices = vk.get("devices") or []
        if not renderer and not devices:
            failures.append(
                f"{triplet}: {vk_key} reports no renderer and no devices — "
                f"Vulkan enumerates nothing inside the container")
        # F3-a: software fallback is a failure, not a resolution.
        if renderer and SOFTWARE_RENDERERS.search(renderer):
            failures.append(
                f"{triplet}: {vk_key} resolves to a SOFTWARE rasterizer "
                f"('{renderer}') — the GPU driver did not capsule-import; "
                f"games would run on the CPU")
        # F3-a device check. The device-type field is read under BOTH
        # spellings ("type" and srtool's hyphenated "device-type"). A cpu
        # fallback device enumerated ALONGSIDE real GPUs is the NORMAL Mesa
        # state — the on-metal capture (2026-07-08) shows x86_64 vulkan listing
        # NVIDIA + Intel + llvmpipe together, with NVIDIA as the renderer — so
        # "any cpu device present" is the wrong test (it would false-fail every
        # healthy Mesa box). The real failure is: the SELECTED renderer is
        # software (checked above), OR NO real (non-cpu) GPU is enumerated at
        # all. Fail only on the latter here.
        def _is_cpu(dev):
            return (isinstance(dev, dict) and str(
                dev.get("type", dev.get("device-type", ""))).lower() == "cpu")
        real_gpus = [d for d in devices if isinstance(d, dict) and not _is_cpu(d)]
        if devices and not real_gpus:
            names = ", ".join(
                str(d.get("name", "?")) for d in devices if isinstance(d, dict))
            failures.append(
                f"{triplet}: {vk_key} enumerates ONLY cpu-type device(s) "
                f"({names}) — the GPU driver did not capsule-import; games "
                f"would run on the CPU")

    return failures


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="RT-2 composed-path (pressure-vessel) assertion")
    ap.add_argument("--file", help="report JSON (default: stdin)")
    args = ap.parse_args(argv)

    try:
        if args.file:
            with open(args.file, "r") as fh:
                doc = json.load(fh)
        else:
            doc = json.load(sys.stdin)
    except (OSError, json.JSONDecodeError) as e:
        print(f"composed-path: FAIL — report unreadable/malformed "
              f"({e.__class__.__name__}: {e}) — the gate could not see; "
              f"refusing to wave through", file=sys.stderr)
        return 2

    failures = assert_composed_path(doc if isinstance(doc, dict) else {})
    if failures:
        print("composed-path: FAIL — the imported graphics stacks do NOT "
              "resolve inside the container:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    # Informational GLX report (asserted by RT-13's render-path item).
    for triplet, short in ARCHES:
        gd = doc["architectures"][triplet].get("graphics-details", {})
        glx_key = next(
            (k for k in gd if isinstance(k, str) and "glx" in k), None)
        glx = gd.get(glx_key, {}) if glx_key else {}
        r = glx.get("renderer") if isinstance(glx, dict) else None
        print(f"composed-path: info — {short} GLX renderer: "
              f"{r or '(not probed)'}", file=sys.stderr)

    print("composed-path: PASS — 32- and 64-bit Vulkan stacks resolve "
          "inside the pressure-vessel container", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
