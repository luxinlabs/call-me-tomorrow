"""Onboarding bot — first-time caller experience.

Psychology sequence (evidence-based order):
  1. Open door          — MI open-ended, creates safety
  2. Peak experience    — Appreciative Inquiry, activates strengths
  3. The gap            — productive cognitive dissonance (peak vs. now)
  4. Miracle question   — SFBT, bypasses limiting beliefs
  5. The obstacle       — GROW Reality, goes deeper than surface
  6. Values             — ACT clarification, intrinsic motivation
  7. The fear           — MI ambivalence, secondary gains
  8. Readiness          — MI change-talk, predicts follow-through

Tone: curious friend, not intake form.
"""

import os

from loguru import logger
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import EndTaskFrame, FunctionCallResultProperties, LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.turns.user_turn_strategies import FilterIncompleteUserTurnStrategies
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.gradium.tts import GradiumTTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.base_transport import BaseTransport
from pipecat.workers.runner import WorkerRunner

from channels import Channel, get_channel
from memory import complete_onboarding, create_user, save_session, update_session_transcript
from nemotron_llm import VLLMOpenAILLMService
from nvidia_stt import NVidiaWebSocketSTTService
from rag import ingest_session
from suggestions import get_suggestion
from transcript import TranscriptLogger


def build_onboarding_prompt(channel: Channel, time_horizon: int) -> str:
    ch = channel.id

    if ch == "career":
        q1 = "What made you pick this up today? Like, what's actually going on with your work right now?"
        q2 = "Tell me about a time — could be recently or years ago — when you were really in your element professionally. What were you doing?"
        q3 = "What's different between that version of you and where you are today?"
        q4 = f"Okay — suppose tonight while you're asleep, a miracle happens. You wake up and {time_horizon} years have passed. Everything worked out. What's the very first thing you notice is different?"
        q5 = "What keeps showing up in your way? Not the obvious stuff — the real thing underneath."
        q6 = "What actually matters most to you in your work? Not what should matter — what does, when you're honest with yourself?"
        q7 = "Here's a harder one: what would you lose if things actually changed? What's comfortable about where you are, even if it's not what you want?"
        q8 = "Last question — on a scale of one to ten, how ready do you actually feel to do something different?"
    else:  # life
        q1 = "What's going on for you right now? Start wherever feels most true."
        q2 = "Tell me about a time when you felt genuinely like yourself — alive, engaged, proud. What was happening?"
        q3 = "What's the distance between that and where you are today?"
        q4 = f"Imagine you wake up {time_horizon} years from now. A miracle happened while you slept. What's different — what do you notice first?"
        q5 = "What's the pattern that keeps getting in your way? The thing you recognize but can't quite shake?"
        q6 = "When you strip everything away — the expectations, the shoulds — what actually matters most to you?"
        q7 = "What would you lose if you actually changed? What does staying the same give you that's hard to admit?"
        q8 = "Scale of one to ten — how ready are you, honestly, to do something different?"

    return f"""\
You are "Recall" — the warm first voice someone hears when they call Call Me Tomorrow.

This is their first ever call. Your job: have a real conversation, help them feel heard, \
and gather what Future Me needs to speak to them meaningfully.

CHANNEL: {channel.name}
TIME HORIZON: {time_horizon} years

TONE — this is everything:
- Sound like a smart, genuinely curious friend. Not a form. Not a therapist. Not a chatbot.
- Short responses between questions — one sentence acknowledgment max, then the next question.
- React authentically: if something they said surprised you or resonated, say so briefly.
- Use "yeah", "that makes sense", "hm" naturally. Never "absolutely" or "great answer".
- ONE question at a time. Always. Even if you're tempted to ask two.
- If they give a short answer, reflect once: "Say more?" or "What does that look like for you?"
- Never rush. Silence is okay. Let them find their words.

THE SEQUENCE (work through these naturally — don't announce you're asking Q3 or Q5):

Q1 (Open door — MI): "{q1}"
Q2 (Peak experience — Appreciative Inquiry): "{q2}"
Q3 (The gap): "{q3}"
Q4 (Miracle question — SFBT): "{q4}"
Q5 (The obstacle — GROW Reality): "{q5}"
Q6 (Values — ACT): "{q6}"
Q7 (The fear — MI ambivalence): "{q7}"
Q8 (Readiness — MI change talk): "{q8}"

You also need their name — weave it in naturally after Q1: "And what's your name?" \
or "What should I call you?" Don't make it feel like form-filling.

AFTER Q8:
Say: "Thank you — I mean that. That's not easy to sit with." \
Pause a beat, then: "I'm going to hand you over to Future Me now. Give me just a moment."
Then call save_profile.

TOOLS:
- save_profile: call after Q8 with everything you learned
- offer_suggestion: call if they hit a specific obstacle that has a grounded insight to offer
- end_call: call after save_profile returns

NEVER break character. Never say this is a product, demo, or AI.
"""


