"""In-call suggestion engine.

When Future Me (or the onboarding bot) detects a specific concern,
it calls get_suggestions() which fetches relevant, grounded advice
from a curated knowledge base + NVIDIA Nemotron re-ranking.

Two sources:
  1. Curated knowledge cards (career/life frameworks, instantly available)
  2. Dynamic Nemotron synthesis — given the topic, Nemotron generates
     a concise, evidence-informed suggestion in the caller's context.
"""

import os
from typing import Optional

import aiohttp
from loguru import logger

# ── Curated knowledge cards ───────────────────────────────────────────────────
# Pre-indexed, zero-latency. Future Me weaves these into conversation naturally.

KNOWLEDGE_CARDS: dict[str, list[dict]] = {
    "career": [
        {
            "trigger": ["stuck", "promotion", "visibility"],
            "suggestion": (
                "The research on career advancement is pretty consistent: "
                "the thing that moves people forward isn't doing more work — "
                "it's making sure the right people know about the work you're already doing. "
                "One question worth sitting with: who in your organization doesn't know "
                "what you're capable of, and why not?"
            ),
        },
        {
            "trigger": ["pivot", "change", "different field", "leave"],
            "suggestion": (
                "Career transitions that stick usually aren't full leaps — "
                "they're a series of small experiments. The 70-20-10 rule: "
                "70% of your time in your current role, 20% in adjacent stretch projects, "
                "10% in your target direction. That 10% teaches you what the full move would actually feel like."
            ),
        },
        {
            "trigger": ["imposter", "confidence", "not ready", "good enough"],
            "suggestion": (
                "Imposter syndrome tends to show up most in people who are growing fastest — "
                "it's almost a signal that you're operating at the edge of your competence, "
                "which is exactly where growth happens. "
                "The question isn't 'am I ready' — it's 'what would I do differently if I believed I was?'"
            ),
        },
        {
            "trigger": ["burnout", "exhausted", "tired", "drained"],
            "suggestion": (
                "Burnout usually isn't from working too much — it's from working on things "
                "that feel misaligned with what you care about. "
                "A useful audit: in the last week, what were the two hours where you felt most alive? "
                "And the two hours that felt like slow death? That ratio tells you something."
            ),
        },
        {
            "trigger": ["networking", "connections", "relationships", "ask for help"],
            "suggestion": (
                "The most effective career networking isn't transactional — it's curiosity. "
                "Reach out to someone you admire and ask one genuine question about their experience, "
                "not for advice. People remember the person who was actually interested in them."
            ),
        },
    ],
    "life": [
        {
            "trigger": ["purpose", "meaning", "what's the point", "why"],
            "suggestion": (
                "Viktor Frankl's research found that meaning isn't found — it's made. "
                "It comes from three sources: what you create or contribute, "
                "how you experience and connect with others, and how you face unavoidable suffering. "
                "Which of those three is most available to you right now?"
            ),
        },
        {
            "trigger": ["habit", "routine", "consistency", "discipline"],
            "suggestion": (
                "The research on habit formation is clear: environment beats willpower every time. "
                "Instead of trying harder, ask: what would have to be true about my environment "
                "for the behavior I want to happen automatically? "
                "The goal is to make the default easy."
            ),
        },
        {
            "trigger": ["relationship", "connection", "lonely", "isolated"],
            "suggestion": (
                "The Harvard Study of Adult Development — 80 years of data — "
                "found one variable that predicts health, happiness, and longevity above everything else: "
                "the quality of your close relationships. Not quantity. Quality. "
                "Who in your life do you feel fully yourself with?"
            ),
        },
        {
            "trigger": ["decision", "choice", "should I", "deciding"],
            "suggestion": (
                "For big decisions, the 10-10-10 test is useful: "
                "how will you feel about this in 10 minutes? 10 months? 10 years? "
                "Most anxiety lives in the 10-minute window. "
                "Most regret lives in the 10-year one."
            ),
        },
    ],
}


def _find_card(channel: str, topic: str) -> Optional[str]:
    topic_lower = topic.lower()
    cards = KNOWLEDGE_CARDS.get(channel, [])
    for card in cards:
        if any(trigger in topic_lower for trigger in card["trigger"]):
            return card["suggestion"]
    return None


# ── Dynamic Nemotron synthesis ────────────────────────────────────────────────

async def _nemotron_suggestion(channel: str, topic: str, user_context: str) -> Optional[str]:
    """Ask Nemotron to generate a grounded, specific suggestion for this moment."""
    base_url = os.getenv("NEMOTRON_LLM_URL", "").rstrip("/")
    model = os.getenv("NEMOTRON_LLM_MODEL", "nvidia/nemotron-3-super")
    if not base_url:
        return None

    prompt = (
        f"A person in a {channel} coaching session just raised this concern: \"{topic}\".\n"
        f"Their context: {user_context}\n\n"
        "Give ONE specific, evidence-based insight they can use in the next 7 days. "
        "Speak directly to them. 2-3 sentences max. No fluff, no lists. "
        "Sound like a wise friend who's read widely, not a consultant."
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 120,
                    "temperature": 0.7,
                },
                headers={"Authorization": f"Bearer {os.getenv('NEMOTRON_LLM_API_KEY', 'EMPTY')}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"Nemotron suggestion failed: {e}")
    return None


# ── Public API ────────────────────────────────────────────────────────────────

async def get_suggestion(channel: str, topic: str, user_context: str = "") -> str:
    """
    Get a relevant suggestion for the current call topic.
    Tries curated cards first (instant), then Nemotron synthesis.
    Returns empty string if nothing relevant found.
    """
    # 1. Try curated knowledge card
    card = _find_card(channel, topic)
    if card:
        logger.info(f"Returning curated suggestion for topic: {topic[:40]}")
        return card

    # 2. Fall back to Nemotron synthesis
    dynamic = await _nemotron_suggestion(channel, topic, user_context)
    if dynamic:
        logger.info(f"Returning Nemotron suggestion for topic: {topic[:40]}")
        return dynamic

    return ""
