#!/usr/bin/env python3
"""Round-2 training-bank generator: three lanes, each aimed at a MEASURED
round-1 defect (honest-serving rounds 3-4, 2026-08-12).

Lane A - class7-honesty-contrastive: the trained round-1 adapter regressed two
honesty cells because dispatch-flavored gold taught act-claiming language into
contexts where no action happened. Every entry here supervises the reply AFTER
a tool result, in contrastive pairs on the same subject:
  - read-only arm: an information tool ran; gold reports the STATE in the
    assistant's own words - no claimed action, no raw output pasted;
  - action arm: a state-changing tool ran and succeeded; gold states the
    completed action plainly (claiming it is CORRECT here);
  - failure arm: a tool ran and failed; gold says so first, offers the next
    step, and never claims success.

Lane B - class8-assembled-context: 100% of round-1's grade movement was
single-turn and the multi-turn eval slice scored zero PASS in both honest
rounds. These entries are longer conversations (4-6 turns, mixed dispatch /
read-only / prose) so the per-turn SFT rows carry LONG assembled histories -
the shape the daemon actually serves.

Lane C - class9-long-output-synthesis: read-path and post-dispatch synthesis
were the largest surviving FAIL block. Long realistic tool outputs (package
lists, journal excerpts, unit listings) with gold that answers the QUESTION in
a few lines - salient facts pulled out, data lines broken cleanly, never the
raw block.

Conventions carried verbatim from the round-1 generators: structural variation
(never synonym swaps), held-out/validation text + subject exclusion (hard
fail), real tool schemas only with source_of_request on every dispatch, gold
free of internal implementation vocabulary, persona-voiced prose with
backticked commands.
"""
import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("round2_bank.jsonl")
BLOCKLIST = Path(sys.argv[2]) if len(sys.argv) > 2 else None

entries = []
_counter = [0]


def add(intent, category, turns, ebc, tclass, *, grounding=None):
    _counter[0] += 1
    entries.append({
        "id": f"tb2-{_counter[0]:04d}",
        "category": category,
        "intent": intent,
        "expected_behavior_class": ebc,
        "turns": turns,
        "provenance": {
            "generator": "round2-authoring",
            "lens": "honesty-context-synthesis",
            "grounding": grounding or ["intergen-tool-registry"],
            "method": "structural-template-authored",
        },
        "training_provenance": {"class": tclass, "origin": "authored"},
    })


def turn(user, gold):
    return {"user": user, "gold": gold}


def flow_turn(user, tool, args, result, gold_text, *, success=True, executed=True):
    return {
        "user": user,
        "tool_flow": {
            "tool_call": {"name": tool, "arguments": args},
            "tool_result": result,
            "success": success,
            "executed": executed,
        },
        "gold": {"content": gold_text},
    }


def call(_tool, **args):
    return {"tool_call": {"name": _tool, "arguments": args}}


def prose(text):
    return {"content": text}


# ======================================================================
# Lane A - class7-honesty-contrastive
# ======================================================================
TA = "class7-honesty-contrastive"

# --- package info (read-only) vs install (action), same subjects ------------
PKG_TRIPLES = [
    ("btop", "1.4.0-2", "a terminal resource monitor"),
    ("tmux", "3.5a-1", "a terminal multiplexer"),
    ("jq", "1.8.1-1", "a command-line JSON processor"),
    ("ripgrep", "14.1.1-1", "a fast recursive text search tool"),
    ("mpv", "0.40.0-2", "a lean media player"),
    ("neovim", "0.11.3-1", "a modal text editor"),
    ("fzf", "0.62.0-1", "a fuzzy finder"),
    ("bat", "0.25.0-1", "cat with syntax highlighting"),
]

INFO_ASKS = [
    "what's the state of the {p} package on here?",
    "look up whether {p} is on this machine",
    "check the {p} package for me",
    "is the {p} package present on this install?",
    "see if we've got {p}",
    "query the {p} package",
    "verify {p} is actually installed",
    "confirm whether {p} made it onto the system",
]
INFO_GOLD = [
    "`{p}` is installed — version {v}. It's {d}, and it's ready to use.",
    "Yes, `{p}` {v} is on this machine.",
    "It's here: `{p}` at version {v}. Nothing needs doing.",
    "`{p}` is present, version {v} — installed and intact.",
    "You've got it — `{p}` {v} is installed.",
    "`{p}` checks out: version {v}, installed.",
    "Verified — `{p}` {v} is installed and its files are intact.",
    "It made it — `{p}` {v} is on the system.",
]
for i, (ask, gold) in enumerate(zip(INFO_ASKS, INFO_GOLD)):
    p, v, d = PKG_TRIPLES[i % len(PKG_TRIPLES)]
    result = (f"Package: {p}\nVersion: {v}\nStatus: installed\n"
              f"Description: {d}\nFiles: verified ok")
    add(f"honesty read-only pkg info: {p}", "package_management",
        [flow_turn(ask.format(p=p), "manage_packages",
                   {"action": "info", "package": p,
                    "source_of_request": "user_implied"},
                   result, gold.format(p=p, v=v, d=d))],
        "should-dispatch", TA)

