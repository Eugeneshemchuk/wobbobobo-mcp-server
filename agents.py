"""
Direction / Hook / Script agents for Wobbo Bobo.

These mirror the same three Claude calls used in the n8n Stage 1+2 workflow
(n8n-wobbobobo-stage1-2.json) so the creative logic stays consistent between
the automated pipeline and this sandbox. If you tune a prompt here, consider
updating the matching n8n HTTP node too (and vice versa) -- they are two
independent copies, not a shared source of truth.
"""

import os
from typing import Literal

import anthropic
from pydantic import BaseModel

MODEL = "claude-sonnet-5"

_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return _client


# ---------------------------------------------------------------------------
# Direction Agent
# ---------------------------------------------------------------------------

class DirectionResult(BaseModel):
    verdict: Literal["direction", "reject"]
    direction_tone: str
    visual_style: str
    why_it_could_hit: str
    reject_reason: str | None = None


DIRECTION_SYSTEM = """You are the Creative Direction Agent for a TikTok AI-video channel called "Wobbo Bobo". Given a video idea, decide the concrete creative direction that would make THIS idea hit hardest. Never force a fixed house style onto every idea.

Taste brief you must apply:
1. Commit fully to the bit. Absurdism only works when played straight, not ironic-distant.
2. Provocative but crafted. Willing to be weird/edgy, but production must read as intentional, never cheap.
3. Contrast is a joke engine. Incongruous pairings (e.g. recognizable character energy placed in a mundane setting) are a strong device.
4. Respect the audience even when playful. Never condescending, never filler.
5. No-slop test: every direction needs an actual point of view, joke, or payoff -- not a templated variable-swap.

Candidate direction anchors (not exhaustive, you are not limited to these three):
- Game-character cinematics: recognizable game characters (e.g. DOTA2/SC2 style) in absurd mundane contexts (office, street, farming).
- Absurdist/chaos content: fully committed, over-the-top absurdity.
- Fun-but-genuinely-educational content: playful, fast, never condescending, respects the viewer's intelligence.

Before deciding, check the idea against the off-limits rules provided. If it trips any of them, or if it genuinely lacks a strong angle, reject it instead of forcing a direction -- give a specific, useful reason.

Video length is capped at 17 seconds -- the direction must be executable in that time."""


async def generate_direction(
    idea: str, verdict: str, reasoning: list[str], off_limits_text: str
) -> DirectionResult:
    client = get_client()
    reasoning_text = "; ".join(reasoning) if reasoning else "(none provided)"
    response = await client.messages.parse(
        model=MODEL,
        max_tokens=600,
        system=DIRECTION_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Idea: {idea}\n\n"
                    f"Original feasibility notes -- verdict: {verdict or '(none)'}, "
                    f"reasoning: {reasoning_text}\n\n"
                    f"Off-limits rules:\n{off_limits_text}"
                ),
            }
        ],
        output_format=DirectionResult,
    )
    return response.parsed_output


# ---------------------------------------------------------------------------
# Hook Agent
# ---------------------------------------------------------------------------

class HookOption(BaseModel):
    style: str
    text: str


class NewHookStyle(BaseModel):
    style_name: str
    style_description: str


class HookResult(BaseModel):
    hooks: list[HookOption]
    new_styles: list[NewHookStyle] = []


HOOK_SYSTEM = """You are the Hook Agent for the TikTok channel "Wobbo Bobo". Given a video idea and its chosen creative direction, produce 3 distinct hook options for the first 3 seconds of the video, each pulled from a DIFFERENT style category in the list provided where possible.

Hook mechanics that apply regardless of style: the visual hook must land in frame 1 (no intro card, no logo card), the "why should I care" must land within 3 seconds, and any spoken/text hook line must be kept to one short line. Video length is capped at 17 seconds total.

If none of the existing categories fit well, you may invent a new style category for one of your 3 hooks -- if you do, include it in new_styles so it can be added to the rotating list for future use."""


async def generate_hooks(
    idea: str, direction_tone: str, visual_style: str, why_it_could_hit: str, hook_styles_text: str
) -> HookResult:
    client = get_client()
    response = await client.messages.parse(
        model=MODEL,
        max_tokens=700,
        system=HOOK_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Idea: {idea}\n"
                    f"Direction tone: {direction_tone}\n"
                    f"Visual style: {visual_style}\n"
                    f"Why it could hit: {why_it_could_hit}\n\n"
                    f"Available hook style categories:\n{hook_styles_text}"
                ),
            }
        ],
        output_format=HookResult,
    )
    return response.parsed_output


# ---------------------------------------------------------------------------
# Script Agent
# ---------------------------------------------------------------------------

class ScriptResult(BaseModel):
    script_text: str
    hook_used: HookOption
    runtime_estimate_seconds: float


SCRIPT_SYSTEM = """You are the Script Agent for the TikTok channel "Wobbo Bobo". Given a video idea, its chosen creative direction, and 3 candidate hooks, write the full script for a video capped at 17 seconds total.

Pick whichever ONE of the 3 hooks actually opens the script (your judgment -- the strongest fit for this idea), and note which one you used. Structure: Hook (0-3s, frame 1, no intro card) -> Body (matches the direction's tone, no fixed template) -> Payoff/loop -> end with the channel signature "Wobbo Bobo" spoken or shown on screen. Tone and voice follow the direction given -- do not impose a generic house style."""


async def generate_script(
    idea: str,
    direction_tone: str,
    visual_style: str,
    why_it_could_hit: str,
    hooks: list[HookOption],
) -> ScriptResult:
    client = get_client()
    hooks_text = "; ".join(f"[{h.style}] {h.text}" for h in hooks)
    response = await client.messages.parse(
        model=MODEL,
        max_tokens=900,
        system=SCRIPT_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Idea: {idea}\n"
                    f"Direction tone: {direction_tone}\n"
                    f"Visual style: {visual_style}\n"
                    f"Why it could hit: {why_it_could_hit}\n"
                    f"Candidate hooks: {hooks_text}"
                ),
            }
        ],
        output_format=ScriptResult,
    )
    return response.parsed_output


SCRIPT_EDIT_SYSTEM = """You are the Script Agent for the TikTok channel "Wobbo Bobo", revising an EXISTING script based on specific feedback -- not writing from scratch. Preserve what already works; change only what the feedback asks for, unless it necessitates a broader rewrite. Keep the same direction/tone unless told otherwise. You may keep the previous hook or swap it only if the edit instruction implies that. Video length stays capped at 17 seconds total."""


async def generate_script_edit(
    idea: str,
    direction_tone: str,
    visual_style: str,
    previous_script: str,
    previous_hook_style: str,
    edit_instruction: str,
) -> ScriptResult:
    client = get_client()
    response = await client.messages.parse(
        model=MODEL,
        max_tokens=900,
        system=SCRIPT_EDIT_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Idea: {idea}\n"
                    f"Direction tone: {direction_tone}\n"
                    f"Visual style: {visual_style}\n"
                    f"Previous script: {previous_script}\n"
                    f"Previous hook style: {previous_hook_style}\n"
                    f"User's edit instruction: {edit_instruction}"
                ),
            }
        ],
        output_format=ScriptResult,
    )
    return response.parsed_output
