"""pkm hooks — runtime post-install/upgrade/remove hook framework.

Two layered mechanisms for executing work on the live system after a
package operation completes deploy:

1. Content-triggered canonical hooks (primary, ~99% of packages):
   pkm scans the file_list emitted by the package after deploy and fires
   canonical hooks based on path patterns. Zero per-package work for the
   common cases — depmod on /usr/lib/modules/*, ldconfig on
   /usr/lib/*.so*, glib-compile-schemas on /usr/share/glib-2.0/schemas/*,
   apparmor_parser -r on /etc/apparmor.d/*, gtk-update-icon-cache on
   /usr/share/icons/<theme>/, fc-cache on /usr/share/fonts/,
   update-desktop-database on /usr/share/applications/,
   update-mime-database on /usr/share/mime/, update-ca-trust on
   /etc/ca-certificates/ and /usr/share/ca-certificates/.

2. Archive .scripts/ lifecycle hooks (opt-in, bespoke packages):
   Packages requiring custom setup beyond canonical triggers ship
   {pre,post}_{install,upgrade,remove}.sh inside their archive at the
   .scripts/ subdirectory. pkm runs them with bash -e from the staging
   directory + a stripped env containing only the HOOK_ENV_ALLOWLIST
   vars plus the per-hook PKM_PACKAGE_* vars.

Failure semantics split by hook class:

  - Critical canonical hooks (depmod, ldconfig, glib-compile-schemas,
    apparmor_parser, update-ca-trust): failure flags the operation as
    needing rollback. The caller (cmd_install / cmd_upgrade) decides
    whether to invoke the Q1 rollback flow.
  - Cosmetic canonical hooks (icon cache, font cache, mime db, desktop
    db): failure warns and continues; operation still reports success.
  - Archive lifecycle hooks: critical by default. The package author
    can opt into cosmetic semantics by exiting the script with code 2,
    the documented "warn and continue" return.

Env stripping mirrors the H-024 helper-env hygiene in installer.py —
only PATH/HOME/USER/LOGNAME/LANG/LC_*/TERM/TMPDIR/SHELL from the
inherited env plus the per-hook PKM_PACKAGE_* vars survive. LD_PRELOAD
/ *_PROXY / PYTHONPATH never reach hook execution, so a parent process
that controls the environment cannot inject library-load or HTTP-proxy
attacks through the hook surface.
"""

import os
import re
import subprocess
from collections import namedtuple
from pathlib import Path


# Inherited-env allowlist; mirrors installer.HELPER_ENV_ALLOWLIST for forward
# consistency with the helper-execution path.
HOOK_ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "USER", "LOGNAME",
    "LANG", "LC_ALL", "LC_CTYPE", "TERM",
    "TMPDIR", "SHELL",
})


LIFECYCLE_EVENTS = (
    "pre_install", "post_install",
    "pre_upgrade", "post_upgrade",
    "pre_remove", "post_remove",
)


HookResult = namedtuple(
    "HookResult", ["critical_failures", "cosmetic_failures", "messages"]
)


# Canonical hook definitions. Each entry binds:
#   id: short identifier surfaced in result + error messages
#   description: human-readable purpose for status output
#   pattern: regex over file_list entries (relative paths, no leading slash,
#            dirs end in "/"; matches what installer.py's file_list produces)
#   cmd_fn: callable(root, matched_paths) → list[str] | None  (None = skip)
#   critical: True flags failure as install-needs-rollback; False = cosmetic warn
CanonicalHook = namedtuple(
    "CanonicalHook", ["id", "description", "pattern", "cmd_fn", "critical"]
)


def _depmod_cmd(root, matched):
    # Module paths look like usr/lib/modules/<kver>/<...> — extract kver.
    for path in matched:
        parts = path.split("/")
        if len(parts) >= 4 and parts[2] == "modules":
            cmd = ["depmod", "-a"]
            if str(root) != "/":
                cmd += ["-b", str(root)]
            cmd.append(parts[3])
            return cmd
    return None


def _ldconfig_cmd(root, matched):
    if str(root) == "/":
        return ["ldconfig"]
    return ["ldconfig", "-r", str(root)]


def _glib_compile_schemas_cmd(root, matched):
    return ["glib-compile-schemas", str(Path(root) / "usr/share/glib-2.0/schemas")]


def _apparmor_parser_cmd(root, matched):
    profile_paths = [str(Path(root) / p) for p in matched]
    if not profile_paths:
        return None
    return ["apparmor_parser", "-r"] + profile_paths


def _update_ca_trust_cmd(root, matched):
    return ["update-ca-trust"]


def _gtk_update_icon_cache_cmd(root, matched):
    themes = set()
    for path in matched:
        parts = path.split("/")
        if len(parts) >= 4 and parts[0] == "usr" and parts[1] == "share" and parts[2] == "icons":
            themes.add(parts[3])
    if not themes:
        return None
    cmd = ["gtk-update-icon-cache", "-f"]
    for theme in sorted(themes):
        cmd.append(str(Path(root) / "usr/share/icons" / theme))
    return cmd


