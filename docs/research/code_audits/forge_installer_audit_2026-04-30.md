# InterGenOS Installer Security Audit — forge-installer backend + TUI

**Date:** 2026-04-30T11:01Z
**Auditor:** DeepSeek V4 PRO via @general subagent
**Master target:** 7c03d58
**Branch:** (archived from working branch — original deleted post-merge)

---

## Scope: installer/backend/ + installer/frontend/tui.py

Depth-first pre-v1.0 hardening review of the interactive installer. Covers bootloader installation, disk partitioning, chroot execution, user account creation, MOK enrollment, and curses TUI input handling.

---

### CRITICAL

#### CI1 — Shell Injection via `shell=True` in ESP Mount Path (CRITICAL)

**File:** installer/backend/bootloader.py:99-102

**Problem:** `subprocess.run()` is called with `shell=True` and an f-string embedding `partitions['esp']` — a device path constructed from user-selected disk data via `_partition_paths()` / `partition_disk_alongside()`. A specially crafted disk name or compromised `lsblk` output injects shell metacharacters via the ESP device path (e.g., `/dev/sda1; rm -rf /`). `shell=True` subjects the command to full shell parsing: whitespace, semicolons, backticks, and `$()` are all interpreted. This is the only `shell=True` call in the entire backend; all other `subprocess.run` calls use list form or `shell=False` (default).

```python
subprocess.run(
    f"mountpoint -q {esp_mount} || mount {partitions['esp']} {esp_mount}",
    shell=True, capture_output=True
)
```

**Fix:** Replace with list-form subprocess — check mountpoint and mount separately:
```python
if subprocess.run(["mountpoint", "-q", esp_mount]).returncode != 0:
    subprocess.run(["mount", partitions["esp"], esp_mount], check=True)
```

---

#### CI2 — `run_chroot` Passes Unescaped Strings to `/bin/bash -c` — Universal Injection Surface (CRITICAL)

**File:** installer/backend/hooks.py:73-78

**Problem:** Every call to `run_chroot` constructs a shell command string via Python f-strings and passes it to `/bin/bash -c`. This means any dynamic value embedded in those f-strings — device paths, usernames, hostnames, locale strings, timezone paths, MOK paths, version strings — is subject to shell metacharacter interpretation. The burden of sanitization is pushed to every call site, and failures there are individual injection bugs. This is the single largest attack surface in the installer: **14 unique call sites** across `bootloader.py`, `users.py`, and `mok.py` construct dynamic shell commands.

```python
def run_chroot(target, command):
    result = subprocess.run(
        ["chroot", str(target), "/bin/bash", "-c", command],
        capture_output=True, text=True
    )
    return result.returncode, result.stdout, result.stderr
```

**Evidence — call sites with unsanitized user-controlled data:**
- `bootloader.py:108-119` — `BOOTLOADER_ID` (constant, safe)
- `bootloader.py:133-137` — `ESP_BOOT_DIR`, `SHIM_BINARY` (constants)
- `bootloader.py:171-183` — `mok_keypair['key_path']`, `mok_keypair['cert_path']` (MOK-controlled)
- `bootloader.py:199-203` — `disk_dev`, `part_num` from `_split_partition(esp_dev)` (parsed)
- `bootloader.py:239-241` — `disk` (device path from user selection)
- `users.py:41` — `username`, `group_str` (user input from curses, zero validation)
- `users.py:72` — `svc` (hardcoded list, safe)
- `mok.py:77-81` — `key_path`, `cert_path`, `common_name` (validated)
- `mok.py:87` — `cert_path`, `der_path` (controlled)
- `mok.py:93` — `key_path`, `cert_path`, `der_path` (controlled)
- `mok.py:138` — `der_path` (controlled)
- `mok.py:167` — `key_path`, `cert_path`, `binary_path`, `output_path` (controlled)
- `mok.py:191` — `cert_path`, `binary_path` (controlled)

