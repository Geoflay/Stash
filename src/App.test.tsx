import { afterEach, beforeEach, expect, test } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { clearMocks, mockIPC } from "@tauri-apps/api/mocks";
import App from "./App";
import "./i18n";
import type { Settings } from "@/features/settings/settings";

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

test("chooses the encoders' preset in the settings", async () => {
  // What Radix's select calls, which jsdom lacks.
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.scrollIntoView = () => {};
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
  const preset = screen.getByRole("combobox", { name: "Encoding speed" });
  expect(preset.textContent).toContain("Faster");
  fireEvent.pointerDown(preset, { button: 0, ctrlKey: false, pointerType: "mouse" });
  fireEvent.click(await screen.findByRole("option", { name: "Medium" }));
  await waitFor(() => expect(calls).toContainEqual({ cmd: "set_settings", args: { settings: { ...settings, preset: "medium" } } }));
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
