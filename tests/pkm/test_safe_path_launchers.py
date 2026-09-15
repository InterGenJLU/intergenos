# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Root-capable Python entry points must disable current-directory imports."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_pkm_installed_wrapper_uses_safe_path_mode():
    recipe = (REPO_ROOT / "packages/core/pkm/build.sh").read_text()
    assert 'exec /usr/bin/python3 -P -m pkm "$@"' in recipe


def test_development_image_pkm_wrapper_uses_safe_path_mode():
    image_builder = (REPO_ROOT / "scripts/create-image.sh").read_text()
    assert 'exec /usr/bin/python3 -P -m pkm "$@"' in image_builder


def test_forge_installed_wrapper_uses_safe_path_mode():
    recipe = (REPO_ROOT / "packages/desktop/forge/build.sh").read_text()
    assert 'exec /usr/bin/python3 -P -m installer "$@"' in recipe


def test_chronicle_pretransaction_handler_uses_absolute_safe_interpreter():
    handler = REPO_ROOT / "assets/intergenos-backup/chronicle-pretxn-handler"
    assert handler.read_text().splitlines()[0] == "#!/usr/bin/python3 -P"


def test_nvidia_eula_helper_uses_absolute_safe_interpreter():
    helper = REPO_ROOT / "packages/extra/nvidia/eula-helper/nvidia-eula.py"
    assert helper.read_text().splitlines()[0] == "#!/usr/bin/python3 -P"