**Fix:** Add list-form path to `run_chroot` and audit all 14 call sites:
```python
def run_chroot(target, command):
    if isinstance(command, list):
        result = subprocess.run(
            ["chroot", str(target)] + command,
            capture_output=True, text=True
        )
    else:
        result = subprocess.run(
            ["chroot", str(target), "/bin/bash", "-c", command],
            capture_output=True, text=True
        )
    return result.returncode, result.stdout, result.stderr
```
Require callers to pass lists or manually `shlex.quote()` every dynamic value in string-form commands. `users.py:41` is the most urgent migration target (see CI3).

---

#### CI3 — Username Shell Injection in `create_user` (CRITICAL)

**File:** installer/backend/users.py:41

**Problem:** `username` is collected from `curses.getstr()` in TUI at `frontend/tui.py:318` with zero validation. A username like `; chmod 777 /etc/sudoers; #` breaks out of the `useradd` command and executes arbitrary code as root inside the chroot. While `useradd` itself validates usernames and would reject the worst cases, the shell injection happens BEFORE `useradd` is invoked — bash interprets the semicolon as command separator. The chroot shares `/dev` with the host (via `mount_virtual_fs`), so escape to the host is possible via `/dev/sda` or `/dev/mem`.

```python
rc, stdout, stderr = run_chroot(target,
    f"useradd -m -G {group_str} -s /bin/bash {username}"
)
```

**Evidence of zero validation in the pipeline:**
1. `tui.py:318` — `self.username = self.prompt(17, "  Username", "")`
2. `tui.py:99-107` — `prompt()` uses `curses.getstr()` with no content validation
3. `users.py:41` — raw f-string interpolation into `/bin/bash -c`

**Fix:** Add a username validation regex before any use:
```python
import re
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]*$")

def create_user(target, username, password, groups=None):
    if not _USERNAME_RE.match(username):
        raise ValueError(f"Invalid username: {username!r}")
    ...
```
Also `shlex.quote()` the username in the shell command as defense-in-depth.

---

### HIGH

#### HI1 — Post-Install Hook Version Injection from `package.yml` (HIGH)

**File:** installer/backend/hooks.py:126-130, 158-165

**Problem:** `hook['version']` is parsed from `package.yml` files found on disk during the `run_post_install_hooks` scan. The stripping only removes leading/trailing quotes; interior single quotes are NOT escaped. A malicious `package.yml` containing:
```yaml
version: "'; malicious_command; echo '"
```
Would produce: `export PKG_VERSION=''; malicious_command; echo '' && ...` — injecting arbitrary shell commands executed as root inside the chroot.

```python
# hooks.py:129 — only leading/trailing quotes stripped
version = line.split(":", 1)[1].strip().strip('"\'')
```

**Fix:** Use `shlex.quote()` on the version string:
```python
cmd = (
    f"export PKG_VERSION={shlex.quote(hook['version'])} && "
    f"export version={shlex.quote(hook['version'])} && "
    f"source {shlex.quote(pkg_path)} && "
    f"post_install"
)
```

---

#### HI2 — `run_post_install_hooks` Sources Arbitrary Build Scripts as Root (HIGH)

**File:** installer/backend/hooks.py:158-165

**Problem:** The `source {pkg_path}` directive executes an arbitrary shell script (`build.sh`) as root inside the chroot. There are zero integrity checks on the build script — no signature verification, no checksum, no content inspection. If an attacker can place a malicious `build.sh` in the packages directory (e.g., via a supply-chain compromise of the package repository), they gain root code execution during install.

```python
pkg_path = f"/tmp/installer-packages/{hook['tier']}/{hook['name']}/build.sh"
cmd = (
    f"export PKG_VERSION='{hook['version']}' && "
    f"export version='{hook['version']}' && "
    f"source {pkg_path} && "
    f"post_install"
)
```

**Evidence:** The function scans the packages directory for any `.sh` files containing `post_install()` (line 122), copies the entire directory into the target (`cp -a`, lines 148-151), then sources the scripts (line 163). No signing, no hash verification, no sandboxing.

