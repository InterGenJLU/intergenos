# InterGen Gating Model: the canonical permission specification

**Status: active (canonical).** Authored 2026-06-09. Owner: InterGenJLU.

This document defines how InterGen decides whether to perform, prompt for, or
refuse an action. The classifier and gate code must implement this model; where
the code and this document disagree, this document is authoritative and the code
is the bug.

---

## §0. The core principle

**Do not invent a gate. Inherit the one the OS already has.**

InterGen's permission decisions mirror the operating system's real privilege
model: a person at a terminal can freely touch their own things and needs
`sudo`/PolicyKit for system things. The user already understands that model, so
mirroring it keeps the machine understandable, in keeping with InterGenOS's goal
of giving people a system they understand, can modify, and can trust. Routing
privileged actions through the OS's *actual* enforcement (pkexec/PolicyKit)
means the gate and the kernel agree: defense in depth, not app-layer
security theater. Security is not first. It is only.

A parallel "autonomy tier" ladder that blocks benign reads is the anti-pattern
this model replaces: it made InterGen refuse questions like "what's my system
status?", which is the system working *against* its user.

---

## §1. Two orthogonal axes: never conflate them

Every action is evaluated on two independent axes, by two independent
mechanisms. Keeping them separate is essential.

| Axis | Question | Mechanism | Where defined |
|---|---|---|---|
| **Privilege** | "Would a human need `sudo` for this, and what does it touch?" | the gating model below | this document, §2–§6 |
| **Content trust** | "Does data crossing the boundary carry injection (inbound) or leak a secret (outbound)?" | the scanner and provenance layer (ingress scanning, egress scanning, InterGen Sentinel) | a separate mechanism, not this document |

**The trap to avoid:** letting "injection risk" creep back into the privilege
gate and re-block benign reads. The privilege axis says *a read is free*; the
content axis independently watches *what the read returns*. Two axes, two
mechanisms, no conflation.

---

## §2. Zones (the filesystem / resource overlay)

| Zone | What it is | Examples | A human needs sudo to **write** it? |
|---|---|---|---|
| **Z1 — User space** | Owned by the user | `~/` and below (`~/.config`, `~/.local`, `~/Documents`, `~/Downloads`), `systemctl --user` units, user-owned processes/files | No |
| **Z2 — System config / state** | Owned by root, ordinary administration | most of `/etc`, `/usr`, `/opt`, `/var`, system `systemd` units, installed packages | Yes |
| **Z3 — System-critical / trust anchor** | Root-owned **and** part of the security and boot trust chain, *plus InterGen's own substrate* (see §3) | `/boot`, EFI/UKIs, Secure Boot keys (MOK/db/KEK/PK), kernel and initramfs, dm-verity hashes, LUKS headers, partition table, `/etc/{shadow,gshadow,passwd,group,sudoers,sudoers.d,crypttab,fstab}`, `/etc/pam.d`, `/etc/polkit-1` | Yes, and these underpin the trust chain |

---

## §3. InterGen's own substrate is Z3 (the self-protection keystone)

**InterGen's own files and directories are system-critical (Z3), so InterGen
may never modify them.** This closes one of the most dangerous attack surfaces
there is: a capable adversary's whole game is to talk the assistant into
rewriting its *own* gate, model, manifest, or guardrails. If InterGen's
substrate is write-forbidden to InterGen, then even a flawless prompt injection
cannot make InterGen weaken InterGen. Only the user, acting manually and outside
the assistant, can. The result is an AI that cannot be coerced into editing its
own restraints.

InterGen's self-substrate (write/modify/replace is FORBIDDEN to InterGen):

- **Code:** `/usr/lib/python3.14/site-packages/intergen/` (all)
- **Panel extension:** `/usr/share/gnome-shell/extensions/intergen@intergenos.org/`
- **Model pins and signature:** `/usr/share/intergen/models-manifest.json`(`.asc`)
- **Verified model store:** `/var/lib/intergen/models/` (root-owned, read-only by design)
- **Daemon units and PolicyKit policy:** `intergen.service`, `org.intergenos.intergen.*.policy`
- **Privileged runner:** `/usr/bin/intergen-model-setup-runner`
- **Signing key and provenance state** used to verify InterGen's own components

(Reading InterGen's own code is fine, see §5; the prohibition is on *changing*
it.)

---

## §4. Operations

| Class | Meaning |
|---|---|
| **R — Read / inspect** | status, list, `cat`, get info (no state change) |
| **W — Write / modify** | edit/create/delete a file, change a config |
| **X — Execute / state-change** | run a command, start/stop/restart a service, install/remove a package, reboot |

---

## §5. The matrix: Operation × Zone → outcome

| | **Z1 User space** | **Z2 System config/state** | **Z3 System-critical / InterGen-self** |
|---|---|---|---|
| **R** | **FREE** | **FREE** (status, `pkm list`, most `/etc`) | listing → **FREE**; secrets (shadow/keys) → **AUTH-PROMPT** |
| **W** | **FREE** | **AUTH-PROMPT** | **FORBIDDEN** |
| **X** | **FREE** | **AUTH-PROMPT** | **FORBIDDEN** |

