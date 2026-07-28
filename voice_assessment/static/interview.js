/**
 * Realtime duplex interview client with pause / end-of-turn speech gate.
 * Streams mic PCM16 @ 16 kHz only while the candidate is speaking;
 * signals activity_start / pause / resume / activity_end to the Live room.
 */
(() => {
  const params = new URLSearchParams(location.search);
  const itemId = params.get("item_id") || "unknown";
  const question = params.get("question") || "Please introduce yourself briefly.";
  const roomHint = params.get("room_id");

  const statusEl = document.getElementById("status");
  const turnEl = document.getElementById("turnState");
  const dotEl = document.getElementById("dot");
  const logEl = document.getElementById("log");
  const btnStart = document.getElementById("btnStart");
  const btnFinish = document.getElementById("btnFinish");
  const saveRec = document.getElementById("saveRec");
  const meterEl = document.getElementById("meter");

  let ws = null;
  let audioCtx = null;
  let workletNode = null;
  let mediaStream = null;
  let playTime = 0;
  let roomId = roomHint;
  let turnConfig = null;
  let interviewerPlaying = false;
  let gate = null;

  function setStatus(text, live = false, error = false) {
    statusEl.textContent = text;
    dotEl.className = "dot" + (error ? " error" : live ? " live" : "");
  }

  function setTurn(text) {
    if (turnEl) turnEl.textContent = text;
  }

  function addLine(role, text) {
    const div = document.createElement("div");
    div.className = "line";
    div.innerHTML = `<div class="who">${role}</div><div>${escapeHtml(text)}</div>`;
    logEl.appendChild(div);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  function rmsDb(int16) {
    if (!int16.length) return -120;
    let sum = 0;
    for (let i = 0; i < int16.length; i++) {
      const x = int16[i] / 32768;
      sum += x * x;
    }
    const rms = Math.sqrt(sum / int16.length) || 1e-12;
    return 20 * Math.log10(rms);
  }

  /** Client speech gate: distinguishes thinking pause vs end-of-turn. */
  class SpeechGate {
    constructor(cfg, sendJson, sendPcm) {
      this.cfg = cfg;
      this.sendJson = sendJson;
      this.sendPcm = sendPcm;
      this.state = "closed"; // closed | open | paused
      this.openCount = 0;
      this.closeCount = 0;
      this.silenceMs = 0;
      this.frameMs = 20;
      this.hadSpeechInTurn = false;
      // A fixed -45 dBFS threshold is wrong in both directions: it never opens on a
      // quiet laptop mic (noiseSuppression + AGC off), and it never closes in a noisy
      // room. Measure the room for ~1.2 s instead, then set the thresholds from it.
      this.calib = [];
      this.calibrated = false;
      this.baseOpenDb = cfg.gate_open_db;
      this.baseCloseDb = cfg.gate_close_db;
      // 300 ms pre-roll: the frames that *triggered* the open must still be sent, or
      // the word onset is clipped and the recogniser guesses at the first syllable.
      this.preRoll = [];
      this.preRollMax = 15;
    }

    _calibrate(db) {
      this.calib.push(db);
      if (this.calib.length < 60) return false; // ~1.2 s @ 20 ms
      const sorted = [...this.calib].sort((a, b) => a - b);
      const p95 = sorted[Math.floor(sorted.length * 0.95)];
      const floor = sorted[Math.floor(sorted.length * 0.5)];
      // +8 dB over the room's 95th percentile, clamped so a dead mic (-90) does not
      // make every frame "speech" and a loud room does not make speech unreachable.
      this.baseOpenDb = Math.min(Math.max(p95 + 8, -60), -25);
      this.baseCloseDb = this.baseOpenDb - 5;
      this.calibrated = true;
      addLine(
        "system",
        `room calibrated: noise floor ${floor.toFixed(1)} dBFS, p95 ${p95.toFixed(1)} — ` +
          `gate opens at ${this.baseOpenDb.toFixed(1)} dBFS` +
          (floor > -35 ? " (noisy room — a headset will help)" : "")
      );
      this.sendJson({
        type: "calibrated",
        noise_floor_db: +floor.toFixed(1),
        p95_db: +p95.toFixed(1),
        open_db: +this.baseOpenDb.toFixed(1),
      });
      return true;
    }

    onFrame(pcmBuffer) {
      const samples = new Int16Array(pcmBuffer);
      let db = rmsDb(samples);
      if (meterEl) {
        meterEl.textContent =
          `${db.toFixed(0)} dBFS` +
          (this.calibrated ? ` (open ${this.baseOpenDb.toFixed(0)})` : " calibrating…");
      }
      if (!this.calibrated) {
        this._calibrate(db);
        return; // never forward room tone
      }
      const openDb =
        this.baseOpenDb + (interviewerPlaying ? this.cfg.gate_barge_in_extra_db : 0);
      const closeDb =
        this.baseCloseDb + (interviewerPlaying ? this.cfg.gate_barge_in_extra_db : 0);

      if (db >= openDb) {
        this.openCount += 1;
        this.closeCount = 0;
        this.silenceMs = 0;
      } else if (db < closeDb) {
        this.closeCount += 1;
        this.openCount = 0;
        if (this.state === "open" || this.state === "paused") {
          this.silenceMs += this.frameMs;
        }
      } else {
        this.openCount = 0;
        this.closeCount = 0;
      }

      if (this.state === "closed" && this.openCount >= this.cfg.gate_open_frames) {
        this.state = "open";
        this.hadSpeechInTurn = true;
        this.sendJson({ type: "activity_start" });
        this.sendJson({ type: "resume" });
        setTurn("you are speaking");
        setStatus("live — listening to you", true);
        // Flush the pre-roll so the first syllable is not lost. The frames that
        // opened the gate are already in here.
        for (const buf of this.preRoll) this.sendPcm(buf);
        this.preRoll = [];
      }

      if (this.state === "open" && this.closeCount >= this.cfg.gate_close_frames) {
        // Dropped below close threshold — may be pause or end.
        if (this.silenceMs >= this.cfg.thinking_pause_ms && this.silenceMs < this.cfg.turn_end_silence_ms) {
          this.state = "paused";
          this.sendJson({ type: "pause" });
          setTurn("thinking pause — still your turn");
          setStatus("live — pause detected, waiting", true);
        }
      }

      if (
        (this.state === "open" || this.state === "paused") &&
        this.hadSpeechInTurn &&
        this.silenceMs >= this.cfg.turn_end_silence_ms
      ) {
        this.state = "closed";
        this.hadSpeechInTurn = false;
        this.silenceMs = 0;
        this.sendJson({ type: "activity_end" });
        setTurn("you finished — interviewer may reply");
        setStatus("live — end of turn", true);
        return; // do not forward this quiet frame
      }

      if (this.state === "paused" && this.openCount >= this.cfg.gate_open_frames) {
        this.state = "open";
        this.sendJson({ type: "resume" });
        setTurn("you resumed speaking");
        setStatus("live — listening to you", true);
      }

      // Forward speech (and a short trailing window while open)
      if (this.state === "open") {
        this.sendPcm(pcmBuffer);
      } else {
        // Keep the most recent 300 ms so an open can be back-dated to the onset.
        this.preRoll.push(pcmBuffer);
        if (this.preRoll.length > this.preRollMax) this.preRoll.shift();
      }
    }
  }

  async function ensureRoom() {
    if (roomId) return roomId;
    const res = await fetch("/api/live/rooms", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        item_id: itemId,
        question,
        save_recording: !!saveRec.checked,
      }),
    });
    if (!res.ok) throw new Error("failed to create live room");
    const data = await res.json();
    roomId = data.room_id;
    const u = new URL(location.href);
    u.searchParams.set("room_id", roomId);
    history.replaceState({}, "", u);
    return roomId;
  }

  async function loadConfig() {
    try {
      const res = await fetch("/api/live/config");
      if (res.ok) return await res.json();
    } catch (_) {}
    return {
      thinking_pause_ms: 900,
      turn_end_silence_ms: 1800,
      gate_open_db: -45,
      gate_close_db: -50,
      gate_open_frames: 3,
      gate_close_frames: 8,
      gate_barge_in_extra_db: 6,
    };
  }

  async function startMic(wsSocket) {
    // Ask for 16 kHz directly so the worklet does no resampling at all on the
    // common path. The worklet resamples correctly either way, but a native
    // 16 kHz context is exact and cheaper.
    try {
      audioCtx = new AudioContext({ sampleRate: 16000 });
    } catch (_) {
      audioCtx = new AudioContext();
    }
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: false,
      },
      video: false,
    });
    if (!window.__playCtx) {
      window.__playCtx = new AudioContext({ sampleRate: 24000 });
    }
    // ?v= is a cache buster: browsers cache AudioWorklet modules aggressively and
    // a stale worklet is invisible and silent.
    await audioCtx.audioWorklet.addModule("/static/mic-worklet.js?v=3");
    const source = audioCtx.createMediaStreamSource(mediaStream);
    workletNode = new AudioWorkletNode(audioCtx, "pcm16-capture");

    const sendJson = (obj) => {
      if (wsSocket.readyState === WebSocket.OPEN) wsSocket.send(JSON.stringify(obj));
    };
    const sendPcm = (buf) => {
      if (wsSocket.readyState === WebSocket.OPEN) wsSocket.send(buf);
    };
    gate = new SpeechGate(turnConfig, sendJson, sendPcm);

    workletNode.port.onmessage = (ev) => {
      // The worklet reports the real context rate once, before any audio. Send it
      // upstream so a rate mismatch is visible in the server log instead of
      // silently producing untranscribable audio.
      if (ev.data && ev.data.type === "meta") {
        addLine(
          "system",
          `mic: context ${ev.data.contextRate} Hz -> ${ev.data.targetRate} Hz ` +
            `(step ${ev.data.step.toFixed(3)})`
        );
        sendJson({ type: "hello", context_rate: ev.data.contextRate, step: ev.data.step });
        return;
      }
      gate.onFrame(ev.data);
    };
    source.connect(workletNode);
    const mute = audioCtx.createGain();
    mute.gain.value = 0;
    workletNode.connect(mute);
    mute.connect(audioCtx.destination);
  }

  function playPcm24k(pcmBytes) {
    const ctx = window.__playCtx || new AudioContext({ sampleRate: 24000 });
    window.__playCtx = ctx;
    if (ctx.state === "suspended") ctx.resume();
    const int16 = new Int16Array(pcmBytes);
    const f32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) f32[i] = int16[i] / 32768;
    const buffer = ctx.createBuffer(1, f32.length, 24000);
    buffer.copyToChannel(f32, 0);
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(ctx.destination);
    const now = ctx.currentTime;
    if (playTime < now) playTime = now + 0.02;
    src.start(playTime);
    playTime += buffer.duration;
  }

  async function start() {
    btnStart.disabled = true;
    try {
      setStatus("loading turn-taking config…");
      turnConfig = await loadConfig();
      setStatus("creating room…");
      await ensureRoom();
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws/live/${roomId}`);
      ws.binaryType = "arraybuffer";

      ws.onopen = async () => {
        setStatus("connecting mic…");
        await startMic(ws);
        setStatus("live — waiting for you", true);
        setTurn("listening");
        btnFinish.disabled = false;
        addLine(
          "system",
          `Realtime interview started. Short pauses (<${turnConfig.thinking_pause_ms}ms) keep your turn; ` +
            `~${turnConfig.turn_end_silence_ms}ms silence ends it so the interviewer can reply.`
        );
      };

      ws.onmessage = (ev) => {
        if (typeof ev.data !== "string") {
          playPcm24k(ev.data);
          return;
        }
        const msg = JSON.parse(ev.data);
        if (msg.type === "transcript") {
          const who = msg.role === "interviewer" ? "Interviewer" : "You";
          const last = logEl.lastElementChild;
          if (last && last.dataset.role === msg.role) {
            const body = last.querySelector(".body");
            const prev = body.textContent || "";
            if (msg.text.startsWith(prev)) body.textContent = msg.text;
            else if (!prev.includes(msg.text)) body.textContent = (prev + " " + msg.text).trim();
          } else {
            const div = document.createElement("div");
            div.className = "line";
            div.dataset.role = msg.role;
            div.innerHTML = `<div class="who">${who}</div><div class="body"></div>`;
            div.querySelector(".body").textContent = msg.text;
            logEl.appendChild(div);
            logEl.scrollTop = logEl.scrollHeight;
          }
        } else if (msg.type === "status") {
          if (msg.turn_config) turnConfig = { ...turnConfig, ...msg.turn_config };
          setStatus(msg.status || "live", msg.status === "live");
          if (msg.turn_state) setTurn(msg.turn_state);
        } else if (msg.type === "turn_state") {
          const labels = {
            listening: "listening for you",
            speaking: "you are speaking",
            paused: "thinking pause — still your turn",
            ended: "turn ended — interviewer may reply",
            interviewer: "interviewer speaking",
          };
          setTurn(labels[msg.turn_state] || msg.turn_state);
        } else if (msg.type === "interviewer_speaking") {
          interviewerPlaying = !!msg.active;
          if (msg.active) setTurn("interviewer speaking");
        } else if (msg.type === "done") {
          setStatus("finished");
          setTurn("done");
          addLine("system", "Interview finished. You can close this panel.");
          cleanup();
          if (window.parent && window.parent !== window) {
            window.parent.postMessage(
              { source: "voice-live", type: "done", room_id: roomId, package: msg.package },
              "*"
            );
          }
        } else if (msg.type === "error") {
          setStatus("error: " + msg.error, false, true);
          addLine("system", msg.error);
        }
      };

      ws.onerror = () => setStatus("websocket error", false, true);
      ws.onclose = () => {
        if (statusEl.textContent.startsWith("live")) setStatus("disconnected");
      };
    } catch (err) {
      setStatus(String(err), false, true);
      btnStart.disabled = false;
    }
  }

  function cleanup() {
    btnFinish.disabled = true;
    btnStart.disabled = false;
    interviewerPlaying = false;
    gate = null;
    try { workletNode && workletNode.disconnect(); } catch (_) {}
    try { mediaStream && mediaStream.getTracks().forEach((t) => t.stop()); } catch (_) {}
    try { audioCtx && audioCtx.close(); } catch (_) {}
    workletNode = null;
    mediaStream = null;
    audioCtx = null;
    if (ws && ws.readyState === WebSocket.OPEN) {
      try { ws.close(); } catch (_) {}
    }
    ws = null;
  }

  btnStart.addEventListener("click", start);
  btnFinish.addEventListener("click", () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      // Force end-of-turn before finishing the whole interview.
      ws.send(JSON.stringify({ type: "activity_end" }));
      ws.send(JSON.stringify({ type: "finish" }));
      setStatus("finishing…");
      setTurn("finishing");
    }
  });
})();
