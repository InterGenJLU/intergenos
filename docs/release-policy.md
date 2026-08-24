# InterGenOS Release Policy

**Status:** Active. Decided 2026-08-18. First applies to the R001 release line.

## Release types

InterGenOS publishes two kinds of releases:

- **Major releases — `R001`, `R002`, …** A major release is produced by a complete
  from-source bootstrap: every package in the distribution is rebuilt from source,
  from an empty build root, with the full validation gate set enforced. A major
  release proves that the source tree alone reproduces the entire system.
- **Point releases — `R001.1`, `R001.2`, …** A point release delivers accumulated
  fixes and minor package additions. Changed packages are rebuilt against the
  proven build substrate of the current major release, a new installation image is
  produced, and the result is installed and evaluated on real hardware before
  anything is published. Major package additions are not delivered through point
  releases; they open a new development arc that concludes in a major release.

No release of either kind is published on a schedule. A release is published when
its content has passed evaluation, and not before.

## When a major release is required

A full from-source rebuild is triggered by any of the following:

1. **Foundation changes** — a new compiler, C library, or kernel major version, or
   any component the package corpus at large links against. An incremental rebuild
   cannot prove the packages built on top of the old foundation.
2. **Global build-policy changes** — distribution-wide compiler flags, hardening
   options, locale baseline, or build-phase ordering.
3. **Major package additions** — significant new capability opens its own
   development arc and lands through a full rebuild.
4. **Build-substrate doubt** — anything that calls the integrity of the proven
   build substrate into question forces a rebuild from a clean base.
5. **Drift limit** — when more than 15% of the package corpus has been rebuilt
   since the last full bootstrap, or at the third consecutive point release,
   whichever comes first, the next release is a major release. This bounds how long
   any change can exist without having proven itself in a complete from-source
   build.

## Support model: one supported line

InterGenOS is a **curated rolling** distribution with a single supported line:

- **The latest release is the supported release.** Installed systems move forward
  with `pkm upgrade` from the package mirror; installation images are refreshed at
  each point release.
- **No maintenance branches, no backports.** Fixes are not reworked onto older
  releases. Security fixes reach users by advancing the supported line, so every
  user runs the exact package set the project evaluates.
- **Rolling in cadence, checkpointed in quality.** Nothing reaches the mirror or
  the installation image without a rebuild, an installation, and an evaluation on
  real hardware. This is the tested-rolling-snapshot model rather than a
  continuous, per-package rolling model.
- **The evaluation is a gate, not a step.** A release candidate is not imaged and
  not published until the installed-system test tier has been run against that
  exact candidate on an installed machine, with nothing failing and no check
  skipped that was not declared in advance; the record of that run is checked by
  the image builder and the mirror publisher, and a missing record refuses the
  release rather than passing it.

A long-term-support line (a designated major release receiving security-only point
releases) is structurally compatible with this policy and may be introduced later
if user demand justifies the maintenance cost. None is offered today.

## Integrity

Every release is published with the artifacts needed to verify it: signed package
index, signed release tag, image checksum, and the release public key. See
`SOURCES.md` and the repository README for verification instructions.
