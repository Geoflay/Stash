import i18n from "@/i18n";

/** m:ss, or h:mm:ss from one hour. */
export function formatDuration(seconds: number): string {
  const total = Math.round(seconds);
  const pad = (n: number) => String(n).padStart(2, "0");
  const [h, m, s] = [Math.floor(total / 3600), Math.floor((total % 3600) / 60), total % 60];
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** m:ss with tenths when there are some (1:05, 1:05.3); what `parse_time` reads back. */
export function formatTime(seconds: number): string {
  const tenths = Math.round(seconds * 10);
  const whole = formatDuration(Math.floor(tenths / 10));
  return tenths % 10 ? `${whole}.${tenths % 10}` : whole;
}

/** Size in KB, MB or GB (powers of 1024, as Windows shows them). */
export function formatSize(bytes: number): string {
  const units = ["units.kb", "units.mb", "units.gb"] as const;
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
  }
  const n = new Intl.NumberFormat(i18n.language, { maximumFractionDigits: value < 100 && unit > 0 ? 1 : 0 }).format(value);
  return i18n.t(units[unit], { n });
}

export function formatPercent(fraction: number): string {
  return new Intl.NumberFormat(i18n.language, { style: "percent" }).format(fraction);
}

/** A line of details, those left out (false, null, empty) skipped: "MP4 · 720p · About 5 MB". */
export function joinDetails(parts: unknown[]): string {
  return parts.filter(Boolean).join(" · ");
}

/** A number typed in a field, when above 0; `null` when empty or not. */
export function positiveNumber(text: string): number | null {
  const n = Number(text);
  return text && n > 0 ? n : null;
}
