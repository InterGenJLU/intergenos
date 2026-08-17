# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Test factories that build REAL parser dataclasses.

Why this module exists (Decided 2026-07-25). Tests in this lane used to fake a
parsed template with hand-rolled ``types.SimpleNamespace`` objects carrying only
the fields the test happened to need. That is silent-drift by construction: when
``parser.Source`` grew the ``extract`` field and ``builder`` began reading it,
the production object was safe (the dataclass default supplies it on every real
source) but three hand-rolled doubles had no such field and the lane failed with
an ``AttributeError`` that described nothing about the change.

A double that is a REAL dataclass instance cannot drift that way: a new field
arrives with its default already attached, and a new REQUIRED field breaks the
factory loudly in one place instead of scattering attribute errors across the
suite. Use these helpers rather than constructing stand-ins by hand.

Only the shape is faked here; behaviour under test still comes from the code
under test.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "igos-build") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "igos-build"))

_parser = importlib.import_module("igos-build.parser")

Source = _parser.Source
Dependencies = _parser.Dependencies
Package = _parser.Package


def make_source(url: str = "https://example.invalid/src.tar.gz", **kwargs):
    """A real :class:`parser.Source`. Every field not named here keeps the
    dataclass default, which is exactly the property that stops drift."""
    return Source(url=url, **kwargs)


def make_dependencies(**kwargs):
    """A real :class:`parser.Dependencies` (all lists default to empty)."""
    return Dependencies(**kwargs)


def make_package(name: str = "demo", version: str = "1.0", **kwargs):
    """A real :class:`parser.Package`.

    The required fields carry test-shaped defaults so a caller states only what
    its assertion depends on; ``source`` and ``dependencies`` are real
    dataclasses too, never bare namespaces.
    """
    kwargs.setdefault("release", 1)
    kwargs.setdefault("description", "test package")
    kwargs.setdefault("license", "GPL-3.0-or-later")
    kwargs.setdefault("source", [])
    kwargs.setdefault("dependencies", make_dependencies())
    kwargs.setdefault("build_style", "custom")
    return Package(name=name, version=version, **kwargs)


def make_tracker_stub(**attrs):
    """A PackageTracker double that cannot fall behind the class.

    ``PackageTracker`` is a mixin whose host (``BuildExecutor``) is expensive to
    stand up, so these tests exercise it on a namespace carrying only the few
    attributes the mixin reads. Each such double used to bind a HAND-WRITTEN
    LIST of methods onto that namespace, which is the same silent-drift shape
    this module was created to remove one layer down: adding a method to
    PackageTracker that an exercised method calls broke every double at once
    with an ``AttributeError`` naming the new method and explaining nothing.

    Binding the whole class instead means a new method arrives on every double
    the moment it exists. Pass the collaborators the test needs
    (``pkg_db=...``, ``pkg_archives=...``, ``logger=...``); anything named here
    wins over the bound method of the same name, so a test can still stub one
    behaviour deliberately.
    """
    from types import MethodType, SimpleNamespace

    stub = SimpleNamespace()
    _tracker = importlib.import_module("igos-build.tracker").PackageTracker
    for name, fn in vars(_tracker).items():
        if name.startswith("__"):
            continue
        if isinstance(fn, staticmethod):
            setattr(stub, name, fn.__func__)
        elif isinstance(fn, classmethod):
            setattr(stub, name, MethodType(fn.__func__, _tracker))
        elif callable(fn):
            setattr(stub, name, MethodType(fn, stub))
        else:
            setattr(stub, name, fn)
    for key, value in attrs.items():
        setattr(stub, key, value)
    return stub
