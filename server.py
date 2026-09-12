"""
Wobbo Bobo Ops & Creative Sandbox -- MCP server.

Complements (does not replace) the n8n Stage 1+2 automation. The n8n workflow
owns the always-on Telegram-triggered pipeline; this server gives a
conversational window into the same Postgres state, plus a sandbox for
testing the Direction/Hook/Script agents without a full Telegram round-trip.

Run: `python server.py` (stdio transport -- for use with Claude Desktop/Code).
"""

import json
import uuid
from datetime import datetime
from typing import Literal

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

import db
from agents import (
    generate_direction as _generate_direction,
    generate_hooks as _generate_hooks,
    generate_script as _generate_script,
    generate_script_edit as _generate_script_edit,
    HookOption,
)

load_dotenv()

mcp = MCPServer("wobbobobo")


def _row_to_dict(row) -> dict:
    """Convert an asyncpg.Record to a plain JSON-serializable dict."""
    d = dict(row)
    for key, value in d.items():
        if isinstance(value, (uuid.UUID, datetime)):
            d[key] = str(value)
    if d.get("direction") and isinstance(d["direction"], str):
        try:
            d["direction"] = json.loads(d["direction"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@mcp.resource("wobbobobo://off-limits-rules")
async def off_limits_rules() -> str:
    """The current active off-limits rules the Direction Agent checks ideas against."""
    rows = await db.fetch(
        "SELECT id, rule_text FROM off_limits_rules WHERE active = true ORDER BY created_at"
    )
    if not rows:
        return "(no active off-limits rules)"
    return "\n".join(f"- [{r['id']}] {r['rule_text']}" for r in rows)


@mcp.resource("wobbobobo://hook-styles")
async def hook_styles() -> str:
    """The current active rotating hook styles the Hook Agent draws from."""
    rows = await db.fetch(
        "SELECT id, style_name, style_description, source FROM hook_styles "
        "WHERE active = true ORDER BY created_at"
    )
    if not rows:
        return "(no active hook styles)"
    return "\n".join(
        f"- [{r['id']}] {r['style_name']} ({r['source']}): {r['style_description']}"
        for r in rows
    )


@mcp.resource("wobbobobo://project/{project_id}")
async def project_resource(project_id: str) -> str:
    """Full record for a single video project."""
    row = await db.fetchrow("SELECT * FROM video_projects WHERE project_id = $1", project_id)
    if row is None:
        return f"No project found with id {project_id}"
    return json.dumps(_row_to_dict(row), indent=2, default=str)


# ---------------------------------------------------------------------------
# Ops tools -- inspect pipeline state
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_projects(status: str | None = None, limit: int = 20) -> list[dict]:
    """List video projects, optionally filtered by status (e.g. 'script_approved',
    'script_rejected', 'awaiting_checkpoint_2'). Most recently updated first."""
    if status:
        rows = await db.fetch(
            "SELECT project_id, chat_id, idea_raw, idea_refined, status, direction, "
            "hook_style, updated_at FROM video_projects WHERE status = $1 "
            "ORDER BY updated_at DESC LIMIT $2",
            status,
            limit,
        )
    else:
        rows = await db.fetch(
            "SELECT project_id, chat_id, idea_raw, idea_refined, status, direction, "
            "hook_style, updated_at FROM video_projects ORDER BY updated_at DESC LIMIT $1",
            limit,
        )
    return [_row_to_dict(r) for r in rows]


@mcp.tool()
async def get_project(project_id: str) -> dict:
    """Full record for a single video project by id."""
    row = await db.fetchrow("SELECT * FROM video_projects WHERE project_id = $1", project_id)
    if row is None:
        return {"error": f"No project found with id {project_id}"}
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Performance feedback loop -- the gap n8n has no home for
# ---------------------------------------------------------------------------

@mcp.tool()
async def log_video_performance(project_id: str, views: int) -> dict:
    """Log a TikTok view count for a published project. Call this whenever you
    check a video's stats -- log it multiple times over its life to track growth."""
    row = await db.fetchrow(
        "INSERT INTO video_performance (project_id, views) VALUES ($1, $2) "
        "RETURNING id, project_id, views, checked_at",
        project_id,
        views,
    )
    return _row_to_dict(row)


@mcp.tool()
async def correlate_performance(
    group_by: Literal["direction", "hook_style", "model_used"] = "direction",
) -> list[dict]:
    """Break down best-known view count (max logged so far) per project, grouped
    by direction tone / hook style / video-gen model, to see what's winning."""
    column_map = {
        "direction": "p.direction->>'tone'",
        "hook_style": "p.hook_style",
        "model_used": "p.model_used",
    }
    group_col = column_map[group_by]
    rows = await db.fetch(
        f"""
        WITH best AS (
            SELECT project_id, MAX(views) AS best_views
            FROM video_performance
            GROUP BY project_id
        )
        SELECT {group_col} AS group_value,
               COUNT(*) AS video_count,
               ROUND(AVG(best.best_views)) AS avg_views,
               MAX(best.best_views) AS max_views
        FROM video_projects p
        JOIN best ON best.project_id = p.project_id
        WHERE {group_col} IS NOT NULL
        GROUP BY {group_col}
        ORDER BY avg_views DESC NULLS LAST
        """
    )
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Manage the editable lists
# ---------------------------------------------------------------------------

@mcp.tool()
async def add_off_limits_rule(rule_text: str) -> dict:
    """Add a new off-limits rule for the Direction Agent to check ideas against."""
    row = await db.fetchrow(
        "INSERT INTO off_limits_rules (rule_text) VALUES ($1) RETURNING id, rule_text, active",
        rule_text,
    )
    return _row_to_dict(row)


@mcp.tool()
async def deactivate_off_limits_rule(rule_id: str) -> dict:
    """Deactivate an off-limits rule by id (soft delete -- keeps history)."""
    row = await db.fetchrow(
        "UPDATE off_limits_rules SET active = false WHERE id = $1 "
        "RETURNING id, rule_text, active",
        rule_id,
    )
    if row is None:
        return {"error": f"No off-limits rule found with id {rule_id}"}
    return _row_to_dict(row)


@mcp.tool()
async def add_hook_style(style_name: str, style_description: str) -> dict:
    """Add a new hook style to the rotating list (source is marked 'manual')."""
    row = await db.fetchrow(
        "INSERT INTO hook_styles (style_name, style_description, source) "
        "VALUES ($1, $2, 'manual') RETURNING id, style_name, style_description, source",
        style_name,
        style_description,
    )
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Creative sandbox -- same agents/prompts as the n8n pipeline, callable directly
# ---------------------------------------------------------------------------

@mcp.tool()
async def generate_direction(idea: str, verdict: str = "", reasoning: list[str] | None = None) -> dict:
    """Run the Direction Agent standalone on an idea (sandbox -- does not touch
    the database). Checks the idea against the live off-limits list."""
    off_limits_text = await off_limits_rules()
    result = await _generate_direction(idea, verdict, reasoning or [], off_limits_text)
    return result.model_dump()


@mcp.tool()
async def generate_hooks(idea: str, direction_tone: str, visual_style: str, why_it_could_hit: str) -> dict:
    """Run the Hook Agent standalone (sandbox). Draws from the live hook-styles
    list but does NOT auto-append any new style it invents -- call add_hook_style
    yourself if you like one it suggests."""
    hook_styles_text = await hook_styles()
    result = await _generate_hooks(idea, direction_tone, visual_style, why_it_could_hit, hook_styles_text)
    return result.model_dump()


@mcp.tool()
async def generate_script(
    idea: str,
    direction_tone: str,
    visual_style: str,
    why_it_could_hit: str,
    hooks: list[dict],
) -> dict:
    """Run the Script Agent standalone (sandbox). `hooks` should be a list of
    {"style": ..., "text": ...} dicts, e.g. from generate_hooks' output."""
    hook_options = [HookOption(**h) for h in hooks]
    result = await _generate_script(idea, direction_tone, visual_style, why_it_could_hit, hook_options)
    return result.model_dump()


@mcp.tool()
async def generate_script_edit(
    idea: str,
    direction_tone: str,
    visual_style: str,
    previous_script: str,
    previous_hook_style: str,
    edit_instruction: str,
) -> dict:
    """Run the Script Agent's edit pass standalone (sandbox) -- revises an
    existing script per free-text feedback, same as Checkpoint 2's Edit action."""
    result = await _generate_script_edit(
        idea, direction_tone, visual_style, previous_script, previous_hook_style, edit_instruction
    )
    return result.model_dump()


if __name__ == "__main__":
    mcp.run()
