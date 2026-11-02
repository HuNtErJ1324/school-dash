/**
 * Recordings as 16 kHz mono WAV, which Hex needs and Whisper takes too. Browsers record Opus or AAC; at 16 kHz,
 * two minutes of speech is about 4 MB.
 */

const RATE = 16_000; // what both hear at

/** `audio` as 16-bit, 16 kHz mono WAV, or as it was when this browser can't decode it. */
export async function toWav(audio: Blob): Promise<Blob> {
  try {
    let context: OfflineAudioContext;
    try {
      context = new OfflineAudioContext(1, 1, RATE); // it resamples what it decodes to its own rate
    } catch {
      context = new OfflineAudioContext(1, 1, 48_000); // a browser that won't run one at 16 kHz
    }
    const decoded = await context.decodeAudioData(await audio.arrayBuffer());
    return wav(downsample(mono(decoded), decoded.sampleRate));
  } catch {
    return audio;
  }
}

function mono(audio: AudioBuffer): Float32Array {
  if (audio.numberOfChannels === 1) return audio.getChannelData(0);
  const out = new Float32Array(audio.length);
  for (let c = 0; c < audio.numberOfChannels; c++) {
    const channel = audio.getChannelData(c);
    for (let i = 0; i < out.length; i++) out[i] += channel[i] / audio.numberOfChannels;
  }
  return out;
}

/** Averages each output sample's span of input: a rough low-pass, plenty for speech. */
function downsample(samples: Float32Array, rate: number): Float32Array {
  if (rate === RATE) return samples;
  const step = rate / RATE;
  const out = new Float32Array(Math.floor(samples.length / step));
  for (let i = 0; i < out.length; i++) {
    const from = Math.floor(i * step);
    const to = Math.max(from + 1, Math.floor((i + 1) * step));
    let sum = 0;
    for (let j = from; j < to; j++) sum += samples[j];
    out[i] = sum / (to - from);
  }
  return out;
}

function wav(samples: Float32Array): Blob {
  const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const text = (at: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(at + i, s.charCodeAt(i));
  };
  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true); // the format chunk's size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, RATE, true);
  view.setUint32(28, RATE * 2, true); // bytes a second
  view.setUint16(32, 2, true); // bytes a sample
  view.setUint16(34, 16, true); // bits a sample
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    view.setInt16(44 + i * 2, Math.max(-1, Math.min(1, samples[i])) * 0x7fff, true);
  }
  return new Blob([view.buffer], { type: "audio/wav" });
}
