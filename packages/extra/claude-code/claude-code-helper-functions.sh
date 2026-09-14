#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
:  # This file is sourced; it has no top-level action.

CLAUDE_CODE_NPM_PACKAGE="@anthropic-ai/claude-code"

claude_code_is_release_version() {
    local version="${1:-}"
    [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

claude_code_select_install_version() {
    local pinned_version="${1:-}"
    local requested_mode="${CLAUDE_CODE_INSTALL_CURRENT:-0}"
    local current_version

    if ! claude_code_is_release_version "$pinned_version"; then
        echo "  ERROR: the reviewed Claude Code version is invalid: ${pinned_version:-empty}" >&2
        return 1
    fi

    case "$requested_mode" in
        ""|0)
            CLAUDE_CODE_SELECTED_MODE="pinned"
            CLAUDE_CODE_SELECTED_REASON="reviewed version recorded by this package"
            CLAUDE_CODE_SELECTED_VERSION="$pinned_version"
            ;;
        1)
            if ! command -v npm >/dev/null 2>&1; then
                echo "  ERROR: npm is required to resolve the current Claude Code release." >&2
                return 1
            fi
            if ! current_version=$(npm view "$CLAUDE_CODE_NPM_PACKAGE" dist-tags.latest); then
                echo "  ERROR: the current Claude Code release could not be read from the npm registry." >&2
                return 1
            fi
            if [[ "$current_version" == *$'\n'* ]] || \
                    ! claude_code_is_release_version "$current_version"; then
                echo "  ERROR: npm returned an invalid current release: ${current_version:-empty}" >&2
                return 1
            fi
            CLAUDE_CODE_SELECTED_MODE="current"
            CLAUDE_CODE_SELECTED_REASON="explicit current-registry request"
            CLAUDE_CODE_SELECTED_VERSION="$current_version"
            ;;
        *)
            echo "  ERROR: CLAUDE_CODE_INSTALL_CURRENT must be absent, 0, or 1." >&2
            return 1
            ;;
    esac
}

claude_code_print_plan() {
    local extension_version="${1:-}"
    local extension_platform="${2:-}"
    local extension_sha256="${3:-}"

    printf 'mode=%s\n' "$CLAUDE_CODE_SELECTED_MODE"
    printf 'mode_reason=%s\n' "$CLAUDE_CODE_SELECTED_REASON"
    printf 'cli_version=%s\n' "$CLAUDE_CODE_SELECTED_VERSION"
    printf 'extension_version=%s\n' "$extension_version"
    printf 'extension_platform=%s\n' "$extension_platform"
    printf 'extension_sha256=%s\n' "$extension_sha256"
}

claude_code_verify_installed_version() {
    local requested_version="${1:-}"
    local reported_output="${2:-}"
    local token
    local actual_version=""
    local -a tokens=()

    if ! claude_code_is_release_version "$requested_version"; then
        echo "  ERROR: requested Claude Code version is invalid: ${requested_version:-empty}" >&2
        return 1
    fi

    if [[ "$reported_output" == *$'\n'* ]]; then
        echo "  ERROR: the installed executable's --version output has more than one line." >&2
        return 1
    fi
    read -r -a tokens <<< "$reported_output"
    for token in "${tokens[@]}"; do
        token="${token#v}"
        token="${token#(}"
        token="${token%)}"
        token="${token%,}"
        if claude_code_is_release_version "$token"; then
            if [ -n "$actual_version" ] && [ "$actual_version" != "$token" ]; then
                echo "  ERROR: the installed executable's --version output names more than one release." >&2
                return 1
            fi
            actual_version="$token"
        fi
    done

    if [ -z "$actual_version" ]; then
        echo "  ERROR: could not parse a release from the installed executable's --version output: ${reported_output:-empty}" >&2
        return 1
    fi
    if [ "$actual_version" != "$requested_version" ]; then
        echo "  ERROR: Claude Code version mismatch: requested $requested_version, reported $actual_version." >&2
        return 1
    fi

    printf '%s\n' "$actual_version"
}

