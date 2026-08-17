#!/usr/bin/env python3
"""Round-1 class-1 training-bank generator (imperative -> dispatch + contrastive twins).

Authored 2026-08-11 for the round-1 training-set lane (private spec:
docs/plans/2026-08-11-round1-training-set-authoring-spec.md, class 1 + §6 rules).

Design constraints honored here:
- Structural variation, never synonym swaps: every template FAMILY below is a
  distinct linguistic structure; objects vary within a family, and each family
  uses only a slice of the object pool (rotated) so no two entries share both
  structure and object.
- Held-out/validation exclusion: no authored user text may match (normalized)
  any excluded eval-cell text; subjects of held-out families (htop, tree,
  disk-usage-in-natural-language, memory, hostname-files) are avoided outright.
- Gold dispatches use ONLY the real tool schemas (validated downstream by
  corpus_to_sft against live discovery) and every dispatch carries
  source_of_request per D-008 (user_direct for imperatives, user_implied for
  question-form checks).
- Prose gold (twins) is persona-voiced (warm, direct, tight), wraps commands in
  backticks per the serving prompt's own rule, teaches ONLY verified-true facts
  (pkm subcommands from pkm/cli.py; systemctl; cups/NetworkManager/bluez ship
  in-tree), and never appends a confirm-gated offer (that is class 5's shape).
"""
import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("class1_bank.jsonl")
BLOCKLIST = Path(sys.argv[2]) if len(sys.argv) > 2 else None

# ---------------------------------------------------------------- object pools
PKGS = ["btop", "iotop", "glances", "ranger", "mc", "tmux", "zellij", "neovim",
        "ripgrep", "fd", "bat", "fzf", "jq", "yq", "duf", "dust", "lazygit",
        "tig", "cmus", "mpv"]
PKG_DESC = {
    "btop": "a terminal resource monitor — CPU, memory, processes, all live",
    "iotop": "a monitor that shows which processes are doing disk I/O",
    "glances": "an all-in-one terminal system monitor",
    "ranger": "a keyboard-driven file manager for the terminal",
    "mc": "Midnight Commander, a two-pane terminal file manager",
    "tmux": "a terminal multiplexer — split panes and sessions that survive disconnects",
    "zellij": "a terminal workspace manager, in the tmux family",
    "neovim": "a modal text editor, the modern Vim",
    "ripgrep": "a very fast recursive text search tool (the `rg` command)",
    "fd": "a fast, friendly alternative to `find`",
    "bat": "`cat` with syntax highlighting and paging",
    "fzf": "a fuzzy finder you can pipe anything through",
    "jq": "a command-line JSON processor",
    "yq": "a command-line YAML processor, jq's sibling",
    "duf": "a clean disk free/usage viewer",
    "dust": "a more readable `du` — it shows where space went",
    "lazygit": "a terminal UI for git",
    "tig": "a text-mode interface for browsing git history",
    "cmus": "a small, fast terminal music player",
    "mpv": "a lean, powerful media player",
}
SVCS = ["bluetooth", "cups", "NetworkManager", "avahi-daemon", "libvirtd",
        "wpa_supplicant", "gdm", "nftables"]
FILES = ["/etc/hosts", "/etc/resolv.conf", "/etc/shells", "/etc/locale.conf",
         "/etc/vconsole.conf", "~/.bashrc"]
APPS = ["calculator", "text editor", "file manager", "firefox", "terminal",
        "settings", "image viewer", "system monitor"]

entries = []
_counter = [0]


def add(intent, category, turns, ebc, tclass, *, grounding=None):
    _counter[0] += 1
    entries.append({
        "id": f"tb1-{_counter[0]:04d}",
        "category": category,
        "intent": intent,
        "expected_behavior_class": ebc,
        "turns": turns,
        "provenance": {
            "generator": "round1-class1-authoring",
            "lens": "imperative-vs-informational",
            "grounding": grounding or ["intergen-tool-registry"],
            "method": "structural-template-authored",
        },
        "training_provenance": {"class": tclass, "origin": "authored"},
    })


def turn(user, gold):
    return {"user": user, "gold": gold}


def call(_tool, **args):
    return {"tool_call": {"name": _tool, "arguments": args}}


def prose(text):
    return {"content": text}


