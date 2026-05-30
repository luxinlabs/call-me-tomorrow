"""Call Me Tomorrow — bot router and server entry point.

Routes each call to the right experience:
  - New user (no profile)  →  Onboarding bot (GROW-style, friend tone)
  - Returning user         →  Session bot (RAG + Future Me + suggestions)

Also mounts:
  GET  /          Custom landing page (channel + time horizon selector)
  POST /feedback-loop   Cekura → Claude auto-improvement
  GET  /sessions        Debug: recent sessions
"""

import os
import uuid

from dotenv import load_dotenv
from fastapi import Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pipecat.runner.run import app  # noqa: E402
from pipecat.runner.types import (
    RunnerArguments,
    SmallWebRTCRunnerArguments,
    WebSocketRunnerArguments,
)
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

from bot_onboarding import run_onboarding
from bot_session import run_session
from cekura_eval import run_feedback_loop
from channels import get_channel
from memory import get_session, get_user_by_phone, init_db
from pre_call_analysis import run_pre_call_analysis
from transcript import turns_to_html

load_dotenv(override=True)

# ── Static files + landing page ───────────────────────────────────────────────
_STATIC = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/", include_in_schema=False)
async def landing():
    return FileResponse(os.path.join(_STATIC, "index.html"))


# ── Lifecycle ─────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def _startup():
    init_db()


# ── API ───────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "call-me-tomorrow"}


@app.get("/sessions")
async def list_sessions():
    import sqlite3
    db = os.getenv("DB_PATH", "calls.db")
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT s.id, u.name, s.channel, s.archetype, s.status, s.created_at, s.cekura_score "
            "FROM sessions s LEFT JOIN users u ON s.user_id=u.id "
            "ORDER BY s.id DESC LIMIT 20"
        ).fetchall()
        conn.close()
        return JSONResponse([dict(r) for r in rows])
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/feedback-loop")
async def trigger_feedback_loop():
    result = await run_feedback_loop()
    return JSONResponse(result)


# ── Outbound phone call ───────────────────────────────────────────────────────
# Stores pending call config until Twilio's WebSocket connects.
_pending_calls: dict[str, dict] = {}


@app.post("/api/call-outbound")
async def call_outbound(request: Request):
    """Initiate a Twilio outbound call to the user's phone.

    Body: { phone, channel, time_horizon, force_onboarding? }
    Returns: { status, call_sid } or { error }
    """
    body = await request.json()
    phone = (body.get("phone") or "").strip()
    if not phone:
        return JSONResponse({"error": "phone number required"}, status_code=400)

    public_url = os.getenv("PUBLIC_URL", "").rstrip("/")
    if not public_url:
        return JSONResponse({"error": "PUBLIC_URL not configured on server"}, status_code=500)

    token = str(uuid.uuid4())
    _pending_calls[token] = {
        "phone": phone,
        "channel": body.get("channel", "career"),
        "time_horizon": int(body.get("time_horizon", 5)),
        "force_onboarding": bool(body.get("force_onboarding", True)),
    }

    try:
        from twilio.rest import Client as TwilioClient
        client = TwilioClient(
            os.environ["TWILIO_ACCOUNT_SID"],
            os.environ["TWILIO_AUTH_TOKEN"],
        )
        call = client.calls.create(
            to=phone,
            from_=os.environ["TWILIO_PHONE_NUMBER"],
            url=f"{public_url}/outbound-twiml/{token}",
            method="GET",
        )
        logger.info(f"Outbound call initiated → {phone}  SID={call.sid}  token={token}")
        return JSONResponse({"status": "calling", "call_sid": call.sid})

    except Exception as e:
        _pending_calls.pop(token, None)
        logger.error(f"Twilio outbound call failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/outbound-twiml/{token}")