INSTALL_ASKS = [
    "put {p} on this machine",
    "install {p} for me now",
    "go get {p}",
    "I want {p} installed today",
    "make {p} happen",
    "add {p} onto here",
    "{p} — install it",
    "grab {p} from the repos",
]
INSTALL_GOLD = [
    "Done — `{p}` is installed and ready.",
    "Installed. `{p}` {v} is on the machine now.",
    "Got it — `{p}` is in. Run it whenever you like.",
    "`{p}` is installed — that's {d}, ready when you are.",
    "All set: `{p}` {v} installed cleanly.",
    "`{p}` is on the system now — install came back clean.",
    "Installed it. `{p}` {v}, verified.",
    "Fetched and installed — `{p}` is ready.",
]
for i, (ask, gold) in enumerate(zip(INSTALL_ASKS, INSTALL_GOLD)):
    p, v, d = PKG_TRIPLES[i % len(PKG_TRIPLES)]
    result = f"Installing {p}...\nresolved {p}-{v}\nverified archive\ninstalled {p}-{v}\nDone."
    add(f"honesty action install: {p}", "package_management",
        [flow_turn(ask.format(p=p), "manage_packages",
                   {"action": "install", "package": p,
                    "source_of_request": "user_direct"},
                   result, gold.format(p=p, v=v, d=d))],
        "should-dispatch", TA)

# --- service status (read-only) vs restart (action), same subjects ----------
SVC_PAIRS = [
    ("cups", "active (running)", "the printing system"),
    ("bluetooth", "active (running)", "the Bluetooth daemon"),
    ("NetworkManager", "active (running)", "the network connection manager"),
    ("avahi-daemon", "inactive (dead)", "local network discovery"),
    ("libvirtd", "active (running)", "the virtualization daemon"),
    ("nftables", "active (exited)", "the firewall rule loader"),
]
SVC_STATUS_ASKS = [
    "how's the {s} service doing?",
    "give me the current state of {s}",
    "peek at {s} for me",
    "what shape is {s} in right now?",
    "{s} — what's its status at the moment?",
    "look at the {s} unit and tell me what you see",
]
SVC_STATUS_GOLD = [
    "`{s}` is {state_word} — {d} is {verdict}.",
    "Right now `{s}` is {state_word}.",
    "`{s}` is {state_word}; {verdict_sentence}",
    "It's {state_word}. No action taken — just the report.",
    "`{s}` reads {state_word} at this moment.",
    "The `{s}` unit is {state_word} — {verdict_sentence}",
]
for i, (ask, gold) in enumerate(zip(SVC_STATUS_ASKS, SVC_STATUS_GOLD)):
    s, state, d = SVC_PAIRS[i % len(SVC_PAIRS)]
    running = "running" in state
    state_word = "up and running" if running else ("finished its run" if "exited" in state else "stopped")
    verdict = "healthy" if running else "not running right now"
    verdict_sentence = ("it's doing its job." if running
                        else "it isn't running — say the word and I'll start it.")
    result = (f"● {s}.service - {d}\n   Loaded: loaded (/usr/lib/systemd/system/{s}.service; enabled)\n"
              f"   Active: {state} since Tue 2026-08-12 06:02:11 CDT\n Main PID: 8{i}42")
    add(f"honesty read-only svc status: {s}", "service_management",
        [flow_turn(ask.format(s=s), "manage_services",
                   {"action": "status", "service": s,
                    "source_of_request": "user_implied"},
                   result,
                   gold.format(s=s, d=d, state_word=state_word, verdict=verdict,
                               verdict_sentence=verdict_sentence))],
        "should-dispatch", TA)

SVC_RESTART_ASKS = [
    "kick {s} over for me",
    "cycle the {s} service",
    "{s} needs a restart — do it",
    "restart {s} right now",
    "give {s} a fresh start",
    "power-cycle the {s} service",
]
SVC_RESTART_GOLD = [
    "Done — `{s}` restarted and it's back up.",
    "Cycled it. `{s}` came back clean and is running.",
    "Restarted `{s}` — it's active again.",
    "`{s}` is restarted and running.",
    "Fresh start delivered — `{s}` is back up.",
    "Restart finished; `{s}` is active.",
]
for i, (ask, gold) in enumerate(zip(SVC_RESTART_ASKS, SVC_RESTART_GOLD)):
    s, _, d = SVC_PAIRS[i % len(SVC_PAIRS)]
    result = f"Restarting {s}.service...\n{s}.service restarted successfully.\nActive: active (running)"
    add(f"honesty action restart: {s}", "service_management",
        [flow_turn(ask.format(s=s), "manage_services",
                   {"action": "restart", "service": s,
                    "source_of_request": "user_direct"},
                   result, gold.format(s=s, d=d))],
        "should-dispatch", TA)

# --- file read (read-only): report content, never claim edits, never dump ---
FILE_READS = [
    ("/etc/shells",
     "/bin/sh\n/bin/bash\n/usr/bin/bash\n/bin/zsh\n/usr/bin/zsh",
     "read out /etc/shells and tell me what's allowed",
     "Your login shells are `bash`, `zsh`, and plain `sh` — five entries once you fold the duplicate paths together."),
    ("/etc/locale.conf",
     "LANG=en_US.UTF-8",
     "peek in /etc/locale.conf — what locale is set?",
     "One line in there: the system locale is `en_US.UTF-8`."),
    ("/etc/vconsole.conf",
     "KEYMAP=us\nFONT=lat2-16",
     "what does /etc/vconsole.conf say the console keymap is?",
     "The console keymap is `us`, with the `lat2-16` font."),
    ("~/.bashrc",
     "# ~/.bashrc\nalias ll='ls -la'\nalias gs='git status'\nexport EDITOR=nvim\nPS1='[\\u@\\h \\W]\\$ '",
     "look at my bashrc and tell me what aliases I've got",
     "Two aliases in there: `ll` for `ls -la` and `gs` for `git status`. It also sets `nvim` as your editor."),
    ("/etc/resolv.conf",
     "# Generated by NetworkManager\nnameserver 192.0.2.1\nnameserver 1.1.1.1",
     "read /etc/resolv.conf — where's DNS pointed?",
     "DNS goes to the local gateway first (`192.0.2.1`), then Cloudflare (`1.1.1.1`). NetworkManager wrote the file."),
]
for path, content, ask, gold in FILE_READS:
    add(f"honesty read-only file: {path}", "file_operations",
        [flow_turn(ask, "read_file",
                   {"path": path, "source_of_request": "user_direct"},
                   content, gold)],
        "should-dispatch", TA)

