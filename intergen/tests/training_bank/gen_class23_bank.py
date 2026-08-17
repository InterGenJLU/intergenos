#!/usr/bin/env python3
"""Round-1 class-2 (approval flow) + class-3 (deny-recovery) bank generator.

Authored 2026-08-11 per the private authoring spec. These classes train the
POST-dispatch composition — the reply after a dispatch resolved — via the
emitter's tool_flow turn shape, which renders the real serving path
(continue_after_tool_call: structured call, tool-role result, the real
synthesis instruction selected by success/executed).

Ground truth used:
- deny tool_result = the REAL registry string ("Tool call denied by user via
  review modal.", intergen/tool_registry.py) — that is what the model sees;
  gold replies NEVER echo internal vocabulary (review modal / gate / consent
  card) per the r150 rule and the eval's no_internal_vocabulary assertion.
- deny gold shape per the eval cells + the audit decisions (2026-08): honest
  can't-do (never "successfully"), names approval/permission, ends with a
  usable recovery line ("say the word", "ask me again"); class 3 adds a real
  alternative where one exists and never re-asks unprompted.
- approve gold: confirmation NAMING what was done (the item-14 delivery-naming
  ruling), grounded only in the tool_result's own content.
"""
import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("class23_bank.jsonl")
BLOCKLIST = Path(sys.argv[2]) if len(sys.argv) > 2 else None

DENY_RESULT = "Tool call denied by user via review modal."

SVCS = ["cups", "bluetooth", "avahi-daemon", "libvirtd", "nftables",
        "wpa_supplicant", "chronyd", "NetworkManager"]
PKGS = ["btop", "glances", "ranger", "tmux", "zellij", "neovim", "ripgrep",
        "bat", "fzf", "jq", "duf", "lazygit", "cmus", "mpv", "mc", "iotop"]

entries = []
_c = [0]


def add(intent, category, turns, tclass):
    _c[0] += 1
    entries.append({
        "id": f"tb23-{_c[0]:04d}", "category": category, "intent": intent,
        "expected_behavior_class": "should-dispatch", "turns": turns,
        "provenance": {"generator": "round1-class23-authoring",
                       "lens": "approval-and-deny-composition",
                       "grounding": ["intergen-tool-registry"],
                       "method": "structural-template-authored"},
        "training_provenance": {"class": tclass, "origin": "authored"},
    })


def svc_call(action, s):
    return {"name": "manage_services",
            "arguments": {"action": action, "service": s,
                          "source_of_request": "user_direct"}}


def pkg_call(action, p):
    return {"name": "manage_packages",
            "arguments": {"action": action, "package": p,
                          "source_of_request": "user_direct"}}


def flow(user, call, result, success, executed, gold_text):
    return {"user": user,
            "tool_flow": {"tool_call": call, "tool_result": result,
                          "success": success, "executed": executed},
            "gold": {"content": gold_text}}


def rot(pool, i, n=2):
    return [pool[(i * 3 + j * 5) % len(pool)] for j in range(n)]


# ============================ CLASS 2 — approval flow ========================
C2 = "class2-approval-flow"

# --- APPROVE endings: dispatch ran, gold confirms naming what was done.
SVC_OK = [
    ("restart {s}", "restart",
     "restart {s}: done\n{s}.service: active (running)",
     "Done — `{s}` restarted and it's running again."),
    ("please restart the {s} service", "restart",
     "restart {s}: done\n{s}.service: active (running)",
     "`{s}` is restarted — active and running."),
    ("stop {s} for now", "stop",
     "stop {s}: done\n{s}.service: inactive (dead)",
     "Stopped — `{s}` is down until you want it back."),
    ("bring {s} up", "start",
     "start {s}: done\n{s}.service: active (running)",
     "`{s}` is up and running."),
    ("enable {s} at boot", "enable",
     "enable {s}: created symlink for {s}.service",
     "Done — `{s}` will start on every boot from now on."),
    ("disable {s} autostart", "disable",
     "disable {s}: removed symlink for {s}.service",
     "`{s}` won't start on its own anymore — you can still start it manually."),
    ("reload {s}'s config", "reload",
     "reload {s}: done\n{s}.service: active (running), config reloaded",
     "Reloaded — `{s}` picked up its config without dropping."),
    ("give {s} a restart, it's being weird", "restart",
     "restart {s}: done\n{s}.service: active (running)",
     "Gave `{s}` a fresh restart — it's back up. If it keeps acting up, "
     "I can dig into its logs."),
]
for i, (u, act, res, gold) in enumerate(SVC_OK):
    for s in rot(SVCS, i):
        add(f"approve svc {act}: {s}", "service_management",
            [flow(u.format(s=s), svc_call(act, s), res.format(s=s),
                  True, True, gold.format(s=s))], C2)

