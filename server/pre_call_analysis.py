"""Pre-call analysis — runs before the pipeline starts.

When a user selects "Career · 3 years ahead" and clicks Answer,
this module fires before the Pipecat pipeline begins:

  1. Builds a focused query from their channel + horizon + profile
  2. Asks NVIDIA Nemotron to synthesize forward-looking world context
     (what has changed in the world in that domain over those years)
  3. Optionally augments with scraped data from curated sources
  4. Returns a short brief that gets injected into Future Me's system prompt
     as "WORLD CONTEXT: things Future Me knows about how things have changed"

The result makes Future Me feel genuinely informed about the real world,
not just the user's personal journey.
"""

import asyncio
import os
from datetime import datetime

import aiohttp
from loguru import logger

# ── Curated domain queries for web enrichment ─────────────────────────────────

DOMAIN_QUERIES = {
    "career": [
        "future of work trends {horizon} years",
        "AI impact on jobs career advice {year}",
        "career growth strategies next {horizon} years",
    ],
    "life": [
        "life satisfaction research happiness science",
        "habits wellbeing psychology latest research",
        "meaningful life purpose research",
    ],
}

# ── Nemotron synthesis ────────────────────────────────────────────────────────

ANALYSIS_PROMPT = """\
You are helping prepare context for a voice AI that will roleplay as someone's \
"Future Self" from {horizon} years in the future (the year {future_year}).

The person is focused on: {channel_name}
Their current role/situation: {user_role}
Their stated goal: {user_goal}

Your task: write a SHORT, grounded "world brief" — 4-6 sentences — that describes \
what has genuinely changed in the world relevant to this person's domain between \
{current_year} and {future_year}.

Focus on:
- Real trends that are likely to have played out (AI, remote work, career shifts, \
  economic changes, wellness research, relationship patterns — whichever apply)
- What the landscape looks like for someone in their position
- What a person 5 years ahead would naturally know that someone today doesn't yet

Write in present tense as if it's {future_year}. Be specific and grounded, not vague. \
Do not mention that you are an AI or that this is a simulation. \
This text will be read privately by the AI, not spoken aloud.

Output ONLY the world brief — no preamble, no labels.
"""


async def _nemotron_analysis(prompt: str) -> str:
    base_url = os.getenv("NEMOTRON_LLM_URL", "").rstrip("/")
    model = os.getenv("NEMOTRON_LLM_MODEL", "nvidia/nemotron-3-super")

    if not base_url:
        logger.warning("NEMOTRON_LLM_URL not set — skipping world brief")
        return ""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 250,
                    "temperature": 0.6,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
                headers={"Authorization": f"Bearer {os.getenv('NEMOTRON_LLM_API_KEY', 'EMPTY')}"},
                timeout=aiohttp.ClientTimeout(total=12),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data["choices"][0]["message"]["content"].strip()
                    logger.info(f"World brief generated ({len(text)} chars)")
                    return text
                else:
                    body = await resp.text()
                    logger.warning(f"Nemotron analysis {resp.status}: {body[:100]}")
    except asyncio.TimeoutError:
        logger.warning("World brief timed out — continuing without it")
    except Exception as e:
        logger.warning(f"World brief failed: {e}")

    return ""


# ── Optional: scrape a curated source for real data ──────────────────────────

async def _scrape_headline_context(channel: str, horizon: int) -> str:
    """Fetch a brief context snippet from a trusted public source."""
    sources = {
        "career": "https://www.weforum.org/agenda/archive/future-of-work/",
        "life": "https://positivepsychology.com/category/happiness/",
    }
    url = sources.get(channel)
    if not url:
        return ""

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=6),
            ) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    # Very rough: extract text between <h2> tags as headlines
                    import re
                    headlines = re.findall(r'<h[23][^>]*>([^<]{15,120})</h[23]>', html)
                    if headlines:
                        return "Recent headlines: " + " · ".join(headlines[:4])
    except Exception:
        pass
    return ""


# ── Public API ────────────────────────────────────────────────────────────────

async def run_pre_call_analysis(
    channel_id: str,
    time_horizon: int,
    user_role: str = "",
    user_goal: str = "",
    channel_name: str = "",
) -> str:
    """
    Returns a 'world context' block ready for injection into Future Me's prompt.
    Runs concurrently: Nemotron synthesis + optional web scrape.
    Total budget: ~12 seconds max.
    """
    current_year = datetime.utcnow().year
    future_year = current_year + time_horizon

    prompt = ANALYSIS_PROMPT.format(
        horizon=time_horizon,
        future_year=future_year,
        current_year=current_year,
        channel_name=channel_name or channel_id.title(),
        user_role=user_role or "professional",
        user_goal=user_goal or "grow and improve",
    )

    # Run Nemotron synthesis and web scrape concurrently
    synthesis, headlines = await asyncio.gather(
        _nemotron_analysis(prompt),
        _scrape_headline_context(channel_id, time_horizon),
        return_exceptions=True,
    )

    if isinstance(synthesis, Exception):
        synthesis = ""
    if isinstance(headlines, Exception):
        headlines = ""

    parts = []
    if synthesis:
        parts.append(synthesis)
    if headlines:
        parts.append(f"\n{headlines}")

    if not parts:
        return ""

    world_brief = "\n".join(parts)
    logger.info(f"Pre-call analysis complete: {len(world_brief)} chars")

    return (
        f"\n\nWORLD CONTEXT (year {future_year} — things Future Me knows about how the world has changed):\n"
        f"{world_brief}\n"
        "Draw on this naturally in conversation — as things you've witnessed or lived through, "
        "not as facts you're reciting."
    )
