import { invoke } from "@tauri-apps/api/core";
import { create } from "zustand";
import type { AudioCodec, Format } from "@/features/download/store";

export type Theme = "system" | "light" | "dark";

/** How hard a re-encode works, fastest first. */
export const ENCODER_PRESETS = ["veryfast", "faster", "medium", "slow"] as const;
export type EncoderPreset = (typeof ENCODER_PRESETS)[number];

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
  /** The sound codec a re-encode writes (the picture is always H.264). */
  audioCodec: AudioCodec;
  /** How hard a re-encode works. */
  preset: EncoderPreset;
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
