"""Cekura evaluation + autonomous prompt improvement.

Evaluation dimensions:
  1. Empathy score     — did Future Me feel emotionally resonant?
  2. Plan accuracy     — were the 30/90/365 actions specific and achievable?
  3. Narrative coherence — did the structure hold across all 3 acts?

Auto-improvement loop:
  Cekura scores → failures → Claude rewrites intake/simulation prompts →
  new version saved to DB → all subsequent calls use improved prompts.
"""

import json
import os

import aiohttp
import anthropic
from loguru import logger

from memory import get_active_prompt_override, save_prompt_version
from channels import get_channel

CEKURA_BASE = "https://api.cekura.ai/v1"
SCORE_THRESHOLD = 0.75


# ── Cekura API ────────────────────────────────────────────────────────────────

async def fetch_latest_evaluation(agent_id: str) -> dict | None:
    api_key = os.getenv("CEKURA_API_KEY", "")
    if not api_key:
        logger.warning("CEKURA_API_KEY not set — skipping")
        return None
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{CEKURA_BASE}/agents/{agent_id}/evaluations/latest",
            headers={"Authorization": f"Bearer {api_key}"},
        ) as resp:
            if resp.status != 200:
                logger.error(f"Cekura API {resp.status}")
                return None
            return await resp.json()


def extract_failures(evaluation: dict) -> list[dict]:
    return [
        {
            "scenario": s.get("description", ""),
            "transcript": s.get("transcript", ""),
            "score": s.get("score", 1.0),
            "feedback": s.get("feedback", ""),
        }
        for s in evaluation.get("scenarios", [])
        if s.get("score", 1.0) < SCORE_THRESHOLD
    ]


# ── Claude auto-improvement ───────────────────────────────────────────────────

async def improve_intake_prompt(failures: list[dict], current_prompt: str) -> str | None:
    """Use Claude to rewrite the intake prompt to fix failing scenarios."""
    client = anthropic.AsyncAnthropic()
    message = await client.messages.create(
        model="claude-opus-4-8",
        max_tokens=2048,
        system=(
            "You are an expert voice AI prompt engineer for a product called 'Call Me Tomorrow'. "
            "The intake bot asks 5 questions before handing the call to a 'Future Me' simulation. "
            "You receive the current intake prompt and failing Cekura test scenarios with feedback. "
            "Rewrite the intake prompt to fix the failures. "
            "Return ONLY the improved prompt text, nothing else."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"Current intake prompt:\n```\n{current_prompt}\n```\n\n"
                f"Failing scenarios:\n```json\n{json.dumps(failures, indent=2)}\n```\n\n"
                "Rewrite to fix these specific failure modes. "
                "Preserve the 5-question structure and voice style."
            ),
        }],
    )
    return message.content[0].text.strip()


# ── Main loop ─────────────────────────────────────────────────────────────────

async def run_feedback_loop() -> dict:
    """
    1. Fetch Cekura evaluation
    2. Extract failing scenarios
    3. Claude rewrites the intake prompt
    4. Save new version — next call uses it automatically
    """
    agent_id = os.getenv("CEKURA_AGENT_ID", "")
    if not agent_id:
        return {"status": "skipped", "reason": "CEKURA_AGENT_ID not set"}

    evaluation = await fetch_latest_evaluation(agent_id)
    if not evaluation:
        return {"status": "skipped", "reason": "Could not fetch evaluation"}

    overall = evaluation.get("overall_score", 1.0)
    failures = extract_failures(evaluation)
    logger.info(f"Cekura score: {overall:.2f}, failures: {len(failures)}")

    if not failures:
        return {"status": "passing", "score": overall}

    # Get current prompt (from DB override or the default)
    channel = get_channel(os.getenv("CEKURA_CHANNEL", "life"))
    override = get_active_prompt_override(channel.id)
    current = override if override else "\n".join(channel.onboarding_questions)

    improved = await improve_intake_prompt(failures, current)
    if not improved:
        return {"status": "error", "reason": "Claude returned nothing"}

    version_name = f"auto-v{int(overall * 100)}"
    save_prompt_version(version_name, improved, score=overall)
    logger.info(f"Saved improved intake prompt as {version_name}")

    return {
        "status": "improved",
        "previous_score": overall,
        "failures_fixed": len(failures),
        "version": version_name,
    }