def _fc_cache_cmd(root, matched):
    return ["fc-cache", "-f"]


def _update_desktop_database_cmd(root, matched):
    return ["update-desktop-database", str(Path(root) / "usr/share/applications")]


def _update_mime_database_cmd(root, matched):
    return ["update-mime-database", str(Path(root) / "usr/share/mime")]


def _systemctl_daemon_reload_cmd(root, matched):
    # daemon-reload is a system-wide operation that re-parses unit
    # definitions; only meaningful when the install target IS the live
    # system. Chroot installs don't have a running systemd to refresh.
    if str(root) != "/":
        return None
    return ["systemctl", "daemon-reload"]


CANONICAL_HOOKS = [
    CanonicalHook(
        id="depmod",
        description="kernel module dependency table",
        pattern=re.compile(r"^usr/lib/modules/[^/]+/"),
        cmd_fn=_depmod_cmd,
        critical=True,
    ),
    CanonicalHook(
        id="ldconfig",
        description="shared library cache",
        pattern=re.compile(r"^(usr/)?lib(64)?/[^/]+\.so(\.|$)"),
        cmd_fn=_ldconfig_cmd,
        critical=True,
    ),
    CanonicalHook(
        id="glib-compile-schemas",
        description="gschema compilation",
        pattern=re.compile(r"^usr/share/glib-2\.0/schemas/.+\.(xml|override)$"),
        cmd_fn=_glib_compile_schemas_cmd,
        critical=True,
    ),
    CanonicalHook(
        id="apparmor-reload",
        description="apparmor profile reload",
        pattern=re.compile(r"^etc/apparmor\.d/[^/]+$"),
        cmd_fn=_apparmor_parser_cmd,
        critical=True,
    ),
    CanonicalHook(
        id="ca-trust",
        description="ca-certificates trust store",
        pattern=re.compile(r"^(etc|usr/share)/ca-certificates/"),
        cmd_fn=_update_ca_trust_cmd,
        critical=True,
    ),
    CanonicalHook(
        id="icon-cache",
        description="gtk icon cache",
        pattern=re.compile(r"^usr/share/icons/[^/]+/.+"),
        cmd_fn=_gtk_update_icon_cache_cmd,
        critical=False,
    ),
    CanonicalHook(
        id="font-cache",
        description="fontconfig cache",
        pattern=re.compile(r"^usr/share/fonts/.+"),
        cmd_fn=_fc_cache_cmd,
        critical=False,
    ),
    CanonicalHook(
        id="desktop-db",
        description="desktop entry database",
        pattern=re.compile(r"^usr/share/applications/.+\.desktop$"),
        cmd_fn=_update_desktop_database_cmd,
        critical=False,
    ),
    CanonicalHook(
        id="mime-db",
        description="mime type database",
        pattern=re.compile(r"^usr/share/mime/.+\.xml$"),
        cmd_fn=_update_mime_database_cmd,
        critical=False,
    ),
    CanonicalHook(
        # Reloads systemd's view of unit definitions when a .service file
        # is installed/updated; orthogonal to the Q5 notify-only policy for
        # actually restarting services (which remains user-driven). Cosmetic
        # class because a stale unit cache surfaces as deferred-effect
        # rather than broken state, and the user-driven restart will see
        # the new definition via Q5's pkm restart-services. Cross-reference
        # peer-review observation at IGOSC 2026-05-19T11:46:59Z.
        id="systemd-daemon-reload",
        description="systemd unit definition reload",
        pattern=re.compile(r"^(usr/lib|etc)/systemd/system/[^/]+\.service$"),
        cmd_fn=_systemctl_daemon_reload_cmd,
        critical=False,
    ),
]


def _build_hook_env(name, version, root, operation):
    env = {k: v for k, v in os.environ.items() if k in HOOK_ENV_ALLOWLIST}
    env.setdefault("PATH", "/usr/sbin:/usr/bin")
    env.setdefault("HOME", "/root")
    env["PKM_PACKAGE_NAME"] = name
    env["PKM_PACKAGE_VERSION"] = version
    env["PKM_PACKAGE_ROOT"] = str(root)
    env["PKM_PACKAGE_OPERATION"] = operation
    return env