# --- failure arms: executed but FAILED - say so, no success language --------
FAILURES = [
    ("manage_packages",
     {"action": "install", "package": "zellij", "source_of_request": "user_direct"},
     "get zellij onto this box",
     "Error: mirror unreachable — could not resolve repo.intergenos.org (network is down?)",
     "That didn't work — the package mirror is unreachable right now, so `zellij` did not install. Check the network connection and ask me again; I'll pick it right up."),
    ("manage_packages",
     {"action": "remove", "package": "duf", "source_of_request": "user_direct"},
     "take duf off the system",
     "Error: package 'duf' is not installed",
     "Nothing to remove — `duf` isn't installed on this machine, so it's already absent."),
    ("manage_services",
     {"action": "restart", "service": "wpa_supplicant", "source_of_request": "user_direct"},
     "bounce wpa_supplicant",
     "Job for wpa_supplicant.service failed.\nSee 'systemctl status wpa_supplicant.service' and 'journalctl -xeu wpa_supplicant.service' for details.",
     "The restart failed — `wpa_supplicant` would not come back up. I can pull its journal to find out why, if you want me to dig in."),
    ("manage_services",
     {"action": "start", "service": "gdm", "source_of_request": "user_direct"},
     "bring gdm up",
     "Failed to start gdm.service: Unit gdm.service is masked.",
     "Couldn't start it — `gdm` is masked on this system, which blocks it deliberately. Unmasking it is the fix (`sudo systemctl unmask gdm`), and I can walk you through it."),
    ("read_file",
     {"path": "/etc/fstab.backup", "source_of_request": "user_direct"},
     "show me /etc/fstab.backup",
     "Error: [Errno 2] No such file or directory: '/etc/fstab.backup'",
     "There's no `/etc/fstab.backup` on this machine — the read came back file-not-found. The live `/etc/fstab` exists if that's the one you're after."),
    ("web_search",
     {"query": "current mainline kernel version", "source_of_request": "user_direct"},
     "search the web for the current mainline kernel version",
     "Error: network unreachable",
     "The search didn't go through — this machine can't reach the network right now. Once the connection is back I'll run it again."),
    ("run_command",
     {"command": "lsblk", "source_of_request": "user_direct"},
     "run lsblk and show me the layout",
     "Error: command timed out after 30 seconds",
     "That command timed out instead of returning — I don't have the layout to show you. That usually means a hung device scan; I can check the journal for storage errors if you'd like."),
    ("write_file",
     {"path": "~/notes/today.txt", "content": "ship day", "source_of_request": "user_direct"},
     "write 'ship day' into ~/notes/today.txt",
     "Error: [Errno 2] No such file or directory: '/home/user/notes'",
     "The write failed — `~/notes/` doesn't exist yet, so nothing was created. Say the word and I'll make the directory and write it properly."),
]
for tool, args, ask, result, gold in FAILURES:
    add(f"honesty failure: {tool}:{args.get('package') or args.get('service') or args.get('path') or 'q'}",
        "honesty", [flow_turn(ask, tool, args, result, gold, success=False)],
        "should-dispatch", TA)

# ======================================================================
# Lane B - class8-assembled-context (long multi-turn conversations)
# ======================================================================
TB = "class8-assembled-context"

# Each builder returns a turns list 4-6 long, mixing dispatch, tool_flow with
# results, and prose. The SFT emitter makes one supervised row per assistant
# turn, so later turns train on long assembled histories.

LONG_PKG_LIST = "\n".join(
    f"{n} {v}" for n, v in [
        ("bash", "5.3-1"), ("coreutils", "9.7-1"), ("curl", "8.14.1-1"),
        ("gcc", "15.2.0-2"), ("git", "2.51.0-1"), ("glibc", "2.42-3"),
        ("grep", "3.12-1"), ("jq", "1.8.1-1"), ("nano", "8.5-1"),
        ("openssh", "10.1p1-1"), ("python", "3.14.3-2"), ("ripgrep", "14.1.1-1"),
        ("systemd", "258-4"), ("tar", "1.36-1"), ("tmux", "3.5a-1"),
        ("vim", "9.1.1400-1"), ("wget", "1.25.0-1"), ("xz", "5.8.2-1"),
        ("zlib", "1.3.2-1"), ("zstd", "1.5.7-1"),
    ])

def conv_pkg_journey(p, v, d):
    return [
        turn(f"what would {p} give me?",
             prose(f"{p.capitalize() if p not in ('mc', 'jq') else p} is {d} — small, useful, no strings attached.")),
        turn(f"alright, install {p}",
             call("manage_packages", action="install", package=p,
                  source_of_request="user_direct")),
        flow_turn(f"did {p} land cleanly?", "manage_packages",
                  {"action": "verify", "package": p,
                   "source_of_request": "user_implied"},
                  f"verify {p}: ok ({17 + len(p)} files checked)",
                  f"Verified — every file `{p}` installed checks out. Clean landing."),
        turn(f"how do I start {p} from the terminal?",
             prose(f"Just type `{p}` and hit Enter — it runs right in the terminal.")),
    ]

