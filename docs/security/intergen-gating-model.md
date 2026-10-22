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

### What a consent surface shows

A consent surface asks a person to approve one act, so it describes the act
that runs and nothing else. Every surface that describes a held act renders ONE
description, derived from the argv the tool's own builder returns for that
call (`tool_registry.describe_call`): the web review card's sentence and command
line, the honest handoff and the refusal message, the desktop dialog's
headline, the zenity fallback's body, the console's prompt line, the router's
offer, and the model's capability guard. The command line is the shell-joined
argv, one per command, joined with `&&`.

- **The update is one act.** A package update runs `pkm sync` and then
  `pkm upgrade --all --yes` under the one approval (or `pkm upgrade --yes
  <package>` when a package is named). `--all` upgrades every installed package
  with a newer signed build and leaves held packages and the kernel
  (`linux-kernel`) as they are; the card says so. `upgrade` and `uninstall` are
  not actions of the package tool: an action outside its schema is refused
  before any card, with the reason.
- **Flags that run are shown:** `--yes`, `--user`, `--all`, and a write's
  creation of missing parent directories.
- **A change on the person's own service manager** (`systemctl --user`) runs in
  the person's own process, never through the privileged runner (where
  `--user` would reach root's manager), and its card says `--user`. It is
  reviewed like every held act of that tool: a card every time.
- **A call that will be refused is refused before any card,** with its reason:
  an install or removal that names no package, a command line with a shell
  construct, a write to a relative path, a `daemon-reload` that names a unit,
  an unknown unit. A card names the act as resolved: `sshd` for `ssh`, the
  absolute path of a write, the manager's configuration for `daemon-reload`.
- **No raw argument dump.** A tool with no command line (`read_file`,
  `analyze_file`, `web_search`, `take_screenshot`) is shown as a fixed sentence
  naming its argument; an external handler as its name and the exact payload it
  is sent.
- **The terminal handoff** is the same command line as a person types it:
  `sudo` on every command that runs as root and on none that runs on the
  person's own manager, and pkm's `--yes` dropped, because a person at a
  terminal answers pkm's own confirmation.
