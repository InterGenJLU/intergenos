# NVIDIA on InterGenOS — Module Signing Chain

InterGenOS's kernel ships `CONFIG_MODULE_SIG_FORCE=y` — every loadable
kernel module MUST carry a valid signature from a key the kernel trusts,
or the module is rejected. NVIDIA's modules are not exempt.

This document covers the end-to-end signing flow + verification recipes
+ recovery procedures.

## The signing chain

The InterGenOS NVIDIA package signs each freshly built nvidia*.ko with
the per-machine Machine Owner Key (MOK) at install time.

```
+---------------------------+
| Forge installer           |
| generates MOK keypair at  |   /var/lib/intergen/mok/mok.{key,crt}
| install time              |   (per installer/backend/mok.py)
+-------------+-------------+
              |
              | mokutil --import mok.der
              v
+---------------------------+
| Cert staged in EFI vars   |   "MokNew" UEFI variable
+-------------+-------------+
              |
              | first boot, MokManager prompts
              | user accepts cert
              v
+---------------------------+
| Cert enrolled at firmware |
| level (UEFI "MokListRT")  |
+-------------+-------------+
              |
              | every kernel boot
              v
+---------------------------+
| Kernel loads MOK into     |   .secondary_trusted_keys keyring
| .secondary_trusted_keys   |   (gated by CONFIG_SECONDARY_TRUSTED_KEYRING)
+-------------+-------------+
              |
              | pkm install nvidia happens
              v
+---------------------------+
| post-install hook builds  |
| open kernel modules + signs|
| each with sign-file +     |
| MOK private half          |
+-------------+-------------+
              |
              | modprobe nvidia
              v
+---------------------------+
| Kernel walks               |
| .secondary_trusted_keys,   |
| finds MOK, verifies         |
| signature, loads module    |
+---------------------------+
```

Four distinct keys, each with one job:

1. **Distro EFI key** (PIV slot 9c hardware token) — signs the bootloader
   + kernel at release time. NEVER touches user systems.
2. **Ephemeral kernel module-signing key** — built into vmlinuz; signs
   in-tree kernel modules at kernel-build time. Private half discarded.
3. **Per-machine MOK** — generated at Forge install time; signs out-of-
   tree modules (like NVIDIA's). Lives at `/var/lib/intergen/mok/`.
4. **Distro GPG key** — signs the pkm repo index. Orthogonal to module
   loading.

The NVIDIA package only interacts with key #3.

## How to verify the chain end-to-end

### Verify a freshly-signed nvidia.ko carries a signature

```
# The signature trailer should appear in the last 28 bytes of the .ko
tail -c 28 /lib/modules/$(uname -r)/extra/nvidia/nvidia.ko | xxd
# Expected: ASCII "~Module signature appended~." at the tail

# modinfo extracts the signature metadata
modinfo -F sig_hashalgo /lib/modules/$(uname -r)/extra/nvidia/nvidia.ko
# Expected: sha256

modinfo -F signer /lib/modules/$(uname -r)/extra/nvidia/nvidia.ko
# Expected: "InterGenOS Machine Owner Key"
```

### Verify the kernel loaded the MOK

```
cat /proc/keys | grep secondary
# Expected: a keyring entry for .secondary_trusted_keys

cat /proc/keys | grep "InterGenOS"
# Expected: an asymmetric key under .secondary_trusted_keys with the MOK CN
```

### Verify the module load succeeded under enforcement

```
cat /sys/module/nvidia/refcnt
# Expected: a number >= 0

dmesg | grep "nvidia: module verification"
# Expected: nothing (silence = success)
# Bad case: "nvidia: module verification failed: signature and/or required key missing"

cat /sys/kernel/security/lockdown
# Expected: [integrity] or [confidentiality]
# (lockdown only matters for kexec/sysrq; module-signing is independent)
```

## Failure mode recovery

### MOK not enrolled at NVIDIA install

**Symptom**: post-install hook completes, but on reboot `modprobe nvidia`
fails with "Required key not available".

**Diagnosis**:
```
mokutil --sb-state           # is Secure Boot on?
mokutil --list-enrolled      # is the InterGenOS MOK enrolled?
cat /proc/keys | grep -i intergen
```

**Recovery**:
- Stage MOK enrollment: `sudo mokutil --import /var/lib/intergen/mok/mok.der`.
- Reboot. MokManager will prompt — accept the InterGenOS MOK.
- After the next reboot, retry `modprobe nvidia`.

### MOK enrollment declined by user

**Symptom**: Same as above, but the user pressed "Continue boot" instead
of "Enroll MOK" at MokManager.

**Recovery options**:
1. Re-stage: `sudo mokutil --import /var/lib/intergen/mok/mok.der` + reboot
   + accept this time.
2. Accept the security degradation: `sudo mokutil --disable-validation`
   (requires Secure Boot off; modules then load unsigned).

### MOK lost (/var partition corrupted)

The EFI variable still holds the public half, but the private half at
`/var/lib/intergen/mok/mok.key` is gone. NEW modules cannot be signed
with a matching cert.

**Recovery**: `mokutil --reset` + reboot + re-enrollment with a fresh
MOK generated via Forge's mok.py routines or a manual `openssl req`
followed by `mokutil --import`.

### Module compile fails

**Symptom**: post-install hook hits "make modules" failure.

**Diagnosis**: NVIDIA's open kernel modules may be incompatible with
the running kernel version (e.g. kernel removed a symbol nvidia uses).

**Recovery**:
- Check NVIDIA's upstream tracker at
  https://github.com/NVIDIA/open-gpu-kernel-modules/issues
- Pull a known-good patch into `/usr/src/nvidia-open-${ver}/` and re-run
  `/var/lib/pkm/hooks/nvidia/rebuild-modules`.

## Manual sign + load (for debugging)

```
# Re-sign a specific .ko manually
sudo /var/lib/pkm/hooks/nvidia/sign-module.sh \
    /lib/modules/$(uname -r)/extra/nvidia/nvidia.ko \
    $(uname -r)

# Verify
modinfo -F signer /lib/modules/$(uname -r)/extra/nvidia/nvidia.ko

# Load
sudo modprobe nvidia
```
