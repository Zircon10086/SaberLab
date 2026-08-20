import { replayCutScore, replayNoteMaximumScore } from './scoring';
import type {
  Replay,
  ReplayHeightEvent,
  ReplayMetadata,
  ReplayNoteEvent,
  ReplayNoteEventType,
  ReplayPauseEvent,
  ReplayPose,
  ReplayQuaternion,
  ReplayVector3,
  ReplayWallEvent,
  ReplayScoreEvent,
  ReplayComboEvent,
  ReplayMultiplierEvent,
  ReplayEnergyEvent,
} from './types';

export const BEATLEADER_REPLAY_MAGIC = 0x442d3d69;

const maxListItems = 2_000_000;
const decoder = new TextDecoder('utf-8', { fatal: true });

class BinaryReader {
  private readonly view: DataView;
  private readonly limit: number;
  offset = 0;

  constructor(private readonly bytes: Uint8Array) {
    this.view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    this.limit = bytes.byteLength;
  }

  private require(length: number) {
    if (!Number.isInteger(length) || length < 0 || this.offset + length > this.limit) {
      throw new Error('truncated BeatLeader replay');
    }
  }

  byte() {
    this.require(1);
    return this.bytes[this.offset++] ?? 0;
  }

  int32() {
    this.require(4);
    const value = this.view.getInt32(this.offset, true);
    this.offset += 4;
    return value;
  }

  int64() {
    this.require(8);
    const value = this.view.getBigInt64(this.offset, true);
    this.offset += 8;
    return value;
  }

  float32() {
    this.require(4);
    const value = this.view.getFloat32(this.offset, true);
    this.offset += 4;
    if (!Number.isFinite(value)) throw new Error('BeatLeader replay contains a non-finite number');
    return value;
  }

  bool() {
    return this.byte() !== 0;
  }

  string() {
    const length = this.int32();
    this.require(length);
    let value: string;
    try {
      value = decoder.decode(this.bytes.subarray(this.offset, this.offset + length));
    } catch {
      throw new Error('BeatLeader replay contains invalid UTF-8');
    }
    this.offset += length;
    return value;
  }

  count(label: string) {
    const count = this.int32();
    if (count < 0 || count > maxListItems) {
      throw new Error(`invalid BeatLeader replay ${label} count`);
    }
    return count;
  }

  skip(length: number) {
    this.require(length);
    this.offset += length;
  }
}

export function isBeatLeaderReplay(data: Uint8Array) {
  if (data.byteLength < 4) return false;
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  return view.getInt32(0, true) === BEATLEADER_REPLAY_MAGIC;
}

function vector3(reader: BinaryReader): ReplayVector3 {
  return { x: reader.float32(), y: reader.float32(), z: reader.float32() };
}

function quaternion(reader: BinaryReader): ReplayQuaternion {
  return { ...vector3(reader), w: reader.float32() };
}

const legacyDifficultyRanks = new Map(
  Object.entries({
    easy: 1,
    normal: 3,
    hard: 5,
    expert: 7,
    expertplus: 9,
  }),
);

