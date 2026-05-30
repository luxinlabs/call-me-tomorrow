// ─── State ────────────────────────────────────────────────────────────────
const params   = new URLSearchParams(location.search);
const channel  = params.get('channel') || 'career';
const horizon  = parseInt(params.get('horizon') || '5');
const phone    = params.get('phone') || '';
const isOnboarding = location.pathname === '/onboarding';

let sessionKey  = null;
let peerConn    = null;
let localStream = null;
let callSeconds = 0;
let timerInterval = null;
let sseSource   = null;

// ─── Overlay helpers ──────────────────────────────────────────────────────
const overlay = document.getElementById('overlay');
const overlayStatus = document.getElementById('overlayStatus');
function setOverlay(msg) { overlayStatus.textContent = msg; }
function hideOverlay()   { overlay.classList.add('hidden'); }

// ─── Timer ────────────────────────────────────────────────────────────────
function startTimer() {
  timerInterval = setInterval(() => {
    callSeconds++;
    const m = String(Math.floor(callSeconds / 60)).padStart(2, '0');
    const s = String(callSeconds % 60).padStart(2, '0');
    document.getElementById('timer').textContent = `${m}:${s}`;
  }, 1000);
}

// ─── Transcript via SSE ───────────────────────────────────────────────────
function startTranscriptStream(key) {
  if (sseSource) sseSource.close();
  sseSource = new EventSource(`/transcript-stream/${key}`);
  sseSource.onmessage = (e) => {
    const turn = JSON.parse(e.data);
    appendTurn(turn.role, turn.speaker, turn.text);
  };
}

function appendTurn(role, speaker, text) {
  const body = document.getElementById('transcriptBody');
  document.getElementById('emptyState')?.remove();
  document.getElementById('thinking').classList.remove('visible');

  const div = document.createElement('div');
  div.className = `turn ${role}`;
  div.innerHTML = `<span class="turn-speaker">${speaker}</span><div class="turn-bubble">${text}</div>`;
  body.appendChild(div);
  body.scrollTop = body.scrollHeight;

  if (role === 'user') {
    document.getElementById('thinking').classList.add('visible');
  }

  if (speaker === 'Future Me') {
    document.getElementById('callerName').textContent = 'Future Me';
  }
}

