<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Gemini Live — voice chat</title>
  <style>
    :root {
      --bg: #0e1218;
      --panel: #171e27;
      --text: #e8eef4;
      --muted: #8b9aab;
      --accent: #2f7cf6;
      --good: #3ecf8e;
      --danger: #e35d6a;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background: radial-gradient(900px 500px at 15% -5%, #1a2838, var(--bg));
      color: var(--text);
      min-height: 100vh;
      padding: 1.5rem;
    }
    h1 { font-size: 1.35rem; margin: 0 0 .35rem; font-weight: 650; }
    .muted { color: var(--muted); font-size: .92rem; max-width: 42rem; line-height: 1.45; }
    .row { display: flex; gap: .75rem; flex-wrap: wrap; align-items: center; margin: 1rem 0; }
    button {
      background: var(--accent); color: white; border: 0; border-radius: 10px;
      padding: .7rem 1.15rem; font-weight: 650; cursor: pointer; font-size: .95rem;
    }
    button.secondary { background: #2a3542; }
    button.danger { background: var(--danger); }
    button:disabled { opacity: .45; cursor: not-allowed; }
    .status, .turn {
      display: inline-flex; gap: .5rem; align-items: center;
      padding: .4rem .75rem; border-radius: 999px; background: var(--panel);
      font-size: .85rem;
    }
    .dot { width: .55rem; height: .55rem; border-radius: 50%; background: var(--muted); }
    .dot.live { background: var(--good); box-shadow: 0 0 0 3px rgba(62,207,142,.2); }
    .dot.error { background: var(--danger); }
    #log {
      background: var(--panel); border-radius: 14px; padding: 1rem 1.1rem;
      height: min(55vh, 480px); overflow: auto; font-size: .95rem; line-height: 1.5;
    }
    .line { margin: 0 0 .7rem; }
    .who {
      color: var(--muted); font-size: .72rem; text-transform: uppercase;
      letter-spacing: .05em; margin-bottom: .15rem;
    }
    .hint {
      margin-top: 1rem; padding: .85rem 1rem; border-radius: 12px;
      background: rgba(47,124,246,.08); border: 1px solid rgba(47,124,246,.22);
      font-size: .88rem; color: var(--muted); max-width: 42rem;
    }
    code { color: #9ec1ff; font-size: .85em; }
  </style>
</head>
<body>
  <h1>Gemini Live voice chat</h1>
  <p class="muted">
    Realtime duplex WebSocket call — your mic ↔ Gemini Live ↔ speakers.
    Talk naturally; short pauses are thinking time, longer silence lets the model reply.
  </p>
  <div class="row">
    <span class="status"><span id="dot" class="dot"></span><span id="status">idle</span></span>
    <span class="turn">turn: <strong id="turnState">idle</strong></span>
    <span class="turn" title="Mic input level">mic: <strong id="micLevel">—</strong>
      <span id="micBar" style="display:inline-block;width:88px;height:8px;background:#2a3542;border-radius:4px;vertical-align:middle;margin-left:.35rem;overflow:hidden">
        <span id="micFill" style="display:block;height:100%;width:0;background:#3ecf8e"></span>
      </span>
    </span>
    <button id="btnStart">Start call</button>
    <button id="btnHangup" class="danger" disabled>Hang up</button>
  </div>
  <div id="log"></div>
  <div class="hint">
    Open this page at <code>http://127.0.0.1:8765/</code>. Uses
    <code>ws://127.0.0.1:8765/ws/live/&lt;room&gt;</code>. Assessment UI is at
    <code>/interview</code>.
  </div>
  <script src="/static/chat.js?v=3"></script>
</body>
</html>
