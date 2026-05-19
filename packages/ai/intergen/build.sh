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

    # Systemd user service
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
