# M4 capability-grounding data

**Status:** shipped. This document describes the three data inputs behind the
"grounded claims" work (the anti-fabrication move). Both consumers have since
landed: the claim gate reads `capability-surface.json` (exercised by
`intergen/tests/test_capability_screen.py`, which calls it the shipped gate),
and the read-only direct-execution path in `intergen/router.py` grounds against
`readonly-state-map.json` (with a missing or unreadable map degrading loudly
rather than silently). The data files ship at `intergen/data/`.

M4's job is to stop InterGen from *inventing* a capability — a `pkm add`
subcommand that does not exist, a tool it does not have — and to let a plain
read-only state question ("are there printers?", "what disks do I have?") answer
by running the one safe command directly. Both need a **ground-truth surface**
to check a claim against, and a **class-to-command map** for the read-only path.
Those are the three artifacts below.

## 1. `intergen/data/capability-surface.json`

The real, machine-readable capability surface, produced by **introspecting the
live code**, never hand-transcribed:

- **pkm** — every subcommand (with aliases, positionals, flags, enum choices and
  nested sub-parsers) walked out of the real `pkm/cli.py` argparse parser. This
  is the authority a claim like "run `pkm add`" is checked against: `add` is not
  in the surface, so the claim is a fabrication. The subcommand list is not
  reproduced here — it is in the artifact, and a copy in prose is one more thing
  that can be wrong.
- **forge** — its parser, from `installer.__main__.build_parser()`.
- **intergen** — its command set and each command's options, read from the
  dispatch chain in `intergen/cli.py:main()` and its `cmd_*` handlers with
  `ast`. That CLI has no argparse parser to ask; the dispatcher is the
  interface, so the dispatcher is what gets read.
- **`igos-*`** — the commands the package recipes install under `/usr/bin`, from
  their `verify_paths`. These are shell scripts: the NAME is derived and
  checkable, the argument surface is not, and each entry says so with
  `introspected: false`. The one exception, `igos-game-window-density`, has a
  real parser and is walked like the others.
- **intergen tools** — every built-in tool discovered by
  `ToolRegistry.discover_tools()`, with its schema parameters, safety tier, and
  whether it escalates (the `_PRIVILEGED_TOOLS` set). This is the authority for
  "InterGen can do X". This block is carried forward by the generator rather
  than re-derived by it, and the artifact's `_meta` records that.

**Regeneration:** `python3 scripts/gen-capability-surface.py`, which walks the
real parsers and the real dispatcher. `--check` compares the shipped artifact
against them and exits non-zero on any difference, writing nothing;
`intergen/tests/test_capability_command_surface.py` runs exactly that, so a
drifted artifact is a test failure.

> **The claim this section used to make, and what it cost.** This document said
> the surface "cannot drift from the code the way a hand-kept list does — which
> is the point", and the artifact's own `_meta` named a generator,
> `gen_capsurface.py`. That generator was never in the tree. The file could
> therefore only be refreshed by hand, which is the very thing the claim denied,
> and it drifted: measured 2026-08-26 against `pkm/cli.py`, the shipped artifact
> was missing three real subcommands (`vacuum`, `hook-baseline`,
> `record-hook-changes`) and four real global flags (`--root`, `--wait`,
> `--no-wait`, `--wait-timeout`). The gate reading it reported the real command
> `pkm vacuum` as a fabrication. A derived-ness claim is only worth what the
> derivation is worth, and there was no derivation. There is one now, it ships,
> and a test drives it — so the claim above is checkable rather than asserted.

> **Finding surfaced while generating this, since resolved:** the registry
> discovered **9** tools (it includes `take_screenshot`) while the howto test
> carried a hard-coded 8-entry allowlist that predated it — a second,
> hand-maintained copy of the same set, free to drift. The durable fix was for
> the claim gate and that test to **derive** the valid-tool set from this
> surface instead. That is now the shipped behaviour: the hand-maintained
> allowlist is gone from the tree and the test suite validates against
> `capability-surface.json` directly.

## 2. `intergen/data/readonly-state-map.json`

The read-only state-question inventory: each question class (disk-space, memory,
cpu-info, gpu, kernel, os-version, hostname, uptime, block-devices, ip-address,
printers, processes, time-date, battery, gpu-utilization, gpu-memory) mapped to
the **one safe read-only command** that answers it, the tool it routes through
(`run_command`), the `requires[]` on-PATH binary that gates it, and the in-tree
package that ships it.

Every command is read-only (no state change), so the M4 path can execute it
without a confirmation prompt. The map is the class→command authority the
read-only direct-execution path grounds against.

## 3. `intergen/data/howto/*.json` — corpus round 2

Six new FACE-gap how-to entries filling the measured-demand read-only classes
the corpus did not yet cover, each grounded in a tree-verified shipped command:

| id | command | ships via |
|---|---|---|
| `hw-what-cpu-do-i-have` | `lscpu` | util-linux-core |
| `hw-what-graphics-card` | `lspci` | pciutils |
| `system-what-os-version` | `cat /etc/os-release` | base-files |
| `system-what-disks-are-there` | `lsblk` | util-linux-core |
| `system-what-is-my-hostname` | `hostnamectl` | systemd |
| `system-what-time-is-it` | `date` | coreutils |

`lsusb` (USB devices) was **not** added: `usbutils` is not in the tree, so no
shipped command backs it — teaching it would be a claim with no substance,
exactly what M4 exists to prevent. It re-enters the corpus if/when usbutils
ships.