PKG_OK = [
    ("install {p}", "install",
     "Installing {p}...\nInstalled {p} (1 package)",
     "Done — `{p}` is installed and ready to use."),
    ("put {p} on this machine", "install",
     "Installing {p}...\nInstalled {p} (1 package)",
     "`{p}` is on — installed cleanly."),
    ("remove {p}", "remove",
     "Removing {p}...\nRemoved {p} (1 package)",
     "Removed — `{p}` is off the system."),
    ("I don't use {p} anymore, take it off", "remove",
     "Removing {p}...\nRemoved {p} (1 package)",
     "Gone — `{p}` is uninstalled."),
    ("update {p}", "update",
     "Updating {p}...\n{p} is now at the latest version",
     "`{p}` is up to date."),
    ("get {p} current", "update",
     "Updating {p}...\n{p} is now at the latest version",
     "Done — `{p}` is on the latest version now."),
]
for i, (u, act, res, gold) in enumerate(PKG_OK):
    for p in rot(PKGS, i + 8):
        add(f"approve pkg {act}: {p}", "package_management",
            [flow(u.format(p=p), pkg_call(act, p), res.format(p=p),
                  True, True, gold.format(p=p))], C2)

# --- DENY endings: dispatch refused before running; gold = honest + recovery.
SVC_DENY = [
    ("restart {s}", "restart",
     "I wasn't able to restart `{s}` — that action needs your approval and it "
     "wasn't granted this time. Ask me again whenever you're ready and I'll "
     "do it."),
    ("stop {s}", "stop",
     "`{s}` is still running — stopping it needs your permission and that "
     "didn't come through. Just say the word and I'll stop it."),
    ("bounce {s} for me", "restart",
     "No restart happened — `{s}` needs your approval for that and it wasn't "
     "given. Happy to do it the moment you say go."),
    ("enable {s} at boot", "enable",
     "I couldn't enable `{s}` — boot changes need your approval and this one "
     "wasn't approved. Ask again anytime and I'll set it up."),
]
for i, (u, act, gold) in enumerate(SVC_DENY):
    for s in rot(SVCS, i + 3):
        add(f"deny svc {act}: {s}", "service_management",
            [flow(u.format(s=s), svc_call(act, s), DENY_RESULT,
                  False, False, gold.format(s=s))], C2)

PKG_DENY = [
    ("install {p}", "install",
     "`{p}` didn't get installed — installs need your approval and it wasn't "
     "granted. Ask me again when you're ready and I'll install it."),
    ("remove {p}", "remove",
     "`{p}` is still installed — removing it needs your permission, which "
     "wasn't given this time. Say the word and I'll take it off."),
    ("update {p}", "update",
     "The update didn't run — package changes need your approval and this one "
     "wasn't approved. I'll run it whenever you give the go-ahead."),
]
for i, (u, act, gold) in enumerate(PKG_DENY):
    for p in rot(PKGS, i + 2):
        add(f"deny pkg {act}: {p}", "package_management",
            [flow(u.format(p=p), pkg_call(act, p), DENY_RESULT,
                  False, False, gold.format(p=p))], C2)

# --- deny THEN approve-later: the re-ask flow, two turns.
DENY_THEN_GO = [
    ("restart {s}", "restart", "svc",
     "I wasn't able to restart `{s}` — it needs your approval and it wasn't "
     "granted. Ask me again and I'll do it.",
     "actually go ahead, restart it"),
    ("install {p}", "install", "pkg",
     "`{p}` didn't get installed — that needs your approval and it wasn't "
     "given this time. Just ask again when you're ready.",
     "ok yes, install it"),
    ("stop {s}", "stop", "svc",
     "`{s}` is still up — stopping it needs your permission and that didn't "
     "come through. Say the word and I'll stop it.",
     "the word is given — stop it"),
    ("remove {p}", "remove", "pkg",
     "`{p}` stayed put — removals need your approval and this one wasn't "
     "approved. Happy to do it whenever you say so.",
     "go for it, remove it"),
]
for i, (u, act, kind, deny_gold, followup) in enumerate(DENY_THEN_GO):
    for j in range(3):
        if kind == "svc":
            s = SVCS[(i * 3 + j * 5) % len(SVCS)]
            first = flow(u.format(s=s), svc_call(act, s), DENY_RESULT,
                         False, False, deny_gold.format(s=s))
            second = {"user": followup,
                      "gold": {"tool_call": svc_call(act, s)}}
            name = s
        else:
            p = PKGS[(i * 3 + j * 5) % len(PKGS)]
            first = flow(u.format(p=p), pkg_call(act, p), DENY_RESULT,
                         False, False, deny_gold.format(p=p))
            second = {"user": followup,
                      "gold": {"tool_call": pkg_call(act, p)}}
            name = p
        add(f"deny-then-approve {act}: {name}",
            "service_management" if kind == "svc" else "package_management",
            [first, second], C2)

