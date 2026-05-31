# Call Me Tomorrow.

> *A phone call from your future self.*

Built at the YC Voice Agents Hackathon · May 2026  
Powered by **Pipecat** · **NVIDIA Nemotron 3 Super** · **Twilio** · **Gradium** · **Cekura**

---

## What it is

You pick up the phone. Your future self — five years from now — is on the line.

They're calling from a real place. They describe where they're sitting, what they just finished, how the light looks. Then they tell you what happened. What you did. What you were wrong about. What actually worked.

The call ends with three things to do: one in 30 days, one in 90, one in a year.

Next week, they call back. They ask what happened. They remember. They get better.

---

## How the call works

```
You call in (or we call you)
        ↓
Recall answers — warm, curious, not a form
  Eight questions over ~15 min
  Peak experience · Gap · Miracle question · Obstacle · Values · Fear · Readiness
  Your name woven in naturally
        ↓
Living profile built
  MBTI-style personality type · Tarot archetype · Now/Becoming snapshots
  Core tension · Superpower · Blind spot · Uncomfortable truth
        ↓
Future Me picks up (5 years out)
  Opens with exactly where they are right now: city, room, time of day, what they just did
  Speaks in first person past tense — "I remember", "I learned", "I was"
  Never a coach. Never generic. You, talking to you.
        ↓
8-minute conversation
  They reference your specific obstacle, your exact fear, your peak moment
  Archetype shapes tone and metaphors invisibly
  World context injected: what changed in your industry over 5 years
        ↓
Act 3: Three concrete actions
  30 days · 90 days · 1 year
  Calendar-schedulable behaviors, not aspirations
        ↓
Session saved · Scored · Memory distilled for next call
```

---

## Architecture

### Two bots, one memory sink

**Onboarding bot (Recall)**
- Runs on first call
- Eight-question psychology sequence (MI + Appreciative Inquiry + SFBT + GROW + ACT)
- Saves structured profile: name, role, peak, goal, obstacle, fear, values, readiness
- Triggers Nemotron personality analysis in background (MBTI, archetype, tensions)
- Ingests full transcript into ChromaDB for RAG

**Session bot (Future Me)**
- Runs on every subsequent call
- Opens with a mandatory scene: physical location + current activity + sensory detail
- Retrieves memory from past sessions (summaries + raw excerpts, top-8 by relevance)
- Assigns tarot archetype (11 Major Arcana, deterministic signal matching)
- Delivers 30/90/365-day action plan
- Scores session on 5 dimensions after every call

### Memory system

Every session produces two layers:

| Layer | What it is | How it's used |
|---|---|---|
| Raw chunks | 200-word overlapping transcript chunks | Granular recall of specific moments |
| Nemotron summary | 150-200 word distillation of what mattered | High-signal recall across sessions |

Both are embedded with NV-Embed-v2 (4096-dim) and stored in ChromaDB. Future Me retrieves the 8 most relevant chunks (summaries ranked above raw) before each session.

### Session quality scoring

Every completed call is automatically scored by Nemotron on 5 Call Me Tomorrow-specific dimensions:

| Dimension | What it measures |
|---|---|
| **Scene Grounding** | Did Future Me open with a specific location, activity, and sensory detail? |
| **Identity Fidelity** | Did they speak as the person, not as a coach? No "you should", no filler |
| **Profile Integration** | Did they weave in the user's actual obstacle, fear, peak moment, values? |
| **Plan Actionability** | Were 30/90/365 items calendar-schedulable behaviors, not vague goals? |
| **Emotional Honesty** | Did they acknowledge the hard parts honestly, not just encourage? |

Score = (sum of 5 dimensions) / 50 → 0.0–1.0 composite. Saved per-session with full breakdown.

---

## Tech stack

| Layer | Technology |
|---|---|
| Telephony | Twilio (PSTN → WebSocket bridge) |
| Voice orchestration | Pipecat 1.3.0 |
| STT | NVIDIA Nemotron WebSocket ASR |
| LLM | NVIDIA Nemotron 3 Super 120B (vLLM) |
| TTS | Gradium voice clone |
| Embeddings | NVIDIA NV-Embed-v2 (4096-dim) |
| Vector store | ChromaDB (local persistent) |
| Session store | SQLite |
| Transport (browser) | SmallWebRTC |
| Transport (phone) | Twilio media streams over FastAPI WebSocket |
| Auto-improvement | Cekura eval + Claude Opus prompt rewriting |

