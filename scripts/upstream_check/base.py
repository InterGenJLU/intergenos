# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Base class for upstream version checkers."""

from dataclasses import dataclass
from abc import ABC, abstractmethod


@dataclass
class Candidate:
    version: str
    url: str
    source: str


class UpstreamChecker(ABC):
    @abstractmethod
    def check(self, url_pattern: str, current_version: str, name: str, pkg_meta: dict) -> list[Candidate]:
        ...
