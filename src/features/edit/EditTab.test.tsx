import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { emit } from "@tauri-apps/api/event";
import { clearMocks, mockIPC } from "@tauri-apps/api/mocks";
import App from "@/App";
import "@/i18n";
import { defaultOutput, noOptions, useDownloads, useJobs } from "@/features/download/store";
import { useSettings, type Preset, type Settings } from "@/features/settings/settings";

const settings: Settings = {
  downloadDir: "C:/Users/me/Downloads",
  theme: "system",
  autoUpdate: true,
  audioCodec: "aac",
  videoCodec: "h264",
  h264Preset: "faster",
  av1Preset: 8,
  encodingPriority: "belowNormal",
  gpu: false,
  gpuPreset: 4,
  maxJobs: 3,
  sponsorBlock: true,
  sponsorCategories: ["sponsor", "intro"],
  presets: [],
};
import { setEditOptions, useEdit, type LocalMedia } from "./store";

const clip: LocalMedia = {
  path: "C:/Users/me/Videos/clip.mp4",
  name: "clip.mp4",
  size: 5_242_880,
  duration: 75,
  width: 1920,
  height: 1080,
  fps: 30,
  audio: true,
  videoCodec: "h264",
  audioCodec: "aac",
};
const song: LocalMedia = { ...clip, path: "C:/Users/me/Music/song.m4a", name: "song.m4a", width: null, height: null, fps: null, videoCodec: null };
/** A smaller video, with fewer frames per second. */
const other: LocalMedia = { ...clip, path: "C:/Users/me/Videos/other.mp4", name: "other.mp4", size: 2_000_000, duration: 40, width: 1280, height: 720, fps: 20 };

let file: LocalMedia = clip;
/** What the file picker returns: `file` unless set. */
let picked: string | string[] | null = null;
let opened: unknown[] = [];
let previewed: unknown[] = [];
/** Finishes the preview copy being made. */
let previewDone: () => void = () => {};
/** The copy the core makes; `null` for a video played in parts. */
let previewCopy: string | null = null;
let started: unknown;
/** Every job started, in order. */
let starts: unknown[] = [];
/** The presets last saved in the settings. */
let savedPresets: Preset[] | null = null;

beforeEach(() => {
  file = clip;
  picked = null;
  starts = [];
  savedPresets = null;
  opened = [];
  previewed = [];
  previewCopy = "C:/cache/preview-1.mp4";
  started = undefined;
  useDownloads.setState({ media: null, error: null });
  useSettings.setState({ settings: null, error: null });
  useJobs.setState({ jobs: [] });
  useEdit.setState({ tab: "download", files: [], selected: 0, preset: null, error: null, format: "mp4", height: null, options: noOptions });
  window.matchMedia = (query: string) =>
    ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }) as unknown as MediaQueryList;
  mockIPC(
    (cmd, args) => {
      if (cmd === "get_settings") return settings;
      if (cmd === "plugin:dialog|open") return picked ?? file.path;
      if (cmd === "set_settings") savedPresets = (args as { settings: Settings }).settings.presets;
      if (cmd === "parse_time") {
        const match = /^(\d+):(\d\d)$/.exec((args as { text: string }).text);
        return match ? Number(match[1]) * 60 + Number(match[2]) : null;
      }
      if (cmd === "open_media") {
        const { path } = args as { path: string };
        opened.push(path);
        return (picked !== null && [clip, other, song].find((m) => m.path === path)) || file;
      }
      if (cmd === "preview_media") {
        const { path, onProgress } = args as { path: string; onProgress: { id: number } };
        previewed.push(path);
        // The core sends its progress on the channel, then the copy's path.
        const internals = (window as unknown as { __TAURI_INTERNALS__: { runCallback(id: number, data: unknown): void } }).__TAURI_INTERNALS__;
        internals.runCallback(onProgress.id, { index: 0, message: 0.42 });
        return new Promise((resolve) => (previewDone = () => resolve(previewCopy)));
      }
      if (cmd === "start_edit") {
        started = args;
        starts.push(args);
        return 3;
      }
    },
    { shouldMockEvents: true },
  );
});

