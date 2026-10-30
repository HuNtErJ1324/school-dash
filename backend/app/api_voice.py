"""Voice for tutoring: what you say becomes text in the reply box, and replies can be read aloud. Both go
through the speech container (voice.py); recordings and audio aren't kept."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .voice import MAX_AUDIO_BYTES, SpeechClient, SpeechError

router = APIRouter(prefix="/api/voice")


def _speech(request: Request) -> SpeechClient:
    return request.app.state.speech


class VoiceStatus(BaseModel):
    stt: bool  # the reply box can take dictation
    tts: bool  # replies can be read in the configured voice (otherwise the browser's own voice reads them)
    problem: str | None  # what to fix when either is off


@router.get("", response_model=VoiceStatus)
async def voice_status(request: Request) -> Any:
    return await _speech(request).status()


class Transcript(BaseModel):
    text: str


@router.post("/transcribe", response_model=Transcript)
async def transcribe(
    request: Request,
    file: Annotated[UploadFile, File()],
    context: Annotated[str, Form(max_length=500)] = "",  # what the session is about: "CSE 410 · Page tables"
) -> Any:
    audio = await file.read(MAX_AUDIO_BYTES + 1)
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "That recording is too long. Keep it under a couple of minutes.")
    if not audio:
        raise HTTPException(422, "The recording is empty.")
    # The course names and the topic, so Whisper spells "semaphore" and "Riemann" the way the course does.
    courses = ", ".join(f"{c.code} {c.name}" if c.name else c.code for c in request.app.state.config.courses)
    prompt = ". ".join(p for p in (courses, context.strip()) if p)
    try:
        text = await _speech(request).transcribe(
            audio, file.filename or "speech.webm", file.content_type or "application/octet-stream", prompt
        )
    except SpeechError as e:
        raise HTTPException(e.status, str(e)) from e
    return {"text": text}


class Speak(BaseModel):
    text: str = Field(min_length=1, max_length=2000)  # the page reads a reply a few sentences at a time


@router.post("/speak", response_class=Response, responses={200: {"content": {"audio/mpeg": {}}}})
async def speak(body: Speak, request: Request) -> Response:
    try:
        audio = await _speech(request).speak(body.text)
    except SpeechError as e:
        raise HTTPException(e.status, str(e)) from e
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})
