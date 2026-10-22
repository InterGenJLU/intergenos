# Meet InterGen — the InterGenOS AI assistant

InterGen is the AI assistant that ships with InterGenOS. Its model runs
on your machine, answers questions about your system, and helps with
shell, configuration, and code tasks. It sends nothing in the background:
a web search you ask for, or accept when it is offered, sends its search
words to the search service and fetches the first result's page, and a
question goes to a cloud provider only if you set one up and approve the
send. The section "What leaves your machine" below lists every case.

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
- **"What's my current IP address?"** — reads your internal address with
  `ifconfig`, asks Cloudflare's public DNS resolver for your public one,
  and says both in plain English.
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
path needs no cloud account or API key and sends no project telemetry. The
trade-offs are honest:

- The local models are smaller than frontier cloud models, so InterGen's
  answers on hard tasks are less sharp.
- Setup downloads the model once; the next section gives the sizes and
  where the files come from. After that, local chat needs no network.
- Answers about unfamiliar software can still be wrong. Use an explicit web
  search or verify the answer against the software's own documentation instead
  of treating generated prose as a package recipe.

For cases where you want the depth of a frontier model, the optional
Phone-A-Friend (Frontier/Cloud Escalation) feature lets you configure a cloud
provider of your choice. No provider is configured by default; the next
section says how a question reaches one once you have set it up.

Two current limits are worth stating plainly. If the full wiki embedding index
does not finish during startup, wiki grounding stays on keyword matching for
that daemon run. Web-search routing is phrasing-sensitive; `search the web for
…` is the reliable form today.


## What leaves your machine

InterGen's model runs on your machine and sends nothing in the background.
Something leaves the machine only in these cases:

- **Setup, when you start it.** `intergen setup`, the Welcomer's AI
  Assistant page or Enable InterGen AI downloads the model that fits your
  hardware, its vision projector and a small embedding model: about 2.1 GB
  on Tier 1, 6.7 GB on Tier 2 and 23.1 GB on Tier 3. The files come from
  the InterGenOS mirror, and from Hugging Face only when the mirror cannot
  serve one; each file is checked against the hash pinned in the signed
  model manifest before it is used.
- **A web search you ask for, or accept when it is offered.** The search
  words go to DuckDuckGo; when a `SERPER_API_KEY` is set in the service's
  environment they go to Serper first, and to DuckDuckGo only if Serper
  fails or returns nothing. InterGen then fetches the first result's page
  from that page's own site (public addresses only, at most 128 KiB,
  within 8 seconds and 3 redirects) and reads it. A search the model
  decides on by itself is held and offered to you instead of being run.
- **A network check.** Read-only network commands such as `ping`, `dig`
  and `traceroute` run without a confirmation when a question calls for
  them, and reach the host they name. Asking for your IP address sends one
  DNS query to Cloudflare's public resolver to read your public IPv4
  address; the IPv6 one is looked up only if you accept the offer that
  follows. A command that downloads, such as `curl` or `wget`, asks you
  first.
- **Phone-A-Friend, only if you set up a provider.** A question goes to
  the provider only when you ask: the "Ask my frontier model" button that
  comes with an offer in the panel, or `intergen ask-frontier`. InterGen
  first shows you the exact text and the provider's name, and sends
  nothing until you approve. The `escalation.mode` setting (`ask` by
  default; `never`, `fallback` or `auto`) decides when InterGen offers
  this; `never` also refuses a send you ask for, and no mode sends
  without asking you first.


## How it scales to your hardware

InterGen is designed around hardware-detected model tiers. It picks a
tier from **two things only: whether the machine has a discrete GPU,
and how much video memory that GPU has.** System RAM is not an input.
A large model held in system memory is slow enough to be the wrong
answer no matter how much of it there is, so there is no decision RAM
could usefully inform. Detection runs at every start of the service, so a
hardware change is picked up at the next start.

Unknown capability always fails *down*. A discrete card whose video
memory cannot be read lands on the floor tier rather than on a tier it
might not be able to run.

Tier selection chooses **which model** to serve. GPU offload is a separate
fit calculation: when the required inputs are readable, the automatic setting
compares that model, its vision projector, and serving headroom with detected
video memory, then requests all layers when it fits or as many layers as fit
otherwise. A user-supplied
`llama_server.gpu_layers` value overrides the automatic plan.

- **Tier 1 — a 2-billion-parameter model, about 1.3 GB.** The universal
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
manifest is refused rather than served without it. Every picture is
checked before InterGen looks at it: its text is transcribed locally
and read by the same scanner that screens web pages and files, a
picture carrying an instruction aimed at the assistant or a command to
run is refused with its reason, and an admitted picture is marked as
untrusted content — words drawn inside a picture are never your request.


## What it can and can't do (the safety chain)

Every action InterGen takes is classified before it runs:

- **AUTO** — read-only operations like `ls`, `df`, and `journalctl --no-pager`.
  Run immediately, with the result shown to you.
- **CONFIRM** — anything that changes state, such as `systemctl
  restart`, `pkm install`, or editing a config file. InterGen pauses
  and shows you exactly what it intends to do. Nothing dispatches until you
  say yes; privileged actions then cross PolicyKit in a short-lived unit and
  return the runner's reported outcome.
- **BLOCKED** — destructive or privilege-escalating operations such as
  `rm -rf`, formatting a disk with `mkfs`, writing a disk with `dd`,
  `sudo`, or changing the firewall with `nft` or `iptables`. InterGen
  refuses and tells you why.

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

InterGen exposes a small D-Bus interface on your session bus
(`com.intergenos.InterGen`). `Ask` takes a question and returns the
answer, through the same router and safety chain as the panel; `Escalate`
makes a Phone-A-Friend request, with the same review before anything is
sent; `Status`, `GetTier`, `ResetConversation`, `PauseForGame` and
`ResumeAfterGame` report on or steer the service. The Welcomer and the
game-launch monitor use it today. Developers who want to give InterGen a
new tool can read the
[tool-author guide](https://github.com/InterGenJLU/intergenos/blob/master/docs/architecture/intergen-tool-author-guide.md).


## How to turn it on or off

InterGen is **off by default**. You can opt in at install time with Forge's
InterGen switch ("Enable InterGen" in the graphical installer, "Enable the
InterGen AI assistant?" in the text installer), from the first-run Welcomer's
AI Assistant page, or later from the Enable InterGen AI application. The
command line setup path is:

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
