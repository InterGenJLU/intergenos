# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The model attribution, in one place, for every surface that shows it.

Tongyi Qianwen License section 4 requires an attribution wherever a
Qwen-family model powers the assistant. Four surfaces render it: the CLI's
`intergen --version`, the web conversation view (which the GTK panel displays
through WebKit, so those two are one surface), the terminal console, and the
first-boot greeter.

The sentence lives HERE and nowhere else. Three surfaces writing their own
wording is how a license statement drifts into three different claims about
the same obligation, each true-ish and none authoritative. The greeter is the
one exception to importing this module, and only because it is a GTK
application rather than part of this package: it asks `intergen --version`
over a process boundary, by the same rule its `_model_offer` already follows,
so it still renders this exact line.

Nothing here is a guess. When the machine cannot be asked what it holds, every
surface renders NOTHING — an attribution is a factual claim about what is
running, and on a Tier-1 box, which serves InternVL3.5-2B, "Powered by Qwen"
would simply be false.
"""

from __future__ import annotations

QWEN_ATTRIBUTION = "Powered by Qwen"

#: The comment the web conversation view ships with, which the server replaces
#: with the rendered line. It lives in the shipped index.html so the injection
#: has a defined place to land instead of being pattern-matched into the page.
HTML_PLACEHOLDER = "<!--INTERGEN_ATTRIBUTION-->"


def qwen_models_present() -> list[str]:
    """Names of the Qwen-family models whose files are on this machine.

    Cheap and read-only by construction: it reads the download manifest and
    the directory entries that manifest names. It loads no model, hashes no
    model file and starts no daemon.

    Any failure yields an empty list, and every caller then renders no
    attribution rather than a guess.
    """
    try:
        from intergen.model_manager import ModelManager
        names: list[str] = []
        for info in ModelManager().list_downloaded():
            name = (getattr(info, "name", "") or "").strip()
            # The paired projector rides the manifest as "<model> (mmproj)".
            # It is the same model for attribution purposes, so it must not
            # appear as a second name on the line.
            base = name.split(" (mmproj)")[0]
            if base.lower().startswith("qwen") and base not in names:
                names.append(base)
        return names
    except Exception:  # noqa: BLE001 — rendering a view must never fail here
        return []


def attribution_line() -> str | None:
    """The one sentence every surface renders, or None when none is owed."""
    present = qwen_models_present()
    if not present:
        return None
    return (f"{QWEN_ATTRIBUTION} — {', '.join(present)}, used under the "
            f"Tongyi Qianwen License.")
