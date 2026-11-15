import { expect, test } from "vitest";
import { toResult, toSource } from "./timeline";

test("maps source and result times around the parts cut", () => {
  const cuts = [[286.7, 335.8]] as const;
  // Before the part, in it (it counts as its end), after it.
  expect(toResult(286, 280, cuts, 1)).toBeCloseTo(6);
  expect(toResult(300, 280, cuts, 1)).toBeCloseTo(6.7);
  expect(toResult(340, 280, cuts, 1)).toBeCloseTo(10.9);
  expect(toResult(340, 280, cuts, 2)).toBeCloseTo(5.45);
  expect(toSource(6, 280, cuts, 1)).toBeCloseTo(286);
  expect(toSource(7, 280, cuts, 1)).toBeCloseTo(336.1);
  expect(toSource(5.45, 280, cuts, 2)).toBeCloseTo(340);
  expect(toResult(toSource(3, 0, [], 1.5), 0, [], 1.5)).toBeCloseTo(3);
});