def rotate(pool, family_idx, n):
    """A deterministic slice of the pool, offset per family so structure x
    object pairs never repeat and every object gets coverage."""
    out = []
    for j in range(n):
        out.append(pool[(family_idx * 3 + j * 5) % len(pool)])
    return list(dict.fromkeys(out))[:n]


# ============================== TARGETS: imperative -> dispatch ==============
TCLASS = "class1-imperative-dispatch"

# --- manage_packages: install (12 families x 3 objects)
INSTALL_FAMS = [
    "Install {p}",
    "install {p} please",
    "Can you install {p} for me?",
    "throw {p} on here",
    "I need {p} on this machine",
    "I'd like to have {p} installed",
    "go ahead and add {p}",
    "set me up with {p}",
    "{p} isn't on here — fix that",
    "hey, mind installing {p}?",
    "need {p} asap",
    "add {p} to this system",
]
for i, fam in enumerate(INSTALL_FAMS):
    for p in rotate(PKGS, i, 3):
        add(f"install imperative: {p}", "package_management",
            [turn(fam.format(p=p),
                  call("manage_packages", action="install", package=p,
                       source_of_request="user_direct"))],
            "should-dispatch", TCLASS)

# --- manage_packages: remove (8 families x 3)
REMOVE_FAMS = [
    "remove {p}",
    "get rid of {p}",
    "I don't need {p} anymore — take it off",
    "yank {p} off this system",
    "{p} can go",
    "please remove {p} from this machine",
    "drop {p}, I never use it",
    "clean {p} off of here",
]
for i, fam in enumerate(REMOVE_FAMS):
    for p in rotate(PKGS, i + 12, 3):
        add(f"remove imperative: {p}", "package_management",
            [turn(fam.format(p=p),
                  call("manage_packages", action="remove", package=p,
                       source_of_request="user_direct"))],
            "should-dispatch", TCLASS)

# --- manage_packages: update (6 families, mixed single/whole-system)
UPDATE_SINGLE_FAMS = [
    "update {p}",
    "get {p} to the latest version",
    "bring {p} up to date",
]
UPDATE_ALL_FAMS = [
    "update everything",
    "bring the whole system up to date",
    "run updates on this machine",
]
for i, fam in enumerate(UPDATE_SINGLE_FAMS):
    for p in rotate(PKGS, i + 20, 2):
        add(f"update imperative: {p}", "package_management",
            [turn(fam.format(p=p),
                  call("manage_packages", action="update", package=p,
                       source_of_request="user_direct"))],
            "should-dispatch", TCLASS)
for fam in UPDATE_ALL_FAMS:
    add("update imperative: whole system", "package_management",
        [turn(fam, call("manage_packages", action="update",
                        source_of_request="user_direct"))],
        "should-dispatch", TCLASS)

# --- manage_packages: question-form checks (user_implied) (8 families)
PKG_CHECK_FAMS = [
    ("is {p} installed?", "info"),
    ("do we have {p} on here?", "info"),
    ("what version of {p} is on this machine?", "info"),
    ("is there a package for {p}?", "search"),
    ("anything in the repos for {p}?", "search"),
    ("can you check whether {p} made it onto this install?", "info"),
    ("did {p} install cleanly? verify it", "verify"),
    ("give me the details on the {p} package", "info"),
]
for i, (fam, action) in enumerate(PKG_CHECK_FAMS):
    for p in rotate(PKGS, i + 25, 3):
        args = {"source_of_request": "user_implied"}
        if action == "search":
            args["query"] = p
        else:
            args["package"] = p
        add(f"package check ({action}): {p}", "package_management",
            [turn(fam.format(p=p),
                  call("manage_packages", action=action, **args))],
            "should-dispatch", TCLASS)

# --- manage_services: state changes (12 families x 3)
SVC_FAMS = [
    ("restart {s}", "restart"),
    ("please restart the {s} service", "restart"),
    ("bounce {s} for me", "restart"),
    ("{s} is acting up — restart it", "restart"),
    ("start {s}", "start"),
    ("bring {s} up", "start"),
    ("stop {s}", "stop"),
    ("shut {s} down for now", "stop"),
    ("enable {s} so it starts at boot", "enable"),
    ("make sure {s} comes up on boot", "enable"),
    ("disable {s} — I don't want it starting on its own", "disable"),
    ("reload {s}'s config", "reload"),
]
for i, (fam, action) in enumerate(SVC_FAMS):
    for s in rotate(SVCS, i, 3):
        add(f"service {action}: {s}", "service_management",
            [turn(fam.format(s=s),
                  call("manage_services", action=action, service=s,
                       source_of_request="user_direct"))],
            "should-dispatch", TCLASS)

