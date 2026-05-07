# systemd configuration overrides

InterGenOS-specific systemd unit overrides. These files are installed alongside their upstream counterparts and take precedence at runtime.

## Files

| File | Purpose |
|------|---------|
| `sshd.service` | OpenSSH daemon configuration override. InterGenOS-specific defaults: tighter listen-address, hardened ciphers, no-root-login by default. |

## Convention

When a new systemd override is added:

1. Place the unit file here with the standard systemd filename (e.g., `<service>.service`, `<timer>.timer`).
2. Wire it into the installer pipeline so `installer/backend/install.py` copies the file into `/etc/systemd/system/` (or `/etc/systemd/system/<service>.d/<override>.conf` for partial overrides) at install time.
3. Add a row to the table above with a one-line description of what the override changes versus the upstream default.

## Why this directory exists

We don't ship a wholesale custom systemd. We ship targeted overrides for services where InterGenOS's defaults differ from upstream Debian / Arch / Fedora practice. The override pattern keeps the customization auditable: a reader can `diff` against the upstream package and see exactly what we changed and why.

If a service needs a wholesale replacement (not just an override), it should live in `packages/core/<service>/` as a full package with its own `.service` files, not here.
