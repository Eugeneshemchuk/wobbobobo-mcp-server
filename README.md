# Wobbo Bobo Ops & Creative Sandbox — MCP Server

A Model Context Protocol server that complements (not replaces) the n8n
Stage 1+2 automation. The n8n workflow owns the always-on, Telegram-triggered
pipeline; this server gives a conversational window into the same Postgres
state, plus a sandbox for testing the Direction/Hook/Script agents without a
full Telegram round-trip.

## What it adds

- **A missing piece the n8n pipeline has no home for**: logging TikTok view
  counts back against a project, and correlating performance by creative
  direction / hook style / video-gen model — the actual data your MVP-exit
  decision (100k views) depends on.
- **Conversational management** of the off-limits rules and hook-styles lists
  (same Postgres tables the n8n workflow reads/writes), instead of the
  Supabase SQL editor.
- **A sandbox** to call the Direction/Hook/Script agents directly and iterate
  on prompts, without waiting on a Telegram approval loop.

## Setup

```bash
cd mcp-server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: DATABASE_URL (same Supabase session-pooler string used in n8n),
# ANTHROPIC_API_KEY (same key used in the n8n Anthropic credential)
```

Then run the schema addition once (Supabase SQL Editor):

```bash
cat schema.sql
```

## Run it standalone (smoke test)

```bash
source .venv/bin/activate
python3 server.py
```

It should sit waiting on stdio — that's normal, it's meant to be driven by an
MCP client, not run interactively. Ctrl+C to stop.

## Register with Claude Code

```bash
claude mcp add wobbobobo -- python3 /Users/eugeneshemchuk/DEV/n8n/mcp-server/server.py
```

(Run `claude mcp add --help` if the flags above don't match your installed
version — the add subcommand's exact syntax has changed across releases.)

## Register with Claude Desktop

Add to your `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "wobbobobo": {
      "command": "python3",
      "args": ["/Users/eugeneshemchuk/DEV/n8n/mcp-server/server.py"]
    }
  }
}
```

Restart Claude Desktop after editing.

## What's exposed

**Resources** (readable data):
- `wobbobobo://off-limits-rules` — active off-limits rules
- `wobbobobo://hook-styles` — active rotating hook styles
- `wobbobobo://project/{project_id}` — a single project's full record

**Tools**:
- `list_projects(status?, limit?)` — browse projects by status
- `get_project(project_id)` — full detail on one project
- `log_video_performance(project_id, views)` — log a TikTok view count
- `correlate_performance(group_by)` — best-views breakdown by direction / hook_style / model_used
- `add_off_limits_rule(rule_text)` / `deactivate_off_limits_rule(id)`
- `add_hook_style(style_name, style_description)`
- `generate_direction(idea, verdict?, reasoning?)` — sandbox call to the Direction Agent
- `generate_hooks(idea, direction_tone, visual_style, why_it_could_hit)` — sandbox call to the Hook Agent
- `generate_script(idea, direction_tone, visual_style, why_it_could_hit, hooks)` — sandbox call to the Script Agent
- `generate_script_edit(...)` — sandbox call to the Script Agent's edit pass

## Known tradeoff

The Direction/Hook/Script prompts here are copied from the n8n workflow's
HTTP nodes, not shared from a single source. If you tune a prompt in one
place, update the other by hand — there's no shared package linking them.