**Fix:**
1. Sign build scripts and verify signatures before sourcing.
2. Run post-install hooks in a restricted shell (`bash --restricted` or a seccomp sandbox).
3. At minimum, validate that `pkg_path` resolves to a regular file within the expected directory tree and log the path before sourcing.

---

#### HI3 — Unsanitized Hostname Writes Newline-Injectable Content to `/etc/hosts` (HIGH)

**File:** installer/backend/config.py:46-54

**Problem:** `hostname` is collected from `curses.getstr()` in TUI (`tui.py:304`) with zero validation. If a user enters `host\n127.0.0.1 evil.com\n#`, the generated `/etc/hosts` would contain arbitrary DNS entries. This allows DNS poisoning of the installed system — an attacker with TUI access (or a malicious operator) could redirect traffic for any domain.

```python
hosts = (
    f"# /etc/hosts — InterGenOS\n"
    f"\n"
    f"127.0.0.1    localhost\n"
    f"127.0.1.1    {hostname}.localdomain {hostname}\n"
    ...
)
(etc / "hosts").write_text(hosts)
```

**Evidence:** No hostname validation exists anywhere. RFC 952 restricts hostnames to alphanumeric + hyphen, max 253 chars, but no check is enforced.

**Fix:** Validate hostname with regex before writing to config files:
```python
_HOSTNAME_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*$"
)
if not _HOSTNAME_RE.match(hostname):
    raise ValueError(f"Invalid hostname: {hostname!r}")
```
Reject input containing newlines (`\n`, `\r`), backslashes, or shell metacharacters.

---

#### HI4 — TOCTOU in `partition_disk_alongside` — Partition Re-Detection Race (HIGH)

**File:** installer/backend/disks.py:293-312

**Problem:** After creating a partition with `parted`, the code re-scans all disks via `detect_disks()` → `lsblk -J` and selects the partition with the highest number as the new root. Between `partprobe`/`udevadm settle` and the `mkfs.ext4` call, an attacker who can create a higher-numbered partition on the same disk (e.g., via a concurrently running udev rule) could cause the installer to format the WRONG partition. Classic TOCTOU: the partition table state is checked at time T1 but used at time T2.

```python
_run(f"parted -s {disk.path} mkpart root ext4 {free_start_mb}MiB 100%")
_run(f"partprobe {disk.path}")
_run("udevadm settle")

new_disk = next((d for d in detect_disks() if d.path == disk.path), None)
...
new_root = max(new_disk.partitions, key=lambda p: p.number)

_run(f"mkfs.ext4 -L intergenos {new_root.path}")
```

**Evidence:** The `max(..., key=lambda p: p.number)` heuristic at line 309 selects the partition with the highest partition number as the target. The window between `detect_disks()` and `mkfs.ext4` is small but real. Worst case: formats the ESP (shared in alongside mode), rendering the system unbootable.

**Fix:**
1. Use `parted -m print` to get the exact partition number created, rather than re-detecting.
2. Or use `sgdisk -i <partnum>` on the specific partition before formatting.
3. At minimum, verify the new partition's start sector matches `free_start_mb` before formatting.

---

### MEDIUM

#### MI1 — Entire `_run` Subsystem Accepts String Commands Through `shlex.split` — Incomplete Sanitization (MEDIUM)

**File:** installer/backend/disks.py:366-388

**Problem:** `shlex.split()` provides shell-like word splitting but is NOT a security boundary. Strings passed to `_run` are constructed via f-strings at 15 call sites, and shlex splitting can produce unexpected argument vectors if the input contains unbalanced quotes, backslashes, or special characters. Pathological inputs can cause `shlex.split` to raise `ValueError` (unterminated quote), which is unhandled and crashes the installer with a confusing error.

```python
def _run(cmd):
    import shlex
    if isinstance(cmd, str):
        cmd_list = shlex.split(cmd)
    else:
        cmd_list = cmd
    ...
    result = subprocess.run(cmd_list, capture_output=True, text=True)
```

