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
   /etc/pki/anchors/ (the trust source directory p11-kit is built with).

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
  - A canonical hook whose command builder returns a HookDeferral has not
    failed at all: the work is real, is still owed, and cannot be done yet.
    The reason is reported and the operation continues unflagged.
  - A canonical hook that exits zero and writes to stderr keeps its OK line
    and gains one NOTE line per stderr line. NOTE is not a failure level: it
    carries what the hook said in the case where it succeeded, which is the
    case where the words OK, WARN and CRITICAL say nothing about it. NOTE
    output that REPEATS is folded rather than printed again — identical lines
    within one operation, and, when the caller passes a NoteFold ledger, a
    block the same hook already said earlier in the same install session. A
    fold always carries its count and its packages, every distinct line is
    still shown, and the hook's unfiltered stderr still reaches the install
    trace, so the fold is a view over the record and never the record.
  - A CRITICAL canonical hook that was selected and whose builder returned no
    command reports DECLINED with its reason. Nothing failed, so no count
    moves; cosmetic hooks keep their silence in the same case.
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
    # The machine owner's signing-key passphrase, and the only name on this
    # list that carries a secret. The kernel package's post-install hook builds
    # and signs this machine's boot image with a key that is encrypted at rest,
    # so during an install the installer sets this variable for the length of
    # the package phase and the hook signs without asking a person who is in
    # the middle of an install. On an installed machine nobody sets it, the
    # hook finds it absent, and it asks the owner at the console instead —
    # which is the whole point of the change: no unattended signing.
    #
    # It stays a name on a default-deny list rather than an inherited
    # environment. A hook that has no business signing anything still cannot
    # read anything else the driver happens to be holding.
    "IGOS_MOK_PASSPHRASE",
})

# Executable identities are part of the hook contract.  PATH remains in the
# child environment only for reviewed shell bodies that still need it; pkm's
# own argv[0] values never delegate identity selection to that PATH.
BASH = "/usr/bin/bash"
SYSTEMD_SYSUSERS = "/usr/bin/systemd-sysusers"
SYSTEMD_TMPFILES = "/usr/bin/systemd-tmpfiles"
DEPMOD = "/usr/sbin/depmod"
LDCONFIG = "/usr/sbin/ldconfig"
GLIB_COMPILE_SCHEMAS = "/usr/bin/glib-compile-schemas"
APPARMOR_PARSER = "/usr/sbin/apparmor_parser"
UPDATE_CA_TRUST = "/usr/bin/update-ca-trust"
UPDATE_CA_TRUST_PROVIDER = "ca-certificates"
GTK_UPDATE_ICON_CACHE = "/usr/bin/gtk-update-icon-cache"
FC_CACHE = "/usr/bin/fc-cache"
UPDATE_DESKTOP_DATABASE = "/usr/bin/update-desktop-database"
UPDATE_MIME_DATABASE = "/usr/bin/update-mime-database"
SYSTEMCTL = "/usr/bin/systemctl"

LIFECYCLE_EVENTS = (
    "pre_install", "post_install",
    "pre_upgrade", "post_upgrade",
    "pre_remove", "post_remove",
)


HookResult = namedtuple(
    "HookResult", ["critical_failures", "cosmetic_failures", "messages"]
)


# One NOTE block that an install showed once and then folded: which hook said
# it, its exact lines, how many package operations said it, and which packages
# those were. Everything a reader needs to see that a repeat WAS a repeat.
FoldedNote = namedtuple(
    "FoldedNote", ["hook_id", "description", "lines", "count", "packages"]
)


def _collapse_identical_lines(lines):
    """Fold byte-identical lines within ONE hook run, keeping first order.

    Returns [(text, times_said)] in the order each text first appeared.
    """
    counts = {}
    order = []
    for line in lines:
        if line not in counts:
            counts[line] = 0
            order.append(line)
        counts[line] += 1
    return [(text, counts[text]) for text in order]