---

## Sponsor technology usage

**NVIDIA Nemotron 3 Super 120B**
Used for every AI-heavy step in the pipeline:
- Main conversation LLM for both Recall (onboarding) and Future Me (session)
- World context synthesis before each session (domain changes over 5 years)
- Real-time suggestions when caller hits a specific named challenge
- Post-onboarding personality profiling (MBTI type, archetype, tensions, blind spot)
- Post-session memory distillation (dense 150-200 word memory summaries for RAG)
- Session quality scoring on 5 product-specific dimensions
- Thinking mode explicitly disabled for all direct API calls (`chat_template_kwargs: {enable_thinking: false}`)

**NVIDIA NV-Embed-v2**
- All transcript chunks (200 words, 60-word overlap) embedded at 4096 dimensions
- Summaries embedded as priority recall chunks
- Cosine similarity retrieval via ChromaDB

**Pipecat / Daily**
- Dual pipeline architecture (onboarding and session workers)
- Transcript capture via `_BotTurnLogger` + context message fallback
- SmallWebRTC transport for browser calls; FastAPIWebsocketTransport for Twilio
- Pipeline frame flow: input → STT → user_agg → LLM → bot_logger → TTS → output → assistant_agg

**Twilio**
- Outbound calls to user's phone (`/api/call-outbound`)
- Localtunnel / ngrok for local development webhook routing
- TwiML → WebSocket bridge connecting phone calls to Pipecat

**Gradium**
- Single voice identity across all calls (voice ID: `Eu9iL_CYe8N-Gkx_`)
- 24kHz output for browser WebRTC; 8kHz for Twilio PSTN

**Cekura**
- Scripted eval callers test the bot against known scenarios
- Scores returned on empathy / specificity / coherence
- Failures passed to Claude Opus for automatic prompt rewriting
- New prompt saved to DB; all subsequent calls use it immediately

---

## Project structure

```
call-me-tomorrow/
└── server/
    ├── bot.py                  # FastAPI server + bot router + all HTTP endpoints
    ├── bot_onboarding.py       # Recall bot — 8-question profile intake
    ├── bot_session.py          # Future Me bot — RAG-enhanced 3-act session
    ├── simulation.py           # Future Me prompt builder (scene + archetype + memory)
    ├── archetype.py            # Tarot archetype assignment (deterministic signal matching)
    ├── personality.py          # Post-onboarding personality profiling via Nemotron
    ├── rag.py                  # ChromaDB + NV-Embed-v2 + Nemotron memory distillation
    ├── memory.py               # SQLite schema, migrations, all DB read/write
    ├── cekura_eval.py          # 5-dimension session scoring + Cekura + Claude loop
    ├── pre_call_analysis.py    # World context synthesis before each session
    ├── suggestions.py          # Curated knowledge cards + Nemotron fallback insights
    ├── channels.py             # Channel definitions (career / life) + session questions
    ├── transcript.py           # Transcript capture, formatting, context fallback
    ├── nemotron_llm.py         # vLLM wrapper with corrected TTFB metrics
    ├── nvidia_stt.py           # NVIDIA ASR WebSocket service
    ├── action_plan.py          # 30/90/365 plan formatter
    ├── static/
    │   ├── index.html          # Landing page (call card + onboarding + session CTAs)
    │   ├── dashboard.html      # Session scores table + bar chart + dimension breakdown
    │   ├── call.html           # Live call page with dual-sided transcript stream
    │   └── onboarding.html     # Standalone onboarding page
    ├── .env.example
    └── pyproject.toml
```

---

## Tarot archetype system

Each caller's onboarding answers are scored against 11 Major Arcana archetypes using keyword signal matching. The archetype is invisible to the caller — it shapes Future Me's tone, metaphors, and emotional register without being named.

| Signal pattern | Archetype | Tone |
|---|---|---|
| Vision + agency gap | The Magician | Possibility, reclamation |
| Intuition suppressed | The High Priestess | Stillness, inner authority |
| Responsibility + control | The Emperor | Structure, accountability |
| Drive + self-doubt | The Chariot | Movement, forward pull |
| Strength + anxiety | Strength | Patience, earned confidence |
| Clarity-seeking | The Hermit | Depth, deliberate pace |
| Hope after loss | The Star | Renewal, quiet optimism |
| Crisis + breakthrough | The Tower | Honesty, necessary disruption |
| Leap into unknown | The Fool | Curiosity, beginner's mind |
| Unfinished business | Judgement | Completion, reckoning |
| Integration | The World | Wholeness, earned arrival |

