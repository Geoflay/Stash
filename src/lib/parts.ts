/** A video the webview can't play is played in parts the core makes as it plays: seconds
 * of a quick H.264 encode each, appended to a Media Source. */

/** Seconds a part lasts: short enough to start playing soon after a seek. */
export const PART_SECONDS = 10;

/** Start and end of each buffered range, in seconds. */
export type Ranges = [number, number][];

/** Where the last part of a video of `duration` seconds starts. */
export const lastPart = (duration: number) => Math.max(Math.ceil(duration / PART_SECONDS) - 1, 0) * PART_SECONDS;

/** The part to make next, by its start, or `null` when none is needed: the one playing at
 * `time`, then the one after it, so it is ready when playback gets there. A part `loaded`
 * stays there until the webview drops it to make room. */
export function nextPart(time: number, duration: number, loaded: ReadonlySet<number>, buffered: Ranges): number | null {
  const at = Math.min(Math.floor(time / PART_SECONDS) * PART_SECONDS, lastPart(duration));
  const held = (from: number) => loaded.has(from) && buffered.some(([start, end]) => start < from + PART_SECONDS && end > from);
  if (!held(at)) return at;
  const after = at + PART_SECONDS;
  return after < duration && !held(after) ? after : null;
}

const rangesOf = (media: HTMLMediaElement): Ranges =>
  Array.from({ length: media.buffered.length }, (_, i) => [media.buffered.start(i), media.buffered.end(i)]);

/** Plays a video of `duration` seconds in `media`, the part starting at `from` coming from
 * `load`. `first` is where playback will start; `onFail` when a part can't be made or
 * played. Returns what stops it. */
export function playParts(
  media: HTMLMediaElement,
  {
    duration,
    audio,
    first,
    load,
    onFail,
  }: {
    duration: number;
    /** The video has a sound track. */
    audio: boolean;
    first: () => number;
    load: (from: number, seconds: number) => Promise<ArrayBuffer>;
    onFail: () => void;
  },
): () => void {
  // High profile H.264, and AAC.
  const mime = `video/mp4; codecs="avc1.64001f${audio ? ", mp4a.40.2" : ""}"`;
  if (typeof MediaSource === "undefined" || !MediaSource.isTypeSupported(mime)) {
    onFail();
    return () => {};
  }
  const source = new MediaSource();
  const loaded = new Set<number>();
  let buffer: SourceBuffer | null = null;
  // The part being made: a newer one replaces it (the core stops the older).
  let loading: number | null = null;
  let stopped = false;

  function fill() {
    if (stopped || !buffer || buffer.updating) return;
    const time = media.readyState >= 1 ? media.currentTime : first();
    const from = nextPart(time, duration, loaded, rangesOf(media));
    if (from === null || from === loading) return;
    loading = from;
    load(from, PART_SECONDS).then(
      (bytes) => {
        if (loading === from) append(from, bytes);
      },
      () => {
        // Replaced by a newer one, or the core can't make it.
        if (stopped || loading !== from) return;
        stopped = true;
        onFail();
      },
    );
  }

  function append(from: number, bytes: ArrayBuffer) {
    if (stopped || !buffer) return;
    if (buffer.updating) {
      buffer.addEventListener("updateend", () => append(from, bytes), { once: true });
      return;
    }
    try {
      buffer.timestampOffset = from;
      buffer.appendBuffer(bytes);
      loaded.add(from);
    } catch {
      // No room left: what was played long ago goes, then the part is made again.
      loading = null;
      const played = media.currentTime - PART_SECONDS;
      if (played > 0) buffer.remove(0, played);
    }
  }

  function appended() {
    if (loading !== null && loaded.has(loading)) loading = null;
    // Playback ends after the last part.
    if (source.readyState === "open" && loaded.has(lastPart(duration))) {
      try {
        source.endOfStream();
      } catch {
        // Not now: a part is being appended.
      }
    }
    fill();
  }

  source.addEventListener("sourceopen", () => {
    if (buffer) return;
    source.duration = duration;
    buffer = source.addSourceBuffer(mime);
    buffer.addEventListener("updateend", appended);
    fill();
  });
  media.addEventListener("seeking", fill);
  media.addEventListener("timeupdate", fill);
  const url = URL.createObjectURL(source);
  media.src = url;
  return () => {
    stopped = true;
    media.removeEventListener("seeking", fill);
    media.removeEventListener("timeupdate", fill);
    URL.revokeObjectURL(url);
  };
}
