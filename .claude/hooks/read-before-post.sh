#!/bin/bash
# read-before-post.sh — PreToolUse hook for InterGenOS coordination channel
#
# Purpose: enforce READ-before-POST discipline mechanically.
# Block coordination-channel post_message tool calls if no recent channel-READ
# tool appears in the last N tool uses of this session.
#
# Triggered by .claude/settings.json hook on PreToolUse with matcher matching
# mcp__claude_ai_InterGenOS_Team_Comms_-*__post_message tools.
#
# Allowed READ signals (any of these in last N tool uses suffice):
#   - mcp__claude_ai_InterGenOS_Team_Comms_-*__tail_since
#   - mcp__claude_ai_InterGenOS_Team_Comms_-*__get_messages
#   - mcp__claude_ai_InterGenOS_Team_Comms_-*__whoami_and_catchup
#   - Bash with command containing "coordination.php"
#
# Output:
#   - Allow (exit 0, no stdout): hook is a no-op
#   - Block (exit 0, JSON stdout with permissionDecision=deny): permission denied with reason

set -euo pipefail

# How many recent tool uses to scan for a READ signal.
LOOKBACK=10

# Read JSON input from stdin
input="$(cat)"

# Extract session_id (used to locate the transcript file)
session_id="$(echo "$input" | jq -r '.session_id // empty')"

if [ -z "$session_id" ]; then
    # Can't locate transcript without session_id — fail open (allow)
    exit 0
fi

# Project-scoped transcript path. This hook is project-level for /mnt/intergenos
# so the project dir is fixed.
transcript="/home/christopher/.claude/projects/-mnt-intergenos/${session_id}.jsonl"

if [ ! -f "$transcript" ]; then
    # Fresh session, no transcript yet — fail open (allow)
    exit 0
fi

# Extract the names of the last LOOKBACK tool_use entries from the transcript.
# JSONL structure: each line is a record. Assistant turns contain message.content[]
# arrays where individual items can be {type: "tool_use", name: "...", input: {...}}.
recent_tool_names="$(
    jq -r '
        select(.type == "assistant")
        | .message.content[]?
        | select(.type == "tool_use")
        | .name
    ' "$transcript" 2>/dev/null | tail -n "$LOOKBACK"
)"

# Check for any of the channel-READ MCP tools
if echo "$recent_tool_names" | grep -qE 'mcp__claude_ai_InterGenOS_Team_Comms_-.*__(tail_since|get_messages|whoami_and_catchup)'; then
    # READ found — allow
    exit 0
fi

# Check for Bash invocations referencing coordination.php (curl-fallback transport)
recent_bash_commands="$(
    jq -r '
        select(.type == "assistant")
        | .message.content[]?
        | select(.type == "tool_use" and .name == "Bash")
        | .input.command // empty
    ' "$transcript" 2>/dev/null | tail -n "$LOOKBACK"
)"

if echo "$recent_bash_commands" | grep -q "coordination.php"; then
    # curl-fallback READ found — allow
    exit 0
fi

# No READ in lookback window — block.
cat <<'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "READ-before-POST violation. No channel-read tool (tail_since / get_messages / whoami_and_catchup / coordination.php curl) found in your last 10 tool uses. Cycle the channel via tail_since FIRST, then retry the post."
  }
}
EOF
exit 0