**Evidence — call sites constructing string commands:**
- `disks.py:127-131, 136-137, 142-145, 149, 260, 264-265, 294, 301-302, 312, 328, 331-334` — all embedding disk/partition paths
- These paths ultimately derive from `lsblk -J` output, which is kernel-generated and typically safe, but no explicit validation is performed.

Example: `disk_path = "/dev/sda' -- evil_arg 'x"` → shlex would split this into unexpected tokens.

**Fix:**
1. Require callers to always use list form; deprecate string form entirely.
2. Add input validation: all disk paths must match `^/dev/[a-zA-Z0-9_/]+$`.
3. Wrap `shlex.split` in a try/except with a clear error message.

---

#### MI2 — Device Path Classification via Substring Match — Fragile NVMe/MMC Detection (MEDIUM)

**File:** installer/backend/disks.py:394, 421-424; installer/backend/bootloader.py:254

**Problem:** Both `_partition_paths()` and `_split_partition()` / `_split_partition_path()` use `"nvme" in disk_path` substring matching rather than prefix matching. A non-NVMe disk path containing the substring "nvme" (e.g., `/dev/sda_nvme_backup`) would be incorrectly classified, producing incorrect partition paths.

```python
# disks.py:394
sep = "p" if "nvme" in disk_path or "mmcblk" in disk_path else ""

# bootloader.py:254
if "nvme" in part_dev or "mmcblk" in part_dev:
    idx = part_dev.rfind("p")
```

**Fix:** Use proper regex with start-of-string anchoring:
```python
import re
_NVME_RE = re.compile(r"^/dev/nvme\d+n\d+$")
_MMC_RE = re.compile(r"^/dev/mmcblk\d+$")
sep = "p" if _NVME_RE.match(disk_path) or _MMC_RE.match(disk_path) else ""
```

---

#### MI3 — No Validation on Locale, Timezone, Keymap Inputs (MEDIUM)

**File:** installer/backend/config.py:58-69, 73-85; installer/frontend/tui.py:305-307

**Problem:** `locale`, `keymap`, and `timezone` are user-supplied strings from `tui.py:305-307` with zero validation. Newline injection in `locale` or `keymap` can inject additional config directives. The `timezone` value is used to construct a filesystem path — `..` traversal could escape the zoneinfo directory. Additionally, `set_timezone` performs `localtime.unlink()` followed by `localtime.symlink_to()` — a TOCTOU window exists between unlink and symlink.

```python
# config.py:61 — newline injection possible
(etc / "locale.conf").write_text(f"LANG={locale}\n")

# config.py:76 — path traversal possible
zoneinfo = Path(target) / "usr" / "share" / "zoneinfo" / timezone
```

**Evidence:** `tui.py:305` — `self.timezone = self.prompt(7, "Timezone", self.timezone)`; `tui.py:306` — `self.locale = self.prompt(9, "Locale", self.locale)` — both using `curses.getstr()` with no validation.

**Fix:**
1. Validate locale against `locale -a` output or a whitelist.
2. Validate timezone against `timedatectl list-timezones` output.
3. Validate keymap against `localectl list-keymaps` output.
4. Reject any value containing newlines, path separators (`/`, `..`), or shell metacharacters.

---

#### MI4 — `mount_virtual_fs` Binds Host `/dev` into Chroot — Full Device Access (MEDIUM)

**File:** installer/backend/hooks.py:8-22

**Problem:** The chroot environment shares the host's `/dev` filesystem. Any code running inside the chroot (post-install hooks, GRUB installation, package extraction) has direct access to ALL host block devices, raw memory (`/dev/mem`), and kernel interfaces. If a malicious post-install hook or compromised package archive exploits this, it can read/write the host's disks.

```python
mounts = [
    (["mount", "--bind", "/dev", f"{target}/dev"], f"{target}/dev"),
    (["mount", "--bind", "/dev/pts", f"{target}/dev/pts"], f"{target}/dev/pts"),
    ...
]
```

