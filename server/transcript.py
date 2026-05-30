"""Transcript capture, formatting, and storage.

Two-source approach:
  - User turns: extracted from LLMContext.messages at end of call
    (100% reliable — context is the ground truth for what the user said)
  - Bot turns: captured live via _BotTurnLogger FrameProcessor
    (placed after LLM in pipeline; captures LLMTextFrame between
     LLMFullResponseStart / LLMFullResponseEnd markers)

The TranscriptionFrame frame-interception approach failed because the NVIDIA
STT pushes frames from a background asyncio task at unpredictable timing
relative to pipeline setup, and the same frame can be captured by the wrong
processor instance at the wrong moment. Context extraction is deterministic.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

if TYPE_CHECKING:
    from pipecat.processors.aggregators.llm_context import LLMContext


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class Turn:
    role: Literal["user", "assistant"]
    speaker: str
    text: str
    ts: str = field(default_factory=lambda: datetime.utcnow().isoformat())


# ── Bot-speech FrameProcessor (placed after LLM in pipeline) ─────────────────

class _BotTurnLogger(FrameProcessor):
    """Captures LLMTextFrame chunks grouped by LLMFullResponseStart/End markers.
    Place AFTER the LLM, BEFORE TTS in the pipeline.
    """

    def __init__(self, turns: list[Turn], bot_name: str = "Recall"):
        super().__init__()
        self._turns = turns
        self._bot_name = bot_name
        self._buffer: list[str] = []
        self._active = False

    def set_bot_name(self, name: str):
        self._bot_name = name

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffer = []
            self._active = True

        elif self._active and isinstance(frame, LLMTextFrame) and frame.text:
            self._buffer.append(frame.text)

        elif isinstance(frame, LLMFullResponseEndFrame):
            self._active = False
            text = "".join(self._buffer).strip()
            if text:
                self._turns.append(
                    Turn(role="assistant", speaker=self._bot_name, text=text)
                )
            self._buffer = []

        await self.push_frame(frame, direction)


# ── Context-based user turn extraction ───────────────────────────────────────

def extract_user_turns_from_context(
    context: "LLMContext",
    skip_injected: int = 1,
) -> list[Turn]:
    """Extract real user speech turns from LLMContext.messages.

    Skips the first `skip_injected` user messages (which are injected
    programmatically, e.g. "The caller just connected. Begin.").
    Skips tool-result messages (list-typed content).

    Args:
        context: The LLMContext from the pipeline.
        skip_injected: How many leading user messages to skip (default 1).
    """
    turns: list[Turn] = []
    skipped = 0

    for msg in context.messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role != "user":
            continue

        # Tool results have list content — skip them
        if isinstance(content, list):
            continue

        if not isinstance(content, str) or not content.strip():
            continue

        if skipped < skip_injected:
            skipped += 1
            continue

        turns.append(Turn(role="user", speaker="You", text=content.strip()))

    return turns


# ── Public container ──────────────────────────────────────────────────────────

class TranscriptLogger:
    """Owns bot-turn capture and merges with user turns extracted from context.

    Pipeline usage (only bot_logger goes in the pipeline):
        pipeline = Pipeline([
            transport.input(),
            stt,
            user_agg,
            llm,
            transcript.bot_logger,   ← ONLY this goes in the pipeline
            tts,
            transport.output(),
            assistant_agg,
        ])

    At call end, call build(context) to produce the full ordered transcript.
    """

    def __init__(self, bot_name: str = "Recall"):
        self._bot_turns: list[Turn] = []
        self.bot_logger = _BotTurnLogger(self._bot_turns, bot_name)

    def switch_bot_name(self, name: str):
        """Call when Recall hands off to Future Me."""
        self.bot_logger.set_bot_name(name)

    def build(self, context: "LLMContext", skip_injected: int = 1) -> list[Turn]:
        """Rebuild ordered transcript by walking context messages.

        Uses context message ORDER (ground truth for conversation sequence).
        User text comes from context messages directly.
        Bot text comes from _bot_turns (clean streamed text, no tool markup).
        """
        user_turns = extract_user_turns_from_context(context, skip_injected)
        bot_turns = list(self._bot_turns)

        result: list[Turn] = []
        u_idx = 0
        b_idx = 0
        skipped = 0

        for msg in context.messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                continue

            if role == "user":
                if isinstance(content, list):       # tool result — skip
                    continue
                if not isinstance(content, str) or not content.strip():
                    continue
                if skipped < skip_injected:
                    skipped += 1
                    continue
                if u_idx < len(user_turns):
                    result.append(user_turns[u_idx])
                    u_idx += 1

            elif role == "assistant":
                # Determine if this assistant message is a speaking turn or just a tool call
                has_text = isinstance(content, str) and content.strip()
                if not has_text and isinstance(content, list):
                    has_text = any(
                        isinstance(i, dict) and i.get("type") == "text"
                        and i.get("text", "").strip()
                        for i in content
                    )
                if has_text and b_idx < len(bot_turns):
                    result.append(bot_turns[b_idx])
                    b_idx += 1

        return result

    def as_text(self, context: "LLMContext", header: str = "", skip_injected: int = 1) -> str:
        turns = self.build(context, skip_injected)
        lines: list[str] = []
        if header:
            lines += [header, "─" * max(len(header), 20), ""]
        for t in turns:
            lines.append(f"[{t.speaker}]")
            lines.append(t.text)
            lines.append("")
        return "\n".join(lines).strip()

    def as_json(self, context: "LLMContext") -> str:
        return json.dumps(
            [{"role": t.role, "speaker": t.speaker, "text": t.text, "ts": t.ts}
             for t in self.build(context)],
            indent=2,
        )


# ── HTML rendering ────────────────────────────────────────────────────────────

def turns_to_html(turns: list[Turn], title: str = "Session") -> str:
    rows = ""
    for t in turns:
        cls = "turn-user" if t.role == "user" else "turn-bot"
        rows += f"""
        <div class="turn {cls}">
          <span class="speaker">{t.speaker}</span>
          <p>{t.text}</p>
        </div>"""
    return f"""
    <section class="transcript-block">
      <h3 class="transcript-title">{title}</h3>
      <div class="transcript-turns">{rows}
      </div>
    </section>"""
