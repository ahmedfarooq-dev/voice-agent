# Client onboarding runbook

Every step to take a new client from "interested" to a working voice agent on their
website and phone number. Follow it in order and tick each box. Skipping steps is how the
bot ends up "acting strange" in front of the client.

Time: about half a day of our work, spread over 3–5 days of waiting on the client.

---

## Phase 0 — Sales handoff (before any technical work)

Agree in writing:

- [ ] **Scope**: website button, phone number, or both.
- [ ] **Who pays for what**. Typical: client pays for the server (~$5–10/month), the phone
      number (~$1.15/month + ~$0.013/min) and API usage (~$0.03/min); we charge setup and
      a monthly fee. Decide whether the API accounts (Deepgram, Gemini, Groq) are **theirs**
      (they own the cost and the data) or **ours** (we bill them). Theirs is cleaner.
- [ ] **Who writes the document**: they fill our template, or we build it from their
      material (brochure, website, price list). The second is a higher setup fee.
- [ ] **Booking**: which Google account's calendar, appointment length, bookable hours,
      what happens after a booking (who calls, how).
- [ ] **What the agent must never do** (quote prices? promise dates? name competitors?).

---

## Phase 1 — Collect from the client

Send them `docs/Client-Intake-Template.docx` and this list. Nothing starts until all
of it is in.

| # | Item | Why | How they get it |
|---|---|---|---|
| 1 | **Filled intake template** (.docx) | The agent's entire knowledge and behaviour | They fill it in Word; anything in [brackets] is guidance and is ignored |
| 2 | **Company name pronunciation**, if not obvious | The agent says it in every greeting | Write it phonetically in the Basics table |
| 3 | **Google account** whose calendar takes bookings | Booking + email invites | They sign in themselves in Phase 4; we never need their password |
| 4 | **Subdomain** for the bot, e.g. `voice.theircompany.com` | HTTPS is mandatory for the microphone; Twilio needs a public address | They add one DNS record we give them (Phase 3) |
| 5 | **Server** (Ubuntu 22.04/24.04, 1–2 vCPU, 2 GB RAM) or permission for us to create one on their card | Runs the bot 24/7 | Hetzner, DigitalOcean, Vultr; any provider |
| 6 | **Twilio account** with a US number (phone clients only) | The phone line | twilio.com → Buy a number → Voice capability. **Upgrade from trial** (add payment) or callers hear a "trial account" message and only verified numbers can call |
| 7 | **API accounts** (if theirs): Deepgram, Google AI Studio (Gemini), Groq | The agent's ears, brain and voice | Free signup each; they give us the keys through a password manager or a call, never email |
| 8 | **Website access**: whoever can paste one line into their site | The button | Their web person or CMS login |
| 9 | **Test callers**: 2–3 people on their side | Real-voice testing in Phase 6 | Names and a time slot |
| 10 | **Call recording decision**: are calls recorded/stored? | US states differ on consent; the greeting may need a notice | Their call, ideally with legal advice; we default to *not* recording |
| 11 | **Notification email address(es)** and a **Brevo account** (free) | Callback messages and bookings are emailed to the team | brevo.com → sign up → verify the sender address they want mail to come from → SMTP & API → create an API key |

---

## Phase 2 — Prepare the document (our side, ~1–2 hours)

- [ ] Put their `.docx` in a fresh project folder's `server/knowledge/` (one folder per client;
      never mix clients).
- [ ] Run `python intake.py --check`. Fix every **FAIL**. Review every **WARN**.
- [ ] Run `python intake.py` and read the output *as a caller would hear it*. Rewrite anything
      that reads like a brochure ("click here", "visit our website", bullet fragments).
- [ ] Confirm every promise in the document maps to something the agent can do:
      book (yes), take a message (yes), transfer a live call (**no**), send email/SMS itself
      (**no**), look up an existing booking (**no**). Rewrite the ones it can't.
- [ ] Prices: either exact figures the agent may say, or a clear "never quote, offer a call"
      rule in "How the agent should behave". Never both.
- [ ] Set the Basics: agent name, greeting, timezone, bookable hours (`Mon-Fri 09:00-17:00`
      format), appointment length and name.
- [ ] Run the text test (`python prompt.py` and a few questions through the LLM, or just talk
      to it locally) for: greeting, one FAQ, one objection, a price question, an off-topic
      question, "are you a real person", a booking, a goodbye.

