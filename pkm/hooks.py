# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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

# Forensic-trace shim — defensive import.
try:
    from . import _trace
    _TRACE_AVAILABLE = True
except ImportError:
    _trace = None
    _TRACE_AVAILABLE = False


# Inherited-env allowlist for lifecycle-hook execution. Same default-deny base
# as installer.HELPER_ENV_ALLOWLIST, with ONE intentional divergence: it does
# NOT carry that list's SUDO_USER entry. Dropping from root to the invoking
# user is a helper-only need (per-user installs, e.g. a VS Code extension);
# no lifecycle hook drops to the invoking user, so per the demonstrated-need
# rule the hook env stays minimal. Do not blindly re-sync these two sets —
# SUDO_USER belongs only on the helper path.
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
    # Module paths look like [usr/]lib/modules/<kver>/<...> — extract kver.
    #
    # The kernel release component is located by finding "modules" in the path
    # rather than by counting from the left. Counting assumed the usr/ prefix,
    # which holds only because package staging pre-seeds the merged-usr compat
    # symlinks, so a recipe writing to lib/modules/ lands in usr/lib/modules/.
    # That is an invariant maintained in a different part of the system: if it
    # ever stopped holding, this function would read the kernel release out of
    # the wrong position and index a release that does not exist, silently.
    for path in matched:
        parts = path.split("/")
        try:
            i = parts.index("modules")
        except ValueError:
            continue
        if i + 1 >= len(parts) or not parts[i + 1]:
            continue
        cmd = ["depmod", "-a"]
        if str(root) != "/":
            cmd += ["-b", str(root)]
        cmd.append(parts[i + 1])
        return cmd
    return None


def _ldconfig_cmd(root, matched):
    if str(root) == "/":
        return ["ldconfig"]
    # Pre-create {root}/etc so ldconfig can write its
    # {root}/etc/ld.so.cache~ temporary cache file. Under alphabetical
    # package install order, early-letter packages (a*) that ship .so
    # files (a52dec, abseil-cpp, accountsservice, acl, alsa-lib) install
    # BEFORE glibc-core extracts and creates /etc/ on the target. Without
    # this pre-create, ldconfig fails with "Can't create temporary cache
    # file /etc/ld.so.cache~: No such file or directory" and the package
    # install is marked CRITICAL-failed. Surfaced 2026-05-26 install #11
    # — 5 of 6 reported failures hit this. exist_ok=True keeps the call
    # idempotent for the common case where /etc/ already exists.
    os.makedirs(Path(root) / "etc", exist_ok=True)
    return ["ldconfig", "-r", str(root)]


def _glib_compile_schemas_cmd(root, matched):
    return ["glib-compile-schemas", str(Path(root) / "usr/share/glib-2.0/schemas")]


# The live kernel's AppArmor LSM interface. Module-level so tests can patch
# presence/absence instead of inheriting the test host's kernel config.
_APPARMOR_IFACE = Path("/sys/kernel/security/apparmor")


