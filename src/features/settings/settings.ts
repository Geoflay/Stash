import { invoke } from "@tauri-apps/api/core";
import { create } from "zustand";
import type { AudioCodec, Format, VideoCodec } from "@/features/download/store";

export type Theme = "system" | "light" | "dark";

/** The picture codecs a re-encode can write. */
export const VIDEO_CODECS = ["h264", "av1"] as const satisfies readonly VideoCodec[];
export type EncodedCodec = (typeof VIDEO_CODECS)[number];

/** How hard x264 works, fastest first. */
export const H264_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow", "placebo"] as const;
export type H264Preset = (typeof H264_PRESETS)[number];

/** How hard SVT-AV1 works: 0 is the slowest and best, 13 the fastest. */
export const AV1_PRESETS = Array.from({ length: 14 }, (_, i) => i);

/** How hard the graphics card works: 1 is the fastest, 7 the slowest and best. */
export const GPU_PRESETS = Array.from({ length: 7 }, (_, i) => i + 1);

/** The priority encodes run at, lowest first (Windows' priority classes, but Realtime). */
export const PRIORITIES = ["idle", "belowNormal", "normal", "aboveNormal", "high"] as const;
export type Priority = (typeof PRIORITIES)[number];

/** Options saved under a name, offered in the Download and Edit tabs (all but the extract). */
export interface Preset {
  name: string;
  /** `original`: each file keeps its own kind. */
  format: Format | "original";
  /** Short side (720 for 720p); `null` keeps the best or the original size. */
  height: number | null;
  mute: boolean;
  speed: number;
  fps: number | null;
  maxMb: number | null;
}

/** Every SponsorBlock category that can be cut. */
export const SPONSOR_CATEGORIES = ["sponsor", "selfpromo", "interaction", "intro", "outro", "preview", "filler", "music_offtopic"] as const;
export type SponsorCategory = (typeof SPONSOR_CATEGORIES)[number];

export interface Settings {
  /** Windows only; filled with the Downloads folder when unset. */
  downloadDir: string | null;
  theme: Theme;
  /** Update yt-dlp when the app starts. */
  autoUpdate: boolean;
  /** The sound codec a re-encode writes. */
  audioCodec: AudioCodec;
  /** The picture codec a re-encode writes. */
  videoCodec: EncodedCodec;
  /** How hard each encoder works. */
  h264Preset: H264Preset;
  av1Preset: number;
  /** The priority encodes run at. */
  encodingPriority: Priority;
  /** Encode with the graphics card when it can (Windows only). */
  gpu: boolean;
  gpuPreset: number;
  /** Jobs of the queue that run at once. */
  maxJobs: number;
  /** Cut the parts SponsorBlock lists out of YouTube downloads. */
  sponsorBlock: boolean;
  /** The SponsorBlock categories cut. */
  sponsorCategories: SponsorCategory[];
  presets: Preset[];
}

/** Error returned by the Rust core: a locale key and its parameters. */
export interface Failure {
  key: string;
  params: Record<string, unknown>;
}

export const getSettings = () => invoke<Settings>("get_settings");
export const saveSettings = (settings: Settings) => invoke<void>("set_settings", { settings });
export const ytdlpVersion = () => invoke<string>("ytdlp_version");
/** The installed version, and whether it changed. */
export const updateYtdlp = () => invoke<[string, boolean]>("update_ytdlp");
export const cookiesImported = () => invoke<boolean>("cookies_imported");
export const importCookies = (path: string) => invoke<void>("import_cookies", { path });
export const removeCookies = () => invoke<void>("remove_cookies");

/** The settings once loaded, and the last error reading or saving them. */
export const useSettings = create<{ settings: Settings | null; error: Failure | null }>(() => ({ settings: null, error: null }));

export async function loadSettings() {
  try {
    useSettings.setState({ settings: await getSettings() });
  } catch (error) {
    useSettings.setState({ error: error as Failure });
  }
}

export function changeSettings(patch: Partial<Settings>) {
  const { settings } = useSettings.getState();
  if (!settings) return;
  const next = { ...settings, ...patch };
  useSettings.setState({ settings: next });
  saveSettings(next).catch((error: Failure) => useSettings.setState({ error }));
}

const presetsOf = () => useSettings.getState().settings?.presets ?? [];

/** Saves `preset`, in place of the one of the same name if any. */
export function savePreset(preset: Preset) {
  const presets = presetsOf();
  const known = presets.some((p) => p.name === preset.name);
  changeSettings({ presets: known ? presets.map((p) => (p.name === preset.name ? preset : p)) : [...presets, preset] });
}

/** Renames a preset; a name already taken gets a number. */
export function renamePreset(name: string, to: string) {
  const presets = presetsOf();
  const taken = (candidate: string) => presets.some((p) => p.name === candidate && p.name !== name);
  let unique = to;
  for (let n = 2; taken(unique); n++) unique = `${to} (${n})`;
  changeSettings({ presets: presets.map((p) => (p.name === name ? { ...p, name: unique } : p)) });
}

export function deletePreset(name: string) {
  changeSettings({ presets: presetsOf().filter((p) => p.name !== name) });
}

/** The preset named `name`, if it still exists. */
export function findPreset(name: string | null): Preset | null {
  return presetsOf().find((p) => p.name === name) ?? null;
}
