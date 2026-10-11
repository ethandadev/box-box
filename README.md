# Box Box 📻

An AI race engineer for **F1 25 with the 2026 Season Pack**. It reads the game's live telemetry for every car,
works out what matters, and talks to you over the radio the way a real engineer does: short, timed for the
straights, and only when it matters. Hold a button on your wheel to talk back.

> Status: **M0, the telemetry spine.** It decodes everything, tracks the session, records and replays, and makes
> its first radio calls (out-lap traffic in practice and quali) as text. Voice, push-to-talk and the LLM brain come next.
> See [docs/PLAN.md](docs/PLAN.md) for the full plan.

## Setup (Windows PC)

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then in this folder run:
   ```powershell
   uv sync
   ```
2. In F1 25, go to **Settings → Telemetry Settings**:

   | Setting | Value |
   |---|---|
   | UDP Telemetry | On |
   | UDP Broadcast Mode | Off |
   | UDP IP Address | 127.0.0.1 |
   | UDP Port | 20777 |
   | UDP Send Rate | 20Hz (plenty for the engineer; keeps recordings small) |
   | UDP Format | **2026** (2025 also works, without the 2026 data) |

3. Allow Python through Windows Firewall the first time it asks (private networks are enough).

## Use

```powershell
uv run box-box watch                 # live: status line + the engineer's radio calls
uv run box-box record                # save the session to recordings\<timestamp>.f1raw.gz (Ctrl+C stops)
uv run box-box watch -f recordings\x.f1raw.gz -s 4    # what would he have said? (4x speed)
uv run box-box replay recordings\x.f1raw.gz           # resend a recording over UDP as if the game were running
```

**Please record a few sessions.** One practice session with traffic around you and one full race give the
best test data for tuning the engineer. Then the engineer can be developed without the game running.
Recordings are compressed (about 1 MB per minute at 20Hz). Share them by committing them:

```powershell
git add recordings
git commit -m "Add recordings"
git push
```

Example radio calls from a practice out-lap:

```
📻 [STRATEGY] Verstappen behind on a push lap, 8 seconds.
📻 [SAFETY] Verstappen is on it, right behind. Stay off the line.
📻 [STRATEGY] Traffic ahead, Alonso on an out lap, 6 seconds.
```

## Layout

```
src/box_box/
  telemetry/   packets.py (decoder, formats 2025 + 2026, all cars) · rawfile.py · sources.py (UDP / file) · names.py
  state/       model.py (World: the live session) · track.py (on-road geometry, wrap-aware)
  engineer/    radio.py (RadioCall, priorities, phrasebook) · traffic.py (out-lap / push-lap traffic)
tests/         real-recording fixtures + a byte-level packet builder for scenario tests
```

## Development

```powershell
uv run pytest
uv run ruff check . ; uv run ruff format .
```

## Credits

UDP layouts follow EA's *F1 25: 2026 Season Pack UDP Specification*, cross-checked against
[MacManley/f1-26-udp](https://github.com/MacManley/f1-26-udp) and
[rdgonzaga/f1-game-telemetry](https://github.com/rdgonzaga/f1-game-telemetry) (both MIT). The `.f1raw` recordings
in `tests/fixtures/` come from the latter. See `tests/fixtures/NOTICE.md`.