claude_code_install_selected_cli() {
    local selected_mode="${1:-}"
    local selected_version="${2:-}"
    local audit_tmpdir
    local version_output
    local installed_version
    local global_prefix
    local module_dir
    local bin_name

    if [ "$selected_mode" != "pinned" ] && [ "$selected_mode" != "current" ]; then
        echo "  ERROR: internal Claude Code install mode is invalid: ${selected_mode:-empty}" >&2
        return 1
    fi
    if ! claude_code_is_release_version "$selected_version"; then
        echo "  ERROR: selected Claude Code version is invalid: ${selected_version:-empty}" >&2
        return 1
    fi
    if ! command -v npm >/dev/null 2>&1; then
        echo "  ERROR: npm not found. Install Node.js first." >&2
        return 1
    fi

    echo "  Checking the package tree for critical security advisories..."
    if ! audit_tmpdir=$(mktemp -d -t igos-claude-code-audit-XXXXXX); then
        echo "  ERROR: could not create the isolated advisory-check directory." >&2
        return 1
    fi
    cat > "$audit_tmpdir/package.json" << JSONEOF
{
  "name": "igos-claude-code-audit-shim",
  "version": "0.0.0",
  "private": true,
  "dependencies": {
    "@anthropic-ai/claude-code": "${selected_version}"
  }
}
JSONEOF
    if ! (cd "$audit_tmpdir" && \
            npm install --ignore-scripts --no-audit --no-fund 2>&1); then
        echo "" >&2
        echo "  ERROR: the advisory and signature verification tree could not be prepared." >&2
        echo "  Refusing to install an unverified Claude Code package." >&2
        rm -rf "$audit_tmpdir"
        return 1
    fi
    if ! (cd "$audit_tmpdir" && npm audit --audit-level=critical 2>&1); then
        echo "" >&2
        echo "  ERROR: npm audit found CRITICAL-severity advisories in the" >&2
        echo "  @anthropic-ai/claude-code@${selected_version} dependency" >&2
        echo "  tree. Refusing to install. Nothing was installed." >&2
        echo "  The advisory is printed above; this needs a fixed upstream" >&2
        echo "  release before the package will install." >&2
        rm -rf "$audit_tmpdir"
        return 1
    fi
    echo "  No critical security advisories."

    echo "  Verifying npm registry signatures for the selected package tree..."
    if ! (cd "$audit_tmpdir" && npm audit signatures 2>&1); then
        echo "" >&2
        echo "  ERROR: npm registry signature verification failed for" >&2
        echo "  @anthropic-ai/claude-code@${selected_version}." >&2
        echo "  Refusing to install a package whose registry signature was not verified." >&2
        rm -rf "$audit_tmpdir"
        return 1
    fi
    rm -rf "$audit_tmpdir"
    echo "  npm registry signatures verified."

    echo "  Installing Claude Code CLI via npm (${selected_mode} version ${selected_version})..."
    if ! npm install -g "@anthropic-ai/claude-code@${selected_version}"; then
        echo "  ERROR: npm could not install Claude Code ${selected_version}." >&2
        return 1
    fi

    if ! global_prefix=$(npm prefix -g 2>/dev/null); then
        echo "  ERROR: npm did not report its global installation prefix." >&2
        return 1
    fi
    # The executable's name is read from the installed module's own manifest
    # (its package.json `bin` map) rather than assumed, and the module must
    # declare exactly one.
    module_dir="${global_prefix%/}/lib/node_modules/${CLAUDE_CODE_NPM_PACKAGE}"
    if ! bin_name=$(node -p 'const b = require(process.argv[1] + "/package.json").bin; (b && typeof b === "object" && Object.keys(b).length === 1) ? Object.keys(b)[0] : ""' "$module_dir" 2>/dev/null) \
            || [ -z "$bin_name" ] || [[ "$bin_name" == */* ]]; then
        echo "  ERROR: the installed module does not declare exactly one executable: ${module_dir}/package.json" >&2
        return 1
    fi
    CLAUDE_CODE_INSTALLED_BIN="${global_prefix%/}/bin/${bin_name}"
    if [ ! -x "$CLAUDE_CODE_INSTALLED_BIN" ]; then
        echo "  ERROR: npm completed but the installed executable is absent: $CLAUDE_CODE_INSTALLED_BIN" >&2
        return 1
    fi
    if ! version_output=$("$CLAUDE_CODE_INSTALLED_BIN" --version 2>&1); then
        echo "  ERROR: the installed executable could not report its version." >&2
        printf '%s\n' "$version_output" >&2
        return 1
    fi
    if ! installed_version=$(claude_code_verify_installed_version \
            "$selected_version" "$version_output"); then
        return 1
    fi

    CLAUDE_CODE_INSTALLED_VERSION="$installed_version"
    CLAUDE_CODE_INSTALLED_VERSION_OUTPUT="$version_output"
    CLAUDE_CODE_INSTALLED_ROOT="${global_prefix%/}/lib/node_modules"
    echo "  Claude Code CLI installed and read back: ${version_output}"
}
