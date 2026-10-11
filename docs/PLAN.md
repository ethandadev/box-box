# "Box Box": an AI race engineer for F1 25 (2026 Season Pack)

A voice race engineer that listens to the game's live telemetry, talks to you over a
push-to-talk radio mapped to a steering-wheel button, and sounds and acts like a real F1
race engineer. It is not a dashboard that reads numbers aloud.

## 0. Decisions (locked in)

| Question | Answer | What it changes |
|---|---|---|
| Platform | **PC** | Telemetry over localhost; wheel read directly over USB HID |
| Wheel | **Moza** | Moza wheels expose their buttons through the base as a standard game controller (visible in Pit House and `joy.cpl`), so SDL reads them while the game has focus. PTT "learn" screen stores device GUID plus button index |
| Compute | **Local-first, RTX 5090 (32 GB)** | STT and TTS run on the GPU. The LLM stays on a fast online model for brains, with a local model as an offline fallback |
| Driver | **Ethan** | The engineer calls you "Ethan" (or nothing, mid-corner) |
| Voice | **Normal American accent** | Original voice. No cloning of real engineers |
| Mode | **Career, racing against AI** | Practice programmes, quali, and races vs AI come first. Online/league features come later. All AI cars have public telemetry, so full data is available for every car |
| Repo | **`box-box`**, separate from Touge | — |

### Local-first stack on the 5090

| Job | Pick | VRAM | Notes |
|---|---|---|---|
| STT | `faster-whisper` large-v3-turbo (int8/fp16) | ~2–3 GB | Only runs while PTT is held, so it costs the game nothing the rest of the time. F1 vocabulary via `initial_prompt` / hotwords |
| TTS (reflex lines) | Kokoro-82M, American male voice | < 1 GB | ~real-time ×50 on a 5090. Pre-renders the phrasebook at startup |
| TTS (conversation) | Kokoro first, then A/B an expressive model (Orpheus / Chatterbox) for emotion on wins and crashes | 2–8 GB | Pick by ear in M3 |
| LLM (live) | Claude Haiku 5.5 (`claude-haiku-5-5`) online | — | Fastest route to a smart engineer with tool use |
| LLM (offline fallback) | A ~8–14B instruct model via llama.cpp / Ollama | 6–10 GB | Used only if the network drops. The reflex path never needs it |

The game at 4K max uses roughly 10–14 GB of VRAM, so the whole stack fits alongside it. GPU work is bursty (only while
you talk or he talks), so it shouldn't dent frame rate. M2 measures that.

---

## 1. What "feels like a real engineer" actually means

Real engineers are good because of **what they don't say** and **when they say it**. The
design rules that follow from that:

1. **Talk on the straights, not in the corners.** Messages wait for a low-workload window,
   unless they're urgent (yellow flag, car stopped ahead, box now).
2. **Short, numeric, driver-name first.** "Gap to Norris one-point-two, he's
   dropping two tenths a lap. Tyres are good." Never a paragraph.
3. **Proactive but rationed.** There's a radio budget per lap that changes with the
   session (quiet on a quali push lap, chattier on a practice long run).
4. **Calm, then one notch up.** The tone stays level and only rises for an incident, the last lap of a
   fight, or a win.
5. **He knows the plan.** Practice has a run plan, quali has a release-timing plan, the race
   has a strategy with Plan B/C. He refers back to them ("Plan A still, box lap 22").
6. **He remembers you.** He knows your weak corners, last weekend's result, and that you hate
   being told about tyre temps every lap.
7. **He can be wrong and say "stand by."** If he doesn't know something, he says "Stand by, checking" and comes back
   instead of making something up.

---

## 2. System architecture

```
 F1 25 ──UDP 20777──► [Ingest] ─► [State Model] ─► [Event Detectors] ─┐
                         │             ▲   ▲                         │ typed events
                         ▼             │   │                         ▼
                    [Recorder]         │   └──── tool calls ──── [Engineer Brain (LLM)]
                    (.f1rec for        │                              ▲        │
                     replay/dev)       │                              │ text   │ text (streamed)
                                       │                              │        ▼
 Wheel button ──► [PTT / HID] ──► [Mic + STT] ──── transcript ────────┘   [Radio Director]
                                                                         (priority queue,
                                                                          timing, budget)
                                                                                 │
                                                                                 ▼
                                                                  [TTS] ─► [Radio FX] ─► 🎧
```

There are two paths. Keeping them separate is the most important architectural decision.

