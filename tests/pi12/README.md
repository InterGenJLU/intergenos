# PI-12 — .PKGINFO build-time gate tests

Regression gates for the PI-12 fix (recipe-less LFS-Ch8 core packages shipping no `.PKGINFO`,
producing 1000+ by-design `*.PKGINFO not found` tar failures that camouflage real ones).

## Files

| File | Tests | Needs |
|---|---|---|
| `test_pi12_pkginfo.py` | T1, T2, T3 (gen-pkginfo) · T2b (`--force-tier` dual-built emission) · T6 (inject loud detector) · T7 (drift guard) · T9, T9b (backfill: missing-only / idempotent / dual-built / fail-loud) | pytest; T6/T7/T9 need `tar`+`gzip` (skip otherwise) |
| `test_pi12_gates.sh`   | T4, T4b (conditioned-2A) · T5, T5b (Step 4.7 sweep + empty-set) | bash + `tar` |

## Run

    pytest tests/pi12/test_pi12_pkginfo.py
    bash   tests/pi12/test_pi12_gates.sh

## What the gates assert

- **conditioned-2A** (T4/T4b): `pkg_archive` asserts a well-formed `.PKGINFO` only when
  `gen-pkginfo` actually ran (python3 present). Recipe-less core packages archived before python
  is built in the Ch8 chroot legitimately have no `.PKGINFO` yet — 2A is skipped there, so it
  cannot false-abort at the first such package.
- **Step 4.7 sweep** (T5/T5b): before sealing the squashfs every staged archive must carry
  `pkgname`/`pkgver`/`pkgrel`; an empty install set is itself refuse-to-seal (no vacuous PASS).
  The predicate + sweep are sourced from `scripts/lib/pi12-sweep.sh` — the single source of
  truth shared with `build-squashfs.sh`, so the test exercises the real function.
- **dual-built `--force-tier`** (T2b): glibc/m4/ncurses ship a toolchain-tier recipe but their
  staged archive is the final core build; `--force-tier core` corrects the emitted tier.
- **inject loud detector** (T6) + **drift guard** (T7): a non-empty inject is a reported gate
  escape; the canonical recipe-less core set must keep classifying as minimal-core.
- **backfill** (T9/T9b): the in-chroot post-python backfill (`scripts/backfill-pkginfo.py`,
  run at end of Ch8) stamps only the missing archives, losslessly, and fails loud on an
  unparseable name.

`T8` (full-build acceptance) is not a unit test — it is a real from-scratch ISO build clearing
2A/4.7/backfill over the early-Ch8 recipe-less set.