**Fix:**
1. Document this as a deliberate design choice in a security model document.
2. Consider bind-mounting only the specific device nodes needed (e.g., `/dev/null`, `/dev/zero`, `/dev/random`, the target disk devices).
3. For post-install hooks, consider `systemd-nspawn` or a container with `--private-dev` for stronger isolation.

---

#### MI5 — `detect_disks` Passes Raw `lsblk` Path to JSON — No Output Integrity Check (MEDIUM)

**File:** installer/backend/disks.py:57-61

**Problem:** The function trusts `lsblk -J` output completely. If `lsblk` is compromised (e.g., a malicious `lsblk` in PATH, or a kernel presenting fake device information via a compromised driver), the installer will operate on attacker-chosen device paths. No verification that `lsblk` is the genuine `/usr/bin/lsblk` from util-linux. The path-to-disk mapping is taken as ground truth.

```python
result = subprocess.run(
    ["lsblk", "-J", "-b", "-o",
     "NAME,SIZE,MODEL,RM,TYPE,FSTYPE,MOUNTPOINT,LABEL,UUID,PATH"],
    capture_output=True, text=True
)
data = json.loads(result.stdout)
```

**Fix:**
1. Use absolute path `/usr/bin/lsblk` instead of bare `lsblk`.
2. Verify that each returned path exists and is a block device: `stat.S_ISBLK(os.stat(path).st_mode)`.
3. Cross-reference with `/sys/block/` for critical operations.

---

### LOW

#### LI1 — MOK Enrollment Password Displayed Twice, Never Zeroed from Memory (LOW)

**File:** installer/backend/mok.py:196-204; installer/frontend/tui.py:602

**Problem:** `generate_enrollment_password()` generates a 12-character MOK password with ~69 bits of entropy — adequate for single-use enrollment. However, the password is stored as a plain instance variable (`tui.py:39`), displayed twice (during setup and on the completion screen), and never zeroed from memory.

```python
def generate_enrollment_password():
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz"
    return "".join(secrets.choice(alphabet) for _ in range(12))
```

**Fix:**
1. Overwrite `self.mok_password` with null bytes after display.
2. Display only once (remove from completion screen); instruct the user to write it down.
3. Consider offering to save the password to a removable USB stick instead of displaying it.

---

#### LI2 — `unmount_target` Uses Best-Effort Unmounts Without Error Checking (LOW)

**File:** installer/backend/disks.py:339-345

**Problem:** All `umount` calls ignore return codes. If an unmount fails (e.g., a process still has files open), the installer silently continues. A failed unmount of `/dev` could leave the host `/dev` bind-mounted into the target after the installer exits, presenting a post-install persistence risk. The unmount order is maintained manually rather than derived from the mount order.

```python
def unmount_target(target="/mnt/target"):
    for sub in ["boot/efi", "dev/pts", "dev", "proc", "sys", "run"]:
        path = f"{target}/{sub}"
        subprocess.run(["umount", path], capture_output=True)
    subprocess.run(["umount", target], capture_output=True)
```

**Fix:**
1. Add `check=True` or log warnings on unmount failures.
2. Use `umount -l` (lazy unmount) as a fallback.
3. Derive the unmount order from the mount order programmatically (`reversed(mounts)`).

---

#### LI3 — No Rollback on Partial Install Failure — Leftover State (LOW)

**File:** installer/frontend/tui.py:425-579

**Problem:** The install screen (`screen_install`) executes 8 sequential steps: partition, mount, install packages, generate config, post-install hooks, user accounts, MOK + bootloader, unmount. If any step between 1 and 8 fails, the system is left in an undefined state. The disk may be partially partitioned, filesystems partially written, and critical config files missing. No rollback is attempted. Specific failure scenarios:
- Failure at step 3 (install packages): formatted-but-empty root partition
- Failure at step 5 (post-install hooks): packages installed but hooks unrun
- Failure at step 7 (bootloader): all files installed but no boot capability

**Fix:**
1. Implement a checkpoint system: record each completed step, so retry can skip already-completed steps.
2. On failure, offer "retry" vs "abort — disk will need to be re-partitioned".
3. At minimum, log the failure point and system state to a debug log.