class NoteFold:
    """The ledger that lets one install session show a repeat once.

    WHY THIS EXISTS, MEASURED RATHER THAN ASSUMED. Carrying a hook's stderr
    into the install output added 184 NOTE lines to the R001.2-03 install this
    machine was built from, and 141 of them were two vendor tools repeating
    themselves: gtk-update-icon-cache printing "Cache file created
    successfully." once for each of 69 packages that ship icons, and
    update-mime-database printing the same eight-line XDG advisory for each of
    9 packages that ship mime data. Sixty-nine identical lines teach a reader
    to skip NOTE output, which ends in the same place as discarding it.

    WHERE THE REPEATS ARE. Not inside one package operation — folding there
    folds 0 of those 184 lines, because no producing run repeats itself. They
    are ACROSS package operations, and one install is one PackageInstaller
    installing 862 packages in one process. So the ledger is held by that
    installer and passed in; it is an argument and never module state, so a
    caller that wants each operation to stand alone simply passes nothing.

    WHAT FOLDING IS ALLOWED TO DO. Collapse, never drop. A block is shown in
    full the first time, in place, in first-occurrence order; every distinct
    line is shown; a repeat is recorded with its count and its packages and
    named in the closing summary; and the hook's unfiltered stderr still goes
    to the install trace in full, so the display is a view over the record and
    never the record itself. A filter that silenced text instead of counting it
    would be the same mechanism that hid eight fontconfig diagnostics inside an
    install that called every one of those hooks OK.
    """

    def __init__(self):
        self._blocks = {}
        self._order = []

    def show(self, hook_id, description, counted_lines, package):
        """Return the (text, times) pairs to display for this block.

        An empty list means every line of the block was already shown for this
        hook earlier in the session; the repeat is recorded, not discarded.
        """
        key = (hook_id, tuple(counted_lines))
        entry = self._blocks.get(key)
        if entry is None:
            self._blocks[key] = {
                "hook_id": hook_id,
                "description": description,
                "lines": [text for text, _ in counted_lines],
                "count": 1,
                "packages": [package],
            }
            self._order.append(key)
            return list(counted_lines)
        entry["count"] += 1
        if package not in entry["packages"]:
            entry["packages"].append(package)
        return []

    def folded(self):
        """The blocks that repeated, in the order they were first shown."""
        return [
            FoldedNote(
                e["hook_id"], e["description"], list(e["lines"]),
                e["count"], list(e["packages"]),
            )
            for e in (self._blocks[k] for k in self._order)
            if e["count"] > 1
        ]

    def folded_line_count(self):
        """How many NOTE lines the fold kept off the display."""
        return sum(len(f.lines) * (f.count - 1) for f in self.folded())


def format_note_fold_summary(note_fold):
    """Say what was folded, so a folded install cannot read as a quiet one.

    Empty string when nothing repeated, so an ordinary install gains no
    wording at all.
    """
    if note_fold is None:
        return ""
    folded = note_fold.folded()
    if not folded:
        return ""
    lines = [
        f"  NOTE output folded in this install: "
        f"{note_fold.folded_line_count()} repeat lines from "
        f"{len(folded)} block(s) already shown. "
        f"The install trace carries every one of them in full."
    ]
    for f in folded:
        shown_for = f.packages[0]
        others = f.packages[1:]
        named = ", ".join(others[:3])
        if len(others) > 3:
            named += f", and {len(others) - 3} more"
        lines.append(
            f"    hook[{f.hook_id}] ({f.description}): {len(f.lines)} line(s), "
            f"shown for {shown_for}, said again in {f.count - 1} more package "
            f"operation(s) ({named}): {f.lines[0]}"
        )
    return "\n".join(lines)


# What a canonical hook's command builder returns when the work is real, is not
# done, and cannot be done YET — as distinct from the two answers that already
# existed. A command means "run this"; None means "this operation is meaningless
# here, say nothing" (a chroot install has no running kernel to load an AppArmor
# profile into). Neither of those fits work that is still owed: running it would
# fail, and saying nothing would leave an install reporting success with the
# target's state unbuilt. run_canonical_hooks reports the reason and counts the
# hook as neither a critical nor a cosmetic failure, because nothing has failed.
HookDeferral = namedtuple("HookDeferral", ["reason"])