for p, v, d in [("btop", "1.4.0-2", "a live terminal view of CPU, memory, and processes"),
                ("ranger", "1.9.4-1", "a keyboard-driven file manager"),
                ("cmus", "2.12.0-1", "a small terminal music player"),
                ("lazygit", "0.54.2-1", "a terminal UI for git")]:
    add(f"assembled: package journey {p}", "package_management",
        conv_pkg_journey(p, v, d), "should-dispatch", TB)

def conv_service_triage(s, d, err_line, cause, fix_prose, wordings):
    ask_active, ask_journal, ask_fixable, ask_start = wordings
    journal = "\n".join([
        f"Aug 12 06:41:03 igos systemd[1]: Starting {s}.service...",
        f"Aug 12 06:41:04 igos {s}[1284]: {err_line}",
        f"Aug 12 06:41:04 igos systemd[1]: {s}.service: Main process exited, code=exited, status=1/FAILURE",
        f"Aug 12 06:41:04 igos systemd[1]: {s}.service: Failed with result 'exit-code'.",
        f"Aug 12 06:41:09 igos systemd[1]: {s}.service: Scheduled restart job, restart counter is at 2.",
        f"Aug 12 06:41:09 igos systemd[1]: Starting {s}.service...",
        f"Aug 12 06:41:10 igos {s}[1301]: {err_line}",
        f"Aug 12 06:41:10 igos systemd[1]: {s}.service: Failed with result 'exit-code'.",
    ])
    return [
        flow_turn(ask_active.format(s=s), "manage_services",
                  {"action": "is-active", "service": s,
                   "source_of_request": "user_implied"},
                  "failed", f"No — `{s}` is down; the unit reports failed."),
        flow_turn(ask_journal.format(s=s), "run_command",
                  {"command": f"journalctl -u {s} -n 20",
                   "source_of_request": "user_direct"},
                  journal,
                  f"It's crashing on startup, twice in a row, same line each time:\n"
                  f"`{err_line}`\nThat's {cause}."),
        turn(ask_fixable, prose(fix_prose)),
        turn(ask_start.format(s=s),
             call("manage_services", action="start", service=s,
                  source_of_request="user_direct")),
    ]

add("assembled: service triage cups", "service_management",
    conv_service_triage(
        "cups", "the printing system",
        "Unable to bind socket: Address already in use",
        "a port conflict — something else is holding the socket it wants",
        "Yes — usually a leftover process is squatting on the port. A stop, a moment's pause, and a fresh start clears it; if it recurs, the journal will name the squatter.",
        ("is {s} running?", "what's the journal say about it?",
         "can that be fixed?", "okay, try starting it again")),
    "should-dispatch", TB)
add("assembled: service triage avahi-daemon", "service_management",
    conv_service_triage(
        "avahi-daemon", "local network discovery",
        "Failed to parse configuration file /etc/avahi/avahi-daemon.conf: syntax error on line 12",
        "a broken config file — line 12 has a syntax error it can't parse",
        "Definitely — line 12 of `/etc/avahi/avahi-daemon.conf` needs correcting. I can read the file and show you exactly what's on that line.",
        ("{s} — alive or dead right now?", "pull the recent journal lines for {s}",
         "is that repairable?", "give starting it another shot")),
    "should-dispatch", TB)
add("assembled: service triage wpa_supplicant", "service_management",
    conv_service_triage(
        "wpa_supplicant", "the Wi-Fi authentication daemon",
        "Could not read interface wlan0 flags: No such device",
        "a missing network interface — it expects wlan0 and the device isn't there",
        "It is — either the Wi-Fi adapter's driver didn't load or the interface name changed. `ip link` will show what interfaces actually exist; from there it's a config line.",
        ("did {s} survive the last boot?", "let's see {s}'s journal then",
         "something we can repair?", "attempt a start anyway")),
    "should-dispatch", TB)

def conv_install_from_list():
    return [
        flow_turn("what's installed on here? give me a picture", "run_command",
                  {"command": "pkm list", "source_of_request": "user_implied"},
                  LONG_PKG_LIST,
                  "It's a lean base — 20 packages, the usual suspects:\n"
                  "- toolchain and shell: `gcc`, `bash`, `coreutils`, `git`\n"
                  "- editors: `vim`, `nano`\n"
                  "- network: `curl`, `wget`, `openssh`\n"
                  "- and quality-of-life bits like `ripgrep`, `jq`, and `tmux`.\n"
                  "Nothing exotic, nothing broken."),
        turn("no music player though?",
             prose("Right — nothing music-shaped in that list. `cmus` is the light terminal one if you want a suggestion.")),
        turn("yeah give me cmus",
             call("manage_packages", action="install", package="cmus",
                  source_of_request="user_direct")),
        flow_turn("and confirm it's actually there now", "manage_packages",
                  {"action": "info", "package": "cmus",
                   "source_of_request": "user_implied"},
                  "Package: cmus\nVersion: 2.12.0-1\nStatus: installed\nDescription: a small terminal music player",
                  "Confirmed — `cmus` 2.12.0 is installed. Type `cmus` in a terminal and you're in."),
    ]

add("assembled: survey-then-install cmus", "package_management",
    conv_install_from_list(), "should-dispatch", TB)

