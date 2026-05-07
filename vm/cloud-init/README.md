# cloud-init seed for the InterGenOS build VM

This directory contains the cloud-init configuration that bootstraps the
build VM on first boot. The two files (`meta-data` + `user-data`) are
packed into a `seed.iso` and attached to the VM at first boot; cloud-init
reads them and applies the configuration.

## Files

- **`meta-data`** — VM identity (instance-id, local-hostname). Static.
- **`user-data`** — User account, ssh authorization, package install,
  and post-install scripts. **Contains template placeholders** (see
  below) that must be substituted before use.

## Template placeholders

`user-data` is **not** ready to use as-is. Three placeholders must be
substituted before this file is packed into a seed.iso:

| Placeholder | Replace with |
|---|---|
| `<username>` | The Linux username for the VM operator (e.g. `christopher`). |
| `REPLACE_WITH_PASSWORD_HASH` | A SHA-512 password hash from `mkpasswd --method=sha-512`. |
| `REPLACE_WITH_SSH_PUBLIC_KEY` | The full public-key text (one line) from the host's `~/.ssh/id_*.pub`. |

The placeholders are intentional — committing real credentials to the
repo would be a supply-chain risk, and rotating credentials on every
operator change is unnecessary. The substitution is performed by
`scripts/build-vm-seed.sh` (or the equivalent Makefile target) at the
moment the seed.iso is generated.

## Why the file is named `user-data`, not `user-data.template`

cloud-init looks for the literal filename `user-data` inside the seed
ISO; the substituted output must end up at that path. Renaming the
in-tree source to `.template` would mean every consumer (the seed-
generation script, any CI lane, future contributors) has to remember
the rename, which is a bigger surface for drift than this README.

## Pre-commit safety check

`scripts/check-public-content.py` (and its pre-push gate counterpart)
should reject any commit that touches `user-data` and replaces a
placeholder with a real-looking value. If you hit this gate, the fix
is to revert the substitution and only do it inside the build pipeline
where the substituted file never lands in version control.