# What a command builder returns when the work does not belong on THIS root and
# will not be done later by anything this tree ships — as distinct from the
# postponement above, which says the work is still owed and closes by itself.
# The certificate-trust hook is the case: it declines for a foreign root rather
# than rebuild the running machine's trust store, and nothing afterwards builds
# the target's, so the honest word is one that does not promise it will be. A
# builder with nothing to say may still return None; for a CRITICAL hook the
# decline is reported either way, because a critical hook that was selected and
# ran nothing is the report a person needs most.
HookDecline = namedtuple("HookDecline", ["reason"])


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
        cmd = [DEPMOD, "-a"]
        if str(root) != "/":
            cmd += ["-b", str(root)]
        cmd.append(parts[i + 1])
        return cmd
    return None


def _ldconfig_cmd(root, matched):
    if str(root) == "/":
        return [LDCONFIG]
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
    return [LDCONFIG, "-r", str(root)]


def _glib_compile_schemas_cmd(root, matched):
    return [GLIB_COMPILE_SCHEMAS, str(Path(root) / "usr/share/glib-2.0/schemas")]


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
    return [APPARMOR_PARSER, "-r"] + profile_paths


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
        # target itself. The visible-skip gap this used to leave — a declined
        # hook was a silent `continue` in run_canonical_hooks — is closed by
        # returning the decline with its reason instead of a bare None, so the
        # operation reports that this hook was selected and did not act.
        #
        # DECLINED and not PENDING, deliberately: a postponement says the work
        # closes by itself, and nothing in this tree rebuilds a target's trust
        # store after the install. Saying "pending" would promise a step no
        # component performs, which is the failure this whole class is about.
        return HookDecline(
            "update-ca-trust takes no root argument, so running it here would "
            "rebuild the RUNNING system's trust store and still leave the "
            "target's unbuilt; the target's store is built on the target"
        )
    return [UPDATE_CA_TRUST]


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
    cmd = [GTK_UPDATE_ICON_CACHE, "-f"]
    for theme in ready:
        cmd.append(str(icons_root / theme))
    return cmd


# fontconfig's own configuration file, and the reason the cache build has to be
# able to wait: fc-cache reads the TARGET's configuration, and packages install
# in an order that puts fonts before the package that ships it.
FONTS_CONF_REL = "etc/fonts/fonts.conf"


def _fc_cache_cmd(root, matched):
    if str(root) == "/":
        return [FC_CACHE, "-f"]
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
    #
    # WAIT WHEN THE TARGET HAS NO CONFIGURATION YET. fc-cache with --sysroot
    # reads the target's /etc/fonts/fonts.conf, which fontconfig itself ships.
    # Packages install in an order that puts font packages before fontconfig, so
    # for part of every fresh install the target has fonts and no configuration,
    # and each of those invocations failed with a diagnostic — eight of them in
    # the install trace this was measured from, every one of them before
    # fontconfig. Nothing about that is the font package's fault and nothing
    # about it is actionable: the file arrives later by itself.
    #
    # So the build is postponed and SAID to be postponed. It is not skipped
    # silently, which would leave an install reporting success over a cache that
    # was never made, and it is not reported as a failure, because nothing has
    # failed — the work is simply not due yet. The trigger takes fonts.conf as
    # well, so installing fontconfig rebuilds the cache for every font that
    # landed before it, and the postponement closes instead of leaking.
    if not (Path(root) / FONTS_CONF_REL).is_file():
        return HookDeferral(
            f"the target has no {FONTS_CONF_REL} yet, which fc-cache reads "
            f"through --sysroot; installing fontconfig rebuilds the cache for "
            f"every font installed before it"
        )
    return [FC_CACHE, "-f", "--sysroot=" + str(root)]


