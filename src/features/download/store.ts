import { invoke, type InvokeArgs } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { useEffect } from "react";
import { create } from "zustand";
import { findPreset, useSettings, type Failure, type Preset, type Settings } from "@/features/settings/settings";

/** One raw stream and the tracks taken from it. */
export interface Part {
  formatId: string;
  video: boolean;
  audio: boolean;
  size: number | null;
}

/** A video height (or audio only) and the streams it needs. */
export interface Choice {
  height: number | null;
  size: number | null;
  /** Frames per second, when the site says. */
  fps: number | null;
  parts: Part[];
}

/** A photo of a TikTok photo post. */
export interface Photo {
  url: string;
  width: number;
  height: number;
}

/** A part SponsorBlock lists to skip, in seconds of the video. */
export interface Segment {
  start: number;
  end: number;
  category: string;
}

/** A part cut out, from and to, in seconds of the source. */
export type Cut = [number, number];

export interface Media {
  url: string;
  title: string;
  author: string | null;
  duration: number | null;
  thumbnail: string | null;
  info: string;
  /** Best first. */
  qualities: Choice[];
  audio: Choice | null;
  /** A TikTok photo post's photos; its sound is `audio`. */
  photos: Photo[];
  /** A small MP4 the player can stream before downloading. */
  preview: string | null;
  /** The parts SponsorBlock lists to skip (YouTube), in order. */
  segments: Segment[];
}

export type Step = "queued" | "download" | "mux" | "encode" | "done" | "cancelled" | "failed";

export interface JobEvent {
  id: number;
  step: Step;
  fraction: number | null;
  eta: number | null;
  /** The finished file: a path on Windows, a content URI on Android. */
  path: string | null;
  error: Failure | null;
}

/** The command and request that started a job, to start it again. */
export interface JobStart {
  command: "start_download" | "start_edit";
  request: object;
}

export interface Job extends JobEvent {
  title: string;
  /** The result is a video or a sound the Edit tab can open (not a folder of photos). */
  editable: boolean;
  start?: JobStart;
  /** Renamed while it ran: the file is renamed once it is saved. */
  renamed?: boolean;
}

/** Queued or running: not finished, cancelled or failed. */
export function isActive({ step }: Job): boolean {
  return step !== "done" && step !== "cancelled" && step !== "failed";
}

export type VideoFormat = "mp4" | "mkv" | "mov";
export type AudioFormat = "m4a" | "mp3" | "flac" | "wav";
export type Format = VideoFormat | AudioFormat;
export type VideoCodec = "h264" | "h265" | "av1";
export type AudioCodec = "aac" | "mp3" | "flac" | "pcm";
export type Quality = "best" | number;

export const VIDEO_FORMATS: VideoFormat[] = ["mp4", "mkv", "mov"];
export const AUDIO_FORMATS: AudioFormat[] = ["m4a", "mp3", "flac", "wav"];

/** The sound codecs each video file can hold, the usual one first; mirrors
 * `Container::audio_codecs` in the Rust core. The picture is always written in H.264. */
export const AUDIO_CODECS: Record<VideoFormat, AudioCodec[]> = {
  mp4: ["aac", "mp3", "flac"],
  mkv: ["aac", "mp3", "flac", "pcm"],
  mov: ["aac", "mp3", "pcm"],
};

/** The sound-only file of each codec. */
export const SOUND_FILE: Record<AudioCodec, AudioFormat> = { aac: "m4a", mp3: "mp3", flac: "flac", pcm: "wav" };

export function isAudioFormat(format: Format): format is AudioFormat {
  return (AUDIO_FORMATS as Format[]).includes(format);
}

/** The kind of file saved and its sound codec (a sound-only file's kind sets its codec). */
export interface Output {
  format: Format;
  audioCodec: AudioCodec;
}

export const defaultOutput: Output = { format: "mp4", audioCodec: "aac" };

/** Another kind of file: a sound codec it can't hold becomes its first one. */
export function withFormat(output: Output, format: Format): Output {
  if (isAudioFormat(format)) return { ...output, format };
  const audioCodec = AUDIO_CODECS[format].includes(output.audioCodec) ? output.audioCodec : AUDIO_CODECS[format][0];
  return { format, audioCodec };
}

/** The file saved as `format`, in the sound codec of the settings (AAC until they load). */
export function outputOf(format: Format, settings: Pick<Settings, "audioCodec"> | null): Output {
  return withFormat({ format: "mp4", audioCodec: settings?.audioCodec ?? "aac" }, format);
}

