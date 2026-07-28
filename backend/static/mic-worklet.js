/**
 * AudioWorklet: float32 mic → Int16 PCM @ exactly 16 kHz mono for Gemini Live.
 *
 * WHY THIS IS NOT A DECIMATOR
 *
 * The previous version consumed `Math.floor(sampleRate / 16000)` input samples per
 * output sample. That is only correct when the AudioContext rate is an integer
 * multiple of 16 kHz:
 *
 *     ctx 48000 -> emits 16000 Hz   correct
 *     ctx 44100 -> emits 22050 Hz   labelled 16000, +37.8% rate error
 *     ctx 88200 -> emits 17640 Hz   labelled 16000, +10.3% rate error
 *
 * 44.1 kHz is common (most USB mics, many built-in codecs). Gemini then receives
 * time-stretched, pitch-shifted speech tagged `rate=16000` and returns no
 * input transcription at all — the mic looks like it is working, bytes flow, and
 * the model never hears a word.
 *
 * So resample properly: a fractional phase accumulator with linear interpolation,
 * which is exact for ANY input rate. The phase and one sample of history carry
 * across process() blocks, so there is no discontinuity at block boundaries.
 *
 * Also: no per-sample allocation. The old `_acc.slice(n)` allocated an array for
 * every output sample — 16,000 allocations/second on the real-time audio thread,
 * which glitches on modest hardware.
 */
const TARGET_RATE = 16000;
const FRAME_SAMPLES = 320; // 20 ms @ 16 kHz

class Pcm16CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    // Input samples consumed per output sample. Fractional is normal and fine.
    this._step = sampleRate / TARGET_RATE;
    // Fractional read position into the current block. Carried across blocks; a
    // value in [-1, 0) means "interpolate from the previous block's last sample".
    this._pos = 0;
    this._prev = 0;
    this._frame = new Int16Array(FRAME_SAMPLES);
    this._n = 0;
    // Tell the main thread the real context rate so it can be logged/diagnosed
    // instead of assumed.
    this.port.postMessage({
      type: "meta",
      contextRate: sampleRate,
      targetRate: TARGET_RATE,
      step: this._step,
    });
  }

  _emit(v) {
    const s = v < -1 ? -1 : v > 1 ? 1 : v;
    // Int16Array assignment truncates toward zero, which is what we want.
    this._frame[this._n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
    if (this._n === FRAME_SAMPLES) {
      const out = new Int16Array(this._frame); // one copy per 20 ms, not per sample
      this._n = 0;
      this.port.postMessage(out.buffer, [out.buffer]);
    }
  }

  process(inputs) {
    const input = inputs[0];
    const ch = input && input[0];
    if (!ch || !ch.length) return true;

    const len = ch.length;
    const step = this._step;
    let pos = this._pos;

    while (pos < len) {
      const i0 = Math.floor(pos);
      const next = i0 + 1;
      // Need the sample after i0 to interpolate; if it is in the next block,
      // stop and carry the phase over.
      if (next >= len) break;
      const s0 = i0 < 0 ? this._prev : ch[i0];
      const frac = pos - i0;
      this._emit(s0 + (ch[next] - s0) * frac);
      pos += step;
    }

    this._prev = ch[len - 1];
    // Carry the unconsumed fraction. Lands in [-1, 0): i0 becomes -1 next block
    // and s0 correctly reads _prev.
    this._pos = pos - len;
    return true;
  }
}

registerProcessor("pcm16-capture", Pcm16CaptureProcessor);
