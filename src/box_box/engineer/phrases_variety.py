"""Many ways to say the calls a driver hears most, so the radio almost never says the same sentence twice.

Each template can hold choices in brackets ("[Good|Nice] lap"), which the phrasebook picks between; an
empty choice ("[, mate|]") may say nothing. Templates only use the fields their call already receives
(a test checks this against the original lines), and the calm personality uses these in place of the
shorter lists in phrases.py.
"""

from __future__ import annotations

VARIETY: dict[str, list[str]] = {
    # ---- laps -------------------------------------------------------------------------------------
    "lap_pb": [
        "{time}. [That's a personal best|New personal best|Best lap so far|That's your best][, nice|, good job|].",
        "[Good lap|Nice lap|Lovely lap|Good one], {time}. [Best so far|New best|That's a PB|Quickest yet].",
        "{time}[, that's|. That's] [a PB|your best yet|the best of the session for you][. Keep building|. More to come|].",
        "[PB|Personal best], {time}. [Good work|Nice job|Keep it coming].",
        "[Okay|Good|Nice], {time}. [Improvement|Step forward|Better], [new best|that's a PB].",
    ],
    "lap_pb_session_best": [
        "{time}[!|.] [Fastest of anyone|Quickest overall|Top of the times|Fastest on track]. P1.",
        "{time}. [That's|You're] [top of the timesheet|P1 on the times|quickest of everyone][, great lap|, mega|].",
        "[Mega|Great|Lovely] lap, {time}. [P1|Top of the pile|Fastest overall].",
        "{time}, [P1|purple lap, P1|fastest of anyone]. [Good job|Nice|That's the one].",
    ],
    "lap_close": [
        "{time}. [Just|Only] {delta} off your best.",
        "{time}[, very close|, close one|, right there]. {delta} off.",
        "{time}. {delta} [shy of|off|away from] your best[, consistent|, good|].",
        "[Close|Nearly|Right on it], {time}, {delta} off.",
    ],
    "lap_slower": [
        "{time}. {delta} off, [sector {sector}|most of it in sector {sector}|lost in sector {sector}].",
        "{time}, {delta} down. [Lost it in sector {sector}|Sector {sector} is where it went|The time's in sector {sector}].",
        "{time}. {delta} [down|off the best]. [Sector {sector} was the slow one|Look at sector {sector}].",
        "[Okay|Right], {time}, {delta} off. [Sector {sector}|It's in sector {sector}].",
    ],
    "lap_slower_corner": [
        "{time}. {delta} [off|down|off the best]. {finding}",
        "{time}, {delta} [down|off]. {finding}",
        "{time}. {finding}",
        "[Okay|Right|So], {time}[, {delta} off|]. {finding}",
    ],
    "lap_position": [
        "P{pos}, {delta} off {name}.",
        "That's P{pos}, {delta} to {name}.",
        "[Currently|You're] P{pos}[,|.] {delta} [behind|off|to] {name}.",
        "P{pos} for now. {name} [is|sits] {delta} [quicker|up the road on time].",
    ],
    "lap_deleted": [
        "[Lap deleted|That lap's deleted], track limits[, {me}|].[ Go again.| Next one.|]",
        "Track limits, [lap deleted|that one's deleted|that lap's deleted]. [Go again|Reset, go again|Next lap].",
        "[Sorry|Unlucky], {me}, [that's deleted|lap deleted]. [Track limits|You were wide].",
    ],
    "purple_sector": [
        "[Purple|Purple sector] {sector}[, nice|, good|].",
        "Sector {sector}, [purple|fastest of anyone|quickest overall].",
        "[Nice|Good], purple in sector {sector}.",
    ],
    # ---- positions --------------------------------------------------------------------------------
    "gained_position": [
        "[Nice move|Good move|Great move|Lovely move]. P{pos}[, keep it going||, well done].",
        "[Good job|Well done|Nice one], P{pos}.",
        "P{pos}. [Well done|Good job|Nicely done][, mate|].",
        "[That's|Up to] P{pos}. [Clean move|Good stuff|Keep pushing].",
        "P{pos}[, great|, mega|]. [Keep it rolling|Keep pushing|Good stuff].",
    ],
    "lost_position": [
        "{name} [is through|got by|is past]. P{pos}.",
        "Lost P{old} to {name}. [Stay with them|Stay close|Keep in touch].",
        "{name} got by. P{pos}. [Stay close, you'll get a run|Hang on to him|Keep the pressure on].",
        "[Okay|Right], {name} [is through|has got you]. P{pos}. [Stay in the fight|Regroup, go again].",
    ],
    # ---- traffic ----------------------------------------------------------------------------------
    "behind_early": [
        "{name} behind on a push lap, {gap}.",
        "[Car behind on a hot lap|Pushing car behind], {name}, {gap}.",
        "Heads up, {name} [pushing|on a push lap] behind you, {gap}.",
        "[Mirrors|Behind you], {name} [on a hot lap|is pushing], {gap}.",
    ],
    "behind_close": [
        "{name} right behind, push lap. [Let him through|Let him by].",
        "Mirrors, {name} on a push lap. [Let him by|Give him room].",
        "{name} right behind you, he's on it. [Stay off the line|Off the racing line].",
        "[Car right behind|{name} on you now], [let him through|stay off the line].",
    ],
    "ahead_slow": [
        "Traffic ahead, {name} on {kind}, {gap}.",
        "{name} ahead on {kind}, {gap}. [He knows you're coming|He's been told|He'll move].",
        "Slow car ahead, {name}, {gap}.",
        "[Heads up|Careful], {name} [on {kind} ahead|ahead on {kind}], {gap}.",
    ],
    "pit_exit_ahead": [
        "Car leaving the pits ahead, {name}. [Careful at the pit exit|Watch the pit exit].",
        "{name} coming out of the pits ahead of you.",
        "[Heads up|Careful|Watch out], {name} [rejoining from the pits|coming out of the pits] ahead.",
    ],
    # ---- tyres ------------------------------------------------------------------------------------
    "tyre_wear": [
        "[Tyres at|Wear's at|Tyre wear] {pct} percent.",
        "Wear's {pct} percent, {worst} the worst.",
        "{pct} percent [wear|worn], {worst} [leading|is the worst one|taking the most].",
        "[Just for info|For info], tyres {pct} percent[, {worst} worst|].",
    ],
    "tyre_wear_high": [
        "{pct} percent wear. [They're going off|They're on the way out], [box soon|we'll be boxing soon].",
        "Tyres are [done|finished|past it], {pct} percent. [Think about boxing|Box soon].",
        "[That's|We're at] {pct} percent. [The cliff's coming|Not long left in these].",
    ],
    "tyres_overheating": [
        "{axle} are running hot. [Manage them in the {where}|Look after them in the {where}].",
        "{axle} hot. [Look after them|Go easy] in the {where}.",
        "[Seeing|We've got] {axle} [hot|overheating]. [Smooth in the {where}|Gentle in the {where}].",
    ],
    "tyres_cold": [
        "Tyres are cold, {axle}. [Get some temperature in|Work them a bit].",
        "{axle} still cold. [Push to warm them|Lean on them a bit].",
        "[Temps are low|Still cold], {axle}. [Build them up|Get some heat in].",
    ],
    "new_tyres": [
        "New {tyre} on. [Give them a lap to come in|Build them up for a lap].",
        "That's new {tyre}. [Build the temperature, then they're yours|Bring them in gently, then go].",
        "On the {tyre} now. [Careful for a lap, they'll be cold|Take a lap to switch them on].",
        "[Fresh|New] {tyre}. [Cold for a lap|Easy on the first lap], [then push|then they're yours].",
    ],
    # ---- energy -----------------------------------------------------------------------------------
    "battery_low": [
        "Battery's low. [Harvest where you can|Recharge where you can].",
        "Battery low, [recharge mode on the straights|we need some harvesting].",
        "[Recharge on, please|Get some charge back]. Battery's low.",
        "[Just for info|Heads up], battery's [low|running low]. [Lift and harvest|Harvest in the braking zones].",
    ],
    "overtake_available": [
        "Overtake [available|is on][.|. Press and hold when you're ready.]",
        "You've got overtake mode, {name} is within range.",
        "[Overtake is on|Overtake's live]. [Press and hold|Use it].",
        "[You've got|We've got] overtake. {name}'s [in range|right there].",
    ],
    # ---- pit stops --------------------------------------------------------------------------------
    "pit_window_soon": [
        "[Box window|Window|Pit window] opens in {n} laps. [Target lap {ideal}|We're looking at lap {ideal}].",
        "Window opens in {n} laps, we're looking at lap {ideal}.",
        "[Just for info|For info], [lap {ideal} is the target|we're targeting lap {ideal}], {n} laps away.",
    ],
    "box_now": [
        "Box this lap, box this lap.",
        "Box, box. Box this lap.",
        "Box box, {me}. [In this lap|This lap].",
        "[Okay|Right] {me}, box this lap. Box this lap.",
        "Box, box, box. [Confirm on the MFD|In this lap].",
        "Box box, {me}. Box and pit, confirm.",
        "Box, box. [Box and pit confirm|Pit confirm].",
    ],
    "pit_stop_done": [
        "Good stop, {seconds} seconds. P{pos}.",
        "{seconds} second stop. P{pos}. [Tyres need a lap|Build the temps].",
        "[Nice|Good|Clean] stop, {seconds}. [Out in P{pos}|You're P{pos}].",
        "[Stop was|That was] {seconds} seconds. P{pos}[, good work from the boys|].",
    ],
    "pit_stop_time": [
        "[Good|Clean|Nice] stop, {seconds} seconds. [Out in P{pos}|Back out in P{pos}|You're P{pos}].",
        "{seconds} second stop. [You're P{pos}|P{pos}][, good job from the guys|].",
        "[Clean|Nice] stop, {seconds}. P{pos}.",
    ],
    # ---- race flow --------------------------------------------------------------------------------
    "fastest_lap_me": [
        "[Fastest lap of the race|That's fastest lap][, nice|, good job|].",
        "That's fastest lap, {me}.",
        "[Purple lap|Fastest lap], [good stuff|nice one|lovely].",
    ],
    "laps_to_go": [
        "{n} laps to go.",
        "{n} to go, {me}.",
        "{n} laps remaining. [Keep it clean|Keep it tidy|Bring it home].",
        "[Okay|Right], {n} [to go|laps left].",
    ],
    "last_lap": [
        "Last lap, last lap. Bring it home.",
        "Final lap, {me}. [Keep it clean|Bring it home].",
        "[Last lap|This is the last lap|Final lap]. [Bring it home|Nice and clean|No mistakes now].",
    ],
    "after_lap_one_gained": [
        "P{pos}. Good start, up {n}.",
        "Nice start. P{pos}, up {n}.",
        "[Great|Mega|Lovely] start, up {n}. P{pos}[, keep it going|].",
    ],
    "after_lap_one_lost": [
        "P{pos}. Lost {n}, long race. Stay calm.",
        "P{pos}. We'll get them back.",
        "[Okay|Right], P{pos}. [Long way to go|Plenty of time]. [Stay calm|Settle in].",
    ],
    "after_lap_one_same": [
        "P{pos}. Good, settle in.",
        "Held position, P{pos}. Settle in.",
        "P{pos}. [Clean start|Good, clean|Tidy start]. [Find your rhythm|Settle in|Long race, settle in].",
    ],
    "car_ahead_pitted": [
        "{name} has boxed. [Clear air, push now|Push now, clear air].",
        "{name} pitting. This is your chance, push.",
        "[Just for info|Okay], {name} [is in|has pitted]. [Push now|Go now, use the clear air].",
    ],
    "car_behind_pitted": [
        "{name} behind has boxed. Undercut threat.",
        "{name} pitted behind. Watch the undercut.",
        "[Just for info|Heads up], {name} [has boxed|is in] behind. [Undercut threat|They'll try the undercut].",
    ],
    "drs_range": [
        "You're within a second of {name}.",
        "Inside a second to {name}. Go get them.",
        "[DRS range|Within the second|Inside the second] on {name}[, go|, go get him|].",
    ],
    "rhythm": [
        "Okay, let's get back in the rhythm.",
        "Shake it off. Back into the rhythm, please.",
        "That one's gone. Let's get back in the rhythm, {me}.",
        "[Reset|Forget it|Shake it off]. [Back in the rhythm|Find the rhythm again|Next lap, back on it].",
    ],
    "retirement_near": [
        "{name} is out of the race.",
        "{name} has retired. [One less to worry about|That's one less].",
        "[Just for info|For info|Heads up], {name} [is out|has stopped|has retired].",
    ],
    # ---- flags ------------------------------------------------------------------------------------
    "yellow_ahead": [
        "Yellow flag, {where}. Careful.",
        "Yellows at {where}. Don't get caught out.",
        "Yellow flag ahead, {where}.",
        "[Careful|Heads up|Watch out], yellow[s|] at {where}[, no overtaking|].",
    ],
    "safety_car": [
        "Safety car, safety car. [Slow down, stay positive to the delta|Positive delta].",
        "Safety car deployed. Keep the delta positive.",
        "Safety car[, safety car|]. [Stay positive|Keep it positive to the delta|Watch the delta].",
    ],
    "vsc": [
        "VSC, VSC. Stay positive to the delta.",
        "Virtual safety car. Positive delta.",
        "VSC[, VSC|]. [Watch the delta|Positive on the delta|Stay above the delta].",
    ],
    "green_flag": [
        "Green, green, green.",
        "And we're green. Go.",
        "[Green flag|We're green|Green]. [Racing|Go|Back to racing][, push|].",
    ],
    # ---- conversation ------------------------------------------------------------------------------
    "feedback_ack": [
        "Copy, thanks. Noted.",
        "Copy that. Understood.",
        "Copy. We'll look at that.",
        "Okay, copy. Thanks {me}.",
        "[Copy|Understood|Got it], [we'll look into it|we'll check the data|noted].",
    ],
    "stand_by": ["Stand by.", "Stand by one.", "[Stand by|Stand by one], [looking|checking|having a look]."],
    "didnt_catch": [
        "Say again, {me}? Didn't catch that.",
        "Sorry, say again?",
        "Radio's broken up, say again?",
        "[Missed that|Didn't get that|Broken up there], [say again|repeat|once more]?",
        "[Lot of static on the radio|There's some static on the radio|Just static this end], [say again|say again, {me}]?",
        "[You're breaking up|Radio's crackling|Signal's bad there], [say again|repeat that]?",
        "[Sorry|Apologies], [only got static|all static there]. [Say again?|Once more?]",
    ],
    "ask_balance": [
        "How's the balance?",
        "How's the car feeling, {me}? Any balance issues?",
        "Feedback on the balance when you're ready.",
        "[Any comments on the balance|How's the car handling|Talk to me about the balance][, {me}|]?",
    ],
    "ask_tyres": [
        "Feedback on this tyre when you can.",
        "How are the tyres feeling, {me}?",
        "How's the tyre?",
        "[How are they holding up|Any feedback on the tyres|How do the tyres feel][, {me}|]?",
    ],
    # ---- strategy ---------------------------------------------------------------------------------
    "tyres_robust": [
        "These tyres are robust, happy to push on them.",
        "Deg's under control. Happy for you to push.",
        "Tyres look good. Lean on them.",
        "[Deg looks fine|Tyres are holding up well|Deg's low]. [Push if you want|You can push|No need to save them].",
    ],
    "plan_a_target": [
        "Plan A, target lap {lap}. Tyres look good.",
        "Still plan A, lap {lap}. Deg's fine.",
        "[Plan A for now|We're on plan A|Still plan A], [lap {lap}|target lap {lap}|box around lap {lap}].",
    ],
    "fuel_ok": [
        "Fuel's fine, no saving.",
        "Fuel's good, push if you need to.",
        "[Fuel's okay|No fuel saving needed|Fuel's on target]. [Push|Full attack|No lift and coast needed].",
    ],
}
