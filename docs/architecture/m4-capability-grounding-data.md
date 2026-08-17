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

- **pkm** — every subcommand (with aliases, positionals, flags, and enum
  choices) walked out of the real `pkm/cli.py` argparse parser. This is the
  authority a claim like "run `pkm add`" is checked against: `add` is not in the
  surface, so the claim is a fabrication. (24 subcommands at capture; the true
  set is `install / remove(uninstall) / reinstall / list(ls) / update(sync,
  refresh) / upgrade / search(find) / info(show) / files(contents) / provides /
  verify / depends(deps) / history / import / refresh-baseline / check-updates /
  restart-services / hold / unhold / mark / autoremove / iso-prep / cache /
  install-helper` — no `add`.)
- **intergen tools** — every built-in tool discovered by
  `ToolRegistry.discover_tools()`, with its schema parameters, safety tier, and
  whether it escalates (the `_PRIVILEGED_TOOLS` set). This is the authority for
  "InterGen can do X".

**Regeneration:** re-run the introspection (interception of the pkm parser +
`ToolRegistry`); the method is recorded in the file's `_meta`. Because it is
derived, it cannot drift from the code the way a hand-kept list does — which is
the point.

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
