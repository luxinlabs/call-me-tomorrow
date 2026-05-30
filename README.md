# Call Me Tomorrow.

> *A phone call from your future self.*

Built at the YC Voice Agents Hackathon · Powered by Pipecat · Nemotron 3 Super 120B · Twilio · Cekura

---

## What is this?

You call a phone number. Your future self picks up.

Over 8 minutes, a voice that sounds like you — five years from now — walks you through what happened, what you did, and what you wish you'd started sooner. The call ends with three concrete actions: one for the next 30 days, one for 90, one for a year from now.

The week after, it calls you back. It remembers what it told you. It asks what actually happened. And it gets smarter.

---

## How it works

```
Inbound call (Twilio)
        ↓
Pipecat orchestration
        ↓
STT: Nemotron Speech Streaming
        ↓
Act 1 — Intake (~3 min)
  Five voice questions → values, fears, dream role, blockers, time horizon
        ↓
Tarot archetype assigned
  22 Major Arcana mapped to life narrative lens
        ↓
Act 2 — Simulation (~3 min)
  Nemotron 3 Super 120B generates Future Me monologue
  Gradium voice delivers it
  Back-and-forth coaching exchange
        ↓
Act 3 — Delivery (~2 min)
  Action plan: 30 / 90 / 365 day milestones
  "I'll call you again next week."
        ↓
Transcript logged → Cekura scores → session saved
        ↓
Week N: What happened vs what Future Me predicted → model updates
```

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Telephony | Twilio |
| Orchestration | Pipecat Cloud |
| STT | Nemotron Speech Streaming (NVIDIA / AWS) |
| LLM | Nemotron 3 Super 120B (NVIDIA / AWS) |
| TTS | Gradium voice clone |
| Evaluation | Cekura |
| Transport (local) | SmallWebRTC |
| Transport (prod) | Twilio media streams |

---

## The auto-improvement loop

This is not a one-shot demo. Every call feeds the next one.

```
Call 1 → Future Me predicts: "You'll get the promotion in 18 months"
                    ↓
         Real life happens
                    ↓
Call 2 → User reports: "I got it in 6 months"
                    ↓
         Delta logged → Cekura scores prediction accuracy
                    ↓
         System prompt rewritten → Future Me recalibrates
                    ↓
Call N → Future Me has memory, track record, earned trust
```

Cekura evaluates every call on three dimensions:

- **Empathy score** — did Future Me feel emotionally resonant?
- **Plan accuracy** — were the actions specific and achievable?
- **Narrative coherence** — did the Tarot framing hold across the full call?

---

## Tarot archetype system

Each intake call maps to one of the 22 Major Arcana based on the user's five answers. The archetype sets the narrative lens for Future Me's monologue.

| Intake signal | Archetype examples |
|--------------|-------------------|
| Fear of stagnation + vision of leadership | The Emperor, The Chariot |
| Creative block + dream of expression | The Star, The Magician |
| Feeling lost + long time horizon | The Fool, The World |
| External pressure + fear of failure | The Tower, Strength |
| Transition + desire for meaning | Judgement, The Hermit |

The archetype is never mentioned explicitly to the user. It shapes *how* Future Me speaks — the tone, the metaphors, the emotional register — not what it says.

---

## Getting started

### Prerequisites

- Python 3.11+
- `uv` package manager
- Nemotron STT + LLM endpoints (provided at the hackathon)
- Gradium API key
- Twilio account with a voice-capable number

### Setup

```bash
git clone https://github.com/your-org/call-me-tomorrow.git
cd call-me-tomorrow/server

cp .env.example .env
# Fill in: GRADIUM_API_KEY, TWILIO_*, NVIDIA_ASR_URL, NEMOTRON_LLM_URL

# Create venv and install
python3.11 -m venv .venv
source .venv/bin/activate
pip install "pipecat-ai[gradium,openai,runner,websocket]>=1.3.0" \
  pipecatcloud twilio anthropic python-dotenv aiohttp loguru

python bot.py
```

Open `http://localhost:7860` and click Connect to take a call locally.

### Environment variables