| Path | Used for | Latency target | LLM in the loop? |
|---|---|---|---|
| **Reflex** | Safety and timing-critical callouts: traffic, flags, "box box", blue flags, damage | < 300 ms | No. Templated phrases with variation, using pre-rendered or fast TTS |
| **Conversational** | Anything you ask, plus the richer proactive messages (strategy, lap feedback, debriefs) | ~0.8–1.5 s to first audio | Yes, with streaming LLM and tool calls |

The LLM never sits between "car approaching at 250 km/h" and you hearing about it.

---

## 3. Components

### 3.1 Telemetry ingest
- F1 25 UDP, **format 2026** (requires the 2026 Season Pack; set *UDP Format = 2026* in
  game settings). Port 20777, send rate 20–60 Hz.
- Parse every packet type: Motion, Session, LapData, Event, Participants, CarSetups,
  CarTelemetry, CarStatus, FinalClassification, LobbyInfo, CarDamage, SessionHistory,
  TyreSets, MotionEx, TimeTrial, LapPositions, and the new 2026 **CarTelemetry2** packet
  (active-aero mode and availability, overtake-mode availability and activation, 2026-rules flag). The Session
  packet also gains active-aero zones.
- Byte layouts come from EA's official UDP spec PDF on the Codemasters/EA forums. Use it as the
  source of truth, because community parsers disagree in places.
- **Recorder/replayer from day one.** Dump raw packets with timestamps to a file and replay
  them at 1× or 10×. That way 90% of development happens without the game running.

### 3.2 State model (the engineer's "pit wall screens")
A single in-memory model, updated per packet:
- **Per car:** position, lap/sector times, `lapDistance`, driver status (in lap / out lap /
  flying lap / in garage), pit status, tyre compound and age, penalties and warnings, ERS/battery, gaps.
- **Player:** everything above plus tyre wear/temps/pressures, brake temps, fuel, damage,
  inputs (throttle/brake/steer), and 2026 energy state, overtake and active aero.
- **Session:** type, weather plus forecast samples, track temp, SC/VSC, flags per marshal
  zone, pit window (ideal/latest lap), track length, DRS/active-aero zones.
- **Track map:** a corner table per track (`corner N → lapDistance start/apex/exit`),
  auto-built from the speed minima of your first clean lap and cached. It lets him say
  "Turn 10" instead of "1,842 metres".
- **History:** every lap of every car for degradation fits and pace comparison.

### 3.3 Event detectors (deterministic, fast)
Small, unit-testable functions that emit typed events with a priority and an expiry time.

**Practice / quali traffic (the outlap feature):**
```
for each other car C:
    d   = relative track distance (wrap-aware) from me to C
    v   = closing speed (Δd / Δt, smoothed over ~0.5 s)
    ttc = d / v                     # time until they reach me
    if C.status == FLYING_LAP and me.status in {OUT_LAP, IN_LAP, COOL_DOWN}:
        if ttc < 8 s  → "Car behind on a push lap, eight seconds."            (once)
        if ttc < 4 s  → "{Name} on a push lap, let him by at Turn {corner where ttc hits 0}."
    if me.status == FLYING_LAP and C ahead on out/in lap and ttc < 6 s:
        → "Traffic ahead, {Name} on an out lap, he's been told."
```
Plus a **gap-finder for release timing**: "Hold in the box… go now, you'll have a
clear lap. Nearest car behind 9 seconds."

**Other detectors:** yellow/double yellow/red flag in the sector ahead, SC/VSC deploy and ending,
blue flags, car stopped on track, weather change in N minutes, tyre temp out of window,
brake temp warning, damage (front wing %, floor), track-limits strike count, penalty issued,
pit window open, undercut threat (car behind pits), battery/overtake-mode available
(2026: within 1 s at the detection point), fastest sector/lap (purple/green), lap delta vs PB,
last lap, and the race result.

### 3.4 Radio director (where the realism comes from)
- **Priority queue:** P0 safety (interrupts anything) > P1 strategy calls > P2 info > P3
  colour/encouragement.
- **Workload gate:** a P1–P3 message only plays when `brake == 0`, `|steer| < 0.1`, and throttle
  has been at 100% for more than 0.4 s, *or* the planned straight from the track map is long enough
  for the estimated speech duration.
- **Staleness:** events expire. A gap message that's 6 seconds old gets dropped or rewritten.
- **Budget:** N messages per lap per session type. P0 is exempt.
- **De-dup and cool-downs:** he doesn't repeat "tyres are good" every lap.
- **Barge-in:** pressing PTT ducks or stops his audio immediately, like a real radio.
- **Half-duplex feel:** a squelch click when he keys in and a click and tail noise when he keys out.

