#!/usr/bin/env python3
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = os.path.dirname(os.path.abspath(__file__))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
PIPER_BIN = os.environ.get(
    "PIPER_BIN",
    shutil.which("piper") or os.path.expanduser("~/.local/bin/piper"),
)
PIPER_VOICE = os.environ.get(
    "PIPER_VOICE",
    os.path.join(ROOT, "voices", "en_US-ryan-high.onnx"),
)
KOKORO_MODEL = os.environ.get(
    "KOKORO_MODEL",
    os.path.join(ROOT, "voices", "kokoro", "kokoro-v1.0.int8.onnx"),
)
KOKORO_VOICES = os.environ.get(
    "KOKORO_VOICES",
    os.path.join(ROOT, "voices", "kokoro", "voices-v1.0.bin"),
)
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "am_michael")
KOKORO_NORTH_VOICE = os.environ.get("KOKORO_NORTH_VOICE", "am_onyx")
KOKORO_PILOT_VOICES = tuple(
    voice.strip()
    for voice in os.environ.get(
        "KOKORO_PILOT_VOICES",
        "af_sarah,am_liam,af_bella,am_echo,af_nova,am_fenrir,af_nicole",
    ).split(",")
    if voice.strip()
) or (KOKORO_VOICE,)
PIPER_NORTH_VOICE = os.environ.get("PIPER_NORTH_VOICE", PIPER_VOICE)
PIPER_PILOT_VOICE = os.environ.get("PIPER_PILOT_VOICE", PIPER_VOICE)
KOKORO_SPEED = float(os.environ.get("KOKORO_SPEED", "1.08"))
TTS_ENGINE = os.environ.get("TTS_ENGINE", "kokoro").lower()
PORT = int(os.environ.get("PORT", "8123"))
TTS_CACHE = os.path.join(tempfile.gettempdir(), "airport-sim-tts")
os.makedirs(TTS_CACHE, exist_ok=True)

_kokoro = None
_kokoro_lock = threading.Lock()
_kokoro_error = None


def kokoro_installed():
    return os.path.isfile(KOKORO_MODEL) and os.path.isfile(KOKORO_VOICES)


def piper_installed(voice_path=None):
    return bool(PIPER_BIN and os.path.isfile(voice_path or PIPER_VOICE))


def reexec_with_kokoro_venv():
    if TTS_ENGINE != "kokoro" or not kokoro_installed():
        return
    if importlib.util.find_spec("kokoro_onnx") is not None:
        return
    if os.environ.get("AIRPORT_SIM_REEXEC") == "1":
        return
    venv_python = os.path.join(ROOT, ".kokoro-venv", "bin", "python")
    if not os.path.isfile(venv_python):
        return
    if os.path.realpath(sys.executable) == os.path.realpath(venv_python):
        return
    os.environ["AIRPORT_SIM_REEXEC"] = "1"
    print(f"Restarting with Kokoro runtime: {venv_python}", flush=True)
    os.execv(venv_python, [venv_python, os.path.abspath(__file__), *sys.argv[1:]])


def load_kokoro():
    global _kokoro, _kokoro_error
    if _kokoro is not None:
        return _kokoro
    with _kokoro_lock:
        if _kokoro is not None:
            return _kokoro
        try:
            from kokoro_onnx import Kokoro

            _kokoro = Kokoro(KOKORO_MODEL, KOKORO_VOICES)
            _kokoro_error = None
        except Exception as error:
            _kokoro_error = str(error)
            raise
    return _kokoro


def synthesize_kokoro(text, voice_name=None):
    import numpy as np

    kokoro = load_kokoro()
    with _kokoro_lock:
        samples, sample_rate = kokoro.create(
            text,
            voice=voice_name or KOKORO_VOICE,
            speed=KOKORO_SPEED,
            lang="en-us",
        )
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())
    return buffer.getvalue()


