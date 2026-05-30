"""Channel definitions for Call Me Tomorrow.

Two channels at launch: Career and Life.
Each channel has its own Future Me framing, onboarding questions, and session questions.
"""

from dataclasses import dataclass


@dataclass
class Channel:
    id: str
    name: str
    tagline: str
    onboarding_questions: list[str]
    session_questions: list[str]
    future_me_domain: str
    cekura_dimensions: list[str]


CAREER = Channel(
    id="career",
    name="Career",
    tagline="The work you were meant to do.",
    onboarding_questions=[
        "Hey — what made you pick this up today? Like, what's actually going on with your work right now?",
        "Tell me about your job. What's genuinely good about it, and what's feeling stuck or off?",
        "If you fast-forward {horizon} years and things worked out the way you hoped — what does your work life actually look like?",
        "What's the thing that keeps getting in the way? The pattern you keep bumping into?",
        "What have you already tried — like, what's something you've attempted that hasn't moved the needle?",
        "What does success feel like for you — not the title or the salary, the actual feeling of it?",
        "Last one: what kind of support would actually help you — accountability, new ideas, or just someone to think out loud with?",
    ],
    session_questions=[
        "What's happened with your work since we last spoke?",
        "What are you most focused on right now — what's taking up your mental energy?",
        "What do you most want to figure out on this call?",
    ],
    future_me_domain=(
        "five years into your career — you made the leap, you did the work, "
        "and you're doing something that actually fits. You remember exactly what it felt like "
        "to be where they are now."
    ),
    cekura_dimensions=["career_specificity", "action_realism", "empathy"],
)

LIFE = Channel(
    id="life",
    name="Life",
    tagline="The life you've been putting off.",
    onboarding_questions=[
        "Hey — what made you want to have this conversation? What's going on for you right now?",
        "When you look at your life overall — relationships, health, energy, meaning — where does it feel most off?",
        "If you could fast-forward {horizon} years and things went the way you hoped — what's actually different?",
        "What's the thing that keeps showing up in your way — the pattern you recognize but can't quite shake?",
        "Is there something you've already tried to change that hasn't stuck? What happened?",
        "What matters most to you — not what should matter, but what actually does when you're honest?",
        "What would it feel like to have this figured out? Not look like — feel like?",
    ],
    session_questions=[
        "What's been happening in your life since we last spoke — the real stuff?",
        "What area is pulling at you most right now?",
        "What do you want to get out of this call?",
    ],
    future_me_domain=(
        "five years into living more intentionally — you made the changes that mattered, "
        "let go of what didn't, and you know yourself a lot better now. "
        "You remember exactly what the fog felt like."
    ),
    cekura_dimensions=["empathy", "narrative_coherence", "plan_specificity"],
)

CHANNELS: dict[str, Channel] = {
    "career": CAREER,
    "life": LIFE,
}


def get_channel(channel_id: str) -> Channel:
    return CHANNELS.get(channel_id, LIFE)
