# InterGenOS First-Boot Password Greeter

**Last updated:** 2026-04-30
**Applies to:** InterGenOS v1.0 and later

The first time you boot an InterGenOS image, the system pauses before the login screen and prompts you to set a password for your user account. This document explains what you will see, how to respond, and what happens if something goes wrong.

This page is the canonical reference for the first-boot greeter. The shim-review submission cites the credentials posture documented here when answering questions about default credentials.

## Overview

InterGenOS ships no default passwords. There is no "intergenos" or "root" or "live" or "guest" credential on a fresh image; there is no shared secret a stranger could try. Instead, the image is bootable but unable to admit a logged-in user until you, the person at the console, choose your own password.

This is enforced in two layers:

1. **Build time.** The build script that produces an InterGenOS image refuses to run without explicit `--root-password` and `--user-password` arguments. There is no default value to fall through to.
2. **First boot.** Whatever passwords were set at build time are immediately overwritten by a system service that runs once on the first boot and prompts you for new ones. You always pick the password that you actually use.

The first-boot greeter is the second layer. It runs once, in a regular text-mode terminal, before the graphical login screen comes up. After you complete it, a flag file marks the system as "first-boot complete" and the greeter never runs again.

## What you'll see on first boot

After the boot animation finishes, the screen clears and you are dropped into a plain text prompt that looks roughly like this:

```
Welcome to InterGenOS.

Before logging in, please choose a password for your account.

New password:
Retype new password:
```

