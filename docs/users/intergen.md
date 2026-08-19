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

InterGen is designed around hardware-detected model tiers. It picks a
tier from **two things only: whether the machine has a discrete GPU,
and how much video memory that GPU has.** System RAM is not an input.
A large model held in system memory is slow enough to be the wrong
answer no matter how much of it there is, so there is no decision RAM
could usefully inform. Detection re-runs if your hardware changes.

Unknown capability always fails *down*. A discrete card whose video
memory cannot be read lands on the floor tier rather than on a tier it
might not be able to run.

- **Tier 1 — a 2-billion-parameter model, about 1.2 GB.** The universal
  floor: every machine with no discrete GPU, every discrete GPU with
  less than roughly 7 GB of video memory, and any card whose video
  memory cannot be read. Good for system queries, command lookups, and
  summarizing logs. Not built for writing code from scratch.
- **Tier 2 — a 9-billion-parameter model, about 5.6 GB.** Selected on a
  discrete GPU with roughly 7 GB of video memory or more: coding,
  configuration drafts, and multi-step reasoning.
- **Tier 3 — a 35-billion-parameter mixture-of-experts model, about
  22 GB.** Selected on a discrete GPU with roughly 22 GB of video
  memory or more: deep code analysis across several files and complex
  architectural questions.

If the machine's model store holds only a smaller model than its tier
calls for, InterGen serves that smaller model and says so, rather than
failing with nothing.

Every tier is vision-capable: InterGen can look at a screenshot or
image you show it, not just the text you type. A model that declares
vision but whose vision component is not pinned in the signed model
manifest is refused rather than served without it.


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
