"""A kernel panic has to leave a record the next boot can read.

Two panics have been seen on this project's hardware. The first left a record and
could be explained; the second left nothing and cannot be. The difference is whether
the kernel's EFI pstore backend is enabled on an installed system.

That backend is built here but the kernel's own default switches it off
(CONFIG_EFI_VARS_PSTORE_DEFAULT_DISABLE), so retention depends on the shipped
module-configuration file turning it back on. This gate asserts the END STATE rather
than one implementation of it: after the kernel fragments and the shipped
modprobe.d tree are both taken into account, the backend must come out enabled.

Written this way the gate survives either fix. If the kernel baseline is ever changed
to enable the backend by default, the assertion still holds and the file becomes
belt-and-braces. If someone drops the file while the kernel default is still off — the
exact silent regression that cost the second panic's record — the gate fails and says so.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FRAGMENT_DIR = REPO / "config" / "kernel" / "fragments"
BASE_FILES = REPO / "packages" / "core" / "intergenos-base-files"
MODPROBE_DIR = BASE_FILES / "files" / "etc" / "modprobe.d"
RECIPE = BASE_FILES / "package.yml"

MODULE = "efi_pstore"
PARAM = "pstore_disable"


def resolved_kernel_option(name: str):
    """The value a config option ends up with after the fragments are concatenated.

    build.sh cats the fragments in sorted order into .config, so a later fragment
    overrides an earlier one. Returns the string value, or None if never set.
    """
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


def module_options(text: str, module: str) -> dict:
    """Parse `options <module> k=v ...` lines out of modprobe.d text.

    Later lines win, which is how modprobe itself resolves repeats within a file.
    """
    found = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 3 or parts[0] != "options":
            continue
        # modprobe treats - and _ in a module name as equivalent
        if parts[1].replace("-", "_") != module.replace("-", "_"):
            continue
        for token in parts[2:]:
            if "=" in token:
                key, _, value = token.partition("=")
                found[key] = value
    return found


def shipped_options() -> dict:
    """Every option the shipped modprobe.d tree sets for the backend's module."""
    merged = {}
    for conf in sorted(MODPROBE_DIR.glob("*.conf")):
        merged.update(module_options(conf.read_text(encoding="utf-8"), MODULE))
    return merged


def is_off(value: str) -> bool:
    return value.strip().lower() in {"1", "y", "yes", "true", "on"}


# --- the invariant ---------------------------------------------------------

def test_the_backend_is_built_at_all():
    """Nothing below can hold if the backend was never compiled."""
    built = resolved_kernel_option("CONFIG_EFI_VARS_PSTORE")
    assert built in {"y", "m"}, (
        "CONFIG_EFI_VARS_PSTORE is %r: the EFI pstore backend is not built, so no "
        "module option can switch it on and a panic cannot be recorded." % (built,)
    )


def test_a_panic_record_is_retained_on_an_installed_system():
    """The end state, however it is reached: the backend comes out enabled."""
    default_disabled = resolved_kernel_option("CONFIG_EFI_VARS_PSTORE_DEFAULT_DISABLE") == "y"
    options = shipped_options()
    turned_on = PARAM in options and not is_off(options[PARAM])

    if not default_disabled:
        return  # the kernel default already leaves it on

    assert turned_on, (
        "The kernel default disables the EFI pstore backend "
        "(CONFIG_EFI_VARS_PSTORE_DEFAULT_DISABLE=y) and nothing under "
        f"{MODPROBE_DIR.relative_to(REPO)} sets `options {MODULE} {PARAM}=0`. "
        "An installed system would load the module, decline to register, and record "
        "nothing when it panics — the state that made the 2026-09-16 panic "
        "unexplainable. Ship the option, or change the kernel default and say so."
    )


def test_the_file_that_carries_the_option_is_declared_by_its_package():
    """A payload file the recipe does not declare is not checked after a build."""
    options = shipped_options()
    if PARAM not in options:
        pytest.skip("no modprobe.d file sets the option; the invariant test owns that case")
    carriers = [
        conf for conf in sorted(MODPROBE_DIR.glob("*.conf"))
        if PARAM in module_options(conf.read_text(encoding="utf-8"), MODULE)
    ]
    declared = RECIPE.read_text(encoding="utf-8")
    for conf in carriers:
        entry = f"/etc/modprobe.d/{conf.name}"
        assert entry in declared, (
            f"{entry} sets the panic-record option but {RECIPE.relative_to(REPO)} "
            "does not list it under verify_paths, so a build that failed to ship it "
            "would pass verification."
        )


# --- controls: the instrument has to be able to fail -----------------------

def test_the_parser_sees_the_option_when_it_is_present():
    text = "# comment\noptions efi_pstore pstore_disable=0\n"
    assert module_options(text, MODULE) == {PARAM: "0"}


def test_the_parser_reports_nothing_when_the_option_is_absent():
    text = "# a file about something else\noptions rtw88_pci disable_aspm=y\n"
    assert module_options(text, MODULE) == {}


def test_the_parser_does_not_read_a_commented_out_option():
    text = "# options efi_pstore pstore_disable=0\n"
    assert module_options(text, MODULE) == {}


def test_a_disabling_value_is_recognised_as_off():
    """The true-positive control for the invariant: 1/y/yes/true all mean disabled."""
    for value in ("1", "y", "Y", "yes", "true", "on"):
        assert is_off(value), value
    for value in ("0", "n", "no", "false", "off"):
        assert not is_off(value), value


def test_the_module_name_is_matched_across_the_dash_underscore_spelling():
    text = "options efi-pstore pstore_disable=0\n"
    assert module_options(text, MODULE) == {PARAM: "0"}
