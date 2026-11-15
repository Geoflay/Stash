import { afterEach, beforeEach, expect, test } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { emit } from "@tauri-apps/api/event";
import { clearMocks, mockIPC } from "@tauri-apps/api/mocks";
import App from "@/App";
import "@/i18n";
import { defaultOutput, noOptions, setOptions, useDownloads, useJobs, type Media } from "./store";
import { useEdit } from "@/features/edit/store";
import { useSettings, type Preset, type Settings } from "@/features/settings/settings";

const settings: Settings = {
  downloadDir: "C:/Users/me/Downloads",
  theme: "system",
  autoUpdate: true,
  audioCodec: "aac",
  preset: "faster",
  maxJobs: 3,
  sponsorBlock: true,
  sponsorCategories: ["sponsor", "intro"],
  presets: [],
};

const media: Media = {
  url: "https://www.youtube.com/watch?v=jNQXAC9IVRw",
  title: "Me at the zoo",
  author: "jawed",
  duration: 19,
  thumbnail: null,
  info: "C:/cache/info-0.json",
  qualities: [
    { height: 240, size: 742_369, fps: 30, parts: [{ formatId: "133", video: true, audio: false, size: 433_081 }, { formatId: "140", video: false, audio: true, size: 309_288 }] },
    { height: 144, size: 504_566, fps: 30, parts: [{ formatId: "160", video: true, audio: false, size: 195_278 }, { formatId: "140", video: false, audio: true, size: 309_288 }] },
  ],
  audio: { height: null, size: 309_288, fps: null, parts: [{ formatId: "140", video: false, audio: true, size: 309_288 }] },
  photos: [],
  preview: "https://cdn/18.mp4",
  segments: [],
};

const sound = { formatId: "audio", video: false, audio: true, size: null };
const photoPost: Media = {
  url: "https://www.tiktok.com/@a/video/1",
  title: "Hehe",
  author: "love_horsys",
  duration: 8,
  thumbnail: null,
  info: "C:/cache/info-1.json",
  qualities: [],
  audio: { height: null, size: null, fps: null, parts: [sound] },
  photos: [
    { url: "https://p16/1.jpeg", width: 1168, height: 1548 },
    { url: "https://p16/2.jpeg", width: 1920, height: 1080 },
  ],
  preview: null,
  segments: [],
};

let analyzed: Media = media;
/** The presets in the settings. */
let presets: Preset[] = [];

let started: unknown;
let nextId = 7;
/** `rename_file` calls: the path and the new title. */
let renamed: { path: string; title: string }[] = [];

/** The title, in the card's name field and in the bottom bar. */
async function titled(title: string) {
  expect(await screen.findByDisplayValue(title)).toBeDefined();
  expect(screen.getByText(title)).toBeDefined();
}

/** Types a new name in a name field and leaves it. */
function rename(field: HTMLElement, title: string) {
  fireEvent.change(field, { target: { value: title } });
  fireEvent.blur(field);
}

beforeEach(() => {
  started = undefined;
  nextId = 7;
  renamed = [];
  analyzed = media;
  presets = [];
  useDownloads.setState({ url: "", media: null, error: null, format: "mp4", quality: "best", options: noOptions, preset: null });
  useSettings.setState({ settings: null, error: null });
  useJobs.setState({ jobs: [] });
  useEdit.setState({ tab: "download" });
  window.matchMedia = (query: string) =>
    ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }) as unknown as MediaQueryList;
  mockIPC(
    (cmd, args) => {
      if (cmd === "get_settings") return { ...settings, presets };
      if (cmd === "analyze") return analyzed;
      if (cmd === "plugin:clipboard-manager|read_text") return ` ${media.url}\n`;
      if (cmd === "parse_time") {
        const match = /^(\d+):(\d\d)$/.exec((args as { text: string }).text);
        return match ? Number(match[1]) * 60 + Number(match[2]) : null;
      }
      if (cmd === "start_download") {
        started = args;
        return nextId++;
      }
      if (cmd === "rename_file") {
        const { path, title } = args as { path: string; title: string };
        renamed.push({ path, title });
        return path.replace(/[^/]*(\.\w+)$/, `${title}$1`);
      }
    },
    { shouldMockEvents: true },
  );
});

