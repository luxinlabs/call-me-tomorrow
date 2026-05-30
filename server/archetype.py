"""Tarot archetype mapping from intake answers to Major Arcana.

The archetype is NEVER mentioned explicitly to the caller. It shapes Future Me's
tone, metaphors, and emotional register — not what is said.
"""

from dataclasses import dataclass

ARCHETYPES: dict[str, dict] = {
    "The Magician": {
        "number": 1,
        "signals": ["creative", "potential", "block", "expression", "ideas", "build", "make", "invent"],
        "tone": "electric and catalytic — Future Me speaks as though every obstacle became fuel",
        "metaphor": "Tools were always in your hands. You just had to learn which ones.",
        "shadow": "scattered",
    },
    "The High Priestess": {
        "number": 2,
        "signals": ["intuition", "quiet", "overthink", "uncertain", "know", "feel", "trust", "listen"],
        "tone": "calm and knowing — Future Me speaks from a place of deep inner certainty",
        "metaphor": "The answers were always there. You learned to stop interrupting them.",
        "shadow": "withdrawn",
    },
    "The Emperor": {
        "number": 4,
        "signals": ["leadership", "structure", "control", "respect", "authority", "build", "team", "lead"],
        "tone": "grounded and direct — Future Me speaks as someone who built systems that outlast them",
        "metaphor": "You stopped waiting for permission. That was the whole thing.",
        "shadow": "rigid",
    },
    "The Chariot": {
        "number": 7,
        "signals": ["promotion", "drive", "ambition", "compete", "win", "move", "fast", "progress"],
        "tone": "forward-moving and confident — Future Me is already thinking about the next peak",
        "metaphor": "The direction was never the question. Learning to steer was.",
        "shadow": "relentless",
    },
    "Strength": {
        "number": 8,
        "signals": ["fear", "courage", "anxiety", "pressure", "brave", "hold", "endure", "soft"],
        "tone": "warm and steady — Future Me has faced hard things and carries them lightly",
        "metaphor": "Strength was never the absence of fear. You learned that early.",
        "shadow": "exhausted",
    },
    "The Hermit": {
        "number": 9,
        "signals": ["alone", "purpose", "meaning", "search", "lost", "find", "retreat", "understand"],
        "tone": "reflective and precise — Future Me has done the inner work and speaks from earned clarity",
        "metaphor": "The lantern you were looking for was the one you carried.",
        "shadow": "isolated",
    },
    "The Star": {
        "number": 17,
        "signals": ["hope", "dream", "inspire", "heal", "vision", "beautiful", "art", "peace"],
        "tone": "open and hopeful — Future Me radiates quiet confidence in what became possible",
        "metaphor": "You stopped asking if you were worth it. That freed everything.",
        "shadow": "naive",
    },
    "The Tower": {
        "number": 16,
        "signals": ["collapse", "pressure", "failure", "break", "crisis", "fall", "wrong", "hard"],
        "tone": "unflinching and honest — Future Me doesn't sugarcoat what happened, but the story ends well",
        "metaphor": "The thing that broke was the thing that needed to.",
        "shadow": "chaos",
    },
    "The Fool": {
        "number": 0,
        "signals": ["start", "new", "leap", "risk", "unknown", "adventure", "change", "begin"],
        "tone": "light and a little wild — Future Me looks back at the leaps with deep affection",
        "metaphor": "You didn't need a map. You needed to trust the first step.",
        "shadow": "reckless",
    },
    "Judgement": {
        "number": 20,
        "signals": ["transition", "calling", "second chance", "wake up", "transform", "purpose", "meant"],
        "tone": "resonant and clear — Future Me speaks as if answering a question you've always had",
        "metaphor": "The call came when you finally stopped being too busy to hear it.",
        "shadow": "late",
    },
    "The World": {
        "number": 21,
        "signals": ["complete", "whole", "global", "everything", "freedom", "fulfilled", "arrived", "done"],
        "tone": "expansive and complete — Future Me speaks from a place of genuine arrival",
        "metaphor": "Integration was never about fixing. It was about finally including yourself.",
        "shadow": "stagnant",
    },
}

_DEFAULT_ARCHETYPE = "The Magician"


@dataclass
class ArchetypeResult:
    name: str
    tone: str
    metaphor: str
    number: int


def assign_archetype(answers: dict[str, str]) -> ArchetypeResult:
    """Score answers against archetype signal words and return the best match."""
    combined = " ".join(answers.values()).lower()
    scores: dict[str, int] = {}
    for name, data in ARCHETYPES.items():
        scores[name] = sum(1 for s in data["signals"] if s in combined)

    best = max(scores, key=lambda k: (scores[k], -ARCHETYPES[k]["number"]))
    if scores[best] == 0:
        best = _DEFAULT_ARCHETYPE

    a = ARCHETYPES[best]
    return ArchetypeResult(name=best, tone=a["tone"], metaphor=a["metaphor"], number=a["number"])
