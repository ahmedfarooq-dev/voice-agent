# Voice Agent

A company voice assistant that answers questions from a client-provided document, handles
objections, books appointments on Google Calendar and takes callback messages. It runs on
the website (browser microphone) and, later, on a phone number (Twilio).

Built with [Pipecat](https://docs.pipecat.ai/) as a cascade pipeline:

```
mic/phone → Silero VAD + Smart Turn → Deepgram Nova-3 (speech-to-text) → LLM (+ tools) → Deepgram Aura-2 (text-to-speech) → speaker/phone
```

## How a new client is set up

Full step-by-step runbook, including server, HTTPS, Google Calendar, Twilio and the
acceptance test: [docs/ONBOARDING.md](docs/ONBOARDING.md). Short version:

1. Give the client `docs/Client-Intake-Template.docx`. They fill it in Word.
   Anything in [square brackets] is guidance and is ignored by the agent.
2. Save the filled file into `server/knowledge/`. Remove any old client's file.
3. Restart the bot. The "Basics" table sets the company name, agent name, timezone,
   bookable hours, appointment length and greeting; the rest becomes the agent's knowledge
   and behaviour rules.
4. Run the pre-flight check: `python intake.py --check`. It fails on anything that would
   break the agent (missing company name, unsupported file, bad timezone or hours) and
   warns about empty sections, oversized content and text that shouldn't be read aloud.
5. Preview what the agent will be told: `python intake.py` (parsed document) or
   `python prompt.py` (full system prompt).

`docs/make_template.py` regenerates the blank template, and `--codainer` writes the filled
example for Codainer into `server/knowledge/`.

## Project structure

```
voice-agent/
├── docs/
│   ├── Client-Intake-Template.docx   # give this to the client
│   ├── make_template.py              # generates the template (and the Codainer example)
│   └── codainer-voice-agent-spec.html # merged spec / sign-off document for Codainer
├── server/
│   ├── bot.py              # Pipeline wiring: transport, STT, LLM, TTS, greeting
│   ├── intake.py           # Reads the client's .docx (and .md/.txt) from knowledge/
│   ├── prompt.py           # Builds the system instruction from the intake document
│   ├── llm.py              # LLM with provider failover (Gemini <-> Groq)
│   ├── tools.py            # Functions the LLM can call: availability, booking, messages, end call
│   ├── booking.py          # Google Calendar: free slots + create event with invite
│   ├── authorize_google.py # One-time Google OAuth (steps inside the file)
│   ├── knowledge/          # The client's filled intake document lives here
│   ├── data/messages.jsonl # Saved callback messages (created at runtime, git-ignored)
│   ├── .env.example        # All settings and API keys
│   └── pyproject.toml
└── README.md
```

## Quick start (Windows)

1. **Get free API keys**
   - Deepgram: https://console.deepgram.com (free $200 credit, used for both listening and speaking)
   - Gemini: https://aistudio.google.com/apikey (free tier; primary LLM)
   - Groq: https://console.groq.com/keys (free tier; fallback LLM)
2. **Configure**: copy `server/.env.example` to `server/.env` and fill in the keys.
3. **Add the client document** to `server/knowledge/` (see above).
4. **Run**, from the project root:

   ```powershell
   .venv\Scripts\activate
   cd server
   python bot.py
   ```

5. Open http://localhost:7860, click **Connect**, allow the microphone and talk.

First-time setup of the environment (already done on this machine):

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install "pipecat-ai[deepgram,groq,runner,silero,webrtc,websocket]>=1.11,<2" tzdata python-docx google-api-python-client google-auth-oauthlib
```

## Booking (Google Calendar)

Bookings are created on the calendar owner's own Google account, so Google emails the
invite to the caller. One-time setup, about 10 minutes:

1. https://console.cloud.google.com → create a project → enable **Google Calendar API**.
2. OAuth consent screen → External → add the calendar owner's email as a **test user**.
3. Credentials → Create → **OAuth client ID** → Desktop app → download the JSON and save it
   as `server/google_credentials.json`.
4. `python authorize_google.py` → sign in as the calendar owner in the browser. This saves
   `server/google_token.json`.

Both files are git-ignored. Without them the agent takes a callback message instead of
booking (saved to `server/data/messages.jsonl`). Bookable hours, appointment length and
title come from the intake document's Basics table, falling back to `.env`.

## Website widget ("Talk to us" button)

`client/widget/` builds a single file, `dist/voice-widget.js`, which the bot serves at
`/widget/voice-widget.js` together with a demo page at http://localhost:7860/widget/.

The client adds it to their website with one line before `</body>`:

```html
<script src="https://BOT-HOST/widget/voice-widget.js" data-label="Talk to us" data-color="#1f4e79"></script>
```

Optional attributes: `data-label`, `data-color`, `data-position="left"`, and
`data-server` if the script is hosted somewhere other than the bot. Browsers only allow
microphone access on HTTPS pages (and on localhost), so the bot must be behind HTTPS in
production.

Rebuild after editing `client/widget/src/widget.js`:

```powershell
cd client\widget
npm install     # first time only
npm run build
```

## Phone number (later)

The same bot answers phone calls; only the connection changes.

1. The client buys a US number in [Twilio](https://console.twilio.com).
2. Expose the bot publicly. Locally: `ngrok http 7860`. In production: the server's HTTPS domain.
3. In Twilio, create a **TwiML Bin** (TwiML Bins → My TwiML Bins → +):

   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <Response>
     <Connect>
       <Stream url="wss://YOUR-HOST/ws" />
     </Connect>
   </Response>
   ```

4. Phone Numbers → Active Numbers → your number → "A call comes in" → **TwiML Bin** → select it → Save.
5. Add `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` to `.env` and run `python bot.py -t twilio -x YOUR-HOST`.

## Tuning notes

- **Latency**: keep the client document short (under about 3,000 words). Every reply
  re-sends the whole prompt.
- **Free-tier limits**: Groq allows 8K tokens/minute and does not discount the repeated
  prompt, so it manages about two exchanges a minute with a full document. That is why
  `LLM_ORDER=gemini,groq`. Check https://aistudio.google.com/rate-limit for Gemini's limits.
- **Turn-taking**: `VAD_STOP_SECS` in `.env` is the silence before the agent assumes you
  finished. Smart Turn (a local model) then judges whether you were pausing or done.
- **Document changes** apply on the next conversation. No restart needed.

## Learn more

- [Pipecat documentation](https://docs.pipecat.ai/)
- [Google Calendar API](https://developers.google.com/calendar/api/v3/reference)
