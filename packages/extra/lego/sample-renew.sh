#!/bin/bash
# Sample lego renewal helper for InterGenOS
#
# This file is shipped to /usr/share/lego/sample-renew.sh as a
# STARTING TEMPLATE. Operators copy + edit + place at one of:
#   /etc/cron.daily/lego-renew         (cron-style; runs daily)
#   /etc/systemd/system/lego-renew.service + lego-renew.timer
#
# DO NOT run this file as-is in production. The defaults below use
# Let's Encrypt's STAGING endpoint (test certs only, not trusted by
# browsers). Operators flip --server to the production endpoint
# deliberately after a successful staging-cert dry-run.
#
# Usage examples:
#
# 1. Initial issuance (HTTP-01, single domain, web-root mode):
#    /usr/bin/lego                                                  \
#        --accept-tos                                                \
#        --email='admin@example.org'                                 \
#        --server=https://acme-staging-v02.api.letsencrypt.org/directory \
#        --domains=example.org                                       \
#        --http                                                      \
#        --http.webroot=/var/www/html                                \
#        --path=/var/lib/lego                                        \
#        run
#
# 2. Renewal (run from systemd timer or cron.daily):
#    /usr/bin/lego                                                  \
#        --accept-tos                                                \
#        --email='admin@example.org'                                 \
#        --server=https://acme-staging-v02.api.letsencrypt.org/directory \
#        --domains=example.org                                       \
#        --http                                                      \
#        --http.webroot=/var/www/html                                \
#        --path=/var/lib/lego                                        \
#        renew                                                       \
#        --days 30                                                   \
#        --reuse-key
#
# 3. DNS-01 challenge (no webroot needed; works for wildcards):
#    LEGO_DNS_PROVIDER_CREDENTIALS=... /usr/bin/lego                 \
#        --accept-tos                                                \
#        --email='admin@example.org'                                 \
#        --domains='*.example.org'                                   \
#        --dns=<provider>                                            \
#        --path=/var/lib/lego                                        \
#        run
#    # Run `/usr/bin/lego dnshelp` for the provider-credentials list.
#
# 4. Hook to reload the web server after successful renewal:
#    add --renew-hook='systemctl reload nginx' (or httpd/caddy/...)
#
# Operator preflight checklist (delete after reading):
#   * Replace 'admin@example.org' with a real email — Let's Encrypt
#     uses this for expiry warnings and tos-acceptance accounting.
#   * Replace 'example.org' with the actual domain(s).
#   * Flip --server to the production endpoint:
#       https://acme-v02.api.letsencrypt.org/directory
#     (after a successful staging-cert dry-run).
#   * Verify the --http.webroot path is the actual DocumentRoot of
#     your web server, AND that the web server serves
#     /.well-known/acme-challenge/* from that path without redirects.
#   * Verify /var/lib/lego is owned by whatever user will run lego
#     (root is typical; consider a dedicated `acme` system user for
#     least-privilege).
#   * For DNS-01: keep provider credentials in a file with mode 600,
#     sourced into the systemd unit's Environment= line — do not
#     commit credentials to /etc/cron.daily/ scripts.

set -euo pipefail
echo "This is a sample. Do not run as-is. See /usr/share/lego/sample-renew.sh"
echo "for usage examples. Copy + customize to /etc/cron.daily/ or build a"
echo "systemd timer at /etc/systemd/system/lego-renew.{service,timer}."
exit 1