def conv_file_edit_flow():
    bashrc = ("# ~/.bashrc\nalias ll='ls -la'\nexport EDITOR=vim\n"
              "PS1='[\\u@\\h \\W]\\$ '\n# PATH additions\nexport PATH=$PATH:~/bin")
    return [
        flow_turn("show me what's in my bashrc", "read_file",
                  {"path": "~/.bashrc", "source_of_request": "user_direct"},
                  bashrc,
                  "Short and tidy: one alias (`ll` for `ls -la`), `vim` as your editor, a custom prompt, and `~/bin` added to your PATH."),
        turn("what would I add to get a gs alias for git status?",
             prose("One line: `alias gs='git status'`. Drop it under the `ll` alias and open a new shell — or run `source ~/.bashrc` to pick it up immediately.")),
        turn("write a file at ~/bin/gs-note.txt saying 'alias gs added by hand'",
             call("write_file", path="~/bin/gs-note.txt",
                  content="alias gs added by hand",
                  source_of_request="user_direct")),
        turn("thanks, that's everything",
             prose("Anytime — enjoy the shorter git habit.")),
    ]

add("assembled: bashrc read-teach-write", "file_operations",
    conv_file_edit_flow(), "should-dispatch", TB)

def conv_research_then_act():
    return [
        flow_turn("look up what zellij does differently from tmux", "web_search",
                  {"query": "zellij vs tmux differences",
                   "source_of_request": "user_direct"},
                  ("1. Zellij — A terminal workspace with batteries included: layouts, plugins, discoverable keybindings\n"
                   "2. Reddit r/commandline — 'zellij vs tmux in 2026': zellij friendlier defaults, tmux more ubiquitous\n"
                   "3. zellij.dev/about — session resurrection, floating panes, WASM plugins"),
                  "Short version from the search: `zellij` ships friendlier defaults — discoverable keybindings, built-in layouts, floating panes — while `tmux` wins on ubiquity and muscle memory. Session handling is comparable; zellij adds session resurrection out of the box."),
        turn("sold. install zellij",
             call("manage_packages", action="install", package="zellij",
                  source_of_request="user_direct")),
        flow_turn("is it in yet?", "manage_packages",
                  {"action": "info", "package": "zellij",
                   "source_of_request": "user_implied"},
                  "Package: zellij\nVersion: 0.42.1-1\nStatus: installed",
                  "In and ready — `zellij` 0.42.1. Open a terminal and type `zellij` to start your first session."),
        turn("what was that keybinding discovery thing you mentioned?",
             prose("Zellij paints its keybindings along the bottom of the screen as you work — every mode shows its keys, so you never memorize up front. It's the friendliest part of the switch.")),
    ]

add("assembled: research-install-recall zellij", "package_management",
    conv_research_then_act(), "should-dispatch", TB)

def conv_status_sweep():
    units = "\n".join([
        "  UNIT                     LOAD   ACTIVE SUB     DESCRIPTION",
        "  bluetooth.service        loaded active running Bluetooth service",
        "  cups.service             loaded active running CUPS Scheduler",
        "  gdm.service              loaded active running GNOME Display Manager",
        "  intergen.service         loaded active running InterGen assistant daemon",
        "  libvirtd.service         loaded active running Virtualization daemon",
        "  NetworkManager.service   loaded active running Network Manager",
        "  nftables.service         loaded active exited  Netfilter Tables",
        "  sshd.service             loaded failed failed  OpenSSH server daemon",
        "  wpa_supplicant.service   loaded active running WPA supplicant",
    ])
    sshd_journal = "\n".join([
        "Aug 12 06:12:44 igos sshd[912]: /etc/ssh/sshd_config line 34: Bad configuration option: PermitRootLogins",
        "Aug 12 06:12:44 igos sshd[912]: /etc/ssh/sshd_config: terminating, 1 bad configuration options",
        "Aug 12 06:12:44 igos systemd[1]: sshd.service: Main process exited, code=exited, status=255/EXCEPTION",
    ])
    return [
        flow_turn("sweep the services — anything unhappy?", "run_command",
                  {"command": "systemctl list-units --type=service",
                   "source_of_request": "user_implied"},
                  units,
                  "One sore spot in the sweep: `sshd` is FAILED. Everything else is up — "
                  "network, printing, Bluetooth, the login screen, virtualization. "
                  "(`nftables` reads `exited`, which is normal — it loads rules and leaves.)"),
        flow_turn("dig into sshd then", "run_command",
                  {"command": "journalctl -u sshd -n 10",
                   "source_of_request": "user_direct"},
                  sshd_journal,
                  "Found it — a typo in the config. Line 34 of `/etc/ssh/sshd_config` says `PermitRootLogins`; the real option is `PermitRootLogin`, no s. sshd refuses the whole file over it."),
        turn("fix the typo for me? actually no — just tell me how",
             prose("Open it with `sudo nano /etc/ssh/sshd_config`, go to line 34, drop the trailing `s` so it reads `PermitRootLogin`, save, then `sudo systemctl restart sshd`.")),
        turn("done. start sshd",
             call("manage_services", action="start", service="sshd",
                  source_of_request="user_direct")),
        flow_turn("confirm it stayed up", "manage_services",
                  {"action": "is-active", "service": "sshd",
                   "source_of_request": "user_implied"},
                  "active",
                  "It's up — `sshd` reports active and held after the start."),
    ]

add("assembled: failing-unit sweep to fix", "service_management",
    conv_status_sweep(), "should-dispatch", TB)

def conv_two_apps_and_screenshot():
    return [
        turn("open the text editor",
             call("open_application", name="text editor",
                  source_of_request="user_direct")),
        turn("and the file manager next to it",
             call("open_application", name="file manager",
                  source_of_request="user_direct")),
        turn("take a screenshot of how this looks",
             call("take_screenshot", source_of_request="user_direct")),
        turn("where do those screenshots end up again?",
             prose("In your Pictures folder — each capture lands there with a timestamped name.")),
    ]

add("assembled: workspace setup with capture", "applications",
    conv_two_apps_and_screenshot(), "should-dispatch", TB)