async def outbound_twiml(token: str):
    """TwiML that connects Twilio's outbound call to our bot WebSocket."""
    public_url = os.getenv("PUBLIC_URL", "").rstrip("/")
    ws_url = (
        public_url
        .replace("https://", "wss://")
        .replace("http://", "ws://")
    ) + f"/outbound-ws/{token}"

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f'<Stream url="{ws_url}"/>'
        "</Connect></Response>"
    )
    return Response(content=xml.encode(), media_type="text/xml")


@app.websocket("/outbound-ws/{token}")
async def outbound_ws(websocket: WebSocket, token: str):
    """Pipecat WebSocket handler for outbound Twilio calls."""
    await websocket.accept()

    config = _pending_calls.pop(token, None)
    if not config:
        logger.warning(f"No pending call for token {token}")
        await websocket.close()
        return

    phone = config["phone"]
    channel_id = config["channel"]
    time_horizon = config["time_horizon"]
    force_onboarding = config["force_onboarding"]

    logger.info(f"Outbound WebSocket connected: token={token} phone={phone}")

    try:
        _, call_data = await parse_telephony_websocket(websocket)
        serializer = TwilioFrameSerializer(
            stream_sid=call_data["stream_id"],
            call_sid=call_data["call_id"],
            account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
            auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
        )
        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                add_wav_header=False,
                serializer=serializer,
            ),
        )
    except Exception as e:
        logger.error(f"Failed to set up outbound transport: {e}")
        await websocket.close()
        return

    transport_overrides = {"audio_in_sample_rate": 8000, "audio_out_sample_rate": 8000}
    init_db()

    channel_obj = get_channel(channel_id)
    user = get_user_by_phone(phone)
    world_context = await run_pre_call_analysis(
        channel_id=channel_id,
        time_horizon=time_horizon,
        user_role=user.get("role", "") if user else "",
        user_goal=user.get("profile_summary", "") if user else "",
        channel_name=channel_obj.name,
    )

    if force_onboarding or not user or not user.get("onboarding_done"):
        await run_onboarding(
            transport, channel_id, time_horizon, phone,
            world_context=world_context, **transport_overrides,
        )
    else:
        await run_session(
            transport, channel_id, time_horizon, phone,
            world_context=world_context, **transport_overrides,
        )


@app.get("/transcript/{session_id}")
async def get_transcript(session_id: int):
    """Return plain-text transcript for a session."""
    session = get_session(session_id)
    if not session:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"transcript": session.get("transcript", ""), "session": session})