afterEach(async () => {
  cleanup();
  await new Promise((resolve) => setTimeout(resolve));
  clearMocks();
});

async function openFromPicker() {
  render(<App />);
  fireEvent.mouseDown(screen.getByRole("tab", { name: "Edit" }));
  // Another file takes the place of the one open.
  fireEvent.click(await screen.findByRole("button", { name: /^(Open a file|Replace file)$/ }));
  return screen.findByDisplayValue(file.name.replace(/\.[^.]*$/, " (edited)"));
}

test.each([390, 1280])("opens a video and saves an edited copy at %ipx", async (width) => {
  window.innerWidth = width;
  await openFromPicker();
  expect(opened).toEqual([clip.path]);
  expect(screen.getByText("1:15 · 1920 × 1080 · 5 MB")).toBeDefined();
  // The file's name takes the place of the prompt; files can be added, or another opened.
  expect(screen.queryByText(/to save an edited copy/)).toBeNull();
  expect(screen.getByRole("button", { name: "Add files" })).toBeDefined();
  expect(screen.getByRole("button", { name: "Replace file" })).toBeDefined();
  expect(document.querySelector("video")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Play preview" }));
  expect(document.querySelector("video")?.getAttribute("src")).toContain("clip.mp4");
  expect(screen.getByRole("combobox", { name: "Quality" }).textContent).toContain("Original · 1080p");
  expect(screen.getByText("30 fps")).toBeDefined();
  // A copy as it is: the file's size.
  expect(screen.getByText(/About 5 MB/)).toBeDefined();

  await act(() => useEdit.setState({ height: 720 }));
  expect(screen.getByRole("combobox", { name: "Quality" }).textContent).toContain("720p");
  fireEvent.click(screen.getByRole("switch", { name: "Remove sound" }));
  // 720p has 4/9 of the pixels, and no sound (128 kb/s of the 5 MB).
  expect(screen.getByText(/About 1\.7 MB/)).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Save a copy" }));
  await waitFor(() =>
    expect(started).toEqual({
      request: { path: clip.path, title: "clip (edited)", options: { ...noOptions, ...defaultOutput, mute: true, height: 720, keepCodecs: true } },
    }),
  );
  fireEvent.mouseDown(await screen.findByRole("tab", { name: "Queue, 1 in progress" }));
  expect(await screen.findByDisplayValue("clip (edited)")).toBeDefined();
  // The options stay as they were back in the Edit tab.
  fireEvent.mouseDown(screen.getByRole("tab", { name: "Edit" }));
  expect(screen.getByRole("combobox", { name: "Quality" }).textContent).toContain("720p");

  // M4A keeps only the sound: no quality, no mute, no frame rate.
  await act(() => useEdit.setState({ format: "m4a" }));
  expect(screen.queryByRole("combobox", { name: "Quality" })).toBeNull();
  expect(screen.queryByRole("switch", { name: "Remove sound" })).toBeNull();
  expect(screen.queryByRole("slider", { name: "Frame rate" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save a copy" }));
  // Sent as they are: the core ignores the picture's options for a sound.
  await waitFor(() =>
    expect(started).toEqual({ request: { path: clip.path, title: "clip (edited)", options: { ...noOptions, ...defaultOutput, format: "m4a", mute: true, height: 720, keepCodecs: true } } }),
  );

  // Another name, typed in the title: shown in the bottom bar and saved under.
  const name = screen.getByRole("textbox", { name: "File name" });
  fireEvent.change(name, { target: { value: "Zoo sound" } });
  fireEvent.blur(name);
  expect(screen.getByText("Zoo sound")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Save a copy" }));
  await waitFor(() => expect(started).toMatchObject({ request: { title: "Zoo sound" } }));
});

test("the shown length of a clip means no extract", async () => {
  file = { ...clip, duration: 75.03 };
  await openFromPicker();
  const end = screen.getByRole("textbox", { name: "End" });
  expect((end as HTMLInputElement).value).toBe("1:15");
  fireEvent.change(end, { target: { value: "1:15" } });
  fireEvent.blur(end);
  await waitFor(() => expect((end as HTMLInputElement).value).toBe("1:15"));
  fireEvent.click(screen.getByRole("button", { name: "Save a copy" }));
  await waitFor(() => expect(started).toMatchObject({ request: { options: { start: null, end: null } } }));
});

test("opens a sound file with sound formats only", async () => {
  file = song;
  await openFromPicker();
  fireEvent.click(screen.getByRole("button", { name: "Play preview" }));
  // A sound: its controls, no picture.
  expect(document.querySelector("audio")).not.toBeNull();
  expect(screen.queryByRole("img", { name: "Preview" })).toBeNull();
  expect(screen.getByRole("button", { name: "Play" })).toBeDefined();
  expect(screen.getByRole("combobox", { name: "Format" }).textContent).toContain("M4A");
  // A sound has no video to choose.
  expect(screen.queryByRole("button", { name: "Video" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Quality" })).toBeNull();
  expect(screen.queryByRole("switch", { name: "Remove sound" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save a copy" }));
  await waitFor(() =>
    expect(started).toEqual({ request: { path: song.path, title: "song (edited)", options: { ...noOptions, ...defaultOutput, format: "m4a", height: null, keepCodecs: true } } }),
  );
});

test("keeps the file's own kind", async () => {
  file = { ...clip, name: "clip.mkv", videoCodec: "h265", audioCodec: "flac" };
  await openFromPicker();
  expect(useEdit.getState().format).toBe("mkv");
  // Its sound alone keeps its codec.
  fireEvent.click(screen.getByRole("button", { name: "Sound only" }));
  expect(screen.getByRole("combobox", { name: "Format" }).textContent).toContain("FLAC");
  cleanup();
  file = { ...clip, name: "clip.MOV" };
  await openFromPicker();
  expect(useEdit.getState().format).toBe("mov");
  cleanup();
  file = { ...song, name: "song.mp3", audioCodec: "mp3" };
  await openFromPicker();
  expect(useEdit.getState().format).toBe("mp3");
});

test("plays a copy of a file the preview can't play", async () => {
  file = { ...clip, path: "C:/Users/me/Videos/clip.mkv", name: "clip.mkv", videoCodec: "h265" };
  await openFromPicker();
  fireEvent.click(screen.getByRole("button", { name: "Play preview" }));
  expect(document.querySelector("video")?.getAttribute("src")).toContain("clip.mkv");
  fireEvent.error(document.querySelector("video")!);
  expect(await screen.findByText("Preparing the preview… 42%")).toBeDefined();
  act(() => previewDone());
  await waitFor(() => expect(document.querySelector("video")?.getAttribute("src")).toContain("preview-1.mp4"));
  expect(previewed).toEqual([file.path]);
  // The copy fails too: no preview.
  fireEvent.error(document.querySelector("video")!);
  expect(screen.getByText("This file can't be previewed.")).toBeDefined();
  expect(previewed).toHaveLength(1);
});

test("plays a video the preview can't play in parts made as it plays", async () => {
  file = { ...clip, path: "C:/Users/me/Videos/clip.mkv", name: "clip.mkv", videoCodec: "h265" };
  previewCopy = null;
  // The webview's Media Source, which the parts are given to.
  class FakeSource extends EventTarget {
    static isTypeSupported = () => true;
    readyState = "closed";
  }
  vi.stubGlobal("MediaSource", FakeSource);
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:parts");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  try {
    await openFromPicker();
    fireEvent.click(screen.getByRole("button", { name: "Play preview" }));
    fireEvent.error(document.querySelector("video")!);
    act(() => previewDone());
    await waitFor(() => expect(document.querySelector("video")?.getAttribute("src")).toBe("blob:parts"));
    // The parts fail too: no preview, and no copy asked for again.
    fireEvent.error(document.querySelector("video")!);
    expect(screen.getByText("This file can't be previewed.")).toBeDefined();
    expect(previewed).toEqual([file.path]);
  } finally {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  }
});

test("the preview seeks to the last edge moved once the running seek is done", async () => {
  await openFromPicker();
  fireEvent.click(screen.getByRole("button", { name: "Play preview" }));
  const video = document.querySelector("video")!;
  const seeks: number[] = [];
  let seeking = true;
  Object.defineProperties(video, {
    readyState: { value: 1 },
    seeking: { get: () => seeking },
    currentTime: { get: () => seeks[seeks.length - 1] ?? 0, set: (time: number) => seeks.push(time) },
    pause: { value: () => {} },
  });
  // Moved while a seek runs: a new seek would cancel it, and show nothing until the drag ends.
  await act(() => setEditOptions({ start: 10 }));
  await act(() => setEditOptions({ start: 12 }));
  expect(seeks).toEqual([]);
  seeking = false;
  fireEvent.seeked(video);
  expect(seeks).toEqual([12]);
});

test("the preview can be dragged off the options", async () => {
  await openFromPicker();
  fireEvent.click(screen.getByRole("button", { name: "Play preview" }));
  const preview = screen.getByRole("img", { name: "Preview" });
  const player = preview.closest<HTMLElement>(".fixed")!;
  player.setPointerCapture = () => {};
  fireEvent.pointerDown(preview, { button: 0, pointerId: 1, clientX: 10, clientY: 10 });
  fireEvent.pointerMove(player, { pointerId: 1, clientX: 110, clientY: 60 });
  fireEvent.pointerUp(player, { pointerId: 1 });
  expect([player.style.left, player.style.top]).toEqual(["100px", "50px"]);
  // Its controls are not handles.
  fireEvent.pointerDown(screen.getByRole("button", { name: "Play" }), { button: 0, pointerId: 2, clientX: 0, clientY: 0 });
  fireEvent.pointerMove(player, { pointerId: 2, clientX: 300, clientY: 300 });
  expect([player.style.left, player.style.top]).toEqual(["100px", "50px"]);
});

test("shows why a file cannot be opened", async () => {
  mockIPC((cmd) => {
    if (cmd === "get_settings") return { ...settings, downloadDir: null };
    if (cmd === "plugin:dialog|open") return "C:/a.mkv";
    if (cmd === "open_media") throw { key: "errors.unsupportedFile", params: {} };
  }, { shouldMockEvents: true });
  render(<App />);
  fireEvent.mouseDown(screen.getByRole("tab", { name: "Edit" }));
  fireEvent.click(await screen.findByRole("button", { name: "Open a file" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "This file cannot be edited: its format is not supported.",
  );
});

test.each([390, 1280])("edits a finished download at %ipx", async (width) => {
  window.innerWidth = width;
  useJobs.setState({
    jobs: [
      { id: 1, title: "Photos", editable: false, step: "done", fraction: null, eta: null, path: "C:/Downloads/Photos", error: null },
      { id: 0, title: "clip", editable: true, step: "download", fraction: null, eta: null, path: null, error: null },
    ],
  });
  render(<App />);
  fireEvent.mouseDown(screen.getByRole("tab", { name: /Queue/ }));
  // A folder of photos cannot be edited.
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  await act(() => emit("job", { id: 0, step: "done", fraction: null, eta: null, path: clip.path, error: null }));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(await screen.findByDisplayValue("clip (edited)")).toBeDefined();
  expect(opened).toEqual([clip.path]);
  expect(screen.getByRole("tab", { name: "Edit" }).getAttribute("aria-selected")).toBe("true");
});

test("opens a file dropped on the window", async () => {
  render(<App />);
  // The drop listeners register one after another.
  await act(() => new Promise((resolve) => setTimeout(resolve, 10)));
  await act(() => emit("tauri://drag-drop", { paths: [clip.path], position: { x: 0, y: 0 } }));
  expect(await screen.findByDisplayValue("clip (edited)")).toBeDefined();
  expect(screen.getByRole("tab", { name: "Edit" }).getAttribute("aria-selected")).toBe("true");
});

/** The line of the file saved as `title`, among several. */
const rowOf = (title: string) => screen.getByDisplayValue(title).closest("li")!;

test("edits several files: shared options, settled for each, and their own", async () => {
  picked = [clip.path, other.path];
  await openFromPicker();
  expect(await screen.findByDisplayValue("other (edited)")).toBeDefined();
  expect(screen.getByText("2 files")).toBeDefined();
  // Each file keeps its own kind until another is chosen for all.
  expect(screen.getByRole("combobox", { name: "Format" }).textContent).toContain("Original format");

  // 720p and 24 fps for all: the smaller file keeps its size and its 20 fps.
  await act(() => useEdit.setState({ height: 720 }));
  await act(() => setEditOptions({ fps: 24 }));
  expect(screen.getByText("A video with fewer frames per second keeps its own frame rate.")).toBeDefined();
  expect(within(rowOf("clip (edited)")).getByText(/^1:15 · MP4 · 720p · 24 fps · About/)).toBeDefined();
  expect(within(rowOf("other (edited)")).getByText("0:40 · MP4 · 720p · 20 fps · About 1.9 MB")).toBeDefined();
  expect(screen.getByText(/^Original format · About .* in all$/)).toBeDefined();

  // A file's own options, folded until asked for.
  const row = rowOf("clip (edited)");
  expect(within(row).queryByRole("switch", { name: "Remove sound" })).toBeNull();
  fireEvent.click(within(row).getByRole("button", { name: "Options of clip (edited)" }));
  fireEvent.click(within(row).getByRole("switch", { name: "Remove sound" }));
  expect(within(row).getByText("Own options ·")).toBeDefined();

  fireEvent.click(screen.getByRole("button", { name: "Save 2 copies" }));
  await waitFor(() => expect(starts).toHaveLength(2));
  expect(starts).toEqual([
    { request: { path: clip.path, title: "clip (edited)", options: { ...noOptions, ...defaultOutput, mute: true, fps: 24, height: 720, keepCodecs: true } } },
    { request: { path: other.path, title: "other (edited)", options: { ...noOptions, ...defaultOutput, height: null, keepCodecs: true } } },
  ]);

  // Back to the shared options, then back to one file: its options become the tab's.
  fireEvent.click(within(row).getByRole("button", { name: "Use the options for all files" }));
  expect(within(row).queryByText("Own options ·")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Remove other (edited)" }));
  expect(screen.getByRole("button", { name: "Replace file" })).toBeDefined();
  expect(useEdit.getState()).toMatchObject({ format: "mp4", height: 720, options: { fps: 24, mute: false } });
});

test("files added after one keep its extract, and a file open stays once", async () => {
  await openFromPicker();
  await act(() => setEditOptions({ start: 10 }));
  picked = [other.path, clip.path];
  fireEvent.click(screen.getByRole("button", { name: "Add files" }));
  expect(await screen.findByDisplayValue("other (edited)")).toBeDefined();
  expect(screen.getAllByRole("button", { name: /^Remove / })).toHaveLength(2);
  // The file's extract is its own; the others have none.
  expect(useEdit.getState().files[0].own).toEqual({ start: 10 });
  fireEvent.click(screen.getByRole("button", { name: "Save 2 copies" }));
  await waitFor(() => expect(starts).toHaveLength(2));
  expect(starts).toMatchObject([{ request: { options: { start: 10 } } }, { request: { options: { start: null } } }]);
});

test("saves the options as a preset, shown until one of them changes", async () => {
  // What Radix's select calls, which jsdom lacks.
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.scrollIntoView = () => {};
  await openFromPicker();
  await act(() => useEdit.setState({ height: 720 }));
  fireEvent.click(screen.getByRole("switch", { name: "Remove sound" }));
  fireEvent.click(screen.getByRole("button", { name: "Save as preset" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Preset name" }), { target: { value: "Small" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  const small = { name: "Small", format: "mp4", height: 720, mute: true, speed: 1, fps: null, maxMb: null };
  await waitFor(() => expect(savedPresets).toEqual([small]));
  const presets = screen.getByRole("combobox", { name: "Preset" });
  expect(presets.textContent).toBe("Small");

  await act(() => setEditOptions({ speed: 2 }));
  expect(presets.textContent).toBe("Custom");
  // The extract is not part of a preset.
  await act(() => setEditOptions({ start: 5 }));
  fireEvent.pointerDown(presets, { button: 0, ctrlKey: false, pointerType: "mouse" });
  fireEvent.click(await screen.findByRole("option", { name: "Small" }));
  expect(useEdit.getState()).toMatchObject({ preset: "Small", height: 720, options: { speed: 1, mute: true, start: 5 } });
});