def synthesize_piper(text, output_path, voice_path=None):
    completed = subprocess.run(
        [
            PIPER_BIN,
            "--model",
            voice_path or PIPER_VOICE,
            "--output_file",
            output_path,
            "--length_scale",
            "1.05",
            "--sentence_silence",
            "0.25",
            "--quiet",
        ],
        input=text.encode(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0 or not os.path.getsize(output_path):
        detail = completed.stderr.decode(errors="replace").strip()
        raise RuntimeError(detail or "Piper produced no audio")


class Handler(SimpleHTTPRequestHandler):
    def send_bytes(self, status, payload, content_type, cache_control="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, status, value):
        self.send_bytes(status, json.dumps(value).encode(), "application/json")

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 8192:
            raise ValueError("Invalid request size")
        return json.loads(self.rfile.read(length))

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/api/chat":
            self.handle_chat()
            return
        if path == "/api/tts":
            self.handle_tts()
            return
        self.send_error(404)

    def handle_chat(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        request = Request(
            f"{OLLAMA_URL}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=180) as response:
                payload = response.read()
                status = response.status
                content_type = response.headers.get("Content-Type", "application/json")
        except HTTPError as error:
            payload = error.read()
            status = error.code
            content_type = error.headers.get("Content-Type", "application/json")
        except (URLError, TimeoutError) as error:
            self.send_json(502, {"error": f"Ollama proxy unavailable: {error}"})
            return

        self.send_bytes(status, payload, content_type)

    def handle_tts(self):
        try:
            payload = self.read_json()
            text = " ".join(str(payload.get("text", "")).split())
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json(400, {"error": str(error)})
            return

        if not text:
            self.send_json(400, {"error": "No text supplied"})
            return
        if len(text) > 500:
            self.send_json(400, {"error": "Text is too long"})
            return

        requested = " ".join(str(payload.get("voice", "south")).split()).strip().lower()
        controller = "north" if requested in {
            "north",
            "tower north",
            "tower_north",
            "tower-north",
            KOKORO_NORTH_VOICE.lower(),
        } else "south"
        pilot_index = None
        if requested == "pilot":
            pilot_index = 0
        elif requested.startswith("pilot-"):
            try:
                pilot_index = max(1, int(requested.split("-", 1)[1])) - 1
            except ValueError:
                pilot_index = None
        if pilot_index is not None:
            kokoro_voice = KOKORO_PILOT_VOICES[pilot_index % len(KOKORO_PILOT_VOICES)]
            piper_voice = PIPER_PILOT_VOICE
        else:
            kokoro_voice = KOKORO_NORTH_VOICE if controller == "north" else KOKORO_VOICE
            piper_voice = PIPER_NORTH_VOICE if controller == "north" else PIPER_VOICE
        if not os.path.isfile(piper_voice):
            piper_voice = PIPER_VOICE

        use_kokoro = TTS_ENGINE == "kokoro" and kokoro_installed()
        use_piper = piper_installed(piper_voice)
        if not use_kokoro and not use_piper:
            self.send_json(503, {"error": "Kokoro and Piper are not installed"})
            return

        engine = "kokoro" if use_kokoro else "piper"
        voice = kokoro_voice if use_kokoro else os.path.basename(piper_voice)
        settings = f"{engine}\0{voice}\0{KOKORO_SPEED if use_kokoro else '1.05'}\0{text}"
        digest = hashlib.sha256(settings.encode()).hexdigest()
        cache_path = os.path.join(TTS_CACHE, f"{digest}.wav")
        if not os.path.isfile(cache_path):
            fd, temp_path = tempfile.mkstemp(suffix=".wav", dir=TTS_CACHE)
            os.close(fd)
            try:
                if use_kokoro:
                    try:
                        audio_payload = synthesize_kokoro(text, kokoro_voice)
                        with open(temp_path, "wb") as output:
                            output.write(audio_payload)
                    except Exception as error:
                        if not use_piper:
                            raise RuntimeError(f"Kokoro failed: {error}") from error
                        print(f"Kokoro unavailable, using Piper: {error}", flush=True)
                        engine = "piper"
                        voice = os.path.basename(piper_voice)
                        synthesize_piper(text, temp_path, piper_voice)
                else:
                    synthesize_piper(text, temp_path, piper_voice)
                os.replace(temp_path, cache_path)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                self.send_json(500, {"error": f"Speech synthesis failed: {error}"})
                return

        with open(cache_path, "rb") as audio:
            audio_payload = audio.read()
        self.send_bytes(200, audio_payload, "audio/wav", "public, max-age=86400")

    def do_GET(self):
        if urlsplit(self.path).path == "/api/health":
            use_kokoro = TTS_ENGINE == "kokoro" and kokoro_installed()
            self.send_json(
                200,
                {
                    "ok": True,
                    "tts": bool(use_kokoro or piper_installed()),
                    "engine": "kokoro" if use_kokoro else ("piper" if piper_installed() else None),
                    "voice": KOKORO_VOICE if use_kokoro else os.path.basename(PIPER_VOICE),
                    "north_voice": KOKORO_NORTH_VOICE if use_kokoro else os.path.basename(PIPER_NORTH_VOICE),
                    "pilot_voices": list(KOKORO_PILOT_VOICES) if use_kokoro else [os.path.basename(PIPER_PILOT_VOICE)],
                    "kokoro_error": _kokoro_error,
                },
            )
            return
        super().do_GET()


def warm_tts():
    if TTS_ENGINE == "kokoro" and kokoro_installed():
        try:
            load_kokoro()
            print(
                f"Kokoro voices: {KOKORO_VOICE} / {KOKORO_NORTH_VOICE} / "
                f"pilots {', '.join(KOKORO_PILOT_VOICES)} "
                f"({os.path.basename(KOKORO_MODEL)})",
                flush=True,
            )
            return
        except Exception as error:
            print(f"Kokoro warm-up failed: {error}", flush=True)
    if piper_installed():
        print(f"Piper fallback: {PIPER_VOICE}", flush=True)
    else:
        print("No TTS engine available", flush=True)


def main():
    reexec_with_kokoro_venv()
    warm_tts()
    handler = partial(Handler, directory=ROOT)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), handler)
    print(f"Airport sim: http://0.0.0.0:{PORT}/airport-sim.html", flush=True)
    print(f"Ollama proxy: {OLLAMA_URL}/api/chat", flush=True)
    print(f"TTS engine: {TTS_ENGINE}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
