# InterGenOS — Developer Certificate of Origin

InterGenOS uses the **Developer Certificate of Origin (DCO) version
1.1**, the same model the Linux kernel and many other major free-
software projects use. The DCO is a lightweight contributor
agreement that asks each contributor to assert, on each commit, that
they have the right to submit it under the project's license.

The DCO **replaces** the implicit "by contributing, you agree…"
language that prior versions of [`CONTRIBUTING.md`](CONTRIBUTING.md)
relied on, and **does not require** signing a separate Contributor
License Agreement.

---

## 1. What you do

For every commit you submit to InterGenOS, **append a
`Signed-off-by:` trailer** to the commit message, using the name and
email address you submit under:

```
Signed-off-by: Random J Developer <random@developer.example.org>
```

The standard `git` command for this is:

```sh
git commit -s
```

or:

```sh
git commit --signoff
```

Both add the trailer automatically using your `user.name` and
`user.email` git configuration values. You can append additional
trailers (`Co-Authored-By:`, `Reviewed-By:`, `Reported-by:`) as
usual; the `Signed-off-by:` trailer must be present on every commit.

For commits authored by multiple humans, each human contributor
appends their own `Signed-off-by:` trailer. The order of trailers
does not matter; their presence does.

---

## 2. What the trailer means (the DCO)

By including a `Signed-off-by:` trailer in a commit, you are
certifying the following — this is the verbatim text of the
**Developer Certificate of Origin 1.1**, from
[developercertificate.org](https://developercertificate.org):

> Developer Certificate of Origin
> Version 1.1
>
> Copyright (C) 2004, 2006 The Linux Foundation and its contributors.
>
> Everyone is permitted to copy and distribute verbatim copies of
> this license document, but changing it is not allowed.
>
>
> Developer's Certificate of Origin 1.1
>
> By making a contribution to this project, I certify that:
>
> (a) The contribution was created in whole or in part by me and I
>     have the right to submit it under the open source license
>     indicated in the file; or
>
> (b) The contribution is based upon previous work that, to the best
>     of my knowledge, is covered under an appropriate open source
>     license and I have the right under that license to submit that
>     work with modifications, whether created in whole or in part
>     by me, under the same open source license (unless I am
>     permitted to submit under a different license), as indicated
>     in the file; or
>
> (c) The contribution was provided directly to me by some other
>     person who certified (a), (b) or (c) and I have not modified
>     it.
>
> (d) I understand and agree that this project and the contribution
>     are public and that a record of the contribution (including all
>     personal information I submit with it, including my sign-off)
>     is maintained indefinitely and may be redistributed consistent
>     with this project and the open source license(s) involved.

The DCO is not legal advice; it is a structured promise that the
contribution is yours to submit. The exact text above is the
governing version.

---

## 3. Why DCO instead of a CLA

A **Contributor License Agreement (CLA)** is a more elaborate
document that often transfers or grants broad rights from the
contributor to the project. The Free Software Foundation,
Software Freedom Conservancy, and many practitioners consider CLAs
overkill for projects whose license already permits everything the
project needs.

The DCO is enough for InterGenOS because:

- The project's **inbound license matches its outbound license**
  (GPL-3.0-or-later in, GPL-3.0-or-later out). We do not need
  contributors to grant us permissions that the GPL already
  provides to anyone who receives the code.

- The project commits to **not relicensing**. We will not ask
  contributors to grant relicensing permission "just in case." If
  the project ever needs a relicensing question answered for a
  specific historical commit, we will obtain the contributor's
  consent for that specific question at that time.

- The DCO leaves the chain of custody on each commit in **the
  commit history itself**. The trailer is part of the
  cryptographically-attestable record (when commits are signed
  with GPG or SSH keys), which composes naturally with the
  project's release-signing topology.

---

## 4. Identity expected on the sign-off

The DCO requires the contributor to use a real name and a working
email address. The intent is to be able to identify the contributor
in case a question about the chain of custody arises (it almost
never does). The standards we apply:

- The `Signed-off-by:` name should be the name you go by — a real
  identity. Pseudonyms are accepted in the InterGenOS project
  **only if** the pseudonym is consistent across the contributor's
  body of work in the project and the contributor's identity can
  be re-established via a working email under the same pseudonym.

- The email address should be **reachable**. We will not write to
  contributors except for direct project business and we do not
  bulk-email or harvest the address list, but a bouncing address
  defeats the purpose of the sign-off.

- AI-assisted commits (such as commits where InterGenOS
  coordinators use an LLM as a coding tool) still require a
  **human** `Signed-off-by:` trailer from the operator-of-record;
  the LLM's `Co-Authored-By:` trailer is supplementary, not a
  substitute. The human sign-off is what carries the DCO assertion
  (a) — "I have the right to submit it."

---

## 5. Enforcement

The `.github/workflows/public-content-audit.yml` workflow is
extended to validate that **every commit in a pull request bears a
valid `Signed-off-by:` trailer.** Pull requests whose commits do
not all carry the trailer will fail this check, and merge will be
blocked until the trailer is added.

To add the trailer to an existing branch retroactively:

```sh
# Single commit
git commit --amend --signoff

# Last N commits
git rebase --signoff HEAD~N

# All commits on a branch since main
git rebase --signoff main
```

`--no-verify` and similar workflow-bypass mechanisms must not be
used to land commits without sign-off, per the project's standing
posture on hooks and gates (see [`CONTRIBUTING.md`](CONTRIBUTING.md)
§ Security Standards).

---

## 6. Existing history

Commits in the InterGenOS repository made before the adoption of
the DCO are **grandfathered**: they were made under the implicit
"by contributing, you agree…" model of the earlier
[`CONTRIBUTING.md`](CONTRIBUTING.md). The DCO applies to all
commits authored from **2026-05-18** forward (the date this
document was published).

Existing contributors who wish to retroactively sign off on prior
contributions are welcome to file a single sign-off statement —
"I, [name <email>], retroactively sign off on commits [sha-range]
under the DCO" — by email to `legal@intergenos.org`. This is
optional; the project does not require retroactive sign-off and
does not consider missing retroactive sign-off a defect.

---

## 7. Cross-references

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — the contribution workflow,
  updated to reference this document as the inbound-licensing
  mechanism.
- [`LICENSE`](LICENSE) — the GPL-3.0-or-later license under which
  all contributions are licensed.
- [`developercertificate.org`](https://developercertificate.org) —
  the canonical DCO 1.1 source.

---

## 8. Provenance

This document was authored 2026-05-18 as part of the InterGenOS
v1.0 legal-readiness sprint, closing audit finding **P-018** (High:
no DCO sign-off requirement — CONTRIBUTING.md used the implicit
"by submitting, you agree" CLA pattern with material defects) from
the 2026-05-18 comprehensive state audit.

**License of this document.** The DCO text in §2 above is the
verbatim DCO 1.1 from `developercertificate.org`, redistributable
under its own terms (verbatim copies permitted; changes not
permitted). The surrounding text in this file is licensed
**CC0-1.0** (public domain dedication).

— InterGenJLU
