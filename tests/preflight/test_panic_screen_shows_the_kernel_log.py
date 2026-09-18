"""A kernel panic has to leave a readable trace on the screen, recorder or not.

WHY THIS FILE EXISTS.

The sibling file tests/preflight/test_panic_record_is_retained.py covers the
recorder: a panic writes its log into a reserved region of memory and the next
boot reads it back. That path needs three things to be true at once — the
machine's image carries the reservation, the recorder registers, and the machine
boots again far enough to run systemd-pstore. A machine installed before the
recorder shipped has none of them, and a machine whose next boot never happens
has the last one missing.

The panic screen is the path that needs none of that. The kernel draws it out of
the panic handler itself, on hardware that is already lit, and a photograph of it
is a record on any machine, including one that has never been updated.

WHAT THIS TREE SHIPPED BEFORE THIS FILE. The kernel's panic screen has two
formatters. "user" prints three lines — "KERNEL PANIC!", "Please reboot your
computer." and the one-line reason — and nothing that says which code failed.
"kmsg" draws the tail of the kernel log: the panic string, the instruction
pointer, the call trace, the tainted line and the loaded modules. The kernel's
default is "user" (drivers/gpu/drm/Kconfig, DRM_PANIC_SCREEN) and this tree had
never overridden it, so on 2026-09-18 a machine running this system took a
fatal exception in interrupt context and showed a screen that named nothing.

The third formatter, "qr_code", is not a substitute. This tree's kernel fragments
have asked for CONFIG_DRM_PANIC_SCREEN_QR_CODE since 2026-07-08, but that option
depends on RUST, no fragment enables CONFIG_RUST, and the option is absent from
the config of every kernel this project has built. drm_panic.c's own fallback for
a kernel built without it is draw_panic_static_user() — the three-line screen. A
command line that selected qr_code would therefore print less than nothing extra
and say nothing about why.

WHAT THESE TESTS PIN.

1. The screen formatter is built at all.
2. The shipped kernel command line selects the kernel-log formatter, by the one
   mechanism the kernel documents for it (drm.panic_screen=).
3. The command line does not select the QR formatter while the kernel is built
   without it, which would silently fall back to the screen that names nothing.
4. The fragment that carries the parameter is declared by its package, so a
   build that failed to ship it would not pass verification.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FRAGMENT_DIR = REPO / "config" / "kernel" / "fragments"
BASE_FILES = REPO / "packages" / "core" / "intergenos-base-files"
CMDLINE_D = BASE_FILES / "files" / "etc" / "kernel" / "cmdline.d"
RECIPE = BASE_FILES / "package.yml"

PARAMETER = "drm.panic_screen"
REQUIRED_SCREEN = "kmsg"


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


def selected_screen(params: str):
    """The formatter the command line selects, or None if it selects none.

    Pure, so the controls below can feed it a command line that must not
    satisfy the assertions. The kernel's own parser takes the LAST assignment
    of a repeated parameter, so this one does too.
    """
    hits = re.findall(rf"(?:^|\s){re.escape(PARAMETER)}=(\S+)", params)
    return hits[-1] if hits else None


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


# --- the invariant ---------------------------------------------------------

def test_the_panic_screen_is_built_at_all():
    """Nothing below can hold if the kernel draws no panic screen."""
    built = resolved_kernel_option("CONFIG_DRM_PANIC")
    assert built == "y", (
        "CONFIG_DRM_PANIC is %r: the kernel draws no panic screen, so a panic "
        "leaves whatever was already on the display and no trace at all." % (built,)
    )


def test_the_shipped_command_line_asks_for_the_kernel_log_screen():
    params = shipped_cmdline_parameters()
    screen = selected_screen(params)
    assert screen == REQUIRED_SCREEN, (
        f"the shipped kernel command line selects {screen!r} for the panic "
        f"screen, not {REQUIRED_SCREEN!r}. Without it a panic prints "
        "'KERNEL PANIC! Please reboot your computer.' and the one-line reason, "
        "and the trace that names the failing driver is never shown — which is "
        "what a machine running this system printed when it panicked on 2026-09-18. "
        f"The shipped command line reads: {params}"
    )


def test_the_qr_screen_is_not_selected_while_the_kernel_is_built_without_it():
    """The formatter that is asked for and never built.

    CONFIG_DRM_PANIC_SCREEN_QR_CODE depends on RUST. If a command line ever
    selects qr_code on a kernel built without it, drm_panic.c falls back to
    draw_panic_static_user() — the three-line screen — and nothing says so.
    """
    qr_asked = resolved_kernel_option("CONFIG_DRM_PANIC_SCREEN_QR_CODE") == "y"
    rust_built = resolved_kernel_option("CONFIG_RUST") == "y"
    screen = selected_screen(shipped_cmdline_parameters())
    if screen != "qr_code":
        return  # the command line does not depend on that formatter
    assert qr_asked and rust_built, (
        "the shipped command line selects the QR-code panic screen, but the "
        f"kernel this tree builds has CONFIG_DRM_PANIC_SCREEN_QR_CODE asked for "
        f"={qr_asked} and CONFIG_RUST built={rust_built}. That option depends on "
        "RUST; without it the QR formatter is not compiled in and the kernel "
        "falls back to the screen that names nothing."
    )


def test_the_fragment_that_carries_the_parameter_is_declared_by_its_package():
    """A payload file the recipe does not declare is not checked after a build."""
    carriers = [
        conf for conf in sorted(CMDLINE_D.glob("*.conf"))
        if PARAMETER + "=" in conf.read_text(encoding="utf-8")
    ]
    assert carriers, (
        f"no file under {CMDLINE_D.relative_to(REPO)} sets {PARAMETER}=; the "
        "invariant test above owns that case."
    )
    declared = RECIPE.read_text(encoding="utf-8")
    for conf in carriers:
        entry = f"/etc/kernel/cmdline.d/{conf.name}"
        assert entry in declared, (
            f"{entry} selects the panic screen but {RECIPE.relative_to(REPO)} "
            "does not list it under verify_paths, so a build that failed to ship "
            "it would pass verification."
        )


# --- controls: the instrument has to be able to fail -----------------------

def test_the_reader_returns_nothing_when_the_parameter_is_absent():
    assert selected_screen("root=UUID=x quiet reserve_mem=1M:4096:pstore") is None


def test_the_reader_does_not_accept_the_default_screen_as_the_kernel_log_one():
    assert selected_screen("drm.panic_screen=user") == "user"
    assert selected_screen("drm.panic_screen=user") != REQUIRED_SCREEN


def test_the_reader_takes_the_last_assignment_as_the_kernel_does():
    assert selected_screen("drm.panic_screen=kmsg drm.panic_screen=user") == "user"


def test_the_reader_is_not_fooled_by_a_similarly_named_parameter():
    assert selected_screen("nodrm.panic_screen=kmsg") is None


def test_the_fragment_reader_skips_comments_and_blank_lines():
    """The fragments are mostly explanation; only their parameter lines count."""
    params = shipped_cmdline_parameters()
    assert "#" not in params, params
