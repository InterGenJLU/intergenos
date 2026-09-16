"""Theme installation waits for bootloader setup and preserves later failures."""

import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def hook(tmp_path, configured=False):
    recipe = (REPO / "packages/desktop/intergenos-grub-theme/build.sh").read_text()
    script = recipe[recipe.index("post_install() {"):].replace("/boot", str(tmp_path))
    if configured:
        menu = tmp_path / "efi/EFI/InterGenOS/grub.cfg"
        menu.parent.mkdir(parents=True)
        menu.write_text("previous menu")
    script += '\ngrub-mkconfig() { echo "called:$*"; return 19; }; post_install\n'
    return subprocess.run(["/usr/bin/bash", "-c", script], capture_output=True, text=True)


def test_initial_install_does_not_probe_live_filesystem(tmp_path):
    result = hook(tmp_path)
    assert result.returncode == 0
    assert "awaits bootloader configuration" in result.stdout
    assert "called:" not in result.stdout
    assert not result.stderr


def test_existing_menu_update_failure_is_reported(tmp_path):
    result = hook(tmp_path, configured=True)
    assert result.returncode == 19
    assert f"called:-o {tmp_path}/efi/EFI/InterGenOS/grub.cfg" in result.stdout
