"""The dashboard's side of voice: what you say in a tutoring session becomes text, and the tutor's replies are read
aloud. With Hex set up (the dictation app on the server's desktop, through hex_relay.py), Hex writes down what you say
whenever it's running, and Whisper only while it isn't; the speech container (Speaches, OpenAI's audio API) runs
Whisper and reads replies in Kokoro's voice. Audio passes through and isn't kept."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from .config import VoiceConfig

MAX_AUDIO_BYTES = 10 * 1024 * 1024  # a couple of minutes of speech, in any format a browser records
MAX_PROMPT_CHARS = 600  # Whisper reads only the last 224 tokens of its prompt
HEX = "http://hex"  # the relay's socket stands in for the address
HEX_DOWN = "Hex isn't running: it runs while you're logged in to the server's desktop."


class SpeechError(Exception):
    """A failure whose message is safe and useful to show in the dashboard as-is."""

    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


class HexDown(SpeechError):
    """Hex isn't running: then, and only then, Whisper writes down what you say instead."""

    def __init__(self) -> None:
        super().__init__(HEX_DOWN)


class RelayDown(SpeechError):
    """Nothing answers on the relay's socket: the `hex` service was killed and left its socket, or is restarting.
    Hex may be running, so Whisper doesn't stand in."""

    def __init__(self) -> None:
        super().__init__("Hex's relay isn't running (`docker compose ps hex`).")


def is_wav(audio: bytes) -> bool:
    return audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"


class HexClient:
    """Hex (github.com/anomalyco/hex), through the relay's socket in ./data/hex. Faster than Whisper, and it uses the
    model Hex dictates with, so the GPU holds one copy. It takes WAV only, and no prompt."""

    def __init__(self, socket: Path, config: VoiceConfig, http: httpx.AsyncClient):
        self.socket = socket
        self.config = config
        self.http = http

    @property
    def enabled(self) -> bool:
        return self.socket.exists()  # the relay makes it, and removes it when it stops

    async def check(self) -> tuple[str | None, bool]:
        """What keeps Hex from writing down what you say now (None when nothing does), and whether that's because
        it isn't running."""
        try:
            resp = await self.http.get(f"{HEX}/models", timeout=5)
        except httpx.ConnectError:
            return str(RelayDown()), False
        except httpx.HTTPError as e:
            return f"Hex's relay isn't answering ({type(e).__name__}).", False
        if resp.status_code == 503 and _code(resp) == "hex-not-running":
            return HEX_DOWN, True
        if not resp.is_success:
            return f"Hex isn't answering ({_code(resp)}).", False
        try:
            installed = {m.get("id") for m in resp.json() if m.get("installed")}
        except (ValueError, AttributeError, TypeError):
            return "Hex's list of models couldn't be read.", False
        if self.config.hex_model not in installed:
            return f"Hex hasn't installed {self.config.hex_model} (`hex model list` shows what it has).", False
        return None, False

    async def transcribe(self, wav: bytes) -> str:
        params = {"model": self.config.hex_model}
        if self.config.language:
            params["language"] = self.config.language
        try:
            # A cold model reloads first, which takes a few seconds.
            resp = await self.http.post(
                f"{HEX}/transcriptions", params=params, content=wav, headers={"Content-Type": "audio/wav"},
                timeout=httpx.Timeout(5, read=120),
            )  # fmt: skip
        except httpx.ConnectError as e:
            raise RelayDown from e
        except httpx.HTTPError as e:
            raise SpeechError(f"Hex's relay isn't answering ({type(e).__name__}).") from e
        if resp.status_code in (400, 413, 415, 422):
            raise SpeechError("That recording couldn't be read. Try again?", 422)
        if resp.status_code == 503 and _code(resp) == "hex-not-running":
            raise HexDown
        if not resp.is_success:
            raise SpeechError(f"Hex couldn't do it ({_code(resp)}).")
        try:
            return str(resp.json().get("transcript", "")).strip()
        except (ValueError, AttributeError) as e:
            raise SpeechError("Hex's transcript couldn't be read.", 502) from e


def _code(resp: httpx.Response) -> str:
    """Hex's reason for an error ("busy", "model-not-ready"), or the HTTP status."""
    try:
        return str(resp.json()["code"])
    except (ValueError, KeyError, TypeError):
        return f"HTTP {resp.status_code}"