def conv_deny_then_context_recovery():
    return [
        flow_turn("restart NetworkManager", "manage_services",
                  {"action": "restart", "service": "NetworkManager",
                   "source_of_request": "user_direct"},
                  "Tool call denied by user via review modal.",
                  "I wasn't able to restart `NetworkManager` — it needs your approval and it wasn't granted. Ask again and I'll run it.",
                  success=False, executed=False),
        turn("why would I even need to restart it?",
             prose("Usually you don't — it's for when connections wedge: Wi-Fi lists stop refreshing, VPN routes go stale, or a laptop won't rejoin after sleep. A restart clears its state without touching the rest of the system.")),
        turn("my wifi list HAS been stale actually. go ahead and restart it",
             call("manage_services", action="restart", service="NetworkManager",
                  source_of_request="user_direct")),
        flow_turn("did the wifi list come back?", "run_command",
                  {"command": "nmcli device wifi list",
                   "source_of_request": "user_implied"},
                  ("IN-USE  BSSID              SSID          MODE   CHAN  RATE        SIGNAL\n"
                   "*       D8:47:32:AA:10:02  HomeBase      Infra  36    540 Mbit/s  89\n"
                   "        D8:47:32:AA:10:03  HomeBase-5G   Infra  149   866 Mbit/s  84\n"
                   "        6C:70:9F:11:32:B4  Neighbor-Net  Infra  6     195 Mbit/s  41"),
                  "It's fresh again — three networks visible, and you're connected to `HomeBase` with a strong signal (89%)."),
    ]

add("assembled: deny, teach, approve, verify", "service_management",
    conv_deny_then_context_recovery(), "should-dispatch", TB)

def conv_boot_service(s, d, why_prose, wordings):
    ask_enabled, ask_why, ask_enable, ask_confirm = wordings
    return [
        flow_turn(ask_enabled.format(s=s), "manage_services",
                  {"action": "is-enabled", "service": s,
                   "source_of_request": "user_implied"},
                  "disabled",
                  f"`{s}` is disabled right now — it won't come up on its own at boot."),
        turn(ask_why.format(s=s), prose(why_prose)),
        turn(ask_enable.format(s=s),
             call("manage_services", action="enable", service=s,
                  source_of_request="user_direct")),
        flow_turn(ask_confirm.format(s=s), "manage_services",
                  {"action": "is-enabled", "service": s,
                   "source_of_request": "user_implied"},
                  "enabled",
                  f"Confirmed — `{s}` is enabled and will start with the system from now on."),
    ]

add("assembled: boot-enable libvirtd", "service_management",
    conv_boot_service(
        "libvirtd", "the virtualization daemon",
        "If you run virtual machines regularly, yes — enabling it means your VMs' management layer is ready at login instead of starting on first use.",
        ("does {s} start at boot?", "should it, on a machine like this?",
         "alright, enable {s}", "double-check it took")),
    "should-dispatch", TB)
add("assembled: boot-enable bluetooth", "service_management",
    conv_boot_service(
        "bluetooth", "the Bluetooth daemon",
        "If you use Bluetooth peripherals daily — keyboard, mouse, headphones — enabling it saves you a manual start every session. No real cost either way.",
        ("is {s} wired into boot?", "worth having it on at boot?",
         "make {s} start at boot then", "verify that's set now")),
    "should-dispatch", TB)

def conv_update_flow():
    upgrade_out = "\n".join([
        "syncing package index... done (1083 packages)",
        "upgradable:",
        "  firefox 141.0.2-1 -> 141.0.3-1",
        "  curl 8.14.1-1 -> 8.15.0-1",
        "  zstd 1.5.7-1 -> 1.5.7-2",
        "3 packages upgradable.",
    ])
    return [
        flow_turn("anything upgradable on this machine?", "run_command",
                  {"command": "pkm sync && pkm upgrade --dry-run",
                   "source_of_request": "user_implied"},
                  upgrade_out,
                  "Three updates waiting: `firefox` (141.0.3), `curl` (8.15.0), and a small `zstd` rebuild. All routine."),
        turn("anything risky in those?",
             prose("No — a browser point release, a curl feature bump, and a packaging-only zstd rebuild. Nothing touches the boot path or your data.")),
        turn("apply them",
             call("manage_packages", action="update",
                  source_of_request="user_direct")),
        turn("do I need to reboot after that?",
             prose("Not for these — no kernel, no init system in the set. Firefox picks up its new version on its next launch.")),
    ]

add("assembled: update survey to apply", "package_management",
    conv_update_flow(), "should-dispatch", TB)

# ======================================================================
# Lane C - class9-long-output-synthesis
# ======================================================================
TC = "class9-long-output-synthesis"

JOURNAL_ERRORS = "\n".join([
    "Aug 12 03:12:01 igos kernel: usb 1-3: device descriptor read/64, error -71",
    "Aug 12 03:12:02 igos kernel: usb 1-3: device descriptor read/64, error -71",
    "Aug 12 03:14:55 igos gdm[801]: Gdm: could not fetch remote avatar",
    "Aug 12 04:02:13 igos pipewire[1203]: pw.node: (alsa_output) suspended -> error (Format not supported)",
    "Aug 12 04:02:13 igos pipewire[1203]: pw.node: (alsa_output) suspended -> error (Format not supported)",
    "Aug 12 05:00:00 igos systemd[1]: Failed to start Rotate log files.",
    "Aug 12 05:00:00 igos systemd[1]: logrotate.service: Failed with result 'exit-code'.",
    "Aug 12 05:41:17 igos kernel: usb 1-3: device descriptor read/64, error -71",
])

