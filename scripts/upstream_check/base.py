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
