import { Channel, invoke } from "@tauri-apps/api/core";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { open } from "@tauri-apps/plugin-dialog";
import { useEffect } from "react";
import { create } from "zustand";
import {
  isAudioFormat,
  isLossless,
  noOptions,
  outputOf,
  requestOptions,
  setsPresetOption,
  SOUND_FILE,
  soundFile,
  startJob,
  type AudioCodec,
  type AudioFormat,
  type Format,
  type Options,
  type VideoCodec,
} from "@/features/download/store";
import { findPreset, useSettings, type Failure, type Preset, type Settings } from "@/features/settings/settings";
import { isAndroid } from "@/lib/platform";

/** A file opened for editing, as `open_media` describes it. */
export interface LocalMedia {
  /** The file the core reads and the preview plays. */
  path: string;
  name: string;
  size: number;
  duration: number;
  width: number | null;
  height: number | null;
  fps: number | null;
  audio: boolean;
  /** The file's codecs, when the app writes them too. */
  videoCodec: VideoCodec | null;
  audioCodec: AudioCodec | null;
}

export type Tab = "download" | "edit" | "jobs";

/** The kind of file saved; `original` (with several files) keeps each file's own. */
export type EditFormat = Format | "original";

/** Every option of a copy, as one object. */
export interface Choices extends Options {
  format: EditFormat;
  /** Lower short side (720 for 720p); `null` keeps the original size. */
  height: number | null;
}

export interface EditFile {
  media: LocalMedia;
  /** The name the copy is saved as: `name (edited)` until changed. */
  title: string;
  /** The options changed on this file alone; the others follow the common ones. */
  own: Partial<Choices>;
}

/** What a file is saved as, every option settled for it. */
export interface Copy {
  media: LocalMedia;
  title: string;
  format: Format;
  height: number | null;
  options: Options;
}

export interface EditState {
  tab: Tab;
  files: EditFile[];
  /** The file the preview shows. */
  selected: number;
  opening: boolean;
  error: Failure | null;
  /** With one file, its kind of file; with several, the one they share. The settings
   * choose the codecs of a re-encode. */
  format: EditFormat;
  /** With one file, its size; with several, the one they share. */
  height: number | null;
  /** With one file, its edits; with several, the ones they share (no extract). */
  options: Options;
  /** The preset last chosen, until an option it sets changes. */
  preset: string | null;
  /** Files are being dragged over the window. */
  dragging: boolean;
  /** What the preview plays: the file, a playable copy of it, or nothing it can play. */
  previewPath: string | null;
  /** A playable copy is being made. */
  preparing: boolean;
  /** How much of it is made, 0 to 1. */
  prepared: number;
}

export const useEdit = create<EditState>(() => ({
  tab: "download",
  files: [],
  selected: 0,
  opening: false,
  error: null,
  format: "mp4",
  height: null,
  options: noOptions,
  preset: null,
  dragging: false,
  previewPath: null,
  preparing: false,
  prepared: 0,
}));

const set = useEdit.setState;
const settingsNow = () => useSettings.getState().settings;

/** The sizes offered, highest first. */
export const HEIGHTS = [1080, 720, 480, 360];

/** The side a "720p" names; `null` for a sound. */
export function shortSide(media: LocalMedia): number | null {
  return media.width === null || media.height === null ? null : Math.min(media.width, media.height);
}

/** The lower sizes a file can be saved at, under its short side. */
export function lowerHeights(media: LocalMedia): number[] {
  const short = shortSide(media);
  return short === null ? [] : HEIGHTS.filter((h) => h < short);
}

/** The highest of `heights` up to `height` (the lowest if all are higher); `null` keeps the size. */
function heightUpTo(heights: number[], height: number | null): number | null {
  if (height === null) return null;
  return heights.find((h) => h <= height) ?? heights[heights.length - 1] ?? null;
}

/** The sound-only file of `media`: in its own codec when the app writes it. */
export function mediaSoundFile(media: LocalMedia, settings: Pick<Settings, "audioCodec"> | null): AudioFormat {
  return media.audioCodec ? SOUND_FILE[media.audioCodec] : soundFile(settings);
}

