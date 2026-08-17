#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# intergen 0.1.0 — InterGen AI assistant for InterGenOS
# https://github.com/InterGenJLU/intergenos
#
# Installs: Python modules, systemd service, D-Bus activation,
# default config, CLI wrapper, Forge integration hook.
#
# Python dependencies (numpy, sentence-transformers, huggingface-hub,
# torch-cpu) are NOT installed at build time. The InterGenOS chroot is
# intentionally offline during the build (security-by-design: no untrusted
# network access during build). The design already supports user-side
# setup via `intergen setup` (post_install message line below); numpy
# etc. are installed at first-run, not build-time.
#
# Halt #22 (2026-05-08): the prior pip install in build() failed because
# the chroot has no resolv.conf — by design. Moved deps to user-side.

build() {
    set -e
    # No build-time work: package contents are pure Python source +
    # systemd/dbus units, copied verbatim by do_install(). Python deps
    # are installed by user at first run via `intergen setup`.
    :
}

do_install() {
    set -e
    # InterGen Python package
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages/intergen"
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/interfaces"
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/tools"
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/scanner"
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/cloud"
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/tests"
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/tests/scenario"
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/tests/scenario/seeds"
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/console"
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/panel"
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/web"

    # Source lives at the top-level /mnt/intergenos/intergen/ (virtiofs-shared
    # from host). package.yml has source: [] so no extraction happens —
    # use absolute paths for the cp.
    cp -a /mnt/intergenos/intergen/*.py "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/"
    cp -a /mnt/intergenos/intergen/interfaces/*.py "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/interfaces/"
    cp -a /mnt/intergenos/intergen/tools/*.py "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/tools/"
    # InterGen Sentinel — scanner architecture (Local-Rules, Local-Qwen, cloud
    # routing) + cloud provider adapters. Both are real import packages
    # (intergen.scanner / intergen.cloud) that ToolRegistry + Sentinel init
    # depend on; omitting them left the registry/Sentinel unable to load at
    # runtime. *.py only — __pycache__ is a build artifact, excluded.
    cp -a /mnt/intergenos/intergen/scanner/*.py "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/scanner/"
    cp -a /mnt/intergenos/intergen/cloud/*.py "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/cloud/"
    cp -a /mnt/intergenos/intergen/tests/*.py "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/tests/"
    # judge_calibration/ is DATA the shipped tests import (known_garbage_seeds.json):
    # test_quality_judge.py was uncollectable on installed systems without it
    # (T-3, decided 2026-07-13). The *.py glob above cannot pick up a directory.
    cp -a /mnt/intergenos/intergen/tests/judge_calibration "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/tests/"
    # tests/scenario/ is a real import package (intergen.tests.scenario) that the
    # shipped top-level scenario tests import; the *.py glob above cannot pick up
    # a directory, so it needs its own copy (same class as judge_calibration).
    cp -a /mnt/intergenos/intergen/tests/scenario/*.py "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/tests/scenario/"
    # scenario/seeds/ holds the graded seed scenarios (JSON) that the shipped
    # test_scenario_seeds.py loads via load_scenarios(); the *.py globs cannot
    # pick up a data directory (same class as judge_calibration).
    cp -a /mnt/intergenos/intergen/tests/scenario/seeds/*.json "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/tests/scenario/seeds/"
    cp -a /mnt/intergenos/intergen/console/*.py "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/console/"
    cp -a /mnt/intergenos/intergen/panel/*.py "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/panel/"
    cp -a /mnt/intergenos/intergen/web/*.html /mnt/intergenos/intergen/web/*.css \
          /mnt/intergenos/intergen/web/*.js /mnt/intergenos/intergen/web/*.json \
          /mnt/intergenos/intergen/web/*.svg \
          "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/web/"

    # GNOME Shell extension
    install -dm755 "${DESTDIR}/usr/share/gnome-shell/extensions/intergen@intergenos.org"
    cp -a /mnt/intergenos/intergen/panel/extension/* \
          "${DESTDIR}/usr/share/gnome-shell/extensions/intergen@intergenos.org/"

    # Normalize ownership to root:root. The cp -a calls above preserve attributes
    # from the virtiofs-shared source tree /mnt/intergenos/intergen/, which is
    # owned by the unprivileged build user (uid 1000) — so the staged .py + web +
    # extension files carry uid 1000 into the package. The squashfs-assembly guard
    # catches and normalizes this, but fix it at the source so the package is
    # correct on its own and the guard stays a backstop rather than a load-bearing
    # step. cp -a is kept for mode/timestamp/symlink fidelity; only ownership is
    # reset here.
    chown -R root:root "${DESTDIR}/usr/lib/python3.14/site-packages/intergen"
    chown -R root:root "${DESTDIR}/usr/share/gnome-shell/extensions/intergen@intergenos.org"

    # ── Package-data staging + fail-closed completeness gate (Ruling 3, 2026-07-07) ─
    # PACKAGE-DATA files are read at runtime via Path(__file__)/data/<f> and MUST land
    # in site-packages/intergen/data/. The enumerate-each-file install pattern below
    # silently DROPPED capability-surface.json + readonly-state-map.json — the M4
    # capability-claim + read-only-state gate ground truth (added to the tree at r21)
    # — so both gates silently no-op'd on the installed box (the drift dates to the
    # r15/r21 data-only adds). The enumerate-each-file pattern IS the defect class;
    # this stages the package-data files, then a fail-closed gate asserts EVERY
    # intergen/data/ file is accounted for (package-data, system-staged, or explicitly
    # excluded), halting do_install if a new data/ file is added without being routed.
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/data"
    for _pkgdata in capability-surface.json readonly-state-map.json; do
        install -Dm644 "/mnt/intergenos/intergen/data/${_pkgdata}" \
            "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/data/${_pkgdata}"
    done
    chown -R root:root "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/data"

    # Completeness inventory — classify every file under intergen/data/:
    #   PACKAGE_DATA: read via Path(__file__)/data/ — staged to site-packages/data/.
    #   SYSTEM: routed to a /usr/share|/etc|... path by an install line below.
    #   EXCLUDED (with why): in the source tree but deliberately not packaged.
    _data_src="/mnt/intergenos/intergen/data"
    _pkg_data="capability-surface.json readonly-state-map.json"
    _sys_data="org.intergenos.intergen.policy intergen-privileged-runner \
        intergen-model-setup-runner intergen-tool-dispatch.logrotate \
        models-manifest.json models-manifest.json.asc internvl-tool-template.jinja \
        destructive-policy-manifest.json destructive-policy-manifest.json.asc \
        voice/fillers.json org.intergenos.InterGenPanel.svg \
        org.intergenos.InterGenPanel.desktop 70-intergen-compute-gpu-pm.rules"
    # EXCLUDED: intergen.service + com.intergenos.InterGen.service are REFERENCE copies
    # of the units the recipe emits verbatim via heredoc to their real locations (the
    # systemd user unit + the D-Bus service above) — the tree copies document shipped
    # content, they are not themselves packaged. latency-matrix.json is read by NO
    # runtime module (grep-verified) — a dev/analysis asset, not a runtime dependency.
    _excl_data="intergen.service com.intergenos.InterGen.service latency-matrix.json"
    _data_all="$(cd "${_data_src}" && find . -type f | sed 's|^\./||')"
    _data_miss=""
    for _rel in ${_data_all}; do
        case " ${_pkg_data} ${_sys_data} ${_excl_data} " in
            *" ${_rel} "*) continue ;;
        esac
        # The howto corpus is loop-installed to the system dir (every domain .json).
        case "${_rel}" in howto/*.json) continue ;; esac
        _data_miss="${_data_miss} ${_rel}"
    done
    if [ -n "${_data_miss}" ]; then
        echo "FATAL(intergen do_install): unaccounted intergen/data/ file(s):${_data_miss}" >&2
        echo "  Route each to site-packages/data/ (package-data read via Path(__file__)/data/)," >&2
        echo "  to a system path, or add to the EXCLUDED inventory with a reason. (Ruling 3.)" >&2
        exit 1
    fi
    # Assert the M4 gate ground-truth actually landed (verify_paths backstops this at
    # squashfs; failing here catches a staging miss in do_install, not phases later).
    for _gt in capability-surface.json readonly-state-map.json; do
        test -s "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/data/${_gt}" \
            || { echo "FATAL(intergen do_install): M4 ground-truth ${_gt} not staged" >&2; exit 1; }
    done

    # Compile the extension's keybinding settings schema. extension.js
    # enable() calls this.getSettings() + Main.wm.addKeybinding(
    # "toggle-intergen", ...) for the Super+I shortcut. On GNOME 49 a
    # Gio.Settings construction against a non-existent schema is a hard
    # g_error that ABORTS the GJS process (uncatchable), so without a
    # compiled gschemas.compiled in the extension's schemas/ dir the
    # extension cannot enable at all — no top-bar icon, no Super+I. The
    # schema source ships via the cp -a above; compile it here. (Contrast
    # pkm-notifier, which declares settings-schema but ships no schema and
    # never calls getSettings(), so it needs no compile step.)
    glib-compile-schemas \
        "${DESTDIR}/usr/share/gnome-shell/extensions/intergen@intergenos.org/schemas"

    # CLI wrapper
    install -Dm755 /dev/stdin "${DESTDIR}/usr/bin/intergen" << 'WRAPPER'
#!/usr/bin/env python3
"""InterGen CLI — command-line interface to the InterGen AI assistant."""
from intergen.cli import main
main()
WRAPPER

    # Panel entry point
    install -Dm755 /dev/stdin "${DESTDIR}/usr/bin/intergen-panel" << 'PANELWRAPPER'
#!/usr/bin/env python3
"""InterGen Panel — GTK4 WebKitGTK desktop window."""
from intergen.panel import main
main()
PANELWRAPPER

    # Default configuration
    install -Dm644 /dev/stdin "${DESTDIR}/etc/intergen/config.yml" << 'CONFIG'
# InterGen AI Assistant — Configuration
# User overrides: ~/.config/intergen/config.yml

llm:
  endpoint: "http://127.0.0.1:8080/v1/chat/completions"
  temperature: 0.6
  top_p: 0.8
  top_k: 20
  max_tokens: 4096
  tool_calling: true
  presence_penalty: 1.5
  context_size: 16384

escalation:
  mode: "ask"

models:
  path: "/var/lib/intergen/models"
  embedding_model: "nomic-ai/nomic-embed-text-v1.5"
  embedding_device: "cpu"

llama_server:
  port: 8080
  # "auto" runs the EARN-OFFLOAD bring-up gate: the GPU must PROVE coherent
  # output (short + ~1k-token long leg) AND >=1.5x CPU throughput before offload
  # is granted, else it serves on CPU. This closes the silent-salad class (Intel
  # ANV Vulkan-F16: layers offload but output is token-salad, zero errors logged).
  # Replace with an integer to OVERRIDE the gate (user control is supreme):
  #   gpu_layers: 999  -> force full offload unconditionally
  #   gpu_layers: 0    -> pin CPU
  gpu_layers: "auto"
  jinja: true
  parallel: 1
  reasoning: "off"

logging:
  level: "INFO"
  file: "/var/log/intergen/intergen.log"
  event_log: "/var/log/intergen/events.jsonl"
  mcp_audit: "/var/log/intergen/mcp-audit.log"
  max_file_size_mb: 50
  backup_count: 5

memory:
  # Memory is per-user state. Left unset so InterGen resolves a per-user
  # XDG path ($XDG_DATA_HOME/intergen/memory.db, default
  # ~/.local/share/intergen/memory.db). The hardened user service
  # (ProtectSystem=strict) cannot write the old /var default, and a shared
  # /var memory.db would leak one user's stored facts to another. To pin a
  # custom location, set db_path here or in ~/.config/intergen/config.yml.
  db_path: null

security:
  mcp_config: "/etc/intergen/mcp.yml"
  mcp_permissions: "/etc/intergen/mcp.d"
  schema_pins: "/var/lib/intergen/mcp-pins"
CONFIG

    # Systemd user service. Closes audit F-038 (HG) "intergen.service no
    # hardening" per matrix row 211 + line 1309: previously this unit
    # shipped strictly less hardened than nginx + the audit's canonical
    # example of "every InterGenOS-authored service VIOLATES" the ratified
    # 2026-04-29 4-0 unanimous AppArmor + daemon-hardening baseline.
    #
    # The directives below are the systemd canonical hardening set for a
    # user service that needs network access (LLM API + MCP transport) +
    # XDG home-dir writes (config + state caches). Read-only system trees
    # via ProtectSystem=strict; no privilege escalation via
    # NoNewPrivileges; private /tmp + /dev to isolate from peer-process
    # state; kernel-tuning + namespace + realtime + SUID surfaces all
    # restricted; address-family restriction to UNIX + INET + INET6 only
    # (NETLINK + PACKET + others denied — intergen does not enumerate
    # network interfaces or capture raw frames). SystemCallFilter pares
    # to the @system-service umbrella with explicit denials of
    # @privileged (capability-changing) + @resources (mlock-class
    # resource-exhaustion). CapabilityBoundingSet + AmbientCapabilities
    # cleared — a user service needs no Linux capabilities.
    #
    # MemoryDenyWriteExecute= is intentionally NOT set: torch + ggml +
    # sentence-transformers use JIT regions that the directive would
    # break. Re-evaluate when llama.cpp-only CPU inference is the v1.0
    # ship target.
    #
    # ProtectHome= is intentionally NOT set: this is a USER unit; the
    # user's own daemon legitimately needs read/write access to its own
    # ${HOME}/.config/intergen + ${HOME}/.local/share/intergen per the
    # AppArmor profile coverage. ProtectHome=read-only would block the
    # user's daemon from writing to its own home.
    #
    # SCOPING — two complementary session-class guards (panic-remediation
    # wave, 2026-07-08). Forge globally enables intergen.service on the
    # D-010 YES path (installer/backend/install.py ~line 832,
    # `systemctl --global enable`), which writes the wants/ symlink into
    # EVERY systemd-user manager including transient ones — GDM's
    # DynamicUser greeter sessions get a user@<dynamic-UID>.service slice
    # (UIDs allocated ~60578+), root su gets user@0.service, etc. None of
    # those managers has an opted-in human user for InterGen to serve.
    #
    # ConditionUser=!@system ALONE is insufficient, and was the defect: a
    # greeter DynamicUser (uid ~60584) is NOT in the system range, so it
    # PASSES !@system — the unit started in every greeter manager, and with
    # the restart bound mis-sized (see StartLimitIntervalSec below) it
    # restart-looped for the machine's whole uptime (11,506 cycles / 59h
    # observed 2026-07-08; the on-CPU task in that boot's kernel-fault
    # post-mortem). Firing there also generated the dynamic-UID lookup +
    # session-bus-init journal spam and tripped the SIGSYS coredump cascade
    # on install #29 (2026-05-27) when pollers shelled out under seccomp.
    #
    # Fix = LAYER two guards keyed on session class, never on a username:
    #  - ConditionUser=!@system (declarative, evaluated before any exec):
    #    excludes system-range managers (user@0 root-su and other <1000).
    #  - ExecCondition getent -s files (in [Service] below): excludes any
    #    manager whose uid is not a real provisioned account in the LOCAL
    #    passwd files backend — every DynamicUser/greeter/transient uid.
    #    `-s files` deliberately bypasses nss-systemd, which would otherwise
    #    RESOLVE a DynamicUser while its scope is live and defeat the check.
    #    An ExecCondition exit of 1-254 SKIPS the unit without marking it
    #    failed, so no restart is scheduled and the loop cannot form.
    #    Together the unit runs only for a real, non-system, provisioned
    #    human login — with no hardcoded username in the shipped artifact.
    install -Dm644 /dev/stdin "${DESTDIR}/usr/lib/systemd/user/intergen.service" << 'SERVICE'
[Unit]
Description=InterGen AI Assistant
Documentation=https://github.com/InterGenJLU/intergenos
After=graphical-session.target
ConditionUser=!@system

# Bounded restarts (panic-remediation wave, 2026-07-08). The interval MUST
# exceed StartLimitBurst*RestartSec (5*5 = 25s) so a genuine crash-loop
# accumulates its burst inside the window and trips into a loud failed
# state; the systemd default 10s is SHORTER than 25s, so 5 RestartSec=5
# restarts never fell inside one window, the limit never fired, and the
# unit restart-looped unbounded (the 11,506-cycle greeter loop).
StartLimitIntervalSec=30
StartLimitBurst=5

[Service]
Type=dbus
BusName=com.intergenos.InterGen
# Session-class guard (see the SCOPING comment above the [Unit] section):
# run only for a uid present in the local passwd files backend; SKIP (not
# fail, so no restart is scheduled) for DynamicUser/greeter/transient
# managers. `-s files` bypasses nss-systemd so a live DynamicUser scope
# cannot resolve past it. `$(id -u)` is written literally into the unit
# (the SERVICE heredoc delimiter is quoted) and expanded by sh at runtime.
ExecCondition=/bin/sh -c 'getent -s files passwd "$(id -u)" >/dev/null'
ExecStart=/usr/bin/intergen daemon
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

# Hardening — F-038 closure (T0-4-A Commit 2; matrix row 211 + line 1309).
NoNewPrivileges=yes
PrivateTmp=yes
# PI-Z28 (2026-07-07, Zephyrus): PrivateDevices=yes gives the daemon a private
# /dev of pseudo-devices only, hiding every GPU node from the daemon and its
# llama-server children -> the Vulkan engine cannot see the RTX and silently
# falls back to CPU (33 layers that belong on the GPU ran on the i9). Opening
# ONLY char-drm was insufficient AND made it worse: on the NVIDIA proprietary
# driver the Vulkan COMPUTE path runs through the nvidia character nodes, NOT the
# DRM nodes (/dev/dri, char-drm major 226) — so char-drm alone still denied the
# real devices, yet exposed enough of the GPU path that llama.cpp spun up its
# compute threadpool and was SIGSYS-killed on a blocked scheduling syscall (see
# the PI-Z10 block below), crash-looping without ever reaching the GPU. Correct
# fix, metal-proved (33/33 offload, ~5.9 GiB VRAM, stable under this full unit):
# open char-drm PLUS the four nvidia compute nodes, named BY PATH so systemd
# re-resolves major:minor at each start (nvidia-uvm's major is dynamically
# allocated — no hardcoded number). nvidia-modeset is a KMS/display node, not
# needed for headless compute, so it stays OUT (a future KMS need is a separate
# decision). DevicePolicy=closed still denies everything else; @raw-io stays
# blocked by the @system-service allowlist and CAP_MKNOD stays dropped
# (CapabilityBoundingSet= empty). Minimal-exposure — exactly the GPU compute
# class opened, nothing more.
PrivateDevices=no
DevicePolicy=closed
DeviceAllow=char-drm rw
DeviceAllow=/dev/nvidiactl rw
DeviceAllow=/dev/nvidia0 rw
DeviceAllow=/dev/nvidia-uvm rw
DeviceAllow=/dev/nvidia-uvm-tools rw
ProtectSystem=strict
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallFilter=~@privileged
SystemCallFilter=~@resources
# PI-Z10 (2026-07-06) + PI-Z28 completion (2026-07-07, Zephyrus/i9-12900H):
# llama.cpp's GGML threadpool tunes its worker threads' CPU scheduling at
# startup — sched_setaffinity (203, pins threads by P/E-core topology) and,
# once the GPU/Vulkan backend spins up, setpriority (141) + sched_setscheduler
# (144). All three live in @resources/@privileged, which the filter above denies
# -> the kernel SIGSYS-kills llama-server in a watchdog crash-loop. PI-Z10 first
# hit sched_setaffinity on hybrid CPUs; the GPU path (PI-Z28) adds the other two.
# Re-allow exactly these three measured syscalls; the rest of the @resources /
# @privileged denial stands (RestrictRealtime=yes still arg-blocks any realtime
# policy sched_setscheduler could request).
SystemCallFilter=sched_setaffinity
SystemCallFilter=sched_setscheduler
SystemCallFilter=setpriority
# Fourth measured re-allow (2026-08-06, AMD/ROCm serving): mbind (237) places
# pages on a NUMA node, and the ROCm/HIP userspace calls it while bringing up
# its memory pools. mbind is a member of @resources, denied above, so the kernel
# SIGSYS-kills llama-server during backend initialisation on that hardware —
# the same crash-loop shape as the three syscalls above, reached by a different
# backend. Named individually, exactly like the others: @resources stays denied
# as a set, and nothing else in it is re-opened.
SystemCallFilter=mbind
CapabilityBoundingSet=
AmbientCapabilities=

[Install]
WantedBy=default.target
SERVICE

    # D-Bus service activation, single-instance THROUGH systemd. SystemdService=
    # makes the --systemd-activation session bus delegate activation to the
    # systemd user unit — one systemd-managed, cgroup-tracked instance under
    # Restart=on-failure that will not double-start an already-active unit (so the
    # Type=dbus unit + on-demand activation can no longer both bind the 8089 web
    # port -> the EADDRINUSE cold-boot crash this key was first added for).
    # Exec= is REQUIRED for dbus-daemon to register the name as activatable at all
    # (verified on-box: with Exec= removed the name drops out of `busctl --user
    # list --activatable` and on-demand activation stops firing) — SystemdService=
    # only REDIRECTS activation, it does not register it. Exec= also remains the
    # activation method on a session bus that lacks --systemd-activation, where a
    # stray dbus-launch bus can Exec-spawn a duplicate that proliferates. That
    # class is closed NOT by removing Exec= (which would break activatability) but
    # by the daemon's fail-closed _claim_bus_name guard: a process that does not
    # confirm sole ownership of the name EXITS before binding any resource (no
    # llama-server, no port), loud in the journal — so a duplicate can never bind
    # or serve on any bus. On a shipped GNOME session the only bus is the
    # --systemd-activation one, so SystemdService= single-instances activation and
    # no Exec= duplicate is ever spawned.
    install -Dm644 /dev/stdin "${DESTDIR}/usr/share/dbus-1/services/com.intergenos.InterGen.service" << 'DBUS'
[D-BUS Service]
Name=com.intergenos.InterGen
Exec=/usr/bin/intergen daemon
SystemdService=intergen.service
DBUS

    # T0-4-E integration pkexec gate (RFC v0.1 §6 line 161 D-007 Option A):
    # privileged tool dispatch goes through PolicyKit. Policy declares
    # the org.intergenos.intergen.privileged-tool action; runner is the
    # exec.path target that re-enters Python for argument validation +
    # dispatch via intergen.privileged_dispatch. The provenance gate
    # (intergen/provenance.py — the T0-4-E provenance surface) authorizes INTENT
    # before this PolicyKit gate authorizes AUTHENTICATION; both fire for
    # privileged operations per the RFC "the two don't replace each other"
    # invariant.
    install -Dm644 /mnt/intergenos/intergen/data/org.intergenos.intergen.policy \
        "${DESTDIR}/usr/share/polkit-1/actions/org.intergenos.intergen.policy"
    install -Dm755 /mnt/intergenos/intergen/data/intergen-privileged-runner \
        "${DESTDIR}/usr/bin/intergen-privileged-runner"
    # Setup-time model-storage provisioner (org.intergenos.intergen.provision-
    # model-storage). Lets the unprivileged `intergen setup` install a
    # downloaded+pin-verified model into the root-owned store under one auth
    # prompt; the runner re-verifies the sha256 against the shipped manifest
    # before any write. Distinct from the runtime AI tool-dispatch runner above.
    install -Dm755 /mnt/intergenos/intergen/data/intergen-model-setup-runner \
        "${DESTDIR}/usr/bin/intergen-model-setup-runner"

    # D-008 RFC §14.3 audit-log retention — decided 2026-05-19.
    # 30-day daily rotation of the per-user tool-dispatch JSONL audit log
    # written by intergen.audit_log. Pairs system-side rotation (this snippet)
    # with user-side wipe via `intergen tool-log --clear` (intergen.cli
    # cmd_tool_log). 644 because logrotate.d snippets are world-readable
    # by design; the actual log files retain their 0o600 perms set by the
    # audit_log writer.
    install -Dm644 /mnt/intergenos/intergen/data/intergen-tool-dispatch.logrotate \
        "${DESTDIR}/etc/logrotate.d/intergen-tool-dispatch"

    # Secondary-AMD-GPU runtime-PM pin: a compute-serving card's suspend/resume
    # cycling collapses the desktop's monitor layout (rule header carries the
    # measured record). Shipped with the serving stack because the serving stack
    # is what cycles the card.
    install -Dm644 /mnt/intergenos/intergen/data/70-intergen-compute-gpu-pm.rules \
        "${DESTDIR}/usr/lib/udev/rules.d/70-intergen-compute-gpu-pm.rules"

    # T0-4-D — model SHA256 pinning manifest + release-key-PIV-signed
    # armored detached signature. The consumer (intergen.model_manager._load_pins)
    # reads PINS_MANIFEST_PATH = /usr/share/intergen/models-manifest.json
    # at ModelManager construction; empty/missing/malformed manifest
    # triggers fail-closed verify_model + download_model refusal per the
    # T0-4-D contract. The detached .asc file is the release-key-PIV-rooted
    # supply-chain anchor (master FP 5597A3E0587B253006D0DD7B8C50826182083050
    # per docs/signing-key.md), produced via `gpg --detach-sign --armor`;
    # v1.0 ships structural signature chain; consumer-side gpg --verify
    # wiring is v1.x scope per the audit doc.
    install -Dm644 /mnt/intergenos/intergen/data/models-manifest.json \
        "${DESTDIR}/usr/share/intergen/models-manifest.json"
    install -Dm644 /mnt/intergenos/intergen/data/models-manifest.json.asc \
        "${DESTDIR}/usr/share/intergen/models-manifest.json.asc"

    # InternVL tool-calling chat template. InternVL3.5 GGUFs ship a plain
    # ChatML template with NO tools block, so llama-server never injects the
    # tool schemas and the model freeforms/fabricates instead of emitting tool
    # calls. This Qwen-Hermes tool template (proven to give InternVL3.5-2B
    # 122/122 Gate-A) is passed via llama-server --chat-template-file. Consumer:
    # config llama_server.chat_template_file -> the launch builder.
    install -Dm644 /mnt/intergenos/intergen/data/internvl-tool-template.jinja \
        "${DESTDIR}/usr/share/intergen/internvl-tool-template.jinja"

    # Sentinel decision #5 — destructive-policy never-list manifest + release-key
    # OpenPGP detached signature. The consumer (intergen.destructive_policy.
    # load_policy) reads DEFAULT_MANIFEST_PATH = /usr/share/intergen/
    # destructive-policy-manifest.json (+ .asc), verifies the detached signature
    # over the exact bytes against the pinned release-key fingerprint
    # (5597A3E0587B253006D0DD7B8C50826182083050), and returns None on ANY doubt —
    # the write_file/run_command chokepoint then fail-closes to its interim
    # AI_IMMUTABLE_PREFIXES floor. Without this install step the signed never-list
    # is never present at runtime and load_policy can ONLY ever fail-close to the
    # floor; shipping it here is what makes the full signed system_ai/boot/identity
    # never-list active. Ships to /usr/share/intergen/ (dm-verity read-only).
    install -Dm644 /mnt/intergenos/intergen/data/destructive-policy-manifest.json \
        "${DESTDIR}/usr/share/intergen/destructive-policy-manifest.json"
    install -Dm644 /mnt/intergenos/intergen/data/destructive-policy-manifest.json.asc \
        "${DESTDIR}/usr/share/intergen/destructive-policy-manifest.json.asc"

    # Perceived-latency filler pools (hop-1 ack + hop-2 progress nudges). Loaded
    # as DATA at runtime so the voice is tunable without a code change; the
    # filler-emit runtime (ring buffer + WS events) consumes this asset. See
    # docs/architecture/intergen-perceived-latency-design.md.
    install -Dm644 /mnt/intergenos/intergen/data/voice/fillers.json \
        "${DESTDIR}/usr/share/intergen/voice/fillers.json"

    # Teaching how-to corpus (PI-218-2) — curated, verified explain-intent answers,
    # retrieved via RAG over the running nomic-embed. Shipped read-only under the
    # AI-immutable /usr/share/intergen prefix (on dm-verity), so the AI can never
    # rewrite its own teaching content. Loop-install every domain file so adding a
    # new domain JSON needs no recipe change. intergen.howto resolves this system
    # dir first, falling back to the in-tree copy only when running from source.
    install -dm755 "${DESTDIR}/usr/share/intergen/howto"
    for _howto in /mnt/intergenos/intergen/data/howto/*.json; do
        install -Dm644 "${_howto}" \
            "${DESTDIR}/usr/share/intergen/howto/$(basename "${_howto}")"
    done

    # Governance tamper-detection baseline (build-established, read-only).
    # governance.verify_hash() at runtime hashes the INSTALLED governance.py
    # and compares it to this file; a missing OR mismatched baseline fails
    # CLOSED. The baseline is set HERE, by the build, over the exact bytes just
    # installed (cp -a is byte-identical), and shipped read-only into
    # dm-verity-protected /usr/share — so it is never written trust-on-first-use
    # by the running user daemon (which couldn't, /var being root-owned + RO)
    # and a tampered governance.py can never self-bless its own hash.
    install -dm755 "${DESTDIR}/usr/share/intergen"
    sha256sum "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/governance.py" \
        | awk '{print $1}' > "${DESTDIR}/usr/share/intergen/governance.sha256"
    chmod 644 "${DESTDIR}/usr/share/intergen/governance.sha256"

    # Create data directories
    install -dm755 "${DESTDIR}/var/lib/intergen/models/llm"
    install -dm755 "${DESTDIR}/var/lib/intergen/models/embedding"
    install -dm755 "${DESTDIR}/var/lib/intergen/data"
    install -dm755 "${DESTDIR}/var/lib/intergen/mcp-pins"
    install -dm755 "${DESTDIR}/var/log/intergen"
    install -dm755 "${DESTDIR}/etc/intergen/mcp.d"

    # Desktop entry + app icon for the intergen-panel window. The window is a
    # GtkApplication with app-id org.intergenos.InterGenPanel; GNOME's window
    # tracker matches a window to the .desktop whose id equals the app-id, then
    # shows that entry's Icon in the taskbar/overview. Without this the
    # Windows-style DtP taskbar showed a generic gear for the InterGen window
    # (dock arc, 2026-06-10). The icon is the bare free-floating InterGen
    # ECG pulse (AI = bare; the OS badge is the framed ArcMenu squircle). The
    # entry is NoDisplay (see the file) — it drives window->icon matching, not
    # an app-grid launcher, since InterGen is opt-in and launches via the
    # readiness-gated panel icon / Super+I.
    install -Dm644 /mnt/intergenos/intergen/data/org.intergenos.InterGenPanel.svg \
        "${DESTDIR}/usr/share/icons/hicolor/scalable/apps/org.intergenos.InterGenPanel.svg"
    install -Dm644 /mnt/intergenos/intergen/data/org.intergenos.InterGenPanel.desktop \
        "${DESTDIR}/usr/share/applications/org.intergenos.InterGenPanel.desktop"

    # Man page
    install -Dm644 /mnt/intergenos/packages/ai/intergen/intergen.1 \
        "${DESTDIR}/usr/share/man/man1/intergen.1"
}

post_install() {
    set -e
    # D-010 (decided 2026-05-19): InterGen is opt-in and is never enabled
    # by default. This package_install path MUST NOT enable the user
    # service. The Forge installer prompts at install time (default NO);
    # the YES path runs `systemctl --global enable intergen.service` in
    # the chroot from installer/backend/install.py PHASE_SERVICES.
    # scripts/check-d010-compliance.sh is a Class A ship-gate that
    # blocks ISO assembly if any package_install or autostart path
    # would re-enable intergen by default.
    echo "InterGen installed. Run 'intergen setup' to configure your AI model."
}