def _apparmor_parser_cmd(root, matched):
    # apparmor_parser -r loads profiles into the RUNNING kernel's apparmor
    # LSM. For a chroot install (target != live system), the parser would
    # load profiles meant for the target's binary paths (/usr/bin/foo)
    # into the LIVE kernel, where they'd either mis-attach, collide with
    # already-loaded profiles, or fail with "ERROR processing regexes"
    # on `#include` resolution against the wrong abstractions/abi paths.
    # Result on 2026-05-26 install #17: install_pipeline reported
    # "apparmor CRITICAL post-install hook failures" on profiles that
    # were structurally fine — pure chroot-context surface, NOT a real
    # profile defect. Skip in chroot install context — the target's
    # apparmor.service unit (enabled via WantedBy=multi-user.target)
    # will load every /etc/apparmor.d/* profile at the target's first
    # boot, in the correct kernel context. Matches the
    # _systemctl_daemon_reload_cmd pattern above.
    if str(root) != "/":
        return None
    # A live root WITHOUT an AppArmor LSM interface — a build chroot on a
    # host kernel where securityfs/apparmor is absent or unmounted, or a
    # kernel with AppArmor disabled — has nothing to load profiles into,
    # and bind-mounting securityfs to force it would load THIS root's
    # profiles into the outer kernel (the exact mis-attach hazard the
    # chroot guard above exists to prevent). apparmor.service loads every
    # /etc/apparmor.d/* profile at the next boot in the correct kernel
    # context. An interface that IS present with a failing parser stays
    # CRITICAL — this skip names an impossible operation, never a failed
    # one. (Origin 2026-07-30: an in-chroot redeploy marked apparmor
    # DEGRADED in the build database — false factory metadata on a
    # correct image.)
    if not _APPARMOR_IFACE.exists():
        return None
    profile_paths = [str(Path(root) / p) for p in matched]
    if not profile_paths:
        return None
    return ["apparmor_parser", "-r"] + profile_paths


def _update_ca_trust_cmd(root, matched):
    if str(root) != "/":
        # DECLINED for a foreign root, rather than run rootless.
        #
        # `update-ca-trust` takes no root argument that this machine can be
        # asked about — the tool is not present here, and the framework's own
        # rule is that a recipe's assumption about a tool is verified against
        # the actual tool, never against memory or another distribution's
        # manual page. What IS certain is that running it bare while installing
        # into another root rebuilds the RUNNING system's trust store: a write
        # outside the install root, touching the one store where a wrong write
        # matters most, and it would still leave the target's store unbuilt.
        #
        # Declining leaves the target's trust store to be built where that can
        # be done correctly — on a machine that has the tool, which is the
        # target itself once it boots. The visible-skip gap this leaves (a
        # declined hook is currently a silent `continue` in run_canonical_hooks)
        # is real and is reported with this change rather than papered over.
        return None
    return ["update-ca-trust"]


def _gtk_update_icon_cache_cmd(root, matched):
    themes = set()
    for path in matched:
        parts = path.split("/")
        if len(parts) >= 4 and parts[0] == "usr" and parts[1] == "share" and parts[2] == "icons":
            themes.add(parts[3])
    if not themes:
        return None
    # gtk-update-icon-cache REQUIRES an index.theme in the theme dir; on a dir
    # without one it exits non-zero with "No theme index file" (PI-13). During a
    # fresh install, packages that ship icons under e.g. hicolor/ are processed
    # before hicolor-icon-theme lands its index.theme, so this trigger fired a
    # flood of expected failures into the install trace — noise that could mask
    # a genuine cache failure. Skip theme dirs with no index.theme
    # yet: they get cached when the owning theme package (which ships
    # index.theme) is installed, and by the install's final icon-cache pass.
    icons_root = Path(root) / "usr/share/icons"
    ready = sorted(t for t in themes if (icons_root / t / "index.theme").exists())
    if not ready:
        return None
    cmd = ["gtk-update-icon-cache", "-f"]
    for theme in ready:
        cmd.append(str(icons_root / theme))
    return cmd


def _fc_cache_cmd(root, matched):
    if str(root) == "/":
        return ["fc-cache", "-f"]
    # Scan the TARGET's font directories, not this machine's.
    #
    # Measured, not assumed: `pkm --root <dir> install font-alias` from the
    # mirror printed `hook[font-cache] OK` while the command it ran was
    # `fc-cache -f` — rebuilding the cache of the machine running pkm, writing
    # outside the install root, and leaving the target's cache unbuilt.
    #
    # The option is fontconfig's own: `-y, --sysroot=SYSROOT  prepend SYSROOT
    # to all paths for scanning`, read from `fc-cache --help` on fontconfig
    # 2.17.1 rather than from memory.
    return ["fc-cache", "-f", "--sysroot=" + str(root)]


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