/** What an edit saves by default: the file's own kind, so that edits needing no
 * re-encode stay a copy. */
export function sourceFormat(media: LocalMedia, settings: Pick<Settings, "audioCodec"> | null): Format {
  if (media.width === null) return mediaSoundFile(media, settings);
  const ext = /\.(mkv|mov)$/i.exec(media.name)?.[1].toLowerCase() as "mkv" | "mov" | undefined;
  return ext ?? "mp4";
}

const common = ({ format, height, options }: Pick<EditState, "format" | "height" | "options">): Choices => ({ ...options, format, height });

/** Settles `choices` for `media`: its own kind for `original`, or for a kind it can't be
 * saved as; no size or frame rate above its own. */
function settle(media: LocalMedia, choices: Choices, settings: Settings | null) {
  const { format: chosen, height, start, end, mute, speed, fps, maxMb } = choices;
  const fits = chosen !== "original" && (isAudioFormat(chosen) ? media.audio : media.width !== null);
  const short = shortSide(media);
  return {
    format: fits ? chosen : sourceFormat(media, settings),
    height: height !== null && short !== null && height < short ? height : null,
    options: { start, end, mute, speed, maxMb, fps: fps !== null && media.fps !== null && fps < media.fps - 0.01 ? fps : null },
  };
}

/** What `file` is saved as: the common options, with its own on top. */
export function copyOf(state: Pick<EditState, "format" | "height" | "options">, file: EditFile, settings: Settings | null): Copy {
  return { media: file.media, title: file.title, ...settle(file.media, { ...common(state), ...file.own }, settings) };
}

/** `copy` as one object, to compare an option with. */
const flat = ({ format, height, options }: Pick<Copy, "format" | "height" | "options">): Choices => ({ ...options, format, height });

/** Files as they are open: one keeps its options in the common ones, so that the tab stays as
 * it is with a single file; several share theirs, each with its own extract. */
function withFiles(state: EditState, files: EditFile[]): Partial<EditState> {
  const settings = settingsNow();
  const [first] = state.files;
  if (files.length > 1 && state.files.length === 1) {
    // The file's extract becomes its own, and its own kind stays each file's.
    const { start, end } = state.options;
    const own = { ...(start !== null && { start }), ...(end !== null && { end }) };
    return {
      files: files.map((f) => (f === first ? { ...f, own } : f)),
      format: state.format === sourceFormat(first.media, settings) ? "original" : state.format,
      options: { ...state.options, start: null, end: null },
    };
  }
  if (files.length === 1 && state.files.length > 1) {
    const { format, height, options } = copyOf(state, files[0], settings);
    return { files: [{ ...files[0], own: {} }], format, height, options };
  }
  return { files };
}

/** Shows `index` in the preview. */
export function selectFile(index: number) {
  const { files, selected } = useEdit.getState();
  if (index !== selected) set({ selected: index, previewPath: files[index]?.media.path ?? null, preparing: false });
}

/** Changes the common options (with one file, its options). */
export function setEditOptions(patch: Partial<Options>) {
  set(({ options, preset }) => ({ options: { ...options, ...patch }, preset: setsPresetOption(patch) ? null : preset }));
}

/** Changes the common kind of file or size. */
export function setEditChoice(patch: Partial<Pick<Choices, "format" | "height">>) {
  set({ ...patch, preset: null });
}

/** Changes options of `index` alone: those set back to the common ones follow them again. */
export function setFileOptions(index: number, patch: Partial<Choices>) {
  set((state) => {
    const file = state.files[index];
    const base = flat(settle(file.media, common(state), settingsNow()));
    const own: Partial<Choices> = { ...file.own };
    for (const [key, value] of Object.entries(patch) as [keyof Choices, unknown][]) {
      if (value === base[key]) delete own[key];
      else Object.assign(own, { [key]: value });
    }
    return { files: state.files.map((f, i) => (i === index ? { ...f, own } : f)) };
  });
}