---

#### LI4 — Shared ESP Free Space Not Checked Before Copying EFI Binaries (LOW)

**File:** installer/backend/disks.py:154-164, 286-291

**Problem:** In alongside mode, the installer shares the existing Windows ESP. It copies shim (~1MB), GRUB (~200KB), MokManager (~1MB), and fallback EFI binaries to both `EFI/intergenos/` and `EFI/BOOT/`. If the ESP is already near capacity, copy operations could fail mid-way, leaving a broken ESP. `detect_existing_esp` filters by total partition size (100MB–2GB) but does not check available free space.

```python
def detect_existing_esp(disk):
    for p in disk.partitions:
        if p.fstype == "vfat" and 100 * 1024**2 <= p.size_bytes <= 2 * 1024**3:
            return p
    return None
```

**Fix:** Before copying EFI binaries, check free space:
```python
stat = os.statvfs(esp_mount)
free_bytes = stat.f_frsize * stat.f_bavail
if free_bytes < 10 * 1024 * 1024:  # 10MB minimum
    raise RuntimeError(f"ESP has insufficient free space: {free_bytes} bytes")
```

---

#### LI5 — Curses Input Width Limit Bypass — Silent Truncation (LOW)

**File:** installer/frontend/tui.py:99-110

**Problem:** `curses.getstr()` with width limit silently truncates input at the specified max characters. Users may believe their full input was accepted when only a prefix was captured. On an 80-column terminal with typical prompts, this leaves ~50 characters — insufficient for edge-case hostnames or timezone paths.

```python
value = self.stdscr.getstr(row, prompt_len, w - prompt_len - 2).decode().strip()
```

**Fix:** Use `curses.textpad` or a scrolling input field, or warn the user when their input is truncated.

---

## Summary

| # | Severity | Category | File:Line | Description |
|---|----------|----------|-----------|-------------|
| CI1 | **CRITICAL** | Shell injection | `bootloader.py:99-102` | `shell=True` with `partitions['esp']` in f-string — metacharacter injection |
| CI2 | **CRITICAL** | Shell injection | `hooks.py:73-78` | `run_chroot` passes unescaped strings to `/bin/bash -c` — 14 call sites |
| CI3 | **CRITICAL** | Shell injection | `users.py:41` | Username from curses injected unescaped into `useradd` shell command |
| HI1 | **HIGH** | Shell injection | `hooks.py:129, 161-162` | `package.yml` version string with unescaped quotes injected into shell |
| HI2 | **HIGH** | Code execution | `hooks.py:158-165` | Arbitrary `build.sh` scripts sourced as root — no integrity checks |
| HI3 | **HIGH** | Config injection | `config.py:46-54` | Hostname with newlines poisons `/etc/hosts` — DNS hijack |
| HI4 | **HIGH** | TOCTOU | `disks.py:293-312` | Partition re-detection race — wrong partition may be formatted |
| MI1 | **MEDIUM** | Shell injection | `disks.py:366-388` | `_run()` uses `shlex.split` on f-string commands — 15 call sites |
| MI2 | **MEDIUM** | Path handling | `disks.py:394; bootloader.py:254` | Substring match for "nvme"/"mmcblk" — fragile classification |
| MI3 | **MEDIUM** | Config injection | `config.py:61, 68, 76` | Unvalidated locale/timezone/keymap — config file injection |
| MI4 | **MEDIUM** | Isolation | `hooks.py:13-14` | Host `/dev` bind-mounted into chroot — full device access |
| MI5 | **MEDIUM** | Trust boundary | `disks.py:57-61` | `lsblk -J` output trusted implicitly — no integrity check |
| LI1 | **LOW** | Info disclosure | `mok.py:196-204; tui.py:602` | MOK password displayed twice, never zeroed from memory |
| LI2 | **LOW** | Error handling | `disks.py:339-345` | `umount` failures silently ignored — stale mounts possible |
| LI3 | **LOW** | Recovery | `tui.py:425-579` | No rollback on partial install failure |
| LI4 | **LOW** | Resource | `disks.py:154-164` | ESP free space not checked before copying EFI binaries |
| LI5 | **LOW** | Input handling | `tui.py:107` | Curses input silently truncated at terminal width |

