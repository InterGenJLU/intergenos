"""Upstream version checkers for vps-source-poller.

Each checker is a callable that takes (url_pattern, current_version, name, pkg_meta)
and returns a list of candidate dicts: [{version, url, source}].
"""

from .base import UpstreamChecker, Candidate
from .types.gnu_ftp import GnuFtpChecker
from .types.github import GitHubChecker
from .types.pypi import PyPIChecker
from .types.gnome import GnomeChecker
from .types.freedesktop import FreedesktopChecker
from .types.cargo import CargoChecker

STRATEGIES = {
    "gnu-ftp": GnuFtpChecker(),
    "github": GitHubChecker(),
    "pypi": PyPIChecker(),
    "gnome": GnomeChecker(),
    "freedesktop": FreedesktopChecker(),
    "cargo": CargoChecker(),
}

def get_checker(strategy):
    return STRATEGIES.get(strategy)