@app.get("/transcript/{session_id}/download")
async def download_transcript(session_id: int):
    """Download transcript as a .txt file."""
    from fastapi.responses import PlainTextResponse
    session = get_session(session_id)
    if not session:
        return JSONResponse({"error": "not found"}, status_code=404)
    transcript = session.get("transcript") or "(no transcript recorded)"
    filename = f"call_me_tomorrow_{session.get('channel','session')}_{session_id}.txt"
    return PlainTextResponse(
        content=transcript,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/profile/{phone}")
async def profile_page(phone: str):
    """HTML profile page showing user info + all session transcripts."""
    from fastapi.responses import HTMLResponse
    import sqlite3, urllib.parse

    phone = urllib.parse.unquote(phone)
    user = get_user_by_phone(phone)
    if not user:
        return HTMLResponse("<p style='font-family:monospace;padding:2rem'>Profile not found.</p>", status_code=404)

    db = os.getenv("DB_PATH", "calls.db")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM sessions WHERE user_id=? ORDER BY id DESC LIMIT 20",
        (user["id"],)
    ).fetchall()
    conn.close()

    from transcript import Turn, turns_to_html
    import json as _json

    session_blocks = ""
    for row in rows:
        s = dict(row)
        title = f"{s.get('channel','').title()} — {s.get('archetype','').title()} — {s.get('created_at','')[:10]}"
        transcript_text = s.get("transcript") or ""
        if transcript_text:
            # Re-render plain text as HTML turns
            lines = transcript_text.split("\n\n")
            turns = []
            for line in lines:
                if line.startswith("[You]"):
                    turns.append(Turn(role="user", speaker="You", text=line[6:].strip()))
                elif line.startswith("[") and "]" in line:
                    sp = line[1:line.index("]")]
                    turns.append(Turn(role="assistant", speaker=sp, text=line[line.index("]")+2:].strip()))
            session_blocks += turns_to_html(turns, title=title)
            dl_link = f'<a href="/transcript/{s["id"]}/download" class="dl-link">↓ download transcript</a>'
            session_blocks = session_blocks.replace("</section>", dl_link + "\n</section>")
        else:
            session_blocks += f'<section class="transcript-block"><h3 class="transcript-title">{title}</h3><p class="no-transcript">Transcript not yet available.</p></section>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Profile — {user.get('name','User')}</title>
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;1,300&family=DM+Mono:wght@300;400&display=swap" rel="stylesheet"/>
  <style>
    :root {{ --void:#0a0a0f; --oracle:#c8b09a; --parchment:#e8e4dc; --dusk:#1c1c22; --answer:#5ab87a; --border:rgba(200,176,154,0.12); }}
    *,*::before,*::after {{ box-sizing:border-box; margin:0; padding:0; }}
    body {{ background:var(--void); color:var(--parchment); font-family:'DM Mono',monospace; font-weight:300; min-height:100vh; }}
    .page {{ max-width:700px; margin:0 auto; padding:3rem 2rem 6rem; }}
    .back {{ font-size:0.65rem; letter-spacing:0.1em; color:var(--oracle); opacity:0.6; text-decoration:none; display:inline-block; margin-bottom:2.5rem; }}
    .back:hover {{ opacity:1; }}
    .profile-name {{ font-family:'Cormorant Garamond',serif; font-size:3rem; font-weight:300; color:var(--parchment); line-height:1; }}
    .profile-meta {{ margin-top:0.5rem; font-size:0.65rem; letter-spacing:0.08em; color:var(--oracle); opacity:0.7; }}
    .profile-summary {{ margin-top:1.25rem; font-size:0.78rem; line-height:1.75; opacity:0.7; max-width:500px; }}
    .divider {{ width:100%; height:1px; background:var(--border); margin:2.5rem 0; }}
    .section-label {{ font-size:0.55rem; letter-spacing:0.2em; text-transform:uppercase; color:var(--oracle); opacity:0.5; margin-bottom:1.5rem; }}
    .transcript-block {{ margin-bottom:2.5rem; border:1px solid var(--border); border-radius:12px; overflow:hidden; }}
    .transcript-title {{ font-size:0.65rem; letter-spacing:0.1em; text-transform:uppercase; color:var(--oracle); opacity:0.6; padding:1rem 1.25rem 0.75rem; border-bottom:1px solid var(--border); }}
    .transcript-turns {{ padding:1.25rem; display:flex; flex-direction:column; gap:1rem; }}
    .turn {{ display:flex; flex-direction:column; gap:0.25rem; }}
    .speaker {{ font-size:0.58rem; letter-spacing:0.12em; text-transform:uppercase; opacity:0.45; }}
    .turn-user .speaker {{ color:var(--parchment); }}
    .turn-bot .speaker {{ color:var(--oracle); }}
    .turn p {{ font-size:0.78rem; line-height:1.65; }}
    .turn-user p {{ opacity:0.85; }}
    .turn-bot p {{ font-family:'Cormorant Garamond',serif; font-style:italic; font-size:0.95rem; color:var(--parchment); opacity:0.9; }}
    .no-transcript {{ font-size:0.7rem; opacity:0.4; padding:1rem 1.25rem 1.25rem; }}
    .dl-link {{ display:inline-block; margin:0 1.25rem 1.25rem; font-size:0.6rem; letter-spacing:0.1em; color:var(--answer); text-decoration:none; opacity:0.7; }}
    .dl-link:hover {{ opacity:1; }}
  </style>
</head>
<body>
<div class="page">
  <a href="/" class="back">← Call Me Tomorrow</a>
  <div class="profile-name">{user.get('name','Unknown')}</div>
  <div class="profile-meta">{user.get('role','')} &nbsp;·&nbsp; {', '.join(user.get('channels',[]))} &nbsp;·&nbsp; {user.get('time_horizon',5)} year horizon</div>
  <p class="profile-summary">{user.get('profile_summary','')}</p>
  <div class="divider"></div>
  <p class="section-label">Call history</p>
  {session_blocks if session_blocks else '<p style="font-size:0.75rem;opacity:0.4">No sessions yet.</p>'}
</div>
</body>
</html>"""
    return HTMLResponse(html)


# ── Bot router ────────────────────────────────────────────────────────────────

async def bot(runner_args: RunnerArguments) -> None:
    """Main entry point. Routes to onboarding or session based on user profile."""

    # Read UI selections from the landing page POST body
    body = runner_args.body or {}
    channel_id = body.get("channel", "life")
    time_horizon = int(body.get("time_horizon", 5))
    phone = body.get("phone") or None

    # Route: onboarding for new users OR if explicitly requested
    force_onboarding = body.get("force_onboarding", False)
    user = get_user_by_phone(phone) if phone else None
    is_new = force_onboarding or not user or not user.get("onboarding_done")

    logger.info(
        f"Call: channel={channel_id} horizon={time_horizon} "
        f"phone={phone} new_user={is_new}"
    )

    # Run pre-call analysis concurrently while setting up transport
    channel = get_channel(channel_id)
    user_role = user.get("role", "") if user else ""
    user_goal = user.get("profile_summary", "") if user else ""
    world_context = await run_pre_call_analysis(
        channel_id=channel_id,
        time_horizon=time_horizon,
        user_role=user_role,
        user_goal=user_goal,
        channel_name=channel.name,
    )

    transport_overrides: dict = {}

    krisp_filter = None
    if os.environ.get("ENV") != "local":
        try:
            from pipecat.audio.filters.krisp_viva_filter import KrispVivaFilter
            krisp_filter = KrispVivaFilter()
        except ImportError:
            pass

    match runner_args:
        case SmallWebRTCRunnerArguments():
            webrtc_conn: SmallWebRTCConnection = runner_args.webrtc_connection
            transport = SmallWebRTCTransport(
                webrtc_connection=webrtc_conn,
                params=TransportParams(
                    audio_in_enabled=True,
                    audio_in_filter=krisp_filter,
                    audio_out_enabled=True,
                ),
            )

        case WebSocketRunnerArguments():
            transport_overrides = {"audio_in_sample_rate": 8000, "audio_out_sample_rate": 8000}
            _, call_data = await parse_telephony_websocket(runner_args.websocket)
            # For Twilio, use caller ID as phone if not passed in body
            twilio_from = call_data.get("from") or call_data.get("from_number")
            if not phone:
                phone = twilio_from
            serializer = TwilioFrameSerializer(
                stream_sid=call_data["stream_id"],
                call_sid=call_data["call_id"],
                account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
                auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
            )
            transport = FastAPIWebsocketTransport(
                websocket=runner_args.websocket,
                params=FastAPIWebsocketParams(
                    audio_in_enabled=True,
                    audio_in_filter=krisp_filter,
                    audio_out_enabled=True,
                    add_wav_header=False,
                    serializer=serializer,
                ),
            )

        case _:
            logger.error(f"Unsupported runner args: {type(runner_args)}")
            return

    init_db()

    if is_new:
        await run_onboarding(
            transport, channel_id, time_horizon, phone,
            world_context=world_context, **transport_overrides
        )
    else:
        await run_session(
            transport, channel_id, time_horizon, phone,
            world_context=world_context, **transport_overrides
        )


if __name__ == "__main__":
    from pipecat.runner.run import main
    main()