/** The sound-only file of the settings' sound codec (AAC: M4A). */
export function soundFile(settings: Pick<Settings, "audioCodec"> | null): AudioFormat {
  return SOUND_FILE[settings?.audioCodec ?? "aac"];
}

/** FLAC and WAV sound: no size limit, their size can't be chosen. */
export function isLossless({ format, audioCodec }: Output): boolean {
  return format === "flac" || format === "wav" || (!isAudioFormat(format) && (audioCodec === "flac" || audioCodec === "pcm"));
}

/** Edits applied after the download; times are in seconds of the source. */
export interface Options {
  start: number | null;
  end: number | null;
  mute: boolean;
  speed: number;
  maxMb: number | null;
  /** A lower frame rate; `null` keeps the source's. */
  fps: number | null;
}

export const noOptions: Options = { start: null, end: null, mute: false, speed: 1, maxMb: null, fps: null };

/** What to save from a photo post: a slideshow, one photo over the sound, the photos, or the sound. */
export type PhotoMode = "slideshow" | "single" | "photos" | "sound";

interface DownloadState {
  url: string;
  media: Media | null;
  analyzing: boolean;
  error: Failure | null;
  /** The kind of file saved; the settings choose the codecs. */
  format: Format;
  quality: Quality;
  options: Options;
  photoMode: PhotoMode;
  /** The photo for "single". */
  photoIndex: number;
  /** Seconds per photo in a slideshow; `null` shares the sound out. */
  photoSeconds: number | null;
  /** The preset last chosen, until an option it sets changes. */
  preset: string | null;
}

export const useDownloads = create<DownloadState>(() => ({
  url: "",
  media: null,
  analyzing: false,
  error: null,
  format: "mp4",
  quality: "best",
  options: noOptions,
  photoMode: "slideshow",
  photoIndex: 0,
  photoSeconds: null,
  preset: null,
}));

const set = useDownloads.setState;

/** Download and edit jobs, newest first: apart from the choices, as they change often. */
export const useJobs = create<{ jobs: Job[] }>(() => ({ jobs: [] }));

/** The streams the current options ask for. */
export function selectedChoice({ media, format, quality }: DownloadState): Choice | null {
  if (!media) return null;
  if (isAudioFormat(format)) return media.audio;
  return media.qualities.find((q) => q.height === quality) ?? media.qualities[0] ?? null;
}

/** The options sent to the core for a result in `output` (the core ignores those that
 * don't apply, such as the frame rate of a sound). */
export function requestOptions(options: Options, output: Output) {
  return { ...options, ...output };
}

/** Whether `patch` changes an option presets set (all but the extract). */
export function setsPresetOption(patch: object): boolean {
  return Object.keys(patch).some((key) => key !== "start" && key !== "end");
}

export function setOptions(patch: Partial<Options>) {
  set(({ options, preset }) => ({ options: { ...options, ...patch }, preset: setsPresetOption(patch) ? null : preset }));
}

/** The options a preset made now would hold. */
export function presetOptions({ format, quality, options }: DownloadState): Omit<Preset, "name"> {
  const { mute, speed, fps, maxMb } = options;
  return { format, height: quality === "best" ? null : quality, mute, speed, fps, maxMb };
}

/** The highest quality of `media` up to `height` (its lowest if all are higher). */
function qualityUpTo(media: Media | null, height: number | null): Quality {
  if (height === null) return "best";
  if (!media) return height;
  const [best] = media.qualities;
  const fit = media.qualities.find((q) => q.height !== null && q.height <= height) ?? media.qualities[media.qualities.length - 1];
  return !fit || fit === best || fit.height === null ? "best" : fit.height;
}

/** Applies `preset` to the link: its kind of file when the link has it, and the highest
 * quality up to its own. */
export function applyPreset(preset: Preset) {
  set(({ media, format, options }) => {
    const wanted = preset.format === "original" ? format : preset.format;
    const can = !media || (isAudioFormat(wanted) ? media.audio !== null : media.qualities.length > 0);
    const { mute, speed, fps, maxMb } = preset;
    return {
      preset: preset.name,
      format: can ? wanted : format,
      quality: qualityUpTo(media, preset.height),
      options: { ...options, mute, speed, fps, maxMb },
    };
  });
}

/** Saves the download under another name. */
export function renameMedia(title: string) {
  set(({ media }) => ({ media: media && { ...media, title } }));
}