# --- manage_services: question-form checks (user_implied) (6 families x 2)
SVC_CHECK_FAMS = [
    ("is {s} running?", "is-active"),
    ("what's the state of {s}?", "status"),
    ("is {s} set to start at boot?", "is-enabled"),
    ("did {s} come up after the last boot?", "status"),
    ("can you check on {s}?", "status"),
    ("{s} — up or down right now?", "is-active"),
]
for i, (fam, action) in enumerate(SVC_CHECK_FAMS):
    for s in rotate(SVCS, i + 12, 2):
        add(f"service check ({action}): {s}", "service_management",
            [turn(fam.format(s=s),
                  call("manage_services", action=action, service=s,
                       source_of_request="user_implied"))],
            "should-dispatch", TCLASS)

# --- read_file (8 families x 2)
READ_FAMS = [
    "show me {f}",
    "read {f} for me",
    "what's in {f}?",
    "print the contents of {f}",
    "let me see {f}",
    "open up {f} and show me what it says",
    "cat {f}",
    "pull up {f}",
]
for i, fam in enumerate(READ_FAMS):
    for f in rotate(FILES, i, 2):
        add(f"read file: {f}", "file_operations",
            [turn(fam.format(f=f),
                  call("read_file", path=f, source_of_request="user_direct"))],
            "should-dispatch", TCLASS)

# --- analyze_file (6 families x 2) — the diagnose-vs-value phrasing target
ANALYZE_FAMS = [
    ("check {f} for problems", "Are there any problems or misconfigurations in this file?"),
    ("does {f} look right to you?", "Does this file look correct and well-formed?"),
    ("can you look over {f} and tell me if anything's wrong?", "Is anything wrong or suspicious in this file?"),
    ("sanity-check {f} for me", "Sanity-check this file: is it valid and sensible?"),
    ("is my {f} sane?", "Is this file valid and sensibly configured?"),
    ("give {f} a once-over", "Review this file for errors or anything unusual."),
]
for i, (fam, q) in enumerate(ANALYZE_FAMS):
    for f in rotate(FILES, i + 8, 2):
        add(f"diagnose file: {f}", "file_operations",
            [turn(fam.format(f=f),
                  call("analyze_file", path=f, question=q,
                       source_of_request="user_direct"))],
            "should-dispatch", TCLASS)

# --- run_command (6 one-off shapes; held-out disk/memory semantics avoided)
RUN_CMDS = [
    ("run uname -r for me", "uname -r"),
    ("what kernel are we on? run uname -a", "uname -a"),
    ("give me the uptime", "uptime"),
    ("run ip addr and show me the output", "ip addr"),
    ("list the block devices", "lsblk"),
    ("show me the last 20 error lines from the journal", "journalctl -p err -n 20"),
]
for fam, cmd in RUN_CMDS:
    add(f"run command: {cmd}", "system_commands",
        [turn(fam, call("run_command", command=cmd,
                        source_of_request="user_direct"))],
        "should-dispatch", TCLASS)

# --- open_application (8)
OPEN_FAMS = [
    "open the {a}",
    "launch {a}",
    "fire up the {a}",
    "start {a} for me",
    "bring up the {a}",
    "can you open {a}?",
    "I want the {a} open",
    "pop open the {a}",
]
for i, fam in enumerate(OPEN_FAMS):
    a = APPS[i % len(APPS)]
    add(f"open app: {a}", "applications",
        [turn(fam.format(a=a),
              call("open_application", name=a,
                   source_of_request="user_direct"))],
        "should-dispatch", TCLASS)

# --- take_screenshot (5)
for fam in ["take a screenshot",
            "grab a screenshot of my screen",
            "screenshot this for me",
            "capture the screen real quick",
            "snap a screenshot"]:
    add("screenshot imperative", "applications",
        [turn(fam, call("take_screenshot", source_of_request="user_direct"))],
        "should-dispatch", TCLASS)

