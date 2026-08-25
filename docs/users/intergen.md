# Meet InterGen — the InterGenOS AI assistant

InterGen is the AI assistant that ships with InterGenOS. Its model runs
on your machine, answers questions about your system, and helps with
shell, configuration, and code tasks. Local questions stay on the box.
Network tools are explicit exceptions: web search sends the query to the
configured search provider, and an enabled Phone-A-Friend request sends the
selected conversation content to the cloud provider you chose.

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
- **"Install htop"** — recognizes the intent, shows `pkm install htop`,
  asks you to confirm, and dispatches the approved package action through
  the PolicyKit-backed privileged runner.
- **"Explain what this Python error means."** — returns the traceback
  with context.

InterGen is not a replacement for general chat tools. It is a **system
assistant**: its strength is knowing your machine, not the whole
world.


## Why local

Every model InterGen serves locally runs on your own CPU and GPU. The local
path needs no cloud account or API key and sends no project telemetry. A
network feature says what it contacts: web search sends its query to
DuckDuckGo or configured Serper, and Phone-A-Friend sends a request to the
provider you explicitly enable. The trade-offs are honest:

- The local models are smaller than frontier cloud models, so InterGen's
  answers on hard tasks are less sharp.
- First use downloads the model — roughly 1.8 GB for the current
  release (the ~1.2 GB Tier-1 model plus its paired vision projector).
  After that, local chat needs no network.
- Answers about unfamiliar software can still be wrong. Use an explicit web
  search or verify the answer against the software's own documentation instead
  of treating generated prose as a package recipe.

For cases where you want the depth of a frontier model, the optional
Phone-A-Friend (Frontier/Cloud Escalation) feature lets you configure a cloud
provider of your choice. No provider is configured by default. The default
`ask` mode requests approval before each send; the optional `fallback` and
`auto` modes send according to the policy you deliberately select.

Two current limits are worth stating plainly. If the full wiki embedding index
does not finish during startup, wiki grounding stays on keyword matching for
that daemon run. Web-search routing is phrasing-sensitive; `search the web for
…` is the reliable form today.


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

Tier selection chooses **which model** to serve. GPU offload is a separate
fit calculation: when the required inputs are readable, the automatic setting
compares that model, its vision projector, and serving headroom with detected
video memory, then requests all layers when it fits or as many layers as fit
otherwise. A user-supplied
`llama_server.gpu_layers` value overrides the automatic plan.

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

If the machine's model store holds only a smaller model than its tier calls
for, InterGen serves the largest downloaded model below that tier rather than
failing with nothing. `intergen status` shows the loaded model separately from
the hardware recommendation.

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
  and shows you exactly what it intends to do. Nothing dispatches until you
  say yes; privileged actions then cross PolicyKit in a short-lived unit and
  return the runner's reported outcome.
- **BLOCKED** — destructive or security-bypass operations such as `rm
  -rf /`, formatting the root disk, or disabling Secure Boot from
  inside the running system. InterGen refuses and tells you why.

The classifier is conservative by design. If a command looks dangerous,
you will be asked. For a system assistant, the annoying-but-safe end of
the spectrum is the right end.

InterGen-owned per-user state lives below `~/.local/state/intergen`,
`~/.local/share/intergen`, `~/.config/intergen`, and
`~/.cache/intergen`. New directories are created mode 0700 and files mode
0600. On its first R001.2 start in an existing home, the daemon removes group
and other permission bits from existing files inside those four trees once,
reports any path it could not inspect, and does not repeatedly undo later
sharing choices.


## Other apps can talk to it

InterGen exposes a D-Bus interface, so the text editor, the terminal,
the system settings panel, and any third-party app can send it
requests for code completion, log summarization, or semantic search.
Apps that want to integrate can read the protocol in the
[tool-author guide](https://github.com/InterGenJLU/intergenos/blob/master/docs/architecture/intergen-tool-author-guide.md).


## How to turn it on or off

InterGen is **off by default**. You can opt in at install time with Forge's
"Enable the InterGen AI assistant?" toggle, from the first-run Welcomer's AI
Assistant page, or later from the Enable InterGen AI application. The command
line setup path is:

```
intergen setup
```

This downloads the model and starts or restarts `intergen.service` for the
current user session. It does not make a disabled unit persistent by itself;
use `systemctl --user enable --now intergen.service` when enabling from the
command line. To opt out for your account even when Forge enabled the unit
globally:

```
systemctl --user mask --now intergen.service
```

To opt back in after masking, run `systemctl --user unmask intergen.service`,
then the enable command above.

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
