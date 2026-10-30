/** Recording what you say, in whatever the browser records (Opus in WebM, or AAC in MP4 on Safari). */

export const MAX_SECONDS = 120;

export const canRecord = () => typeof MediaRecorder !== "undefined" && !!navigator.mediaDevices?.getUserMedia;

export interface Recording {
  stop(): Promise<Blob>; // what was said
  cancel(): void;
}

const TYPES = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm", "audio/ogg;codecs=opus"];

/** Start recording from the microphone. Asks for it the first time. */
export async function record(): Promise<Recording> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  const mimeType = TYPES.find((t) => MediaRecorder.isTypeSupported(t));
  const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  const parts: Blob[] = [];
  recorder.ondataavailable = (e) => {
    if (e.data.size) parts.push(e.data);
  };
  const release = () => stream.getTracks().forEach((t) => t.stop()); // and the browser's recording light goes off
  recorder.start();
  return {
    stop: () =>
      new Promise<Blob>((resolve) => {
        recorder.onstop = () => {
          release();
          resolve(new Blob(parts, { type: recorder.mimeType || mimeType || "audio/webm" }));
        };
        recorder.stop();
      }),
    cancel: () => {
      recorder.onstop = null;
      if (recorder.state !== "inactive") recorder.stop();
      release();
    },
  };
}

/** What to tell you when the microphone won't start. */
export function micProblem(e: unknown): string {
  const name = e instanceof DOMException ? e.name : "";
  if (name === "NotAllowedError") return "The microphone is blocked. Allow it for this site in the browser's settings.";
  if (name === "NotFoundError") return "There's no microphone.";
  if (name === "NotReadableError") return "Another app is using the microphone.";
  return e instanceof Error ? e.message : "The microphone didn't start.";
}