# Account-database skeleton, shipped by intergenos-base-files as reference
# data under /usr/share (never as /etc payload — decided 2026-07-24) plus the
# create-only helper that is the single sanctioned path from there to /etc.
ACCOUNT_SKEL_REL = "usr/share/intergenos-base-files/account-skel"
ACCOUNT_SEED_SCRIPT_REL = "usr/lib/intergenos/seed-account-skel.sh"


def _account_skel_seed_cmd(root, matched):
    # Seed <root>/etc/{passwd,group,shadow,gshadow} from the shipped skeleton
    # BEFORE the first systemd-sysusers run on a root that has none.
    #
    # Why this has to happen here. systemd-sysusers creates the databases if
    # they are absent, populated with the sysusers.d-declared entries and
    # nothing else. The skeleton's baseline accounts — bin, sys, daemon and
    # the rest of the historical low-uid set — are declared by no sysusers.d
    # fragment, so a root whose databases were first written by sysusers is
    # permanently missing them. Measured consequences on a fresh install:
    # openssh's post_install refuses with "invalid group 'sys'", the man-db
    # tmpfiles entry exits 65 on every boot, and `pkm verify man-db` reports
    # DEGRADED.
    #
    # Any seed downstream of this point is inert. The skeleton is create-only
    # by contract (it never rewrites a database that exists), so once sysusers
    # has created them a seed can only report what it found — which is exactly
    # how the installer's config-phase call, four phases past this hook, came
    # to be a permanent no-op. Ordering, not the presence of a caller, is the
    # load-bearing property; the config-phase call stays as an idempotent belt
    # for a target that no package install ever touched.
    #
    # Fires only when there is something to do and something to do it with:
    # an absent <root>/etc/passwd plus both the skeleton and the helper under
    # <root>. Anything else returns None and the sysusers hook proceeds as
    # before. Failure IS loud — the hook is critical, and the helper itself
    # exits non-zero on a skeleton it cannot read or a database it cannot
    # write.
    root = Path(root)
    if (root / "etc" / "passwd").exists():
        return None
    skel = root / ACCOUNT_SKEL_REL
    script = root / ACCOUNT_SEED_SCRIPT_REL
    if not skel.is_dir() or not script.is_file():
        return None
    return ["/bin/bash", str(script), "--root", str(root)]


def _systemd_sysusers_cmd(root, matched):
    # Process freshly-installed /usr/lib/sysusers.d/*.conf entries so the
    # declared system users/groups exist on <root> before any subsequent
    # operation (archive lifecycle post_install chown, tmpfiles --create
    # for user-owned paths, etc.) needs to resolve them. Mirrors Arch
    # Linux's systemd-sysusers.hook pacman mechanism. Runs in BOTH
    # live-system context (root == "/") AND chroot install context
    # (root != "/"): unlike daemon-reload or apparmor which need the
    # running kernel, sysusers just writes /etc/{passwd,group,shadow,
    # gshadow} at <root> and is safe in either context. Targets ONLY
    # the freshly-installed sysusers.d files (positional args), not all
    # of /usr/lib/sysusers.d, so we don't re-process unrelated entries
    # on every package install.
    files = [str(Path(root) / p) for p in matched if p.endswith(".conf")]
    if not files:
        return None
    cmd = ["systemd-sysusers"]
    if str(root) != "/":
        cmd += ["--root", str(root)]
    cmd += files
    return cmd


def _systemd_tmpfiles_cmd(root, matched):
    # Process freshly-installed /usr/lib/tmpfiles.d/*.conf entries so
    # runtime directories (e.g. /var/lib/<pkg>, /run/<pkg>) exist with
    # correct ownership before the archive lifecycle post_install hook
    # tries to write to them or chown them. Mirrors Arch's
    # systemd-tmpfiles.hook pacman mechanism. Targets ONLY the
    # freshly-installed tmpfiles.d files (positional args). Safe in
    # chroot context via --root (same model as sysusers above).
    files = [str(Path(root) / p) for p in matched if p.endswith(".conf")]
    if not files:
        return None
    cmd = ["systemd-tmpfiles"]
    if str(root) != "/":
        cmd += ["--root", str(root)]
    cmd += ["--create"] + files
    return cmd


