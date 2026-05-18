# InterGenOS — Privacy Notice

**Effective date:** 2026-05-18 (initial publication)

InterGenOS is designed to be a **local-first operating system**. The
default posture is that nothing leaves your machine without you knowing
about it. This document tells you what that means in concrete terms,
what the named exceptions are, what your rights are under applicable
laws, and how to exercise them.

If you only read one section, read [§3 — In one paragraph](#3--in-one-paragraph).

---

## 1. Why this notice exists

The Prime Directive of InterGenOS states:

> *InterGenOS exists to put the user in control of their own machine.
> Every design decision, every default, every included component must
> serve this purpose: giving people a system they understand, can
> modify, and can trust.*

A user who cannot tell what data the system is processing — and where
that data goes — does not control the machine. So this notice exists
for two reasons:

1. **Transparency.** You are entitled to know what the system does
   with information about and from you, regardless of any legal
   obligation to disclose it.

2. **Compliance.** The General Data Protection Regulation (EU
   2016/679, "GDPR"), the California Consumer Privacy Act (Cal. Civ.
   Code §1798.100 *et seq.*, "CCPA"), the UK Data Protection Act
   2018, and equivalent statutes in other jurisdictions require a
   privacy notice from anyone who processes personal data of
   residents of those jurisdictions — even if the processing is
   entirely local on the user's own machine and even if no data is
   sold or shared.

This notice covers both purposes. Where a section is a legal
disclosure required by a specific statute, that statute is cited
inline.

---

## 2. Who is the controller

The **data controller** for purposes of GDPR Art. 4(7), the
**business** for purposes of CCPA §1798.140(d), and the equivalent
identified party in other jurisdictions is:

```
InterGenJLU
trading as InterGen Studios
contact: privacy@intergenos.org
```

In practice, the controller's processing of your data is limited to
the narrow channels described in §5 below — primarily the operation
of the project's binary distribution mirror. Most of what you do on
InterGenOS happens entirely on your own machine and never reaches us.

---

## 3. In one paragraph

InterGenOS processes information **locally on your machine**. Local
processing includes your interactions with the desktop, your shell
history, your application data, system logs, the **InterGen**
assistant's conversation history, and the **pkm** package manager's
install records. **None of this is sent to the project, to a cloud
service, or to any third party** as part of normal operation. The
named exceptions are: (a) **package downloads** from the InterGenOS
mirror at `repo.intergenos.org`, which reveal your IP and User-Agent
to our mirror like any HTTPS request; (b) **LLM model downloads** from
the model author's distribution (HuggingFace by default), which reveal
your IP and User-Agent to that third party; (c) **explicit user-
invoked network actions** (the InterGen assistant's `web_search`,
`weather`, etc.; your web browser; any package you install that
makes network calls), which behave according to those tools' own
network behavior. There is **no telemetry**, no analytics ping, no
phone-home, no usage reporting, no crash reporting transmitted off
your machine, and no shadow background traffic. We do not sell, share,
or transfer any data we do not have.

The rest of this document is the detail behind that paragraph.

---

## 4. What we do not collect or send

To establish the baseline before discussing the narrow exceptions:

- **No telemetry.** InterGenOS contains no telemetry component.
  There is no daemon, service, timer, or scheduled job whose
  purpose is to report usage, configuration, errors, or any other
  data to InterGenJLU, InterGen Studios, or any third party. We
  did not write one and we have not bundled one from an upstream.

- **No analytics.** No application installed by default sends
  usage statistics, page-view counts, click-tracking, or
  fingerprinting data to us or to anyone else as part of normal
  operation. (Applications you install later may include such
  facilities; that is between you and those applications.)

- **No accounts.** InterGenOS does not require, and does not
  offer, an "InterGenOS account". You do not sign in to use the
  system, and there is no account you could create.

- **No phone-home version check.** The system does not check for
  updates by contacting an InterGenOS server in the background.
  Updates happen when you run `pkm sync`/`pkm upgrade` (or are
  scheduled by you), at which point the package-mirror exception
  in §5.1 applies.

- **No crash reporting.** Crashes and errors are written to the
  systemd journal on your machine. They are not transmitted off
  your machine by any default component.

- **No advertising IDs.** There is no advertising identifier on
  InterGenOS and no mechanism to track you across sessions or
  installations for advertising purposes.

- **No "improved by your data" model training.** Nothing you do on
  InterGenOS — including your conversations with InterGen — is
  used to train, fine-tune, or otherwise improve any model. We do
  not have your conversations.

- **No data sales.** InterGenJLU does not sell, share, lease, rent,
  exchange, or otherwise transfer personal data, because we do not
  possess personal data outside the narrow channels in §5.

This baseline applies to the system as InterGenOS ships it. Third-
party software you install later operates under its own privacy
terms, which we do not control.

---

## 5. Named exceptions: when data crosses the network

These are the network-crossing points in the system as we ship it.
Each is a deliberate, documented data flow with a stated purpose.

### 5.1 Package downloads to / from the InterGenOS mirror

**What.** When you run `pkm sync` (or the system runs it as part of
an `pkm install` / `pkm upgrade`), pkm makes HTTPS requests to:

```
https://repo.intergenos.org/x86_64/current/InterGenOS.db
https://repo.intergenos.org/x86_64/current/InterGenOS.db.sig
https://repo.intergenos.org/x86_64/current/<package>.igos.tar.gz
```

**Who sees what.** Like any HTTPS request, our mirror server's access
log records — by default — your **IP address**, the **User-Agent**
string pkm sends, the **time** of the request, and the **path**
requested. The User-Agent identifies pkm and its version; it does
not include your hostname, username, or any other system identifier.
TLS prevents the request body and response body from being readable
by anyone in transit between you and the mirror.

**Where it goes.** The mirror runs on a virtual private server
operated under InterGenJLU's name; the operator is **KnownHost
Inc.**, the cPanel container is on `origin.intergenstudios.com`,
and the underlying VPS is physically located in the United States.
Access logs are retained per the mirror operator's default web-server
configuration (rotated and discarded after a short period).

**What we do with it.** Nothing beyond what is needed to operate
the mirror. We do not analyze access logs to build user profiles.
We do not export logs to third parties for analytics. We do not
attempt to correlate package downloads to identify users. We do
not have a per-user "InterGenOS use history" because we do not
construct one.

**Your control.** You can:
- Run `pkm sync` only when you want to (the default does not
  schedule it; you trigger it).
- Use the system without ever running `pkm sync` — InterGenOS
  works fully offline once installed.
- Route pkm through a proxy or Tor (pkm respects standard `HTTPS_PROXY`
  / `http_proxy` environment variables).
- Run your own mirror (the mirror layout is documented in
  [`docs/mirror/design.md`](docs/mirror/design.md)) and point pkm
  at it via `/etc/pkm/repos.conf`.

### 5.2 LLM model downloads (InterGen)

**What.** The InterGen AI assistant is a local-first component
that runs language-model inference on your machine. The models
themselves are not bundled with InterGenOS (model weights are
large and have their own licenses); the InterGen component
downloads them from the model author's distribution channel at
first-run, **only if you launch InterGen**.

**Where from.** By default, InterGen fetches Qwen3.5 GGUF model
quantizations from **HuggingFace** at
`https://huggingface.co/unsloth/Qwen3.5-*-GGUF/...`, and the
nomic-embed-text-v1.5 embedding model from the same provider.
HuggingFace is a third-party service operated by **Hugging Face,
Inc.** (United States). When InterGen requests a model file, your
IP address and User-Agent reach HuggingFace's servers; their
privacy policy applies to what they do with that data.

**Who decides this happens.** You do. InterGen does not auto-launch
or auto-download at install or at boot in the default configuration.
The first time you launch InterGen, you will see an acceptance
prompt that discloses (a) the model that will be downloaded, (b)
the source it will be downloaded from, (c) the license the model
weights are distributed under (e.g., the Tongyi Qianwen License for
Qwen models — see [`docs/governance/license-policy.md`](docs/governance/license-policy.md)),
and (d) the fact that this is a network-crossing point. You can
decline; if you decline, InterGen does not start.

**What we do with it.** Nothing — we are not in the path. The
download is between your machine and HuggingFace; InterGenJLU
does not see the request, does not log it, and does not host
the model. You can verify this by inspecting the network calls
or reading the [InterGen source](packages/ai/intergen/).

**Your control.**
- Decline at the acceptance prompt (InterGen does not start).
- Decline and uninstall InterGen entirely (`pkm remove intergen`).
- Use a different model source by pointing the `MODEL_CATALOG_URL`
  configuration at a mirror you control.
- Pre-stage models offline (sneakernet them onto your machine)
  and tell InterGen to use the pre-staged files instead of
  fetching.

### 5.3 Explicit user-invoked network actions

**What.** When you ask InterGen to perform an action whose
implementation requires the network — for example,
`web_search`, `weather`, `fetch`, browsing the web in your
browser, sending an email, or any package you install that
itself makes network calls — that action makes network requests
to the relevant third party. **Your action is the trigger.**

**Disclosure.** Each network-using InterGen tool (per
[`docs/architecture/intergen-provenance-gate-design.md`](docs/architecture/intergen-provenance-gate-design.md))
is gated by the provenance-and-pkexec dispatcher. Ingress-derived
network actions surface a user-review modal before execution. Your
machine talks to those third parties when you ask it to, not
otherwise.

**What we do with it.** Nothing — InterGenJLU is not in the path
for these requests either. Your network requests to third parties
do not transit through any InterGenJLU service.

### 5.4 First-boot account creation in the GUI installer (Forge)

**What.** When you install InterGenOS from the live ISO using
Forge (the graphical installer), you are prompted for a username
and password. These are stored **on your installed system's local
disk only**. They are never transmitted off the machine.

**No exception.** This is included for clarity only — it is not
actually a network-crossing exception. Install-time account
creation happens entirely on your hardware.

---

## 6. Local data on your installed system

InterGenOS as a normal operating system writes a variety of data
to local disk. This data **remains on your machine** unless you
explicitly transfer it. It is your data, controlled by you, on
your storage.

For transparency, the locations are:

| What | Where | Default rotation |
|---|---|---|
| System and service logs | `/var/log/journal/` (systemd journal) | Bounded by `SystemMaxUse` in `journald.conf` |
| Shell history | `~/.bash_history` (per user) | Bounded by `HISTSIZE` / `HISTFILESIZE` |
| Application data | `~/.config/`, `~/.local/share/`, `~/.cache/` (per user) | Per application |
| InterGen conversation history | `$XDG_DATA_HOME/intergen/conversations/` (per user) | Bounded by user-controlled retention setting (default: indefinite until you delete) |
| InterGen tool-call audit log | `$XDG_STATE_HOME/intergen/tool-dispatch.jsonl` (per user) | Bounded by user-controlled retention; 30-day default per the InterGen dispatcher design |
| Package install records | `/var/lib/pkm/installed/` and `/var/lib/pkm/database.json` | Retained as long as the package is installed |
| Package cache | `/var/cache/pkm/` | Cleared by `pkm cache clean`; bounded by user-set cache-size limit |
| User documents and files | wherever you put them | Per your filesystem usage |

You can read, modify, delete, or back up any of these at any time
using ordinary filesystem tools. You are the owner, and the only
party with access to them, on a machine you control. **No background
sync** uploads any of these to anything.

The InterGen assistant's `tool-log` CLI (per the dispatcher design)
provides a convenient interface for reviewing and wiping the
tool-call audit log if you prefer that to direct file operations.

---

## 7. What we know about you

Under GDPR Art. 13(1)(a), Art. 14, and CCPA §1798.130(a)(5), we
must tell you what personal data we have. Here is the complete
list of personal data that InterGenJLU as a controller may hold
about you:

- **If you have made a `pkm sync`/`pkm install` request against
  our mirror within the access-log retention window:** an entry
  in our mirror server's access log containing your IP address,
  the time, and the path requested. We do not link this to a
  named individual unless you have separately identified yourself
  to us.

- **If you have contacted us at `privacy@intergenos.org`,
  `legal@intergenos.org`, `trademark@intergenos.org`, or any other
  project email address:** the email itself and our reply, retained
  for the period needed to handle the inquiry and any follow-up
  obligations.

- **If you have filed an issue, opened a pull request, or commented
  on GitHub at `github.com/InterGenJLU/intergenos`:** the
  information you provided to GitHub for that purpose, visible to
  us through GitHub. GitHub's privacy policy governs that data;
  we do not receive a copy independent of GitHub.

- **If you have requested source code under our `SOURCES.md` §6b
  written offer:** the contact information you provided in your
  request and our shipping records for the fulfillment.

- **If you have made a Data Subject Request (see §10 below):** the
  information you provided to authenticate the request and our
  records of the disposition.

That is the exhaustive list. We do not hold any personal data
acquired by other means.

---

## 8. Legal bases for processing (GDPR Art. 6)

For data subjects in the European Economic Area, the United Kingdom,
or other jurisdictions applying GDPR-equivalent law, the legal
bases for our processing are:

- **Performance of a contract / pre-contractual steps** (Art. 6(1)(b))
  — for processing your source-code request, your trademark
  permission request, your legal correspondence.

- **Legitimate interests** (Art. 6(1)(f)) — for the minimal access
  logging needed to operate the binary mirror securely; our
  legitimate interest is in detecting abuse and ensuring service
  availability, and your fundamental rights are not overridden by
  this minimal logging.

- **Compliance with a legal obligation** (Art. 6(1)(c)) — where
  applicable to retention of correspondence required by law.

We do **not** rely on consent (Art. 6(1)(a)) for the mirror access
logging, because we are not collecting it for any purpose beyond
operating the service. We do not rely on the "necessary for a task
carried out in the public interest" basis (Art. 6(1)(e)).

---

## 9. International transfers

If you are located outside the United States and you fetch packages
from our mirror, your IP address and request metadata are
transmitted to a server located in the United States. We rely on:

- **Article 49(1)(b)** GDPR — the transfer is necessary for the
  performance of a request initiated by you (you requested the
  package).

- **Standard Contractual Clauses** are not applicable because we
  are not exporting personal data to a third-party processor;
  the United States is the location of the mirror operator we
  directly contract with.

If you want to avoid this international transfer, you can:
- Use a regional mirror operated by a third party (none currently
  exists at scale, but the layout is documented and anyone can
  operate one),
- Operate your own mirror,
- Use the system entirely offline after initial installation.

---

## 10. Your rights

Depending on your jurisdiction, you have some or all of the
following rights:

### Under GDPR (residents of the EEA and UK)

- **Right of access** (Art. 15) — ask us what personal data we
  hold about you. Response is the list in §7 above, populated for
  your specific case.
- **Right to rectification** (Art. 16) — ask us to correct
  inaccurate data.
- **Right to erasure** (Art. 17) — ask us to delete data we hold
  about you. We will honor this request promptly except where law
  requires us to retain (e.g., financial records of a source-media
  shipment).
- **Right to restriction of processing** (Art. 18).
- **Right to data portability** (Art. 20) — receive your data in
  a machine-readable format.
- **Right to object** (Art. 21) — including to processing based
  on legitimate interests.
- **Right not to be subject to automated decision-making** (Art.
  22) — n/a, we do not engage in automated decision-making about
  you.
- **Right to lodge a complaint with a supervisory authority**
  (Art. 77).

### Under CCPA / CPRA (California residents)

- **Right to know** what personal information we collect, use,
  disclose, and share.
- **Right to delete** personal information we have collected from
  you.
- **Right to correct** inaccurate personal information.
- **Right to opt out of sale or sharing** — moot, because we do
  not sell or share personal information.
- **Right to limit use of sensitive personal information** — moot,
  because we do not collect or use sensitive personal information.
- **Right to non-discrimination** for exercising any CCPA right.

### Under other applicable law

If you are in a jurisdiction with an equivalent statute (UK DPA
2018, Quebec Law 25, Brazil LGPD, etc.), the corresponding rights
under your statute are available to you to the extent applicable.

---

## 11. How to exercise your rights

Email `privacy@intergenos.org` with subject line:

```
Data Subject Request — <right being exercised>
```

Examples:
- `Data Subject Request — Access` (GDPR Art. 15)
- `Data Subject Request — Erasure` (GDPR Art. 17)
- `CCPA Right to Know`
- `CCPA Right to Delete`

Include:
1. The right(s) you are exercising
2. Enough information for us to identify what we may hold about
   you (an email address you have used to contact us, the
   approximate date of your interaction, etc.). If we cannot
   identify you in our records we may need additional information
   or may report back that we hold nothing matching.
3. Verification information appropriate to the request — we may
   confirm a small detail of prior correspondence to verify that
   the requester is the data subject.

We will respond:
- **EU/UK residents:** within one (1) month per GDPR Art. 12(3),
  extensible by two months for complex requests with notice.
- **California residents:** within forty-five (45) days per CCPA
  §1798.130(a)(2), extensible by forty-five days with notice.
- **Other jurisdictions:** within the time the applicable statute
  requires, or within 45 days if no statute applies, whichever is
  shorter.

There is no charge for routine requests. For repetitive or
manifestly excessive requests, we reserve the right to charge a
reasonable fee or refuse, per GDPR Art. 12(5) and equivalent
provisions.

---

## 12. Children

InterGenOS is not directed to children under 13 (United States)
or 16 (EEA, depending on member state). We do not knowingly collect
personal information from children. If you believe a child has
provided personal information to us — typically through a GitHub
issue, email, or source-media request — please notify
`privacy@intergenos.org` and we will delete the information.

---

## 13. Security

We protect personal data we hold to a standard appropriate to its
sensitivity. Concretely:

- The mirror VPS is access-restricted (SSH key auth, no password
  login).
- The release-signing keys (which are not personal data, but are
  critical infrastructure) are on offline hardware tokens, not
  on the VPS.
- Email correspondence is stored in standard professional email
  systems with provider-side encryption-in-transit and at-rest.
- We do not store payment information; physical-media fulfillment
  under SOURCES.md §6b is invoice-based.

In the event of a personal data breach affecting EU/UK residents,
we will notify the relevant supervisory authority within 72 hours
where the breach is likely to result in risk to your rights and
freedoms (GDPR Art. 33), and notify affected individuals where
required (Art. 34).

---

## 14. Changes to this notice

We may revise this notice. The version currently in effect is the
one at the top of this file (or in the master branch of the
canonical repository, whichever is more recent). Material changes
will be announced through the project's release-notes channel and
[`CHANGELOG.md`](CHANGELOG.md). Clarifications, typographical
corrections, and structural reorganization may be made without
announcement.

Prior versions of this notice are available in the repository's
git history. The version that applied to your data at the time we
received it is the version that governs disposition of that data.

---

## 15. Contact

For privacy questions, Data Subject Requests, breach reports, and
all matters arising under this notice:

```
privacy@intergenos.org
```

For routine project questions, see
[`CONTRIBUTING.md`](CONTRIBUTING.md). For security vulnerability
reports, see [`SECURITY.md`](SECURITY.md). For legal correspondence
on other matters, `legal@intergenos.org`.

If you are dissatisfied with our response, you may lodge a complaint
with your jurisdiction's data protection authority. In the EEA,
this is your member-state Data Protection Authority. In the UK, it
is the Information Commissioner's Office (ICO). In California, the
California Privacy Protection Agency (CPPA).

---

## 16. Cross-references

- [`LICENSE`](LICENSE) — GPL-3.0-or-later license for the code.
- [`SOURCES.md`](SOURCES.md) — source-availability commitment.
- [`TRADEMARK.md`](TRADEMARK.md) — trademark policy.
- [`EXPORT-NOTICE.md`](EXPORT-NOTICE.md) — export-control posture.
- [`SECURITY.md`](SECURITY.md) — vulnerability disclosure.
- [`docs/architecture/intergen-provenance-gate-design.md`](docs/architecture/intergen-provenance-gate-design.md)
  — InterGen tool-dispatcher design (covers per-tool consent and
  audit-log behavior).
- [`docs/mirror/design.md`](docs/mirror/design.md) — public binary
  mirror design and operational notes.

---

## 17. Provenance

This notice was authored 2026-05-18 as part of the InterGenOS v1.0
legal-readiness sprint, closing audit finding **P-020** (High: no
PRIVACY.md / no privacy-notice surface) from the 2026-05-18
comprehensive state audit.

**License of this document.** This `PRIVACY.md` is licensed
**CC0-1.0** (public domain dedication) so other projects may adapt
its text. Adapting it does not transfer any obligation from us to
you, and vice versa; each project must make its own commitments.

— InterGenJLU
