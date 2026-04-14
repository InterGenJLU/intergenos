#!/bin/bash
# intergen 0.1.0 — InterGen AI assistant for InterGenOS
# https://github.com/InterGenJLU/intergenos
#
# Installs: Python modules, systemd service, D-Bus activation,
# default config, CLI wrapper, Forge integration hook.
#
# Python dependencies (numpy, torch-cpu, sentence-transformers) are
# installed via pip at build time — they ship as part of this package.

build() {
    # Install Python dependencies into the package
    # CPU-only torch — no NVIDIA/CUDA bloat
    pip3 install --target="${DESTDIR}/usr/lib/python3.14/site-packages" \
        --no-cache-dir \
        numpy \
        sentence-transformers \
        huggingface-hub \
        2>&1 | tail -5

    # Torch CPU-only requires separate index URL
    pip3 install --target="${DESTDIR}/usr/lib/python3.14/site-packages" \
        --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch \
        2>&1 | tail -5
}

do_install() {
    # InterGen Python package
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages/intergen"
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/interfaces"
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/tools"
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/tests"

    cp -a intergen/*.py "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/"
    cp -a intergen/interfaces/*.py "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/interfaces/"
    cp -a intergen/tools/*.py "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/tools/"
    cp -a intergen/tests/*.py "${DESTDIR}/usr/lib/python3.14/site-packages/intergen/tests/"

    # CLI wrapper
    install -Dm755 /dev/stdin "${DESTDIR}/usr/bin/intergen" << 'WRAPPER'
#!/usr/bin/env python3
"""InterGen CLI — command-line interface to the InterGen AI assistant."""
import sys
from intergen.__main__ import main
sys.exit(main())
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
  context_size: 8192

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

    # Create data directories
    install -dm755 "${DESTDIR}/var/lib/intergen/models/llm"
    install -dm755 "${DESTDIR}/var/lib/intergen/models/embedding"
    install -dm755 "${DESTDIR}/var/lib/intergen/data"
    install -dm755 "${DESTDIR}/var/lib/intergen/mcp-pins"
    install -dm755 "${DESTDIR}/var/log/intergen"
    install -dm755 "${DESTDIR}/etc/intergen/mcp.d"
}

post_install() {
    # Enable the systemd user service for all users
    systemctl --global enable intergen.service 2>/dev/null || true
    echo "InterGen installed. Run 'intergen setup' to configure your AI model."
}
