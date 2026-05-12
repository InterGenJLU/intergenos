"""Project-root pytest configuration: ensure project root is importable.

Several test files load InterGenOS scripts via importlib.util.spec_from_file_location
(because scripts/ has no __init__.py and many script filenames contain hyphens).
Those scripts often import project-internal packages like ``pkm.repo``, which
require the project root to be on ``sys.path``.

When test directories have inconsistent ``__init__.py`` presence (some packages,
some loose test files), pytest's automatic ``sys.path`` insertion can fail to
include the project root reliably during collection. Placing this conftest at
the project root ensures it runs before any test-file import and the project
root is always on sys.path.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Pre-import pkm so that scripts loaded via importlib.util.spec_from_file_location
# (e.g., tests/repo-publish/test_generate_repodb.py loading scripts/generate-repodb.py)
# find pkm.repo in sys.modules even when pytest's collection ordering interferes
# with their module-level `from pkm.repo import ...`. Pre-loading here is cheap
# (~25ms one-time cost) and removes a class of "depends-on-collection-order" failures.
try:
    import pkm.repo  # noqa: F401
except ImportError:
    # If pkm itself can't import (e.g., missing dependency), let the test that
    # actually needs it surface the real error rather than swallowing it here.
    pass