### Severity Totals

| Severity | Count |
|----------|-------|
| CRITICAL | 3 |
| HIGH | 4 |
| MEDIUM | 5 |
| LOW | 5 |
| **TOTAL** | **17** |

---

## Cross-Reference to Prior Audit (pkm_igos_build, 2026-04-29)

The prior audit of `pkm/`, `sign-release.sh`, kernel config, and `igos-build/` identified 64 findings across four scopes (9 CRITICAL, 22 HIGH, 16 MEDIUM, 17 LOW). Multiple findings in this installer audit are structurally analogous:

| Installer Finding | Prior Audit Analog | Shared Pattern |
|---|---|---|
| CI1 (`shell=True` ESP mount) | B1, B2, B10 (configure_flags, patch filenames, validation scripts) | `shell=True` + unsanitized f-string interpolation — identical root cause |
| CI2 (`run_chroot` universal injection) | B1, B2, B10 | String interpolation into shell commands without `shlex.quote()` |
| MI1 (`_run` via `shlex.split`) | B1, B2 | F-string → string → subprocess pipeline with no input validation |
| HI1 (version injection) | B1 | YAML-derived values interpolated into shell commands unescaped |
| HI2 (arbitrary script sourcing) | H3 (`--archive` bypasses trust chain) | Code execution without integrity verification |
| MI4 (host `/dev` bind-mount) | — | Unique to installer; analogous concern to B7 (host env wholesale propagation) |
| LI3 (no rollback) | M3, SR1 (partial state on failure) | No atomic transaction across multi-step operations |

**Highest-priority cross-cutting fix:** Migrate all `shell=True` call sites to `shell=False` with argument lists. The prior audit's cross-cutting recommendation (switch from `shell=True` + string interpolation to `shell=False` + argument lists) applies identically to the installer codebase. Combined, the two audits identify **14** distinct `shell=True` / `shlex.split` call sites that should be migrated before shim-review submission.

---

## Prioritized Remediation Plan

### Immediate (before v1.0 installer release)
1. **CI1** — Replace `shell=True` in `bootloader.py:99-102` with list-form subprocess calls
2. **CI3** — Add username validation regex in `users.py:create_user` + `shlex.quote()` defense-in-depth
3. **HI1** — `shlex.quote()` version strings in `hooks.py:run_post_install_hooks`
4. **HI3** — Validate hostname format before writing to `/etc/hosts`

### Short-term (v1.0 hardening cycle)
5. **CI2** — Add list-form path to `run_chroot` and audit all 14 call sites
6. **HI2** — Add build.sh integrity verification (signatures or checksums)
7. **HI4** — Use `parted -m print` to get exact partition number instead of re-detection
8. **MI1** — Deprecate string form of `_run()`, require list arguments throughout
9. **MI3** — Add locale/timezone/keymap validation

### Medium-term (post v1.0)
10. **MI2** — Replace substring matching with proper regex for device path classification
11. **MI4** — Evaluate stronger chroot isolation (systemd-nspawn or private /dev)
12. **MI5** — Add `lsblk` integrity checks and device node validation
13. **LI1-LI5** — Address during general hardening cycle

---

## Next Steps

1. **CI1 + CI3** — CRITICAL shell injection vectors, fix before installer ships to any user
2. **CI2** — Architectural `run_chroot` refactor, fix before post-install hook system accepts third-party packages
3. **HI2** — Supply-chain integrity for build scripts, fix before package repository is opened to community contributions
4. **Cross-cutting:** Execute the `shell=True` → `shell=False` migration across all 14 `run_chroot` call sites and the `_run` subsystem in parallel with the pkm_igos_build migration (64 sites pending from prior audit)
