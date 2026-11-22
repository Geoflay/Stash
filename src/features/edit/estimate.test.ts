import { expect, test } from "vitest";
import { noOptions } from "@/features/download/store";
import type { Settings } from "@/features/settings/settings";
import { estimate, type LocalMedia } from "./store";

const clip: LocalMedia = {
  path: "C:/Users/me/Videos/clip.mp4",
  name: "clip.mp4",
  size: 5_000_000,
  duration: 60,
  width: 1920,
  height: 1080,
  fps: 30,
  audio: true,
  videoCodec: "h264",
  audioCodec: "aac",
};
const copy = (media: LocalMedia) => ({ media, title: "clip", format: "mp4" as const, height: null, options: noOptions });
const av1 = { videoCodec: "av1", audioCodec: "aac" } as Settings;

test("a copy keeps the size of a picture already in the settings' codec", () => {
  expect(estimate(copy(clip), null)).toBe(clip.size);
  expect(estimate(copy({ ...clip, videoCodec: "av1" }), av1)).toBe(clip.size);
});

test("a picture in another codec is re-encoded: its size is unknown", () => {
  expect(estimate(copy({ ...clip, videoCodec: "av1" }), null)).toBeNull();
  expect(estimate(copy({ ...clip, videoCodec: "h265" }), null)).toBeNull();
  expect(estimate(copy(clip), av1)).toBeNull();
});