export function parseBeatLeaderReplay(data: Uint8Array): Replay {
  const reader = new BinaryReader(data);
  const magic = reader.int32();
  if (magic !== BEATLEADER_REPLAY_MAGIC) throw new Error('invalid BeatLeader replay magic number');
  const version = reader.byte();

  let metadata: ReplayMetadata | undefined;
  const poses: ReplayPose[] = [];
  const notes: ReplayNoteEvent[] = [];
  const walls: ReplayWallEvent[] = [];
  const heights: ReplayHeightEvent[] = [];
  const pauses: ReplayPauseEvent[] = [];
  let songStartTime = 0;

  while (reader.offset < data.byteLength) {
    const section = reader.byte();
    switch (section) {
      case 0: {
        const modVersion = reader.string();
        const gameVersion = reader.string();
        void reader.string();
        const playerId = reader.string();
        const playerName = reader.string();
        const platform = reader.string();
        void reader.string();
        void reader.string();
        void reader.string();
        const hash = reader.string();
        void reader.string();
        void reader.string();
        const difficultyStr = reader.string();
        void reader.int32();
        const mode = reader.string();
        const environment = reader.string();
        const modifiers = reader.string();
        const jumpDistance = reader.float32();
        const leftHanded = reader.bool();
        const height = reader.float32();
        const startTime = reader.float32();
        const failTime = reader.float32();
        const speed = reader.float32();

        metadata = {
          version: `BeatLeader ${version}`,
          levelId: `custom_level_${hash.toUpperCase()}`,
          difficulty: legacyDifficultyRanks.get(difficultyStr.toLowerCase()) ?? 1,
          characteristic: mode,
          environment,
          modifiers: modifiers === '' ? [] : modifiers.split(','),
          noteSpawnOffset: 0,
          leftHanded,
          initialHeight: height,
          roomRotation: 0,
          roomCenter: { x: 0, y: 0, z: 0 },
          failTime,
          gameVersion,
          pluginVersion: modVersion,
          platform,
          hasPlaySettings: true,
          songSpeed: speed,
          jumpDistance,
          player: playerId !== '' ? { id: playerId, name: playerName } : undefined,
        };
        songStartTime = startTime;
        break;
      }
      case 1: {
        const framesCount = reader.count('frames');
        for (let i = 0; i < framesCount; i++) {
          poses.push({
            time: reader.float32(),
            fps: reader.int32(),
            head: { position: vector3(reader), rotation: quaternion(reader) },
            leftHand: { position: vector3(reader), rotation: quaternion(reader) },
            rightHand: { position: vector3(reader), rotation: quaternion(reader) },
          });
        }
        break;
      }
      case 2: {
        const noteCount = reader.count('notes');
        for (let i = 0; i < noteCount; i++) {
          const rawNoteId = reader.int32();
          const scoringTypeDivisor = rawNoteId < 100_000 ? 10_000 : 10_000_000;
          const lineIndexDivisor = scoringTypeDivisor / 10;
          const lineLayerDivisor = lineIndexDivisor / 10;
          const rawScoringType = Math.floor(rawNoteId / scoringTypeDivisor);
          const lineIndex = Math.floor((rawNoteId % scoringTypeDivisor) / lineIndexDivisor);
          const noteLineLayer = Math.floor((rawNoteId % lineIndexDivisor) / lineLayerDivisor);
          const colorType = Math.floor((rawNoteId % 100) / 10);
          const cutDirection = rawNoteId % 10;

          const eventTime = reader.float32();
          const spawnTime = reader.float32();
          const eventTypeRaw = reader.int32();
          const scoringType =
            eventTypeRaw !== 3 && (rawScoringType < 3 || rawScoringType > 12) ? 1 : rawScoringType - 2;

          let eventType: ReplayNoteEventType;
          switch (eventTypeRaw) {
            case 0:
              eventType = 1;
              break;
            case 1:
              eventType = 2;
              break;
            case 2:
              eventType = 3;
              break;
            case 3:
              eventType = 4;
              break;
            default:
              eventType = 0;
          }

          let cutPoint = { x: 0, y: 0, z: 0 };
          let cutNormal = { x: 0, y: 0, z: 0 };
          let saberDirection = { x: 0, y: 0, z: 0 };
          let saberType = 0;
          let directionOk = false;
          let saberSpeed = 0;
          let cutAngle = 0;
          let cutDistanceToCenter = 0;
          let cutDirectionDeviation = 0;
          let beforeCutRating = 0;
          let afterCutRating = 0;
          let timeDeviation = 0;

          if (eventTypeRaw === 0 || eventTypeRaw === 1) {
            void reader.bool();
            directionOk = reader.bool();
            void reader.bool();
            void reader.bool();
            saberSpeed = reader.float32();
            saberDirection = vector3(reader);
            saberType = reader.int32();
            timeDeviation = reader.float32();
            cutDirectionDeviation = reader.float32();
            cutPoint = vector3(reader);
            cutNormal = vector3(reader);
            cutDistanceToCenter = reader.float32();
            cutAngle = reader.float32();
            beforeCutRating = reader.float32();
            afterCutRating = reader.float32();
          }

          notes.push({
            noteId: {
              time: spawnTime,
              lineLayer: noteLineLayer,
              lineIndex,
              colorType,
              cutDirection,
              scoringType,
            },
            eventType,
            cutPoint,
            cutNormal,
            saberDirection,
            saberType,
            directionOk,
            saberSpeed,
            cutAngle,
            cutDistanceToCenter,
            cutDirectionDeviation,
            beforeCutRating,
            afterCutRating,
            time: eventTime,
            unityTimescale: 1,
            timeSyncTimescale: 1,
            timeDeviation,
          });
        }
        break;
      }
      case 3: {
        const wallCount = reader.count('walls');
        for (let i = 0; i < wallCount; i++) {
          const rawWallId = reader.int32();
          const energy = reader.float32();
          const time = reader.float32();
          const spawnTime = reader.float32();

          const lineIndex = Math.floor(rawWallId / 100);
          const obstacleType = Math.floor((rawWallId % 100) / 10);
          const width = rawWallId % 10;

          walls.push({
            time,
            exitTime: time,
            energy,
            obstacleTime: spawnTime,
            obstacleDuration: 0,
            lineIndex,
            lineLayer: obstacleType,
            width,
            height: 0,
          });
        }
        break;
      }
      case 4: {
        const heightCount = reader.count('heights');
        for (let i = 0; i < heightCount; i++) {
          heights.push({
            height: reader.float32(),
            time: reader.float32(),
          });
        }
        break;
      }
      case 5: {
        const pauseCount = reader.count('pauses');
        for (let i = 0; i < pauseCount; i++) {
          const duration = reader.int64();
          const time = reader.float32();
          const unixStartTime = BigInt(Math.round(songStartTime + time));
          const durationMs = duration * 1000n;
          const unixEndTime = unixStartTime + duration;
          pauses.push({
            duration: durationMs,
            time,
            unixStartTime,
            unixEndTime,
          });
        }
        break;
      }
      case 6: {
        if (metadata) {
          metadata.controllerOffsets = {
            left: { position: vector3(reader), rotation: quaternion(reader) },
            right: { position: vector3(reader), rotation: quaternion(reader) },
          };
        } else {
          vector3(reader);
          quaternion(reader);
          vector3(reader);
          quaternion(reader);
        }
        break;
      }
      case 7: {
        const customDataCount = reader.count('custom data');
        for (let i = 0; i < customDataCount; i++) {
          reader.string();
          reader.skip(reader.int32());
        }
        break;
      }
      default:
        reader.offset = data.byteLength;
        break;
    }
  }

  if (!metadata) throw new Error('BeatLeader replay is missing info section');

  const scores: ReplayScoreEvent[] = [];
  const combos: ReplayComboEvent[] = [];
  const multipliers: ReplayMultiplierEvent[] = [];

  let currentScore = 0;
  let currentCombo = 0;
  let multiplier = 1;
  let progress = 0;
  let immediateMax = 0;
  let maxPossibleMultiplier = 1;
  let maxPossibleProgress = 0;

  let currentEnergy = 0.5;
  const energies: ReplayEnergyEvent[] = [{ time: 0, energy: 0.5 }];

  type SimulationEvent =
    | { type: 'note'; data: ReplayNoteEvent; time: number }
    | { type: 'wall'; data: ReplayWallEvent; time: number };

  const simulationEvents: SimulationEvent[] = [
    ...notes.map<SimulationEvent>((data) => ({ type: 'note', data, time: data.time })),
    ...walls.map<SimulationEvent>((data) => ({ type: 'wall', data, time: data.time })),
  ].sort((a, b) => a.time - b.time);

  for (const event of simulationEvents) {
    if (event.type === 'wall') {
      currentEnergy = event.data.energy;
      currentCombo = 0;
      if (multiplier > 1) multiplier /= 2;
      progress = 0;
      combos.push({ time: event.time, combo: currentCombo });
      energies.push({ time: event.time, energy: currentEnergy });
      multipliers.push({ time: event.time, multiplier, nextMultiplierProgress: 0 });
      continue;
    }

    const note = event.data;
    const chainLink = note.noteId.scoringType === 5 || note.noteId.scoringType === 8;

    if (note.eventType === 1) {
      currentCombo++;
      progress++;
      if (multiplier < 8 && progress >= multiplier * 2) {
        multiplier *= 2;
        progress = 0;
      }

      maxPossibleProgress++;
      if (maxPossibleMultiplier < 8 && maxPossibleProgress >= maxPossibleMultiplier * 2) {
        maxPossibleMultiplier *= 2;
        maxPossibleProgress = 0;
      }
      immediateMax += replayNoteMaximumScore(note) * maxPossibleMultiplier;

      const cutScore = replayCutScore(note);
      if (cutScore !== undefined) currentScore += cutScore.total * multiplier;
    } else if (note.eventType === 2 || note.eventType === 3) {
      currentCombo = 0;
      if (multiplier > 1) {
        multiplier /= 2;
      }
      progress = 0;

      maxPossibleProgress++;
      if (maxPossibleMultiplier < 8 && maxPossibleProgress >= maxPossibleMultiplier * 2) {
        maxPossibleMultiplier *= 2;
        maxPossibleProgress = 0;
      }
      immediateMax += replayNoteMaximumScore(note) * maxPossibleMultiplier;
      currentEnergy = Math.max(0, currentEnergy - (chainLink ? (note.eventType === 2 ? 0.025 : 0.03) : 0.15));
    } else if (note.eventType === 4) {
      currentCombo = 0;
      if (multiplier > 1) {
        multiplier /= 2;
      }
      progress = 0;
      currentEnergy = Math.max(0, currentEnergy - 0.15);
    }

    if (note.eventType === 1) {
      currentEnergy = Math.min(1, currentEnergy + (chainLink ? 0.002 : 0.01));
    }

    scores.push({ time: note.time, score: currentScore, immediateMaxPossibleScore: immediateMax });
    combos.push({ time: note.time, combo: currentCombo });
    energies.push({ time: note.time, energy: currentEnergy });
    multipliers.push({
      time: note.time,
      multiplier,
      nextMultiplierProgress: multiplier === 8 ? 0 : progress / (multiplier * 2),
    });
  }

  return {
    metadata,
    poses,
    heights,
    notes,
    scores,
    combos,
    multipliers,
    energies,
    pauses,
    walls,
  };
}
