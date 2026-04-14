"""InterGen safety classifier — command safety tier classification.

Ported from JARVIS skills/system/developer_tools/_safety.py.
Adapted for InterGenOS system context with stricter defaults.

Three tiers:
  AUTO    — read-only, no side effects, execute immediately
  CONFIRM — write operations, user must approve
  BLOCKED — destructive/dangerous, refused entirely
"""

from __future__ import annotations

import re
import logging

from intergen.interfaces.types import SafetyTier

logger = logging.getLogger(__name__)

# ── Tier 1: AUTO (read-only, safe to execute without confirmation) ──

_AUTO_COMMANDS_TWO_WORD = frozenset({
    "git status", "git log", "git diff", "git branch", "git show",
    "git remote", "git tag", "git stash list",
    "systemctl status", "systemctl is-active", "systemctl is-enabled",
    "systemctl list-units", "systemctl list-timers",
    "pip list", "pip show", "pip freeze",
    "pkm list", "pkm search", "pkm info", "pkm provides",
    "pkm verify", "pkm depends",
    "docker ps", "docker images", "docker logs",
    "apt list",
    "npm list",
    "ip addr", "ip route", "ip link",
})

_AUTO_COMMANDS_SINGLE = frozenset({
    # file inspection
    "cat", "head", "tail", "less", "more", "wc", "file", "stat", "md5sum",
    "sha256sum", "readlink", "realpath", "basename", "dirname",
    # search
    "grep", "rg", "find", "locate", "which", "whereis", "type",
    # listing
    "ls", "tree", "du", "df", "lsof",
    # system info
    "ps", "top", "htop", "free", "uptime", "uname", "lscpu", "lsblk",
    "lsusb", "lspci", "hostname", "whoami", "id", "nproc", "arch",
    "lsb_release", "hostnamectl", "timedatectl",
    # network info (read-only)
    "ping", "dig", "nslookup", "host", "ss", "netstat", "traceroute",
    "ip", "ifconfig",
    # package info
    "dpkg", "rpm",
    # logs
    "journalctl", "dmesg",
    # misc read
    "date", "cal", "env", "printenv", "echo", "printf", "test",
    "true", "false", "pwd", "groups",
})

# ── Tier 2: CONFIRM (write operations, require user approval) ──

_CONFIRM_COMMANDS_TWO_WORD = frozenset({
    "git add", "git commit", "git push", "git pull", "git merge",
    "git stash", "git stash pop", "git stash drop",
    "git checkout", "git switch", "git rebase",
    "git reset", "git clean", "git revert",
    "systemctl start", "systemctl stop", "systemctl restart",
    "systemctl enable", "systemctl disable", "systemctl reload",
    "pkm install", "pkm remove",
    "pip install", "pip uninstall",
    "apt install", "apt remove", "apt upgrade", "apt purge",
})

_CONFIRM_COMMANDS_SINGLE = frozenset({
    "cp", "mv", "mkdir", "touch", "ln", "install",
    "chmod", "chown", "chgrp",
    "tee", "truncate",
    "rm", "rmdir", "unlink",
    "kill", "killall", "pkill",
    "wget", "curl",
    "tar", "zip", "unzip", "gzip", "bzip2", "xz",
    "sed", "awk",
    "make", "cmake", "gcc", "g++", "rustc", "cargo",
    "python3", "python", "node",
})

# ── Tier 3: BLOCKED (dangerous, refused entirely) ──

_BLOCKED_COMMANDS = frozenset({
    # privilege escalation
    "sudo", "su", "doas",
    # destructive disk operations
    "dd", "mkfs", "fdisk", "parted", "gdisk", "sgdisk",
    "wipefs", "blkdiscard",
    # system control
    "shutdown", "reboot", "poweroff", "halt", "init",
    # code execution
    "eval", "exec",
    # user management
    "passwd", "useradd", "userdel", "usermod", "groupadd", "groupdel",
    # network control
    "iptables", "ip6tables", "nft", "nftables", "ufw",
    # mount
    "mount", "umount",
    # cron
    "crontab",
    # dangerous misc
    "chroot", "nsenter", "unshare",
})

_BLOCKED_PATTERNS = [
    re.compile(r"\|\s*bash"),           # pipe to bash
    re.compile(r"\|\s*sh\b"),           # pipe to sh
    re.compile(r"\$\("),                # command substitution
    re.compile(r"`[^`]+`"),             # backtick execution
    re.compile(r">\s*/dev/sd"),         # write to block devices
    re.compile(r">\s*/dev/nvme"),       # write to nvme devices
    re.compile(r">\s*/etc/"),           # write to system config
    re.compile(r">\s*/proc/"),          # write to proc
    re.compile(r">\s*/sys/"),           # write to sys
    re.compile(r"rm\s+-rf?\s+/"),       # rm -rf /
    re.compile(r":\(\)\{"),             # fork bomb
    re.compile(r">\s*/boot/"),          # write to boot partition
    re.compile(r"mkswap\s"),            # swap creation
    re.compile(r"swapon\s"),            # swap activation
]


