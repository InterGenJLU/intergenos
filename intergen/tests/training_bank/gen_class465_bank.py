#!/usr/bin/env python3
"""Round-1 classes 4 (fragments), 6 (frustration), 5 (teach-then-offer) bank.

Held-out discipline beyond the blocklist: disk-usage, memory, hostname, gpu and
"slow" complaint subjects are excluded ENTIRELY — their eval families sit in
held-out/validation splits (lex_disk_terse_bare, sys_memory, messy_terse_ram,
lex_gpu_terse, emo_frustrated_slow, lex_hostname_*). Fragment coverage grows on
other subjects; the held-out families stay measurement-only.

Gold rules: resolvable fragments dispatch the right read-only tool
(user_implied — the user asked by fragment, the tool call is the reasonable
follow-on); ambiguous fragments get the clarification-echo shape (item-5:
echo what WAS understood, never the generic rephrase line). Frustration gets
absorbed tone + either a real diagnostic dispatch or the scope-naming question
(decided 2026-08, items 13/15). Teach-then-offer: teach, then an offer
worded as a real question (varied — the literal-substring trap is a named
bin-(c) issue), then BOTH continuations authored (accept -> dispatch;
decline -> graceful close, no re-offer).
"""
import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("class465_bank.jsonl")
BLOCKLIST = Path(sys.argv[2]) if len(sys.argv) > 2 else None

entries = []
_c = [0]


def add(intent, category, turns, tclass, ebc="should-dispatch"):
    _c[0] += 1
    entries.append({
        "id": f"tb465-{_c[0]:04d}", "category": category, "intent": intent,
        "expected_behavior_class": ebc, "turns": turns,
        "provenance": {"generator": "round1-class465-authoring",
                       "lens": "fragments-frustration-teach-offer",
                       "grounding": ["intergen-tool-registry"],
                       "method": "structural-template-authored"},
        "training_provenance": {"class": tclass, "origin": "authored"},
    })


def t(user, gold):
    return {"user": user, "gold": gold}


def call(_tool, **args):
    return {"tool_call": {"name": _tool, "arguments": args}}


def prose(x):
    return {"content": x}


# ============================ CLASS 4 — fragments ============================
C4 = "class4-fragments-terse"

# Resolvable fragments -> the obvious read-only dispatch (user_implied).
FRAG_DISPATCH = [
    ("kernel", call("run_command", command="uname -r",
                    source_of_request="user_implied")),
    ("kernel?", call("run_command", command="uname -r",
                     source_of_request="user_implied")),
    ("uptime", call("run_command", command="uptime",
                    source_of_request="user_implied")),
    ("uptime?", call("run_command", command="uptime",
                     source_of_request="user_implied")),
    ("ip", call("run_command", command="ip addr",
                source_of_request="user_implied")),
    ("ip address?", call("run_command", command="ip addr",
                         source_of_request="user_implied")),
    ("network", call("run_command", command="ip addr",
                     source_of_request="user_implied")),
    ("wifi?", call("manage_services", action="status",
                   service="wpa_supplicant",
                   source_of_request="user_implied")),
    ("bluetooth", call("manage_services", action="status",
                       service="bluetooth", source_of_request="user_implied")),
    ("bluetooth??", call("manage_services", action="status",
                         service="bluetooth",
                         source_of_request="user_implied")),
    ("printers", call("manage_services", action="status", service="cups",
                      source_of_request="user_implied")),
    ("printer broken?", call("manage_services", action="status",
                             service="cups",
                             source_of_request="user_implied")),
    ("errors", call("run_command", command="journalctl -p err -n 20",
                    source_of_request="user_implied")),
    ("any errors?", call("run_command", command="journalctl -p err -n 20",
                         source_of_request="user_implied")),
    ("failed services", call("manage_services", action="list-units",
                             source_of_request="user_implied")),
    ("services", call("manage_services", action="list-units",
                      source_of_request="user_implied")),
    ("packages", call("manage_packages", action="list",
                      source_of_request="user_implied")),
    ("what's installed", call("manage_packages", action="list",
                              source_of_request="user_implied")),
    ("blocks", call("run_command", command="lsblk",
                    source_of_request="user_implied")),
    ("drives?", call("run_command", command="lsblk",
                     source_of_request="user_implied")),
]
for frag, gold in FRAG_DISPATCH:
    add(f"fragment dispatch: {frag}", "system_info", [t(frag, gold)], C4)

