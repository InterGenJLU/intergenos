"""Build styles for igos-build.

Each style encapsulates a common build pattern (autotools, cmake, etc.)
and generates the shell commands for each build phase.
"""

from .base import BuildStyle, BuildPhase
from .autotools import AutotoolsStyle
from .cmake import CMakeStyle
from .meson import MesonStyle
from .make import MakeStyle
from .custom import CustomStyle


# Registry of available build styles
STYLES: dict[str, type[BuildStyle]] = {
    "autotools": AutotoolsStyle,
    "cmake": CMakeStyle,
    "meson": MesonStyle,
    "make": MakeStyle,
    "custom": CustomStyle,
}


def get_style(name: str) -> BuildStyle:
    """Get a build style instance by name.

    Args:
        name: One of: autotools, cmake, meson, make, custom

    Returns:
        A BuildStyle instance.

    Raises:
        ValueError: If the style name is not recognized.
    """
    cls = STYLES.get(name)
    if cls is None:
        raise ValueError(f"unknown build style '{name}' — available: {', '.join(sorted(STYLES))}")
    return cls()
