/**
 * Reading replies aloud, one at a time. A reply is said in pieces (speakable.ts): the speech container's voice
 * makes each one while the one before plays, and the browser's own voice reads whatever it can't.
 *
 * Phones only play sound that a tap started, and a reply that's read as it arrives comes long after the tap that
 * sent the question. So every reply plays through one audio element, which the first tap (on the mic, Send,
 * a speaker button) starts with a moment of silence; after that it may play whenever.
 */
import { useSyncExternalStore } from "react";

import { api } from "../api";
import { chunks, speakable } from "./speakable";

export interface Playing {
  key: string; // what's being read: "message:12"
  loading: boolean; // still making its first piece
}

let playing: Playing | null = null;
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};
function set(next: Playing | null) {
  playing = next;
  listeners.forEach((l) => l());
}

/** What's being read aloud, if anything. */
export function usePlaying(): Playing | null {
  return useSyncExternalStore(subscribe, () => playing);
}

let element: HTMLAudioElement | null = null;
let unlocked = false;
let run: AbortController | null = null;
const speaking = new Set<SpeechSynthesisUtterance>(); // held, or Chrome can drop one before it ends

const synth = () => ("speechSynthesis" in window ? window.speechSynthesis : null);

/** A fifth of a second of 8 kHz silence, as WAV. */
function silence(): Blob {
  const samples = 1600;
  const view = new DataView(new ArrayBuffer(44));
  const text = (at: number, s: string) => [...s].forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + samples, true);
  text(8, "WAVEfmt ");
  view.setUint32(16, 16, true); // PCM
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, 8000, true);
  view.setUint32(28, 8000, true);
  view.setUint16(32, 1, true);
  view.setUint16(34, 8, true); // 8-bit, where silence is 128
  text(36, "data");
  view.setUint32(40, samples, true);
  return new Blob([view, new Uint8Array(samples).fill(128)], { type: "audio/wav" });
}

/** Call from a tap, before anything is read that the tap didn't start itself. */
export function unlock() {
  element ??= new Audio();
  if (unlocked) return;
  unlocked = true;
  const url = URL.createObjectURL(silence());
  element.src = url;
  element.play().then(
    () => URL.revokeObjectURL(url),
    () => {
      unlocked = false; // not a tap after all; the next one tries again
      URL.revokeObjectURL(url);
    },
  );
  synth()?.speak(new SpeechSynthesisUtterance(" ")); // Safari's own voice needs the same
}

/** Stop, if what's being read is one of `keys`. */
export function stopIf(keys: Iterable<string>) {
  if (playing && new Set(keys).has(playing.key)) stop();
}

export function stop() {
  run?.abort();
  run = null;
  element?.pause();
  synth()?.cancel();
  set(null);
}

/** Read a reply's markdown aloud, in the speech container's voice when `server` (else the browser's). */
export async function play(key: string, markdown: string, server: boolean) {
  stop();
  const ctrl = new AbortController();
  run = ctrl;
  set({ key, loading: true });
  try {
    const parts = chunks(await speakable(markdown));
    let i = 0;
    if (server) {
      const make = (n: number) => {
        const audio = api.speak(parts[n], ctrl.signal);
        audio.catch(() => {}); // seen when its turn comes
        return audio;
      };
      let next = parts.length ? make(0) : null;
      for (; i < parts.length && !ctrl.signal.aborted; i++) {
        let audio: Blob;
        try {
          audio = await next!;
        } catch {
          break; // the browser reads the rest
        }
        next = i + 1 < parts.length ? make(i + 1) : null;
        if (i === 0) set({ key, loading: false });
        try {
          await playAudio(audio, ctrl.signal);
        } catch {
          break;
        }
      }
    }
    if (ctrl.signal.aborted) return;
    set({ key, loading: false });
    // The browser's voice cuts long pieces short (Chrome stops at about 15 seconds), so it gets a sentence or two.
    for (const part of parts.slice(i).flatMap((p) => chunks(p, 200, 200))) {
      if (ctrl.signal.aborted) return;
      await sayInBrowser(part, ctrl.signal);
    }
  } finally {
    if (run === ctrl) {
      run = null;
      set(null);
    }
  }
}

function playAudio(audio: Blob, signal: AbortSignal): Promise<void> {
  element ??= new Audio();
  const el = element;
  const url = URL.createObjectURL(audio);
  return new Promise<void>((resolve, reject) => {
    const finish = (error?: unknown) => {
      el.onended = el.onerror = null;
      signal.removeEventListener("abort", aborted);
      URL.revokeObjectURL(url);
      if (error) reject(error);
      else resolve();
    };
    const aborted = () => finish();
    signal.addEventListener("abort", aborted);
    el.onended = () => finish();
    el.onerror = () => finish(new Error("The audio couldn't be played."));
    el.src = url;
    el.play().catch(finish);
  });
}

function sayInBrowser(text: string, signal: AbortSignal): Promise<void> {
  const speech = synth();
  if (!speech) return Promise.resolve();
  return new Promise<void>((resolve) => {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-US";
    u.onend = u.onerror = () => resolve();
    signal.addEventListener("abort", () => resolve(), { once: true });
    window.setTimeout(resolve, 3000 + text.length * 150); // a browser with no voices may never say it's done
    speaking.add(u);
    speech.speak(u);
  }).finally(() => speaking.clear());
}

// -- reading each reply as it arrives: a setting kept on this device ---------------------------------------

const KEY = "voice-read-aloud";
let readAloud = (() => {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false; // a private window, or storage turned off
  }
})();
const settingListeners = new Set<() => void>();

export function setReadAloud(on: boolean) {
  readAloud = on;
  try {
    localStorage.setItem(KEY, on ? "1" : "0");
  } catch {
    // not remembered, that's all
  }
  settingListeners.forEach((l) => l());
}

/** Whether the tutor's replies are read aloud as they arrive. */
export function useReadAloud(): boolean {
  return useSyncExternalStore(
    (listener) => {
      settingListeners.add(listener);
      return () => settingListeners.delete(listener);
    },
    () => readAloud,
  );
}
