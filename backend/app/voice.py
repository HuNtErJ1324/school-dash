"""The dashboard's side of the speech container (Speaches, OpenAI's audio API): it turns what you say in a
tutoring session into text, and reads the tutor's replies aloud. Audio passes through and isn't kept."""

from __future__ import annotations

from typing import Any

import httpx

from .config import VoiceConfig

MAX_AUDIO_BYTES = 10 * 1024 * 1024  # a couple of minutes of speech, in any format a browser records
MAX_PROMPT_CHARS = 600  # Whisper reads only the last 224 tokens of its prompt


class SpeechError(Exception):
    """A failure whose message is safe and useful to show in the dashboard as-is."""

    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


class SpeechClient:
    def __init__(self, url: str | None, config: VoiceConfig, http: httpx.AsyncClient):
        self.url = url.rstrip("/") if url else None
        self.config = config
        self.http = http

    @property
    def enabled(self) -> bool:
        return self.url is not None

    async def status(self) -> dict[str, Any]:
        """{"stt", "tts", "problem"}: what the speech container can do now, and what to fix when it can't."""
        if not self.url:
            return {"stt": False, "tts": False, "problem": "There's no speech container: SPEECH_URL isn't set."}
        try:
            resp = await self.http.get(f"{self.url}/v1/models", timeout=5)
            resp.raise_for_status()
            installed = {m.get("id") for m in resp.json().get("data", [])}
        except (httpx.HTTPError, ValueError, AttributeError) as e:
            return {"stt": False, "tts": False, "problem": f"The speech container isn't answering ({type(e).__name__})."}
        stt, tts = self.config.stt_model in installed, self.config.tts_model in installed
        missing = [m for m, ok in ((self.config.stt_model, stt), (self.config.tts_model, tts)) if not ok]
        problem = f"The speech container hasn't downloaded {' or '.join(missing)}." if missing else None
        return {"stt": stt, "tts": tts, "problem": problem}

    async def transcribe(self, audio: bytes, filename: str, content_type: str, prompt: str = "") -> str:
        """What was said in a recording. `prompt` (course names, the topic) helps Whisper spell their terms."""
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