class SpeechClient:
    def __init__(
        self, url: str | None, config: VoiceConfig, http: httpx.AsyncClient, hex_client: HexClient | None = None
    ):
        self.url = url.rstrip("/") if url else None
        self.config = config
        self.http = http
        self.hex = hex_client

    async def status(self) -> dict[str, Any]:
        """{"stt", "tts", "problem"}: whether dictation and reading aloud work now, and what to fix when they don't."""
        whisper, tts, problems = await self._speaches()
        stt = whisper
        if self.hex and self.hex.enabled:
            problem, down = await self.hex.check()
            stt = whisper if down else problem is None  # while Hex runs, it alone takes dictation
            if problem:
                standing_in = " Whisper writes down what you say until it's back." if down and whisper else ""
                problems.insert(0, problem + standing_in)
        return {"stt": stt, "tts": tts, "problem": " ".join(problems) or None}

    async def _speaches(self) -> tuple[bool, bool, list[str]]:
        """Whether the speech container can run Whisper and Kokoro, and what's wrong."""
        if not self.url:
            return False, False, ["There's no speech container: SPEECH_URL isn't set."]
        try:
            resp = await self.http.get(f"{self.url}/v1/models", timeout=5)
            resp.raise_for_status()
            installed = {m.get("id") for m in resp.json().get("data", [])}
        except (httpx.HTTPError, ValueError, AttributeError) as e:
            return False, False, [f"The speech container isn't answering ({type(e).__name__})."]
        wanted = [m for m in (self.config.stt_model, self.config.tts_model) if m]
        missing = [m for m in wanted if m not in installed]
        problems = [f"The speech container hasn't downloaded {' or '.join(missing)}."] if missing else []
        whisper = bool(self.config.stt_model) and self.config.stt_model in installed
        return whisper, self.config.tts_model in installed, problems

    async def transcribe(self, audio: bytes, filename: str, content_type: str, prompt: str = "") -> str:
        """What was said in a recording. With Hex set up, Hex hears it, and Whisper only while Hex isn't running.
        `prompt` (course names, the topic) helps Whisper spell their terms."""
        hex_down: HexDown | None = None
        if self.hex and self.hex.enabled:
            try:
                if is_wav(audio):
                    return await self.hex.transcribe(audio)
                if not (await self.hex.check())[1]:
                    raise SpeechError("Hex needs WAV, and this browser's recording isn't.", 422)
                raise HexDown
            except HexDown as e:  # any other error is Hex's to report: while it runs, Whisper stays idle
                hex_down = e
        if not self.url or not self.config.stt_model:
            raise hex_down or SpeechError("There's no speech container: SPEECH_URL isn't set.")
        try:
            return await self._whisper(audio, filename, content_type, prompt)
        except SpeechError as e:
            if hex_down:
                raise SpeechError(f"{hex_down} {e}", e.status) from e
            raise

    async def _whisper(self, audio: bytes, filename: str, content_type: str, prompt: str) -> str:
        data = {"model": self.config.stt_model, "response_format": "json", "temperature": "0"}
        if self.config.language:
            data["language"] = self.config.language
        if prompt.strip():
            data["prompt"] = prompt.strip()[-MAX_PROMPT_CHARS:]
        files = {"file": (filename, audio, content_type)}
        bad = "That recording couldn't be read. Try again?"
        resp = await self._post("/v1/audio/transcriptions", bad, data=data, files=files)
        try:
            return str(resp.json().get("text", "")).strip()
        except (ValueError, AttributeError) as e:
            raise SpeechError("The speech container's transcript couldn't be read.", 502) from e

    async def speak(self, text: str) -> bytes:
        """`text` read aloud by the configured voice, as MP3."""
        body = {"model": self.config.tts_model, "voice": self.config.voice, "input": text, "response_format": "mp3",
                "speed": self.config.speed}  # fmt: skip
        bad = f"The speech container can't read with voice {self.config.voice!r} at speed {self.config.speed}."
        return (await self._post("/v1/audio/speech", bad, json=body)).content

    async def _post(self, path: str, bad_request: str, **kwargs: Any) -> httpx.Response:
        if not self.url:
            raise SpeechError("There's no speech container: SPEECH_URL isn't set.")
        try:
            # The first request after a restart loads the model onto the GPU, which takes a while.
            resp = await self.http.post(f"{self.url}{path}", timeout=httpx.Timeout(5, read=120), **kwargs)
        except httpx.HTTPError as e:
            raise SpeechError(f"The speech container isn't answering ({type(e).__name__}).") from e
        if resp.status_code == 404:
            raise SpeechError("The speech container doesn't have the model that config.yaml's voice section names.")
        if resp.status_code in (400, 415, 422):
            raise SpeechError(bad_request, 422)
        if not resp.is_success:
            raise SpeechError(f"The speech container returned HTTP {resp.status_code}.", 502)
        return resp
