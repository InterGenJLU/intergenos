# Meet InterGen — the InterGenOS AI assistant

InterGen is the AI assistant that ships with InterGenOS. It runs
entirely on your machine, answers questions about your system, and
helps with shell, configuration, and code tasks. It never sends your
data anywhere outside the box.

This document is the introductory tour. The architecture deep-dive
lives in the source tree at
[`docs/components/intergen.md`](https://github.com/InterGenJLU/intergenos/blob/master/docs/components/intergen.md)
for the curious.


## What it does

InterGen is a chat-style assistant you can open from the Applications
menu or from any terminal with the `intergen` command. You can ask it
things like:

- **"How much free space is on my root partition?"** — it runs the
  right `df` invocation and reads back the answer.
- **"What's my current IP address?"** — direct `ip` command plus a
  plain-English summary.
- **"Write me a systemd timer that runs `backup.sh` every Sunday at
  03:00."** — drafts the unit and timer files, then asks before
  installing.
- **"Why did sshd fail to start after I edited the config?"** — reads
  `journalctl -u sshd`, summarizes the error, and suggests a fix.
- **"Install htop"** — recognizes the intent and asks you to confirm
  before running `sudo pkm install htop`.
- **"Explain what this Python error means."** — returns the traceback
  with context.

InterGen is not a replacement for general chat tools. It is a **system
assistant**: its strength is knowing your machine, not the whole
world.


## Why local

Every model InterGen uses runs on your own CPU and GPU. Nothing about
your prompts, your files, your configuration, or your machine identity
ever leaves the local network. There is no cloud account, no API key,
no telemetry, and no "we just send it to improve the service"
loophole. The trade-offs are honest:

- The local models are smaller than frontier cloud models, so InterGen's
  answers on hard tasks are less sharp.
- First use downloads the model — roughly 1.8 GB for the current
  release (the ~1.2 GB Tier-1 model plus its paired vision projector).
  After that, no network is needed.
- If you ask about a brand-new piece of software the model has not
  seen, it tells you so rather than guessing.

The trade-off you do not make is data exposure. For cases where you
want the depth of a frontier model, the optional Phone-A-Friend
(Frontier/Cloud Escalation) feature lets you opt in to a cloud
provider of your choice on a per-request basis — off by default, never
silent.


## How it scales to your hardware

InterGen is designed around hardware-detected model tiers. It inspects
your RAM and GPU at setup and picks the model tier automatically,
re-selecting if your hardware changes. **The Tier-1 model is the
universal floor — it runs on every install — and the Tier-2 model
ships and is selected automatically on capable hardware.** The Tier-3
model is still on the roadmap.

- **Tier 1 (~1.2 GB model)** — the floor, on every machine. Good for
  system queries, command lookups, and summarizing logs. Not built for
  writing code from scratch.
- **Tier 2 (~5.5 GB model)** — selected automatically on machines with
  8 to 15 GB of RAM *and a discrete GPU*, and used on larger machines too
  until the Tier-3 model ships: coding, configuration drafts, and
  multi-step reasoning. On a Tier-2 machine without a discrete GPU,
  InterGen runs the smaller Tier-1 model instead, so answers stay
  responsive.
- **Tier 3 (~21 GB model, roadmap)** — planned for machines with 16 GB
  or more of RAM *and* a discrete GPU: deep code analysis across
  multiple files and complex architectural questions. Until it ships,
  those machines run the Tier-2 model.

Both the Tier-1 and Tier-2 models are vision-capable: InterGen can look
at a screenshot or image you show it, not just the text you type.


## What it can and can't do (the safety chain)

Every action InterGen takes is classified before it runs:

- **AUTO** — read-only operations like `ls`, `df`, and `journalctl`.
  Run immediately, with the result shown to you.
- **CONFIRM** — anything that changes state, such as `systemctl
  restart`, `pkm install`, or editing a config file. InterGen pauses
  and shows you exactly what it intends to do. Nothing runs until you
  say yes.
- **BLOCKED** — destructive or security-bypass operations such as `rm
  -rf /`, formatting the root disk, or disabling Secure Boot from
  inside the running system. InterGen refuses and tells you why.

The classifier is conservative by design. If a command looks dangerous,
you will be asked. For a system assistant, the annoying-but-safe end of
the spectrum is the right end.


## Other apps can talk to it

InterGen exposes a D-Bus interface, so the text editor, the terminal,
the system settings panel, and any third-party app can send it
requests for code completion, log summarization, or semantic search.
Apps that want to integrate can read the protocol in the
[tool-author guide](https://github.com/InterGenJLU/intergenos/blob/master/docs/architecture/intergen-tool-author-guide.md).


## How to turn it on or off

InterGen is **off by default**. You opt in either at install time (the
"Enable the InterGen AI assistant?" toggle in Forge's package-selection
screen) or at any time later by running:

```
intergen setup
```

This downloads the model, enables the `intergen.service` user unit,
and starts the assistant. To opt out:

```
systemctl --user disable --now intergen.service
```

The model files stay on disk in case you want to re-enable later
without re-downloading. They live system-wide under
`/var/lib/intergen/models/`. To free the disk space, remove that
directory as root:

```
sudo rm -rf /var/lib/intergen/models/
```


## Where to read more

- [Architecture deep-dive](https://github.com/InterGenJLU/intergenos/blob/master/docs/components/intergen.md)
- [Tool-author guide](https://github.com/InterGenJLU/intergenos/blob/master/docs/architecture/intergen-tool-author-guide.md) (for app developers)
- [Provenance gate design](https://github.com/InterGenJLU/intergenos/blob/master/docs/architecture/intergen-provenance-gate-design.md)
- [Source code](https://github.com/InterGenJLU/intergenos/tree/master/intergen)
