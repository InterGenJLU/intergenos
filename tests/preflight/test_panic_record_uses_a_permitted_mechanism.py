"""The panic recorder must be configured in a way a locked-down kernel permits.

WHY THIS FILE EXISTS.

Row 45 asks for a panic record the next boot can read. The EFI backend that the
shipped modprobe.d option enables was proven necessary and not sufficient: a
deliberate panic with it registered wrote nothing, and why the write did not
land in panic context cannot be determined from userspace on a shipped system.
The record therefore moves to the RAM-backed recorder, which writes to a small
reserved region of memory that survives the warm reboot a panic causes.

The obvious way to configure that recorder is a fixed physical address:
`memmap=` to carve the region out and `ramoops.mem_address=` to point the
recorder at it. Measured on an installed machine on 2026-09-16, that form CANNOT
WORK HERE and fails in the worst way — quietly. Every machine this project
installs runs with Secure Boot on, which puts the kernel in integrity lockdown,
and lockdown refuses module parameters the kernel marks as hardware parameters.
`fs/pstore/ram.c` declares exactly one of those: `mem_address`. The region is
reserved, the boot log says `Lockdown: unsafe module parameters is restricted`,
the recorder never starts, and a machine configured that way would look
configured while recording nothing — the same class of failure row 45 exists to
close.

The kernel ships a mechanism for exactly this: `reserve_mem=SIZE:ALIGN:NAME`
reserves the region and `ramoops.mem_name=NAME` claims it, with no hardware
parameter anywhere, and no fixed address that would have to be free on every
machine.

WHAT THESE TESTS PIN.

1. The shipped command-line fragment configures the recorder by NAME.
2. It uses none of the forms lockdown refuses. This is the real regression gate:
   someone simplifying the configuration back to an address would break panic
   recording on every Secure Boot machine and nothing would say so.
3. The reservation's name and the recorder's name are the same string. Two names
   that drift apart is the other way this goes quiet.
4. The recorder is actually built, the module is actually loaded at boot, and
   both shipped files are declared by their package so a build that failed to
   ship them would not pass verification.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FRAGMENT_DIR = REPO / "config" / "kernel" / "fragments"
BASE_FILES = REPO / "packages" / "core" / "intergenos-base-files"
CMDLINE_D = BASE_FILES / "files" / "etc" / "kernel" / "cmdline.d"
MODULES_LOAD_D = BASE_FILES / "files" / "etc" / "modules-load.d"
RECIPE = BASE_FILES / "package.yml"

# The one parameter fs/pstore/ram.c declares with module_param_hw, and the
# command-line reservation that only makes sense together with it.
REFUSED_FORMS = ("ramoops.mem_address=", "memmap=")


def shipped_cmdline_parameters() -> str:
    """Every parameter the shipped cmdline.d fragments contribute, as one string.

    Mirrors what the linux-kernel post-install hook does when it builds the
    image: strip comments and blank lines, join what is left.
    """
    parts = []
    for conf in sorted(CMDLINE_D.glob("*.conf")):
        for raw in conf.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts.append(line)
    return " ".join(parts)


def resolved_kernel_option(name: str):
    """The value an option ends up with after the fragments are concatenated."""
    value = None
    set_re = re.compile(rf"^{re.escape(name)}=(.+)$")
    unset_re = re.compile(rf"^#\s*{re.escape(name)} is not set\s*$")
    for fragment in sorted(FRAGMENT_DIR.glob("*.config")):
        for line in fragment.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if unset_re.match(line):
                value = None
            else:
                hit = set_re.match(line)
                if hit:
                    value = hit.group(1).strip()
    return value


def test_the_ram_backed_recorder_is_built_at_all():
    built = resolved_kernel_option("CONFIG_PSTORE_RAM")
    assert built in {"y", "m"}, (
        "CONFIG_PSTORE_RAM is %r: the RAM-backed panic recorder is not built, so "
        "no command line can configure it." % (built,)
    )


def test_the_recorder_is_configured_by_name():
    params = shipped_cmdline_parameters()
    reserve = re.search(r"reserve_mem=(\S+):(\S+):(\S+)", params)
    assert reserve, (
        "no reserve_mem= reservation in the shipped kernel command line; the "
        "RAM-backed recorder has no region to write a panic into: " + params
    )
    claim = re.search(r"ramoops\.mem_name=(\S+)", params)
    assert claim, (
        "the region is reserved but nothing claims it: ramoops.mem_name= is "
        "absent, so the recorder starts with no memory and records nothing."
    )
    assert reserve.group(3) == claim.group(1), (
        "the reservation is named %r and the recorder asks for %r. Two names "
        "that do not match leave the recorder with no region, and the only "
        "symptom is a panic that is never recorded."
        % (reserve.group(3), claim.group(1))
    )


def test_the_backend_is_named_rather_than_left_to_load_order():
    params = shipped_cmdline_parameters()
    assert "pstore.backend=ramoops" in params, (
        "pstore accepts one backend at a time and this tree also ships the EFI "
        "backend's enabling option. Without pstore.backend=ramoops, which one "
        "records a panic is decided by module load order."
    )


def test_none_of_the_refused_forms_are_used():
    """The regression this file mainly exists to stop."""
    params = shipped_cmdline_parameters()
    used = [form for form in REFUSED_FORMS if form in params]
    assert not used, (
        "the shipped kernel command line uses " + ", ".join(used) + ". A machine "
        "with Secure Boot on runs in integrity lockdown, which refuses the "
        "recorder's mem_address parameter (the one hardware parameter "
        "fs/pstore/ram.c declares). The region would be reserved, the recorder "
        "would never start, the boot log would say 'unsafe module parameters is "
        "restricted', and the machine would look configured while recording "
        "nothing. Reserve the region by name with reserve_mem= instead."
    )


def test_the_module_is_loaded_at_boot():
    carriers = []
    for conf in sorted(MODULES_LOAD_D.glob("*.conf")):
        for raw in conf.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line == "ramoops":
                carriers.append(conf)
    assert carriers, (
        "nothing under %s loads the ramoops module. The kernel builds it as a "
        "module and no bus or device announces it, so it is never loaded and the "
        "panic record has nowhere to go." % MODULES_LOAD_D.relative_to(REPO)
    )


def test_both_shipped_files_are_declared_by_their_package():
    declared = RECIPE.read_text(encoding="utf-8")
    for path in sorted(CMDLINE_D.glob("*.conf")) + sorted(MODULES_LOAD_D.glob("*.conf")):
        entry = "/etc/" + str(path.relative_to(BASE_FILES / "files" / "etc"))
        assert entry in declared, (
            f"{entry} is shipped but {RECIPE.relative_to(REPO)} does not list it "
            "under verify_paths, so a build that failed to ship it would pass "
            "verification."
        )


# --- controls: the instrument has to be able to fail -----------------------

def test_the_reader_skips_comments_and_blank_lines():
    """The fragment is mostly explanation; only its parameter line counts."""
    params = shipped_cmdline_parameters()
    assert "#" not in params, params
    assert "reserve_mem=" in params


def test_the_refused_form_would_be_caught_if_it_were_present():
    sample = "root=UUID=x memmap=1M$0x30000000 ramoops.mem_address=0x30000000"
    used = [form for form in REFUSED_FORMS if form in sample]
    assert used == list(REFUSED_FORMS), used


def test_a_mismatched_pair_of_names_would_be_caught():
    sample = "reserve_mem=1M:4096:pstore ramoops.mem_name=something-else"
    reserve = re.search(r"reserve_mem=(\S+):(\S+):(\S+)", sample)
    claim = re.search(r"ramoops\.mem_name=(\S+)", sample)
    assert reserve and claim
    assert reserve.group(3) != claim.group(1)
