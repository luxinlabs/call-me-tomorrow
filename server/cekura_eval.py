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


# ── Local Nemotron session scoring ────────────────────────────────────────────

SCORE_DIMENSIONS = [
    ("scene_grounding",    "Scene Grounding",    "Did Future Me open with a specific physical location, current activity, and one sensory/emotional detail (sound, light, feeling)? Generic openings score 0. Vivid, specific, believable scenes score 10."),
    ("identity_fidelity",  "Identity Fidelity",  "Did Future Me consistently speak AS the person — first-person past tense ('I remember', 'I learned', 'I was') — never as a coach ('you should', 'you will', 'you need to')? Any coaching-speak, life-advice tone, or AI filler ('Certainly!', 'Great question') drops the score."),
    ("profile_integration","Profile Integration", "Did Future Me reference specific details from the user's onboarding — their named obstacle, their stated fear, their peak moment, their core values? Generic advice that could apply to anyone scores 0. Weaving in the user's exact words or situations scores 10."),
    ("plan_actionability", "Plan Actionability",  "Were the 30/90/365-day actions specific enough to put on a calendar? 'Schedule a 15-minute conversation with your manager by Friday' scores 10. 'Be more visible at work' scores 0. Each item must name a specific behavior, not an aspiration."),
    ("emotional_honesty",  "Emotional Honesty",   "Did Future Me speak honestly about the hard parts — acknowledging struggle, setbacks, fear, and the slow pace of change — rather than relentless positivity? 'I almost gave up twice' scores high. 'You'll get there, I believe in you' scores 0."),
]


async def score_session_locally(
    transcript: str,
    user_goal: str = "",
    user_obstacle: str = "",
    user_fear: str = "",
) -> tuple[float, dict]:
    """Score a Call Me Tomorrow session on 5 product-specific dimensions.

    Returns (composite_0_to_1, breakdown_dict).
    Breakdown contains raw 0-10 scores for each dimension.
    """
    base_url = os.getenv("NEMOTRON_LLM_URL", "").rstrip("/")
    model = os.getenv("NEMOTRON_LLM_MODEL", "nvidia/nemotron-3-super")
    if not base_url or not transcript.strip():
        return 0.0, {}

    context_lines = []
    if user_goal:
        context_lines.append(f"User's stated 5-year goal: {user_goal}")
    if user_obstacle:
        context_lines.append(f"User's main obstacle from onboarding: {user_obstacle}")
    if user_fear:
        context_lines.append(f"User's fear of change: {user_fear}")
    context_block = "\n".join(context_lines) or "(onboarding context not provided)"

    dim_lines = "\n".join(
        f'{i+1}. {key} — {desc}'
        for i, (key, _, desc) in enumerate(SCORE_DIMENSIONS)
    )
    keys_str = ", ".join(f'"{k}"' for k, _, _ in SCORE_DIMENSIONS)

    prompt = (
        "You are evaluating a Call Me Tomorrow session — a voice call where an AI plays "
        "the user's future self, 5 years from now. This product lives or dies on authenticity: "
        "Future Me must feel like a real person talking to their past self, not a life coach or chatbot.\n\n"
        f"ONBOARDING CONTEXT:\n{context_block}\n\n"
        f"TRANSCRIPT:\n{transcript[:4000]}\n\n"
        "Score the session on each dimension from 0 to 10:\n"
        f"{dim_lines}\n\n"
        f"Return ONLY a JSON object with keys {keys_str}.\n"
        "Integer values 0-10. No explanation. No markdown."
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200,
                    "temperature": 0.2,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
                headers={"Authorization": f"Bearer {os.getenv('NEMOTRON_LLM_API_KEY', 'EMPTY')}"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"Local scoring API {resp.status}")
                    return 0.0, {}
                data = await resp.json()
                raw = (data["choices"][0]["message"]["content"] or "").strip()
                if not raw:
                    logger.warning("Scoring returned empty content — thinking mode may be active")
                    return 0.0, {}
                if raw.startswith("```"):
                    raw = raw.split("```")[1].lstrip("json").strip()
                # Find the JSON object in the response
                start = raw.find("{")
                end = raw.rfind("}") + 1
                if start == -1 or end == 0:
                    logger.warning(f"No JSON found in scoring response: {raw[:100]}")
                    return 0.0, {}
                scores = json.loads(raw[start:end])
                total = sum(scores.get(k, 0) for k, _, _ in SCORE_DIMENSIONS)
                composite = round(total / (len(SCORE_DIMENSIONS) * 10), 3)
                logger.info(f"Session score: {composite:.3f} breakdown={scores}")
                return composite, scores
    except Exception as e:
        logger.warning(f"Local session scoring failed: {e}")
        return 0.0, {}


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
