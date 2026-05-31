"""Generate a rich personality profile from onboarding answers using NVIDIA Nemotron."""

import json
import os

import aiohttp
from loguru import logger


async def generate_personality_profile(answers: dict) -> dict:
    """Analyze onboarding answers and return a structured personality profile."""
    base_url = os.getenv("NEMOTRON_LLM_URL", "").rstrip("/")
    model = os.getenv("NEMOTRON_LLM_MODEL", "nvidia/nemotron-3-super")

    if not base_url:
        logger.warning("NEMOTRON_LLM_URL not set — skipping personality profile")
        return {}

    prompt = f"""\
You are a sharp, non-cliché psychologist and executive coach analyzing someone's first coaching session.
Based purely on what they revealed in their answers, generate an honest, specific personality profile.
Avoid generic HR-speak. Every observation should feel like it could only be about THIS person.

ONBOARDING ANSWERS:
- When they felt most alive/capable: {answers.get('peak', '(not provided)')}
- What success looks like 5 years out: {answers.get('goal', '(not provided)')}
- The obstacle/pattern that keeps showing up: {answers.get('obstacle', '(not provided)')}
- What they'd lose if they actually changed: {answers.get('fear', '(not provided)')}
- What genuinely matters to them: {answers.get('values', '(not provided)')}
- Readiness score (1-10): {answers.get('readiness', '(not provided)')}
- Other context: {answers.get('extra', '(not provided)')}

Return ONLY a valid JSON object with exactly these keys:
{{
  "mbti": "4-letter type e.g. INTJ",
  "mbti_note": "One specific sentence explaining WHY this type, grounded in their words",
  "archetype": "The [Word] — e.g. The Architect, The Agitator, The Quiet Builder",
  "energy_line": "One-sentence poetic diagnosis of how they move through the world",
  "now": "2 vivid sentences about where they actually are right now — include the texture, not just the facts",
  "becoming": "2 sentences about who they are clearly moving toward becoming",
  "core_tension": "The central internal conflict in 1 phrase, e.g. 'competence used as armor'",
  "superpower": "What makes them distinctively capable — 1 sentence, specific",
  "blind_spot": "What they are reliably not seeing about themselves — honest but not cruel",
  "uncomfortable_truth": "Something true about them that's hard to hear but ultimately kind — 1 sentence"
}}

Output ONLY the JSON object — no markdown fences, no preamble."""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 900,
                    "temperature": 0.65,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
                headers={"Authorization": f"Bearer {os.getenv('NEMOTRON_LLM_API_KEY', 'EMPTY')}"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(f"Personality profile API {resp.status}: {body[:200]}")
                    return {}
                data = await resp.json()
                raw = data["choices"][0]["message"]["content"].strip()
                # Strip markdown fences if present
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                return json.loads(raw)
    except Exception as e:
        logger.warning(f"Personality profile generation failed: {e}")
        return {}
