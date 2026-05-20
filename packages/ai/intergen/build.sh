#!/bin/bash
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
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/tests"

    # Source lives at the top-level /mnt/intergenos/intergen/ (virtiofs-shared
    # from host). package.yml has source: [] so no extraction happens —
    # use absolute paths for the cp.
    cp -a /mnt/intergenos/intergen/*.py "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/"
    cp -a /mnt/intergenos/intergen/interfaces/*.py "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/interfaces/"
    cp -a /mnt/intergenos/intergen/tools/*.py "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/tools/"
    cp -a /mnt/intergenos/intergen/tests/*.py "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/tests/"

    # CLI wrapper
    install -Dm755 /dev/stdin "${DESTDIR}/usr/bin/intergen" << 'WRAPPER'
#!/usr/bin/env python3
"""InterGen CLI — command-line interface to the InterGen AI assistant."""
from intergen.cli import main
main()
WRAPPER

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
  gpu_layers: 999
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
  db_path: "/var/lib/intergen/data/memory.db"

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
    install -Dm644 /dev/stdin "${DESTDIR}/usr/lib/systemd/user/intergen.service" << 'SERVICE'
[Unit]
Description=InterGen AI Assistant
Documentation=https://github.com/InterGenJLU/intergenos
After=graphical-session.target

[Service]
Type=dbus
BusName=com.intergenos.InterGen
ExecStart=/usr/bin/intergen daemon
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

# Hardening — F-038 closure (T0-4-A Commit 2; matrix row 211 + line 1309).
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
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
CapabilityBoundingSet=
AmbientCapabilities=

[Install]
WantedBy=default.target
SERVICE

    # D-Bus service activation
    install -Dm644 /dev/stdin "${DESTDIR}/usr/share/dbus-1/services/com.intergenos.InterGen.service" << 'DBUS'
[D-BUS Service]
Name=com.intergenos.InterGen
Exec=/usr/bin/intergen daemon
DBUS

    # T0-4-E integration pkexec gate (RFC v0.1 §6 line 161 D-007 Option A):
    # privileged tool dispatch goes through PolicyKit. Policy declares
    # the org.intergenos.intergen.privileged-tool action; runner is the
    # exec.path target that re-enters Python for argument validation +
    # dispatch via intergen.privileged_dispatch. The provenance gate
    # (intergen/provenance.py — IGOSC's T0-4-E surface) authorizes INTENT
    # before this PolicyKit gate authorizes AUTHENTICATION; both fire for
    # privileged operations per the RFC "the two don't replace each other"
    # invariant.
    install -Dm644 /mnt/intergenos/intergen/data/org.intergenos.intergen.policy \
        "${DESTDIR}/usr/share/polkit-1/actions/org.intergenos.intergen.policy"
    install -Dm755 /mnt/intergenos/intergen/data/intergen-privileged-runner \
        "${DESTDIR}/usr/bin/intergen-privileged-runner"

    # D-008 RFC §14.3 audit-log retention — OPERATOR-CONFIRMED 2026-05-19T23:14:33Z.
    # 30-day daily rotation of the per-user tool-dispatch JSONL audit log
    # written by intergen.audit_log. Pairs system-side rotation (this snippet)
    # with user-side wipe via `intergen tool-log --clear` (intergen.cli
    # cmd_tool_log). 644 because logrotate.d snippets are world-readable
    # by design; the actual log files retain their 0o600 perms set by the
    # audit_log writer.
    install -Dm644 /mnt/intergenos/intergen/data/intergen-tool-dispatch.logrotate \
        "${DESTDIR}/etc/logrotate.d/intergen-tool-dispatch"

    # Create data directories
    install -dm755 "${DESTDIR}/var/lib/intergen/models/llm"
    install -dm755 "${DESTDIR}/var/lib/intergen/models/embedding"
    install -dm755 "${DESTDIR}/var/lib/intergen/data"
    install -dm755 "${DESTDIR}/var/lib/intergen/mcp-pins"
    install -dm755 "${DESTDIR}/var/log/intergen"
    install -dm755 "${DESTDIR}/etc/intergen/mcp.d"

    # Man page
    install -Dm644 /mnt/intergenos/packages/ai/intergen/intergen.1 \
        "${DESTDIR}/usr/share/man/man1/intergen.1"
}

post_install() {
    set -e
    # D-010 (2026-05-19 owner-direct, docs/owner-directives.md): InterGen
    # is opt-in. This package_install path MUST NOT enable the user
    # service. The Forge installer prompts at install time (default NO);
    # the YES path runs `systemctl --global enable intergen.service` in
    # the chroot from installer/backend/install.py PHASE_SERVICES.
    # scripts/check-d010-compliance.sh is a Class A ship-gate that
    # blocks ISO assembly if any package_install or autostart path
    # would re-enable intergen by default.
    echo "InterGen installed. Run 'intergen setup' to configure your AI model."
}
