# Centennial KAPA — Airport Simulator

A single-file Three.js airport simulation built around a stylized Centennial Airport (KAPA) layout. Three runways, parallel runway operations, procedural pavement, OSM-derived buildings, a five-aircraft fixed-wing fleet, and two independent helicopters are sequenced by a tower decision loop. Clearances are spoken locally through Kokoro-82M, with Piper retained as a fallback, and proxied by the bundled web server.

This is a visual simulation, not a navigation or flight-planning tool.

## Run It

There is no frontend build step.

```bash
cd /home/mbrewski/develop/airport-sim
python3 serve.py
```

Open `http://localhost:8123/airport-sim.html`, or replace `localhost` with the machine's LAN IP. Opening the HTML directly with `file://` will not work because the simulator imports ES modules and calls the local TTS/chat endpoints.

`python3 serve.py` detects the local Kokoro runtime and re-executes itself inside `.kokoro-venv` automatically. The server serves the static files, proxies `POST /api/chat` to Ollama on `127.0.0.1:11434`, and synthesizes `POST /api/tts` in-process. Remote devices therefore only need a browser.

To rebuild the speech runtime from scratch:

```bash
uv venv --python 3.12 .kokoro-venv
uv pip install --python .kokoro-venv/bin/python kokoro-onnx
mkdir -p voices/kokoro
curl -L -o voices/kokoro/kokoro-v1.0.int8.onnx \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.int8.onnx
curl -L -o voices/kokoro/voices-v1.0.bin \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin
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

The field includes the OSM taxiway network, the main apron and gate lane at `z = -245`, hangar rows, the terminal area, and the Heliplex helipads. Runway numbers, threshold bars, aiming bars, centerlines, edge lights, approach lights, taxiway lights, apron lamps, hold-short markings and signs, apron edge markings, hangar door panels, window bands, roof lines, and vents are generated procedurally.

The control tower, fuel store, fuel pump, radar dish, windsock, PAPI, and one hold-short sign are reused from the supplied low-poly GLB. The GLB's original runway, buildings, static aircraft, and fencing are removed before those props are repositioned for KAPA.

## Runway Selection

Weather chooses one primary runway from `17L`, `35R`, `10`, and `28`, then adds a parallel mate when applicable. Wind is reported in degrees from the field so the HUD heading and runway selection agree.

- `?scenario=random` picks a runway plan and randomizes aircraft states.
- `?scenario=fixed` uses wind `185°` at `8 kt`, with `17L` and `17R` active.

Each active runway has its own traffic reservation. A departure or arrival on `17L` no longer blocks the parallel `17R`, while single-runway operations still serialize traffic. Inbound traffic has priority over departures, and a runway remains reserved until the arrival has exited.

Taxi routes to `17R` and `35L` cross the active north-south runway. Aircraft stop at the hold-short point, request a crossing, and wait for a `CLEAR_CROSS` clearance before entering the runway. A crossing aircraft occupies the runway until it clears the far side, so departures and arrivals cannot use that runway during the crossing.

## ATC Voice

Kokoro-82M uses `am_michael` for Tower South and `am_adam` for Tower North. South handles `17L`, `35R`, `10`, and `28`; North handles `17R` and `35L`. A second male controller is used only for the north-side runways. Both feeds share one transmission queue, so clearances never talk over each other.

Piper `1.2.0` remains installed at `~/.local/piper/piper` with `voices/en_US-ryan-high.onnx` as a fallback. If Kokoro fails, or if `TTS_ENGINE=piper` is set, `serve.py` uses Piper instead. `TTS_ENGINE=kokoro` is the default. `KOKORO_VOICE`, `KOKORO_NORTH_VOICE`, and `KOKORO_SPEED` can override the south voice, north voice, and rate. `/api/tts` accepts `voice: "south"` or `voice: "north"`, and `/api/health` reports the active engine, south voice, and north voice.

Browsers require a gesture before audio playback. Tap once to unlock ATC voice; the button changes from `ATC voice: tap` to `ATC voice on`, and the unlock step plays a radio check for each controller. Tap again to mute. Every `CLEAR_TAXI`, `CLEAR_TAKEOFF`, `CLEAR_APPROACH`, `CLEAR_CROSS`, and `HOLD` decision is spoken; `MONITOR` stays silent.

The server caches WAV files in `/tmp/airport-sim-tts`. The cache key includes the controller voice, so south and north versions of the same phrase are stored separately. Callsigns use NATO phonetics and runway numbers use spoken digits before synthesis. The browser plays returned WAVs through one reusable audio element with serialized playback.

## Simulation

Five fixed-wing aircraft follow this state machine:

```text
PARKED -> TAXI_OUT -> HOLD_SHORT -> LINEUP -> TAKEOFF
       -> AIRBORNE -> APPROACH -> LANDING -> TAXI_IN -> PARKED
```

Ground routes use tight waypoint capture so aircraft settle precisely on hold pads and aprons. Airborne routes use a turn-radius-based capture gate so fast aircraft cannot orbit a waypoint they cannot make. Takeoff rotation occurs after 60% of the runway, and landing turnoff uses the runway's published exit point.

The decision loop runs every ten seconds and can use rules, Ollama, or an OpenAI-compatible endpoint. Snapshot data includes wind, active runways, per-runway reservations, runway crossings, aircraft states, positions, and requests. Decisions are normalized, validated against the active-runway set, and rejected rather than applied if inconsistent. Ollama failures fall back to the deterministic rule engine, and animation never waits on model latency.

Two safety flows extend the fixed-wing sequence:

- Gate conflict handling: when an arrival's assigned stand is still occupied, it is reassigned to a free stand before the taxi-in route is built.
- Go-around handling: if another aircraft or an active crossing occupies the arrival runway on short final, the arrival climbs away, re-enters the pattern, and requests a new approach.

## Helicopter Operations

Two helicopters operate independently of the fixed-wing ATC loop:

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
- `serve.py` — static server, Ollama proxy, and Kokoro/Piper synthesis.
- `.kokoro-venv/` — Python 3.12 runtime containing `kokoro-onnx`, `onnxruntime`, and `numpy`.
- `voices/kokoro/kokoro-v1.0.int8.onnx` — Kokoro-82M int8 speech model.
- `voices/kokoro/voices-v1.0.bin` — Kokoro voice pack.
- `voices/en_US-ryan-high.onnx` — Piper fallback voice model.
- `voices/en_US-ryan-high.onnx.json` — Piper voice configuration.
- `airport-sim-README.md` — this document.

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
curl -s -H 'Content-Type: application/json' \
  --data '{"text":"Tower radio check.","voice":"north"}' \
  -o /tmp/tts.wav http://127.0.0.1:8123/api/tts
```

## Controls

- Mouse drag: orbit.
- Scroll: zoom.
- Speed buttons: simulation time scale.
- Auto-orbit button: toggle camera orbit.
- ATC voice button: unlock, mute, or re-enable clearances.
- Reset view: return to the home camera.
- AI badge: shows whether decisions come from rules, Ollama, or an OpenAI-compatible model.

`window.airport` exposes the scene, camera, orbit controls, aircraft, decision history, weather, renderer, startup scenario, voice state, and `decideNow()` for debugging and automated tests.

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