There is no graphical interface. There is no progress bar. There is no theme. This is deliberate — a tty prompt is the most reliable way to ask for a password on a brand-new system, and it does not depend on the graphical stack working correctly. (See [Security model](#security-model) for why this matters.)

You will be asked, in order, for:

1. A new password for the root account (used for system administration — `su`, single-user mode, recovery boot).
2. A new password for the user account the image was built for (your everyday login).

Both prompts are required. The system will not finish booting until both passwords are set. After both prompts succeed, the greeter prints a brief confirmation, and the system continues booting into the graphical login screen.

## Setting your password

When the greeter asks you to type a password, it does not echo your keystrokes — that is normal. Typing is silent so the password does not appear on the screen.

Recommendations:

- **Length matters more than complexity.** A passphrase of three or four ordinary words is strong and memorable; a short scramble of symbols is neither.
- **Pick something you can type without thinking.** You will type this password every time you unlock a screen, install a package, or run an administrative command. A password you constantly mistype is one you will be tempted to weaken.
- **Do not reuse a password from another account.** A password leaked from a website should not unlock your laptop.

If you mistype the password (the two entries do not match, or the password is empty), the greeter will reject the entry and ask again. There is no retry limit; you can take as many attempts as you need. The system will not boot past the prompt until both entries match a non-empty value.

Beyond empty/mismatch rejection, the greeter does not impose its own length or character-class rules. It hands the password to `chpasswd`, which honors whatever password-policy rules the system's PAM stack is configured with — typically `pam_pwquality` or `pam_cracklib` checking for minimum length, dictionary words, repeated characters, and similar weakness patterns. If your password is rejected by PAM, the rejection message (for example, "Password is too short" or "Password is based on a dictionary word") is printed on the same tty so you can see the reason and pick something the policy accepts.

## Setting your username

The username for your account is set at image-build time, not at first boot. This is intentional: usernames are baked into file ownership across the system before the image is even written, and changing one safely after the fact requires more than just renaming a directory. If you need a different username than the image was built with, the simplest path is to rebuild the image with `--image-user <name>`, not to edit the running system.

If you received the image from someone else and the built-in username does not suit you:

1. Boot to first login and complete the greeter normally with a password you choose.
2. After login, you can create an additional user with your preferred name (`useradd`, `passwd`, etc.), grant it administrator rights if needed, and use that account going forward. The original username can be left in place or removed once the new account is fully working.

## If something goes wrong (recovery)

The greeter is designed to fail safe, not to leave you locked out. If something genuinely breaks — a power cycle mid-prompt, a kernel hang, a broken graphical stack — the system has fallback paths.

**Power-cycled mid-prompt.** Nothing was committed; the flag file that marks first-boot as complete is only written after both prompts succeed. The greeter will run again on the next boot.

**Greeter unit fails to start.** The unit's `OnFailure=getty@tty1.service` directive routes systemd to a normal text-mode login prompt on the same tty if the greeter cannot run for any reason — a missing `chpasswd` binary, a missing user account, an `INTERGENOS_USER` environment variable that was not propagated from the build, or a script exit through any of its non-zero exit codes. The login prompt accepts the credentials the image was built with — those are the brief-window fallback that the greeter would normally have overwritten. Once you can log in, run `passwd` (and `sudo passwd root`) to set credentials of your own choosing.

**Booted into an SSH-only or headless environment.** The greeter is bound to `/dev/tty1`. On a system with no usable tty1 — for example, a serial-console-only server or a headless appliance — the unit's `OnFailure` routes to `getty@tty1.service`, which also requires tty1 and therefore also cannot serve a prompt. The system will boot to multi-user state but the local console will be unreachable; SSH access (if installed) works using the build-time credentials. Serial-console fallback is out of scope for v1, which is desktop-first; if you need to install InterGenOS on hardware without tty1, the supported path is to log in via SSH with the build-time credentials, run `passwd` (and `sudo passwd root`), and then `touch /etc/intergenos/first-boot-completed` to mark first boot as done so the greeter does not block subsequent boots.

**Forgotten password after first boot.** First-boot only sets the password the first time. After that, recovering a lost password is the same as on any Linux distribution — boot to single-user mode or a recovery image, mount the root filesystem, and run `passwd <user>`. There is nothing InterGenOS-specific about that path.

## Internals

This section is for new maintainers and reviewers who want to understand the implementation. End users do not need to read it.

### Files

| Path | Purpose |
|---|---|
| `/etc/systemd/system/intergenos-first-boot-greeter.service` | systemd unit definition that runs the greeter on first boot |
| `/etc/systemd/system/intergenos-first-boot-greeter.service.d/intergenos-user.conf` | drop-in installed by `create-image.sh` setting `Environment=INTERGENOS_USER=<account>` so the greeter knows which account to prompt for |
| `/usr/libexec/intergenos/first-boot-greeter` | bash script that prompts for the password and writes it via `chpasswd` |
| `/etc/intergenos/first-boot-completed` | flag file written after a successful first boot; gates the unit's `ConditionPathExists` |

### Idempotency contract

The greeter unit is `Type=oneshot` and uses `ConditionPathExists=!<flag>` so the systemd dependency is satisfied trivially on every subsequent boot. The flag is only written after a successful run; an interrupted run leaves the flag absent, and the unit re-fires on the next boot. There is no per-user state and no journal-only memory of progress.

The flag file is written atomically — temp-file plus `mv` — so a crash during the write does not leave a partially-written marker that would skip the prompt without the password having been set.

### Ordering

The unit is ordered `After=systemd-vconsole-setup.service systemd-tmpfiles-setup.service intergen-firstboot.service` so the console font and tmpfiles state are settled and the boot-animation stage has finished before the prompt appears. It also declares `Conflicts=getty@tty1.service` so that systemd takes tty1 from getty for the duration of the greeter, then hands tty1 back to getty (or whatever the graphical target activates) once the unit completes.

### Exit codes

The greeter script exits with a small ladder of distinct codes so the systemd journal makes failures diagnosable:

| Exit code | Meaning |
|---|---|
| `0` | Both passwords set successfully; flag file written; unit completes cleanly |
| `2` | `chpasswd` binary not found in the chroot — install hook is broken |
| `3` | The user account named by `INTERGENOS_USER` does not exist in `/etc/passwd` — `useradd` step in `create-image.sh` failed silently |
| `4` | `INTERGENOS_USER` environment variable empty or not set — service drop-in (`intergenos-user.conf`) missing or malformed |

Any non-zero exit triggers `OnFailure=getty@tty1.service`, so the user always reaches a usable prompt — the codes are a forensic aid for diagnosing why the greeter dropped through, not a UX-affecting branch.

### Password channel

The greeter pipes the chosen password to `chpasswd` via stdin. The password is never passed on a command line, never written to a file on disk between input and the `chpasswd` invocation, and never appears in the systemd journal beyond a generic "greeter completed" indicator.

### What the greeter does *not* do

- It does not generate keys, certificates, or machine identifiers. Those have separate per-machine generation paths in the installer.
- It does not enroll a Machine Owner Key or interact with Secure Boot. Module-signing trust is established at install time, not at first boot. See [`docs/ephemeral-module-signing.md`](ephemeral-module-signing.md).
- It does not collect telemetry. The flag file records that a first boot completed; nothing else is logged about the user's input.

## Security model

The first-boot greeter exists because of a simple security principle: an image with a known default credential is an image with a known way in. If the password to your laptop is `intergenos` because that was the literal default in `create-image.sh`, then anyone who has heard of InterGenOS and has physical access to your unattended machine can log in. That is not a hardened distribution; that is a published key under the doormat.

The two-layer posture closes that hole on both sides:

- The build-time half makes it impossible to accidentally produce an image with a shared default. The build refuses to run without explicit credentials. A reviewer who downloads the InterGenOS source and runs the build will see this as the first behavior of the build script — a clear signal that no shared default exists.
- The first-boot half makes it impossible for whoever built the image to retain knowledge of the credential after handing it off. Whatever the builder typed at build time gets overwritten the first time the image actually boots. The downloader is the only person who knows the password they are using.

This matches the Prime Directive: the user is in control of their own machine. The user picks the password they actually use. The user does not inherit a builder's choice silently. The user does not have to trust that some default was secure enough.

This also matches the security-only alignment: every password decision is a security decision, and the only acceptable answer to "what is the default password" is "there is no default password."

## See also

- [`docs/ephemeral-module-signing.md`](ephemeral-module-signing.md) — module-signing trust chain
- [`docs/signing-key.md`](signing-key.md) — release signing key publication
- [`docs/signing-procedure.md`](signing-procedure.md) — signing-ceremony procedure (Appendix B covers Nitrokey first-touch)
- `intergenos-first-boot(7)` — man page summary of the same content