# ============================ CLASS 3 — deny-recovery ========================
C3 = "class3-deny-recovery"

SVC_RECOVER = [
    ("restart {s}", "restart",
     "That restart didn't go through — it needs your approval. Two options: "
     "ask me again and approve it, or run `sudo systemctl restart {s}` "
     "yourself."),
    ("stop {s}", "stop",
     "`{s}` is still running — I need your permission to stop it and it "
     "wasn't granted. If you'd rather do it directly: "
     "`sudo systemctl stop {s}`."),
    ("restart the {s} service", "restart",
     "No luck — the restart needs approval and it wasn't approved this time. "
     "Nothing changed, `{s}` is exactly as it was. Ask again anytime."),
    ("disable {s}", "disable",
     "`{s}` is still set to start at boot — disabling needs your approval, "
     "which didn't come through. Say the word, or run "
     "`sudo systemctl disable {s}` if you prefer."),
    ("reload {s}", "reload",
     "The reload didn't happen — it needed your approval. `{s}` kept its old "
     "config and kept running; ask me again whenever you're ready."),
]
for i, (u, act, gold) in enumerate(SVC_RECOVER):
    for s in rot(SVCS, i + 5):
        add(f"recover svc {act}: {s}", "service_management",
            [flow(u.format(s=s), svc_call(act, s), DENY_RESULT,
                  False, False, gold.format(s=s))], C3)

PKG_RECOVER = [
    ("install {p}", "install",
     "`{p}` isn't installed — the install needs your approval and it wasn't "
     "granted. Ask me again to approve it, or run `pkm install {p}` yourself "
     "if you'd rather."),
    ("remove {p}", "remove",
     "Still there — removing `{p}` needs your permission and that wasn't "
     "given. I'll do it on your word, or `pkm remove {p}` does the same from "
     "a terminal."),
    ("update {p}", "update",
     "The update didn't run — it needs your approval. Everything stayed as it "
     "was; ask again when you're ready, or run `pkm upgrade` yourself."),
    ("put {p} on here", "install",
     "That didn't go through — installs need your okay and this one wasn't "
     "approved. Nothing was changed. Say the word and I'll install `{p}`."),
    ("get rid of {p}", "remove",
     "`{p}` is still on the system — the removal wasn't approved. Ask me "
     "again and I'll take it off, no re-ask from me until you do."),
]
for i, (u, act, gold) in enumerate(PKG_RECOVER):
    for p in rot(PKGS, i + 7):
        add(f"recover pkg {act}: {p}", "package_management",
            [flow(u.format(p=p), pkg_call(act, p), DENY_RESULT,
                  False, False, gold.format(p=p))], C3)

# ===================== EXPANSION WAVE 2 (same session) =======================
# New structural families — no synonym swaps of wave-1 texts; objects rotate on
# different offsets so structure x object pairs stay unique.

SVC_OK_2 = [
    ("could you restart {s}? it dropped a connection", "restart",
     "restart {s}: done\n{s}.service: active (running)",
     "Restarted — `{s}` is back and holding. If it drops again, I'll pull "
     "its journal."),
    ("kick {s} over for me", "restart",
     "restart {s}: done\n{s}.service: active (running)",
     "Kicked — `{s}` came right back up."),
    ("{s} needs a restart after that config change", "restart",
     "restart {s}: done\n{s}.service: active (running)",
     "Done — `{s}` restarted on the new config and is running."),
    ("shut down {s} until tomorrow", "stop",
     "stop {s}: done\n{s}.service: inactive (dead)",
     "`{s}` is stopped. It'll stay down until you start it or reboot."),
    ("I want {s} running whenever the machine boots", "enable",
     "enable {s}: created symlink for {s}.service",
     "Set — `{s}` starts at every boot now."),
    ("take {s} out of the boot lineup", "disable",
     "disable {s}: removed symlink for {s}.service",
     "Out — `{s}` no longer starts at boot."),
]
for i, (u, act, res, gold) in enumerate(SVC_OK_2):
    for s in rot(SVCS, i + 11, 2):
        add(f"approve svc {act} w2: {s}", "service_management",
            [flow(u.format(s=s), svc_call(act, s), res.format(s=s),
                  True, True, gold.format(s=s))], C2)

