# SPDX-License-Identifier: GPL-3.0-or-later
"""A font installed before fontconfig defers its cache; it never reports done.

WHY THIS FILE EXISTS.

Installing into a target root runs `fc-cache -f --sysroot=<target>`, and
fontconfig reads the TARGET's configuration. Packages install in an order that
puts font packages before fontconfig, so for part of every fresh install the
target has fonts and no /etc/fonts/fonts.conf, and every one of those
invocations failed with a diagnostic — eight of them in the install trace this
came from. Two things were wrong at once. The failures were noise nobody could
act on, because the missing file was not the font package's fault and arrives
later by itself. And the caches those invocations were supposed to build were
never built afterwards: fontconfig's own install fired no font-cache trigger,
because the trigger only watched /usr/share/fonts.

WHAT THESE TESTS PIN.

1. With no target configuration, the hook does not run the tool and does not
   claim success — it returns a deferral carrying a reason, and
   run_canonical_hooks reports that reason. A postponed cache that says so is
   the honest state; the states this rules out are the two that existed before,
   a failure the owner cannot act on and a silence that looks like success.
2. A deferral is NOT a failure. It appears in neither the critical nor the
   cosmetic failure list, so an install is not flagged for rollback and is not
   marked degraded over work that has simply not come due yet.
3. Once the target HAS the configuration, the same builder produces the real
   command, with the root named through fontconfig's own --sysroot option.
4. The trigger selects etc/fonts/fonts.conf, so installing fontconfig rebuilds
   the cache for every font that landed before it. Without this arm the
   deferral above would be a leak rather than a postponement.
5. The live-system command is unchanged, because that is the case every
   installed machine takes.
"""

from __future__ import annotations

from pathlib import Path

from pkm import hooks

FONT_CACHE_HOOK = next(
    hook for hook in hooks.CANONICAL_HOOKS if hook.id == "font-cache"
)
A_FONT = "usr/share/fonts/am4c116/Demo-Regular.ttf"
THE_CONFIG = "etc/fonts/fonts.conf"


def test_a_foreign_root_without_the_configuration_defers_and_says_why(tmp_path):
    root = tmp_path / "target"
    root.mkdir()
    result = FONT_CACHE_HOOK.cmd_fn(str(root), [A_FONT])
    assert isinstance(result, hooks.HookDeferral), (
        "the font cache hook returned something other than a deferral for a "
        f"target that has no {THE_CONFIG}: {result!r}"
    )
    assert result.reason.strip(), "the deferral carries no reason to report"


def test_the_deferral_is_reported_and_is_not_a_failure(tmp_path):
    root = tmp_path / "target"
    root.mkdir()
    result = hooks.run_canonical_hooks(
        str(root), [A_FONT], "am4c116-demo-font", "1.0", "install",
    )
    assert "font-cache" not in result.critical_failures
    assert "font-cache" not in result.cosmetic_failures, (
        "a cache that has not come due yet was counted as a failed hook"
    )
    lines = [m for m in result.messages if "font-cache" in m]
    assert lines, "the postponed font cache was not reported at all"
    assert not any(" OK " in line for line in lines), (
        "the postponed font cache was reported as done: " + "; ".join(lines)
    )
    assert any("PENDING" in line for line in lines), (
        "the report does not say the cache is still owed: " + "; ".join(lines)
    )


def test_the_configuration_arriving_makes_the_real_command(tmp_path):
    root = tmp_path / "target"
    (root / "etc/fonts").mkdir(parents=True)
    (root / THE_CONFIG).write_text("<fontconfig/>\n", encoding="utf-8")
    cmd = FONT_CACHE_HOOK.cmd_fn(str(root), [A_FONT])
    assert isinstance(cmd, list), f"the hook still declined with the target configured: {cmd!r}"
    assert cmd[0] == "/usr/bin/fc-cache"
    joined = " ".join(cmd)
    assert str(root) in joined
    assert "--sysroot" in joined or "-y" in cmd, joined


def test_installing_fontconfig_triggers_the_cache_for_the_fonts_before_it():
    assert FONT_CACHE_HOOK.pattern.search(THE_CONFIG), (
        "installing fontconfig fires no font-cache hook, so every font "
        "installed before it keeps an unbuilt cache with nothing reported"
    )
    assert FONT_CACHE_HOOK.pattern.search(A_FONT), "the font arm of the trigger was lost"
    for unrelated in (
        "etc/fonts/conf.d/10-demo.conf",
        "etc/fonts/fonts.conf.bak",
        "usr/share/doc/fontconfig/fonts.conf",
    ):
        assert not FONT_CACHE_HOOK.pattern.search(unrelated), unrelated


def test_the_live_system_command_is_unchanged():
    assert FONT_CACHE_HOOK.cmd_fn("/", [A_FONT]) == ["/usr/bin/fc-cache", "-f"]
