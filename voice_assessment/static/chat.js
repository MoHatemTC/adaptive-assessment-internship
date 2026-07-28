/**
 * Free-form Gemini Live voice chat via WebSocket duplex.
 * POST /api/live/rooms {mode:"chat"} → ws://host/ws/live/{room_id}
 */
(() => {
  const params = new URLSearchParams(location.search);
  const roomHint = params.get("room_id");

  const statusEl = document.getElementById("status");
  const turnEl = document.getElementById("turnState");
  const dotEl = document.getElementById("dot");
  const logEl = document.getElementById("log");
  const btnStart = document.getElementById("btnStart");
  const btnHangup = document.getElementById("btnHangup");
  const micLevelEl = document.getElementById("micLevel");
  const micFillEl = document.getElementById("micFill");

  let ws = null;
  let audioCtx = null;
  let workletNode = null;
  let mediaStream = null;
  let playTime = 0;
  let roomId = roomHint;
  let turnConfig = null;
  let assistantPlaying = false;
  let streamContinuous = false; // gated + activity markers (server VAD broken on Live preview)
  let framesSent = 0;
  let lastLevelUi = 0;

  function setStatus(text, live = false, error = false) {
    statusEl.textContent = text;
    dotEl.className = "dot" + (error ? " error" : live ? " live" : "");
  }

  function setTurn(text) {
    turnEl.textContent = text;
  }

  function appendTranscript(role, text) {
    const who = role === "interviewer" || role === "assistant" ? "Assistant" : "You";
    const key = role === "interviewer" ? "assistant" : role;
    const last = logEl.lastElementChild;
    if (last && last.dataset.role === key) {
      const body = last.querySelector(".body");
      const prev = body.textContent || "";
      if (text.startsWith(prev)) body.textContent = text;
      else if (!prev.includes(text)) body.textContent = (prev + " " + text).trim();
      logEl.scrollTop = logEl.scrollHeight;
      return;
    }
    const div = document.createElement("div");
    div.className = "line";
    div.dataset.role = key;
    div.innerHTML = `<div class="who">${who}</div><div class="body"></div>`;
    div.querySelector(".body").textContent = text;
    logEl.appendChild(div);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function addSystem(text) {
    const div = document.createElement("div");
    div.className = "line";
    div.innerHTML = `<div class="who">system</div><div class="body"></div>`;
    div.querySelector(".body").textContent = text;
    logEl.appendChild(div);
    logEl.scrollTop = logEl.scrollHeight;
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

  function updateMicMeter(db) {
    const now = performance.now();
    if (now - lastLevelUi < 80) return;
    lastLevelUi = now;
    // Map -60..0 dB → 0..100%
    const pct = Math.max(0, Math.min(100, ((db + 60) / 60) * 100));
    if (micLevelEl) micLevelEl.textContent = `${db.toFixed(0)} dB`;
    if (micFillEl) {
      micFillEl.style.width = `${pct}%`;
      micFillEl.style.background = db > -45 ? "#3ecf8e" : db > -55 ? "#f0b429" : "#e35d6a";
    }
  }

  class SpeechGate {
    constructor(cfg, sendJson, sendPcm) {
      this.cfg = cfg;
      this.sendJson = sendJson;
      this.sendPcm = sendPcm;
      this.state = "closed";
      this.openCount = 0;
      this.closeCount = 0;
      this.silenceMs = 0;
      this.frameMs = 20;
      this.hadSpeechInTurn = false;
    }

    onFrame(pcmBuffer) {
      const samples = new Int16Array(pcmBuffer);
      const db = rmsDb(samples);
      updateMicMeter(db);

      // Continuous+server-VAD is broken on gemini-3.1-flash-live-preview — do not use.
      if (streamContinuous) {
        this.sendPcm(pcmBuffer);
        framesSent += 1;
        return;
      }

      const openDb =
        this.cfg.gate_open_db + (assistantPlaying ? this.cfg.gate_barge_in_extra_db : 0);
      const closeDb =
        this.cfg.gate_close_db + (assistantPlaying ? this.cfg.gate_barge_in_extra_db : 0);

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
      }

      if (this.state === "open" && this.closeCount >= this.cfg.gate_close_frames) {
        if (
          this.silenceMs >= this.cfg.thinking_pause_ms &&
          this.silenceMs < this.cfg.turn_end_silence_ms
        ) {
          this.state = "paused";
          this.sendJson({ type: "pause" });
          setTurn("thinking pause — still your turn");
          setStatus("live — pause, still your turn", true);
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
        setTurn("your turn ended — assistant may reply");
        setStatus("live — waiting for reply", true);
        return;
      }

      if (this.state === "paused" && this.openCount >= this.cfg.gate_open_frames) {
        this.state = "open";
        this.sendJson({ type: "resume" });
        setTurn("you resumed");
        setStatus("live — listening to you", true);
      }

      if (this.state === "open") {
        this.sendPcm(pcmBuffer);
        framesSent += 1;
      }
    }
  }

  let gate = null;

  async function roomExists(id) {
    try {
      const res = await fetch(`/api/live/rooms/${id}`);
      if (!res.ok) return false;
      const data = await res.json();
      // Dead rooms after server restart or hang-up cannot be rejoined.
      return data && data.status !== "finished" && data.status !== "error";
    } catch (_) {
      return false;
    }
  }

  async function createFreshRoom() {
    const res = await fetch("/api/live/rooms", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        item_id: "live_chat",
        mode: "chat",
        question:
          "Greet the human warmly in one short sentence and invite them to talk " +
          "about whatever they like. Then listen.",
        save_recording: false,
      }),
    });
    if (!res.ok) throw new Error("failed to create chat room");
    const data = await res.json();
    roomId = data.room_id;
    history.replaceState({}, "", `/?room_id=${roomId}&mode=chat`);
    return roomId;
  }

  async function ensureChatRoom() {
    if (roomId && (await roomExists(roomId))) return roomId;
    if (roomId) {
      addSystem(`Previous room ${roomId} is gone (server restart or ended). Creating a new call…`);
      roomId = null;
    }
    return createFreshRoom();
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
    // 16 kHz natively so the worklet needs no resampling. A non-multiple context
    // rate (44.1 kHz) used to emit mislabelled audio Gemini could not transcribe.
    try {
      audioCtx = new AudioContext({ sampleRate: 16000 });
    } catch (_) {
      audioCtx = new AudioContext();
    }
    if (audioCtx.state === "suspended") await audioCtx.resume();
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        // Help quiet laptop mics reach Gemini.
        autoGainControl: true,
      },
      video: false,
    });
    if (!window.__playCtx) {
      window.__playCtx = new AudioContext({ sampleRate: 24000 });
    }
    await audioCtx.audioWorklet.addModule("/static/mic-worklet.js?v=3");
    const source = audioCtx.createMediaStreamSource(mediaStream);
    workletNode = new AudioWorkletNode(audioCtx, "pcm16-capture");

    const sendJson = (obj) => {
      if (wsSocket.readyState === WebSocket.OPEN) wsSocket.send(JSON.stringify(obj));
    };
    const sendPcm = (buf) => {
      if (wsSocket.readyState !== WebSocket.OPEN) return;
      // Batch ~100ms of PCM (5×20ms) to avoid flooding the Live WS.
      if (!window.__pcmBatch) window.__pcmBatch = [];
      window.__pcmBatch.push(new Uint8Array(buf));
      let total = 0;
      for (const b of window.__pcmBatch) total += b.byteLength;
      if (total < 3200) return; // < 100ms @ 16k PCM16
      const out = new Uint8Array(total);
      let off = 0;
      for (const b of window.__pcmBatch) {
        out.set(b, off);
        off += b.byteLength;
      }
      window.__pcmBatch = [];
      wsSocket.send(out.buffer);
    };
    gate = new SpeechGate(turnConfig || {}, sendJson, sendPcm);
    workletNode.port.onmessage = (ev) => {
      // The worklet posts a one-off {type:"meta"} before any audio.
      if (ev.data && ev.data.type === "meta") {
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

    // Quick mic sanity: if level stays dead for 2s, warn.
    setTimeout(() => {
      if (framesSent < 10) {
        addSystem(
          "Mic warning: almost no audio frames yet. Check browser mic permission and OS input device."
        );
      } else {
        addSystem(`Mic OK — streamed ${framesSent} frames so far.`);
      }
    }, 2000);
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
      setStatus("loading config…");
      turnConfig = await loadConfig();
      setStatus("creating chat room…");
      await ensureChatRoom();
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws/live/${roomId}`);
      ws.binaryType = "arraybuffer";

      ws.onopen = async () => {
        setStatus("connecting mic…");
        framesSent = 0;
        await startMic(ws);
        setStatus("live — on call", true);
        setTurn("listening");
        btnHangup.disabled = false;
        streamContinuous =
          (turnConfig && turnConfig.stream_mode === "continuous") ||
          (turnConfig && turnConfig.gating_mode === "server_vad");
        addSystem(
          `Call started (room ${roomId}). Mode=${streamContinuous ? "continuous" : "gated (speak, then pause ~2s for a reply)"}. ` +
            `Mic meter should rise above about -45 dB while you talk.`
        );
      };

      ws.onmessage = (ev) => {
        if (typeof ev.data !== "string") {
          playPcm24k(ev.data);
          return;
        }
        const msg = JSON.parse(ev.data);
        if (msg.type === "transcript") {
          appendTranscript(msg.role, msg.text);
        } else if (msg.type === "status") {
          if (msg.turn_config) {
            turnConfig = { ...turnConfig, ...msg.turn_config };
            streamContinuous =
              turnConfig.stream_mode === "continuous" ||
              turnConfig.gating_mode === "server_vad";
            if (gate) gate.cfg = turnConfig;
          }
          setStatus(msg.status === "live" ? "live — on call" : msg.status || "live", true);
          if (msg.turn_state) setTurn(msg.turn_state);
        } else if (msg.type === "turn_state") {
          const labels = {
            listening: "listening for you",
            speaking: "you are speaking",
            paused: "thinking pause — still your turn",
            ended: "waiting for assistant",
            interviewer: "assistant speaking",
          };
          setTurn(labels[msg.turn_state] || msg.turn_state);
        } else if (msg.type === "interviewer_speaking") {
          assistantPlaying = !!msg.active;
          if (msg.active) setTurn("assistant speaking");
        } else if (msg.type === "done") {
          setStatus("call ended");
          setTurn("done");
          addSystem("Call ended.");
          cleanup(false);
        } else if (msg.type === "error") {
          setStatus("error: " + msg.error, false, true);
          addSystem(msg.error);
          if (String(msg.error || "").includes("1011") || String(msg.error || "").includes("keepalive")) {
            addSystem(
              "Gemini Live socket dropped (keepalive). Click Start call for a fresh room — " +
                "server now disables client WS pings which usually causes this."
            );
            roomId = null;
            history.replaceState({}, "", "/");
            btnStart.disabled = false;
            btnHangup.disabled = true;
          }
        }
      };

      ws.onerror = () => setStatus("websocket error", false, true);
      ws.onclose = (ev) => {
        const code = ev && ev.code;
        if (code === 4404 || (roomId && statusEl.textContent.includes("websocket"))) {
          setStatus("room expired — click Start call", false, true);
          roomId = null;
          history.replaceState({}, "", "/");
        } else if (statusEl.textContent.startsWith("live")) {
          setStatus("disconnected");
        }
        btnHangup.disabled = true;
        btnStart.disabled = false;
      };
    } catch (err) {
      setStatus(String(err), false, true);
      btnStart.disabled = false;
    }
  }

  function cleanup(sendFinish) {
    btnHangup.disabled = true;
    btnStart.disabled = false;
    assistantPlaying = false;
    gate = null;
    try {
      workletNode && workletNode.disconnect();
    } catch (_) {}
    try {
      mediaStream && mediaStream.getTracks().forEach((t) => t.stop());
    } catch (_) {}
    try {
      audioCtx && audioCtx.close();
    } catch (_) {}
    workletNode = null;
    mediaStream = null;
    audioCtx = null;
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        if (sendFinish) {
          ws.send(JSON.stringify({ type: "activity_end" }));
          ws.send(JSON.stringify({ type: "finish" }));
        } else {
          ws.close();
        }
      } catch (_) {}
    }
    if (!sendFinish) ws = null;
  }

  btnStart.addEventListener("click", start);
  btnHangup.addEventListener("click", () => {
    setStatus("hanging up…");
    cleanup(true);
  });
})();