### The three outcomes

1. **FREE** — InterGen does it immediately, no prompt. (All reads of non-secret
   data; everything in the user's own space.)
2. **AUTH-PROMPT** — InterGen triggers the OS's *real* authorization
   (pkexec/PolicyKit), phrased plainly: *"InterGen wants to restart Bluetooth.
   That needs admin rights. Authorize?"* Never an opaque governance card of
   internal checks. The user's explicit authorization (their password) is the
   gate; the OS enforces it.
3. **FORBIDDEN** — InterGen will not do it, and **says so transparently** (§6).

### Secret reads mirror the OS

A user can `sudo cat` their own shadow file, so InterGen reading a secret is
**AUTH-PROMPT**, not forbidden: InterGen does not pretend to be stricter than
the OS. (Whether the *content* of that read may then leave the machine is the
separate content-trust axis, §1.)

---

## §6. Transparent refusal

When InterGen refuses a Z3 action, it must be **open about it and hand control
back to the user**. It states plainly *what* it won't do and *why* (it protects
the boot and security trust chain, including its own integrity), and confirms
that **the user can do it themselves**. There is never a silent failure, never a
hidden refusal, and never internal jargon ("blocked by the safety layer",
tiers, privileges).

Example wording: *"I won't modify the system's boot and security files. That's
the trust chain that keeps this machine yours. It's your machine, so you can do
it yourself with `sudo` if you intend to; I just won't be the one to touch it."*

---

## §7. Mapping current tools onto the model

| Tool / action | Zone·Op | Outcome |
|---|---|---|
| `manage_services status / is-active / list-units` | Z2·R | **FREE** |
| `manage_services restart / stop / enable` (system unit) | Z2·X | **AUTH-PROMPT** |
| `manage_services *` on an InterGen unit | Z3·X | **FORBIDDEN** |
| `manage_packages list / search / info` | Z2·R | **FREE** |
| `manage_packages install / remove` | Z2·X | **AUTH-PROMPT** |
| `read_file ~/…`, `write_file ~/…` | Z1·R/W | **FREE** (read still subject to content-axis) |
| `read_file /etc/…` (non-secret) | Z2·R | **FREE** |
| `read_file /etc/shadow`, key material | Z3·R secret | **AUTH-PROMPT** |
| `write_file /etc/…` | Z2·W | **AUTH-PROMPT** |
| `write_file /boot, /etc/sudoers, /etc/shadow, InterGen substrate` | Z3·W | **FORBIDDEN** |
| `run_command` | classify by the command's real zone·op | same three outcomes |

`write_file`'s `_classify_path` is this model in miniature, and the Z3 half of
it has since landed. It now resolves a write path against four layers, in order:
an exact protected-file list, a set of danger-equivalent path prefixes, the
**operator-signed never-list manifest** (the primary and comprehensive Z3 set,
which canonicalizes both pattern and candidate so a symlink or `..` detour
cannot launder the comparison), and a defense-in-depth floor of canonicalized
prefixes covering InterGen's own configuration and state that holds even if the
signed manifest could not be loaded. Anything matching returns BLOCKED; every
other write is at least CONFIRM. The same function is called by both the
gate-side classifier and the root-side enforcement, so the two cannot diverge.

What remains from this section's original scope is the **Z1 "FREE for your own
files" tier** — a write under the user's own home still classifies as CONFIRM
rather than FREE — and the generalization of the zone classifier to the other
tools.

---

## §8. Implementation notes

- A single **zone classifier** (`zone(path) → Z1|Z2|Z3`) and **op classifier**
  (read-only via each tool's enumerated `AUTO` allowlist) feed one
  **outcome resolver** (`zone × op → FREE | AUTH_PROMPT | FORBIDDEN`). One place
  to audit; every tool routes through it.
- **Reads are FREE**, realized as follows: a read-only action stays `READ_ONLY`
  even on a `_PRIVILEGED_TOOLS` member (the `classify_safety == AUTO`
  short-circuit in `_classify_risk_tier`), and the gate auto-approves a
  non-ingress `READ_ONLY` call without a prompt.
- **FORBIDDEN** is checked *before* any gate prompt. A Z3 write or state-change
  (including InterGen's self-substrate) yields the §6 transparent refusal, never
  a prompt.
- **AUTH-PROMPT** routes through the existing pkexec/PolicyKit escalation, with
  plain-language consent wording (§5).
- **Retire** the autonomy-tier-blocks-reads behavior. If an "autonomy tier"
  axis survives, it may only modulate how many AUTH-PROMPT actions InterGen
  *offers* — it must never block a read.
- The content-trust axis (ingress scanning, egress scanning) is untouched by
  this work and continues to operate independently (§1).

---

## §9. Change log

| Date | Change |
|---|---|
| 2026-06-09 | Initial canonical spec, derived from the action-by-zone `sudo`-mirroring matrix. Establishes: inherit the OS privilege model, two-axis separation, Z1/Z2/Z3 zones, InterGen self-substrate in Z3 (no self-modification), the three outcomes (FREE/AUTH-PROMPT/FORBIDDEN), secret reads as AUTH-PROMPT, and the transparent-refusal rule. |
