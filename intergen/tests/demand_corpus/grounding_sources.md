# Demand-corpus grounding sources (provenance registry)

Every `provenance.grounding` key in the JSONL bank resolves to one entry here. The corpus
tooling cross-checks that no entry cites an unregistered key. Keys are stable; add, never
rename. Percentages below anchor the demand-distribution weighting of this half.

## `openai-howpeopleuse-2025`
**OpenAI — "How People Use ChatGPT"** (Sep 2025). Privacy-preserving analysis of ~1.5M
conversations sampled from 700M weekly active users — the largest public study of real
consumer AI use to date. Headline distribution used to shape the demand half:
- Top-level topics: **Practical Guidance 28.8%**, **Seeking Information 24.4%**,
  **Writing 23.9%** (≈77% of all messages combined).
- Intent split: **Asking ≈49%**, **Doing ≈40%**, **Expressing ≈11%** — people value the
  assistant most as an advisor (Asking), with task-completion (Doing) a strong second.
- Non-work personal use ≈73% of consumer messages by mid-2025.
- Source: https://openai.com/index/how-people-are-using-chatgpt/

## `nber-w34255-chatgpt`
**NBER Working Paper w34255 — "How People Use ChatGPT"** (Chatterji et al.). The peer
academic write-up of the same OpenAI study; corroborates the topic taxonomy and the
Asking/Doing/Expressing intent framing above.
- Source: https://www.nber.org/system/files/working_papers/w34255/w34255.pdf

## `voice-assistant-tasks`
Common task classes for consumer voice assistants (Siri / Alexa / Google Assistant), from
usage surveys and how-to guides. The everyday DO-ask distribution a phone/desktop assistant
faces: smart-home/device control, navigation/directions, music & media playback, weather &
news, timers/reminders/alarms, calls & messages, translation, unit/quick math, jokes/trivia.
- Sources: https://www.todoist.com/inspiration/voice-assistant ,
  https://www.consumercellular.com/blog/beginners-guide-using-voice-assistants-siri-google-assistant-alexa/

## `askubuntu-linux4noobs`
The recurring ask classes on Ubuntu/Linux help forums (Ask Ubuntu, r/linux4noobs): wifi /
wireless-chipset problems, GPU (NVIDIA) driver install, dual-boot setup, package install &
`apt`/package-manager errors, file permissions & `sudo`, "no Photoshop → use GIMP" app
alternatives, disk space, and "how do I do X on the terminal".
- Sources: https://askubuntu.com/ , https://allthingsopen.org/articles/5-common-mistakes-new-linux-users

## `linux-beginner-firstweek`
What a new-to-Linux desktop user hits in week one: run a live distro to check hardware
(wireless, GPU, trackpad) support; install apps from the distribution's package manager, not
random installers; create a normal user and use `sudo` for elevation; pick/understand a
desktop environment; find replacements for Windows/Mac apps.
- Source: https://linux.slashdot.org/story/24/06/15/2212207/what-advice-would-you-give-a-first-time-linux-user

## `tecmint-basic-linux`
Concrete beginner Linux command asks in natural phrasing: list files, where am I, move
between folders, copy/move/delete files, how much disk space, how much memory, what's
running, change permissions, who's logged in, get help on a command, find files, run as
admin, shut down. Grounds the `system_info` / `file_management` / `howto_teach` natural
phrasings.
- Source: https://www.tecmint.com/linux-basic-questions/

---

# Surface-flex (code-grounded) sources

The surface-flex half is grounded not in the internet demand distribution but in
InterGen's OWN code + data files — it walks the real tool/route/state/howto/capability/
memory surface so every dispatchable aspect is flexed. Each key below resolves to the
in-tree ground-truth the surface-flex generator (`surface_flex_gen.py`) reads at
generation time, so a product schema change re-flows into the corpus on regeneration.

## `intergen-tool-registry`
The dispatchable tool surface: `intergen/tool_registry.py` (`_PRIVILEGED_TOOLS`,
`_classify_risk_tier`) + `intergen/tools/*.py` (the 9 registered BaseTool subclasses and
their action enums / safety tiers). Grounds the per-tool capability + dispatch-ask cells.

## `intergen-readonly-state-map`
`intergen/data/readonly-state-map.json` — the 16 read-only system-state classes
(disk-space, memory, cpu-info, gpu, kernel, hostname, uptime, printers, processes, …),
each with its probing command + `question_examples`. Grounds the state-question cells.

## `intergen-howto-corpus`
`intergen/data/howto/*.json` — the 15 howto domains / 160 entries, each with `triggers`
(example phrasings) and an optional teach-then-offer `action`. Grounds the teach-vs-act cells.

## `intergen-capability-surface`
`intergen/data/capability-surface.json` — the introspected ground truth of the `pkm`
subcommand set + the 9 intergen tools. Grounds the capability-question + fabrication-bait cells.

## `intergen-memory-patterns`
`intergen/memory.py` — the explicit-pattern classifiers (remember / recall / forget /
transparency / preference / complaint) + the SessionTurnIndex out-of-window recall.
Grounds the memory + antecedent-recall cells.

## `intergen-decomposer`
`intergen/decomposer.py` — the compound/decomposition restraint surface (pure-knowledge
whole vs mixed decompose vs arithmetic). Grounds the compound + math cells.

## `intergen-router`
`intergen/router.py` — the route-selection surface (offer/affirmative binding, tool-result
follow-up binding, wrong-tool vs local-state discrimination). Grounds the multi-turn offer
flows + follow-up + wrong-tool cells.