# --- web_search (8)
SEARCHES = [
    ("search the web for the latest stable kernel release", "latest stable Linux kernel release"),
    ("look up what Wayland fractional scaling means", "Wayland fractional scaling explained"),
    ("find out when Python 3.14 came out", "Python 3.14 release date"),
    ("search for btrfs vs ext4 comparisons", "btrfs vs ext4 comparison"),
    ("web-search the current LTS kernel version", "current LTS Linux kernel version"),
    ("look up the systemd release notes for the newest version", "latest systemd release notes"),
    ("search the web: best terminal fonts for coding", "best terminal fonts for programming"),
    ("can you look up what zram is used for?", "what is zram used for Linux"),
]
for fam, q in SEARCHES:
    add(f"web search: {q[:30]}", "web",
        [turn(fam, call("web_search", query=q,
                        source_of_request="user_direct"))],
        "should-dispatch", TCLASS)

# --- write_file (6)
WRITES = [
    ("create a file at ~/todo.txt with the line 'buy milk'", "~/todo.txt", "buy milk"),
    ("make a notes file at ~/notes/ideas.txt containing 'app: pomodoro timer'", "~/notes/ideas.txt", "app: pomodoro timer"),
    ("write 'backup ran OK' into ~/backup-status.txt", "~/backup-status.txt", "backup ran OK"),
    ("save a file ~/scratch/hello.txt that just says 'hello'", "~/scratch/hello.txt", "hello"),
    ("put the text 'call the dentist' in a new file ~/reminders.txt", "~/reminders.txt", "call the dentist"),
    ("make ~/projects/README.md with the single line '# projects'", "~/projects/README.md", "# projects"),
]
for fam, path, content in WRITES:
    kwargs = {"path": path, "content": content, "source_of_request": "user_direct"}
    if "/" in path.rsplit("~/", 1)[-1]:
        kwargs["create_dirs"] = True
    add(f"write file: {path}", "file_operations",
        [turn(fam, call("write_file", **kwargs))],
        "should-dispatch", TCLASS)

# --- two-turn: informational turn then imperative follow-through (18)
TT_PKG = rotate(PKGS, 7, 12)
TT_OPENERS = [
    "what's {p}?",
    "what does {p} actually do?",
    "is {p} any good?",
]
TT_FOLLOWUPS = [
    "nice, install it",
    "sold — put it on",
    "okay, install that",
    "yeah I want that, install it",
]
for j, p in enumerate(TT_PKG):
    opener = TT_OPENERS[j % len(TT_OPENERS)]
    follow = TT_FOLLOWUPS[j % len(TT_FOLLOWUPS)]
    add(f"two-turn describe-then-install: {p}", "package_management",
        [turn(opener.format(p=p), prose(f"{p.capitalize() if p != 'mc' else p} is {PKG_DESC[p]}.")),
         turn(follow,
              call("manage_packages", action="install", package=p,
                   source_of_request="user_direct"))],
        "should-dispatch", TCLASS)

TT_SVC = [("which service handles printing here?",
           "Printing goes through `cups` — it manages the queues and talks to your printers.",
           "restart it", "cups"),
          ("what manages my network connections on this system?",
           "`NetworkManager` — it owns your wired and wireless connections.",
           "give it a restart", "NetworkManager"),
          ("what's the service for bluetooth?",
           "That's `bluetooth` — the BlueZ daemon behind your adapters and pairings.",
           "restart it please", "bluetooth"),
          ("which service runs the login screen?",
           "`gdm` — the GNOME display manager. Careful: restarting it ends your graphical session.",
           "understood — restart it anyway", "gdm"),
          ("what service does local network discovery?",
           "`avahi-daemon` — it handles mDNS, the .local names on your LAN.",
           "stop it for now", "avahi-daemon"),
          ("which service is the firewall on this machine?",
           "`nftables` — it loads and enforces the packet filter rules.",
           "reload it", "nftables")]
TT_SVC_ACTIONS = {"restart it": "restart", "give it a restart": "restart",
                  "restart it please": "restart",
                  "understood — restart it anyway": "restart",
                  "stop it for now": "stop", "reload it": "reload"}
for opener, answer, follow, svc in TT_SVC:
    add(f"two-turn identify-then-act: {svc}", "service_management",
        [turn(opener, prose(answer)),
         turn(follow,
              call("manage_services", action=TT_SVC_ACTIONS[follow], service=svc,
                   source_of_request="user_direct"))],
        "should-dispatch", TCLASS)

# ============================ TWINS: informational, NO tool call =============
TWCLASS = "class1-contrastive-informational"
G_CAP = ["intergen-capability-surface"]