/** Sets `index` back to the common options. */
export function resetFile(index: number) {
  set(({ files }) => ({ files: files.map((f, i) => (i === index ? { ...f, own: {} } : f)) }));
}

export function renameEditFile(index: number, title: string) {
  set(({ files }) => ({ files: files.map((f, i) => (i === index ? { ...f, title } : f)) }));
}

export function removeFile(index: number) {
  set((state) => {
    const files = state.files.filter((_, i) => i !== index);
    const selected = Math.max(0, Math.min(state.selected > index ? state.selected - 1 : state.selected, files.length - 1));
    const shown = files[selected]?.media.path ?? null;
    const moved = shown !== state.files[state.selected]?.media.path;
    return { ...withFiles(state, files), selected, ...(moved && { previewPath: shown, preparing: false }) };
  });
}

/** The options a preset made now would hold. */
export function presetOptions(state: EditState): Omit<Preset, "name"> {
  const { mute, speed, fps, maxMb } = state.options;
  return { format: state.format, height: state.height, mute, speed, fps, maxMb };
}

/** Applies `preset`: with one file, as far as the file allows; with several, to the
 * common options, each file settling them for itself. */
export function applyPreset(preset: Preset) {
  set((state) => {
    const { mute, speed, fps, maxMb } = preset;
    const options = { ...state.options, mute, speed, fps, maxMb };
    const [only, more] = state.files;
    if (!only || more) return { preset: preset.name, format: preset.format, height: heightUpTo(HEIGHTS, preset.height), options };
    const short = shortSide(only.media);
    const height = short !== null && preset.height !== null && preset.height >= short ? null : heightUpTo(lowerHeights(only.media), preset.height);
    const settled = settle(only.media, { ...options, format: preset.format, height }, settingsNow());
    return { preset: preset.name, ...settled };
  });
}

const titleOf = (media: LocalMedia) => `${media.name.replace(/\.[^.]*$/, "")} (edited)`;

/** Opens files (paths, or content URIs on Android) in the Edit tab, in place of those open
 * or after them with `add`. A file already open is left as it is. */
export async function openFiles(paths: string[], add = false) {
  set({ tab: "edit", opening: true, error: null });
  try {
    const opened: EditFile[] = [];
    for (const path of paths) {
      try {
        const media = await invoke<LocalMedia>("open_media", { path });
        opened.push({ media, title: titleOf(media), own: {} });
      } catch (error) {
        set({ error: error as Failure });
      }
    }
    if (!opened.length) return;
    const state = useEdit.getState();
    const kept = add ? state.files : [];
    const files = [...kept, ...opened.filter((f) => !kept.some((k) => k.media.path === f.media.path))];
    if (kept.length) {
      set(withFiles(state, files));
      return;
    }
    // New files: their own kind, no edits, or the preset chosen.
    const [first] = files;
    set({
      files,
      selected: 0,
      format: files.length > 1 ? "original" : sourceFormat(first.media, settingsNow()),
      height: null,
      options: noOptions,
      previewPath: first.media.path,
      preparing: false,
    });
    const preset = findPreset(state.preset);
    if (preset) applyPreset(preset);
  } finally {
    set({ opening: false });
  }
}

/** Opens a file in place of those open. */
export const openFile = (path: string) => openFiles([path]);

/** The preview can't play the file: plays a copy the core makes, or nothing if that fails too. */
export async function fixPreview() {
  const { files, selected, previewPath, preparing } = useEdit.getState();
  const media = files[selected]?.media;
  if (!media || preparing) return;
  if (previewPath !== media.path) {
    set({ previewPath: null });
    return;
  }
  set({ preparing: true, prepared: 0 });
  const shown = () => {
    const now = useEdit.getState();
    return now.files[now.selected]?.media === media;
  };
  const onProgress = new Channel<number>((prepared) => {
    if (shown()) set({ prepared });
  });
  const copy = await invoke<string>("preview_media", { path: media.path, onProgress }).catch(() => null);
  // Another file may be shown meanwhile.
  if (shown()) set({ previewPath: copy, preparing: false });
}

