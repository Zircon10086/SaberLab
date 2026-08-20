import { z } from 'zod';

export type BeatSaberJsonValue = null | boolean | number | string | BeatSaberJsonValue[] | BeatSaberJsonObject;

export interface BeatSaberJsonObject {
  [key: string]: BeatSaberJsonValue;
}

export function beatSaberNumber(value: BeatSaberJsonValue | undefined) {
  if (value?.constructor === Number) return Number(value);
  if (value?.constructor === String) {
    const parsed = Number.parseFloat(String(value));
    return Number.isNaN(parsed) ? 0 : parsed;
  }
  if (value?.constructor === Boolean) return Number(value);
  return 0;
}

export function beatSaberString(value: BeatSaberJsonValue | undefined) {
  return value?.constructor === String || value?.constructor === Number || value?.constructor === Boolean
    ? String(value)
    : '';
}

export function beatSaberBoolean(value: BeatSaberJsonValue | undefined) {
  if (value?.constructor === Boolean) return Boolean(value);
  if (value?.constructor === Number) return Number(value) !== 0;
  return value?.constructor === String && String(value) === 'true';
}

export function beatSaberStringArray(value: BeatSaberJsonValue | undefined) {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => (entry?.constructor === String ? [String(entry)] : []));
}

export function beatSaberTrack(value: BeatSaberJsonValue | undefined) {
  return value?.constructor === String ? [String(value)] : beatSaberStringArray(value);
}

const beatSaberValueSchema = z.custom<BeatSaberJsonValue | undefined>();

export const beatSaberNumberSchema = beatSaberValueSchema.transform(beatSaberNumber);

export const beatSaberIntegerSchema = beatSaberNumberSchema.transform(Math.trunc);

export const beatSaberStringSchema = beatSaberValueSchema.transform(beatSaberString);

export const beatSaberBooleanSchema = beatSaberValueSchema.transform(beatSaberBoolean);

// JSON.parse establishes this invariant before domain schemas consume the value
export const beatSaberJsonValueSchema = z.custom<BeatSaberJsonValue>();
export const beatSaberJsonTextSchema = z.string().transform((text) => beatSaberJsonValueSchema.parse(JSON.parse(text)));

export const beatSaberJsonObjectSchema = z.custom<BeatSaberJsonObject>(
  (value) => value !== null && Object(value) === value && !Array.isArray(value),
);

export const beatSaberJsonArraySchema = z.custom<BeatSaberJsonValue[]>(Array.isArray);

export const beatSaberStringArraySchema = beatSaberValueSchema.transform(beatSaberStringArray);

export const beatSaberTrackSchema = beatSaberValueSchema.transform(beatSaberTrack);

export const beatSaberVector3Schema = z
  .array(beatSaberJsonValueSchema)
  .min(3)
  .transform(([x, y, z]): [number, number, number] => [
    beatSaberNumberSchema.parse(x),
    beatSaberNumberSchema.parse(y),
    beatSaberNumberSchema.parse(z),
  ]);