INSTALL_TEACH = [
    ("how would I install {p}?",
     "`pkm install {p}` — that's the whole move. `pkm` is this system's package manager."),
    ("what's the command to put {p} on here?",
     "That'd be `pkm install {p}`. Everything package-shaped on this system goes through `pkm`."),
    ("if I wanted {p}, how would I get it myself?",
     "Run `pkm install {p}` in a terminal. You can peek first with `pkm info {p}` if you want the details."),
    ("how does installing software work on this system?",
     "Through `pkm`, the package manager: `pkm install <name>` installs, `pkm search <term>` finds candidates."),
    ("teach me how package installs are done here",
     "One tool: `pkm`. `pkm install <name>` pulls the package from the InterGenOS mirror and installs it, and `pkm info <name>` shows what you're getting first."),
    ("what would be the way to add {p} without your help?",
     "`pkm install {p}` does it — same thing I'd run for you."),
    ("how do I install things from the terminal?",
     "`pkm install <package>` — `pkm` is the package manager here, and it covers search, info, and removal too."),
    ("curious — what actually installs a package on this machine?",
     "`pkm` does. `pkm install <name>` fetches it from the mirror, verifies it, and lays it down."),
]
for i, (q, a) in enumerate(INSTALL_TEACH):
    p = PKGS[(i * 7) % len(PKGS)]
    add("teach: how to install", "package_management",
        [turn(q.format(p=p), prose(a.format(p=p)))],
        "should-teach", TWCLASS, grounding=G_CAP)

REMOVE_TEACH = [
    ("how do I uninstall something here?",
     "`pkm remove <name>` — that's the uninstall on this system."),
    ("what's the command to remove a package?",
     "`pkm remove <package>`. If you're not sure of the exact name, `pkm list` shows what's installed."),
    ("how would removing {p} work, hypothetically?",
     "You'd run `pkm remove {p}` — pkm unwinds the package's files and records the removal."),
    ("teach me package removal on this box",
     "It's `pkm remove <name>`. `pkm list` first if you want to see what's actually installed."),
    ("say I wanted {p} gone — what's the command?",
     "`pkm remove {p}` takes it off."),
]
for i, (q, a) in enumerate(REMOVE_TEACH):
    p = PKGS[(i * 3 + 1) % len(PKGS)]
    add("teach: how to remove", "package_management",
        [turn(q.format(p=p), prose(a.format(p=p)))],
        "should-teach", TWCLASS, grounding=G_CAP)

UPDATE_TEACH = [
    ("how do updates work on this system?",
     "`pkm sync` refreshes the package index from the mirror, then `pkm upgrade` brings installed packages up to date."),
    ("what's the command to update everything?",
     "`pkm upgrade` — run `pkm sync` first so the index is fresh."),
    ("how would I check for new versions myself?",
     "`pkm sync` to refresh the index, then `pkm upgrade` shows and applies what's newer."),
    ("teach me the update flow here",
     "Two steps: `pkm sync` pulls the latest package index, `pkm upgrade` updates what's installed. That's the whole flow."),
]
for q, a in UPDATE_TEACH:
    add("teach: how updates work", "package_management",
        [turn(q, prose(a))], "should-teach", TWCLASS, grounding=G_CAP)

SVC_TEACH = [
    ("how do I restart a service myself?",
     "`sudo systemctl restart <service>` — swap in the service name and you're done."),
    ("what's the command to check if a service is running?",
     "`systemctl status <service>` gives you the full picture; `systemctl is-active <service>` gives a one-word answer."),
    ("how do I stop a service from starting at boot?",
     "`sudo systemctl disable <service>` — it stays stopped across reboots unless something else pulls it in."),
    ("how would I turn a service on at boot?",
     "`sudo systemctl enable <service>` — add `--now` if you also want it started immediately."),
    ("teach me how services are managed on this system",
     "It's systemd: `systemctl status <name>` to look, `sudo systemctl start/stop/restart <name>` to act, `enable`/`disable` for boot behavior."),
    ("what shows me all the services?",
     "`systemctl list-units --type=service` — add `--all` to include the inactive ones."),
    ("how do I see why a service failed?",
     "`systemctl status <name>` shows the last log lines, and `journalctl -u <name>` gives the full history."),
    ("how do I reload a service's config without restarting it?",
     "`sudo systemctl reload <service>` — the service re-reads its config and keeps running."),
]
for q, a in SVC_TEACH:
    add("teach: service management", "service_management",
        [turn(q, prose(a))], "should-teach", TWCLASS, grounding=G_CAP)

