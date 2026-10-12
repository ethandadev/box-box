"""Everything the engineer says on his own, by situation and personality.

Style rules (from real F1 radio): short, numbers first, the driver's name only when it adds weight,
no filler, calm unless it matters. `{me}` is the driver's name, `{name}` another driver's surname.
Personalities: "calm" (default, always complete), "fired_up", "dry". Missing ones fall back to calm.
"""

from __future__ import annotations

PHRASES: dict[str, dict[str, list[str]]] = {
    # ---- session ------------------------------------------------------------------------------
    "greet_practice": {
        "calm": [
            "Radio check, {me}. {track}, {session}. Track temp {track_temp}. Let's get the programme going.",
            "Hey {me}, radio check. {session} at {track}, {weather}. Take your time on the out lap.",
            "Morning {me}. {session}, {track}. Usual plan, build up the pace and give me feedback.",
        ],
        "fired_up": [
            "{me}! Radio check. {session} at {track}, let's get to work!",
            "Radio check, {me}. {track}, {weather}. Let's find some lap time today!",
        ],
        "dry": [
            "Radio check, {me}. {session} at {track}. The car's in one piece. Let's keep it that way.",
            "{me}, radio check. {track}, {weather}. Try to bring the car back looking like this.",
        ],
    },
    "greet_sprint": {
        "calm": [
            "Radio check, {me}. Sprint, {laps} laps from P{grid}. No stop, so it's flat out from lights out.",
            "Okay {me}, {laps} laps, P{grid}. Sprint rules: no stop, every lap counts. Clean into turn one.",
        ],
        "fired_up": ["Sprint time, {me}! P{grid}, {laps} laps, no stop. Flat out!"],
    },
    "greet_quali": {
        "calm": [
            "Radio check, {me}. {session}, {minutes} minutes on the clock. Let's get a banker in early.",
            "Okay {me}, {session} at {track}. Track's {track_temp} degrees. Let's get it done.",
        ],
        "fired_up": [
            "Qualifying, {me}! {minutes} minutes. Let's put it at the sharp end!",
            "Here we go {me}, {session}. Let's nail this.",
        ],
        "dry": [
            "{session}, {me}. {minutes} minutes. No pressure. Well, some pressure.",
        ],
    },
    "greet_race": {
        "calm": [
            "Radio check, {me}. Race day at {track}, {laps} laps. Starting P{grid}. Keep it clean into turn one.",
            "Okay {me}, {laps} laps at {track}. P{grid} on the grid. Look after the tyres early.",
        ],
        "fired_up": [
            "Race day, {me}! P{grid}, {laps} laps. Let's go racing!",
            "{me}, {laps} laps at {track} from P{grid}. Let's have a big one today!",
        ],
        "dry": [
            "Radio check, {me}. P{grid}, {laps} laps. Turn one is not where the race is won. Just saying.",
        ],
    },
    "greet_time_trial": {
        "calm": ["Radio check, {me}. Time trial at {track}. Clean laps, build it up."],
    },
    "quali_run": {
        "calm": [
            "Okay, {run} run. {minutes} minutes left. Build the tyre temperature on the out lap.",
            "{run_cap} run, {me}. {minutes} minutes on the clock. Get the tyres in, find some space.",
            "Out on the {run} run. Good out lap, get temperature in the fronts.",
        ],
        "fired_up": ["{run_cap} run, {me}! {minutes} minutes. Let's find that lap time!"],
    },
    "time_left": {
        "calm": [
            "{minutes} minutes left in the session.",
            "{minutes} minutes to go.",
        ],
        "fired_up": ["{minutes} minutes left, {me}!"],
    },
    "quali_drop_zone": {
        "calm": [
            "You're P{pos}, that's in the drop zone. You need to go again.",
            "P{pos}, {me}. Not safe. We need another lap.",
        ],
        "fired_up": ["P{pos}, drop zone! We need one more lap, {me}!"],
    },
    "quali_safe": {
        "calm": ["P{pos}. You're safe for now.", "P{pos}. That should be enough to go through."],
    },
    "quali_last_lap_chance": {
        "calm": ["This is the last chance, {me}. Make it count.", "Final lap of the session. This is it."],
        "fired_up": ["Last chance, {me}! Everything on this lap!"],
    },
    "session_end_practice": {
        "calm": [
            "That's the session. Best lap {best}, P{pos}. Good work, {me}.",
            "Session over. {best} was your best, P{pos}. Bring it in.",
        ],
        "dry": ["And that's practice. {best}, P{pos}. Nobody hit anything. Success."],
    },
    "session_end_quali": {
        "calm": [
            "That's qualifying. P{pos}, {me}. Good job.",
            "Quali done, P{pos}. We'll take it.",
            "P{pos}. Good lap considering. We'll go to work for tomorrow.",
        ],
        "fired_up": ["P{pos} in qualifying! Great job, {me}!"],
    },
    "quali_out": {
        "calm": [
            "That's P{pos}, we're out. Sorry {me}. We'll regroup for tomorrow.",
            "P{pos}, not enough to go through. You got the best out of it. We'll get better.",
        ],
        "dry": ["P{pos}. We're out. The good news is the tyres are very fresh for tomorrow."],
    },
    "quali_pole": {
        "calm": ["Pole position, {me}! P1! Great job.", "That's pole! P1, {me}. Brilliant lap."],
        "fired_up": ["POLE! Pole position, {me}! What a lap! Get in there!"],
        "dry": ["P1. Pole position. I'll allow a small celebration."],
    },
    # ---- traffic --------------------------------------------------------------------------------
    "behind_early": {
        "calm": [
            "{name} behind on a push lap, {gap}.",
            "Car behind on a hot lap, {name}, {gap}.",
            "Heads up, {name} pushing behind you, {gap}.",
        ],
        "fired_up": ["{name} coming on a push lap, {gap} behind!", "Watch the mirrors, {name} on a hot lap, {gap}."],
    },
    "behind_close": {
        "calm": [
            "{name} right behind, push lap. Let {name} by.",
            "Mirrors. {name} on a push lap, right behind you.",
            "{name} is on it, right behind. Stay off the line.",
        ],
    },
    "ahead_slow": {
        "calm": [
            "Traffic ahead, {name} on {kind}, {gap}.",
            "{name} ahead on {kind}, {gap}. They know you're coming.",
            "Slow car ahead, {name}, {gap}.",
        ],
    },
    "pit_exit_ahead": {
        "calm": [
            "Car leaving the pits ahead, {name}. Careful at the pit exit.",
            "{name} coming out of the pits ahead of you.",
        ],
    },
    # ---- flags ----------------------------------------------------------------------------------
    "yellow_ahead": {
        "calm": [
            "Yellow flag, {where}. Careful.",
            "Yellows at {where}. Don't get caught out.",
            "Yellow flag ahead, {where}.",
        ],
        "fired_up": ["Yellow flag, yellow flag, {where}!"],
    },
    "safety_car": {
        "calm": [
            "Safety car, safety car. Slow down, stay positive to the delta.",
            "Safety car deployed. Keep the delta positive.",
        ],
        "fired_up": ["Safety car, safety car! Slow it down, positive delta!"],
    },
    "vsc": {
        "calm": [
            "VSC, VSC. Virtual safety car deployed, keep it above the delta.",
            "Virtual safety car. Stay positive to the delta.",
        ],
    },
    "safety_car_in": {
        "calm": ["Safety car in this lap. Get the tyres ready.", "Safety car coming in. Restart's on."],
        "fired_up": ["Safety car in this lap! Get ready!"],
    },
    "vsc_ending": {
        "calm": ["VSC ending. Get ready to go.", "VSC ending, VSC ending. Just watch your dash."],
    },
    "green_flag": {
        "calm": ["Green, green, green. Racing.", "And we're green. Go."],
        "fired_up": ["Green flag! Go, go, go!"],
    },
    "red_flag": {
        "calm": [
            "Red flag, red flag. Slow down and bring it to the pits.",
            "Red flag. Session stopped. Come in slowly.",
            "Red flag, red flag. No overtaking. Follow the cars into the pit lane, pit limiter on the way in.",
        ],
    },
    "blue_flags": {
        "calm": ["Blue flags. Let the car behind through.", "Blue flag, {name} coming through. Let them by."],
    },
    "warning_track_limits": {
        "calm": [
            "That's a track limits warning. {count} now.",
            "Track limits, that's warning number {count}.",
        ],
        "dry": ["Track limits again. {count}. The white line is not decorative."],
    },
    "penalty_time": {
        "calm": ["We have a {seconds} second penalty. {reason}", "{seconds} second penalty, {me}. {reason}"],
    },
    "penalty_dt": {
        "calm": ["Drive through penalty. You need to serve it within three laps.", "We've got a drive through."],
    },
    "penalty_sg": {
        "calm": ["Stop and go penalty, {seconds} seconds. Box within three laps to serve."],
    },
    "lap_deleted": {
        "calm": [
            "Lap deleted, track limits.",
            "That lap's deleted, {me}. Track limits.",
            "Lap's gone, track limits. Go again.",
        ],
        "dry": ["Lap deleted. Track limits. Again."],
    },
    # ---- race -----------------------------------------------------------------------------------
    "after_lap_one_gained": {
        "calm": ["P{pos} after lap one. Good start, gained {n}.", "Nice start, {me}. P{pos}, up {n}."],
        "fired_up": ["What a start! P{pos}, up {n}!", "Great launch, {me}! P{pos}!"],
        "dry": ["P{pos}. Up {n}. Someone's been practising starts."],
    },
    "after_lap_one_lost": {
        "calm": [
            "P{pos} after lap one. Lost {n}, long race, stay calm.",
            "P{pos}. Lost a couple, we'll get them back.",
        ],
        "fired_up": ["P{pos}, lost {n}. Plenty of time, let's fight back!"],
    },
    "after_lap_one_same": {
        "calm": ["P{pos} after lap one. Good, settle in.", "Held position, P{pos}. Settle in."],
    },
    "drs_range": {
        "calm": ["You're within a second of {name}.", "Inside a second to {name}. Go get them."],
    },
    "gained_position": {
        "calm": ["Nice move. P{pos}.", "Good job, P{pos}.", "P{pos}. Well done.", "Good move. P{pos}, keep it going."],
        "fired_up": ["Yes! P{pos}! Great move!", "Get in! P{pos}!"],
        "dry": ["P{pos}. Tidy.", "That'll do. P{pos}."],
    },
    "lost_position": {
        "calm": [
            "{name} is through. P{pos}.",
            "Lost P{old} to {name}. Stay with them.",
            "{name} got by. P{pos}. Stay close, you'll get a run.",
        ],
        "fired_up": ["{name} got you. P{pos}. Let's get it back!"],
    },
    "car_ahead_pitted": {
        "calm": ["{name} has boxed from ahead. Push now, this is your chance.", "{name} pitting. Clear air, push."],
        "fired_up": ["{name} boxed! Push, push, push!"],
    },
    "car_behind_pitted": {
        "calm": [
            "{name} behind has boxed. Undercut threat, we need to respond soon.",
            "{name} pitted behind. Watch the undercut.",
        ],
    },
    "pit_window_soon": {
        "calm": [
            "Pit window opens in {n} laps. Ideal lap is {ideal}.",
            "Box window is lap {ideal}, {n} laps from now.",
        ],
    },
    "box_now": {
        "calm": [
            "Box this lap, box this lap.",
            "Box, box. Box this lap.",
            "Box box, {me}. In this lap.",
            "Okay {me}, box this lap. Box this lap.",
        ],
        "fired_up": ["Box, box, box! This lap!"],
    },
    "pit_stop_time": {
        "calm": ["Good stop, {seconds} seconds. Out in P{pos}.", "{seconds} second stop. You're P{pos}."],
        "fired_up": ["Great stop! {seconds} seconds, P{pos}!"],
    },
    "pit_stop_done": {
        "calm": [
            "Good stop, {seconds} seconds. Out in P{pos}.",
            "{seconds} second stop. You're P{pos}. Tyres need a lap.",
        ],
        "fired_up": ["Great stop! {seconds} seconds, P{pos}! Get them switched on!"],
    },
    "laps_to_go": {
        "calm": ["{n} laps to go.", "{n} to go, {me}.", "{n} laps remaining. Keep it clean."],
        "fired_up": ["{n} laps to go, {me}. {n} of your finest!", "{n} to go! Let's bring it home strong!"],
        "dry": ["{n} laps to go. {n} of your finest, if you have them."],
    },
    "last_lap": {
        "calm": ["Last lap, last lap. Bring it home.", "Final lap, {me}. Keep it clean."],
        "fired_up": ["Last lap! Bring it home, {me}!"],
        "dry": ["Last lap. Please don't do anything interesting."],
    },
    "finish_win": {
        "calm": ["P1! That's a win, {me}! Fantastic job!", "You've won it, {me}! P1! What a drive!"],
        "fired_up": ["P1! P1! Get in there! What a race, {me}!", "Yes! You've won it! Get in there, {me}!"],
        "dry": ["P1. That's a win. I'm told I'm allowed to be excited. Get in there!"],
    },
    "finish_podium": {
        "calm": [
            "P{pos}! That's a podium, {me}. Great job.",
            "Podium, P{pos}. Well done, {me}.",
            "P{pos}, not a bad day. You earned your money today, mate.",
        ],
        "fired_up": ["P{pos}! Podium! Brilliant drive, {me}!"],
    },
    "finish_points": {
        "calm": [
            "P{pos}, {me}. Points. Good job.",
            "That's P{pos}. Solid points today, well done mate.",
            "Chequered flag, P{pos}. Good management out there.",
        ],
        "fired_up": ["P{pos}! Good points, {me}!", "P{pos}! That's a great drive, {me}!"],
        "dry": ["P{pos}. Points. The accountants will be thrilled."],
    },
    "finish_no_points": {
        "calm": [
            "P{pos}. Tough one, {me}. We'll go through it.",
            "P{pos}. Not our day. We'll review.",
            "That's P{pos}. I know it's frustrating. You drove well with what we had.",
        ],
        "dry": ["P{pos}. We'll call that character building."],
    },
    "finish_damage_limitation": {
        "calm": [
            "Damage limitation today, but we'll take the points.",
            "Not the day we wanted, but good damage limitation.",
        ],
    },
    "finish_gained": {
        "calm": ["Up {n} from the grid.", "{n} places gained today.", "That's {n} places from where we started."],
    },
    "finish_lost": {
        "calm": [
            "Tough to lose those places. Head up, we go again next time.",
            "We'll get those places back next time.",
        ],
    },
    "fastest_lap_me": {
        "calm": ["Fastest lap of the race. Nice.", "That's fastest lap, {me}."],
        "fired_up": ["Fastest lap! Purple!"],
    },
    "retirement_near": {
        "calm": ["{name} is out of the race.", "{name} has retired. One less to worry about."],
    },
    "overtake_available": {
        "calm": [
            "Overtake available.",
            "You've got overtake mode, {name} is within range.",
            "Overtake is on. Press and hold.",
            "Overtake available. Press and hold when you're ready.",
        ],
        "fired_up": ["Overtake is on! Use it!"],
    },
    "battery_low": {
        "calm": [
            "Battery's low. Harvest where you can.",
            "Battery low, recharge mode on the straights.",
            "Recharge on, please. Battery's low.",
        ],
    },
    # ---- laps & sectors -------------------------------------------------------------------------
    "lap_pb": {
        "calm": ["{time}. That's a personal best.", "Good lap, {time}. Best so far.", "{time}, new best."],
        "fired_up": ["{time}! Personal best, mega!", "Purple for you, {time}! Great lap!"],
        "dry": ["{time}. Best so far. Keep that up."],
    },
    "lap_provisional_pole": {
        "calm": ["{time}. Provisional pole, {me}.", "{time}, that's P1. Good lap."],
        "fired_up": ["{time}! Provisional pole! Mega lap, {me}!"],
    },
    "lap_so_close_to_pole": {
        "calm": [
            "P2, just {delta} to {name}. So tight. Really good effort.",
            "P2, {delta} off pole. That's tight, {me}.",
        ],
    },
    "lap_pb_session_best": {
        "calm": ["{time}! Fastest of anyone. P1.", "{time}. That's quickest overall."],
        "fired_up": ["{time}! P1! Fastest of everyone! Mega lap, {me}!"],
    },
    "lap_slower": {
        "calm": [
            "{time}. {delta} off your best, mostly sector {sector}.",
            "{time}, {delta} down. Lost it in sector {sector}.",
        ],
        "dry": ["{time}. {delta} off. Sector {sector} is where it went."],
    },
    "lap_slower_corner": {
        "calm": ["{time}. {delta} off. {finding}", "{time}, {delta} down. {finding}", "{time}. {finding}"],
    },
    "lap_close": {
        "calm": ["{time}. Just {delta} off your best.", "{time}. Very close, {delta} off."],
    },
    "lap_position": {
        "calm": [
            "That's P{pos}, {delta} off {name}.",
            "P{pos}, {delta} behind {name}.",
            "Currently P{pos}, {delta} to {name}.",
        ],
    },
    "purple_sector": {
        "calm": ["Purple sector {sector}.", "Sector {sector}, purple."],
        "fired_up": ["Purple sector {sector}! Keep going!"],
    },
    "long_run_summary": {
        "calm": [
            "That run averaged {avg}, deg about {deg} a lap. Good data.",
            "Average {avg} on that run. Tyres held up, {deg} a lap.",
        ],
    },
    # ---- car ------------------------------------------------------------------------------------
    "wing_damage": {
        "calm": ["We have front wing damage, {side}, {pct} percent.", "Front wing damage, {side} side. {pct} percent."],
    },
    "wing_damage_box": {
        "calm": [
            "Heavy front wing damage. Box this lap for a new nose.",
            "Front wing's badly damaged. Box, box for a new wing.",
        ],
    },
    "wing_damage_practice": {
        "calm": [
            "Front wing damage, {side} side, {pct} percent. Bring it in, we'll change the nose.",
            "That's {pct} percent on the {side} front wing. Box this lap, new wing.",
        ],
        "dry": ["Front wing's {pct} percent missing. Bring what's left back to the garage, please."],
    },
    "floor_damage": {
        "calm": [
            "We've got {part} damage, {pct} percent. We'll see what it costs.",
            "{part} damage, {pct} percent. Car's still okay to continue.",
        ],
    },
    "puncture": {
        "calm": ["Puncture, {corner}! Box, box, box.", "You've got a puncture, {corner}. Bring it in, box."],
    },
    "tyres_overheating": {
        "calm": [
            "{axle} running hot. Manage the {where}.",
            "{axle} are overheating. Look after them in the {where}.",
        ],
    },
    "tyres_cold": {
        "calm": ["Tyres are cold, {axle}. Get some temperature in.", "{axle} still cold. Push to warm them."],
    },
    "tyre_wear": {
        "calm": ["Tyres at {pct} percent wear.", "Wear is {pct} percent, {worst} is the worst."],
    },
    "tyre_wear_high": {
        "calm": [
            "Tyres at {pct} percent. They're going off, {me}. We should box soon.",
            "{pct} percent wear. Tyres are done, think about boxing.",
        ],
    },
    "brakes_hot": {
        "calm": ["Brakes running hot, {corner}. Lift and coast if you can.", "{corner} brake is hot. Manage it."],
    },
    "fuel_short": {
        "calm": [
            "We need to save fuel. About {laps} laps short. Lift and coast.",
            "Fuel is marginal, {laps} short. Lift and coast into the big stops.",
        ],
    },
    "fuel_ok": {
        "calm": ["Fuel's good. No saving needed.", "Fuel's fine, push if you need to."],
    },
    "ers_fault": {
        "calm": ["We have an ERS fault. Keep going, we'll manage it."],
    },
    "drs_fault": {
        "calm": ["DRS fault. You'll have to do without it."],
    },
    "engine_damage": {
        "calm": ["We're seeing engine damage, {pct} percent. Short shift where you can."],
    },
    # ---- weather --------------------------------------------------------------------------------
    "rain_coming": {
        "calm": [
            "Rain expected in about {minutes} minutes, {pct} percent chance.",
            "Radar shows rain in {minutes} minutes. {pct} percent.",
            "Possible rain in {minutes} minutes, {pct} percent. We'll keep you updated.",
        ],
    },
    "rain_started": {
        "calm": [
            "It's starting to rain. {intensity}.",
            "Rain now, {intensity}. Careful on the painted lines.",
            "Rain's arrived, {intensity}. Kerbs and white lines will be slippery.",
        ],
    },
    "rain_stopping": {
        "calm": ["Rain is stopping. Track will dry from here.", "Rain's easing off. Watch for a drying line."],
    },
    "wrong_tyres_wet": {
        "calm": [
            "Track's wet, slicks won't work. Box for inters.",
            "It's getting wet. Inters are the tyre, box this lap.",
        ],
    },
    "wrong_tyres_dry": {
        "calm": ["Track's drying. Slicks will be quicker soon.", "Dry line is forming. We're looking at slicks."],
    },
    # ---- strategy ---------------------------------------------------------------------------------
    "plan_a_target": {
        "calm": [
            "Still plan A, target lap {lap}. Tyres look good, happy to push.",
            "Currently thinking plan A, target lap {lap}. Deg is fine.",
        ],
    },
    "tyres_to_the_end": {
        "calm": [
            "Target is to get to the end on this tyre. They're holding up, happy to push.",
            "These will go to the end. Happy for you to push on them.",
        ],
    },
    "rhythm": {
        "calm": [
            "Okay, let's get back in the rhythm.",
            "Shake it off. Back into the rhythm, please.",
            "That one's gone. Let's get back in the rhythm, {me}.",
        ],
        "dry": ["Let's not do that again. Back in the rhythm."],
    },
    "nothing_to_prove": {
        "calm": [
            "Gap is {gap}, {me}. You've got nothing to prove. Look after the car and bring it home.",
            "{gap} in hand. No risks now, nothing to prove. Bring it home.",
        ],
        "fired_up": ["{gap} in hand! Nothing to prove, {me}, just bring it home!"],
    },
    "tyre_offset_attack": {
        "calm": [
            "Big offset on tyres to {name}, {n} laps fresher. You might have some fun towards the end.",
            "We're {n} laps fresher than {name}. He'll be struggling, go get him.",
        ],
        "fired_up": ["{n} laps fresher than {name}! You're going to have some fun, {me}!"],
    },
    "tyres_robust": {
        "calm": [
            "These tyres are robust, happy to push on them.",
            "Deg is under control. Happy for you to push.",
            "Tyres are holding up well. You can lean on them.",
            "Still plan A. Tyres look good, push when you need to.",
        ],
        "fired_up": ["Tyres are mega, {me}! Push on them!", "Deg's good, you can go for it!"],
        "dry": ["Tyres are fine. Better than fine. Push."],
    },
    "plan_b": {
        "calm": [
            "Tyres are going off quicker than planned. Thinking plan B, about {n} laps left in them.",
            "Deg is higher than we expected. We're looking at plan B, maybe {n} laps on these.",
            "Okay {me}, thinking plan B. Plan B. These tyres have about {n} laps.",
        ],
        "fired_up": ["Deg's biting, {me}. Plan B, about {n} laps left on these!"],
    },
    "plan_extend": {
        "calm": [
            "Tyres are holding up. Plan A plus {n}, we'll go a bit longer.",
            "We're going longer. Plan A plus {n}. Keep the pace up.",
        ],
        "fired_up": ["Tyres are still good! Plan A plus {n}, keep pushing!"],
    },
    "sc_window": {
        "calm": [
            "Just for info, we're in the window for a safety car. If it comes out, we box.",
            "We're in the safety car window now. Safety car or VSC, we're coming in.",
            "Safety car window is open. If it comes out, we box.",
        ],
    },
    "box_under_sc": {
        "calm": [
            "We're in the window. Box this lap, box this lap. Cheap stop.",
            "Box, box under the safety car. Box this lap.",
        ],
        "fired_up": ["Box, box, box! Safety car, free stop! In this lap!"],
    },
    "box_under_vsc": {
        "calm": ["VSC, we're in the window. Box this lap, box this lap.", "Box under the VSC. Box this lap."],
    },
    "new_tyres": {
        "calm": [
            "New {tyre} on. Give them a lap to come in.",
            "That's new {tyre}. Build the temperature, then they're yours.",
            "On the {tyre} now. Careful for a lap, they'll be cold.",
        ],
    },
    "sc_cold_tyres": {
        "calm": [
            "Tyres are on the cold side. Keep weaving, get the temperature in.",
            "Temps are dropping behind the safety car. Weaving is good, keep it up.",
        ],
    },
    "ask_conditions": {
        "calm": [
            "Update on the conditions when you can. Grip and visibility.",
            "Any comments on the conditions, {me}?",
        ],
    },
    "rival_yet_to_stop": {
        "calm": [
            "{name} ahead is yet to stop, {age} laps on the {tyre}. He has to come in.",
            "Just for info, {name} hasn't stopped yet. {age} lap old {tyre}.",
            "{name}, {gap}, still to stop. His {tyre} are {age} laps old.",
        ],
    },
    "rival_going_long": {
        "calm": [
            "{name} still out on {age} lap old {tyre}. We think he's trying to go to the end.",
            "{name} hasn't stopped, {age} laps on the {tyre}. Looks like he's one-stopping to the end.",
        ],
    },
    "ask_tyres": {
        "calm": ["Feedback on this tyre when you can.", "How are the tyres feeling, {me}?", "How's the tyre?"],
    },
    "ask_balance": {
        "calm": [
            "How's the balance?",
            "How's the car feeling, {me}? Any balance issues?",
            "Feedback on the balance when you're ready.",
        ],
    },
    "feedback_ack": {
        "calm": [
            "Copy, thanks. Noted.",
            "Copy that. Understood.",
            "Copy. We'll look at that.",
            "Okay, copy. Thanks {me}.",
        ],
        "dry": ["Copy. Writing it down. In pen."],
    },
    # ---- encouragement --------------------------------------------------------------------------
    # ---- radio system ---------------------------------------------------------------------------
    "stand_by": {"calm": ["Stand by.", "Stand by one."]},
    "didnt_catch": {
        "calm": ["Say again, {me}? Didn't catch that.", "Sorry, say again?", "Radio's broken up, say again?"],
    },
}


