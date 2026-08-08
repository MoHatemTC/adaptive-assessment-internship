(() => {
  const statusEl = document.getElementById("status");
  const presentingEl = document.getElementById("presenting");
  const reportEl = document.getElementById("report");
  const sessionInfoEl = document.getElementById("sessionInfo");
  const btnStart = document.getElementById("btnStart");
  const targetsEl = document.getElementById("targets");

  let sessionId = null;
  let mediaRecorder = null;
  let recordedChunks = [];
  let recordingStart = 0;

  function setStatus(msg, cls = "muted") {
    statusEl.className = `row ${cls}`;
    statusEl.textContent = msg;
  }

  function htmlEscape(s) {
    return String(s || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  async function api(path, options = {}) {
    const res = await fetch(path, options);
    const text = await res.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch (_) {}
    if (!res.ok) throw new Error(data.detail || data.error || `${res.status} ${res.statusText}`);
    return data;
  }

  function parseTargets() {
    const raw = (targetsEl.value || "").trim();
    if (!raw) return null;
    return raw.split(",").map((x) => x.trim()).filter(Boolean);
  }

  async function startSession() {
    setStatus("Starting session…");
    const body = { target_variables: parseTargets(), use_llm: true, seed: 0 };
    const data = await api("/api/cat/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    sessionId = data.session_id;
    sessionInfoEl.textContent = ` Session: ${sessionId}`;
    renderState(data);
    setStatus("Session started.", "ok");
  }

  async function refreshState() {
    if (!sessionId) return;
    const data = await api(`/api/cat/sessions/${sessionId}`);
    renderState(data);
  }

  function renderState(data) {
    if (data.stop) {
      presentingEl.style.display = "none";
      reportEl.style.display = "block";
      const report = data.report || {};
      reportEl.innerHTML = `
        <h3>Assessment Finished</h3>
        <div class="row"><b>Stop reason:</b> ${htmlEscape(data.stop_reason)}</div>
        <div class="row"><b>Items administered:</b> ${htmlEscape(data.items_administered)}</div>
        <pre>${htmlEscape(JSON.stringify(report, null, 2))}</pre>
      `;
      return;
    }

    reportEl.style.display = "none";
    presentingEl.style.display = "block";
    const presenting = data.presenting;
    if (!presenting) {
      presentingEl.innerHTML = "<div class='warn'>No presenting item yet.</div>";
      return;
    }
    const item = presenting.item;
    const header = `
      <div class="row"><b>Variable:</b> ${htmlEscape(presenting.variable)} · <b>Criterion:</b> ${htmlEscape(presenting.criterion)}</div>
      <div class="row"><b>Item:</b> ${htmlEscape(item.item_id)} · <b>Modality:</b> ${htmlEscape(item.modality)}</div>
      <div class="row muted"><b>Open variables:</b> ${(data.open_variables || []).join(", ")}</div>
    `;

    if (item.modality === "mcq") {
      const opts = (item.options || [])
        .map((o, i) => `<label class="row"><input type="radio" name="mcqOpt" value="${i}"> ${htmlEscape(o)}</label>`)
        .join("");
      presentingEl.innerHTML = `
        ${header}
        <div class="row"><b>Question</b></div>
        <div class="row">${htmlEscape(item.stem || "")}</div>
        <div class="row">${opts}</div>
        <button id="btnSubmitMcq">Submit MCQ</button>
        ${debugBlock(data)}
      `;
      document.getElementById("btnSubmitMcq").onclick = submitMcq;
      return;
    }

    if (item.modality === "code") {
      const starter = item.starter_code || `def ${item.function_name || "solve"}():\n    pass\n`;
      presentingEl.innerHTML = `
        ${header}
        <div class="row"><b>Prompt</b></div>
        <div class="row">${htmlEscape(item.prompt || "")}</div>
        <div class="row muted">Language: ${htmlEscape(item.language || "python")} · Function: ${htmlEscape(item.function_name || "solve")}</div>
        <div class="row"><textarea id="codeInput">${htmlEscape(starter)}</textarea></div>
        <button id="btnSubmitCode">Submit Code</button>
        ${debugBlock(data)}
      `;
      document.getElementById("btnSubmitCode").onclick = submitCode;
      return;
    }

    if (item.modality === "open") {
      presentingEl.innerHTML = `
        ${header}
        <div class="row"><b>Prompt</b></div>
        <div class="row">${htmlEscape(item.question || "")}</div>
        <div class="row muted">Answer format: voice preferred; transcript fallback is available.</div>
        <div class="row">
          <button id="btnStartRec">Start Recording</button>
          <button id="btnStopRec" disabled>Stop Recording</button>
          <span id="recStatus" class="muted"></span>
        </div>
        <div class="row">
          <label>Transcript fallback (optional)</label>
          <textarea id="openTranscript" style="min-height:90px"></textarea>
        </div>
        <button id="btnSubmitOpen">Submit Open Answer</button>
        ${debugBlock(data)}
      `;
      document.getElementById("btnStartRec").onclick = startRecording;
      document.getElementById("btnStopRec").onclick = stopRecording;
      document.getElementById("btnSubmitOpen").onclick = submitOpen;
      return;
    }

    presentingEl.innerHTML = `${header}<div class="err">Unsupported modality: ${htmlEscape(item.modality)}</div>`;
  }

  function debugBlock(data) {
    if (!data.last_graded) return "";
    return `
      <details class="row">
        <summary>Last graded debug</summary>
        <pre>${htmlEscape(JSON.stringify({ last_graded: data.last_graded }, null, 2))}</pre>
      </details>
    `;
  }

  async function submitMcq() {
    const selected = document.querySelector("input[name='mcqOpt']:checked");
    if (!selected) return setStatus("Choose an option first.", "warn");
    setStatus("Submitting MCQ…");
    const payload = { type: "mcq", chosen_index: Number(selected.value), use_llm: true, seed: 0 };
    const data = await api(`/api/cat/sessions/${sessionId}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderState(data);
    setStatus("MCQ submitted.", "ok");
  }

  async function submitCode() {
    const code = document.getElementById("codeInput").value || "";
    if (!code.trim()) return setStatus("Code cannot be empty.", "warn");
    setStatus("Submitting code…");
    const payload = { type: "code", code, use_llm: true, seed: 0 };
    const data = await api(`/api/cat/sessions/${sessionId}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderState(data);
    setStatus("Code submitted.", "ok");
  }

  async function startRecording() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return setStatus("This browser does not support microphone recording.", "err");
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recordedChunks = [];
      mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
      mediaRecorder.ondataavailable = (ev) => {
        if (ev.data && ev.data.size > 0) recordedChunks.push(ev.data);
      };
      mediaRecorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
      };
      mediaRecorder.start();
      recordingStart = Date.now();
      document.getElementById("btnStartRec").disabled = true;
      document.getElementById("btnStopRec").disabled = false;
      document.getElementById("recStatus").textContent = " recording…";
      setStatus("Recording started.", "ok");
    } catch (e) {
      setStatus(`Microphone error: ${e}`, "err");
    }
  }

  function stopRecording() {
    if (!mediaRecorder) return;
    mediaRecorder.stop();
    document.getElementById("btnStartRec").disabled = false;
    document.getElementById("btnStopRec").disabled = true;
    const secs = ((Date.now() - recordingStart) / 1000).toFixed(1);
    document.getElementById("recStatus").textContent = ` recorded ${secs}s`;
    setStatus("Recording stopped.", "ok");
  }

  async function submitOpen() {
    const transcript = (document.getElementById("openTranscript").value || "").trim();
    const hasAudio = recordedChunks.length > 0;
    if (!hasAudio && !transcript) return setStatus("Record audio or provide transcript text.", "warn");
    setStatus("Submitting open answer…");

    const form = new FormData();
    form.append("type", "open");
    form.append("use_llm", "true");
    form.append("seed", "0");
    if (transcript) form.append("transcript", transcript);
    if (hasAudio) {
      const blob = new Blob(recordedChunks, { type: "audio/webm" });
      form.append("audio", blob, "answer.webm");
    }

    const data = await api(`/api/cat/sessions/${sessionId}/answer`, {
      method: "POST",
      body: form,
    });
    recordedChunks = [];
    renderState(data);
    setStatus("Open answer submitted.", "ok");
  }

  btnStart.onclick = async () => {
    try {
      await startSession();
    } catch (e) {
      setStatus(`Start failed: ${e}`, "err");
    }
  };

  setInterval(async () => {
    if (!sessionId) return;
    try {
      await refreshState();
    } catch (e) {
      setStatus(`Refresh failed: ${e}`, "warn");
    }
  }, 5000);
})();