/** Seconds each of `count` photos shows: the sound's length shared out, or `chosen`
 * (at least 0.5 s). Without sound: 3 s each, and at least 5 s for a single photo. */
export function slideSeconds(count: number, sound: number | null, chosen: number | null): number {
  const seconds = Math.max(chosen ?? (sound !== null ? sound / Math.max(count, 1) : 3), 0.5);
  return sound === null && count === 1 ? Math.max(seconds, 5) : seconds;
}

/** The length of the sound under a photo post's video; `null` without one. */
export function slideSound({ media, options }: Pick<DownloadState, "media" | "options">): number | null {
  return !options.mute && media?.audio ? media.duration : null;
}

/** The video made from a photo post's photos: the photos used and the seconds each shows. */
export function slides(state: DownloadState) {
  const { media, photoMode, photoIndex, photoSeconds } = state;
  if (!media?.photos.length || (photoMode !== "slideshow" && photoMode !== "single")) return null;
  const photos = photoMode === "single" ? [media.photos[photoIndex] ?? media.photos[0]] : media.photos;
  return { photos, seconds: slideSeconds(photos.length, slideSound(state), photoMode === "single" ? null : photoSeconds) };
}

/** Length of what will be saved, before edits; `null` when unknown or not a video or sound. */
export function resultDuration(state: DownloadState): number | null {
  const made = slides(state);
  if (made) return made.photos.length * made.seconds;
  if (state.media?.photos.length && state.photoMode === "photos") return null;
  return state.media?.duration ?? null;
}

/** The parts of the settings' SponsorBlock categories inside the extract, in order and
 * apart; none when SponsorBlock is off. */
export function cutsOf(
  { media, options }: Pick<DownloadState, "media" | "options">,
  settings: Pick<Settings, "sponsorBlock" | "sponsorCategories"> | null,
): Cut[] {
  if (!media?.segments.length || !settings?.sponsorBlock) return [];
  const start = options.start ?? 0;
  const end = options.end ?? media.duration ?? Number.POSITIVE_INFINITY;
  const parts = media.segments
    .filter((s) => (settings.sponsorCategories as string[]).includes(s.category))
    .map((s): Cut => [Math.max(s.start, start), Math.min(s.end, end)])
    .filter(([a, b]) => b - a > 0.01)
    .sort((x, y) => x[0] - y[0]);
  const cuts: Cut[] = [];
  for (const [a, b] of parts) {
    const last = cuts[cuts.length - 1];
    if (last && a <= last[1]) last[1] = Math.max(last[1], b);
    else cuts.push([a, b]);
  }
  return cuts;
}

/** Seconds the `cuts` remove. */
export function cutLength(cuts: Cut[]): number {
  return cuts.reduce((sum, [a, b]) => sum + b - a, 0);
}

/** The `start_download` request for the current choices, saved as `output` without the
 * `cuts`; `null` when there is nothing to download. */
export function buildRequest(state: DownloadState, output: Output, cuts: Cut[] = []) {
  const { media, options } = state;
  if (!media) return null;
  const { info, title } = media;
  if (media.photos.length) {
    const sound = media.audio?.parts ?? [];
    const made = slides(state);
    if (made) {
      return { info, title, parts: sound, options, photos: { list: made.photos, files: false, seconds: made.seconds } };
    }
    if (state.photoMode === "photos") {
      return { info, title, parts: [], options: noOptions, photos: { list: media.photos, files: true } };
    }
    return sound.length ? { info, title, parts: sound, options } : null;
  }
  const choice = selectedChoice(state);
  if (!choice) return null;
  return { info, title, parts: choice.parts, options: { ...requestOptions(options, output), cuts } };
}

/** Rough size of `output`: the kept part of the streams (without the `cuts`), capped by the
 * size limit. Unknown when re-encoded to other codecs, unless a limit caps it. */
export function estimate(state: DownloadState, output: Output, cuts: Cut[] = []): number | null {
  if (state.media?.photos.length) return null;
  const choice = selectedChoice(state);
  const { options, media } = state;
  const sound = isAudioFormat(output.format);
  const limit = options.maxMb && !isLossless(output) ? options.maxMb * 1e6 : null;
  const copied = sound ? output.format === "m4a" : output.audioCodec === "aac";
  if (choice?.size == null || !copied) return limit;
  // Fewer frames, about that much less picture.
  const frames = !sound && options.fps !== null && choice.fps ? Math.min(options.fps / choice.fps, 1) : 1;
  let size = 0;
  for (const part of choice.parts) {
    if (part.video) size += (part.size ?? 0) * frames;
    else if (!(options.mute && !sound)) size += part.size ?? 0;
  }
  const duration = media?.duration;
  if (duration) size *= ((options.end ?? duration) - (options.start ?? 0) - cutLength(cuts)) / duration;
  size /= options.speed;
  return limit ? Math.min(size, limit) : size;
}

