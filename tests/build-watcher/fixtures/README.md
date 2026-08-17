# build-watcher fixtures

RED/GREEN fixtures for `scripts/build-watcher.sh` (work-plan 1.15, spec §5.1).

## Real distilled slices (from the two banked glibc logs)

The full logs are preserved on jarvis-storage
(`intergenos_build_trace/fixtures/`, with `SHA256SUMS`); the one-time full-log
replay is the separate burn-time acceptance leg. These committed files are small
distilled slices that carry the discriminating shapes:

- **`recursion_glibc_launch4.log`** — distilled from the launch-4 known-bad log
  (`glibc-20260703-004204.log`, full SHA256 `a2085e7c…`, 941 MB). Windows around
  5 consecutive configure re-runs → **configure_runs = 5 (> 2)** → must ALARM.
- **`healthy_glibc_dualwidth.log`** — distilled from the launch-5 known-healthy log
  (`glibc-20260703-062107.log`, full SHA256 `dd90eb0c…`, 33 MB). Both legitimate
  dual-width configure runs (= 2) + a make-syscalls burst → must stay QUIET.

The make-syscalls discriminator (> 120) is exercised at real scale (2909 vs 53)
by the full-log replay leg; the committed slices trip/clear it via the synthetic
boundary fixture below (the real bad slice trips the configure discriminator, and
make-syscalls in a compact slice stay sparse — ~1 per 590 lines).

## Synthetic fixtures (thresholds, timestamps)

- **`make_syscalls_recursion.log`** — 2 legit configure runs + 121 make-syscalls
  lines → make_syscalls = 121 (> 120) → must ALARM (isolates that discriminator).
- **`budget_alarm_3x.log`** — a default-class package (30-min budget) with
  timestamps spanning 6300 s (3.5×) → BUDGET-ALARM.
- **`budget_halt_5x.log`** — same, spanning 9900 s (5.5×) → BUDGET-HALT.
- **`halt_clean_stopafter.log`** — orchestrator tail with a `Stopping after phase`
  line → UNIT-DEAD verdict = clean.
- **`halt_failure.log`** — a tier-driver `FAILED in build` line → UNIT-DEAD verdict
  = failure (and replays as HALT-LINE).
