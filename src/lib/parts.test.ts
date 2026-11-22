import { expect, test } from "vitest";
import { lastPart, nextPart } from "./parts";

test("the part playing comes first, then the one after it", () => {
  expect(nextPart(0, 95, new Set(), [])).toBe(0);
  expect(nextPart(42, 95, new Set(), [])).toBe(40);
  expect(nextPart(42, 95, new Set([40]), [[40, 50]])).toBe(50);
  expect(nextPart(42, 95, new Set([40, 50]), [[40, 60]])).toBeNull();
});

test("no part after the last one", () => {
  expect(lastPart(95)).toBe(90);
  expect(lastPart(100)).toBe(90);
  expect(lastPart(4)).toBe(0);
  expect(nextPart(100, 100, new Set(), [])).toBe(90);
  expect(nextPart(93, 95, new Set([90]), [[90, 94.96]])).toBeNull();
});

test("a part the webview dropped is made again", () => {
  expect(nextPart(5, 95, new Set([0, 10]), [[10, 20]])).toBe(0);
});