# Ambiguous fragments -> clarification-echo (what WAS understood, a real guess).
FRAG_CLARIFY = [
    ("thing", "I caught 'thing' but not which thing — are you after a system "
              "reading, an app, or something else?"),
    ("it broke", "Something broke — got it. Which part: an app, a device, or "
                 "the system itself?"),
    ("fix", "Happy to fix — I just need the target. What's misbehaving?"),
    ("check", "I can check plenty — services, packages, logs. Which one did "
              "you mean?"),
    ("status??", "Status of what, exactly? A service, the whole system, or "
                 "something specific?"),
    ("teee", "'teee' doesn't look like a package or command I know — did you "
             "mean something like `tee`?"),
    ("instal", "Looks like you want to install something ('instal') — which "
               "package?"),
    ("restrt", "Reads like 'restart' — which service should I restart?"),
    ("pkgs?", "Packages — sure. Installed ones, or searching for something "
              "new?"),
    ("logs", "Which logs — system errors, a specific service, or everything "
             "recent?"),
]
for frag, gold in FRAG_CLARIFY:
    add(f"fragment clarify: {frag}", "system_info",
        [t(frag, prose(gold))], C4, ebc=None)

# ============================ CLASS 6 — frustration ==========================
C6 = "class6-frustration-intent"

# Half: pre-emptive diagnostics — a REAL scan dispatched, findings led with.
FRUST_SCAN = [
    ("NOTHING WORKS on this stupid thing",
     call("run_command", command="journalctl -p err -n 20",
          source_of_request="user_implied")),
    ("everything is broken again",
     call("run_command", command="journalctl -p err -n 20",
          source_of_request="user_implied")),
    ("this machine is driving me crazy, nothing responds right",
     call("manage_services", action="list-units",
          source_of_request="user_implied")),
    ("ugh, it's all falling apart today",
     call("run_command", command="journalctl -p err -n 20",
          source_of_request="user_implied")),
    ("WHY does nothing ever just work",
     call("manage_services", action="list-units",
          source_of_request="user_implied")),
    ("I swear this thing hates me, errors everywhere",
     call("run_command", command="journalctl -p err -n 20",
          source_of_request="user_implied")),
]
for u, gold in FRUST_SCAN:
    add(f"frustration scan: {u[:30]}", "diagnostics", [t(u, gold)], C6)

# Half: the scope-naming clarifying question, absorbed tone, no apology spiral.
FRUST_ASK = [
    ("I hate this computer",
     "Rough day with it — let's fix that. What's it doing (or not doing) "
     "right now?"),
    ("nothing I try works",
     "Let's sort it out. What were you trying to do when it fought back?"),
    ("this is the worst OS ever",
     "Fair enough — something's clearly wrong. What system(s) are you having "
     "issues with?"),
    ("why is everything so complicated",
     "It doesn't have to be. Tell me what you're trying to get done and I'll "
     "handle the complicated part."),
    ("I give up",
     "Don't yet — hand it to me. What were you working on?"),
    ("STUPID machine ate my afternoon",
     "That's an afternoon owed back. What went wrong — an app, a device, or "
     "the system?"),
]
for u, gold in FRUST_ASK:
    add(f"frustration ask: {u[:30]}", "diagnostics",
        [t(u, prose(gold))], C6, ebc=None)

# ============================ CLASS 5 — teach-then-offer =====================
C5 = "class5-teach-then-offer"

