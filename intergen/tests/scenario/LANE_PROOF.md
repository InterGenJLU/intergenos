# The harness run every piece of InterGen work owes

**One command, run from the root of the tree the change is on, before that work
is offered as finished.**

```
python3 -m intergen.tests.scenario.lane_proof \
    --out ./lane-proof-runs --run-id <short-name-for-this-work> \
    --posture <the tier this box actually serves> \
    --baseline <the results.json from the same command on the unchanged tree>
```

`--posture` is required and takes one of `2B-locked`, `9B-native`,
`35B-native`. A scenario turn can carry assertions written for different tiers
that contradict each other — the same sentence routing freeform on the locked
tier and through tools on the native one — so a run that does not say which tier
it drove grades some of its assertions against a machine that was never there. A
run on a locked 2B box with no posture named once counted 31 such failures as
product defects. There is no default, because a default would be a guess about
the machine.

It loads the graded corpus under `intergen/tests/scenario/corpus/`, drives every
scenario against the tree it is run from, and writes three files under
`./lane-proof-runs/<run-id>/`:

| file | what it is |
|---|---|
| `scenarios.jsonl` | one row per scenario, written as that scenario finishes |
| `results.json` | the harness's own results object, the same one every run produces |
| `summary.txt` | the human-readable verdict counts, per-axis rates, and every non-PASS scenario named |

Attach `results.json` and `summary.txt` to the work. `scenarios.jsonl` is what a
run that was stopped part way still has to show for itself.

## What makes the command fail, and what does not

* **Exit 3 — a scenario could not be driven.** The daemon refused, the transport
  broke, the scenario raised. Nothing was measured about those scenarios, so
  nothing may be claimed about them. This is an instrument failure and it is
  never a result. On 2026-08-26 a direct-mode run returned 64 of these in 0.0
  seconds each, from one broken call in the harness's own conversation reset,
  and the output read as 64 product failures until someone opened it.
* **Exit 2 — something that used to pass does not.** Only when `--baseline`
  names a prior `results.json`. A scenario absent from the baseline is never
  counted as a regression, so adding coverage cannot fail the run that adds it.
* **Exit 4 — nothing was selected, or the wrong tree answered.** An empty
  selection reports nothing and must not exit 0. The command also prints the
  directory `intergen` actually resolved to and refuses one outside the current
  tree unless `--allow-installed` says that was the intent: running a harness
  script by absolute path once put that script's directory first on `sys.path`
  and silently measured the INSTALLED package while reporting on the tree.
* **A grade that is not PASS is data, not a failure of the command.** The corpus
  holds scenarios the assistant does not satisfy yet — that is what it is for.
  Exiting non-zero on them would push people to trim the corpus down to whatever
  is currently green.

## Getting the baseline

Run the same command on the unchanged tree first — a second worktree checked out
at the commit the work is based on:

```
git worktree add ../proof-base <base-commit>
cd ../proof-base && python3 -m intergen.tests.scenario.lane_proof \
    --out ./lane-proof-runs --run-id base --posture <the same tier>
```

Use the SAME `--posture` for the base run and the change run: two runs graded
under different tiers are not comparable, and the difference between them would
be read as a regression.

Then point `--baseline ../proof-base/lane-proof-runs/base/results.json` at it.

## Narrowing the run

Only while iterating, never for the proof that is attached:

```
--batch field_shapes     # only scenarios tagged batch:field_shapes
--tag shape:S1           # only scenarios carrying that exact tag
--limit 20               # stop after 20 scenarios
```

Every filter must match — they narrow together, they do not add up.

## What it costs

Measured 2026-08-26 on an 8-core CPU-only machine serving the 2B floor, corpus at
674 scenarios / 861 turns: the first scenario takes about 40 seconds while the
model's prompt cache warms, and the rest settle to roughly 5 to 10 seconds each.
Budget about an hour and a half for the whole corpus and about ten minutes for a
single 64-scenario class. Run it under a service manager, not in a terminal you
intend to close:

```
systemd-run --user --unit=lane-proof --collect --working-directory="$PWD" \
    python3 -m intergen.tests.scenario.lane_proof --out ./lane-proof-runs \
        --run-id <name> --posture <the tier this box actually serves>
```

## Which daemon answers

`--mode direct` (the default) starts an in-process daemon from the tree under
test. It needs the D-Bus name `com.intergenos.InterGen` to be free: the daemon
claims that name before it binds anything, so an already-running daemon makes the
in-process one exit immediately and every scenario then fails its readiness gate.
Stop the running one first:

```
systemctl --user mask intergen.service && systemctl --user stop intergen.service
# ... run the proof ...
systemctl --user unmask intergen.service && systemctl --user start intergen.service
```

`--mode dbus` drives the daemon already on the session bus instead. That daemon
serves whichever code is INSTALLED, so it answers a question about the installed
package and not about the working tree.

A tree-run daemon also logs `GOVERNANCE HASH MISMATCH` at startup. The baseline
it compares against, `/usr/share/intergen/governance.sha256`, is written when the
package is built and records the hash of the INSTALLED `governance.py`; a working
tree whose copy has moved on cannot match it. The mismatch suspends governance's
own autonomous-action gate, which sits in front of the browser panel's tool
dispatch and not in front of the router the harness drives.