afterEach(async () => {
  cleanup();
  // Let the job listener's async unlisten finish before the mocks go away.
  await new Promise((resolve) => setTimeout(resolve));
  clearMocks();
});

test.each([390, 1280])("analyzes a link and downloads it at %ipx", async (width) => {
  window.innerWidth = width;
  render(<App />);
  fireEvent.change(screen.getByRole("textbox", { name: "Video link" }), { target: { value: media.url } });
  fireEvent.click(screen.getByRole("button", { name: "Analyze" }));

  await titled("Me at the zoo");
  expect(screen.getByText("jawed · 0:19")).toBeDefined();
  expect(screen.getByRole("combobox", { name: "Quality" }).textContent).toContain("Best · 240p");
  expect(screen.getByRole("combobox", { name: "Format" }).textContent).toContain("MP4");
  expect(screen.getByText(/About 725 KB/)).toBeDefined();
  // The link plays before it is downloaded, from the bottom bar's picture, in a player that closes.
  expect(screen.queryByLabelText("Preview")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Play preview" }));
  // The stream plays hidden; its pictures are drawn in the preview.
  expect(document.querySelector("video")?.getAttribute("src")).toBe(media.preview);
  expect(screen.getByRole("img", { name: "Preview" })).toBeDefined();
  // It plays as chosen: speed and sound.
  await act(() => setOptions({ speed: 2, mute: true }));
  expect(document.querySelector("video")).toMatchObject({ playbackRate: 2, muted: true });
  await act(() => setOptions({ speed: 1, mute: false }));
  fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
  expect(screen.queryByLabelText("Preview")).toBeNull();
  // Moving the extract opens it.
  const start = screen.getByRole("textbox", { name: "Start" });
  fireEvent.change(start, { target: { value: "0:02" } });
  fireEvent.blur(start);
  await waitFor(() => expect(useDownloads.getState().options.start).toBe(2));
  expect(screen.getByLabelText("Preview")).toBeDefined();
  await act(() => setOptions({ start: null }));

  // Video or sound first, then the kind of file.
  fireEvent.click(screen.getByRole("button", { name: "Sound only" }));
  expect(screen.getByRole("combobox", { name: "Format" }).textContent).toContain("M4A");
  expect(screen.queryByRole("combobox", { name: "Quality" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Audio codec" })).toBeNull();
  expect(screen.getByText(/About 302 KB/)).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Video" }));
  expect(useDownloads.getState().format).toBe("mp4");

  fireEvent.click(screen.getByRole("button", { name: "Download" }));
  // The job shows in the Queue tab, counted on it meanwhile.
  expect(await screen.findByRole("tab", { name: "Queue, 1 in progress" })).toBeDefined();
  expect(started).toEqual({
    request: { info: media.info, title: media.title, parts: media.qualities[0].parts, options: { ...noOptions, ...defaultOutput, cuts: [] } },
  });
  fireEvent.mouseDown(screen.getByRole("tab", { name: /Queue/ }));
  expect(await screen.findByText("Waiting…")).toBeDefined();

  await act(() => emit("job", { id: 7, step: "download", fraction: 0.5, eta: 3, path: null, error: null }));
  expect(screen.getByText("Downloading · 50% · 0:03 left")).toBeDefined();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeDefined();

  await act(() => emit("job", { id: 7, step: "done", fraction: null, eta: null, path: "C:/Users/me/Downloads/Me at the zoo.mp4", error: null }));
  expect(screen.getByText("Done")).toBeDefined();
  expect(screen.getByRole("button", { name: "Open" })).toBeDefined();
  expect(screen.getByRole("button", { name: "Show in folder" })).toBeDefined();
  expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  expect(screen.getByRole("tab", { name: "Queue" })).toBeDefined();
});

test("the title renames the file: before the download, while it runs and once saved", async () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Paste" }));
  rename(await screen.findByDisplayValue("Me at the zoo"), "  Zoo  ");
  await titled("Zoo");
  // Escape or an empty name keeps it.
  const field = screen.getByRole("textbox", { name: "File name" }) as HTMLInputElement;
  fireEvent.change(field, { target: { value: "Other" } });
  fireEvent.keyDown(field, { key: "Escape" });
  expect(field.value).toBe("Zoo");
  rename(field, " ");
  expect(field.value).toBe("Zoo");
  fireEvent.click(screen.getByRole("button", { name: "Download" }));
  await waitFor(() => expect(started).toMatchObject({ request: { title: "Zoo" } }));

  // While it runs: renamed once saved.
  fireEvent.mouseDown(screen.getByRole("tab", { name: /Queue/ }));
  rename(await screen.findByDisplayValue("Zoo"), "Zoo 2");
  expect(renamed).toEqual([]);
  await act(() => emit("job", { id: 7, step: "done", fraction: null, eta: null, path: "C:/Users/me/Downloads/Zoo.mp4", error: null }));
  await waitFor(() => expect(renamed).toEqual([{ path: "C:/Users/me/Downloads/Zoo.mp4", title: "Zoo 2" }]));
  // Once saved: at once.
  rename(screen.getByDisplayValue("Zoo 2"), "Zoo 3");
  await waitFor(() => expect(useJobs.getState().jobs[0].path).toBe("C:/Users/me/Downloads/Zoo 3.mp4"));
  expect(renamed[1]).toEqual({ path: "C:/Users/me/Downloads/Zoo 2.mp4", title: "Zoo 3" });
});

test("pasting a link analyzes it", async () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Paste" }));
  await titled("Me at the zoo");
  expect((screen.getByRole("textbox", { name: "Video link" }) as HTMLInputElement).value).toBe(media.url);
});

test("Ctrl+V with a link analyzes it, in the link field or outside any field", async () => {
  render(<App />);
  const link = screen.getByRole("textbox", { name: "Video link" }) as HTMLInputElement;
  const paste = (target: Element, text: string) => fireEvent.paste(target, { clipboardData: { getData: () => text } });
  // Not a link: pasted as usual.
  paste(document.body, "hello");
  expect(link.value).toBe("");
  paste(link, ` ${media.url}\n`);
  await titled("Me at the zoo");
  expect(link.value).toBe(media.url);
  // In another field, a link is only pasted there.
  analyzed = photoPost;
  paste(screen.getByRole("spinbutton", { name: "Size limit" }), photoPost.url);
  expect(link.value).toBe(media.url);
  paste(document.body, photoPost.url);
  await titled("Hehe");
  expect(link.value).toBe(photoPost.url);
});

test("a failed job starts again, and the finished ones can be cleared", async () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Paste" }));
  await screen.findByDisplayValue("Me at the zoo");
  fireEvent.click(screen.getByRole("button", { name: "Download" }));
  await waitFor(() => expect(useJobs.getState().jobs).toHaveLength(1));
  const first = started;
  await act(() => emit("job", { id: 7, step: "failed", fraction: null, eta: null, path: null, error: { key: "errors.network", params: { detail: "reset" } } }));
  fireEvent.mouseDown(screen.getByRole("tab", { name: /Queue/ }));
  expect(screen.getByText("Network error: reset")).toBeDefined();

  // The same request again, in place of the failed job.
  started = undefined;
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("Waiting…")).toBeDefined();
  expect(started).toEqual(first);
  expect(useJobs.getState().jobs.map((j) => j.id)).toEqual([8]);
  expect(screen.queryByRole("button", { name: "Clear finished" })).toBeNull();

  await act(() => emit("job", { id: 8, step: "done", fraction: null, eta: null, path: "C:/Users/me/Downloads/Me at the zoo.mp4", error: null }));
  await act(() => useJobs.setState(({ jobs }) => ({ jobs: [{ ...jobs[0], id: 9, step: "download", title: "Running" }, ...jobs] })));
  fireEvent.click(screen.getByRole("button", { name: "Clear finished" }));
  expect(useJobs.getState().jobs.map((j) => j.id)).toEqual([9]);
  expect(screen.getByDisplayValue("Running")).toBeDefined();
  expect(screen.queryByRole("button", { name: "Clear finished" })).toBeNull();

  // A re-encode that fails says so in plain words, FFmpeg's detail underneath.
  const encode = { key: "errors.encode", params: { detail: "write: Invalid argument" } };
  await act(() => emit("job", { id: 9, step: "failed", fraction: null, eta: null, path: null, error: encode }));
  expect(screen.getByText(/^Re-encoding failed: the file may be damaged/)).toBeDefined();
  expect(screen.getByText("write: Invalid argument")).toBeDefined();
});