---

## Phase 3 — Server and HTTPS (our side, ~1 hour)

On the client's Ubuntu server, as root or with sudo:

- [ ] **DNS**: client adds an **A record** `voice` → server's public IP. Wait until
      `ping voice.theircompany.com` resolves.
- [ ] **Firewall / cloud firewall**: allow inbound **TCP 22, 80, 443** and **UDP 1024–65535**.
      The UDP range is for WebRTC audio from the website button; without it the button
      connects but no audio flows.
- [ ] **Install**:
      ```bash
      apt update && apt install -y python3.12 python3.12-venv git
      git clone https://github.com/ahmedfarooq-dev/Voice-agent-.git /opt/voice-agent
      cd /opt/voice-agent && python3.12 -m venv .venv
      .venv/bin/pip install "pipecat-ai[deepgram,groq,runner,silero,webrtc,websocket]>=1.11,<2" tzdata python-docx google-api-python-client google-auth-oauthlib
      ```
- [ ] **Configure**: copy `server/.env.example` to `server/.env` and fill in the client's keys,
      `LLM_ORDER=gemini,groq`, timezone, and the Brevo settings (`BREVO_API_KEY`,
      `NOTIFY_TO`, `NOTIFY_FROM_EMAIL`). Put their `.docx` in `server/knowledge/`.
      Run `.venv/bin/python server/intake.py --check` and `.venv/bin/python server/notify.py`
      on the server; the second must land a test email in the client's inbox.
- [ ] **Run as a service** so it restarts on crash and reboot: `/etc/systemd/system/voice-agent.service`
      ```ini
      [Unit]
      Description=Voice agent
      After=network-online.target

      [Service]
      WorkingDirectory=/opt/voice-agent/server
      ExecStart=/opt/voice-agent/.venv/bin/python bot.py --host 127.0.0.1 --port 7860 --allowed-origins https://www.theircompany.com https://theircompany.com
      Restart=always
      RestartSec=3
      Environment=PYTHONUTF8=1

      [Install]
      WantedBy=multi-user.target
      ```
      `systemctl enable --now voice-agent` then `journalctl -u voice-agent -f` to watch it
      start. `--allowed-origins` limits which websites may use the button; list every domain
      variant the client's site is served from.
- [ ] **HTTPS with Caddy** (gets and renews the certificate automatically):
      ```bash
      apt install -y caddy
      cat > /etc/caddy/Caddyfile <<'EOF'
      voice.theircompany.com {
          reverse_proxy 127.0.0.1:7860
      }
      EOF
      systemctl reload caddy
      ```
- [ ] **Verify**: open `https://voice.theircompany.com/widget/` in a browser. The demo page
      must load with a padlock. Click the button, allow the mic, talk. If it connects but
      stays silent, it's the UDP firewall rule.

---

## Phase 4 — Google Calendar (with the client on a call, ~15 min)

- [ ] In **our** Google Cloud project: Google Auth Platform → **Audience** → add the client's
      Google address as a **Test user**.
- [ ] On our laptop (the server has no browser): `python authorize_google.py`. Share screen;
      the client signs in on **their** account, clicks through "Google hasn't verified this
      app" → Advanced → Continue, and allows calendar access. This creates
      `server/google_token.json` locally.
- [ ] Copy that file to the server: `scp server/google_token.json root@SERVER:/opt/voice-agent/server/`
      and `systemctl restart voice-agent`.
- [ ] On the server: `.venv/bin/python server/check_calendar.py --book`. The client should see a
      test invite arrive and then a cancellation. Confirm the appointment time matches their
      timezone expectation.
- [ ] **Critical**: while the Google app is in "Testing" status, its sign-ins **expire after
      7 days** and bookings silently stop. Before handover, set the app to **In production**
      (Google Auth Platform → Audience → Publish app). The "unverified app" warning remains
      on sign-in, which is fine; the token then no longer expires.
- [ ] If the client's calendar is not their primary one, set `GOOGLE_CALENDAR_ID` in `.env`
      to the calendar's ID (Calendar settings → Integrate calendar).

---

## Phase 5 — Website button and phone number