# Pre-archive-lifecycle canonical hooks. Fired BEFORE the archive's
# .scripts/post_install.sh runs, so per-package post_install code can
# assume the package's declared system users + runtime dirs already
# exist on <root>. Each entry must be safe to run in both live-system
# and chroot-install contexts (no daemon-reload, no apparmor_parser-r
# into a foreign kernel, etc.). ORDER IS SIGNIFICANT — the list is
# iterated in sequence and the account-skeleton seed is only effective
# ahead of the sysusers run it precedes.
CANONICAL_HOOKS_PRE = [
    CanonicalHook(
        # MUST stay ahead of the sysusers entry: the seed is create-only, so
        # it has an effect only on a root whose account databases sysusers
        # has not written yet. Same trigger pattern as sysusers so the two
        # always fire as a pair, in this order, on the same install.
        id="account-skel-seed",
        description="baseline account databases from the shipped skeleton",
        pattern=re.compile(r"^usr/lib/sysusers\.d/[^/]+\.conf$"),
        cmd_fn=_account_skel_seed_cmd,
        critical=True,
    ),
    CanonicalHook(
        id="sysusers",
        description="declarative system user/group creation",
        pattern=re.compile(r"^usr/lib/sysusers\.d/[^/]+\.conf$"),
        cmd_fn=_systemd_sysusers_cmd,
        critical=True,
    ),
    CanonicalHook(
        id="tmpfiles",
        description="declarative runtime directory creation",
        pattern=re.compile(r"^usr/lib/tmpfiles\.d/[^/]+\.conf$"),
        cmd_fn=_systemd_tmpfiles_cmd,
        critical=True,
    ),
]