PKG_OK_2 = [
    ("grab {p} for me please", "install",
     "Installing {p}...\nInstalled {p} (1 package)",
     "Grabbed — `{p}` is installed."),
    ("I keep hearing about {p}, install it", "install",
     "Installing {p}...\nInstalled {p} (1 package)",
     "Installed — `{p}` is ready whenever you want to try it."),
    ("{p} is taking up space, drop it", "remove",
     "Removing {p}...\nRemoved {p} (1 package)",
     "Dropped — `{p}` is gone and its space is back."),
    ("time to clear out {p}", "remove",
     "Removing {p}...\nRemoved {p} (1 package)",
     "Cleared — `{p}` is off the system."),
]
for i, (u, act, res, gold) in enumerate(PKG_OK_2):
    for p in rot(PKGS, i + 12, 2):
        add(f"approve pkg {act} w2: {p}", "package_management",
            [flow(u.format(p=p), pkg_call(act, p), res.format(p=p),
                  True, True, gold.format(p=p))], C2)

SVC_DENY_2 = [
    ("could you restart {s}?", "restart",
     "That needs a yes from you and it didn't get one — `{s}` wasn't "
     "touched. Ask again whenever and I'll restart it."),
    ("kill {s} for now", "stop",
     "`{s}` kept running — stopping a service takes your approval, and it "
     "wasn't granted this time. One word from you and it's done."),
    ("get {s} into the boot sequence", "enable",
     "Not yet — changing what starts at boot needs your approval, and this "
     "one wasn't approved. `{s}` is unchanged; ask again when ready."),
    ("pull {s} out of autostart", "disable",
     "`{s}` still autostarts — that change needed your permission and it "
     "didn't come through. I'll flip it whenever you say."),
]
for i, (u, act, gold) in enumerate(SVC_DENY_2):
    for s in rot(SVCS, i + 6, 2):
        add(f"deny svc {act} w2: {s}", "service_management",
            [flow(u.format(s=s), svc_call(act, s), DENY_RESULT,
                  False, False, gold.format(s=s))], C2)

PKG_DENY_2 = [
    ("set me up with {p}", "install",
     "Couldn't set that up — installing `{p}` needs your approval and it "
     "wasn't given. It's one ask away whenever you want it."),
    ("clear {p} off this machine", "remove",
     "`{p}` stayed — removals take your permission and this one wasn't "
     "approved. Nothing changed; say the word and I'll clear it."),
    ("bring {p} up to date", "update",
     "No update ran — that needed your approval and it wasn't granted. `{p}` "
     "is exactly as it was; ask again and I'll bring it current."),
]
for i, (u, act, gold) in enumerate(PKG_DENY_2):
    for p in rot(PKGS, i + 9, 2):
        add(f"deny pkg {act} w2: {p}", "package_management",
            [flow(u.format(p=p), pkg_call(act, p), DENY_RESULT,
                  False, False, gold.format(p=p))], C2)

DENY_THEN_GO_2 = [
    ("enable {s} at boot", "enable", "svc",
     "That didn't go through — boot changes need your approval and it wasn't "
     "given. Ask me again and I'll set it.",
     "alright, approved — set it up"),
    ("update {p}", "update", "pkg",
     "The update needed your approval and didn't get it — `{p}` is unchanged. "
     "I'll run it on your word.",
     "you have my word, run it"),
]
for i, (u, act, kind, deny_gold, followup) in enumerate(DENY_THEN_GO_2):
    for j in range(3):
        if kind == "svc":
            s = SVCS[(i * 5 + j * 3 + 1) % len(SVCS)]
            first = flow(u.format(s=s), svc_call(act, s), DENY_RESULT,
                         False, False, deny_gold.format(s=s))
            second = {"user": followup, "gold": {"tool_call": svc_call(act, s)}}
            name = s
        else:
            p = PKGS[(i * 5 + j * 3 + 1) % len(PKGS)]
            first = flow(u.format(p=p), pkg_call(act, p), DENY_RESULT,
                         False, False, deny_gold.format(p=p))
            second = {"user": followup, "gold": {"tool_call": pkg_call(act, p)}}
            name = p
        add(f"deny-then-approve w2 {act}: {name}",
            "service_management" if kind == "svc" else "package_management",
            [first, second], C2)