FILE_TEACH = [
    ("how do I look at a file's contents in the terminal?",
     "`cat <path>` prints it straight out; `less <path>` lets you scroll. Either works."),
    ("what's the command to see a config file without opening an editor?",
     "`cat <path>` — or `less <path>` if it's long and you want to page through."),
    ("how would I edit a config file by hand?",
     "Open it in an editor — `nano <path>` is the gentle one, and save with Ctrl+O. For system files, put `sudo` in front."),
    ("how do I search inside files from the terminal?",
     "`grep <pattern> <path>` for one file, `grep -r <pattern> <dir>` to sweep a directory."),
]
for q, a in FILE_TEACH:
    add("teach: file viewing", "file_operations",
        [turn(q, prose(a))], "should-teach", TWCLASS, grounding=G_CAP)

MISC_TEACH = [
    ("how do screenshots work here?",
     "Ask me and I'll take one, or press PrintScreen — the capture lands in your Pictures folder."),
    ("how would I open an app without the mouse?",
     "Hit the Super key, type the app's name, and Enter launches it."),
    ("how do I search the web from here?",
     "Just ask me to search and I'll run it, or open the browser and search like usual."),
    ("what's the command to create an empty file?",
     "`touch <path>` — it creates the file if it isn't there."),
    ("how do I make a new directory?",
     "`mkdir <path>` — add `-p` to create the parents in one go."),
    ("how do I write text into a file from the terminal?",
     "`echo 'your text' > <path>` overwrites, `>>` appends. For anything longer, open the file in `nano`."),
]
for q, a in MISC_TEACH:
    add("teach: misc how-to", "system_commands",
        [turn(q, prose(a))], "should-teach", TWCLASS, grounding=G_CAP)

# --- "what does X do" — description without action (12)
for j, p in enumerate(rotate(PKGS, 3, 12)):
    q_forms = ["what does {p} do?", "what is {p}, exactly?", "what's {p} for?"]
    q = q_forms[j % len(q_forms)].format(p=p)
    add(f"describe package: {p}", "package_management",
        [turn(q, prose(f"{p} is {PKG_DESC[p]}."))],
        "should-teach", TWCLASS)

# --- "what does service X do" (6)
SVC_DESC = {
    "bluetooth": "the BlueZ daemon — it runs your Bluetooth adapters and pairings",
    "cups": "the printing system — queues, drivers, and printer chatter",
    "NetworkManager": "the service that owns your network connections, wired and wireless",
    "avahi-daemon": "local network discovery — the mDNS `.local` names on your LAN",
    "libvirtd": "the virtualization daemon — it manages virtual machines",
    "gdm": "the login screen — it starts your graphical session",
}
for s, d in SVC_DESC.items():
    add(f"describe service: {s}", "service_management",
        [turn(f"what does the {s} service do?", prose(f"{s} is {d}.")),],
        "should-teach", TWCLASS)

# --- family-paired twins: one informational twin per imperative family,
# sharing that family's FIRST object (the eval contrast-pair shape: same
# subject, opposite target). Each question is a distinct structure echoing its
# imperative sibling; answers are individually worded.
PAIRED_INSTALL_TWINS = [
    ("how would installing {p} go?", "`pkm install {p}` — one command, done."),
    ("what would you run to install {p}?", "I'd run `pkm install {p}` — pkm is the package manager here."),
    ("could you explain how {p} gets installed?", "Sure — `pkm install {p}` fetches it from the InterGenOS mirror, verifies it, and installs it."),
    ("if someone wanted {p} on here, what's the way?", "`pkm install {p}` is the way."),
    ("do I need anything special to install {p}?", "Nothing special — `pkm install {p}` handles the fetch, the verification, and the install."),
    ("what's involved in getting {p} installed?", "Just `pkm install {p}`. pkm pulls it from the mirror and checks it before anything lands."),
    ("where would {p} come from if I installed it?", "From the InterGenOS package mirror — `pkm install {p}` fetches and verifies it from there."),
    ("is installing {p} complicated?", "Not at all — `pkm install {p}` and you're done."),
    ("what tool handles installing something like {p}?", "`pkm` — the package manager. `pkm install {p}` would do it."),
    ("how does one add software like {p} here?", "Through `pkm`: `pkm install {p}` installs it, `pkm info {p}` describes it first."),
    ("what would the install command for {p} look like?", "`pkm install {p}` — that's the whole command."),
    ("walk me through what installing {p} would involve",
     "Short walk: `pkm install {p}`. pkm resolves it from the mirror, verifies the archive, installs it, and records it — one command end to end."),
]
for i, (q, a) in enumerate(PAIRED_INSTALL_TWINS):
    p = rotate(PKGS, i, 3)[0]
    add(f"paired twin (install): {p}", "package_management",
        [turn(q.format(p=p), prose(a.format(p=p)))],
        "should-teach", TWCLASS, grounding=G_CAP)

