# Centennial KAPA — Airport Simulator

A single-file Three.js airport simulation built around a stylized Centennial Airport (KAPA) layout. Three runways, parallel and crosswind operations, procedural pavement, OSM-derived buildings, and live ADS-B traffic are sequenced by a tower decision loop. Airborne tracks follow OpenSky; landings are handed to the sim’s taxi-in loop when a stand is free. A five-aircraft scripted fleet and two helicopters exist only with `?live=0`. Clearances and pilot calls are spoken locally through Kokoro-82M, with Piper retained as a fallback, and proxied by the bundled web server.

This is a visual simulation, not a navigation or flight-planning tool.

## Build and Run at Home

There is no frontend build step and no Node/npm install. The simulator is static HTML and JavaScript; `serve.py` serves the files and provides the local TTS and Ollama proxy endpoints.

### Prerequisites

- `git`
- Python 3.10 or newer; Python 3.12 is recommended for the Kokoro runtime
- [`uv`](https://docs.astral.sh/uv/) for the isolated Kokoro environment
- Internet access for Three.js and the aircraft GLBs loaded from CDNs
- Optional: [Ollama](https://ollama.com/) for local-model ATC decisions; rules mode works without it

### Clean checkout

```bash
git clone https://github.com/mbrewski/airport-sim.git
cd airport-sim

uv venv --python 3.12 .kokoro-venv
uv pip install --python .kokoro-venv/bin/python kokoro-onnx

mkdir -p voices/kokoro
curl -L -o voices/kokoro/kokoro-v1.0.int8.onnx \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.int8.onnx
curl -L -o voices/kokoro/voices-v1.0.bin \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin
```

`serve.py` detects the local `.kokoro-venv` and re-executes itself inside it automatically. The model files are only needed for spoken ATC; the traffic simulation still runs without them.

### Optional local model

```bash
ollama serve
ollama pull qwen3.5-9b-64k
```

The default model is `qwen3.5-9b-64k:latest`. The browser calls `/api/chat`, which `serve.py` proxies to `http://127.0.0.1:11434`. If Ollama is not running, append `?ai=rules` to skip model startup and use the deterministic rule engine.

### Start the simulator

```bash
python3 serve.py
```

Open `http://localhost:8123/airport-sim.html`. Replace `localhost` with the machine's LAN IP to use another device on the network. The server listens on `0.0.0.0` by default; set `PORT=8080 python3 serve.py` to use a different port.

On this host it also runs as a user systemd unit (`~/.config/systemd/user/airport-sim.service`) with `Restart=always` and linger enabled, so it starts at boot:

```bash
systemctl --user status airport-sim
systemctl --user restart airport-sim
```

Opening the HTML directly with `file://` will not work because the simulator imports ES modules and calls the local TTS/chat endpoints. For a fast offline-style demo without Ollama, use:

```text
http://localhost:8123/airport-sim.html?scenario=fixed&ai=rules&live=0
```

## Airfield Layout

The pavement, taxiway centerlines, apron polygons, building footprints, and helipads come from OpenStreetMap data for Centennial Airport, generated into `kapa-layout.js`. The scene is flat and uses local metres:

- `+X` is east.
- `+Z` is north.
- `+Y` is up.
- The origin is near the `17L` threshold.

| Runway | Length | Width | Ends | Controller |
| --- | ---: | ---: | --- | --- |
| `17L/35R` | 3,049 m | 30 m | `17L`, `35R` | Tower South |
| `17R/35L` | 2,135 m | 23 m | `17R`, `35L` | Tower North |
| `10/28` | 1,462 m | 23 m | `10`, `28` | Tower South |

All six runway ends have their own hold pad, connector, lineup route, departure path, and approach path. Two pairs support parallel operations:

- `17L` and `17R` can be active together.
- `35R` and `35L` can be active together.
- `10` and `28` are used as single crosswind runways.

The visible ground is a 28 km square (camera far clip 42 km). The paved field is about 3.1 × 4.9 km. Trees ring the airport from 2.4 km to 13 km. Live ADS-B is accepted out to 20 km.

The field includes the OSM taxiway network, the main apron and gate lane at `z = -245`, hangar rows, the terminal area, and the Heliplex helipads. Runway numbers, threshold bars, aiming bars, centerlines, edge lights, approach lights, taxiway lights, apron lamps, hold-short markings and signs, apron edge markings, hangar door panels, window bands, roof lines, and vents are generated procedurally.

The control tower, fuel store, fuel pump, radar dish, windsock, PAPI, and one hold-short sign are reused from the supplied low-poly GLB. The GLB's original runway, buildings, static aircraft, and fencing are removed before those props are repositioned for KAPA. Three tugs and three fuel bowsers run small service loops on the west, center, and east apron, clear of the gate lane.

## Runway Selection

Weather chooses one primary runway from `17L`, `35R`, `10`, and `28`, then adds a parallel mate when applicable. Wind is reported in degrees from the field so the HUD heading and runway selection agree.

- `?scenario=random` picks a runway plan and randomizes aircraft states.
- `?scenario=fixed` uses wind `185°` at `8 kt`, with `17L` and `17R` active.

Live KAPA METAR drives the wind by default. `serve.py` proxies `/api/weather` from the Aviation Weather Center. The simulator polls about once a minute, writes direction and speed, selects runways the same way as a scripted shift, and turns the windsock. Variable (`VRB`) reports keep the current heading and only update speed. A change waits if anyone is taxiing, lining up, taking off, landing, on approach, or occupying a runway. Airborne traffic assigned a now-closed runway is cleared to request the new active set. Tower announces a real change (heading ≥ 8°, speed ≥ 3 kt, or a runway swap). Append `?weather=0` to use the old scripted 75–150 second random shifts instead. `window.airport.shiftWind()` still forces a scripted shift from the console when live METAR is off.

Each active runway has its own occupancy. Pavement is hot only for lineup, takeoff, landing roll, or an active crossing. One approach is allowed per runway. An arrival still in the pattern does not block a departure; short final (about 900 m or below 130 m) does. Waiting to cross is not occupancy. Inbound traffic still has first refusal of a free runway. Takeoff rotation is by speed (about 0.7 Vr after 140 m), not a fraction of the 3 km runway. Vacating taxi-in traffic yields only to a takeoff or landing roll, not to an aircraft holding short.

Taxi routes to `17R` and `35L` cross the active north-south runway. Aircraft stop at the hold-short point, request a crossing, and wait for a `CLEAR_CROSS` clearance before entering the runway. A crossing aircraft occupies the runway until it clears the far side, so departures and arrivals cannot use that runway during the crossing.

## ATC Voice

Kokoro-82M uses `am_michael` for Tower South and the deeper `am_onyx` for Tower North. South handles `17L`, `35R`, `10`, and `28`; North handles `17R` and `35L`. A second male controller is used only for the north-side runways. Both feeds share one transmission queue, so clearances never talk over each other.

Each aircraft also has a distinct pilot voice. Pilots call the tower with taxi, departure, approach, and crossing requests, then read back the controller's clearance in the same voice. The default pilot cast is `af_sarah`, `am_liam`, `af_bella`, `am_echo`, `af_nova`, `am_fenrir`, and `af_nicole`, assigned consistently across the five fixed-wing aircraft and two helicopters.

Piper `1.2.0` remains installed at `~/.local/piper/piper` with `voices/en_US-ryan-high.onnx` as a fallback. If Kokoro fails, or if `TTS_ENGINE=piper` is set, `serve.py` uses Piper instead. `TTS_ENGINE=kokoro` is the default. `KOKORO_VOICE`, `KOKORO_NORTH_VOICE`, `KOKORO_PILOT_VOICES`, and `KOKORO_SPEED` can override the south voice, north voice, comma-separated pilot cast, and rate. `/api/tts` accepts `voice: "south"`, `voice: "north"`, or `voice: "pilot-1"` through `voice: "pilot-7"`; `/api/health` reports the active engine and voice lists.

Browsers require a gesture before audio playback. Tap once anywhere to unlock ATC voice; the button changes from `ATC voice: tap` to `ATC voice on`, and the unlock step plays a radio check for each controller. Tap again to mute. Every `CLEAR_TAXI`, `CLEAR_TAKEOFF`, `CLEAR_APPROACH`, `CLEAR_CROSS`, and `HOLD` decision is spoken with the pilot request and readback surrounding the controller clearance; `MONITOR` stays silent.

The server caches WAV files in `/tmp/airport-sim-tts`. The cache key includes the selected voice, so controller and pilot versions of the same phrase are stored separately. Callsigns use NATO phonetics and runway numbers use spoken digits before synthesis. The browser plays returned WAVs through one reusable audio element with serialized playback.

## Live ADS-B traffic

Live mode is on by default when `serve.py` is running. The simulator polls `/api/traffic`, which proxies OpenSky around KAPA (about 8 nm, cached four seconds). Airborne tracks below about 9,000 ft AGL and slower than airliner cruise are spawned and followed in local metres (origin at the `17L` threshold). Labels on those ships read `ADS-B`. The HUD brand line reports the last poll (`KAPA / Live ADS-B · N ads-b`).

Descending tracks toward the field are handed to the sim immediately (orbit at current altitude, inbound call). Tower then sequences `CLEAR_APPROACH`; the approach starts from present range and does not fly back out to a 2 km final. That arrival occupies the runway in the snapshot, but the physical reservation (`holdsRunway`) starts at landing so the same tail is not told to hold short of itself. Overflights and climbers still follow ADS-B until they leave coverage. HOLD on an aircraft already on approach, landing, lining up, or taking off is rejected. Airborne holds use “extend downwind,” not hold-short. If they were never cleared, a strip-only landing handoff still applies when the track is on the pavement. A full ramp fades the arrival instead. Once handed off, that hex stays one object through landing, taxi, and parking so noisy `on_ground` flags cannot respawn it with a new voice. Inbound calls for a tail are limited to once per two minutes. The pilot voice is pinned to the Mode S hex. Live tails that park are retired after a short dwell so the next arrival can use the gate. A real airborne track after parking starts a new follow.

Live mode does not spawn the invented startup five (`N5520T`, `N731TW`, `N442LB`, `N908J`, `N117CT`) or the two scripted helicopters. The field starts empty until OpenSky reports local traffic. Append `?live=0` for the scripted fleet. OpenSky is anonymous and rate-limited; a failed poll keeps the last good snapshot. Restart `serve.py` after pulling so `/api/traffic` is available.

## Simulation

Five fixed-wing aircraft follow this state machine:

```text
PARKED -> TAXI_OUT -> HOLD_SHORT -> LINEUP -> TAKEOFF
       -> AIRBORNE -> APPROACH -> LANDING -> TAXI_IN -> PARKED
```

Ground routes follow the taxi polyline, not the aircraft heading, so they stay on the painted lane even at 4×. Straight-lane taxi is about 19 m/s, with slower gate crawl and landing-exit speeds. Aircraft settle precisely on hold pads and aprons. Taxi also locks 20 m cells and the next waypoint, and a plane holds if another ground aircraft is within about 24 m or ahead in a 42 m cone. Inbound has right of way over outbound; everyone yields to hold-short, lineup, landing, and takeoff. Two lineups yield by id. `CLEAR_TAKEOFF` is accepted only when the aircraft is at that runway’s hold pad, so a 17L hold cannot be sent across 17L for a 17R departure. Only one runway crossing is cleared at a time. Airborne routes use a turn-radius-based capture gate so fast aircraft cannot orbit a waypoint they cannot make. Takeoff rotation is at about 70% of takeoff speed after ~140 m of roll; they lift off once they are 14 m up. After landing they brake hard and vacate by ~280 m (or 18% of runway length). Touchdown is in the first 70 m; a long high final goes around. A landing roll that starts short of the threshold is pulled onto the pavement.

The decision loop runs every ten seconds and can use rules, Ollama, or an OpenAI-compatible endpoint. Snapshot data includes wind, active runways, pavement occupancy, short-final flags, and requests. A runway is hot only for lineup, takeoff, landing, or an active crossing. An arrival still in the pattern does not block a departure; short final (~1 km / low) does. One approach is allowed per runway. A taxi waiting to cross does not occupy the runway. Takeoff rotation is speed-based (rotate ~70% of Vr after ~140 m), not 60% of runway length. Decisions are validated against those rules. The HUD log shows tower phrasing. Model rejections stay in the browser console.

If twenty seconds pass with no approach or takeoff, Tower South and Tower North get a hot-mic exchange from Ollama (`HOT MIC` in the log, spoken if ATC voice is on). Each round is a new prompt so the jokes do not repeat on a loop. `window.airport.banterNow()` forces one.

Click a decision-log row, or a callsign in the clearance bar, to chase that aircraft with the camera. Drag the field, Reset view, or Auto-orbit to drop the follow.

Two safety flows extend the fixed-wing sequence:

- Gate conflict handling: when an arrival's assigned stand is still occupied, it is reassigned to a free stand before the taxi-in route is built.
- Go-around handling: if another aircraft or an active crossing occupies the arrival runway on short final, the arrival climbs away, re-enters the pattern, and requests a new approach.

## Helicopter Operations

With `?live=0`, two helicopters operate independently of the fixed-wing ATC loop:

- `N911KA`, a medium twin-engine medevac helicopter, starts at the Heliplex pad.
- `N450KA`, a light turbine helicopter, starts at Pad 8.

Each helicopter spools its rotors, lifts off vertically, flies a local five-point circuit, approaches its pad, lands, and parks. The HUD reports helicopter count and airborne helicopters separately from fixed-wing traffic, and helicopters do not take runway clearances or consume fixed-wing runway reservations.

Startup is generated separately. A local plan picks the wind and runway, then the model may populate aircraft states while the runway plan remains enforced. If model startup fails, a fully local scenario is used.

Useful switches:

```text
airport-sim.html?ai=rules
airport-sim.html?ai=ollama
airport-sim.html?ai=openai
airport-sim.html?ai=ollama&model=qwen3.5-9b-64k:latest
airport-sim.html?scenario=random
airport-sim.html?scenario=fixed
airport-sim.html?live=0
airport-sim.html?weather=0
```

The endpoint and model defaults live in `AI_CONFIG` near the top of the module. Ollama is called through `/api/chat`, which `serve.py` proxies to `http://127.0.0.1:11434`.

## Rendering

The visual pass uses:

- Procedural hold-short signs, runway markings, apron markings, and hangar facade details.
- Two animated helicopters with rotor spin, vertical lift, and local circuit routes.
- A procedural `Sky` with a warm low sun and cool upper atmosphere.
- ACES filmic tone mapping, fog, and an elevated exposure for dusk legibility.
- A 4096 px directional shadow map covering the runway complex.
- Instanced runway lights, approach lights, taxiway lights, and tree line.
- A widened camera far plane and sky scale for the 3 km runway geometry.
- An elevated southeast home view framing the runway complex and apron.

## Files

- `airport-sim.html` — simulator, scene, simulation logic, HUD, and model adapter.
- `kapa-layout.js` — generated runway, taxiway, apron, building, and helipad geometry.
- `serve.py` — static server, Ollama proxy, Kokoro/Piper synthesis, OpenSky traffic, and KAPA METAR proxy.
- `.kokoro-venv/` — Python 3.12 runtime containing `kokoro-onnx`, `onnxruntime`, and `numpy`.
- `voices/kokoro/kokoro-v1.0.int8.onnx` — Kokoro-82M int8 speech model.
- `voices/kokoro/voices-v1.0.bin` — Kokoro voice pack.
- `voices/en_US-ryan-high.onnx` — Piper fallback voice model.
- `voices/en_US-ryan-high.onnx.json` — Piper voice configuration.
- `README.md` — this document.

## Validation

The simulator is tested with headless Chromium using SwiftShader:

```bash
node /tmp/opencode/kapa_smoke2.js
node /tmp/opencode/kapa_routes.js
node /tmp/opencode/kapa_10_cycle.js
node /tmp/opencode/kapa_35_cycle.js
node /tmp/opencode/kapa_28_cycle.js
node /tmp/opencode/kapa_long.js
node /tmp/opencode/kapa_geom.js
node /tmp/opencode/kapa_views.js
node /tmp/opencode/heli_cycle.js
node /tmp/opencode/crossing_debug_long.js
node /tmp/opencode/gate_test.js
node /tmp/opencode/goaround_test.js
```

These checks cover page load, console errors, route generation for all six runway ends, full departures and arrivals on `10`, `17L/17R`, `28`, and `35R/35L`, parallel operations, gate returns, runway crossings, go-arounds, helicopter circuits, finite positions, HUD state, and screenshots. The long fixed-scenario run exercised every state transition through parking with no runtime errors or abnormal aircraft positions. A live Ollama startup selected wind `206°`, activated `17L/17R`, and produced `CLEAR_TAKEOFF`, `CLEAR_TAXI`, and `MONITOR` decisions; malformed model replies fell back to rules without interrupting traffic. Visual screenshots were also checked with a local vision model for pavement gaps, markings, and framing.

The speech endpoint can be checked independently:

```bash
curl -s http://127.0.0.1:8123/api/health
curl -s http://127.0.0.1:8123/api/traffic | head
curl -s http://127.0.0.1:8123/api/weather
curl -s -H 'Content-Type: application/json' \
  --data '{"text":"Tower radio check.","voice":"north"}' \
  -o /tmp/tts-north.wav http://127.0.0.1:8123/api/tts
curl -s -H 'Content-Type: application/json' \
  --data '{"text":"Tower, N5520T, ready to taxi.","voice":"pilot-1"}' \
  -o /tmp/tts-pilot.wav http://127.0.0.1:8123/api/tts
```

## Controls

After the field loads, a popup lists the controls. **Got it** dismisses it.

- Mouse drag: orbit (cancels aircraft follow).
- Scroll: zoom.
- Click a decision-log entry or a clearance-bar callsign: chase that aircraft.
- Pause: freeze the sim clock.
- Speed buttons (scripted `?live=0` only): simulation time scale. Hidden in live ADS-B mode.
- Auto-orbit button: toggle camera orbit (cancels follow).
- ATC voice button: unlock, mute, or re-enable clearances.
- Reset view: return to the home camera and drop follow.
- AI badge: shows whether decisions come from rules, Ollama, or an OpenAI-compatible model.

`window.airport` exposes the scene, camera, orbit controls, aircraft, decision history, weather, renderer, startup scenario, voice state, live traffic and METAR status, `shiftWind()`, `focusAircraft(id)`, `banterNow()`, and `decideNow()` for debugging and automated tests.

## Attribution

- Airfield geometry derived from OpenStreetMap data © OpenStreetMap contributors, available under the ODbL.
- Aircraft, vehicles, and reusable field props from the `3dassets.dev` Aircraft Fleet & Airfield pack (CC0).
- Three.js and addons load from jsDelivr.

## Design Choices

- One HTML file keeps the simulator easy to share and inspect.
- OSM geometry provides real runway and taxiway proportions without a runtime map dependency.
- The supplied GLB is reduced to a small set of reusable props.
- The rule engine remains a deterministic offline fallback.
- The LLM is a decision layer, not part of the motion loop.
- Kokoro runs on the host and is served through the same web server.
