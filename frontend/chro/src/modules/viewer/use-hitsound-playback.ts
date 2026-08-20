import { useEffect, useRef, useState, type RefObject } from 'react';

import { Result } from 'better-result';

import { firstHitsoundAfter, HitsoundPlayer, type HitsoundEvent } from '../../core/clock/hitsounds';
import type { SongClock } from '../../core/clock/song-clock';
import { loadCustomHitsound } from '../../core/hitsound-storage';
import { isForcedLightshowMode, type LightshowMode } from '../../core/lighting/basic-light';
import type { HitsoundPreset, ViewerSettings } from '../../core/viewer-settings';

interface HitsoundPlaybackOptions {
  audioOffset: number;
  clockRef: RefObject<SongClock | null>;
  lightshowModeRef: RefObject<LightshowMode>;
  settingsRef: RefObject<ViewerSettings>;
  volume: number;
  hitsoundPreset: HitsoundPreset;
  customGoodHitsound: string | null;
  customBadHitsound: string | null;
}

export function useHitsoundPlayback({
  audioOffset,
  clockRef,
  lightshowModeRef,
  settingsRef,
  volume,
  hitsoundPreset,
  customGoodHitsound,
  customBadHitsound,
}: HitsoundPlaybackOptions) {
  const [player] = useState(() => new HitsoundPlayer());
  const eventsRef = useRef<HitsoundEvent[]>([]);
  const timeRef = useRef(0);
  const indexRef = useRef(0);

  useEffect(() => {
    player.setVolume(volume);
  }, [player, volume]);

  useEffect(() => {
    const controller = new AbortController();
    void player.setBuffers(null, null);

    void (async () => {
      let goodBuffer: ArrayBuffer | null = null;
      let badBuffer: ArrayBuffer | null = null;

      if (hitsoundPreset === 'custom') {
        const [goodResult, badResult] = await Promise.all([
          customGoodHitsound === null ? Result.ok(null) : loadCustomHitsound('good'),
          customBadHitsound === null ? Result.ok(null) : loadCustomHitsound('bad'),
        ]);
        if (controller.signal.aborted) return;
        if (goodResult.isErr()) {
          console.warn(goodResult.error);
          return;
        }
        if (badResult.isErr()) {
          console.warn(badResult.error);
          return;
        }
        goodBuffer = goodResult.value;
        badBuffer = badResult.value;
      } else if (hitsoundPreset !== 'default') {
        const response = await Result.tryPromise({
          try: () => fetch(`${import.meta.env.BASE_URL}hitsounds/${hitsoundPreset}.wav`, { signal: controller.signal }),
          catch: (cause) => new Error(`Hitsound preset ${hitsoundPreset} could not be loaded`, { cause }),
        });
        if (controller.signal.aborted) return;
        if (response.isErr()) {
          console.warn(response.error);
          return;
        }
        if (!response.value.ok) {
          console.warn(
            new Error(`Hitsound preset ${hitsoundPreset} could not be loaded (${String(response.value.status)})`),
          );
          return;
        }
        const buffer = await Result.tryPromise({
          try: () => response.value.arrayBuffer(),
          catch: (cause) => new Error(`Hitsound preset ${hitsoundPreset} could not be read`, { cause }),
        });
        if (buffer.isErr()) {
          console.warn(buffer.error);
          return;
        }
        goodBuffer = buffer.value;
      }

      if (controller.signal.aborted) return;
      const updated = await player.setBuffers(goodBuffer, badBuffer);
      if (updated.isErr()) console.warn(updated.error);
    })();
    return () => {
      controller.abort();
    };
  }, [player, hitsoundPreset, customGoodHitsound, customBadHitsound]);

  useEffect(() => {
    player.stop();
    const clock = clockRef.current;
    const time = clock?.currentTime() ?? 0;
    const scaledAudioOffset = audioOffset * (clock?.getRate() ?? 1);
    timeRef.current = time;
    indexRef.current = firstHitsoundAfter(eventsRef.current, time - scaledAudioOffset);
  }, [audioOffset, clockRef, player]);

  useEffect(() => {
    let frame = 0;

    function schedule() {
      const clock = clockRef.current;
      if (clock !== null) {
        const currentTime = clock.currentTime();
        const rate = clock.getRate();
        const scaledAudioOffset = audioOffset * rate;
        if (!clock.isPlaying()) {
          timeRef.current = currentTime - 1e-6;
          indexRef.current = firstHitsoundAfter(eventsRef.current, timeRef.current - scaledAudioOffset);
        } else {
          const horizon = Math.min(currentTime + 0.04 * rate, clock.duration);
          const events = eventsRef.current;
          let index = indexRef.current;
          if (currentTime > timeRef.current) {
            const nextTime = currentTime - 1e-6;
            if (nextTime > timeRef.current) {
              timeRef.current = nextTime;
              index = firstHitsoundAfter(events, timeRef.current - scaledAudioOffset);
            }
          }
          while (index < events.length) {
            const event = events[index];
            if (event === undefined || event.time + scaledAudioOffset > horizon) break;
            if (
              event.time + scaledAudioOffset > timeRef.current &&
              settingsRef.current.hitsounds &&
              !isForcedLightshowMode(lightshowModeRef.current)
            ) {
              player.play(event.good, (event.time + scaledAudioOffset - currentTime) / rate);
            }
            index++;
          }
          indexRef.current = index;
          timeRef.current = horizon;
        }
      }
      frame = requestAnimationFrame(schedule);
    }

    frame = requestAnimationFrame(schedule);
    return () => {
      cancelAnimationFrame(frame);
    };
  }, [audioOffset, clockRef, lightshowModeRef, settingsRef]);

  useEffect(
    () => () => {
      player.dispose();
    },
    [player],
  );

  function clear() {
    eventsRef.current = [];
    timeRef.current = 0;
    indexRef.current = 0;
  }

  function load(events: HitsoundEvent[]) {
    eventsRef.current = events;
    timeRef.current = 0;
    const rate = clockRef.current?.getRate() ?? 1;
    indexRef.current = firstHitsoundAfter(events, -audioOffset * rate);
  }

  function resume() {
    player.resume();
  }

  function seek(time: number) {
    timeRef.current = time;
    const rate = clockRef.current?.getRate() ?? 1;
    indexRef.current = firstHitsoundAfter(eventsRef.current, time - audioOffset * rate);
  }

  return {
    clear,
    load,
    resume,
    seek,
  };
}