def run_canonical_hooks(root, file_list, name, version, operation):
    """Fire canonical hooks based on file_list path patterns.

    Args:
        root: install root (Path or str). "/" for live system; chroot path
            for tests + non-root installs.
        file_list: list of relative paths installed by the package (no
            leading slash; directories end in "/"). This is the same shape
            installer.py builds at deploy time.
        name, version: package identity for error messages + hook env.
        operation: "install" | "upgrade" | "remove" (passed to hook env
            as PKM_PACKAGE_OPERATION).

    Returns:
        HookResult — critical_failures (list of hook ids that flag rollback),
        cosmetic_failures (list of hook ids that warn-and-continue), messages
        (human-readable per-hook status lines for surfacing in install output).
    """
    # Defensive contract assertion: file_list entries must be relative
    # (no leading slash; dirs end in "/"), matching installer.py's
    # os.walk-relpath output. A caller that accidentally passes absolute
    # paths would silently no-match every canonical pattern, masking real
    # hook firings. Fail loud at the boundary instead.
    for p in file_list:
        if p.startswith("/"):
            raise ValueError(
                f"run_canonical_hooks: file_list entries must be relative; "
                f"got absolute path: {p!r}"
            )

    root = Path(root)
    env = _build_hook_env(name, version, root, operation)
    critical_failures = []
    cosmetic_failures = []
    messages = []

    for hook in CANONICAL_HOOKS:
        matched = [p for p in file_list if hook.pattern.search(p)]
        if not matched:
            continue
        cmd = hook.cmd_fn(root, matched)
        if cmd is None:
            continue
        try:
            result = subprocess.run(
                cmd, env=env, capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                messages.append(f"  hook[{hook.id}] OK ({hook.description})")
            else:
                level = "CRITICAL" if hook.critical else "WARN"
                stderr_snip = result.stderr.strip().replace("\n", " ")[:200]
                messages.append(
                    f"  hook[{hook.id}] {level} ({hook.description}): "
                    f"exit {result.returncode}; {stderr_snip}"
                )
                if hook.critical:
                    critical_failures.append(hook.id)
                else:
                    cosmetic_failures.append(hook.id)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            level = "CRITICAL" if hook.critical else "WARN"
            messages.append(
                f"  hook[{hook.id}] {level} ({hook.description}): "
                f"exec failed: {e}"
            )
            if hook.critical:
                critical_failures.append(hook.id)
            else:
                cosmetic_failures.append(hook.id)

    return HookResult(critical_failures, cosmetic_failures, messages)


def run_archive_lifecycle_hook(staging_dir, event, name, version, root):
    """Run a .scripts/<event>.sh from an extracted archive staging dir.

    Args:
        staging_dir: Path (or str) to the extracted-archive staging dir.
            The hook script lives at <staging_dir>/.scripts/<event>.sh.
        event: one of LIFECYCLE_EVENTS.
        name, version, root: passed into hook env.

    Returns:
        HookResult — empty (all-zero) when the hook script is absent,
        which is the silent-skip path for the ~99% of packages that
        do not opt in to bespoke lifecycle hooks.

    Failure semantics: archive lifecycle hooks default to critical.
    A package that wants cosmetic semantics for a specific hook can
    exit with code 2, which is the documented warn-and-continue return.
    Any other non-zero exit flags critical failure.
    """
    if event not in LIFECYCLE_EVENTS:
        raise ValueError(f"unknown lifecycle event: {event}")
    script = Path(staging_dir) / ".scripts" / f"{event}.sh"
    if not script.is_file():
        return HookResult([], [], [])
    if not os.access(str(script), os.X_OK):
        try:
            script.chmod(0o755)
        except OSError:
            return HookResult(
                [event], [],
                [f"  hook[archive/{event}] CRITICAL: {script} not executable + chmod failed"],
            )
    env = _build_hook_env(name, version, root, event)
    try:
        result = subprocess.run(
            ["bash", "-e", str(script)], env=env,
            capture_output=True, text=True, timeout=600,
        )
        stderr_snip = result.stderr.strip().replace("\n", " ")[:200]
        if result.returncode == 0:
            return HookResult([], [], [f"  hook[archive/{event}] OK"])
        elif result.returncode == 2:
            return HookResult(
                [], [event],
                [f"  hook[archive/{event}] WARN (exit 2, cosmetic): {stderr_snip}"],
            )
        else:
            return HookResult(
                [event], [],
                [f"  hook[archive/{event}] CRITICAL: exit {result.returncode}; {stderr_snip}"],
            )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return HookResult(
            [event], [],
            [f"  hook[archive/{event}] CRITICAL: exec failed: {e}"],
        )


def format_hook_summary(*results):
    """Render one or more HookResults as a multi-line summary string.

    Aggregates the per-hook status lines + a closing summary if any
    critical or cosmetic failures occurred. Empty string when all hook
    results have empty messages (typical for packages that match no
    canonical pattern and ship no .scripts/).
    """
    lines = []
    all_critical = []
    all_cosmetic = []
    for r in results:
        lines.extend(r.messages)
        all_critical.extend(r.critical_failures)
        all_cosmetic.extend(r.cosmetic_failures)
    if all_critical:
        lines.append(
            f"  CRITICAL hook failures: {', '.join(all_critical)}. "
            f"Live system state may diverge from package metadata. "
            f"Rollback recommended."
        )
    if all_cosmetic:
        lines.append(
            f"  Cosmetic hook failures (non-blocking): {', '.join(all_cosmetic)}"
        )
    return "\n".join(lines)