def _update_desktop_database_cmd(root, matched):
    return [UPDATE_DESKTOP_DATABASE, str(Path(root) / "usr/share/applications")]


def _update_mime_database_cmd(root, matched):
    return [UPDATE_MIME_DATABASE, str(Path(root) / "usr/share/mime")]


def _systemctl_daemon_reload_cmd(root, matched):
    # daemon-reload is a system-wide operation that re-parses unit
    # definitions; only meaningful when the install target IS the live
    # system. Chroot installs don't have a running systemd to refresh.
    if str(root) != "/":
        return None
    return [SYSTEMCTL, "daemon-reload"]


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
    return [BASH, str(script), "--root", str(root)]


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
    cmd = [SYSTEMD_SYSUSERS]
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
    cmd = [SYSTEMD_TMPFILES]
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
        # THE TRIGGER NAMED A DIRECTORY FAMILY THIS TREE DOES NOT USE. Its
        # previous form, ^(etc|usr/share)/ca-certificates/, is the Debian
        # layout; no recipe here installs anything under either of those two
        # paths, so the hook selected nothing and the trust updater that landed
        # with ca-certificates r3 was never invoked by a package operation. A
        # trigger that selects no path is indistinguishable from a package that
        # shipped no trust anchor — the failure had nothing to report it.
        #
        # What this tree actually configures: p11-kit is built with
        # -D trust_paths=/etc/pki/anchors and ca-certificates emits its trusted
        # roots there in the OpenSSL TRUSTED CERTIFICATE format p11-kit-trust
        # requires. That one directory is the authoritative input, and its
        # anchors/ and blocklist/ subdirectories are how p11-kit organises a
        # trust path, so the trigger takes any descendant of it.
        #
        # It deliberately does NOT take the extracted outputs — the bundle and
        # the hashed certificate directory under /etc/ssl/certs, and the
        # /etc/pki/tls/certs alias. Those are what the updater WRITES; a trigger
        # on them would make the hook respond to its own result. Nor does it
        # take /etc/pki/ca-trust/source/anchors, which ca-certificates ships
        # empty as a future per-certificate drop-in point and which p11-kit is
        # not configured to read: a file landing there changes no trust today,
        # and firing on it would report a regeneration that regenerated nothing.
        id="ca-trust",
        description="ca-certificates trust store",
        pattern=re.compile(r"^etc/pki/anchors/.+"),
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
        # The second arm is what closes the postponement in _fc_cache_cmd: a
        # target that had no fontconfig configuration when its fonts arrived
        # gets its cache built when fontconfig lands, because fontconfig's own
        # install now selects this hook. Without it the deferral would be a
        # leak — a cache nobody ever comes back to build.
        id="font-cache",
        description="fontconfig cache",
        pattern=re.compile(r"^usr/share/fonts/.+"
                           r"|^etc/fonts/fonts\.conf$"),
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