---

## Getting started locally

### Prerequisites

- Python 3.11+
- `uv` or standard `pip`
- Access to NVIDIA Nemotron endpoints
- Gradium API key
- Twilio account + phone number (for outbound calls)
- Cekura API key + agent ID (for evaluation loop, optional)

### Setup

```bash
cd call-me-tomorrow/server

cp .env.example .env
# Fill in your keys (see Environment variables below)

python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # or: uv sync

python bot.py
# → http://localhost:7860
```

For outbound Twilio calls, start the tunnel first:

```bash
bash start.sh
# Opens localtunnel → writes PUBLIC_URL to .env → starts server
```

### Environment variables

```bash
# NVIDIA Nemotron (provided at hackathon)
NVIDIA_ASR_URL=ws://your-asr-endpoint:8080
NEMOTRON_LLM_URL=http://your-llm-endpoint/v1
NEMOTRON_LLM_MODEL=nvidia/nemotron-3-super
NEMOTRON_LLM_API_KEY=EMPTY           # or your key
NEMOTRON_ENABLE_THINKING=false       # must be false for direct API calls

# NVIDIA Embeddings
NVIDIA_EMBED_URL=http://your-llm-endpoint/v1
NVIDIA_EMBED_MODEL=nvidia/nv-embed-v2

# Gradium TTS
GRADIUM_API_KEY=your_key
GRADIUM_VOICE_ID=your_voice_id

# Twilio
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_token
TWILIO_PHONE_NUMBER=+1xxxxxxxxxx

# Anthropic (prompt auto-improvement loop)
ANTHROPIC_API_KEY=sk-ant-...

# Cekura (evaluation)
CEKURA_API_KEY=your_key
CEKURA_AGENT_ID=your_agent_id

# Server
ENV=local
DB_PATH=calls.db
CHROMA_PATH=chroma_db
PUBLIC_URL=                          # auto-detected from ngrok/localtunnel if empty
```

---

## HTTP endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Landing page |
| `GET` | `/onboarding` | Standalone onboarding page |
| `GET` | `/dashboard` | Session scores + chart |
| `GET` | `/call` | Live call page (WebRTC) |
| `GET` | `/transcript/<id>` | Full session view: answers, plan, score breakdown, transcript |
| `GET` | `/transcript/<id>/download` | Download transcript as `.txt` |
| `GET` | `/profile/<phone>` | User profile: personality profile + all session history |
| `GET` | `/sessions` | Recent sessions JSON (used by dashboard) |
| `POST` | `/start` | Start browser WebRTC call |
| `POST` | `/api/call-outbound` | Initiate Twilio outbound call to phone number |
| `POST` | `/api/score-all` | Retroactively score all unscored completed sessions |
| `POST` | `/feedback-loop` | Run Cekura eval → Claude prompt improvement cycle |

---

## The auto-improvement loop

```
Session ends
    ↓
Nemotron scores on 5 dimensions (scene, identity, profile, plan, honesty)
    ↓
Score + breakdown saved to DB
    ↓
Cekura runs scripted eval callers → returns failures
    ↓
POST /feedback-loop
    ↓
Claude Opus rewrites intake prompt to fix failures
    ↓
New version saved to DB → all subsequent calls use it
```

Trigger manually:

```bash
curl -X POST http://localhost:7860/feedback-loop
```

---

## Business model

Executive coaches charge $300–800/hr. Call Me Tomorrow delivers a personalized, voice-native future-self simulation that updates with every call — for a fraction of the price.

The moat is memory. After 4 sessions, Future Me knows your specific obstacles and track record. After 12, it has pattern data across your decisions. After a year, it knows you better than most coaches ever will — because it remembers exactly what you said you'd do, and what actually happened.

---

## Built with

- [Pipecat](https://pipecat.ai) — voice agent orchestration
- [NVIDIA Nemotron](https://build.nvidia.com) — LLM, ASR, embeddings
- [Gradium](https://gradium.ai) — voice synthesis
- [Twilio](https://twilio.com) — telephony
- [Cekura](https://cekura.ai) — evaluation and auto-improvement
- [Anthropic Claude](https://anthropic.com) — prompt engineering loop
- [ChromaDB](https://trychroma.com) — vector memory store

---

*Built at the YC Voice Agents Hackathon, May 30 2026.*  
*"Pick up. It's you."*