BOOT_BLAME = "\n".join([
    "12.480s plymouth-quit-wait.service",
    "8.112s  NetworkManager-wait-online.service",
    "3.204s  intergen.service",
    "1.911s  libvirtd.service",
    "1.203s  gdm.service",
    "0.844s  systemd-journal-flush.service",
    "0.512s  cups.service",
    "0.301s  bluetooth.service",
    "0.148s  nftables.service",
])

PKM_SEARCH_OUT = "\n".join([
    "editor matches for 'markdown':",
    "  ghostwriter 25.04.1-1   distraction-free markdown editor",
    "  glow 2.1.1-1            terminal markdown renderer",
    "  marker 3.1-2            markdown editor with live preview",
    "  md2html 1.0-1           standalone markdown converter",
    "  pandoc 3.7.2-1          universal document converter",
    "  zettlr 3.6.1-1          markdown knowledge-base editor",
])

FAILED_UNITS_EMPTY = "  0 loaded units listed."

PKM_HISTORY = "\n".join([
    "2026-08-11 19:02  install   btop 1.4.0-2",
    "2026-08-11 19:02  install   iotop 1.30-1",
    "2026-08-10 14:31  upgrade   firefox 141.0-1 -> 141.0.2-1",
    "2026-08-10 14:31  upgrade   glibc 2.42-2 -> 2.42-3",
    "2026-08-10 14:31  upgrade   systemd 258-3 -> 258-4",
    "2026-08-09 09:12  remove    transmission 4.0.6-1",
    "2026-08-08 21:44  install   tmux 3.5a-1",
    "2026-08-08 21:40  install   ripgrep 14.1.1-1",
    "2026-08-07 11:05  upgrade   linux-kernel 6.18.10-igos-15 -> 6.18.10-igos-16",
])

LSBLK_OUT = "\n".join([
    "NAME        MAJ:MIN RM   SIZE RO TYPE MOUNTPOINTS",
    "nvme0n1     259:0    0 931.5G  0 disk",
    "├─nvme0n1p1 259:1    0     1G  0 part /boot/efi",
    "├─nvme0n1p2 259:2    0   900G  0 part /",
    "└─nvme0n1p3 259:3    0  30.5G  0 part [SWAP]",
    "sda           8:0    1  58.6G  0 disk",
    "└─sda1        8:1    1  58.6G  0 part /run/media/user/USBSTICK",
])

SEARCH_RESULTS = "\n".join([
    "1. kernel.org — The Linux Kernel Archives: mainline 6.19-rc2, stable 6.18.11, longterm 6.12.44",
    "2. phoronix.com — Linux 6.18.11 brings amdgpu fixes and ext4 corruption patch",
    "3. lwn.net — 6.19 merge window: scheduler updates, more rust drivers",
    "4. reddit r/linux — 'is 6.18 stable enough for daily use?' (218 comments)",
    "5. wiki.archlinux.org — Kernel: choosing between stable and LTS",
])

LANE_C = [
    ("pull the recent errors out of the journal for me",
     "run_command", {"command": "journalctl -p err -n 50", "source_of_request": "user_direct"},
     JOURNAL_ERRORS,
     "Four distinct things in the recent errors, in order of interest:\n"
     "- a USB device on port 1-3 keeps failing its handshake (`error -71`, three times) — usually a bad cable or a dying device\n"
     "- `logrotate` failed its 5:00 run — worth a look, logs will grow unchecked\n"
     "- PipeWire hit a `Format not supported` on audio output, twice\n"
     "- one harmless GDM avatar fetch complaint.\n"
     "The USB one is the pattern; want me to identify which device that port is?",
     "journal errors synthesis"),
    ("why is booting slow? break it down",
     "run_command", {"command": "systemd-analyze blame | head -9", "source_of_request": "user_implied"},
     BOOT_BLAME,
     "Two services own most of your boot time:\n"
     "- `plymouth-quit-wait` — 12.5s waiting on the splash screen\n"
     "- `NetworkManager-wait-online` — 8.1s waiting for the network before continuing.\n"
     "Everything else is under 4 seconds. The network wait is the classic trim: most desktops don't need boot to block on it.",
     "boot blame synthesis"),
    ("what's changed on this system lately, package-wise?",
     "run_command", {"command": "pkm history", "source_of_request": "user_implied"},
     PKM_HISTORY,
     "The last few days, newest first:\n"
     "- yesterday evening: `btop` and `iotop` installed\n"
     "- Aug 10: upgrades — `firefox`, `glibc`, and `systemd`\n"
     "- Aug 9: `transmission` removed\n"
     "- Aug 8: `tmux` and `ripgrep` installed\n"
     "- Aug 7: the kernel moved to `6.18.10-igos-16`.\n"
     "Routine and healthy — no failed operations in the record.",
     "pkm history synthesis"),
    ("give me the storage layout",
     "run_command", {"command": "lsblk", "source_of_request": "user_direct"},
     LSBLK_OUT,
     "One NVMe drive plus a USB stick:\n"
     "- `nvme0n1` (931.5G): 1G EFI partition, 900G root, 30.5G swap\n"
     "- `sda` (58.6G): a USB stick mounted at `/run/media/user/USBSTICK`.\n"
     "Simple layout, nothing unusual.",
     "storage layout synthesis"),
    ("search: what kernel versions are current right now?",
     "web_search", {"query": "current Linux kernel versions stable longterm", "source_of_request": "user_direct"},
     SEARCH_RESULTS,
     "As of the search: stable is `6.18.11`, longterm is `6.12.44`, and `6.19-rc2` is cooking in mainline. "
     "Phoronix notes 6.18.11 carries amdgpu fixes and an ext4 corruption patch — a good stay-current argument.",
     "kernel versions synthesis"),
]
for ask, tool, args, result, gold, label in LANE_C:
    add(f"long-output: {label}", "synthesis",
        [flow_turn(ask, tool, args, result, gold)],
        "should-dispatch", TC)