**Website (client's web person, 5 min)**
- [ ] Paste before `</body>` on every page where the button should appear:
      ```html
      <script src="https://voice.theircompany.com/widget/voice-widget.js" data-label="Talk to us" data-color="#1f4e79"></script>
      ```
- [ ] Their site must itself be **HTTPS** (browsers refuse the microphone otherwise).
- [ ] Check on desktop Chrome, Safari and a phone. The button appears bottom-right; the label
      and colour match their brand.

**Phone (us, in their Twilio account, 5 min)**
- [ ] Twilio Console → **Develop → TwiML Bins → Create**. Name `voice-agent`, content:
      ```xml
      <?xml version="1.0" encoding="UTF-8"?>
      <Response>
        <Connect>
          <Stream url="wss://voice.theircompany.com/ws" />
        </Connect>
      </Response>
      ```
- [ ] **Phone Numbers → Manage → Active numbers → their number → Voice Configuration**:
      "A call comes in" → **TwiML Bin** → select `voice-agent` → **Save**.
- [ ] Call the number from a mobile. The greeting should start within 2 seconds.
- [ ] If the client wants the number to ring a human first and only fall to the agent
      when unanswered, that's a different TwiML (Dial with a timeout, then Connect). Ask.

---

## Phase 6 — Acceptance test (with the client's test callers, ~30 min)

Run this script on **both** website and phone. Everything must pass before handover.

**Testers must wear headphones on the website test.** With open speakers the microphone
hears the agent's own voice, the agent waits for "the caller" to finish, and it looks
frozen. This is not a bug in the agent; phone lines cancel echo and most laptops do too,
but a loud speaker next to a microphone will always reproduce it.

| # | Say | Expect |
|---|---|---|
| 1 | (nothing, just connect) | Greeting names the company and the agent, within 2 s |
| 2 | "What do you do?" | Specific answer from their document, 2–4 sentences |
| 3 | One FAQ from their list | The document's answer, not an invention |
| 4 | A price question | Follows their pricing rule exactly |
| 5 | An objection ("too expensive") | Their scripted response, then a nudge toward booking |
| 6 | "What's the weather?" | Brief "can't check that", steers back; **no** message offer |
| 7 | "Are you a real person?" | Honest, then continues |
| 8 | "I'd like to book" | Asks day → offers up to 3 times → name → email spelled back → phone read back → confirms; invite email arrives; event on the calendar |
| 9 | "Can someone call me back?" | Asks name, then phone, reads it back; message appears in `server/data/messages.jsonl` |
| 10 | A question not in the document | "I don't have that detail", offers a message; **no** invented answer |
| 11 | Interrupt the agent mid-sentence | It stops and listens |
| 12 | "Bye" | Fixed goodbye, then the call ends |

- [ ] Fix any failure by editing the document (most cases) and re-running `intake.py --check`.
      Document changes apply on the next call; no restart.
- [ ] Note the reply delay. Over ~2 seconds consistently means the document is too long or
      the server is far from the US; both are fixable.

---

## Phase 7 — Handover

- [ ] Give the client: the widget snippet, the phone number, where messages land, and the rule
      "edit the Word document and send it to us; changes are live within the hour".
- [ ] Explain what the agent will not do (transfer, send email/SMS, reschedule).
- [ ] **Callback messages and bookings are emailed** to the addresses in `NOTIFY_TO`. Confirm
      with the client that a test email arrived (`python notify.py`) and that they checked
      the spam folder once. A missed callback is the worst outcome.
- [ ] Rotate any API key that was ever sent over chat or email.
- [ ] Record in our notes: server IP, domain, which Google account, Twilio number, key
      ownership, go-live date.

---

## Phase 8 — After go-live

- [ ] Day 1 and day 3: read the logs (`journalctl -u voice-agent --since yesterday`) for
      errors, rate-limit fallbacks (`resting it 30s`) and failed bookings.
- [ ] If Gemini rate limits appear under real traffic, the client enables billing on their
      Google AI Studio project (pay-as-you-go, cents per call). Same for Deepgram when the
      $200 credit runs down (check the console balance monthly).
- [ ] Ask the client for the three most common caller questions after the first week and add
      them to the document.
- [ ] Every document edit: `intake.py --check` first, then deploy.

---

## Known limitations to state up front

- One running bot serves **one company**. Each client gets their own server (or their own
  port and subdomain on a shared one).
- The website button uses public STUN servers. A small share of callers on strict corporate
  networks may not get audio; a TURN relay fixes that and can be added when needed.
- English only in this version.
- No live transfer, no outbound calls, no reading or changing existing bookings.