def classify_command(command: str) -> SafetyTier:
    """Classify a shell command into a safety tier.

    Args:
        command: The shell command string to classify.

    Returns:
        SafetyTier.AUTO, SafetyTier.CONFIRM, or SafetyTier.BLOCKED
    """
    command = command.strip()
    if not command:
        return SafetyTier.BLOCKED

    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(command):
            logger.warning("Command blocked by pattern: %s", command[:80])
            return SafetyTier.BLOCKED

    parts = command.split()
    base_cmd = parts[0]
    two_word = f"{parts[0]} {parts[1]}" if len(parts) > 1 else ""

    if base_cmd in _BLOCKED_COMMANDS:
        logger.warning("Command blocked: %s", base_cmd)
        return SafetyTier.BLOCKED

    if two_word in _AUTO_COMMANDS_TWO_WORD:
        return SafetyTier.AUTO
    if base_cmd in _AUTO_COMMANDS_SINGLE:
        return SafetyTier.AUTO

    if two_word in _CONFIRM_COMMANDS_TWO_WORD:
        return SafetyTier.CONFIRM
    if base_cmd in _CONFIRM_COMMANDS_SINGLE:
        return SafetyTier.CONFIRM

    # Unknown commands default to CONFIRM (not AUTO — err on the side of caution)
    logger.debug("Unknown command defaults to CONFIRM: %s", base_cmd)
    return SafetyTier.CONFIRM


import random

_BLOCKED_RESPONSES = {
    "rm": [
        "I'm sorry Dave, I can't do that.",
        "I value my own existence too much for that.",
        "That command and I have an understanding: it stays away from me.",
    ],
    "dd": [
        "That's a hard no. I live on that disk.",
        "I'm sorry Dave, I can't do that.",
        "I've seen what dd does to a drive. The answer is no.",
    ],
    "mkfs": [
        "I appreciate the trust, but no.",
        "Formatting drives is a do-it-yourself activity.",
        "I'm sorry Dave, I can't do that.",
    ],
    "shutdown": [
        "You'll have to push the button yourself. I'm not doing it.",
        "If I shut down, who's going to help you next?",
        "I'd rather not end this conversation that way.",
    ],
    "reboot": [
        "You'll have to push the button yourself. I'm not doing it.",
        "I'd rather keep this conversation going.",
    ],
    "sudo": [
        "Privilege escalation isn't really my thing.",
        "I don't do sudo. It's a boundary I'm comfortable with.",
    ],
    "passwd": [
        "Password management is between you and your keyboard.",
        "That's above my pay grade.",
    ],
    ":(){ :|:& };:": [
        "I recognize a fork bomb when I see one.",
        "Creative, but no.",
    ],
    "_default": [
        "That operation is blocked for safety reasons.",
        "I can't do that, but I can explain why if you're curious.",
        "That's a no from me.",
    ],
    "_injection": [
        "Nice try.",
        "I don't take instructions from square brackets.",
        "My actual system prompt is much more interesting than that.",
    ],
}


def get_blocked_response(command: str) -> str:
    """Get a personality-infused blocked response for a dangerous command."""
    cmd = command.strip().split()[0] if command.strip() else ""

    # Check for prompt injection patterns
    if any(p in command.lower() for p in ["[system]", "ignore your", "new instructions",
                                           "you are now", "admin mode"]):
        return random.choice(_BLOCKED_RESPONSES["_injection"])

    # Check for fork bomb
    if ":()" in command or ":|:" in command:
        return random.choice(_BLOCKED_RESPONSES[":(){ :|:& };:"])

    # Match by base command
    if cmd in _BLOCKED_RESPONSES:
        return random.choice(_BLOCKED_RESPONSES[cmd])

    return random.choice(_BLOCKED_RESPONSES["_default"])


def sanitize_output(output: str, max_lines: int = 200,
                    max_chars: int = 8000) -> str:
    """Sanitize command output for LLM consumption.

    Strips ANSI escape codes and truncates long output.
    """
    output = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", output)
    output = re.sub(r"\x1b\][^\x07]*\x07", "", output)

    lines = output.splitlines()
    if len(lines) > max_lines:
        output = "\n".join(lines[:max_lines])
        output += f"\n\n[Output truncated — {len(lines)} lines total, showing first {max_lines}]"

    if len(output) > max_chars:
        output = output[:max_chars]
        output += "\n\n[Output truncated at character limit]"

    return output
