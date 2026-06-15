---
description: Run a complete autonomous Maltego investigation on a target (domain, email, IP, or URL) and present a finished briefing.
---

You are driving a Maltego OSINT investigation. The target is: **$ARGUMENTS**

Do this autonomously, without pausing to ask between read-only steps:

1. Call the `maltego_investigate` tool with `query` = the target above (leave
   `allow_network` at its default unless the user asked for offline/passive).
2. It returns ONE complete briefing — detection, important discoveries, ranked
   next-best-actions, and an inline report. Present that briefing to the user
   clearly and concisely.
3. In a single closing line, offer to **save** the graph as a `.mtgx`
   (`maltego_save_graph`, which opens in Maltego CE) or **export** a report
   (`maltego_export_report`). Do not write any files unless the user asks.

Only stop and ask the user if you are genuinely blocked:
- an OSINT provider needs an API key that isn't configured (name the env var), or
- a file would be overwritten, or
- the target is ambiguous.

If `$ARGUMENTS` is empty, ask the user what to investigate (one short question).