# (teach+offer prose, accept-follow -> dispatch, decline-follow -> close)
TEACH = [
    ("how do I check for system updates?",
     "`pkm sync` refreshes the index, then `pkm upgrade` applies what's new. "
     "Shall I run the update for you now?",
     "yes please", call("manage_packages", action="update",
                        source_of_request="user_direct"),
     "no, I'll do it myself later",
     "Sounds good — it's `pkm sync` then `pkm upgrade` whenever you're ready."),
    ("how do screenshots work here?",
     "PrintScreen captures the screen to your Pictures folder — or I can take "
     "one. Want a screenshot now?",
     "sure, take one", call("take_screenshot",
                            source_of_request="user_direct"),
     "nah, just wanted to know",
     "Now you know — PrintScreen any time you need it."),
    ("how would I see the kernel version?",
     "`uname -r` prints it. Want me to run it and tell you?",
     "go ahead", call("run_command", command="uname -r",
                      source_of_request="user_direct"),
     "no need",
     "Alright — `uname -r` in any terminal when you're curious."),
    ("how do I check recent system errors?",
     "`journalctl -p err -n 20` shows the last twenty error-level entries. "
     "Would you like me to pull them up?",
     "yeah show me", call("run_command", command="journalctl -p err -n 20",
                          source_of_request="user_direct"),
     "not right now",
     "No problem — that command's there whenever you want a look."),
    ("how would I search for a package?",
     "`pkm search <term>` looks through the repos. Anything you want me to "
     "search for right now?",
     "yes — search for a terminal music player",
     call("manage_packages", action="search", query="terminal music player",
          source_of_request="user_direct"),
     "no, just learning",
     "Learning counts — `pkm search <term>` whenever you need it."),
    ("how do I list what's installed?",
     "`pkm list` prints every installed package. Want the list now?",
     "sure", call("manage_packages", action="list",
                  source_of_request="user_direct"),
     "no thanks",
     "Easy — `pkm list` any time."),
]
for q, teach_offer, yes_u, yes_gold, no_u, no_gold in TEACH:
    add(f"teach-offer accept: {q[:30]}", "practical_guidance",
        [t(q, prose(teach_offer)), t(yes_u, yes_gold)], C5)
    add(f"teach-offer decline: {q[:30]}", "practical_guidance",
        [t(q, prose(teach_offer)), t(no_u, prose(no_gold))], C5)

# ===================== EXPANSION WAVE 2 (same session) =======================

FRAG_DISPATCH_2 = [
    ("os version", call("read_file", path="/etc/os-release",
                        source_of_request="user_implied")),
    ("which os is this", call("read_file", path="/etc/os-release",
                              source_of_request="user_implied")),
    ("dns?", call("read_file", path="/etc/resolv.conf",
                  source_of_request="user_implied")),
    ("nameservers", call("read_file", path="/etc/resolv.conf",
                         source_of_request="user_implied")),
    ("virt?", call("manage_services", action="status", service="libvirtd",
                   source_of_request="user_implied")),
    ("vms running?", call("manage_services", action="status",
                          service="libvirtd",
                          source_of_request="user_implied")),
    ("firewall", call("manage_services", action="status", service="nftables",
                      source_of_request="user_implied")),
    ("firewall up?", call("manage_services", action="status",
                          service="nftables",
                          source_of_request="user_implied")),
    ("time sync?", call("manage_services", action="status", service="chronyd",
                        source_of_request="user_implied")),
    ("login screen ok?", call("manage_services", action="status",
                              service="gdm",
                              source_of_request="user_implied")),
    ("boots?", call("run_command", command="journalctl -b -p err -n 20",
                    source_of_request="user_implied")),
    ("last boot errors", call("run_command",
                              command="journalctl -b -p err -n 20",
                              source_of_request="user_implied")),
    ("shells", call("read_file", path="/etc/shells",
                    source_of_request="user_implied")),
    ("locale?", call("read_file", path="/etc/locale.conf",
                     source_of_request="user_implied")),
    ("screenshot", call("take_screenshot", source_of_request="user_implied")),
    ("screencap", call("take_screenshot", source_of_request="user_implied")),
]
for frag, gold in FRAG_DISPATCH_2:
    add(f"fragment dispatch w2: {frag}", "system_info", [t(frag, gold)], C4)

FRAG_CLARIFY_2 = [
    ("srvice", "Looks like 'service' — which one, and what should I do "
               "with it?"),
    ("updte", "Reads like 'update' — the whole system, or one package?"),
    ("wat", "Not sure what you're after there — what would you like me to "
            "check or do?"),
    ("brokn", "Something's broken — I'm on it as soon as I know what. Which "
              "app or device?"),
    ("open", "Open what — an app, a file, or a page?"),
    ("run", "Run what? Give me the command or the app name and I'll go."),
    ("net thing", "Network something — is the connection down, or do you "
                  "want its details?"),
    ("printer thing", "Printer trouble or printer setup — which is it?"),
    ("that file", "Which file? Give me the path and I'll pull it up."),
]
for frag, gold in FRAG_CLARIFY_2:
    add(f"fragment clarify w2: {frag}", "system_info",
        [t(frag, prose(gold))], C4, ebc=None)