CANONICAL_HOOKS = [
    CanonicalHook(
        # The usr/ prefix is OPTIONAL here for the same reason the library
        # trigger no longer enumerates directories: the kernel's own
        # modules_install writes to lib/modules/, and it reaches usr/lib/modules/
        # only because package staging pre-seeds the merged-usr compat symlinks.
        # A trigger that depends on an invariant maintained elsewhere is a
        # silent-failure surface, and this one is critical — a module dependency
        # table that was never rebuilt fails at the next modprobe, not here.
        id="depmod",
        description="kernel module dependency table",
        pattern=re.compile(r"^(usr/)?lib/modules/[^/]+/"),
        cmd_fn=_depmod_cmd,
        critical=True,
    ),
    CanonicalHook(
        # THE TRIGGER DELIBERATELY DOES NOT ENUMERATE DIRECTORIES. Its previous
        # form, ^(usr/)?lib(64)?/[^/]+\.so(\.|$), named the library directories
        # it knew about, and every directory family added afterwards was invisible
        # to it: measured against the recipes' own declared shipped paths, 90 of
        # 595 shared libraries never selected this hook — the whole 32-bit tree
        # under /usr/lib32 (on the loader path via the drop-in the 32-bit C
        # library ships) and the compute stack under /opt/rocm/lib (on the loader
        # path via the drop-in the HIP runtime ships). Nothing reported this,
        # because a cache that was never rebuilt looks exactly like a cache with
        # nothing to add. Measured consequence, 2026-08-05: a 40-package 32-bit
        # closure installed and the cache file kept the previous day's timestamp,
        # so 26 32-bit libraries stayed unresolvable and the game launcher that
        # needs them refused to start, twice, with no message a user could act on.
        #
        # Which directories the loader actually searches is decided by
        # /etc/ld.so.conf and its drop-ins AT RUN TIME, and a static regex here
        # cannot track that decision without going stale again — which is the
        # defect, not an instance of it. So the trigger matches any shared-library
        # file, and lets ldconfig apply its own configuration to decide what
        # enters the cache. The cost of that choice is bounded and known: an
        # occasional cache rebuild for a library in a package-private directory
        # (a Python extension module, say) that ldconfig will correctly ignore.
        # A redundant rebuild is idempotent and takes a fraction of a second; a
        # rebuild that never happens is silent and breaks every program that
        # needed the library.
        #
        # The second alternative fires when a package DECLARES a new search
        # directory by shipping a loader drop-in, which the old trigger also
        # could not see: the directory becomes searchable only once the cache is
        # rebuilt, and the package that declares it may ship no library itself.
        id="ldconfig",
        description="shared library cache",
        pattern=re.compile(r"[^/]+\.so(\.|$)"
                           r"|^etc/ld\.so\.conf\.d/[^/]+\.conf$"),
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
        # peer-review observation, 2026-05-19T11:46:59Z.
        #
        # THE TRIGGER USED TO NAME ONE UNIT SUFFIX. Every other kind of unit
        # definition was invisible to it, and the tree ships them: a timer
        # (the package manager's own update check), a path unit (the display
        # manager's monitor sync), a socket unit (the container daemon), and
        # target units (the firmware and power-management stacks). Drop-in
        # configuration under <unit>.d/ was invisible for the same reason, and a
        # drop-in changes a unit definition exactly as a unit file does — four
        # recipes ship one. All of these were measured in the recipes, not
        # assumed. The suffix list below is the set systemd defines; a unit
        # whose suffix is not a unit type is not a unit.
        #
        # USER-MANAGER UNITS ARE DELIBERATELY NOT CLAIMED HERE. One package
        # ships usr/lib/systemd/user/. `systemctl daemon-reload` refreshes the
        # SYSTEM manager only; it does not reach any user manager, so matching
        # user units would run a command that cannot do the job and report a
        # hook that ran. A user manager picks the definition up at the user's
        # next login, or when that user reloads their own manager. Naming the
        # gap is honest; firing a no-op at it would not be.
        id="systemd-daemon-reload",
        description="systemd unit definition reload",
        pattern=re.compile(
            r"^(usr/lib|etc)/systemd/system/"
            r"(?:[^/]+\.(?:service|socket|timer|path|mount|automount"
            r"|target|slice|scope|swap|device)"
            r"|[^/]+\.d/[^/]+\.conf)$"),
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


def run_canonical_hooks(root, file_list, name, version, operation, hooks=None):
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
        hooks: which canonical hook list to iterate. Defaults to
            CANONICAL_HOOKS (post-lifecycle infrastructure: ldconfig,
            depmod, icon-cache, etc.). Pass CANONICAL_HOOKS_PRE to fire
            the pre-lifecycle hooks (account-skeleton seed, sysusers,
            tmpfiles) — these must run BEFORE the archive
            .scripts/post_install.sh so per-package lifecycle code finds
            users + dirs already created. Hooks fire in list order.

    Returns:
        HookResult — critical_failures (list of hook ids that flag rollback),
        cosmetic_failures (list of hook ids that warn-and-continue), messages
        (human-readable per-hook status lines for surfacing in install output).
    """
    if hooks is None:
        hooks = CANONICAL_HOOKS
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

    for hook in hooks:
        matched = [p for p in file_list if hook.pattern.search(p)]
        if not matched:
            continue
        cmd = hook.cmd_fn(root, matched)
        if cmd is None:
            continue
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event(
                    "pkm_hook_fire",
                    pkg=name, hook=hook.id,
                    description=hook.description,
                    matched_count=len(matched),
                )
            except Exception:
                pass
        import time as _time
        _hook_start = _time.monotonic()
        try:
            if _TRACE_AVAILABLE:
                result = _trace.traced_run(
                    cmd, env=env, timeout=300,
                    phase="pkm_canonical_hook",
                    intent=hook.description, pkg=name,
                )
            else:
                result = subprocess.run(  # trace-coverage: allow — _trace shim unavailable fallback
                    cmd, env=env, capture_output=True, text=True, timeout=300
                )
            if _TRACE_AVAILABLE:
                try:
                    _trace.trace_event(
                        "pkm_hook_done",
                        pkg=name, hook=hook.id, rc=result.returncode,
                        duration_ms=int((_time.monotonic() - _hook_start) * 1000),
                    )
                except Exception:
                    pass
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


def archive_lifecycle_hook_path(staging_dir, event):
    """Path of the .scripts/<event>.sh in a staging dir, or None if absent.

    The presence test on its own, so a caller can decide whether the
    expensive work that surrounds a hook is worth doing at all. The
    hook-output recorder (pkm/hookrecord.py) walks the whole install tree
    twice around the hook; that is affordable only because this returns
    None for the ~99% of packages that ship no lifecycle hook, and the
    caller skips the walk entirely. Sharing the path construction with
    run_archive_lifecycle_hook is the point: a caller that guessed the
    layout separately could gate on a path the runner does not use.
    """
    if event not in LIFECYCLE_EVENTS:
        raise ValueError(f"unknown lifecycle event: {event}")
    script = Path(staging_dir) / ".scripts" / f"{event}.sh"
    return script if script.is_file() else None


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
    script = archive_lifecycle_hook_path(staging_dir, event)
    if script is None:
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
    if _TRACE_AVAILABLE:
        try:
            _trace.trace_event(
                "pkm_hook_fire",
                pkg=name, hook=f"archive/{event}",
                script_path=str(script),
            )
        except Exception:
            pass
    import time as _time
    _hook_start = _time.monotonic()
    try:
        if _TRACE_AVAILABLE:
            result = _trace.traced_run(
                ["bash", "-e", str(script)], env=env, timeout=600,
                phase="pkm_archive_lifecycle",
                intent=f"archive/{event}", pkg=name,
            )
        else:
            result = subprocess.run(  # trace-coverage: allow — _trace shim unavailable fallback
                ["bash", "-e", str(script)], env=env,
                capture_output=True, text=True, timeout=600,
            )
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event(
                    "pkm_hook_done",
                    pkg=name, hook=f"archive/{event}",
                    rc=result.returncode,
                    duration_ms=int((_time.monotonic() - _hook_start) * 1000),
                )
            except Exception:
                pass
        # WHAT THE HOOK SAID REACHES THE PERSON RUNNING pkm.
        #
        # A lifecycle hook is the only part of a package that speaks at install
        # time, and these messages are what the caller hands to its reporter.
        # Until this block existed, everything a hook printed was discarded
        # whenever it exited 0 — the result carried the word OK and nothing
        # else — so a hook that reported what it had decided about a machine
        # was silent in precisely the case that happens. Both streams are
        # carried because a hook that warns and still exits 0 puts that warning
        # on stderr, and a warning nobody sees is the same defect wearing a
        # different stream. A hook that prints nothing still yields exactly the
        # one OK line it always did, so the ~99% of packages with no lifecycle
        # hook, and the quiet ones that have one, read unchanged.
        said = [
            f"  hook[archive/{event}]: {line}"
            for line in result.stdout.splitlines() if line.strip()
        ] + [
            f"  hook[archive/{event}] stderr: {line}"
            for line in result.stderr.splitlines() if line.strip()
        ]
        # The status line below keeps one line readable by shortening a long
        # stderr, and it SAYS when it has done so. A cap that leaves no mark
        # turns "there was more" into "that was all", which is the harder error
        # to notice: the reader has no reason to go looking for the rest.
        _flat = result.stderr.strip().replace("\n", " ")
        stderr_snip = _flat[:200]
        if len(_flat) > 200:
            stderr_snip += f" […truncated, {len(_flat)} chars total]"
        if result.returncode == 0:
            return HookResult([], [], said + [f"  hook[archive/{event}] OK"])
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
