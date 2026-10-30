import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api, type Message } from "../api";
import { chatText } from "../canvas/reply";
import { HeadphonesIcon, MicIcon, SpeakerIcon, StopIcon } from "../ui/icons";
import { play, setReadAloud, stop, stopIf, unlock, usePlaying, useReadAloud } from "./player";
import { canRecord, MAX_SECONDS, micProblem, record, type Recording } from "./record";

/** What the speech container can do. Asked once a page, and again every few minutes. */
export function useVoice() {
  return useQuery({ queryKey: ["voice"], queryFn: api.voice, staleTime: 5 * 60_000, refetchInterval: 5 * 60_000 });
}

const browserVoice = () => "speechSynthesis" in window;

/** Which voice buttons a reply box gets: the mic once the speech container takes dictation, and reading aloud. */
export function useVoiceTools(): { mic: boolean; readAloud: boolean } {
  const voice = useVoice();
  return { mic: !!voice.data?.stt && canRecord(), readAloud: !!voice.data?.tts || browserVoice() };
}

type MicState = "idle" | "starting" | "recording" | "hearing";

/**
 * Tap to talk, tap again when you're done: what you said goes in the reply box, for you to check (Whisper hears
 * "x squared", not $x^2$) and send. Stops any reading aloud first, so the tutor doesn't hear itself.
 */
export function MicButton({ onText, onProblem, context }: {
  onText: (text: string) => void;
  onProblem: (problem: string | null) => void;
  context?: string; // what the session is about, so it spells the course's terms right
}) {
  const [state, setState] = useState<MicState>("idle");
  const recording = useRef<Recording | null>(null);
  useEffect(() => () => recording.current?.cancel(), []);

  const start = async () => {
    unlock();
    stop();
    onProblem(null);
    setState("starting");
    try {
      recording.current = await record();
      setState("recording");
    } catch (e) {
      onProblem(micProblem(e));
      setState("idle");
    }
  };
  const finish = async () => {
    const r = recording.current;
    if (!r) return;
    recording.current = null;
    setState("hearing");
    try {
      const { text } = await api.transcribe(await r.stop(), context);
      if (text) onText(text);
      else onProblem("I didn't catch anything. Try again a little closer to the microphone?");
    } catch (e) {
      onProblem(e instanceof Error ? e.message : "What you said couldn't be written down.");
    } finally {
      setState("idle");
    }
  };
  const finishRef = useRef(finish);
  finishRef.current = finish;
  useEffect(() => {
    if (state !== "recording") return;
    const limit = window.setTimeout(() => void finishRef.current(), MAX_SECONDS * 1000);
    return () => window.clearTimeout(limit);
  }, [state]);

  const label =
    state === "recording" ? "Stop and write down what you said"
    : state === "hearing" ? "Writing down what you said"
    : "Say your reply";  // prettier-ignore
  return (
    <button
      type="button"
      className={`icon-button composer-photo mic mic-${state}`}
      aria-label={label}
      title={label}
      aria-pressed={state === "recording"}
      disabled={state === "starting" || state === "hearing"}
      onClick={() => void (state === "recording" ? finish() : start())}
    >
      {state === "recording" ? <StopIcon /> : <MicIcon />}
    </button>
  );
}

/** Read each reply aloud as it arrives, on this device, until it's turned off. */
export function ReadAloudToggle() {
  const on = useReadAloud();
  const label = on ? "Reading replies aloud. Tap to stop" : "Read replies aloud as they arrive";
  return (
    <button
      type="button"
      className="icon-button composer-photo read-aloud"
      aria-label="Read replies aloud"
      aria-pressed={on}
      title={label}
      onClick={() => {
        if (on) stop();
        else unlock();
        setReadAloud(!on);
      }}
    >
      <HeadphonesIcon />
    </button>
  );
}

const messageKey = (id: number) => `message:${id}`;

/** Read one reply aloud, or stop reading it. */
export function SpeakButton({ message }: { message: Message }) {
  const voice = useVoice();
  const playing = usePlaying();
  const key = messageKey(message.id);
  if (!voice.data?.tts && !browserVoice()) return null;
  const mine = playing?.key === key;
  return (
    <button
      type="button"
      className={mine && playing.loading ? "icon-button msg-speak loading" : "icon-button msg-speak"}
      aria-label={mine ? "Stop reading" : "Read aloud"}
      title={mine ? "Stop reading" : "Read aloud"}
      aria-pressed={mine}
      onClick={() => {
        if (mine) return stop();
        unlock();
        void play(key, chatText(message.text), !!voice.data?.tts);
      }}
    >
      {mine && !playing.loading ? <StopIcon /> : <SpeakerIcon />}
    </button>
  );
}

/**
 * With reading aloud on, reads each reply that finishes while you're looking at the conversation. Leaving the
 * conversation stops it.
 */
export function useReadNewReplies(messages: Message[]) {
  const on = useReadAloud();
  const server = !!useVoice().data?.tts;
  const running = useRef(new Set<number>());
  const shown = useRef<number[]>([]);
  shown.current = messages.map((m) => m.id);

  useEffect(() => {
    for (const m of messages) {
      if (m.role !== "assistant") continue;
      if (m.status === "running") running.current.add(m.id);
      else if (running.current.delete(m.id) && m.status === "ready" && on && document.visibilityState === "visible") {
        void play(messageKey(m.id), chatText(m.text), server);
      }
    }
  }, [messages, on, server]);

  useEffect(() => () => stopIf(shown.current.map(messageKey)), []);
}
