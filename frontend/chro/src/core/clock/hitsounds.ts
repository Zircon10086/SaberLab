import { Result } from 'better-result';

import { songBpmTimeToSeconds } from '../beatmap/bpm';
import type { ReplayNoteEventType } from '../replay/types';

export interface HitsoundEvent {
  time: number;
  good: boolean;
}

interface HitsoundNote {
  beat: number;
  interactable?: boolean;
  replayEndTime?: number;
  replayEventType?: ReplayNoteEventType;
}

export function buildHitsoundEvents(notes: HitsoundNote[], songBpm: number) {
  const events = notes
    .flatMap((note): HitsoundEvent[] => {
      if (note.interactable === false) return [];
      if (note.replayEventType === 0 || note.replayEventType === 3 || note.replayEventType === 4) return [];
      if (note.replayEventType === 2) {
        return [{ time: note.replayEndTime ?? songBpmTimeToSeconds(note.beat, songBpm), good: false }];
      }
      return [{ time: songBpmTimeToSeconds(note.beat, songBpm), good: true }];
    })
    .sort((left, right) => left.time - right.time);
  const collapsed: HitsoundEvent[] = [];
  for (const event of events) {
    const previous = collapsed.at(-1);
    if (previous !== undefined && Math.abs(previous.time - event.time) <= 1e-6) {
      previous.good &&= event.good;
    } else {
      collapsed.push(event);
    }
  }
  return collapsed;
}

export function firstHitsoundAfter(events: HitsoundEvent[], time: number) {
  let low = 0;
  let high = events.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if ((events[middle]?.time ?? Infinity) <= time) low = middle + 1;
    else high = middle;
  }
  return low;
}

const SILENCE_THRESHOLD = 0.001;

function trimLeadingSilence(buffer: AudioBuffer, context: AudioContext): AudioBuffer {
  const { numberOfChannels, sampleRate, length } = buffer;
  let startSample = 0;
  outer: for (let i = 0; i < length; i++) {
    for (let c = 0; c < numberOfChannels; c++) {
      if (Math.abs(buffer.getChannelData(c)[i] ?? 0) > SILENCE_THRESHOLD) {
        startSample = i;
        break outer;
      }
    }
  }
  if (startSample === 0) return buffer;
  const trimmedLength = length - startSample;
  const trimmed = context.createBuffer(numberOfChannels, trimmedLength, sampleRate);
  for (let c = 0; c < numberOfChannels; c++) {
    trimmed.copyToChannel(buffer.getChannelData(c).subarray(startSample), c);
  }
  return trimmed;
}

export class HitsoundPlayer {
  private context: AudioContext | null = null;
  private sounds = new Map<AudioScheduledSourceNode, GainNode>();
  private volume = 1;
  private goodCutBuffer: AudioBuffer | null = null;
  private badCutBuffer: AudioBuffer | null = null;
  private customGoodCutArrayBuffer: ArrayBuffer | null = null;
  private customBadCutArrayBuffer: ArrayBuffer | null = null;
  private bufferGeneration = 0;

  setVolume(volume: number) {
    this.volume = Math.min(Math.max(volume, 0), 1);
  }

  async setBuffers(goodBuffer: ArrayBuffer | null, badBuffer: ArrayBuffer | null) {
    const generation = ++this.bufferGeneration;
    this.customGoodCutArrayBuffer = goodBuffer;
    this.customBadCutArrayBuffer = badBuffer;
    this.goodCutBuffer = null;
    this.badCutBuffer = null;

    const context = this.context;
    if (context === null) return Result.ok(undefined);

    const [goodDecoded, badDecoded] = await Promise.all([
      goodBuffer === null ? Result.ok(null) : this.decodeBuffer(context, goodBuffer),
      badBuffer === null ? Result.ok(null) : this.decodeBuffer(context, badBuffer),
    ]);
    if (generation !== this.bufferGeneration) return Result.ok(undefined);

    if (goodDecoded.isOk()) this.goodCutBuffer = goodDecoded.value;
    if (badDecoded.isOk()) this.badCutBuffer = badDecoded.value;
    if (goodDecoded.isErr()) return Result.err(goodDecoded.error);
    if (badDecoded.isErr()) return Result.err(badDecoded.error);
    return Result.ok(undefined);
  }

  private decodeBuffer(context: AudioContext, arrayBuffer: ArrayBuffer) {
    return Result.tryPromise({
      try: async () => {
        const decoded = await context.decodeAudioData(arrayBuffer.slice(0));
        return trimLeadingSilence(decoded, context);
      },
      catch: (cause) => new Error('Hitsound audio could not be decoded', { cause }),
    });
  }

  resume() {
    if (!this.context) {
      const created = Result.try(() => new AudioContext());
      if (created.isErr()) {
        console.warn('Hitsound audio could not start', created.error);
        return;
      }
      this.context = created.value;
      void this.setBuffers(this.customGoodCutArrayBuffer, this.customBadCutArrayBuffer).then((result) => {
        if (result.isErr()) console.warn(result.error);
      });
    }
    const context = this.context;
    void Result.tryPromise(() => context.resume()).then((result) => {
      if (result.isErr()) console.warn('Hitsound audio could not resume', result.error);
    });
  }

  play(good: boolean, delay = 0) {
    const context = this.context;
    if (context === null || this.volume === 0) return;

    const buffer = good ? this.goodCutBuffer : this.badCutBuffer;
    const start = context.currentTime + Math.max(delay, 0);
    const gain = context.createGain();

    if (buffer) {
      const source = context.createBufferSource();
      source.buffer = buffer;
      gain.gain.setValueAtTime((good ? 1 : 0.67) * this.volume, start);
      this.sounds.set(source, gain);
      source.onended = () => {
        source.disconnect();
        gain.disconnect();
        this.sounds.delete(source);
      };
      source.connect(gain);
      gain.connect(context.destination);
      source.start(start);
    } else {
      const oscillator = context.createOscillator();
      oscillator.type = good ? 'sine' : 'square';
      oscillator.frequency.setValueAtTime(good ? 880 : 180, start);
      gain.gain.setValueAtTime((good ? 1 : 0.67) * this.volume, start);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + (good ? 0.035 : 0.07));
      this.sounds.set(oscillator, gain);
      oscillator.onended = () => {
        oscillator.disconnect();
        gain.disconnect();
        this.sounds.delete(oscillator);
      };
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.start(start);
      oscillator.stop(start + (good ? 0.04 : 0.075));
    }
  }

  stop() {
    for (const [source, gain] of this.sounds) {
      source.onended = null;
      source.stop();
      source.disconnect();
      gain.disconnect();
    }
    this.sounds.clear();
  }

  dispose() {
    this.bufferGeneration++;
    this.stop();
    const context = this.context;
    if (context !== null) {
      void Result.tryPromise(() => context.close()).then((result) => {
        if (result.isErr()) console.warn('Hitsound audio could not close', result.error);
      });
    }
    this.context = null;
    this.goodCutBuffer = null;
    this.badCutBuffer = null;
  }
}
