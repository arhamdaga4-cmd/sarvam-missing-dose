# Missing Dose — a daily medicine check-in call, in the patient's own language

A voice agent that phones patients at home, asks whether they took today's
medicines, listens for symptoms, reminds them about their next appointment, and
escalates to a human coordinator when something is wrong.

Built on Sarvam (Saaras v4 for speech-to-text, Bulbul v3 for text-to-speech,
Sarvam-105B Conversations for the dialogue) and Exotel AgentStream for the phone
leg. Only **one** Sarvam key is needed — the `sk_...` subscription key from
[dashboard.sarvam.ai](https://dashboard.sarvam.ai). You do **not** need the
`X-API-Key` from the hosted voice-agent product, and you do not need access to
`console.sarvam.ai` / `apps.sarvam.ai` for anything in this repository.

```
   Exotel dials the patient
            │
            ▼
   wss://your-server/stream        ◄── Exotel AgentStream (base64 PCM16, 8 kHz)
            │
      companion/telephony/bridge.py
            │
            ├── companion/stt.py   ──► POST /speech-to-text   (saaras:v4)
            ├── companion/conversation.py ──► POST /v1/chat/completions
            │                                   (sarvam-105b-conversations)
            ├── companion/tts.py   ──► POST /text-to-speech   (bulbul:v3)
            │
            ▼
   audio back to the patient
            │
            ▼
   report → companion/escalation.py → coordinator (console / webhook / email)
                    │
                    ▼
              SQLite (check-ins, transcripts, escalations)
```

## What actually works

Everything below runs and is covered by tests (`pytest`, 46 tests, no network
needed):

| Piece | Status | Notes |
| --- | --- | --- |
| Dialogue engine (4 topics → closing) | working | `companion/conversation.py`, language-locked per patient |
| Structured report extraction | working | separate LLM call, strict JSON, `parse_error` instead of invented data |
| Escalation rules | working | urgent / soon / info, keyword + severity + adherence rules |
| Persistent storage | working | SQLite: patients, check-ins, transcripts, escalations |
| Notifications | working | console + JSONL always; webhook + SMTP when configured |
| Text-mode harness | working | `python run_checkup.py P001` |
| Offline engine | working | no API key needed; deterministic, clearly labelled |
| Exotel outbound call | **written, not verified against a live account** | see *Telephony caveats* |
| Live WebSocket bridge | **written, tested at protocol level** | real audio path not exercised here |
| Daily scheduler | working | due-logic tested; retries failed calls |
| HTTP API + webhooks | working | `/health`, `/api/*`, `/exotel/status`, `/exotel/stream-url` |

## Quickstart (no API key, 30 seconds)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python run_checkup.py            # all three demo patients
python run_checkup.py P002       # one patient
python run_checkup.py P001 --json
```

You will see a full call transcript in Hindi/Tamil/Gujarati, a report, and an
escalation decision. This uses `companion/offline_client.py`, which produces
scripted agent lines and a keyword-based report — it is a demo harness, not a
model, and every report it produces is tagged `"engine": "offline-heuristic"`.

## Quickstart (real Sarvam calls, text mode)

```bash
cp .env.example .env
# put your sk_... key in .env
python run_checkup.py P001
```

Now the agent lines come from `sarvam-105b-conversations` and the report from a
second extraction call. Add `--audio` to save the opening line as a WAV you can
play on your laptop.

## Making real phone calls

You need four things: a Sarvam key, an Exotel account with an ExoPhone, a
public HTTPS/WSS URL, and the patient's real phone number in `patients.json`.

1. **Exotel credentials** — dashboard → Settings → API credentials. Put the
   Account SID, API key, API token and your ExoPhone in `.env` as
   `EXOTEL_ACCOUNT_SID`, `EXOTEL_API_KEY`, `EXOTEL_API_TOKEN`, `EXOTEL_CALLER_ID`.
2. **A public URL** — `ngrok http 8000`, or a VPS. Set `PUBLIC_BASE_URL` and
   `PUBLIC_WS_URL` (`wss://<host>/stream`).
3. **Attach the stream to the call.** Two documented options:
   - `EXOTEL_BRIDGE_NUMBER` — the number Exotel dials first (a softphone or IVR
     line that answers immediately). We pass `StreamUrl` and
     `StreamBegin=at Leg2Connect` to the Connect Two Numbers API.
   - `EXOTEL_FLOW_URL` — a call flow containing a **Voicebot applet** whose URL
     is `wss://<host>/stream?sample-rate=8000`. This is the cleaner option for
     unattended outbound bot calls, since no bridge leg has to answer.
4. **Run it**

```bash
python -m uvicorn companion.server:app --host 0.0.0.0 --port 8000
python -m companion.scheduler --once --patient P001     # dial one patient
python -m companion.scheduler --dry-run                 # simulate instead
python -m companion.scheduler                           # run forever, daily 09:30 IST
```

Exotel will open `wss://<host>/stream?patient_id=P001`, send `connected`,
`start`, then `media` every ~100 ms. The bridge buffers audio until it hears
~700 ms of silence, transcribes the utterance, runs one dialogue turn, speaks
the reply back, and on `stop` writes the report and fires escalations.

### Escalation back into the call flow

Put a **Passthru applet** after the Voicebot applet, pointing at
`POST /exotel/status`. It answers with `{"escalate": true|false, "level": ...}`,
so the flow can route to a human agent, or hang up, when the check-in needed
follow-up. `/exotel/stream-url` exists for the dynamic-URL form of the applet.

## Configuration

Everything is environment-driven; see `.env.example` for the full list. The
ones that matter most:

| Variable | Default | Meaning |
| --- | --- | --- |
| `SARVAM_API_KEY` | — | `sk_...` key from dashboard.sarvam.ai (the only Sarvam key needed) |
| `SARVAM_CHAT_MODEL` | `sarvam-105b-conversations` | dialogue + report extraction |
| `SARVAM_TTS_MODEL` / `SARVAM_TTS_SPEAKER` | `bulbul:v3` / `anushka` | voice; per-patient `voice` overrides |
| `SARVAM_STT_MODEL` | `saaras:v4` | speech recognition |
| `TELEPHONY_SAMPLE_RATE` | `8000` | must match what Exotel streams |
| `VAD_RMS_THRESHOLD` | `450` | energy gate; raise it for noisy lines |
| `SCHEDULE_TIME` / `SCHEDULE_TIMEZONE` | `09:30` / `Asia/Kolkata` | daily call window |
| `COORDINATOR_WEBHOOK_URL` | — | Slack/Sheets/anything that takes JSON |
| `SMTP_*`, `ALERT_EMAIL_TO` | — | email alerts for `soon`/`urgent` |

## HTTP surface

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | liveness + whether the Sarvam key is configured |
| GET | `/api/patients` | active patients |
| GET | `/api/checkins/{patient_id}` | check-in history with reports |
| GET | `/api/escalations` | recent escalations and delivery status |
| POST | `/exotel/status` | call-status webhook; also the Passthru escalation hook |
| POST | `/exotel/stream-url` | returns `{"url": "wss://..."}` for a dynamic applet |
| WS | `/stream` | AgentStream audio bridge |

## How escalation is decided

`companion/escalation.py` is deliberately rule-based, so a coordinator can read
and argue with it:

- **urgent** — a chest-pain/breathlessness/bleeding/fainting keyword in any
  supported language, a symptom the model graded `severe`, or an entry in
  `urgent_flags`.
- **soon** — two or more missed doses, "didn't take medicines today", a declined
  appointment, an unparseable report, or three days with no completed check-in.
- **info** — one missed dose, side effects, transport help needed.

Levels never downgrade: once a rule fires urgent, later milder findings cannot
pull it back down. Every escalation stores which channels actually delivered.

## Tests

```bash
python -m pytest -q          # 46 tests, fully offline
```

Coverage worth knowing about: Exotel chunk-size constraints (3.2–100 KB,
multiple of 320), resampling 22.05 kHz → 8 kHz, WAV downmix, topic ordering,
JSON extraction tolerance, escalation thresholds, barge-in `clear`, idempotent
finalisation, and the FastAPI surface via `TestClient`.

## Layout

```
run_checkup.py            CLI: run a check-in in text mode
chat.py                   one-shot LLM sanity check
patients.json             demo patients (Hindi / Tamil / Gujarati)
companion/
  config.py               env-driven settings
  sarvam_client.py        chat / TTS / STT with retries and clear 403 handling
  offline_client.py       key-free deterministic stand-in
  conversation.py         4-topic dialogue + report extraction
  escalation.py           rules -> urgent / soon / info
  notifier.py             console, JSONL, webhook, email
  patient_db.py           SQLite storage
  tts.py / stt.py         telephony-ready audio conversion
  audio_utils.py          resample, downmix, chunk for Exotel
  call_simulator.py       scripted full call
  scheduler.py            daily cadence + retries
  server.py               FastAPI app
  telephony/exotel.py     outbound call API
  telephony/bridge.py     the live audio loop
tests/                    pytest suite
```

## Telephony caveats — read before you demo this

These are real limitations, not disclaimers:

1. **The Exotel leg is unverified.** The request shape follows Exotel's
   published Connect Two Numbers and AgentStream docs, but I could not place a
   real call from this environment. Confirm with one test call before pointing
   it at a patient.
2. **Latency is turn-based, not streaming.** Each utterance goes through one
   REST transcription (~0.5–1.5 s), one LLM turn, then one TTS request. Expect
   roughly 2–4 s between the patient finishing and the agent replying. Sarvam's
   realtime WebSocket STT (`/speech-to-text-realtime/ws`, `saaras:v3-realtime`)
   and the TTS WebSocket are the upgrade path for sub-second turns.
3. **The VAD is an RMS gate**, not a model. It will cut people off in noisy
   rooms and on speakerphone. Tune `VAD_RMS_THRESHOLD`, or swap in a proper VAD.
4. **No auth on the HTTP API.** `/api/*` exposes patient names and transcripts.
   Put it behind a private network or add auth before it leaves your laptop.
5. **PHI is stored in plain SQLite** in `data/`. Encrypt at rest and set a
   retention policy before using real patients.
6. **No consent capture.** A real deployment needs the patient to have agreed
   to automated calls, and a way to opt out (DTMF 9 is a common pattern — the
   `dtmf` event is already received and logged).
7. **One language per patient**, fixed at call time. Code-mixed speech
   (Hindi + English) is handled by Saaras but the dialogue stays in one language.

## References

- Chat Completion — https://docs.sarvam.ai/api/api-guides-tutorials/chat-completion/overview
- Saaras (STT) — https://docs.sarvam.ai/api/getting-started/models/saaras
- Bulbul (TTS) — https://docs.sarvam.ai/api/getting-started/models/bulbul
- Authentication (`api-subscription-key`) — https://docs.sarvam.ai/api-reference/authentication
- Exotel AgentStream protocol — https://developer.exotel.com/docs/agentstream/websocket-protocol
- Exotel Connect Two Numbers — https://developer.exotel.com/docs/voice-v1/api-reference/connect-two-numbers

Not affiliated with Sarvam AI or Exotel. Demo data is fictional.