def run_canonical_hooks(root, file_list, name, version, operation, hooks=None,
                        note_fold=None):
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
        note_fold: optional NoteFold ledger shared by every package
            operation of one install session. Given one, a NOTE block this
            hook already said earlier in the session is shown once and
            counted there instead of printed again; without one, each
            operation stands alone and nothing is remembered between calls.
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
        if isinstance(cmd, HookDeferral):
            messages.append(
                f"  hook[{hook.id}] PENDING ({hook.description}): {cmd.reason}"
            )
            continue
        if isinstance(cmd, HookDecline) or cmd is None:
            # A SELECTED HOOK THAT RAN NOTHING IS NOT A SILENT ONE.
            #
            # Until this block existed, a builder returning no command was a
            # bare `continue`: the package installed a file the hook's trigger
            # matched, the hook decided not to act, and the operation's output
            # carried no trace of either. For a cosmetic hook that silence is
            # right — a skipped icon cache is not news. For a CRITICAL hook it
            # is the gap that matters: those hooks are the ones whose absence
            # leaves the target's state diverging from its metadata.
            #
            # Nothing is counted as a failure here, because nothing failed.
            if hook.critical:
                reason = (
                    cmd.reason if isinstance(cmd, HookDecline)
                    else "the hook's command builder produced no command for this root"
                )
                messages.append(
                    f"  hook[{hook.id}] DECLINED ({hook.description}): {reason}"
                )
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
                # WHAT THE HOOK SAID ON ITS WAY TO A ZERO EXIT.
                #
                # Until this block existed, a canonical hook that succeeded
                # produced the word OK and nothing else, and everything it had
                # written to stderr was dropped. A tool that does its work and
                # warns about the state it found was therefore silent in exactly
                # the case that happens. Measured in the R001.2-03 install trace:
                # 102 of 779 canonical hook runs exited zero with something on
                # stderr, and eight of those were fc-cache printing
                # "Cannot load default config file" — the diagnostic that named a
                # real ordering defect, discarded eight times by an install that
                # called every one of them OK.
                #
                # NOTE is a level of its own, deliberately: OK, WARN and CRITICAL
                # keep their exact meanings and their counts, nothing that reads
                # those words changes, and a hook that spoke is still a hook that
                # succeeded — it is counted neither critical nor cosmetic.
                #
                # One line per non-blank stderr line, uncapped, which is what
                # run_archive_lifecycle_hook already does for the other hook
                # path. The volume is measured, not assumed: across that whole
                # install the canonical hooks wrote 8,728 bytes of stderr on
                # zero exits — 184 lines, at most 8 from any single run.
                #
                # REPETITION IS FOLDED, NEVER DROPPED. Two vendor tools
                # produced 141 of the 184 NOTE lines a real install gained,
                # each saying one identical thing once per package. Identical
                # lines from this hook fold here with the number of times it
                # said them; a block this hook already said earlier in the
                # same install session folds through the ledger, which keeps
                # the count and the packages for the closing summary. The
                # unfiltered stderr is already in the install trace, so what
                # is folded is the display and never the record.
                note_lines = [
                    line for line in (result.stderr or "").splitlines()
                    if line.strip()
                ]
                counted = _collapse_identical_lines(note_lines)
                # A HOOK THAT SAID NOTHING HAS NOTHING TO FOLD. Found by
                # running the real icon-cache hook against a real scratch
                # root, where the tool wrote nothing at all: the ledger was
                # being handed the empty block, recorded it as a block, and
                # counted the next silent run as a repeat of it — an install
                # would have claimed a fold that never happened, and the
                # summary raised on a block with no first line. Silence is
                # never an entry.
                if counted and note_fold is not None:
                    counted = note_fold.show(
                        hook.id, hook.description, counted, name,
                    )
                messages.extend(
                    f"  hook[{hook.id}] NOTE ({hook.description}): {text}"
                    + ("" if times == 1
                       else f" [the same line {times} times in this"
                            f" package operation]")
                    for text, times in counted
                )
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
            if isinstance(e, FileNotFoundError) and cmd[0] == UPDATE_CA_TRUST:
                detail = (
                    f"required program {UPDATE_CA_TRUST} is missing; "
                    f"expected provider: {UPDATE_CA_TRUST_PROVIDER}"
                )
            else:
                detail = f"exec failed: {e}"
            messages.append(
                f"  hook[{hook.id}] {level} ({hook.description}): "
                f"{detail}"
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


def _archive_lifecycle_command(script):
    """Return the fixed shell command for a sealed archive hook."""
    return [BASH, "-e", str(script)]


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
        cmd = _archive_lifecycle_command(script)
        if _TRACE_AVAILABLE:
            result = _trace.traced_run(
                cmd, env=env, timeout=600,
                phase="pkm_archive_lifecycle",
                intent=f"archive/{event}", pkg=name,
            )
        else:
            result = subprocess.run(  # trace-coverage: allow — _trace shim unavailable fallback
                cmd, env=env,
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