// ─── WebRTC ───────────────────────────────────────────────────────────────
async function startCall() {
  setOverlay('Starting call...');

  try {
    // 1. Get microphone
    localStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });

    // 2. POST /start
    setOverlay('Connecting to Future Me...');
    const startRes = await fetch('/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        transport: 'webrtc',
        body: { channel, time_horizon: horizon, phone: phone || null, force_onboarding: isOnboarding },
      }),
    });

    if (!startRes.ok) throw new Error(`/start → ${startRes.status}`);
    const startData = await startRes.json();
    sessionKey = startData.sessionId;

    // 3. Start SSE transcript stream
    startTranscriptStream(sessionKey);

    // 4. Create peer connection
    const iceConfig = startData.iceConfig || {};
    peerConn = new RTCPeerConnection({
      iceServers: iceConfig.iceServers || [{ urls: 'stun:stun.l.google.com:19302' }],
    });

    // Add local audio track
    localStream.getTracks().forEach(t => peerConn.addTrack(t, localStream));

    // Handle remote audio
    peerConn.ontrack = (e) => {
      const audio = document.getElementById('remoteAudio');
      audio.srcObject = e.streams[0];
      document.getElementById('waveform').classList.add('speaking');
    };

    // 5. Create offer
    const offer = await peerConn.createOffer();
    await peerConn.setLocalDescription(offer);

    // Wait for ICE gathering
    await new Promise(resolve => {
      if (peerConn.iceGatheringState === 'complete') { resolve(); return; }
      peerConn.addEventListener('icegatheringstatechange', () => {
        if (peerConn.iceGatheringState === 'complete') resolve();
      });
      setTimeout(resolve, 3000);
    });

    // 6. Exchange with Pipecat WebRTC signaling endpoint
    const offerRes = await fetch(`/sessions/${sessionKey}/api/offer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sdp: peerConn.localDescription.sdp,
        type: peerConn.localDescription.type,
      }),
    });

    if (!offerRes.ok) throw new Error(`/sessions/${sessionKey}/api/offer → ${offerRes.status}`);
    const answer = await offerRes.json();
    await peerConn.setRemoteDescription(new RTCSessionDescription(answer));

    // ── Connected ──
    hideOverlay();
    document.getElementById('statusDot').classList.add('active');
    document.getElementById('statusText').textContent = 'Connected';
    startTimer();

  } catch (err) {
    console.error('Call setup failed:', err);
    setOverlay(`Failed: ${err.message}`);
    const btn = document.createElement('button');
    btn.style.cssText = 'margin-top:1rem;padding:0.6rem 1.4rem;border:1px solid rgba(200,176,154,0.3);background:transparent;color:#c8b09a;font-family:DM Mono,monospace;font-size:0.68rem;cursor:pointer;border-radius:8px';
    btn.textContent = 'Try again';
    btn.onclick = () => { btn.remove(); overlayStatus.textContent=''; startCall(); };
    overlay.appendChild(btn);
  }
}

// ─── End call ─────────────────────────────────────────────────────────────
function endCall() {
  const btn = document.getElementById('btnEnd');
  if (btn) { btn.textContent = 'Ending...'; btn.classList.add('ending'); }

  if (peerConn) { peerConn.close(); peerConn = null; }
  if (localStream) { localStream.getTracks().forEach(t => t.stop()); }
  if (timerInterval) clearInterval(timerInterval);
  if (sseSource) sseSource.close();

  const dot = document.getElementById('statusDot');
  const statusText = document.getElementById('statusText');
  const waveform = document.getElementById('waveform');
  if (dot) dot.classList.remove('active');
  if (statusText) statusText.textContent = 'Ended';
  if (waveform) waveform.classList.remove('speaking');

  showEnvelopeAnimation();
}

function showEnvelopeAnimation() {
  const el = document.createElement('div');
  el.id = 'envelopeOverlay';
  el.style.cssText = `
    position:fixed;inset:0;background:rgba(10,10,15,.96);
    display:flex;flex-direction:column;align-items:center;justify-content:center;
    z-index:999;opacity:0;transition:opacity .5s ease;
  `;
  el.innerHTML = `
    <div id="envelopeWrap" style="position:relative;width:120px;height:90px;perspective:400px">
      <svg id="envelope" viewBox="0 0 120 90" fill="none" xmlns="http://www.w3.org/2000/svg"
           style="width:120px;height:90px;filter:drop-shadow(0 8px 32px rgba(200,176,154,.18))">
        <!-- Envelope body -->
        <rect x="4" y="20" width="112" height="66" rx="6" fill="rgba(22,22,25,.9)" stroke="rgba(200,176,154,.3)" stroke-width="1.2"/>
        <!-- Bottom flap (static) -->
        <path d="M4 82 L60 50 L116 82" fill="rgba(200,176,154,.06)" stroke="rgba(200,176,154,.18)" stroke-width="1"/>
        <!-- Left/right sides -->
        <path d="M4 20 L60 54" stroke="rgba(200,176,154,.18)" stroke-width="1"/>
        <path d="M116 20 L60 54" stroke="rgba(200,176,154,.18)" stroke-width="1"/>
        <!-- Top flap -->
        <path id="topFlap" d="M4 20 L60 54 L116 20" fill="rgba(200,176,154,.08)" stroke="rgba(200,176,154,.25)" stroke-width="1.2"
              style="transform-origin:60px 20px;transform:rotateX(0deg);transition:transform .6s ease .3s"/>
        <!-- Wax seal dot -->
        <circle cx="60" cy="55" r="6" fill="rgba(200,176,154,.25)" stroke="rgba(200,176,154,.4)" stroke-width="1"/>
        <circle cx="60" cy="55" r="2.5" fill="rgba(200,176,154,.5)"/>
      </svg>
    </div>
    <p id="envMsg" style="margin-top:1.8rem;font-family:'DM Mono',monospace;font-size:.64rem;
       letter-spacing:.12em;color:rgba(200,176,154,.7);text-transform:uppercase;
       opacity:0;transition:opacity .5s ease .8s">Sending to Future Me...</p>
    <p style="margin-top:.6rem;font-family:'DM Mono',monospace;font-size:.54rem;
       letter-spacing:.06em;color:rgba(200,176,154,.35);
       opacity:0;transition:opacity .5s ease 1.4s" id="envSub">Your profile is on its way.</p>
  `;
  document.body.appendChild(el);

  // Fade in overlay
  requestAnimationFrame(() => {
    el.style.opacity = '1';
    // Fold top flap closed
    setTimeout(() => {
      document.getElementById('topFlap').style.transform = 'rotateX(180deg)';
    }, 400);
    // Show text
    setTimeout(() => {
      document.getElementById('envMsg').style.opacity = '1';
      document.getElementById('envSub').style.opacity = '1';
    }, 800);
    // Fly envelope up and away
    setTimeout(() => {
      const wrap = document.getElementById('envelopeWrap');
      wrap.style.transition = 'transform 1s cubic-bezier(.4,0,.2,1), opacity .8s ease';
      wrap.style.transform = 'translateY(-80px) scale(.7)';
      wrap.style.opacity = '0';
      document.getElementById('envMsg').textContent = 'Delivered.';
    }, 2200);
    // Redirect home
    setTimeout(() => {
      window.location.href = '/';
    }, 3600);
  });
}

// ─── Boot ─────────────────────────────────────────────────────────────────
startCall();