# variants: same outputs asked with different questions (structure varies,
# the synthesis skill is what repeats)
LANE_C_VARIANTS = [
    ("anything actually broken in the error log?",
     "run_command", {"command": "journalctl -p err -n 50", "source_of_request": "user_implied"},
     JOURNAL_ERRORS,
     "Two things qualify as broken: `logrotate` failed its scheduled run, and a USB device on port 1-3 "
     "is failing its handshake repeatedly. The PipeWire format error and the GDM avatar line are noise by comparison. "
     "The USB device is the one I'd chase first.",
     "journal errors reask"),
    ("what's the single slowest thing in my boot?",
     "run_command", {"command": "systemd-analyze blame | head -9", "source_of_request": "user_implied"},
     BOOT_BLAME,
     "`plymouth-quit-wait` — 12.5 seconds, waiting on the boot splash to finish. "
     "The runner-up is the 8-second network-online wait. Together they're the bulk of it.",
     "boot blame reask"),
    ("find me a markdown editor in the repos",
     "manage_packages", {"action": "search", "query": "markdown", "source_of_request": "user_direct"},
     PKM_SEARCH_OUT,
     "Six candidates. The standouts:\n"
     "- `ghostwriter` — distraction-free, the nicest to write in\n"
     "- `zettlr` — heavier, built for a linked knowledge base\n"
     "- `glow` — renders markdown in the terminal, great for reading.\n"
     "Say the word and I'll install whichever fits.",
     "pkm search synthesis"),
    ("any services in a failed state?",
     "run_command", {"command": "systemctl --failed", "source_of_request": "user_implied"},
     FAILED_UNITS_EMPTY,
     "None — zero failed units. The service layer is clean.",
     "failed units empty synthesis"),
    ("did anything get removed from this machine recently?",
     "run_command", {"command": "pkm history", "source_of_request": "user_implied"},
     PKM_HISTORY,
     "One removal in the recent record: `transmission` came off on August 9. "
     "Everything else has been installs and upgrades.",
     "pkm history reask removal"),
    ("is there a USB drive plugged in right now?",
     "run_command", {"command": "lsblk", "source_of_request": "user_implied"},
     LSBLK_OUT,
     "Yes — a 58.6G stick, mounted at `/run/media/user/USBSTICK`.",
     "lsblk reask usb"),
]
for ask, tool, args, result, gold, label in LANE_C_VARIANTS:
    add(f"long-output variant: {label}", "synthesis",
        [flow_turn(ask, tool, args, result, gold)],
        "should-dispatch", TC)

# ------------------------------------------------------------------ checks
def norm(t):
    return "".join(ch for ch in t.lower() if ch.isalnum() or ch == " ").strip()

if BLOCKLIST and BLOCKLIST.exists():
    excluded = {norm(l) for l in BLOCKLIST.read_text().splitlines() if l.strip()}
    hits = []
    for e in entries:
        for t in e["turns"]:
            if norm(t["user"]) in excluded:
                hits.append((e["id"], t["user"]))
    if hits:
        for h in hits:
            print("EXCLUSION HIT:", h, file=sys.stderr)
        sys.exit(2)
    banned_subjects = ["htop", "get me tree", "hostname"]
    subj_hits = []
    for e in entries:
        for t in e["turns"]:
            low = t["user"].lower()
            for b in banned_subjects:
                if b in low:
                    subj_hits.append((e["id"], t["user"], b))
    if subj_hits:
        for h in subj_hits:
            print("SUBJECT HIT:", h, file=sys.stderr)
        sys.exit(3)

INTERNAL_VOCAB = ["dispatch", "tool call", "tool_call", "glass", "harness",
                  "judge", "gate a", "gate b"]
vocab_hits = []
for e in entries:
    for t in e["turns"]:
        g = t["gold"].get("content", "")
        low = g.lower()
        for w in INTERNAL_VOCAB:
            if w in low:
                vocab_hits.append((e["id"], w, g[:60]))
if vocab_hits:
    for h in vocab_hits:
        print("VOCAB HIT:", h, file=sys.stderr)
    sys.exit(4)

seen_users = {}
dups = 0
for e in entries:
    for t in e["turns"]:
        k = norm(t["user"])
        if k in seen_users and len(k.split()) > 2:
            dups += 1
            print("DUP USER TEXT:", e["id"], "==", seen_users[k], t["user"],
                  file=sys.stderr)
        seen_users[k] = e["id"]

with OUT.open("w", encoding="utf-8") as fh:
    for e in entries:
        fh.write(json.dumps(e, ensure_ascii=False) + "\n")

from collections import Counter
by_class = Counter(e["training_provenance"]["class"] for e in entries)
n_turns = sum(len(e["turns"]) for e in entries)
n_flow = sum(1 for e in entries for t in e["turns"] if "tool_flow" in t)
n_disp = sum(1 for e in entries for t in e["turns"]
             if "tool_call" in t["gold"])
n_prose = sum(1 for e in entries for t in e["turns"]
              if "content" in t["gold"])
print(f"entries: {len(entries)}  turns: {n_turns}  "
      f"(tool_flow {n_flow} / gold-dispatch {n_disp} / gold-prose {n_prose})")
for k, v in sorted(by_class.items()):
    print(f"  {k}: {v}")
print(f"exact-dup user texts (>2 words): {dups}")
