import { Result, TaggedError } from 'better-result';

type HitsoundSlot = 'good' | 'bad';

class HitsoundStorageError extends TaggedError('HitsoundStorageError')<{
  message: string;
  cause: unknown;
}>() {}

async function getOpfsDirectory(create = false) {
  const root = await navigator.storage.getDirectory();
  return root.getDirectoryHandle('chroviewer_hitsounds', { create });
}

function fileToDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (result === null || result instanceof ArrayBuffer) {
        reject(new Error('FileReader returned an invalid data URL'));
        return;
      }
      resolve(result);
    };
    reader.onerror = () => {
      reject(new Error('FileReader failed', { cause: reader.error }));
    };
    reader.readAsDataURL(file);
  });
}

function dataUrlToArrayBuffer(dataUrl: string) {
  const base64 = dataUrl.split(',')[1];
  if (base64 === undefined) throw new Error('stored hitsound is not a data URL');
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index++) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes.buffer;
}

async function validateAudio(file: File) {
  const contextResult = Result.try({
    try: () => new AudioContext(),
    catch: (cause) => new HitsoundStorageError({ message: 'Audio validation could not start', cause }),
  });
  if (contextResult.isErr()) return contextResult;

  const context = contextResult.value;
  const decoded = await Result.tryPromise({
    try: async () => {
      const buffer = await file.arrayBuffer();
      await context.decodeAudioData(buffer.slice(0));
    },
    catch: (cause) =>
      new HitsoundStorageError({
        message: 'The selected audio file could not be decoded by the browser',
        cause,
      }),
  });
  const closed = await Result.tryPromise({
    try: () => context.close(),
    catch: (cause) => new HitsoundStorageError({ message: 'Audio validation could not finish', cause }),
  });
  return decoded.isErr() ? Result.err(decoded.error) : closed;
}

export async function saveCustomHitsound(slot: HitsoundSlot, file: File) {
  const valid = await validateAudio(file);
  if (valid.isErr()) return valid;

  const filename = `${slot}_hitsound`;
  const opfs = await Result.tryPromise({
    try: async () => {
      const directory = await getOpfsDirectory(true);
      const fileHandle = await directory.getFileHandle(filename, { create: true });
      const writable = await fileHandle.createWritable();
      await writable.write(file);
      await writable.close();
    },
    catch: (cause) => new HitsoundStorageError({ message: 'Custom hitsound could not be saved to OPFS', cause }),
  });
  if (opfs.isOk()) return opfs;

  return Result.tryPromise({
    try: async () => {
      const dataUrl = await fileToDataUrl(file);
      localStorage.setItem(`chroviewer_hitsound_${slot}`, dataUrl);
    },
    catch: (cause) => new HitsoundStorageError({ message: 'Custom hitsound could not be saved', cause }),
  });
}

export async function loadCustomHitsound(slot: HitsoundSlot) {
  const filename = `${slot}_hitsound`;
  const opfs = await Result.tryPromise({
    try: async () => {
      const directory = await getOpfsDirectory();
      const fileHandle = await directory.getFileHandle(filename);
      const file = await fileHandle.getFile();
      return file.arrayBuffer();
    },
    catch: (cause) => new HitsoundStorageError({ message: 'Custom hitsound could not be read from OPFS', cause }),
  });
  if (opfs.isOk()) return opfs;

  const fallback = Result.try({
    try: () => {
      const dataUrl = localStorage.getItem(`chroviewer_hitsound_${slot}`);
      return dataUrl === null ? null : dataUrlToArrayBuffer(dataUrl);
    },
    catch: (cause) => new HitsoundStorageError({ message: 'Custom hitsound fallback could not be read', cause }),
  });
  if (fallback.isOk() && fallback.value !== null) return fallback;
  return opfs.error.cause instanceof DOMException && opfs.error.cause.name === 'NotFoundError'
    ? fallback
    : Result.err(opfs.error);
}

export async function clearCustomHitsound(slot: HitsoundSlot) {
  const filename = `${slot}_hitsound`;
  const opfs = await Result.tryPromise({
    try: async () => {
      const directory = await getOpfsDirectory();
      await directory.removeEntry(filename);
    },
    catch: (cause) => new HitsoundStorageError({ message: 'Custom hitsound could not be removed from OPFS', cause }),
  });
  const fallback = Result.try({
    try: () => {
      localStorage.removeItem(`chroviewer_hitsound_${slot}`);
    },
    catch: (cause) => new HitsoundStorageError({ message: 'Custom hitsound fallback could not be removed', cause }),
  });

  if (opfs.isOk() || fallback.isOk()) return Result.ok(undefined);
  return fallback;
}