# ---- spoken-radio rewrite --------------------------------------------------------------------------
# Real engineers clip: name first, number next, verb only if needed, the driver's name rarely. These
# replace the calm lines that read like a script when spoken.
_CLIPPED: dict[str, list[str]] = {
    "greet_practice": [
        "Radio check, {me}. {session}, track's {track_temp} degrees. Build into it.",
        "Hey {me}, radio check. {track}, usual plan. Build the pace, feedback when you can.",
        "Radio check. {session} at {track}. Take your time on the out lap.",
    ],
    "greet_race": [
        "Radio check, {me}. {laps} laps, starting P{grid}. Clean into turn one.",
        "Okay {me}, P{grid}, {laps} laps. Look after the tyres early.",
    ],
    "greet_quali": [
        "Radio check, {me}. {minutes} minutes. Let's get a banker in early.",
        "Okay, qualifying. Track's {track_temp} degrees. Let's get it done.",
    ],
    "time_left": ["{minutes} minutes left.", "{minutes} minutes to go."],
    "quali_safe": ["P{pos}, you're safe.", "P{pos}. That's enough for now."],
    "session_end_practice": [
        "That's the session. {best}, P{pos}. Good work.",
        "Session's done. Best was {best}, P{pos}.",
    ],
    "behind_close": [
        "{name} right behind, push lap. Let him through.",
        "Mirrors, {name} on a push lap. Let him by.",
        "{name} right behind you, he's on it. Stay off the line.",
    ],
    "ahead_slow": [
        "Traffic ahead, {name} on {kind}, {gap}.",
        "{name} ahead on {kind}, {gap}. He knows you're coming.",
        "Slow car ahead, {name}, {gap}.",
    ],
    "vsc": ["VSC, VSC. Stay positive to the delta.", "Virtual safety car. Positive delta."],
    "green_flag": ["Green, green, green.", "And we're green. Go."],
    "after_lap_one_gained": ["P{pos}. Good start, up {n}.", "Nice start. P{pos}, up {n}."],
    "after_lap_one_lost": ["P{pos}. Lost {n}, long race. Stay calm.", "P{pos}. We'll get them back."],
    "after_lap_one_same": ["P{pos}. Good, settle in.", "Held position, P{pos}. Settle in."],
    "car_ahead_pitted": ["{name} has boxed. Clear air, push now.", "{name} pitting. This is your chance, push."],
    "car_behind_pitted": ["{name} behind has boxed. Undercut threat.", "{name} pitted behind. Watch the undercut."],
    "pit_window_soon": [
        "Box window opens in {n} laps. Target lap {ideal}.",
        "Window opens in {n} laps, we're looking at lap {ideal}.",
    ],
    "lap_slower": ["{time}. {delta} off, sector {sector}.", "{time}, {delta} down. Lost it in sector {sector}."],
    "lap_position": ["P{pos}, {delta} off {name}.", "That's P{pos}, {delta} to {name}."],
    "long_run_summary": [
        "Good run. Average {avg}, deg about {deg} a lap.",
        "That run averaged {avg}. Deg {deg} a lap.",
    ],
    "wing_damage": ["Front wing damage, {side} side, {pct} percent.", "Front wing damage, {side} side. {pct} percent."],
    "floor_damage": [
        "{part} damage, {pct} percent. We'll keep an eye on it.",
        "{part} damage, {pct} percent. Car's okay, keep going.",
    ],
    "tyres_overheating": [
        "{axle} are running hot. Manage them in the {where}.",
        "{axle} hot. Look after them in the {where}.",
    ],
    "tyre_wear": ["Tyres at {pct} percent.", "Wear's {pct} percent, {worst} the worst."],
    "tyre_wear_high": [
        "{pct} percent wear. They're going off, box soon.",
        "Tyres are done, {pct} percent. Think about boxing.",
    ],
    "fuel_ok": ["Fuel's fine, no saving.", "Fuel's good, push if you need to."],
    "plan_a_target": ["Plan A, target lap {lap}. Tyres look good.", "Still plan A, lap {lap}. Deg's fine."],
    "tyres_to_the_end": ["These will go to the end. Happy to push.", "Tyres will make the end. Push on them."],
    "tyres_robust": [
        "These tyres are robust, happy to push on them.",
        "Deg's under control. Happy for you to push.",
        "Tyres look good. Lean on them.",
    ],
    "plan_b": [
        "Deg's higher than we thought. Thinking plan B, about {n} laps on these.",
        "Thinking plan B. Plan B. Maybe {n} laps left in these.",
    ],
    "pit_stop_done": ["Good stop, {seconds} seconds. P{pos}.", "{seconds} second stop. P{pos}. Tyres need a lap."],
}
for _key, _lines in _CLIPPED.items():
    PHRASES.setdefault(_key, {})["calm"] = _lines