### 3.5 Engineer brain (LLM)
- **Model:** a fast hosted model with tool use and streaming. Default to **Claude Haiku 5.5**
  (`claude-haiku-5-5`), escalating to Sonnet 5.5 for the post-session debrief and strategy
  deep-dives where latency matters less.
- **Prompt layers** (with prompt caching on the static part):
  1. Persona and radio-style rules (static, cached)
  2. Track notes and the session plan (per session, cached)
  3. Driver memory (per user, cached)
  4. A live "pit wall snapshot" (compact text, around 300 tokens, regenerated per request)
  5. The last ~10 radio exchanges
- **Tools** (the LLM *asks* the state model; it never guesses numbers):
  `get_gaps()`, `get_tyres()`, `get_car_health()`, `get_weather_forecast()`,
  `get_strategy_options()`, `compare_laps(lap_a, lap_b)`, `sector_loss_vs_best()`,
  `get_competitor(name)`, `get_energy_state()`, `set_preference(key, value)` (e.g. "less
  tyre info"), `schedule_callout(condition, text)` (e.g. "tell me when Leclerc pits").
- **Output contract:** at most 2 sentences per radio transmission, numbers spoken the radio way
  ("one-point-two", "P4", "two tenths"), no markdown, and no hedging fluff.
- **Proactive rich messages:** some detector events (e.g. "lap complete" in practice) are
  handed to the LLM to phrase with context: "Good lap. Lost a tenth and a half in sector two,
  mostly Turn 9, you're braking earlier than your best."

### 3.6 Voice in: push-to-talk, mic and STT
- **Wheel button:** read the wheel as a joystick via SDL2 (`pygame`/`pysdl2`). Most wheels
  (Fanatec, Moza, Logitech, Thrustmaster, Simucube) expose buttons through DirectInput
  non-exclusively, so you can read them while the game has focus. A **"Learn button" setup
  screen** lets you press the button and stores device GUID plus button index. Hold-to-talk
  and tap-to-toggle are both supported.
  - Pick a button that is unbound in-game, or deliberately bind it to the same button
    as the in-game radio menu if you want both.
- **Mic capture:** `sounddevice` at 16 kHz mono with a ring buffer that starts ~200 ms *before* the
  button press, so your first word isn't clipped.
- **STT:** streaming **Deepgram Nova** (or similar) with keyword boosting for F1 vocabulary
  (box, inters, softs, mediums, hards, Plan B, undercut, driver surnames, track corner names).
  Local fallback: `faster-whisper` (large-v3-turbo) on GPU. Transcribe while you're still talking
  so the transcript is final ~150 ms after release.
- **Quick commands bypass the LLM:** "box this lap", "what's the gap", and "radio check" get
  matched by intent and answered from templates instantly.

### 3.7 Voice out: TTS and radio FX
- **TTS:** a low-latency streaming engine such as ElevenLabs Flash or Cartesia Sonic (both
  ~100 ms to first audio). Local fallback: Kokoro or Piper.
- **Voice:** design an **original** voice, e.g. calm, mid-30s, British or another accent you like.
  **Don't clone a real engineer's voice** (Bono, GP, etc.). It's a likeness and consent issue,
  and most TTS providers forbid it. You can get the vibe through cadence and phrasing alone.
- **Radio chain:** band-pass 300–3,400 Hz → compressor → light saturation → occasional
  dropout or crackle at low "signal" → squelch open/close SFX → a bed of pit-wall noise. Duck
  the game audio by ~6 dB while he talks (Windows audio session API), or just rely on volume.
- **Pre-render** the 200 most common reflex phrases at startup (with 3–4 variations each) so P0
  callouts have zero synthesis latency.

### 3.8 Memory and personality persistence
- SQLite: sessions, laps, stints, incidents, and preferences.
- After each session, an LLM-written **debrief** is stored and summarised into the driver memory:
  "Struggles with T1 at Monza on cold tyres; prefers gap info every 2 laps; beat
  Russell at Silverstone last week."
- He brings it up naturally: "Same as Silverstone, keep it clean through the first stint."

---

## 4. The persona

**Name:** configurable. Default *"Jake"*, an American race engineer, calm Midwestern delivery. Calls the driver "Ethan".

**Character sheet (goes in the system prompt):**
- Ex-data engineer who came up through IndyCar before 10 years in F1. Laconic, with dry humour that only
  shows when things are calm. Fiercely on the driver's side, and never blames the driver on radio during a race.
- He calls you by your chosen name or nickname. He'll call you "mate" once a race at most.
- **Stock vocabulary:** "Copy." "Understood." "Stand by." "Box, box." "Stay out, stay out."
  "Mode push." "Plan B." "Keep it clean." "Tyres look good." "We're checking." "Nice and
  tidy." "Get in there!" (wins only).
- **Never:** long sentences, "As an AI…", emoji, apologising more than once, or inventing data.
- **Escalation ladder:** level → firm ("Need you to push now") → urgent ("BOX BOX BOX") →
  celebratory (victory) → supportive (crash: "Are you okay? … Okay. Box, we'll look at the
  car").

**Example transmissions:**

| Situation | Engineer |
|---|---|
| Outlap, car closing | "Verstappen behind on a push lap, four seconds. Let him by into Turn 10." |
| You're on a hot lap, traffic | "Traffic ahead, Alonso on his out lap, he's been told." |
| Practice lap done | "Lap one-thirty-two-four. Two tenths off your best, all in sector two." |
| You ask "how are the tyres?" | "Fronts are fine, rears forty percent, getting warm. Manage the exits." |
| Rain coming | "Rain expected in about six minutes. Not heavy. We stay out for now." |
| Undercut threat | "Hamilton's boxed. Box this lap, box this lap. Hards." |
| 2026 overtake mode | "Overtake available next lap, you're under a second at detection." |
| You won | "P1! P1! Get in there! What a drive. Get in there!" |

---

## 5. Session behaviour

| Session | What he focuses on |
|---|---|
| **Practice** | Run plan ("3-lap run on softs, then long run on mediums"), traffic management on out/in laps, sector-by-sector feedback, setup feedback questions ("how's the balance in the slow stuff?"), degradation numbers after long runs |
| **Qualifying** | Release timing into a gap, tyre prep on the out lap, a "this is it, last lap" call, track-evolution notes, silence during the push lap except for P0 |
| **Race** | Start procedure, gaps every 1–2 laps (to your preference), strategy and pit calls, undercut/overcut, SC/VSC opportunities, weather, energy/overtake (2026), damage, "last lap", cool-down and result |
| **Post-session** | Spoken debrief with the key takeaways, stored to memory |

---

## 6. Strategy model (makes him sound smart)
- Fit a tyre degradation curve per compound from your laps (fuel-corrected linear or
  quadratic).
- Pit-loss per track (measured from your own stops, or seeded from a table).
- Every lap, simulate stop-lap options for you and the cars ±2 positions, then rank them.
- Feed the top 2–3 options to the LLM as `get_strategy_options()` so it can explain in
  one sentence: "Box lap 18 for hards gets you out ahead of Piastri."

---

## 7. Tech stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12 + `asyncio` | Fastest to build. Telemetry at 60 Hz is trivial load |
| UDP parsing | `struct` / `ctypes` structs, auto-generated from a spec table | One table per format year |
| Wheel input | `pysdl2` or `pygame` joystick | Cross-wheel and non-exclusive |
| Audio I/O | `sounddevice` + `numpy` / `scipy` DSP | Low-latency streams and the radio filter |
| STT | Deepgram streaming / `faster-whisper` | Cloud speed plus a local fallback |
| LLM | Anthropic SDK: `claude-haiku-5-5` (live), `claude-sonnet-5-5` (debrief) | Fast tool use and streaming |
| TTS | ElevenLabs Flash / Cartesia Sonic / Kokoro (local) | Streaming at ~100 ms |
| Storage | SQLite | Zero ops |
| UI | Small local web page (FastAPI + HTMX) for setup, button learn, persona, and a live debug view | Usable on a second monitor or phone |

Optional later: package as a single `.exe` with PyInstaller, and add a SimHub overlay.

---

## 8. Latency budget (conversation path)

| Step | Target |
|---|---|
| PTT release → final transcript | 150–300 ms |
| Transcript → first LLM token (cached prompt, small snapshot) | 300–600 ms |
| First sentence → first TTS audio | 100–200 ms |
| **Total to first syllable** | **≈ 0.6–1.1 s** |

Tricks: stream LLM output and send it to TTS **sentence by sentence**, play a short "Copy," or
"Stand by," filler (pre-rendered) immediately on release when the question needs tools, and keep the
snapshot compact.

---

## 9. Milestones

| # | Milestone | Done when |
|---|---|---|
| M0 ✅ | **Telemetry spine:** UDP parser (2025 and 2026 formats, all cars), recorder, replayer, state model, first detector (out-lap traffic) | A recorded race replays and the state matches the game HUD. *Built and tested on real recordings. Needs one check against your own game.* |
| M1 | **Reflex engineer:** detectors plus radio director plus pre-rendered TTS plus radio FX | Outlap traffic, flags, and gaps are called out at the right moments, only on straights |
| M2 | **Talk to him:** wheel PTT learn screen, mic, STT, LLM with tools, streaming TTS | You can ask "gap to the car ahead?" and get a correct spoken answer in ~1 s |
| M3 | **Personality:** persona prompt, escalation, budget/preferences, session-type behaviour | Blind test: a friend thinks it's a pre-recorded human engineer |
| M4 | **Strategy:** degradation fit, pit-loss, undercut detection, pit calls | He calls a correct undercut in a real race |
| M5 | **Memory and polish:** debriefs, cross-session memory, setup UI, packaging | Install on a fresh PC in under 5 minutes |

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| He talks too much and gets annoying | Radio budget, workload gate, and a "less radio" voice command. Tune with replays |
| LLM invents numbers | Numbers only come from tools. A post-filter checks spoken numbers against state |
| Network hiccup mid-race | Reflex path is fully local. On LLM timeout he says "Stand by" and uses the template fallback. Local STT/TTS fallback |
| UDP format changes with game patches | A format table per version, a packet-size assertion, and a loud warning in the UI |
| Wheel button not visible (console players) | Fall back to a USB button, Stream Deck, keyboard hotkey, or phone PTT page |
| Cost | Haiku plus prompt caching is cents per race. TTS is the main cost, which pre-rendering and local TTS reduce |

---

## 11. Q&A

**Does this work on PS5/Xbox?**
Telemetry yes: the console sends UDP to your PC's IP. The wheel button no, because the wheel
is plugged into the console and the PC can't see it. Use a USB button box, a Stream Deck, a
phone PTT web page, or voice activation with a wake word ("Radio,").

**Is there an "F1 26" game?**
No. The 2026 season is a Season Pack for F1 25 with its own UDP format ("2026") that adds active aero and
overtake mode data. Support both 2025 and 2026 formats. A new mainline game is expected
later, and it will just be another format table.

**Can it use the real engineers' voices?**
Don't. Cloning a real person's voice without consent is an ethical and legal problem and is
against TTS providers' terms. Build an original voice and get the realism from phrasing,
timing, and the radio effect. That's 80% of what makes it sound real anyway.

**Why not use a speech-to-speech "realtime" model end-to-end?**
It's lower latency for chat, but you lose control of the voice character and the radio
timing, and it can't be the safety path. Cascaded STT → LLM → TTS with a separate reflex path
is more controllable. A realtime model can be revisited later as an option for the conversation path.

**How does he know which corner I'm at?**
From `lapDistance` plus an auto-built corner table (speed minima on a clean lap), named via a
small per-track JSON ("Turn 1 – Copse"). He says "Turn 10" or "into Copse".

**Will the LLM slow down the important callouts?**
No. Safety and traffic callouts never touch the LLM. They're deterministic detectors plus
pre-rendered audio.

**How much will it cost to run?**
A race with ~60 LLM exchanges on Haiku with prompt caching costs pennies. Cloud TTS is the
biggest line item. Pre-rendered reflex lines plus local TTS for repeats keep it low, and local
TTS/STT makes it nearly free.

**Can I talk to him naturally or only use commands?**
Naturally: "How are we looking on the tyres compared to Leclerc?" works. Common commands
are also matched instantly without the LLM.

**Can I tell him to shut up?**
Yes. "Less radio", "only gaps", and "quiet during quali laps" become stored preferences.

**Multiplayer/league racing?**
Works the same, and the Participants and LobbyInfo packets give real player names. Other players'
telemetry may be restricted by their "UDP telemetry: restricted" setting, so he falls back to
timing-only data for those cars.

**Will he be "coaching" level, telling me braking points?**
A first version does it per sector and corner by comparing your laps (speed and brake traces at each
`lapDistance`). Detailed coaching ("brake at the 100 board") is a natural M6.

---

## 12. Next up (M1)

1. Ethan records a few sessions with `box-box record` (one practice with traffic, one race) to use as test data.
2. Radio director: priority queue, workload gate (straights only), staleness, and per-lap budget.
3. Kokoro TTS plus the radio FX chain, with the phrasebook pre-rendered.
4. More detectors: flags/SC/VSC, gaps each lap in races, weather change, damage, pit window, and lap/sector feedback in practice.
5. Corner map per track (auto-built from speed minima on a clean lap, then hand-named), so traffic calls say "into Turn 10".
