# Changelog

## 1.1.0

**Sounds like a real radio**
- Every transmission keys up like the real thing: a click and the beep, a moment of open channel, the voice
  swelling in narrow-band over a hiss floor, then the hiss tail and the closing beep. Sometimes he holds the
  key between two sentences. Your own push-to-talk clicks and chirps too.
- Bluetooth headsets no longer swallow the start of messages: the output is kept awake between messages with
  an inaudible noise floor, and the first message after a long quiet gets a short moment to wake into.
- The first sound of a transmission is no longer clipped ("erstappen", "eclerc"): silence trimming keeps
  quiet first consonants, and the radio's channel-opening effect is shorter and gentler on the first word.
- Driver names said the way English commentary says them: Verstappen with a V (not the Dutch F),
  "HULL-ken-burg", "BOT-us". "Kerbs" no longer loses its vowel.
- "I need a box", "box me", "coming in" get the box confirmation straight away instead of a question.
- The AI no longer tells you to "check the setup tool", and acknowledges feedback in your words instead of
  inventing a symptom ("slippery" isn't "understeer in the slow stuff").
- Paddock words said like the pit wall: "Inters" is a quick IN-terz (not in-TERZ), plus Pirelli, Ferrari,
  McLaren, Aston, Sauber, Haas, outlap, hotlap, quali, parc fermé, Tifosi, kerbs, deg.
- A short breath between pieces of information instead of running sentences together, and the speed now
  drifts a little from call to call: quicker on box and safety calls, steadier on chat.
- Radio overdrive: the voice is pushed hot into the radio (clipped like a mic held too close), then turned
  back down so it's exactly as loud as before, just dirtier. Settings → Voice → Radio overdrive (default
  12 dB; 0 is clean, 20+ is a bad signal).
- Speaking speed applies to every voice (up to 1.6); cloud voices are sped up without changing pitch.
- Google Gemini (free tier) and OpenAI voices each have their own key in Settings → Voice, so a human-sounding
  voice works whichever AI you chat with. A voice that runs out of credits is swapped for the local one for the
  session, with the reason in Diagnostics.
- Radio text is said the way engineers say it: no "slash", "turns one to three", "five k p h", "S Q one".

**Talks like a real engineer**
- Crashes: a big hit gets "You okay?", then a moment later one report in words ("heavy damage on the left
  side of the front wing, that's four tenths, box this lap"), never percentages.
- Ask "check the floor", "how's the front wing", "check the engine" or "any damage": "Copy, having a
  look", then the answer about that part. "Hi" just gets a hello back.
- Coming into the pits: which tyres you're going to and where you'll rejoin ("fresh hards, out in P7,
  right behind Alonso, close on pit exit"). Ask "how many positions do we lose if we box?" and he works it
  out from the gaps and the pit loss (calibrated to the game's own rejoin prediction).
- Diff, brake bias, ERS and fuel map advice, said like the pit wall: "I keep getting wheelspin, any diff
  advice?" gets "Lower the diff, try 65, should help with the rears out of turn 14". Locking the fronts:
  "Bias back two, 54". Flat battery in hotlap mode: "Energy mode medium". Short on fuel on standard:
  "Fuel map lean, and lift and coast". Once fuel's recovered: "back to standard".
- He watches the wheels: wheelspin or lock-ups at the same corner lap after lap get a diff or bias
  suggestion without being asked (Settings → Callouts → Diff & brake bias tips). Spinning up on the
  grass doesn't count, wet tyres don't get diff tips, and once you change the diff or bias he starts counting again.
- No data yet? Sometimes "Stand by", sometimes "we're having trouble with the sensors", sometimes just
  "We are checking." A garbled transmission gets "bit of static on the radio, say again?".
- Now and then he says the important bit twice so it lands ("Tyres are 9 laps old, tyres are 9 laps old",
  "Gap to Leclerc is 1.2, gap to Leclerc is 1.2"), and a box call sometimes ends "Box and pit, confirm".
  Rare (Natural speech setting decides how often), never on lap times.
- Answers worked out live sometimes take a short thinking beat at a comma, like a person, not a script.
- Far more variety: the common calls have hundreds of ways to be said between them, dealt so the same
  sentence almost never comes back.
- No repeats after flashbacks; no false punctures from worn tyres; no position commentary through your own stop.
- Practice traffic: each car called once ("on a push lap, 8 seconds", then "let him by"), not five times.
- Sprint weekends: the Sprint is called the Sprint, with no pit-stop talk and points for the top 8.
- The AI knows practice from qualifying from a race, knows when the race is over, answers in three sentences
  at most, and complaints ("my tyres are overheating") go to the AI rather than a canned readout.
- Phrasing modelled on 2025-26 team radio.

**Fixes**
- A flashback across the line no longer reads out a lap you never finished, or undoes a stop's count.
- Nothing cuts in between your question and the answer.
- The live radio feed can't be read by other websites; short keys are never shown in the browser.
- Plus about 40 smaller fixes from two full code reviews.