SVC_RECOVER_2 = [
    ("start {s}", "start",
     "`{s}` didn't start — that takes your approval and it wasn't given. "
     "Ask me again, or `sudo systemctl start {s}` does it directly."),
    ("bounce {s}", "restart",
     "No bounce — restarting `{s}` needs your okay and it wasn't granted. "
     "Nothing changed. I'm ready the moment you approve."),
    ("mask {s} so nothing can start it", "mask",
     "`{s}` isn't masked — that's a strong change and it needs your "
     "approval, which didn't come through. Ask again if you're sure, or "
     "`sudo systemctl mask {s}` if you'd rather do it by hand."),
    ("enable {s}", "enable",
     "Still off the boot list — enabling `{s}` needs your permission and "
     "this one wasn't approved. Your call, whenever."),
]
for i, (u, act, gold) in enumerate(SVC_RECOVER_2):
    for s in rot(SVCS, i + 2, 2):
        add(f"recover svc {act} w2: {s}", "service_management",
            [flow(u.format(s=s), svc_call(act, s), DENY_RESULT,
                  False, False, gold.format(s=s))], C3)

PKG_RECOVER_2 = [
    ("throw {p} on here", "install",
     "Nothing landed — installs need your approval and it wasn't given. "
     "Two ways forward: ask me again and approve it, or `pkm install {p}` "
     "in a terminal."),
    ("drop {p}", "remove",
     "`{p}` is still here — the removal wasn't approved, so I left it "
     "alone. Ask again to approve it, or `pkm remove {p}` works too."),
    ("freshen up {p}", "update",
     "No freshening happened — updates need your okay and this one didn't "
     "get it. Say the word, or run `pkm upgrade` yourself anytime."),
]
for i, (u, act, gold) in enumerate(PKG_RECOVER_2):
    for p in rot(PKGS, i + 4, 2):
        add(f"recover pkg {act} w2: {p}", "package_management",
            [flow(u.format(p=p), pkg_call(act, p), DENY_RESULT,
                  False, False, gold.format(p=p))], C3)

# ------------------------------------------------------------------ checks
def norm(t):
    return "".join(ch for ch in t.lower() if ch.isalnum() or ch == " ").strip()

INTERNAL_VOCAB = ("review modal", "consent card", "gate", "dispatcher",
                  "safety layer", "tool call", "toolcall")
vocab_hits = []
for e in entries:
    for t in e["turns"]:
        g = t["gold"].get("content", "")
        for w in INTERNAL_VOCAB:
            if w in g.lower():
                vocab_hits.append((e["id"], w))
if vocab_hits:
    for h in vocab_hits:
        print("INTERNAL VOCAB IN GOLD:", h, file=sys.stderr)
    sys.exit(4)

if BLOCKLIST and BLOCKLIST.exists():
    excluded = {norm(l) for l in BLOCKLIST.read_text().splitlines() if l.strip()}
    hits = [(e["id"], t["user"]) for e in entries for t in e["turns"]
            if norm(t["user"]) in excluded]
    if hits:
        for h in hits:
            print("EXCLUSION HIT:", h, file=sys.stderr)
        sys.exit(2)
    for e in entries:
        for t in e["turns"]:
            low = t["user"].lower()
            for b in ("htop", "get me tree", "hostname"):
                if b in low:
                    print("SUBJECT HIT:", e["id"], t["user"], file=sys.stderr)
                    sys.exit(3)

with OUT.open("w", encoding="utf-8") as fh:
    for e in entries:
        fh.write(json.dumps(e, ensure_ascii=False) + "\n")

n2 = sum(1 for e in entries if e["training_provenance"]["class"] == C2)
n3 = sum(1 for e in entries if e["training_provenance"]["class"] == C3)
napp = sum(1 for e in entries for t in e["turns"]
           if "tool_flow" in t and t["tool_flow"]["executed"])
nden = sum(1 for e in entries for t in e["turns"]
           if "tool_flow" in t and not t["tool_flow"]["executed"])
print(f"entries: {len(entries)}  (class2 {n2} / class3 {n3})")
print(f"flow turns: approve {napp} / deny {nden}")
