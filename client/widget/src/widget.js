/**
 * "Talk to us" voice widget.
 *
 * Usage on any website (one line):
 *   <script src="https://BOT-HOST/widget/voice-widget.js" data-label="Talk to us"></script>
 *
 * Optional attributes on the script tag:
 *   data-server="https://BOT-HOST"   bot server (defaults to where this script was loaded from)
 *   data-label="Talk to us"          button text
 *   data-color="#1f4e79"             brand colour
 *   data-position="right"            "right" or "left"
 *
 * Renders inside a shadow DOM so the host site's CSS can't break it and vice versa.
 */
import { PipecatClient } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";

const script = document.currentScript;
const cfg = {
  server: (script?.dataset.server || new URL(script?.src || location.href).origin).replace(/\/$/, ""),
  label: script?.dataset.label || "Talk to us",
  color: script?.dataset.color || "#1f4e79",
  position: script?.dataset.position === "left" ? "left" : "right",
};

const MIC_ICON =
  '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 17v4M8 21h8"/></svg>';

const STYLE = `
  :host { all: initial; }
  * { box-sizing: border-box; font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
  .wrap { position: fixed; bottom: 20px; ${cfg.position}: 20px; z-index: 2147483000; display: flex; flex-direction: column; align-items: flex-end; gap: 10px; }
  .btn { display: inline-flex; align-items: center; gap: 8px; background: ${cfg.color}; color: #fff; border: 0; border-radius: 999px; padding: 12px 18px; font-size: 15px; font-weight: 600; cursor: pointer; box-shadow: 0 6px 20px rgba(0,0,0,.2); transition: transform .12s, filter .12s; }
  .btn:hover { filter: brightness(1.08); transform: translateY(-1px); }
  .btn:disabled { opacity: .7; cursor: default; transform: none; }
  .panel { display: none; background: #fff; color: #1a1a1a; border-radius: 14px; padding: 14px 16px; width: 260px; box-shadow: 0 10px 30px rgba(0,0,0,.22); font-size: 14px; }
  .panel.open { display: block; }
  .row { display: flex; align-items: center; gap: 10px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: #bbb; flex: none; }
  .dot.connecting { background: #e0a800; animation: pulse 1s infinite; }
  .dot.listening { background: #2e9e5b; }
  .dot.speaking { background: ${cfg.color}; animation: pulse .8s infinite; }
  .dot.error { background: #c0392b; }
  @keyframes pulse { 0%,100% { transform: scale(1); opacity: 1 } 50% { transform: scale(1.5); opacity: .55 } }
  .status { flex: 1; }
  .hint { color: #666; font-size: 12px; margin-top: 8px; }
  .end { margin-top: 12px; width: 100%; background: #f2f2f2; color: #1a1a1a; border: 0; border-radius: 8px; padding: 9px; font-size: 13px; font-weight: 600; cursor: pointer; }
  .end:hover { background: #e6e6e6; }
`;

class VoiceWidget {
  constructor() {
    this.client = null;
    this.audio = null;
    this.active = false;
    this.mount();
  }

  mount() {
    const host = document.createElement("div");
    host.id = "voice-widget";
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>${STYLE}</style>
      <div class="wrap">
        <div class="panel">
          <div class="row"><span class="dot"></span><span class="status">Connecting…</span></div>
          <div class="hint">Allow microphone access when your browser asks.</div>
          <button class="end">End conversation</button>
        </div>
        <button class="btn">${MIC_ICON}<span>${cfg.label}</span></button>
      </div>`;
    document.body.appendChild(host);

    this.panel = root.querySelector(".panel");
    this.dot = root.querySelector(".dot");
    this.statusEl = root.querySelector(".status");
    this.hintEl = root.querySelector(".hint");
    this.btn = root.querySelector(".btn");
    this.btnLabel = root.querySelector(".btn span");
    root.querySelector(".end").addEventListener("click", () => this.stop());
    this.btn.addEventListener("click", () => (this.active ? this.stop() : this.start()));
  }

  setStatus(kind, text, hint = "") {
    this.dot.className = `dot ${kind}`;
    this.statusEl.textContent = text;
    this.hintEl.textContent = hint;
    this.hintEl.style.display = hint ? "block" : "none";
  }

  async start() {
    this.active = true;
    this.panel.classList.add("open");
    this.btn.disabled = true;
    this.btnLabel.textContent = "Connecting…";
    this.setStatus("connecting", "Connecting…", "Allow microphone access when your browser asks.");

    try {
      this.client = new PipecatClient({
        transport: new SmallWebRTCTransport(),
        enableMic: true,
        enableCam: false,
        callbacks: {
          onBotReady: () =>
            this.setStatus("listening", "Listening… go ahead and speak", "Headphones give the best experience."),
          onBotStartedSpeaking: () => this.setStatus("speaking", "Speaking… (you can interrupt)"),
          onBotStoppedSpeaking: () => this.setStatus("listening", "Listening…"),
          onUserStartedSpeaking: () => this.setStatus("listening", "Hearing you…"),
          onTrackStarted: (track, participant) => {
            if (!participant?.local && track.kind === "audio") this.playTrack(track);
          },
          onDisconnected: () => this.reset(),
          onError: (e) => {
            console.error("[voice-widget]", e);
            this.setStatus("error", "Something went wrong. Please try again.");
          },
        },
      });

      await this.client.startBotAndConnect({
        endpoint: `${cfg.server}/start`,
        requestData: { transport: "webrtc", enableDefaultIceServers: true },
      });

      this.btn.disabled = false;
      this.btnLabel.textContent = "End";
    } catch (e) {
      console.error("[voice-widget] connect failed:", e);
      const denied = /permission|notallowed|denied/i.test(String(e?.message || e?.name || e));
      this.setStatus(
        "error",
        denied ? "Microphone access was blocked." : "Couldn't connect. Please try again.",
        denied ? "Allow the microphone in your browser's site settings, then try again." : ""
      );
      this.btn.disabled = false;
      this.btnLabel.textContent = cfg.label;
      this.active = false;
      await this.client?.disconnect().catch(() => {});
      this.client = null;
    }
  }

  playTrack(track) {
    if (!this.audio) {
      this.audio = document.createElement("audio");
      this.audio.autoplay = true;
      document.body.appendChild(this.audio);
    }
    this.audio.srcObject = new MediaStream([track]);
  }

  async stop() {
    if (this.client) await this.client.disconnect().catch(() => {});
    this.reset();
  }

  reset() {
    this.active = false;
    this.client = null;
    if (this.audio) {
      this.audio.srcObject = null;
      this.audio.remove();
      this.audio = null;
    }
    this.panel.classList.remove("open");
    this.btn.disabled = false;
    this.btnLabel.textContent = cfg.label;
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => new VoiceWidget());
} else {
  new VoiceWidget();
}