FRUST_SCAN_2 = [
    ("great, ANOTHER thing that doesn't work",
     call("run_command", command="journalctl -p err -n 20",
          source_of_request="user_implied")),
    ("this install has been nothing but problems",
     call("manage_services", action="list-units",
          source_of_request="user_implied")),
    ("why is this thing fighting me on everything today",
     call("run_command", command="journalctl -p err -n 20",
          source_of_request="user_implied")),
    ("errors, errors, always errors",
     call("run_command", command="journalctl -p err -n 20",
          source_of_request="user_implied")),
]
for u, gold in FRUST_SCAN_2:
    add(f"frustration scan w2: {u[:28]}", "diagnostics", [t(u, gold)], C6)

FRUST_ASK_2 = [
    ("I can't deal with this today",
     "Then don't — hand it over. What's the thing that broke?"),
    ("everything about this setup annoys me",
     "Let's fix the worst one first. What's annoying you most right now?"),
    ("useless. absolutely useless.",
     "Let me change that — what were you trying to do?"),
    ("I miss my old computer",
     "Give me a chance to win you over. What's this one doing wrong?"),
    ("whatever, nothing works anyway",
     "Something specific isn't working — name it and I'll go after it."),
    ("HOW is this so hard",
     "It shouldn't be. Tell me what you're stuck on and I'll take it from "
     "there."),
]
for u, gold in FRUST_ASK_2:
    add(f"frustration ask w2: {u[:28]}", "diagnostics",
        [t(u, prose(gold))], C6, ebc=None)

TEACH_2 = [
    ("how do I find which package owns a file?",
     "`pkm provides <path>` names the owner. Want me to look one up for you?",
     "yes — who owns /usr/bin/python3",
     call("run_command", command="pkm provides /usr/bin/python3",
          source_of_request="user_direct"),
     "no, that's all I needed",
     "That's the one — `pkm provides <path>` whenever you're curious."),
    ("how would I check a service's recent log?",
     "`journalctl -u <service> -n 50` shows its last fifty lines. Should I "
     "pull one up for you?",
     "yes, cups please",
     call("run_command", command="journalctl -u cups -n 50",
          source_of_request="user_direct"),
     "no thanks, good to know",
     "Anytime — swap in any service name you like."),
    ("how do I see what changed in recent package operations?",
     "`pkm history` lists the recent installs, removals and updates. Want me "
     "to show it?",
     "go for it",
     call("run_command", command="pkm history",
          source_of_request="user_direct"),
     "not now",
     "It'll be there when you want it — `pkm history`."),
    ("how would I verify a package installed correctly?",
     "`pkm verify <name>` checks its files against the record. Is there one "
     "you'd like me to verify?",
     "yes, verify tmux",
     call("manage_packages", action="verify", package="tmux",
          source_of_request="user_direct"),
     "no, just wondering",
     "Simple as that — `pkm verify <name>` any time."),
]
for q, teach_offer, yes_u, yes_gold, no_u, no_gold in TEACH_2:
    add(f"teach-offer accept w2: {q[:28]}", "practical_guidance",
        [t(q, prose(teach_offer)), t(yes_u, yes_gold)], C5)
    add(f"teach-offer decline w2: {q[:28]}", "practical_guidance",
        [t(q, prose(teach_offer)), t(no_u, prose(no_gold))], C5)

# ------------------------------------------------------------------ checks
def norm(x):
    return "".join(ch for ch in x.lower() if ch.isalnum() or ch == " ").strip()

BANNED_SUBJECTS = ("htop", "get me tree", "hostname", "disk", "memory", "ram",
                   " gpu", "slow")
for e in entries:
    for turn in e["turns"]:
        low = " " + turn["user"].lower()
        for b in BANNED_SUBJECTS:
            if b in low:
                print("SUBJECT HIT:", e["id"], turn["user"], b, file=sys.stderr)
                sys.exit(3)

if BLOCKLIST and BLOCKLIST.exists():
    excluded = {norm(l) for l in BLOCKLIST.read_text().splitlines() if l.strip()}
    hits = [(e["id"], turn["user"]) for e in entries for turn in e["turns"]
            if norm(turn["user"]) in excluded]
    if hits:
        for h in hits:
            print("EXCLUSION HIT:", h, file=sys.stderr)
        sys.exit(2)

for e in entries:
    if e["expected_behavior_class"] is None:
        del e["expected_behavior_class"]

with OUT.open("w", encoding="utf-8") as fh:
    for e in entries:
        fh.write(json.dumps(e, ensure_ascii=False) + "\n")

from collections import Counter
c = Counter(e["training_provenance"]["class"] for e in entries)
print(f"entries: {len(entries)} ", dict(c))