/** The files FFmpeg reads, by extension (Windows) or MIME type (Android). */
const EXTENSIONS = ["mp4", "m4v", "mov", "mkv", "webm", "avi", "wmv", "flv", "ts", "mts", "m2ts", "3gp", "mpg", "mpeg", "ogv"];
const SOUND_EXTENSIONS = ["m4a", "mp3", "wav", "flac", "ogg", "opus", "aac", "wma"];

/** Lets the user pick video or sound files: several, or one with `single`. */
export async function pickFiles({ add = false, single = false } = {}) {
  const extensions = isAndroid ? ["video/*", "audio/*"] : [...EXTENSIONS, ...SOUND_EXTENSIONS];
  const picked = await open({ multiple: !single, filters: [{ name: "Video or sound", extensions }] });
  const paths = [picked ?? []].flat();
  if (paths.length) await openFiles(single ? paths.slice(0, 1) : paths, add);
}

/** Bytes per second of sound, as the core's `AUDIO_KBPS` (128 kb/s). */
const SOUND_BYTES = 16_000;

/** Rough size of the copy: the file's size for the part kept, its picture scaled to the
 * height and frame rate chosen, capped by the size limit. Unknown when re-encoded to other
 * codecs than the file's, unless a limit caps it. */
export function estimate({ media, format, height, options }: Copy, settings: Settings | null): number | null {
  const output = outputOf(format, settings);
  const limit = options.maxMb && !isLossless(output) ? options.maxMb * 1e6 : null;
  if (!media.duration) return limit;
  const kept = ((options.end ?? media.duration) - (options.start ?? 0)) / options.speed / media.duration;
  const short = shortSide(media);
  const scale = height !== null && short ? (height / short) ** 2 : 1;
  const frames = options.fps !== null && media.fps ? Math.min(options.fps / media.fps, 1) : 1;
  const sound = media.audio ? Math.min(SOUND_BYTES * media.duration, media.size) : 0;
  let size: number | null;
  if (isAudioFormat(format)) {
    // Sound alone: copied from a sound file of that codec, re-encoded at 128 kb/s otherwise.
    const copied = media.width === null && media.audioCodec !== null && SOUND_FILE[media.audioCodec] === format;
    size = copied ? media.size * kept : isLossless(output) ? null : sound * kept;
  } else {
    // A copy keeps the file's codecs; a re-encode in the same ones keeps about its bitrate.
    const reshapes = Math.abs(kept - 1) > 0.001 || scale < 1 || frames < 1;
    const writable = media.videoCodec !== null && (!media.audio || media.audioCodec !== null);
    const same = media.videoCodec === "h264" && (!media.audio || options.mute || output.audioCodec === media.audioCodec);
    size = (reshapes ? same : writable || same) ? ((media.size - sound) * scale * frames + (options.mute ? 0 : sound)) * kept : null;
  }
  if (size === null) return limit;
  return limit ? Math.min(size, limit) : size;
}

/** Starts saving a copy of every file open, each under its title. */
export async function saveCopies() {
  const state = useEdit.getState();
  const settings = settingsNow();
  for (const file of state.files) {
    const { media, title, format, height, options } = copyOf(state, file, settings);
    // A copy that needs no re-encode keeps the file's own codecs.
    const request = { path: media.path, title, options: { ...requestOptions(options, outputOf(format, settings)), height, keepCodecs: true } };
    try {
      await startJob({ command: "start_edit", request }, title);
    } catch (error) {
      set({ error: error as Failure });
    }
  }
}

/** Windows: files dropped anywhere on the window open in the Edit tab, after those open. */
export function useFileDrop() {
  useEffect(() => {
    if (isAndroid) return;
    const unlisten = getCurrentWebview().onDragDropEvent(({ payload }) => {
      // "over" comes with every mouse move: only a change is set.
      const dragging = payload.type === "enter" || payload.type === "over";
      if (dragging !== useEdit.getState().dragging) set({ dragging });
      if (payload.type === "drop" && payload.paths.length) void openFiles(payload.paths, true);
    });
    return () => void unlisten.then((stop) => stop());
  }, []);
}
