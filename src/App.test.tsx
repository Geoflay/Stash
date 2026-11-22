import { afterEach, beforeEach, expect, test } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { clearMocks, mockIPC } from "@tauri-apps/api/mocks";
import { emit } from "@tauri-apps/api/event";
import App from "./App";
import { useEdit } from "@/features/edit/store";
import "./i18n";
import type { Settings } from "@/features/settings/settings";

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

let calls: { cmd: string; args: unknown }[] = [];

beforeEach(() => {
  calls = [];
  window.matchMedia = (query: string) =>
    ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }) as unknown as MediaQueryList;
  mockIPC((cmd, args) => {
    calls.push({ cmd, args });
    if (cmd === "get_settings") return settings;
    if (cmd === "ytdlp_version") return "2026.10.26";
  }, { shouldMockEvents: true });
});

afterEach(async () => {
  cleanup();
  // Let the job listener's async unlisten finish before the mocks go away.
  await new Promise((resolve) => setTimeout(resolve));
  clearMocks();
});

test.each([390, 1280])("shows both tabs and the settings button at %ipx", async (width) => {
  window.innerWidth = width;
  render(<App />);
  expect(screen.getByRole("tab", { name: "Download" })).toBeDefined();
  expect(screen.getByRole("tab", { name: "Edit" })).toBeDefined();
  expect(await screen.findByRole("button", { name: "Settings" })).toHaveProperty("disabled", false);
});

test("sets the codecs and the jobs at once in the settings", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
  expect(screen.getByRole("combobox", { name: "Audio codec" }).textContent).toContain("AAC");
  expect(screen.getByRole("button", { name: "3" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "2" }));
  await waitFor(() => expect(calls).toContainEqual({ cmd: "set_settings", args: { settings: { ...settings, maxJobs: 2 } } }));
});

test("each video codec has its own presets in the settings", async () => {
  // What Radix's select calls, which jsdom lacks.
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.scrollIntoView = () => {};
  const pick = async (name: string, option: string) => {
    fireEvent.pointerDown(screen.getByRole("combobox", { name }), { button: 0, ctrlKey: false, pointerType: "mouse" });
    fireEvent.click(await screen.findByRole("option", { name: option }));
  };
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
  expect(screen.getByRole("combobox", { name: "Encoding speed" }).textContent).toContain("Faster");
  await pick("Encoding speed", "Very slow");
  await waitFor(() => expect(calls).toContainEqual({ cmd: "set_settings", args: { settings: { ...settings, h264Preset: "veryslow" } } }));

  // AV1: SVT-AV1's presets, 0 to 13; H.264's is kept.
  await pick("Video codec", "AV1");
  const av1 = { ...settings, h264Preset: "veryslow", videoCodec: "av1" };
  await waitFor(() => expect(calls).toContainEqual({ cmd: "set_settings", args: { settings: av1 } }));
  const preset = screen.getByRole("combobox", { name: "Encoding speed" });
  expect(preset.textContent).toBe("8");
  fireEvent.pointerDown(preset, { button: 0, ctrlKey: false, pointerType: "mouse" });
  const options = await screen.findAllByRole("option");
  expect(options.map((o) => o.textContent)).toEqual(["0 · Slowest, best", ...Array.from({ length: 12 }, (_, i) => String(i + 1)), "13 · Fastest"]);
  fireEvent.click(screen.getByRole("option", { name: "4" }));
  await waitFor(() => expect(calls).toContainEqual({ cmd: "set_settings", args: { settings: { ...av1, av1Preset: 4 } } }));
});

test("sets the encoding priority and the graphics card in the settings", async () => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.scrollIntoView = () => {};
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
  const priority = screen.getByRole("combobox", { name: "Priority" });
  expect(priority.textContent).toBe("Below normal");
  fireEvent.pointerDown(priority, { button: 0, ctrlKey: false, pointerType: "mouse" });
  const options = await screen.findAllByRole("option");
  expect(options.map((o) => o.textContent)).toEqual(["Low", "Below normal", "Normal", "Above normal", "High"]);
  fireEvent.click(screen.getByRole("option", { name: "Low" }));
  await waitFor(() => expect(calls).toContainEqual({ cmd: "set_settings", args: { settings: { ...settings, encodingPriority: "idle" } } }));
  // The card's speeds show once it is on.
  expect(screen.queryByRole("combobox", { name: "Graphics card speed" })).toBeNull();
  fireEvent.click(screen.getByRole("switch", { name: "Use the graphics card" }));
  const gpu = { ...settings, encodingPriority: "idle", gpu: true };
  await waitFor(() => expect(calls).toContainEqual({ cmd: "set_settings", args: { settings: gpu } }));
  const speed = screen.getByRole("combobox", { name: "Graphics card speed" });
  expect(speed.textContent).toBe("4");
  fireEvent.pointerDown(speed, { button: 0, ctrlKey: false, pointerType: "mouse" });
  const speeds = await screen.findAllByRole("option");
  expect(speeds.map((o) => o.textContent)).toEqual(["1 · Fastest", "2", "3", "4", "5", "6", "7 · Slowest, best"]);
  fireEvent.click(screen.getByRole("option", { name: "7 · Slowest, best" }));
  await waitFor(() => expect(calls).toContainEqual({ cmd: "set_settings", args: { settings: { ...gpu, gpuPreset: 7 } } }));
});

