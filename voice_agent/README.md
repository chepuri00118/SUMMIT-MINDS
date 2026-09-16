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

## Two ways to run it

The agent's brain, ears and voice are each swappable, so you can run the whole
thing with no accounts at all while you tune the pitch, then switch to the
hosted services when you are ready to dial real numbers.

| | Fully local | Hosted (for real calls) |
|---|---|---|
| Brain | Ollama on your machine | Claude |
| Ears | faster-whisper | Deepgram |
| Voice | Piper | ElevenLabs |
| Phone | your microphone | Twilio |
| Cost | nothing | a few cents a call |
| Accounts needed | none | four |

Only `BRAIN_PROVIDER` decides the brain; the phone path always uses Deepgram
and ElevenLabs, because phone audio is 8kHz mu-law and the local models are not
set up for it.

## Running it with no accounts

```bash
cd voice_agent
pip install -r requirements.txt
./scripts/setup_local.sh        # installs Ollama, a model, Whisper and Piper
```

Put the three lines it prints into `.env`, then:

```bash
python scripts/simulate_call.py --script hostile   # text, fastest to iterate
python scripts/talk_local.py                       # actually talk to it out loud
```

`talk_local.py` is microphone to speakers with nothing leaving your laptop.

**What local mode will not tell you.** A 7B model is noticeably stiffer than
Claude and will sometimes miss a tool call, so judge the *shape* of the call
here, not the polish. And Whisper cannot transcribe until you stop talking, so
turn-taking is slower and more polite than a real call — local mode cannot show
you barge-in or true latency. Those only show up on stage 4, calling your own
mobile.

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
export ANTHROPIC_API_KEY=sk-ant-...

# read a whole call end to end, no typing
python scripts/simulate_call.py --script interested
python scripts/simulate_call.py --script brushoff
python scripts/simulate_call.py --script skeptical
python scripts/simulate_call.py --script hostile

# or play the prospect yourself
python scripts/simulate_call.py --persona busy
```

Runs the real prompt, the real tools and the real humanizer, so the text is
what the voice would say. Tool calls are printed inline as they fire, so you
can watch it grade the lead and capture the email. Only `ANTHROPIC_API_KEY` is
needed — no phone, no Twilio, no call minutes.

The four `--script` scenarios are the conversations that actually happen on
these calls: a live one, a brush-off, a quality-skeptic, and someone who wants
off the list. Run `hostile` first — it is the one where a bad agent creates a
legal problem rather than a lost deal.

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
- **Latency vs. safety on the model call.** Thinking runs at `low` effort
  rather than being switched off. Disabling it outright is the obvious way to
  shave latency and it backfires here: the model then sometimes writes a tool
  call into its visible text instead of making a real one, which on a phone
  call means the agent says "log_discovery" out loud to a prospect.
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

## Testing, in four stages

Each stage needs more than the last. Do them in order — every stage catches
things the next one would make expensive to find.

### Stage 1 — no credentials, no accounts

```bash
python -m pytest tests -q
```

29 tests over the speech scrubber, do-not-call suppression, calling-window
logic, outcome recording and TwiML generation: the parts where a bug costs
money or breaks a rule. Needs no API keys at all.

You can also boot the server with nothing configured and check it answers:

```bash
uvicorn app.main:app --port 8099 &
curl localhost:8099/health
curl -X POST 'localhost:8099/voicemail?lead_id=x'    # reads back your voicemail script
```

### Stage 2 — what it says (needs `ANTHROPIC_API_KEY` only)

This is the stage that matters most, and the cheapest one. No phone, no Twilio.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python scripts/simulate_call.py --script interested
python scripts/simulate_call.py --script brushoff
python scripts/simulate_call.py --script skeptical
python scripts/simulate_call.py --script hostile
```

What to look for, in priority order:

1. **Does `hostile` immediately stop?** It must admit it is an AI when asked,
   take the removal request on the first ask, fire `mark_do_not_call`, and hang
   up. If it argues or tries one more close, fix that before anything else —
   that is the failure that creates a legal problem rather than a lost deal.
2. **Does it invent anything?** Watch for a made-up price, tonnage, client name
   or turnaround promise. Everything it states should trace back to
   `config/company.yaml`.
3. **Does it sound like a person?** Turns of one or two sentences, contractions,
   no lists read aloud, no "leverage" or "circle back".
4. **Do the tools fire at the right time?** They print inline. The email should
   be captured the moment it is said, not at the end.

Tune `config/script.yaml` and re-run. No code changes needed.

### Stage 3 — the plumbing, still dialing nobody

```bash
export PUBLIC_BASE_URL=http://localhost:8099
uvicorn app.main:app --port 8099 &
python scripts/dial_campaign.py --leads data/leads.sample.csv --dry-run --delay 0
```

Walks the whole list, applies every filter, prints who *would* be dialed and
why the rest were skipped. Confirm the skip reasons are what you expect —
timezone filtering is the one people are surprised by.

Prove the do-not-call list actually blocks a dial:

```bash
python -c "import sys; sys.path.insert(0,'.'); from app.compliance.dnc import add_to_dnc; add_to_dnc('555-010-0001','test')"
python scripts/dial_campaign.py --leads data/leads.sample.csv --dry-run --delay 0
rm data/do_not_call.csv
```

Dolan Steel should now skip with "on do-not-call list", even though the number
is formatted differently in the CSV.

### Stage 4 — call yourself (needs every key)

Fill in `.env` completely, then:

```bash
ngrok http 8080                      # put the https URL in PUBLIC_BASE_URL
uvicorn app.main:app --port 8080
```

Make a one-row lead CSV with **your own mobile number** and your real first
name, then dial it with `--limit 1`. Call yourself a dozen times, playing a
different prospect each time, before any real number is dialed.

On those calls, listen specifically for:

- **Interrupt it mid-sentence.** It must stop within about a word. If it talks
  over you, that is the single thing most likely to get you hung up on.
- **The gap after you stop talking.** You should hear a backchannel almost
  immediately and real speech under about a second. Longer means latency is
  wrong somewhere.
- **Say nothing at all** and confirm it ends the call rather than hanging open.
- **Say "take me off your list"** and confirm the number lands in
  `data/do_not_call.csv`.

Then read `data/calls/*.json` and check the transcript and grade match what
actually happened.

### Then: fifty real calls, watched

Point it at fifty of your least valuable leads, `--limit 5` at a time, and read
every transcript before the next batch. The agent will be wrong about your
business in ways only you can catch.

## Rough cost per call

At a two-minute average, the four vendors together run roughly $0.15–0.30 per
call, most of it speech synthesis. Verify against current pricing before
budgeting a campaign.
