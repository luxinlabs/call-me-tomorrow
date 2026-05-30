"""Session bot — returning user, channel-aware, RAG-enhanced Future Me."""

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

from action_plan import ActionPlan, format_plan_for_speech
from archetype import assign_archetype
from channels import get_channel
from memory import (
    get_pending_action_items,
    get_user_by_phone,
    save_action_items,
    save_session,
    update_session_transcript,
)
from nemotron_llm import VLLMOpenAILLMService
from nvidia_stt import NVidiaWebSocketSTTService
from rag import get_memory_context, ingest_session
from simulation import build_future_me_prompt, build_session_intake_prompt
from suggestions import get_suggestion
from transcript import TranscriptLogger


async def run_session(
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
    user = get_user_by_phone(phone) if phone else None
    user_id = str(user["id"]) if user else "anon"
    user_name = user["name"] if user else ""
    effective_horizon = user["time_horizon"] if user else time_horizon

    logger.info(f"Session: user={user_id} channel={channel_id} horizon={effective_horizon}")

    # ── Mutable call state ────────────────────────────────────────────────────
    intake_answers: dict[str, str] = {}
    current_archetype: str = ""
    session_id: int | None = None
    transcript_logger = TranscriptLogger(bot_name="Recall", session_key=session_key)

    # Pre-fetch pending action items for check-in
    pending_actions = get_pending_action_items(int(user_id) if user_id != "anon" else -1, channel_id)

    # ── Tools ─────────────────────────────────────────────────────────────────

    async def assign_archetype_tool(
        params: FunctionCallParams,
        q1_current_focus: str,
        q2_whats_on_mind: str,
        q3_goal_for_call: str,
    ) -> None:
        """Call after the three session-opening questions are answered.

        Args:
            q1_current_focus: Their answer to the first session question.
            q2_whats_on_mind: Their answer to the second session question.
            q3_goal_for_call: What they want to get from this call.
        """
        nonlocal intake_answers, current_archetype

        intake_answers = {
            "q1": q1_current_focus,
            "q2": q2_whats_on_mind,
            "q3": q3_goal_for_call,
        }

        archetype = assign_archetype(intake_answers)
        current_archetype = archetype.name

        # Build RAG context based on their stated concern
        concern_query = f"{q1_current_focus} {q2_whats_on_mind}"
        memory_ctx = await get_memory_context(user_id, channel_id, concern_query)

        future_me_prompt = build_future_me_prompt(
            answers=intake_answers,
            archetype=archetype,
            channel=channel,
            time_horizon=effective_horizon,
            memory_context=memory_ctx + world_context,
            pending_actions=pending_actions,
        )
        transcript_logger.switch_bot_name("Future Me")

        # Inject Future Me persona
        context.add_message({"role": "system", "content": future_me_prompt})

        logger.info(f"Archetype: {archetype.name} | RAG context: {len(memory_ctx)} chars")
        await params.result_callback({
            "ok": True,
            "archetype": archetype.name,
            "instruction": (
                "Intake complete. You are now Future Me. "
                "Begin your opening monologue — present tense, specific, grounded. "
                "If there are pending action items, check in on one naturally after your opening."
            ),
        })

    async def offer_suggestion(
        params: FunctionCallParams,
        topic: str,
        user_context: str = "",
    ) -> None:
        """Fetch a relevant insight when the caller raises a specific challenge.

        Call this when the caller describes a concrete problem (e.g. stuck on a promotion,
        struggling with a habit, facing a big decision). Don't call for every turn —
        only when a grounded, evidence-based perspective would genuinely help.

        Args:
            topic: The specific challenge or concern they raised.
            user_context: Brief summary of their situation so far.
        """
        suggestion = await get_suggestion(channel_id, topic, user_context)
        if suggestion:
            await params.result_callback({
                "suggestion": suggestion,
                "instruction": "Weave this into your response naturally as something Future Me learned or observed. Don't announce it.",
            })
        else:
            await params.result_callback({
                "suggestion": "",
                "instruction": "No specific insight — respond from your own experience.",
            })

    async def deliver_action_plan(
        params: FunctionCallParams,
        day_30_action: str,
        day_90_action: str,
        day_365_action: str,
    ) -> None:
        """Deliver the 30/90/365 action plan. Call when transitioning to Act 3.

        Args:
            day_30_action: One specific, behavioral action achievable in 30 days.
            day_90_action: One specific action achievable in 90 days.
            day_365_action: One specific action achievable in one year.
        """
        nonlocal session_id

        plan = ActionPlan(
            day_30=day_30_action,
            day_90=day_90_action,
            day_365=day_365_action,
            closing_line="That's what I'd tell myself. I'll check in with you again.",
        )
        speech = format_plan_for_speech(plan)

        # Save session and action items
        session_id = save_session(
            phone=phone,
            channel=channel_id,
            archetype=current_archetype,
            answers=intake_answers,
            action_plan={"day_30": day_30_action, "day_90": day_90_action, "day_365": day_365_action},
            user_id=int(user_id) if user_id != "anon" else None,
        )

        if user_id != "anon":
            save_action_items(
                session_id=session_id,
                user_id=int(user_id),
                channel=channel_id,
                plan={"day_30": day_30_action, "day_90": day_90_action, "day_365": day_365_action},
            )

        logger.info(f"Action plan saved: session_id={session_id}")
        await params.result_callback({
            "ok": True,
            "plan_speech": speech,
            "instruction": "Deliver this conversationally as Future Me. Then call end_call.",
        })

    async def end_call(params: FunctionCallParams) -> None:
        """End the call after the goodbye is spoken."""
        # Embed transcript for future RAG retrieval
        if session_id:
            transcript = transcript_logger.as_text(
                context=context,
                header=f"Session — {channel.name}",
            )
            await ingest_session(
                user_id=user_id,
                channel=channel_id,
                session_id=session_id,
                transcript=transcript,
                metadata={"archetype": current_archetype, "type": "session"},
            )
            update_session_transcript(session_id, transcript)

        logger.info(f"Session {session_id} complete")
        await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
        await params.result_callback(
            {"ok": True}, properties=FunctionCallResultProperties(run_llm=False)
        )

    # ── Services ──────────────────────────────────────────────────────────────

    tool_functions = [assign_archetype_tool, offer_suggestion, deliver_action_plan, end_call]
    tools = ToolsSchema(standard_tools=tool_functions)

    stt = NVidiaWebSocketSTTService(
        url=os.getenv("NVIDIA_ASR_URL", "ws://44.241.251.184:8080"),
        strip_interim_prefix=True,
    )

    enable_thinking = os.getenv("NEMOTRON_ENABLE_THINKING", "false").lower() == "true"
    llm = VLLMOpenAILLMService(
        api_key=os.getenv("NEMOTRON_LLM_API_KEY", "EMPTY"),
        base_url=os.getenv("NEMOTRON_LLM_URL", "http://nemotron-fleet-alb-1322439314.us-west-2.elb.amazonaws.com/v1"),
        settings=VLLMOpenAILLMService.Settings(
            model=os.getenv("NEMOTRON_LLM_MODEL", "nvidia/nemotron-3-super"),
            system_instruction=build_session_intake_prompt(channel, effective_horizon, user_name),
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