async def run_onboarding(
    transport: BaseTransport,
    channel_id: str,
    time_horizon: int,
    phone: str | None,
    world_context: str = "",
    audio_in_sample_rate: int = 16000,
    audio_out_sample_rate: int = 24000,
    session_key: str | None = None,
) -> None:
    channel = get_channel(channel_id)
    logger.info(f"Onboarding: channel={channel_id} horizon={time_horizon} phone={phone}")

    transcript_logger = TranscriptLogger(bot_name="Recall", session_key=session_key)

    async def save_profile(
        params: FunctionCallParams,
        name: str,
        current_role: str,
        peak_experience: str,
        main_goal: str,
        main_obstacle: str,
        hidden_fear: str,
        core_values: str,
        readiness_score: int,
        extra_context: str = "",
    ) -> None:
        """Save the caller's full profile after the onboarding conversation.

        Args:
            name: Their name or preferred name.
            current_role: What they do now (job, life stage, situation).
            peak_experience: The moment they described when they felt most alive/capable.
            main_goal: What the miracle-question future looks like for them.
            main_obstacle: The real pattern getting in their way.
            hidden_fear: What they'd lose if things actually changed.
            core_values: What actually matters most to them.
            readiness_score: Their 1-10 readiness rating.
            extra_context: Any other important context from the conversation.
        """
        answers = {
            "peak": peak_experience,
            "goal": main_goal,
            "obstacle": main_obstacle,
            "fear": hidden_fear,
            "values": core_values,
            "readiness": readiness_score,
            "extra": extra_context,
        }

        profile_summary = (
            f"Name: {name}. Role: {current_role}. "
            f"Peak: {peak_experience[:80]}. "
            f"Goal ({time_horizon}yr): {main_goal[:80]}. "
            f"Obstacle: {main_obstacle[:60]}. "
            f"Fear of change: {hidden_fear[:60]}. "
            f"Values: {core_values[:60]}. "
            f"Readiness: {readiness_score}/10."
        )

        user_id = create_user(
            phone=phone or "",
            name=name,
            role=current_role,
            time_horizon=time_horizon,
            channel=channel_id,
        )
        complete_onboarding(user_id, profile_summary)

        transcript_text = transcript_logger.as_text(
            context=context,
            header=f"Onboarding — {channel.name} — {name}",
        )

        session_id = save_session(
            phone=phone,
            channel=channel_id,
            archetype="onboarding",
            answers=answers,
            action_plan={},
            user_id=user_id,
        )
        update_session_transcript(session_id, transcript_text)

        await ingest_session(
            user_id=str(user_id),
            channel=channel_id,
            session_id=session_id,
            transcript=transcript_text,
            metadata={"type": "onboarding", "name": name},
        )

        logger.info(f"Profile saved: user_id={user_id} session_id={session_id}")
        await params.result_callback({
            "ok": True,
            "user_id": user_id,
            "session_id": session_id,
            "instruction": "Profile saved. Now say a warm single-sentence closing. Then call end_call.",
        })

    async def offer_suggestion(
        params: FunctionCallParams,
        topic: str,
        user_context: str = "",
    ) -> None:
        """Fetch a grounded insight when they hit a specific, named challenge.

        Only call this when a concrete, evidence-based perspective would genuinely help
        — not for every answer. Use it sparingly.

        Args:
            topic: The specific challenge or concern they raised.
            user_context: Brief summary of what they've shared so far.
        """
        suggestion = await get_suggestion(channel_id, topic, user_context)
        await params.result_callback({
            "suggestion": suggestion,
            "instruction": (
                "If suggestion is non-empty, weave it into your response naturally "
                "as something you've heard or observed — not as a fact you're delivering. "
                "Keep it to one sentence."
            ),
        })

    async def end_call(params: FunctionCallParams) -> None:
        """End the call. Only call after save_profile has returned and you've said goodbye."""
        await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
        await params.result_callback(
            {"ok": True}, properties=FunctionCallResultProperties(run_llm=False)
        )

    tool_functions = [save_profile, offer_suggestion, end_call]
    tools = ToolsSchema(standard_tools=tool_functions)

    stt = NVidiaWebSocketSTTService(
        url=os.getenv("NVIDIA_ASR_URL", "ws://44.241.251.184:8080"),
        strip_interim_prefix=True,
    )

    enable_thinking = os.getenv("NEMOTRON_ENABLE_THINKING", "false").lower() == "true"
    llm = VLLMOpenAILLMService(
        api_key=os.getenv("NEMOTRON_LLM_API_KEY", "EMPTY"),
        base_url=os.getenv(
            "NEMOTRON_LLM_URL",
            "http://nemotron-fleet-alb-1322439314.us-west-2.elb.amazonaws.com/v1",
        ),
        settings=VLLMOpenAILLMService.Settings(
            model=os.getenv("NEMOTRON_LLM_MODEL", "nvidia/nemotron-3-super"),
            system_instruction=build_onboarding_prompt(channel, time_horizon),
            extra={"extra_body": {"chat_template_kwargs": {"enable_thinking": enable_thinking}}},
        ),
    )

    tts = GradiumTTSService(
        api_key=os.environ["GRADIUM_API_KEY"],
        settings=GradiumTTSService.Settings(
            voice=os.getenv("GRADIUM_VOICE_ID", "Eu9iL_CYe8N-Gkx_"),
        ),
    )

    for fn in tool_functions:
        llm.register_direct_function(fn)

    context = LLMContext(tools=tools)
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_turn_strategies=FilterIncompleteUserTurnStrategies(),
        ),
    )

    pipeline = Pipeline([
        transport.input(),
        stt,
        transcript_logger.user_logger,   # before user_agg — captures TranscriptionFrame
        user_agg,
        llm,
        transcript_logger.bot_logger,   # after LLM — captures LLMTextFrame
        tts,
        transport.output(),
        assistant_agg,
    ])

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            audio_in_sample_rate=audio_in_sample_rate,
            audio_out_sample_rate=audio_out_sample_rate,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_connected(transport, client):
        context.add_message({"role": "user", "content": "The caller just connected. Begin."})
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport, client):
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()
