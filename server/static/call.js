// ─── State ────────────────────────────────────────────────────────────────
const params   = new URLSearchParams(location.search);
const channel  = params.get('channel') || 'career';
const horizon  = parseInt(params.get('horizon') || '5');
const phone    = params.get('phone') || '';

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
        body: { channel, time_horizon: horizon, phone: phone || null, force_onboarding: !phone },
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
  btn.textContent = 'Ending...';
  btn.classList.add('ending');

  if (peerConn) { peerConn.close(); peerConn = null; }
  if (localStream) { localStream.getTracks().forEach(t => t.stop()); }
  if (timerInterval) clearInterval(timerInterval);
  if (sseSource) sseSource.close();

  document.getElementById('statusDot').classList.remove('active');
  document.getElementById('statusText').textContent = 'Ended';
  document.getElementById('waveform').classList.remove('speaking');

  setTimeout(() => { window.location.href = '/'; }, 1500);
}

// ─── Boot ─────────────────────────────────────────────────────────────────
startCall();