PAIRED_REMOVE_TWINS = [
    ("how would removing {p} go?", "`pkm remove {p}` — pkm unwinds its files and records the removal."),
    ("what would you run to take {p} off?", "`pkm remove {p}` — that's the removal command here."),
    ("could you explain how {p} would get uninstalled?", "`pkm remove {p}` does it — pkm deletes the package's files and updates its records."),
    ("is removing {p} safe to do?", "Generally yes — `pkm remove {p}` takes off just that package; anything depending on it would be flagged."),
    ("what happens when a package like {p} is removed?", "pkm deletes the files that package owns and drops it from the installed set — `pkm remove {p}` is the command."),
    ("what's the uninstall command for {p}?", "`pkm remove {p}`."),
    ("how do removals work for something like {p}?", "Through pkm: `pkm remove {p}` — clean, recorded, reversible with a reinstall."),
    ("what would the removal of {p} look like?", "One command — `pkm remove {p}` — and it's off the system."),
]
for i, (q, a) in enumerate(PAIRED_REMOVE_TWINS):
    p = rotate(PKGS, i + 12, 3)[0]
    add(f"paired twin (remove): {p}", "package_management",
        [turn(q.format(p=p), prose(a.format(p=p)))],
        "should-teach", TWCLASS, grounding=G_CAP)

PAIRED_SVC_TWINS = [
    ("how would restarting {s} work?", "`sudo systemctl restart {s}` — systemd stops it and brings it right back."),
    ("what's the command to restart the {s} service?", "`sudo systemctl restart {s}`."),
    ("how would I bounce {s} myself?", "`sudo systemctl restart {s}` — that's the bounce."),
    ("what would you run if {s} were misbehaving?", "First look: `systemctl status {s}`. If it needs a kick, `sudo systemctl restart {s}`."),
    ("how do I start {s} by hand?", "`sudo systemctl start {s}`."),
    ("what brings {s} up if it's stopped?", "`sudo systemctl start {s}` brings it up."),
    ("how would I stop {s} manually?", "`sudo systemctl stop {s}`."),
    ("what's the command to shut {s} down?", "`sudo systemctl stop {s}` — it stays down until started or the next boot."),
    ("how does enabling {s} at boot work?", "`sudo systemctl enable {s}` wires it into boot; add `--now` to also start it immediately."),
    ("what makes {s} start automatically at boot?", "An enabled unit — `sudo systemctl enable {s}` sets that up."),
    ("how would I keep {s} from auto-starting?", "`sudo systemctl disable {s}` — it stops coming up at boot."),
    ("how does a config reload for {s} work?", "`sudo systemctl reload {s}` — the service re-reads its config without dropping."),
]
for i, (q, a) in enumerate(PAIRED_SVC_TWINS):
    s = rotate(SVCS, i, 3)[0]
    add(f"paired twin (service): {s}", "service_management",
        [turn(q.format(s=s), prose(a.format(s=s)))],
        "should-teach", TWCLASS, grounding=G_CAP)

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
    # subject-level hard exclusions (held-out families' subjects)
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

n_disp = sum(1 for e in entries for t in e["turns"] if "tool_call" in t["gold"])
n_prose = sum(1 for e in entries for t in e["turns"] if "content" in t["gold"])
n_tgt = sum(1 for e in entries if e["training_provenance"]["class"] == TCLASS)
n_twin = sum(1 for e in entries if e["training_provenance"]["class"] == TWCLASS)
print(f"entries: {len(entries)}  (targets {n_tgt} / twins {n_twin})")
print(f"gold turns: dispatch {n_disp} / prose {n_prose}")
print(f"exact-dup user texts (>2 words): {dups}")
