/** Parts left out of a result, in source seconds, in order and apart. */
export type Cuts = readonly (readonly [number, number])[];

/** Seconds into the result at source time `t`, for an extract from `start` without `cuts`,
 * played at `speed`. */
export function toResult(t: number, start: number, cuts: Cuts, speed: number): number {
  const removed = cuts.reduce((sum, [a, b]) => sum + Math.min(Math.max(t - a, 0), b - a), 0);
  return (t - start - removed) / speed;
}

/** The source time `r` seconds into that result. */
export function toSource(r: number, start: number, cuts: Cuts, speed: number): number {
  let t = start + r * speed;
  for (const [a, b] of cuts) if (t >= a) t += b - a;
  return t;
}