/** Adds or updates a job; events can arrive before `start_download` returns. */
function upsert(id: number, patch: Partial<Job>) {
  useJobs.setState(({ jobs }) => {
    const index = jobs.findIndex((j) => j.id === id);
    if (index < 0) {
      const job: Job = { id, title: "", editable: true, step: "queued", fraction: null, eta: null, path: null, error: null, ...patch };
      return { jobs: [job, ...jobs] };
    }
    return { jobs: jobs.map((j, i) => (i === index ? { ...j, ...patch } : j)) };
  });
}

/** Starts a job from either tab and lists it; throws when the core refuses it. */
export async function startJob(start: JobStart, title: string, editable = true) {
  const id = await invoke<number>(start.command, { request: start.request });
  upsert(id, { title, editable, start });
}

/** Starts a failed job again, in place of the old one. */
export async function retryJob(job: Job) {
  if (!job.start) return;
  try {
    await startJob(job.start, job.title, job.editable);
    useJobs.setState(({ jobs }) => ({ jobs: jobs.filter((j) => j.id !== job.id) }));
  } catch (error) {
    set({ error: error as Failure });
  }
}

/** Renames a job's file: at once when it is saved, otherwise once it is; a retry uses the new name. */
export async function renameJob(job: Job, title: string) {
  const start = job.start && { ...job.start, request: { ...job.start.request, title } };
  upsert(job.id, { title, start, renamed: isActive(job) });
  if (job.step === "done" && job.path) await renameFile(job.id, job.path, title);
}

async function renameFile(id: number, path: string, title: string) {
  try {
    upsert(id, { path: await invoke<string>("rename_file", { path, title }) });
  } catch (error) {
    set({ error: error as Failure });
  }
}

/** Takes the finished, cancelled and failed jobs off the list. */
export function clearFinished() {
  useJobs.setState(({ jobs }) => ({ jobs: jobs.filter(isActive) }));
}

export async function analyze() {
  const url = useDownloads.getState().url.trim();
  if (!url) return;
  set(({ options }) => ({
    analyzing: true,
    error: null,
    media: null,
    options: { ...options, start: null, end: null, fps: null },
    photoMode: "slideshow",
    photoIndex: 0,
    photoSeconds: null,
  }));
  try {
    const media = await invoke<Media>("analyze", { url });
    // A link with no video saves the sound, in the codec of the settings.
    const { format } = useDownloads.getState();
    const sound = isAudioFormat(format) ? format : soundFile(useSettings.getState().settings);
    set({ media, format: media.qualities.length ? format : sound });
    // The preset chosen applies to the new link too.
    const preset = findPreset(useDownloads.getState().preset);
    if (preset && !media.photos.length) applyPreset(preset);
  } catch (error) {
    set({ error: error as Failure });
  } finally {
    set({ analyzing: false });
  }
}

export async function download() {
  const state = useDownloads.getState();
  const { settings } = useSettings.getState();
  const request = buildRequest(state, outputOf(state.format, settings), cutsOf(state, settings));
  if (!request) return;
  try {
    await startJob({ command: "start_download", request }, request.title, !request.photos?.files);
  } catch (error) {
    set({ error: error as Failure });
  }
}

/** Runs a job action (cancel, open…) and shows its error, if any. */
export function jobAction(command: "cancel_download" | "open_file" | "reveal_file" | "share_file", args: InvokeArgs) {
  invoke(command, args).catch((error: Failure) => set({ error }));
}

/** Keeps the job list in step with the core's `job` events. */
export function useJobEvents() {
  useEffect(() => {
    const unlisten = listen<JobEvent>("job", ({ payload }) => {
      upsert(payload.id, payload);
      const job = useJobs.getState().jobs.find((j) => j.id === payload.id);
      if (job?.renamed && payload.step === "done" && payload.path) {
        upsert(job.id, { renamed: false });
        void renameFile(job.id, payload.path, job.title);
      }
    });
    return () => void unlisten.then((stop) => stop());
  }, []);
}
