import { beforeEach } from "vitest";
import { mockConvertFileSrc, mockWindows } from "@tauri-apps/api/mocks";

// jsdom has no ResizeObserver; Radix sliders only need it to exist.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// The app reads the current webview (file drops) and turns paths into preview URLs.
beforeEach(() => {
  mockWindows("main");
  mockConvertFileSrc("windows");
});