- **The OS prompt** is PolicyKit's own, and it cannot carry the act. Its message
  is the static text of the installed policy action
  (`org.intergenos.intergen.privileged-tool`: "Authentication is required to
  let InterGen perform the privileged system action you just approved"), and
  the only values pkexec could put in it are its own: the program and the
  command line it starts, which are the privileged runner and a request
  identifier, never the act inside the request. It authenticates the person;
  the card they have just answered is what describes the act.

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
- **The root side re-derives the route.** The privileged dispatcher
  (`privileged_dispatch.main`) asks `_classify_risk_tier` again, of the same
  tool and arguments, after the approval token verifies and before the tool's
  own argument validation, and runs only a call that the classifier's answer
  sends across the boundary. A read-only call, and a change on the
  person's own service manager (`manage_services` with `user_mode`, which run
  as root would reach root's manager, not theirs), are refused there with the
  reason, recorded, and with the approval unspent. `write_file` refuses a
  relative path in its own validation, which the dispatcher re-runs, so that
  refusal does not rest on the caller either: under the runner a relative path
  would resolve against root's own home.
- **The root side installs the bytes it verified.** A privileged helper that
  checks a file the caller supplies uses that file through the descriptor it
  checked, never through its path again: the model-storage provisioner
  (`model_setup_dispatch`) opens each staged file once, without following a link
  at its name, requires a regular file owned by the authenticated caller
  (`PKEXEC_UID`), hashes it from that descriptor against root's own pin, copies
  it from the same descriptor into the store's temporary file, and hashes that
  file again before it replaces the store's entry. A staged file that is a
  link, is not a regular file, belongs to another account, or changes after its
  hash is refused, and nothing is installed. The other root readers of a path
  an account controls follow the same rule: the greeter's monitor-layout sync
  (`igos-greeter-monitors-sync`) and the backup engine's restore of user data
  (`chronicle.engine._restore_one`) each hold the source before they open it
  (below), take its type, owner and size from that hold, and check and copy
  the bytes they read through a temporary file hashed in full before the
  rename. The greeter's copy, and the first-boot seed's, land at 0600; the
  seat config directory is made 0700 only when it is absent and otherwise
  keeps the mode the display manager gives it.
- **The root side holds a named object before it opens it.** Opening a FIFO
  or a device runs what it does when it is opened, so a privileged helper
  handed a path does not open it to learn what it is: `intergen.held_open`
  walks the directory from `/` by descriptor without following a link, holds
  the name with `O_PATH|O_NOFOLLOW`, which opens nothing, requires the held
  object to be a regular file (and the caller's own, where that is asked),
  and only then opens that same inode through `/proc/self/fd`, its device and
  inode compared with the hold's. The model-storage provisioner's two staging
  paths, the file tool's open of an existing name under the privileged runner
  (the write, and the read of the old content for the diff) and the
  privileged runner's request file open this way; a link, a FIFO or a device
  there is refused before anything opens it, and the refusal says nothing was
  opened. The backup engine carries the same contract as `chronicle.held_open`
  (the two packages share no library): its user-data capture, its config-state
  and restore-point captures of a caller's scope, its diff and its restore
  walk to each path by descriptor and act at that descriptor, a scope entry is
  captured only as a regular file, a directory or a link, and a restore writes
  a new name at the walked directory and renames it there. A store is adopted,
  and then used, only when no other account can change it or a directory
  above it. The greeter's monitor-layout sync holds the layout the same way.
- **One authentication is one call.** Each privileged tool has its own
  PolicyKit action, selected by pkexec from the runner's first argument (the
  tool's name, matched against the action's `exec.argv1`), so the prompt names
  the kind of action it is for. Every one of those actions is `auth_admin` with
  no keep: an administrator's password for each call, and the next call asks
  again. A kept authorization (`auth_admin_keep`) would be wrong here, because
  pkexec asks PolicyKit about its parent process and every runner's parent is
  the person's systemd user manager: one authentication would cover any runner
  any process of the account asked that manager for in the next five minutes.
  The PolicyKit prompt is the gate. The dispatch token and the request file stay
  behind it as defense in depth (the root side refuses a request whose tool is
  not the one authenticated for, and verifies the token before it acts); neither
  of them is the gate.
- **Retire** the autonomy-tier-blocks-reads behavior. If an "autonomy tier"
  axis survives, it may only modulate how many AUTH-PROMPT actions InterGen
  *offers* — it must never block a read.
- The content-trust axis (ingress scanning, egress scanning) is untouched by
  this work and continues to operate independently (§1).
- **A command runs with the environment written for it, and nothing else
  reaches it.** A command `run_command` starts runs as a transient service of
  the person's user manager, behind the credential masks; the service unsets
  glibc's unsafe loader variables, and `env -i` starts the command with PATH
  fixed to the system directories, the account's HOME, USER and LOGNAME, its
  runtime directory and session bus, `TERM=dumb`, LANG and `LC_*` as locale
  names, and three settings (npm's update check off, the local print
  scheduler's socket, no Python user site). The program is resolved on that
  PATH. A drop-in or dependency directory (.d, .wants, .requires, .upholds)
  that systemd would apply to a tool's service or to the privileged dispatch's
  unit, under any name it applies to it, refuses the start (the dispatch's
  before any password is asked for) and is named with the reason, unless it,
  every directory above it and everything within it are owned by root and
  writable by no one else: ownership decides, never permission bits an owner
  can change back. `git` asks first in every form, since it reads
  configuration that names programs it runs from the home and from the work
  tree it stands in.

---

## §9. Change log

| Date | Change |
|---|---|
| 2026-06-09 | Initial canonical spec, derived from the action-by-zone `sudo`-mirroring matrix. Establishes: inherit the OS privilege model, two-axis separation, Z1/Z2/Z3 zones, InterGen self-substrate in Z3 (no self-modification), the three outcomes (FREE/AUTH-PROMPT/FORBIDDEN), secret reads as AUTH-PROMPT, and the transparent-refusal rule. |
| 2026-10-22 | §5 "What a consent surface shows": every surface renders one description derived from the argv the tool's own builder returns; the update is the refresh then the upgrade under one approval; flags that run are shown; a user-manager act runs as the person; a call that will be refused is refused before any card; no raw argument dump; the terminal handoff carries sudo on every root command and drops pkm's --yes; the OS prompt's text is static. |
| 2026-10-22 | §8 "The root side re-derives the route": the privileged dispatcher runs only a call `_classify_risk_tier` sends across the boundary, refusing a read-only call and a change on the person's own service manager before the tool's own argument validation; `write_file` refuses a relative path in its own validation, which the dispatcher re-runs. |
| 2026-10-22 | §8 "The root side installs the bytes it verified": the model-storage provisioner hashes and copies each staged file from one descriptor, opened without following a link and owned by the authenticated caller, and re-hashes the store's temporary copy before it replaces the entry. |
| 2026-10-22 | §8 "The root side installs the bytes it verified" names the other root readers of a path an account controls: the greeter's monitor-layout sync and the backup engine's user-data restore read their source through one descriptor and hash their copy before the rename; the greeter's copy lands at 0600. |
| 2026-10-22 | §8 "The root side holds a named object before it opens it": `intergen.held_open` holds a path a privileged helper is handed with `O_PATH` and checks the held object before any open for reading or writing, so a FIFO's or a device's open never runs as root; adopted by the model-storage provisioner's staging paths, the file tool's open of an existing name under the privileged runner and the runner's request file. |
| 2026-10-22 | §8: AUTH-PROMPT is one administrator authentication per call. One PolicyKit action per privileged tool, `auth_admin` with no keep, chosen by pkexec from the tool's name so the prompt names the kind of action; the dispatch token and the request file are defense in depth, never the gate. |
| 2026-10-22 | §8: a command `run_command` starts runs with an environment written for it (glibc's unsafe loader variables unset, `env -i` with a fixed allowlist, the program resolved on a fixed PATH); a drop-in that would apply to its service where the account can write refuses the start; every `git` form asks first. |
| 2026-10-22 | §8: the drop-in gate reads every directory systemd applies to a tool's service and to the privileged dispatch's unit, dependency directories included, and takes one as the administrator's only when root owns it along its whole path. |
| 2026-10-22 | §8 "The root side holds a named object before it opens it": the backup engine's own `chronicle.held_open` carries the contract to its user-data capture, scoped captures, diff and restore; a store is adopted and used only when no other account can change it; the greeter's layout sync holds the layout before it opens it, and the first-boot seed lands at the sync's modes. |
