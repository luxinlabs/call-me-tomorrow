"""30/90/365 action plan builder for Act 3 of the call.

Returns a structured plan for the LLM to deliver conversationally.
"""

from dataclasses import dataclass


@dataclass
class ActionPlan:
    day_30: str
    day_90: str
    day_365: str
    closing_line: str


def build_action_plan_prompt(answers: dict[str, str], archetype_name: str) -> str:
    """System prompt for generating the action plan (injected as a tool result)."""
    return f"""\
Based on the caller's intake:
- Today they are: {answers.get('q1', '?')}
- Their fear: {answers.get('q2', '?')}
- Their vision: {answers.get('q3', '?')}
- Their obstacle: {answers.get('q4', '?')}
- What success feels like: {answers.get('q5', '?')}
- Their archetype: {archetype_name}

Generate three actions: one achievable in 30 days, one in 90 days, one in a year.
Make each one SPECIFIC and BEHAVIORAL — not vague goals, but concrete first steps.
Format as a Python dict with keys: day_30, day_90, day_365, closing_line.
The closing_line should be 1 sentence that Future Me says before scheduling the callback.
"""


def format_plan_for_speech(plan: ActionPlan) -> str:
    """Format the plan as spoken delivery text for the LLM."""
    return (
        f"In the next thirty days: {plan.day_30} "
        f"In the next ninety: {plan.day_90} "
        f"And by this time next year: {plan.day_365} "
        f"{plan.closing_line}"
    )
