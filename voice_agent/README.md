# RNT Steel Detailing — AI cold-calling agent

An outbound voice agent that cold-calls steel fabricators, erectors and GCs,
qualifies them, handles the usual objections, and books a call with a human
estimator. It talks in real time over the phone and is built to sound like a
person rather than an IVR.

---

## How it works

```
  Twilio (phone line)
        │  mu-law audio, both directions, over one websocket
        ▼
  app/main.py ──────────── app/conversation/session.py
                                │
             ┌──────────────────┼──────────────────┐
             ▼                  ▼                  ▼
      Deepgram (STT)      Claude (brain)    ElevenLabs (TTS)
      what they said    what to say next   how it sounds
                                │
                                ▼
                    tools → app/conversation/outcomes.py
                        → CRM records + do-not-call list
```

Audio never leaves 8 kHz mu-law anywhere in the path, so there is no resampling
latency between the caller's ear and the model.

## What makes it sound human

The hard part of a voice agent is not the voice, it is the timing. Four things
do most of the work:

- **Barge-in.** The moment the prospect starts speaking, playback stops, the
  buffered audio in Twilio is flushed, and the half-finished turn is thrown
  away. A bot that keeps talking over you is the fastest way to get hung up on.
- **Backchannels.** A short "mm-hmm" goes out the instant they stop talking,
  while the model is still thinking. Without it there is a dead second on every
  single turn, which reads as a bad line or a script.
- **Sentence-level streaming.** Audio starts after the model's first sentence,
  not its last.
- **Speech-shaped prompting.** `app/brain/prompts.py` forbids lists, markdown,
  corporate vocabulary and three-sentence turns, and `app/brain/humanizer.py`
  scrubs anything that leaks through and adds occasional breath pauses.

## Setup

```bash
cd voice_agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then fill in the keys
```

You need accounts with Twilio (a phone number), Deepgram, ElevenLabs and
Anthropic. For the voice, clone or pick one in ElevenLabs and put its id in
`ELEVENLABS_VOICE_ID` — pick a voice that already sounds conversational; no
amount of prompting fixes a newsreader voice.

## 1. Fill in the company profile first

`config/company.yaml` is what the agent states as fact on live calls. Several
fields are marked `TODO` because the website was unreachable from the build
environment — **every one of those needs a real value before you dial anyone.**
Wrong facts here become wrong claims to a prospect.

`config/script.yaml` holds the openers, discovery questions, objection handling
and closes. Both files are plain YAML; editing the pitch never requires touching
code.

## 2. Rehearse it for free

```bash
python scripts/simulate_call.py
python scripts/simulate_call.py --persona busy
```

A terminal chat against the real prompt and real tools. Use this to tune the
script until the transcripts read the way your best salesperson talks. Only
`ANTHROPIC_API_KEY` is needed.

## 3. Run the server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080
ngrok http 8080          # in development; put the https URL in PUBLIC_BASE_URL
```

## 4. Dial

```bash
# always start here — filters the whole list, dials nobody
python scripts/dial_campaign.py --leads data/leads.sample.csv --dry-run

# live
python scripts/dial_campaign.py --leads data/my_leads.csv --limit 25
```

Lead CSV columns: `first_name,last_name,company,title,phone,email,city,state,timezone,notes,status`.
Only `phone` is required, but `first_name`, `company` and `timezone` make the
calls markedly better. See `data/leads.sample.csv`.

## Results

- `data/calls/*.json` — full transcript, everything learned, and the outcome
  for each call.
- `data/call_summary.csv` — one row per call. Open it in Excel, sort by grade,
  work the hot ones.
- `data/do_not_call.csv` — permanent suppression list.

Leads are graded `hot` / `warm` / `cold` / `disqualified` by the agent during
the call, with a reason.

## Guardrails built in

- **Do-not-call is absolute.** Anyone who asks to be removed is written to
  `data/do_not_call.csv` immediately and no code path will ever dial that
  number again. Numbers are compared digits-only, so reformatting cannot slip
  past it.
- **Local calling hours.** Calls only go out 9am–5pm on weekdays *in the
  prospect's timezone*, not yours.
- **AI disclosure.** The agent identifies itself as an AI assistant, and always
  answers honestly when asked. This is a legal requirement in a growing number
  of US states and is on by default in `config/company.yaml`.
- **No invented facts.** The prompt forbids making up prices, tonnages, client
  names or certifications. Real quotes come from a human estimator.
- **No pressure.** Two closing attempts maximum, then the agent thanks them and
  ends.
- **Hard timeouts.** A call cannot exceed `MAX_CALL_SECONDS`, and dead air ends
  it after `MAX_SILENCE_SECONDS`.

## Before you go live — your side

These are business and legal decisions, not code:

1. **Fill in every `TODO`** in `config/company.yaml`.
2. **Scrub your lead list against the national DNC registry** (US) and any
   provincial/state equivalent. The list in this repo only captures people who
   ask *you* to stop; it is not a substitute for registry scrubbing.
3. **Check call-recording consent law** in the states you dial. `record=True`
   is set in `app/telephony/twilio_handler.py`; two-party-consent states need a
   spoken notice or the recording turned off.
4. **Register your numbers** for STIR/SHAKEN and brand them, or your calls show
   up as "Spam Likely" and none of this matters.
5. **Confirm AI-disclosure requirements** with counsel for your target states.
6. **Listen to the first fifty calls yourself.** The recordings and transcripts
   are there. Tune `config/script.yaml`, not the code.

## Tests

```bash
python -m pytest tests -q
```

Covers the speech scrubber, DNC suppression, calling-window logic and outcome
recording — the parts where a bug costs money or breaks a rule.

## Rough cost per call

At a two-minute average, the four vendors together run roughly $0.15–0.30 per
call, most of it speech synthesis. Verify against current pricing before
budgeting a campaign.
