"""The theme package's own /etc/grub.d script keeps the theme on every regeneration.

WHAT WAS WRONG (measured on an R001.3 install, 2026-10-16). 00_header writes
`set theme=` only when GRUB_THEME names a file it can read at mkconfig time; an
EFI install names the theme by its path on the EFI partition, so 00_header
wrote nothing. The installer appended a runtime-guarded directive to the menu
it generated, and the first later grub-mkconfig on the installed system dropped
it: the menu came back unthemed.

WHAT THIS PROVES. /etc/grub.d/05_intergenos_theme, run as grub-mkconfig runs it
(with GRUB_THEME exported and grub-mkconfig_lib sourced from pkgdatadir; here a
stand-in library whose is_path_readable_by_grub answers as told), writes the
boot-time directive exactly when 00_header cannot — the EFI-partition path, or
a file GRUB cannot read — and nothing when 00_header has already set the theme
or no theme was asked for. GRUB's own grub-script-check accepts what it writes.
The recipe installs it executable and lists it among the paths pkm verifies.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "packages/desktop/intergenos-grub-theme"
SNIPPET = PKG / "assets/etc-grub.d/05_intergenos_theme"
ESP_THEME = "/EFI/InterGenOS/themes/intergenos/theme.txt"
STUB_LIB = 'is_path_readable_by_grub() { [ "${STUB_READABLE:-0}" = 1 ]; }\n'


def run(tmp_path, theme, readable=False):
    lib = tmp_path / "lib"
    lib.mkdir(exist_ok=True)
    (lib / "grub-mkconfig_lib").write_text(STUB_LIB)
    env = {"PATH": "/usr/bin:/bin", "pkgdatadir": str(lib),
           "STUB_READABLE": "1" if readable else "0"}
    if theme is not None:
        env["GRUB_THEME"] = theme
    r = subprocess.run(["/bin/sh", str(SNIPPET)], env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


def directive_lines(text):
    return [l.strip() for l in text.splitlines()
            if l.strip() and not l.lstrip().startswith("#")]


def test_efi_partition_theme_gets_the_boot_time_directive(tmp_path):
    out = run(tmp_path, ESP_THEME)
    assert directive_lines(out) == [
        "insmod gfxmenu",
        f"if [ -e {ESP_THEME} ]; then",
        f"set theme={ESP_THEME}",
        "export theme",
        "fi",
    ]


def test_a_theme_00_header_can_read_is_left_to_00_header(tmp_path):
    theme = tmp_path / "theme.txt"
    theme.write_text("# theme\n")
    assert run(tmp_path, str(theme), readable=True) == ""


def test_a_theme_file_grub_cannot_read_gets_the_directive(tmp_path):
    # An encrypted root: the file exists here, GRUB cannot open it at boot.
    theme = tmp_path / "theme.txt"
    theme.write_text("# theme\n")
    out = run(tmp_path, str(theme), readable=False)
    assert f"set theme={theme}" in out
    assert out.count("set theme=") == 1


@pytest.mark.parametrize("theme", [None, ""])
def test_no_theme_asked_for_writes_nothing(tmp_path, theme):
    assert run(tmp_path, theme) == ""


@pytest.mark.skipif(shutil.which("grub-script-check") is None,
                    reason="grub-script-check not installed")
def test_grub_accepts_the_directive(tmp_path):
    cfg = tmp_path / "part.cfg"
    cfg.write_text(run(tmp_path, ESP_THEME))
    r = subprocess.run(["grub-script-check", str(cfg)],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_recipe_installs_it_executable_and_pkm_verifies_it():
    build = (PKG / "build.sh").read_text()
    assert ('install -m 0755 -v "${assets}/etc-grub.d/05_intergenos_theme" \\\n'
            '        "${DESTDIR}/etc/grub.d/05_intergenos_theme"') in build
    assert "  - /etc/grub.d/05_intergenos_theme\n" in (PKG / "package.yml").read_text()
