"""tests/chronicle pytest configuration.

The Chronicle engine ships under assets/intergenos-backup/ (staged into the
package's generated source tarball), so `import chronicle` does not resolve from
the repo root the way pkm/intergen do. Insert the assets dir onto sys.path here
so every test in this directory imports the real, shipped engine. The
project-root conftest already isolates XDG state into a throwaway dir, so these
tests never touch production state.
"""

import sys
from pathlib import Path

_ASSETS = Path(__file__).resolve().parents[2] / "assets" / "intergenos-backup"
if str(_ASSETS) not in sys.path:
    sys.path.insert(0, str(_ASSETS))
