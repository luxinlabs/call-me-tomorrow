<!-- Hackathon submission requirements -->

# Hackathon Submission — Call Me Tomorrow

## 1. What is this?

Call Me Tomorrow is a voice agent that delivers **calls from your future self** — literally you who is calling back from five years ahead to guide you based on your lived experience. You talk like you’re on the phone with a friend; Nemotron handles ASR + LLM reasoning inside Pipecat; Future Me answers with vivid coaching that lands in a crisp 30 / 90 / 365‑day action plan. Every session is logged, scored, and fed back into memory so each subsequent call feels wiser than the last.

## 2. Demo video (< 60 seconds)

https://youtu.be/84UB4T1J5Ck

## 3. How we used Cekura, Nemotron, and Pipecat

### Pipecat

- Full voice pipeline (NVIDIA STT → Nemotron LLM → Gradium TTS) runs as a Pipecat worker.
- Custom tools (`offer_suggestion`, `deliver_action_plan`, `end_call`) enforce the 3‑act structure.
- Transcript logger + WebRTC transport come for free from Pipecat, which made iteration fast.

### Nemotron

- **Nemotron Speech** handles real‑time streaming ASR at <250 ms latency.
- **Nemotron 3 Super** runs both onboarding + session prompts, the world‑context brief, personality synthesis, and now the local session scorer.
- **NV-Embed-v2** powers our RAG layer so Future Me can reference prior calls.

### Cekura

- Objective: keep empathy + specificity ≥0.75 so action plans feel human.
- Wired the `/feedback-loop` endpoint that fetches Cekura evals, extracts failing scenarios, calls Claude (via anthropic) to rewrite the onboarding prompt, and persists the new version.
- In practice we ran a 12‑scenario sweep: baseline overall 0.64 → improved prompt scored 0.77 after the loop saved `auto-v64`.
- Added a Nemotron-based local scorer (`score_session_locally`) so we can keep measuring between official Cekura runs.

## 4. What was built during the hackathon

- Rebuilt the landing page + onboarding UI into production polish (hero, CTA cards, workflow timeline, session dashboard with quality trends).
- Split onboarding vs session bots, added profile routing, personality profile generation, and immediate NV‑Embed ingestion.
- Added the Nemotron scoring loop, Cekura auto‑improvement endpoint, and session dashboard score visualizations/trends.
- Implemented fail‑safe persistence (fallback session saves, transcript extraction from context messages) so every call is logged even on early hang‑ups.

## 5. Feedback on the tools

### Nemotron (NVIDIA)

- **What worked well:** Streaming ASR remained stable even with background noise; Nemotron 3 Super stayed coherent across 8‑minute dialogues, and the `enable_thinking` flag prevented hallucinated tool calls.
- **Wishlist:** Better documentation on temperature vs creativity trade‑offs for long‑form coaching, and smaller checkpoint options for faster cold starts.

### Cekura

- **What worked well:** Scenario playback + scoring gave us concrete failure cases to fix, and the API surface was simple enough to automate prompt saves.
- **Wishlist:** More granular per‑turn feedback (e.g., which turn triggered a low empathy score) and a dry‑run mode for local testing without consuming credits.

### Pipecat

- **What worked well:** Tool registration + transcript logging are opinionated in the right way; integrating WebRTC + Twilio transports was straightforward.
- **Wishlist:** Built‑in helpers for fail‑safe session persistence (we wrote our own) and clearer debugging around `on_client_disconnected` ordering.

## 6. Live link (optional)

Currently running locally while we finish compliance. We can add a public link here once the Twilio number is re‑pointed through Pipecat Cloud.

---

> The original project documentation now lives in **Call Me Tomorrow README.md**.
