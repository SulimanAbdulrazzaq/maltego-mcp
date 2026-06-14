# 🕵️ maltego-mcp

> **An AI-assisted Maltego CE investigation copilot.** Build, expand, analyze, and
> report on OSINT investigation graphs through natural language — exported as native
> Maltego `.mtgx` files.

![MCP](https://img.shields.io/badge/MCP-server-blue)
![Claude Code Plugin](https://img.shields.io/badge/Claude%20Code-plugin-7c3aed)
![Python](https://img.shields.io/badge/python-3.10%2B-green)
![License](https://img.shields.io/badge/license-MIT-lightgrey)
![Tools](https://img.shields.io/badge/tools-49-orange)
![Tests](https://img.shields.io/badge/tests-62%20passing-brightgreen)

An [MCP](https://modelcontextprotocol.io) server that turns an LLM into a
**Maltego CE investigation copilot** — an AI-assisted OSINT platform built on
Maltego Community Edition's native graph format.

---

## Contents

- [How it works](#how-it-works)
- [Capabilities at a glance](#capabilities-at-a-glance)
- [Install as a Claude Code plugin (recommended)](#install-as-a-claude-code-plugin-recommended)
- [Manual / pip install](#manual--pip-install)
- [Tools](#tools) · [full reference → `TOOLS.md`](TOOLS.md)
- [Transforms by provider](#transforms-by-provider)
- [Example workflows](#example-workflows)
- [Verifying a graph opens in Maltego CE](#verifying-a-graph-opens-in-maltego-ce)
- [Extending](#extending) · [Architecture](#architecture) · [Notes & limitations](#notes--limitations)

## How it works

Maltego CE has no live external API. Instead of trying to remote-control the
desktop UI, this server uses Maltego's **native `.mtgx` graph file** (a ZIP
archive of GraphML XML) as the integration surface:

1. The LLM builds / reads / edits an investigation graph held **in memory**.
2. Transforms — and high-level **investigation workflows / machines** — expand
   the graph (e.g. a domain → its IP addresses → open ports).
3. The LLM **analyzes** the graph (summaries, pivots, next steps), lays it out,
   and generates a shareable **report**.
4. The graph is saved as a **`.mtgx` file** that you open (or refresh) in
   Maltego CE.

A pluggable **transform-provider layer** keeps the design extensible: the
built-in `local` provider needs no API keys, and external OSINT providers
(VirusTotal, Shodan, SecurityTrails, Censys, Hunter.io, HaveIBeenPwned) activate
automatically when their API keys are present — all without changing the graph
core or the MCP tools.

### Capabilities at a glance

| Area | What you get |
|------|--------------|
| **Build graphs** | Create/edit entities & links; save/open native `.mtgx`. |
| **High-level workflows** | `investigate_domain/email/ip` — one call runs many transforms. |
| **Unified entry point** | `maltego_investigate(query)` — detect, build, expand, analyze, recommend in one call. |
| **Investigation Memory** | Procedural memory: records *why/how* each step ran; queryable; travels in the `.mtgx`. |
| **Next Best Action** | Deterministic, explainable, memory-aware ranking of the most valuable next move. |
| **Risk & confidence** | Per-entity confidence, source reliability, linkage, priority, novelty scores. |
| **Real-time mode** | Optional event stream (entity_discovered, transform_*, report_generated, …). |
| **Investigation machines** | Reusable templates (Passive Domain, Email, Infrastructure Mapping). |
| **OSINT providers** | VirusTotal, Shodan, SecurityTrails, Censys, Hunter.io, HIBP (env-var keys). |
| **AI analysis** | Summarize, explain entity, identify pivots, suggest next steps. |
| **Layout** | Deterministic hierarchical / radial / force-directed layouts. |
| **CSV import** | Bulk-build graphs from `type,value` CSV. |
| **Reporting** | Deterministic Markdown / HTML investigation reports (now incl. quality scores). |
| **Continuation** | Load or merge existing `.mtgx` investigations and keep working. |

## Install as a Claude Code plugin (recommended)

This repo is both a **plugin** and a **plugin marketplace**. Install it straight from GitHub:

```shell
# in Claude Code
/plugin marketplace add SulimanAbdulrazzaq/maltego-mcp
/plugin install maltego-mcp@maltego-mcp
```

The bundled MCP server is launched with [`uv`](https://docs.astral.sh/uv/) via `uvx`, which
auto-installs the Python dependencies in an isolated environment — **no manual `pip install`
needed**. Install `uv` once if you don't have it:

```shell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

After install, the `maltego_*` tools are available in Claude. Set OSINT keys (optional) in your
shell before starting Claude Code to enable those providers (see
[Transforms by provider](#transforms-by-provider)).

> Prefer not to use `uv`? See [Manual / pip install](#manual--pip-install) and point the MCP
> command at the venv's `maltego-mcp` executable instead.

### Windows troubleshooting (first launch)

On Windows, the very first launch occasionally fails with
`failed to rename ... Access is denied (os error 5)` while unpacking `pywin32` (a transitive
dependency of `mcp`). This is **Windows Defender** briefly locking the files during real-time
scanning — not a bug in the plugin. Fixes:

- **Just retry** — toggle the plugin off/on or restart Claude Code; it succeeds once Defender
  finishes scanning and the dependency is cached (verified: it works on the second attempt).
- Or pre-warm the cache once from a terminal: `uvx --from <path-to-this-repo> maltego-mcp`
  (Ctrl-C after it prints nothing — it's a stdio server), then retry in Claude Code.
- Or add an exclusion for `%LOCALAPPDATA%\uv\cache` in Windows Security → Virus & threat
  protection → Exclusions.

## Manual / pip install

```bash
cd maltego-mcp
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -e .
```

Then register the stdio server with any MCP client, e.g.:

```json
{
  "mcpServers": {
    "maltego": { "command": ".../.venv/Scripts/maltego-mcp.exe" }
  }
}
```

## Running

The server speaks MCP over **stdio** (ideal for local desktop integration):

```bash
maltego-mcp
# or
python -m maltego_mcp.server
```

### Register with an MCP client

Example `mcp.json` / client config entry:

```json
{
  "mcpServers": {
    "maltego": {
      "command": "C:/Users/you/Desktop/maltego_mcp/.venv/Scripts/maltego-mcp.exe"
    }
  }
}
```

(Use the `.venv` Python on macOS/Linux: `"command": ".../.venv/bin/maltego-mcp"`.)

### Test with the MCP Inspector

```bash
npx @modelcontextprotocol/inspector maltego-mcp
```

## Tools

**49 tools.** The tables below group them by area. For the **complete reference with every
argument, type, and default**, see [`TOOLS.md`](TOOLS.md) (auto-generated from the live tool
schemas — the file an AI agent should read to learn the exact interface).

### Graph management
| Tool | Description |
|------|-------------|
| `maltego_create_graph` | Create a new empty in-memory graph (becomes active). |
| `maltego_open_graph` | Load an existing `.mtgx` file into memory. |
| `maltego_save_graph` | Write a graph to a `.mtgx` file for Maltego CE. |
| `maltego_list_graphs` | List open graphs and show the active one. |
| `maltego_set_active_graph` | Switch the active graph. |

### Entities & links
| Tool | Description |
|------|-------------|
| `maltego_add_entity` | Add a node (domain, IP, person, email, …). |
| `maltego_add_link` | Add a directed link between two entities. |
| `maltego_list_entities` | List/filter/paginate entities on the active graph. |
| `maltego_get_entity` | Full details for one entity. |
| `maltego_update_entity` | Update value/properties/notes/weight. |
| `maltego_delete_entity` | Delete an entity and its links. |
| `maltego_list_entity_types` | Discover supported Maltego entity types. |

### Transforms
| Tool | Description |
|------|-------------|
| `maltego_list_transforms` | List available transforms (optionally by input type; shows availability). |
| `maltego_run_transform` | Run a single transform on an entity and add the results. |

### High-level investigation workflows
| Tool | Description |
|------|-------------|
| `maltego_investigate_domain` | Seed a domain and auto-run all applicable/available transforms. |
| `maltego_investigate_email` | Seed an email and auto-investigate (domain, breaches, footprint). |
| `maltego_investigate_ip` | Seed an IP and auto-investigate (reverse DNS, ports, services). |
| `maltego_list_machines` | List investigation machines (workflow templates). |
| `maltego_run_machine` | Run a machine (e.g. `passive_domain`) against a seed value. |

### Unified entry point (primary interface for agents)
| Tool | Description |
|------|-------------|
| `maltego_investigate` | Detect type → build/reuse graph → run a machine → layout → analyze → recommend, all in one call, recording everything to Investigation Memory. |

### AI-oriented analysis (the "copilot")
| Tool | Description |
|------|-------------|
| `maltego_summarize_graph` | Deterministic overview: counts, type breakdown, key/isolated entities. |
| `maltego_explain_entity` | Explain one entity: data, neighbours, applicable transforms. |
| `maltego_identify_pivots` | Rank the most-connected pivot entities. |
| `maltego_suggest_next_steps` | Simple heuristic suggestions (retained for compatibility). |
| `maltego_next_best_actions` | **Decision engine**: deterministic, explainable, memory-aware ranking of the best next move. |

### Investigation Memory (procedural memory)
| Tool | Description |
|------|-------------|
| `maltego_list_investigation_steps` | List recorded steps (what ran, why, outcome, importance). |
| `maltego_explain_why` | Trace an entity's provenance — which transform/step discovered it and why. |
| `maltego_explain_transform` | Full detail of one recorded transform execution (by execution id). |
| `maltego_get_investigation_timeline` | Chronological narrative of the whole investigation. |

### Risk & confidence scoring
| Tool | Description |
|------|-------------|
| `maltego_score_entity` | Confidence, source reliability, linkage strength, priority, novelty for one entity. |
| `maltego_rank_entities` | Rank all entities by investigation priority. |
| `maltego_explain_scores` | Scores plus a deterministic rationale for one entity. |

### Real-time mode (optional)
| Tool | Description |
|------|-------------|
| `maltego_subscribe_events` | Enable live event mode; returns a subscription id. |
| `maltego_get_recent_events` | Poll recent events (supports `since_seq` incremental polling). |

### Layout, CSV, import/export, link & graph management
| Tool | Description |
|------|-------------|
| `maltego_apply_layout` | Assign positions (`hierarchical`/`radial`/`force`), saved into the `.mtgx`. |
| `maltego_import_csv` | Build entities/links from a CSV file or inline CSV text. |
| `maltego_export_csv` | Export entities to CSV (round-trips with import). |
| `maltego_export_json` | Export the full graph + memory + scores as JSON. |
| `maltego_list_links` / `maltego_delete_link` | List or delete individual links. |
| `maltego_rename_graph` / `maltego_delete_graph` | Rename or drop an open graph. |
| `maltego_list_providers` | List OSINT providers and whether their API keys are configured. |
| `maltego_generate_report` / `maltego_export_report` | Markdown/HTML report inline or to a file. |
| `maltego_load_graph` / `maltego_import_graph` | Open a `.mtgx` as a new graph, or merge into the active one. |

### Outcome-based learning (opt-in)
| Tool | Description |
|------|-------------|
| `maltego_learning_stats` | View cross-investigation transform success/yield history. |
| `maltego_reset_learning` | Clear the learning store. |

Learning is **off by default** (keeps results deterministic). Enable with
`MALTEGO_MCP_LEARNING=1` (or `MALTEGO_MCP_LEARNING_PATH=/path/to/learning.json`). When on, the
server records per-transform outcomes across investigations and lets that history nudge
`maltego_next_best_actions`.

### Transforms by provider

Built-in `local` provider — **no API key required**:

- `dns.domain_to_ip` — domain/DNS name → IPv4 addresses (DNS A record) *[network]*
- `dns.ip_to_host` — IPv4 address → hostname (reverse DNS / PTR) *[network]*
- `parse.url_to_domain` — URL → domain *(offline)*
- `parse.email_to_domain` — email → domain *(offline)*
- `parse.domain_to_website` — domain → Website entity *(offline)*

External OSINT providers — **activate when their env-var key is set** (see
`maltego_list_providers`). Transforms are always *listed* but only *run* when
configured; a missing key yields an actionable message rather than an error.

| Provider | Env var(s) | Example transforms |
|----------|-----------|--------------------|
| VirusTotal | `VIRUSTOTAL_API_KEY` | `vt.domain_to_ip`, `vt.domain_to_subdomains`, `vt.ip_to_domain` |
| Shodan | `SHODAN_API_KEY` | `shodan.ip_to_info`, `shodan.domain_to_subdomains` |
| SecurityTrails | `SECURITYTRAILS_API_KEY` | `securitytrails.domain_to_subdomains`, `securitytrails.domain_to_dns` |
| Censys | `CENSYS_API_ID`, `CENSYS_API_SECRET` | `censys.ip_to_services` |
| Hunter.io | `HUNTER_API_KEY` | `hunter.domain_to_emails` |
| Have I Been Pwned | `HIBP_API_KEY` | `hibp.email_to_breaches` |

Configure keys via environment variables (in your MCP client's `env` block or
the shell), then restart the server. Example client config:

```json
{
  "mcpServers": {
    "maltego": {
      "command": ".../.venv/Scripts/maltego-mcp.exe",
      "env": { "VIRUSTOTAL_API_KEY": "...", "SHODAN_API_KEY": "..." }
    }
  }
}
```

## Example workflows

**Manual (low-level):**
```
maltego_create_graph(name="acme-recon")
maltego_add_entity(type="maltego.Domain", value="example.com")        # -> n0
maltego_run_transform(transform_name="dns.domain_to_ip", entity_id="n0")
maltego_apply_layout(algorithm="hierarchical")
maltego_save_graph(path="C:/Users/you/Desktop/acme-recon.mtgx")
```

**One-call copilot (recommended for agents):**
```
maltego_investigate(query="bob@example.com")   # detect → build → expand → analyze → recommend
maltego_explain_why(entity_id="n1")            # why is this entity here?
maltego_next_best_actions()                    # explainable, memory-aware ranking
maltego_rank_entities()                        # focus on the highest-priority findings
maltego_get_investigation_timeline()           # the reasoning trace
maltego_generate_report(format="html")
maltego_save_graph(path="C:/cases/example.mtgx")   # memory travels inside the .mtgx
```

**AI-assisted (step-by-step):**
```
maltego_investigate_domain(value="example.com")     # auto-runs transforms
maltego_identify_pivots()                            # find key nodes
maltego_next_best_actions()                          # what to do next (decision engine)
maltego_apply_layout(algorithm="radial")
maltego_generate_report(format="markdown")           # shareable report
maltego_save_graph(path="C:/Users/you/Desktop/example.mtgx")
```

**Continue a previous investigation:**
```
maltego_load_graph(path="C:/cases/old.mtgx")         # reopen
maltego_run_machine(machine_name="infrastructure_mapping", seed_value="example.com")
maltego_import_graph(path="C:/cases/related.mtgx")   # merge in another case
```

**Bulk import from CSV:**
```
maltego_import_csv(content="type,value,link_to\nDomain,example.com,\nIPv4Address,1.2.3.4,example.com\n")
```

## Extending

### Add a new OSINT provider

1. Create a module under `src/maltego_mcp/transforms/osint/`.
2. Write pure parser functions (`dict -> list[ResultEntity]`) and async `run`
   functions that read the API key from an env var via `require_keys(...)`.
3. Register a `ProviderInfo` with `providers.register(...)` and your
   `Transform(...)` objects (with `api_key_env=...`) via `registry.register(...)`.
4. Import the module from `transforms/osint/__init__.py`.

No changes to the graph core, orchestration, machines, or MCP tools are needed —
new transforms automatically participate in `investigate_*`, machines, analysis,
and suggestions.

### Add an investigation machine

```python
from maltego_mcp.machines import Machine, register_machine
register_machine(Machine(
    name="my_workflow", display_name="My Workflow", description="...",
    seed_type="maltego.Domain",
    transform_names=["dns.domain_to_ip", "vt.domain_to_subdomains"],
    allow_network=True, max_rounds=2,
))
```

## Architecture

```
src/maltego_mcp/
├── server.py          # FastMCP server + all 49 tool registrations
├── models.py          # Pydantic input models
├── entities.py        # Maltego entity-type catalog
├── formatting.py      # markdown/JSON response helpers + error mapping
├── detect.py          # query -> entity type/value/machine (for maltego_investigate)
├── orchestration.py   # breadth-first engine + run_and_record (memory+events choke-point)
├── machines.py        # reusable workflow templates + registry
├── analysis.py        # deterministic summarize/explain/pivots/next-steps
├── recommendation.py  # Next Best Action decision engine (memory-aware, explainable)
├── scoring.py         # Risk & confidence engine (deterministic per-entity metrics)
├── memory.py          # Investigation Memory (procedural memory; storage + queries)
├── learning.py        # opt-in cross-investigation outcome learning (feeds NBA)
├── events.py          # architecture-agnostic event bus (real-time mode)
├── layout.py          # hierarchical / radial / force-directed layouts
├── csv_import.py      # CSV -> entities/links (type aliases, dedupe)
├── reporting.py       # deterministic Markdown / HTML reports (incl. quality scores)
├── graph/
│   ├── graph_store.py # in-memory Graph (+ .memory, merge, analysis helpers)
│   ├── mtgx_writer.py # Graph -> .mtgx (GraphML + positions + memory sidecar + zip)
│   └── mtgx_reader.py # .mtgx -> Graph (recovers positions + memory sidecar)
└── transforms/
    ├── base.py        # Transform/registry + ProviderInfo/ProviderRegistry (+ reliability)
    ├── local.py       # built-in no-auth transforms
    └── osint/         # external providers (VT, Shodan, SecurityTrails, ...)
        ├── base_http.py
        └── virustotal.py, shodan.py, securitytrails.py, censys.py, hunterio.py, hibp.py
```

### Investigation Memory & determinism

* **Procedural memory** (`memory.py`) records every transform execution — the
  trigger entity, the chosen transform, *why* it was chosen, what it discovered,
  status, importance, and whether to reconsider it. It is stored on
  `Graph.memory`, kept *separate from the graph structure*, and serialized to a
  sidecar member (`maltego_mcp/investigation_memory.json`) inside the `.mtgx` —
  so it travels with the investigation but never affects Maltego CE
  compatibility (Maltego ignores unknown archive members).
* `orchestration.run_and_record` is the single choke-point through which the
  engine *and* the manual `maltego_run_transform` tool execute transforms, so
  memory and events are captured consistently everywhere.
* **Scores** (`scoring.py`) are computed deterministically from the graph + memory
  and provider reliability (`ProviderInfo.reliability`), so the same investigation
  always yields the same scores, recommendations, and reports.

## Verifying a graph opens in Maltego CE

The `.mtgx` format is validated by our reader/writer round-trip and by checking entity
property field names against Maltego's real definitions (e.g. `Domain`/`Website` → `fqdn`,
`Company`/`Organization` → `title`, `IPv4Address` → `ipv4-address`). To confirm end-to-end in
the actual app:

1. Install **Maltego CE** (free; requires a download + account at maltego.com).
2. Generate a sample: `maltego_investigate("example.com")` (or add a few entities) then
   `maltego_save_graph(path="…/sample.mtgx")`. A ready-made `sample-verification.mtgx` is
   produced on the Desktop by the test fixtures.
3. In Maltego CE: **File → Import → Import Graph** (or File → Open) and select the `.mtgx`.
4. Confirm entities render **with their values populated**, correct types, and links with
   labels. If a given type's value is blank, its `main_property` in `src/maltego_mcp/entities.py`
   needs correcting against Maltego's field id for that entity.

> Status: the common entity types (Domain, IP, Email, Website, Person, Company, …) have had
> their field names verified against a reference; opening in a real Maltego CE install is the
> final confirmation step and is left to the user (no Maltego install was available here).

## Notes & limitations

- Targets Maltego CE's file format; it does **not** remote-control the running
  desktop app. Re-open or refresh the `.mtgx` in Maltego after saving.
- The entity-type catalog is a curated subset; custom `maltego.*` types are
  accepted and saved as-is.
- Graphs live in process memory until saved; restarting the server clears unsaved
  graphs.
- Layout positions are written into the `.mtgx` as yFiles node graphics. Maltego
  CE may re-run its own layout on import; positions are always available via the
  tools regardless.
- OSINT provider transforms call third-party APIs — respect each provider's terms
  of service and rate limits. Without a key, those transforms are listed but skip
  with a clear "missing credential" message.
- Reports, layouts, scores, and recommendations are **deterministic**: the same
  graph + memory always yields the same output, so results are reproducible and
  shareable. (Timestamps in memory/events are the only non-deterministic field.)
- **Investigation Memory** is stored as a sidecar JSON member inside the `.mtgx`
  and is recovered on load — it survives save/load and merges, and is ignored by
  Maltego CE. Manually-added/CSV entities are "analyst-provided" (no discovering
  step) and scored with full source reliability.
- **Real-time mode** is optional: the event bus always buffers cheaply, and
  `maltego_subscribe_events` enables live callbacks. Over MCP stdio you retrieve
  events by polling `maltego_get_recent_events` (use `since_seq` for increments).

## License

MIT