test("chooses what SponsorBlock cuts in the settings", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
  expect(screen.getByRole("button", { name: "Sponsor" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: "Outro" }).getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(screen.getByRole("button", { name: "Outro" }));
  const categories = ["sponsor", "intro", "outro"];
  await waitFor(() => expect(calls).toContainEqual({ cmd: "set_settings", args: { settings: { ...settings, sponsorCategories: categories } } }));
  // Off: the categories are hidden.
  fireEvent.click(screen.getByRole("switch", { name: "SponsorBlock" }));
  await waitFor(() =>
    expect(calls).toContainEqual({ cmd: "set_settings", args: { settings: { ...settings, sponsorCategories: categories, sponsorBlock: false } } }),
  );
  expect(screen.queryByRole("button", { name: "Sponsor" })).toBeNull();
});

test("the title bar minimizes, maximizes and closes the window", async () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Minimize" }));
  fireEvent.click(screen.getByRole("button", { name: "Maximize" }));
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() =>
    expect(calls.map((c) => c.cmd)).toEqual(expect.arrayContaining(["plugin:window|minimize", "plugin:window|toggle_maximize", "plugin:window|close"])),
  );
});

test("renames and deletes presets in the settings", async () => {
  const small = { name: "Small", format: "mp4" as const, height: 720, mute: true, speed: 1.5, fps: 24, maxMb: null };
  mockIPC((cmd, args) => {
    calls.push({ cmd, args });
    if (cmd === "get_settings") return { ...settings, presets: [small] };
  }, { shouldMockEvents: true });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
  expect(screen.getByText("MP4 · 720p · Remove sound · 1.5× speed · 24 fps")).toBeDefined();
  const name = screen.getByRole("textbox", { name: "Preset name" });
  fireEvent.change(name, { target: { value: "Tiny" } });
  fireEvent.blur(name);
  await waitFor(() => expect(calls).toContainEqual({ cmd: "set_settings", args: { settings: { ...settings, presets: [{ ...small, name: "Tiny" }] } } }));
  fireEvent.click(screen.getByRole("button", { name: "Delete Tiny" }));
  await waitFor(() => expect(calls).toContainEqual({ cmd: "set_settings", args: { settings: { ...settings, presets: [] } } }));
  expect(screen.getByText(/as a preset to find them here/)).toBeDefined();
});

test("files opened with Edit with Stash replace those of the Edit tab", async () => {
  const media = (path: string) => {
    const name = path.split("/").pop();
    return { path, name, size: 1_000_000, duration: 10, width: 1280, height: 720, fps: 30, audio: true, videoCodec: "h264", audioCodec: "aac" };
  };
  let waiting = ["C:/Users/me/Videos/a.mp4"];
  mockIPC((cmd, args) => {
    calls.push({ cmd, args });
    if (cmd === "get_settings") return settings;
    if (cmd === "take_opened") {
      const paths = waiting;
      waiting = [];
      return paths;
    }
    if (cmd === "open_media") return media((args as { path: string }).path);
  }, { shouldMockEvents: true });
  render(<App />);
  await waitFor(() => expect(screen.getByRole("tab", { name: "Edit" }).getAttribute("aria-selected")).toBe("true"));
  await waitFor(() => expect(useEdit.getState().files.map((f) => f.media.name)).toEqual(["a.mp4"]));
  // Two files chosen together (one Stash started for each) replace it together.
  waiting = ["C:/Users/me/Videos/b.mp4"];
  await emit("opened");
  waiting = [...waiting, "C:/Users/me/Videos/c.mp4"];
  await emit("opened");
  await waitFor(() => expect(useEdit.getState().files.map((f) => f.media.name)).toEqual(["b.mp4", "c.mp4"]));
  // A file later replaces them.
  waiting = ["C:/Users/me/Videos/d.mp4"];
  await emit("opened");
  await waitFor(() => expect(useEdit.getState().files.map((f) => f.media.name)).toEqual(["d.mp4"]));
  useEdit.setState({ files: [], tab: "download" });
});