```bash
# Nemotron (NVIDIA / AWS — provided at hackathon)
NVIDIA_ASR_URL=ws://44.241.251.184:8080
NEMOTRON_LLM_URL=http://nemotron-fleet-alb-1322439314.us-west-2.elb.amazonaws.com/v1
NEMOTRON_LLM_MODEL=nvidia/nemotron-3-super

# Gradium
GRADIUM_API_KEY=your_key_here

# Twilio
TWILIO_ACCOUNT_SID=AC16ad99e390a79837b04bbdbc9ecda107
TWILIO_AUTH_TOKEN=your_token_here
TWILIO_PHONE_NUMBER=+18446380121

# Anthropic (feedback loop)
ANTHROPIC_API_KEY=your_key_here

# Cekura (evaluation)
CEKURA_API_KEY=your_key_here
CEKURA_AGENT_ID=your_agent_id
```

---

## Deploy to Pipecat Cloud

```bash
# Install CLI
pip install pipecat-ai-cli
pc cloud auth login

# Upload secrets
pc cloud secrets set call-me-tomorrow-secrets --file .env

# Deploy
pc cloud deploy
```

Then wire your Twilio number to the deployed service with this TwiML Bin:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://api.pipecat.daily.co/ws/twilio">
      <Parameter name="_pipecatCloudServiceHost"
        value="call-me-tomorrow.YOUR_ORG_NAME"/>
    </Stream>
  </Connect>
</Response>
```

---

## Evaluate with Cekura

```bash
# From Claude Code with the Cekura plugin installed:
/cekura-report
```

This spins up 10–20 simulated callers with varied archetypes, runs full conversations against your deployed bot, and returns transcripts, scores, and failure modes. Use the output to trigger the auto-improvement loop:

```bash
curl -X POST http://localhost:7860/feedback-loop
```

This fetches the Cekura scores, sends failures to Claude, and saves an improved intake prompt. All subsequent calls use it automatically.

Select **Pipecat** as the provider when connecting your agent in the Cekura dashboard.

---

## The five intake questions

These are the only questions Future Me asks. Everything else is derived.

1. **"Describe yourself in one word — right now, today."**
   *Maps to: self-perception, archetype seed*

2. **"What's the thing that keeps you up at night?"**
   *Maps to: core fear, shadow archetype*

3. **"If everything went right, where would you be in five years?"**
   *Maps to: vision, aspiration archetype*

4. **"What's the one thing standing between you and that?"**
   *Maps to: obstacle, blocker signal*

5. **"What would success actually feel like — not look like, feel like?"**
   *Maps to: values, emotional register for Future Me's voice*

---

## Project structure

```
call-me-tomorrow/
├── server/
│   ├── bot.py              # Main Pipecat bot — 3-act pipeline
│   ├── archetype.py        # Tarot mapping (11 Major Arcana, signal-word scoring)
│   ├── simulation.py       # Future Me + intake prompt builders
│   ├── action_plan.py      # 30/90/365 plan formatter
│   ├── memory.py           # SQLite session store
│   ├── cekura_eval.py      # Cekura scoring + Claude auto-improvement
│   ├── main.py             # FastAPI server (/health, /sessions, /feedback-loop)
│   ├── nemotron_llm.py     # NVIDIA vLLM wrapper
│   ├── nvidia_stt.py       # NVIDIA ASR WebSocket
│   └── .env.example
├── pcc-deploy.toml         # Pipecat Cloud config
└── README.md
```

---

## Business model

Executive coaches charge $500/hr. Call Me Tomorrow delivers a personalized, voice-native future-self simulation that updates weekly based on your actual decisions — for $30/month.

The auto-improvement loop is the moat. Every call makes the next one more accurate. After 12 weeks, Future Me has a track record. After a year, it knows you better than you know yourself.

---

## Built with

- [Pipecat](https://pipecat.ai) — voice agent orchestration
- [NVIDIA Nemotron](https://www.nvidia.com/en-us/ai/) — open-weights STT + LLM
- [AWS](https://aws.amazon.com) — compute infrastructure
- [Twilio](https://twilio.com) — telephony
- [Gradium](https://gradium.ai) — TTS
- [Cekura](https://cekura.ai) — evaluation and auto-improvement
- [Daily / Pipecat Cloud](https://daily.co) — deployment and WebRTC

---

*Call Me Tomorrow was built at the YC Voice Agents Hackathon, May 2026.*
*"Pick up. It's you."*
# call-me-tomorrow