test("sends the codecs of the settings and the frame rate", async () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Paste" }));
  await screen.findByDisplayValue("Me at the zoo");

  // 30 fps at most, the source's; one step down is 29.
  expect(screen.getByText("30 fps")).toBeDefined();
  const thumb = screen.getByRole("slider", { name: "Frame rate" });
  expect(thumb.getAttribute("aria-valuemax")).toBe("30");
  fireEvent.keyDown(thumb, { key: "ArrowLeft" });
  expect(screen.getByText("29 fps")).toBeDefined();
  // The picture's stream, 29/30 of it: 433,081 × 29 ÷ 30 + 309,288 ≈ 711 KB.
  expect(screen.getByText(/About 711 KB/)).toBeDefined();
  fireEvent.change(screen.getByRole("spinbutton", { name: "Size limit" }), { target: { value: "8" } });
  // Another sound codec in the settings: the size is only known from the limit.
  await act(() => useSettings.setState({ settings: { ...settings, audioCodec: "mp3" } }));
  await act(() => useDownloads.setState({ format: "mkv" }));
  // 8 MB is 7.6 MB counted as Windows does.
  expect(screen.getByText(/About 7\.6 MB/)).toBeDefined();
  // The sound codec is chosen in the settings only.
  expect(screen.queryByRole("combobox", { name: "Audio codec" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Download" }));
  await waitFor(() => expect(started).toBeDefined());
  expect((started as { request: { options: unknown } }).request.options).toEqual({
    ...noOptions,
    fps: 29,
    maxMb: 8,
    format: "mkv",
    audioCodec: "mp3",
    cuts: [],
  });

  // FLAC sound: no size limit; a sound-only file has no frame rate.
  await act(() => useSettings.setState({ settings: { ...settings, audioCodec: "flac" } }));
  expect(screen.queryByRole("spinbutton", { name: "Size limit" })).toBeNull();
  await act(() => useDownloads.setState({ format: "mp3" }));
  expect(screen.queryByRole("slider", { name: "Frame rate" })).toBeNull();
  expect(screen.queryByRole("switch", { name: "Remove sound" })).toBeNull();
});

test("offers no preview when the link does not play", async () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Paste" }));
  await screen.findByDisplayValue("Me at the zoo");
  fireEvent.click(screen.getByRole("button", { name: "Play preview" }));
  fireEvent.error(document.querySelector("video")!);
  expect(screen.queryByLabelText("Preview")).toBeNull();
  expect(screen.queryByRole("button", { name: "Play preview" })).toBeNull();
});

test("cuts the parts SponsorBlock lists in the chosen categories", async () => {
  analyzed = {
    ...media,
    segments: [
      { start: 2, end: 4, category: "sponsor" },
      { start: 3, end: 5, category: "intro" },
      { start: 10, end: 12, category: "outro" },
    ],
  };
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Paste" }));
  await screen.findByDisplayValue("Me at the zoo");
  // The sponsor and the intro overlap: one part, 3 s; outros are not chosen.
  expect(screen.getByText("1 part cut · 0:03")).toBeDefined();
  // 16 s kept of 19.
  expect(screen.getByText(/About 611 KB/)).toBeDefined();
  // Only what is inside the extract.
  await act(() => setOptions({ start: 3 }));
  expect(screen.getByText("1 part cut · 0:02")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Download" }));
  await waitFor(() => expect(started).toBeDefined());
  expect((started as { request: { options: { cuts: unknown } } }).request.options.cuts).toEqual([[3, 5]]);
  // SponsorBlock off: nothing cut.
  await act(() => useSettings.setState({ settings: { ...settings, sponsorBlock: false } }));
  expect(screen.queryByText(/part cut/)).toBeNull();
});

test("shows a readable error", async () => {
  mockIPC((cmd) => {
    if (cmd === "get_settings") return { ...settings, downloadDir: null };
    if (cmd === "analyze") throw { key: "errors.private", params: {} };
  }, { shouldMockEvents: true });
  render(<App />);
  fireEvent.change(screen.getByRole("textbox", { name: "Video link" }), { target: { value: "https://youtu.be/x" } });
  fireEvent.click(screen.getByRole("button", { name: "Analyze" }));
  expect((await screen.findByRole("alert")).textContent).toBe("This video is private.");
});

test.each([390, 1280])("sends the edit options at %ipx", async (width) => {
  window.innerWidth = width;
  render(<App />);
  fireEvent.change(screen.getByRole("textbox", { name: "Video link" }), { target: { value: media.url } });
  fireEvent.click(screen.getByRole("button", { name: "Analyze" }));
  await screen.findByDisplayValue("Me at the zoo");

  // Start at 0:05 (typed), then a bad end that reverts to the full length.
  const [start, end] = [screen.getByRole("textbox", { name: "Start" }), screen.getByRole("textbox", { name: "End" })];
  expect((end as HTMLInputElement).value).toBe("0:19");
  fireEvent.change(start, { target: { value: "0:05" } });
  fireEvent.blur(start);
  await waitFor(() => expect((start as HTMLInputElement).value).toBe("0:05"));
  fireEvent.change(end, { target: { value: "nope" } });
  fireEvent.blur(end);
  await waitFor(() => expect((end as HTMLInputElement).value).toBe("0:19"));

  fireEvent.click(screen.getByRole("switch", { name: "Remove sound" }));
  // 14 s of 19 without the audio stream: 433,081 × 14 ÷ 19 ≈ 312 KB.
  expect(screen.getByText(/About 312 KB/)).toBeDefined();
  // Any size, no presets.
  expect(screen.queryByRole("button", { name: "10 MB" })).toBeNull();
  fireEvent.change(screen.getByRole("spinbutton", { name: "Size limit" }), { target: { value: "10" } });
  expect((screen.getByRole("spinbutton", { name: "Size limit" }) as HTMLInputElement).value).toBe("10");

  fireEvent.click(screen.getByRole("button", { name: "Download" }));
  fireEvent.mouseDown(await screen.findByRole("tab", { name: /Queue/ }));
  await screen.findByText("Waiting…");
  expect(started).toEqual({
    request: {
      info: media.info,
      title: media.title,
      parts: media.qualities[0].parts,
      options: { start: 5, end: null, mute: true, speed: 1, maxMb: 10, fps: null, ...defaultOutput, cuts: [] },
    },
  });

  await act(() => emit("job", { id: 7, step: "encode", fraction: 0.25, eta: null, path: null, error: null }));
  expect(screen.getByText("Re-encoding · 25%")).toBeDefined();
  // Too small a limit is not an error: the file is saved, with a note.
  await act(() => emit("job", { id: 7, step: "done", fraction: null, eta: null, path: "C:/Downloads/zoo.mp4", error: { key: "jobs.overSize", params: { sizeMb: 12.5 } } }));
  expect(screen.getByText("Over the size limit: 12.5 MB is as small as this gets.")).toBeDefined();
  expect(screen.getByRole("button", { name: "Open" })).toBeDefined();
});

test.each([390, 1280])("offers the four ways to save a photo post at %ipx", async (width) => {
  window.innerWidth = width;
  analyzed = photoPost;
  render(<App />);
  fireEvent.change(screen.getByRole("textbox", { name: "Video link" }), { target: { value: photoPost.url } });
  fireEvent.click(screen.getByRole("button", { name: "Analyze" }));
  expect(await screen.findByText("love_horsys · 0:08 · 2 photos")).toBeDefined();
  // No format or quality: a slideshow by default, 4 s a photo shared from the 8 s sound.
  expect(screen.queryByRole("button", { name: "Video" })).toBeNull();
  expect(screen.getByRole("combobox").textContent).toContain("Slideshow video");
  expect((screen.getByRole("spinbutton", { name: "Seconds per photo" }) as HTMLInputElement).placeholder).toBe("Auto (4 s)");
  expect((screen.getByRole("textbox", { name: "End" }) as HTMLInputElement).value).toBe("0:08");
  // 1.5 s a photo makes a 3 s video.
  fireEvent.change(screen.getByRole("spinbutton", { name: "Seconds per photo" }), { target: { value: "1.5" } });
  expect((screen.getByRole("textbox", { name: "End" }) as HTMLInputElement).value).toBe("0:03");

  const send = async () => {
    fireEvent.click(screen.getByRole("button", { name: "Download" }));
    await waitFor(() => expect(started).toBeDefined());
    const request = (started as { request: Record<string, unknown> }).request;
    started = undefined;
    return request;
  };
  expect(await send()).toMatchObject({ parts: [sound], photos: { list: photoPost.photos, files: false, seconds: 1.5 } });

  const choose = (mode: string) => act(() => useDownloads.setState({ photoMode: mode as never }));
  await choose("single");
  fireEvent.click(screen.getByRole("button", { name: "Photo 2" }));
  expect(screen.getByRole("button", { name: "Photo 2" }).getAttribute("aria-pressed")).toBe("true");
  // One photo over the whole 8 s sound.
  expect(await send()).toMatchObject({ photos: { list: [photoPost.photos[1]], files: false, seconds: 8 } });

  await choose("photos");
  expect(screen.queryByText("Speed")).toBeNull();
  expect(await send()).toMatchObject({ parts: [], photos: { list: photoPost.photos, files: true } });

  await choose("sound");
  expect(screen.queryByRole("switch", { name: "Remove sound" })).toBeNull();
  const request = await send();
  expect(request).toMatchObject({ parts: [sound] });
  expect(request.photos).toBeUndefined();
});

test("a preset sets the options of the link, and of the next ones", async () => {
  // What Radix's select calls, which jsdom lacks.
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.scrollIntoView = () => {};
  presets = [{ name: "Small", format: "mkv", height: 200, mute: true, speed: 1.5, fps: null, maxMb: 5 }];
  render(<App />);
  fireEvent.change(screen.getByRole("textbox", { name: "Video link" }), { target: { value: media.url } });
  fireEvent.click(screen.getByRole("button", { name: "Analyze" }));
  await titled("Me at the zoo");
  const preset = screen.getByRole("combobox", { name: "Preset" });
  expect(preset.textContent).toBe("Custom");
  fireEvent.pointerDown(preset, { button: 0, ctrlKey: false, pointerType: "mouse" });
  fireEvent.click(await screen.findByRole("option", { name: "Small" }));
  // The highest quality up to the preset's.
  expect(screen.getByRole("combobox", { name: "Quality" }).textContent).toContain("144p");
  expect(screen.getByRole("combobox", { name: "Format" }).textContent).toContain("MKV");
  expect(screen.getByRole("switch", { name: "Remove sound" }).getAttribute("aria-checked")).toBe("true");
  expect(screen.getByRole("spinbutton", { name: "Size limit" })).toHaveProperty("value", "5");

  // The next link gets it too.
  analyzed = { ...media, title: "Another" };
  await act(() => setOptions({ mute: false }));
  expect(preset.textContent).toBe("Custom");
  fireEvent.pointerDown(preset, { button: 0, ctrlKey: false, pointerType: "mouse" });
  fireEvent.click(await screen.findByRole("option", { name: "Small" }));
  fireEvent.click(screen.getByRole("button", { name: "Analyze" }));
  await titled("Another");
  expect(useDownloads.getState()).toMatchObject({ preset: "Small", format: "mkv", quality: 144, options: { mute: true, speed: 1.5, maxMb: 5 } });
});
