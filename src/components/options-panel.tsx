import { invoke } from "@tauri-apps/api/core";
import { useState, type ComponentProps, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Segmented } from "@/components/segmented";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import {
  AUDIO_FORMATS,
  isAudioFormat,
  VIDEO_FORMATS,
  type AudioFormat,
  type Format,
  type Options,
} from "@/features/download/store";
import i18n from "@/i18n";
import { formatSize, formatTime } from "@/lib/format";

export interface QualityOption {
  value: string;
  label: string;
  size: number | null;
}

function formatSpeed(speed: number) {
  return `${new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 2 }).format(speed)}×`;
}

/** An m:ss field; the core parses what is typed, and a bad value reverts. */
function TimeField({ label, value, onChange }: { label: string; value: number; onChange: (seconds: number) => void }) {
  const [text, setText] = useState<string | null>(null);
  async function commit() {
    if (text === null) return;
    const seconds = await invoke<number | null>("parse_time", { text }).catch(() => null);
    setText(null);
    if (seconds !== null) onChange(seconds);
  }
  return (
    <Input
      inputMode="decimal"
      aria-label={label}
      value={text ?? formatTime(value)}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") void commit();
      }}
      className="w-20 text-center tabular-nums"
    />
  );
}

/** The options panel: one line per option, split by rules. */
export const panelClass = "grid divide-y [&>*]:py-2 [&>*:first-child]:pt-0 [&>*:last-child]:pb-0";

/** The layout of a panel line: its name on the left, its control on the right. */
export const rowClass = "grid min-h-field grid-cols-[6.5rem_minmax(0,1fr)] items-center gap-x-3 text-sm";

/** A line of the panel: `label`, then its control. */
export function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className={rowClass}>
      <span className="font-medium">{label}</span>
      {children}
    </div>
  );
}

/** A slider with its value shown after it. */
function SliderRow({ label, shown, ...slider }: { label: string; shown: string } & Omit<ComponentProps<typeof Slider>, "thumbLabels">) {
  return (
    <Row label={label}>
      <div className="flex items-center gap-3">
        <Slider {...slider} thumbLabels={[label]} className="flex-1 py-2" />
        <span className="min-w-12 text-right whitespace-nowrap text-muted-foreground tabular-nums">{shown}</span>
      </div>
    </Row>
  );
}

/** A labelled drop-down list. */
export function Choice<T extends string>({
  label,
  value,
  onChange,
  items,
}: {
  label: string;
  value: T;
  onChange: (value: T) => void;
  items: { value: T; label: ReactNode }[];
}) {
  return (
    <Row label={label}>
      <Select value={value} onValueChange={(v) => onChange(v as T)}>
        <SelectTrigger aria-label={label} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent position="popper">
          {items.map((item) => (
            <SelectItem key={item.value} value={item.value} className="min-h-field">
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Row>
  );
}

/** Kind of file and quality of the result; the settings choose the codecs. */
export function FormatOptions({
  format,
  onFormat,
  soundFile,
  qualities,
  quality,
  onQuality,
  canVideo = true,
  canAudio = true,
}: {
  format: Format;
  onFormat: (format: Format) => void;
  /** The sound-only file offered first. */
  soundFile: AudioFormat;
  qualities: QualityOption[];
  quality: string;
  onQuality: (quality: string) => void;
  /** The source has a picture: video files are offered. */
  canVideo?: boolean;
  /** The source has sound: sound-only files are offered. */
  canAudio?: boolean;
}) {
  const { t } = useTranslation();
  const sound = isAudioFormat(format);
  return (
    <>
      {canVideo && canAudio && (
        <Row label={t("options.kind")}>
          <Segmented
            label={t("options.kind")}
            value={sound ? "sound" : "video"}
            onChange={(kind) => onFormat(kind === "sound" ? soundFile : "mp4")}
            options={[
              { value: "video", label: t("options.kinds.video") },
              { value: "sound", label: t("options.kinds.sound") },
            ]}
          />
        </Row>
      )}
      <Choice
        label={t("options.format")}
        value={format}
        onChange={onFormat}
        items={(sound ? AUDIO_FORMATS : VIDEO_FORMATS).map((f) => ({ value: f, label: t(`options.formats.${f}`) }))}
      />
      {!sound && (
        <Choice
          label={t("options.quality")}
          value={quality}
          onChange={onQuality}
          items={qualities.map((q) => ({
            value: q.value,
            label: (
              <>
                <span className="whitespace-nowrap">{q.label}</span>
                {q.size !== null && <span className="whitespace-nowrap text-muted-foreground">{formatSize(q.size)}</span>}
              </>
            ),
          }))}
        />
      )}
    </>
  );
}

/** Edits applied to the result, shared by the Download and Edit tabs. */
export function EditOptions({
  duration,
  options,
  onOptions,
  canMute,
  fps = null,
  sizeLimit = true,
}: {
  /** Length of the result before edits, in seconds; extracting needs it. */
  duration: number | null;
  options: Options;
  onOptions: (patch: Partial<Options>) => void;
  /** The result has video, so its sound can be removed. */
  canMute: boolean;
  /** The source's frame rate, when known and the result has video: a lower one can be chosen. */
  fps?: number | null;
  /** The size can be chosen (not with FLAC or WAV sound). */
  sizeLimit?: boolean;
}) {
  const { t } = useTranslation();
  const start = options.start ?? 0;
  const end = options.end ?? duration ?? 0;
  // Setting the whole length back means no extract (and no re-encoding). The fields show
  // tenths, so typing the shown length of a 7.02 s clip ("0:07") counts as the whole of it.
  const range = (from: number, to: number) => {
    if (duration === null) return;
    const s = Math.min(Math.max(from, 0), duration);
    const e = Math.min(Math.max(to, s), duration);
    onOptions({ start: s > 0.05 ? s : null, end: e < duration - 0.05 ? e : null });
  };

  return (
    <>
      {duration !== null && duration > 0 && (
        <div className="grid gap-1">
          <Row label={t("options.extract")}>
            <div className="flex items-center gap-2">
              <TimeField label={t("options.start")} value={start} onChange={(s) => range(s, end)} />
              <span aria-hidden className="text-muted-foreground">
                –
              </span>
              <TimeField label={t("options.end")} value={end} onChange={(e) => range(start, e)} />
            </div>
          </Row>
          <Slider
            min={0}
            max={duration}
            step={0.1}
            minStepsBetweenThumbs={1}
            value={[start, end]}
            onValueChange={([s, e]) => range(s, e)}
            thumbLabels={[t("options.start"), t("options.end")]}
            className="py-3"
          />
        </div>
      )}

      {canMute && (
        <label className={rowClass}>
          <span className="font-medium">{t("options.mute")}</span>
          <Switch checked={options.mute} onCheckedChange={(mute) => onOptions({ mute })} className="justify-self-end" />
        </label>
      )}

      <SliderRow
        label={t("options.speed")}
        shown={formatSpeed(options.speed)}
        min={0.1}
        max={5}
        step={0.05}
        value={[options.speed]}
        onValueChange={([speed]) => onOptions({ speed })}
      />

      {fps !== null && fps > MIN_FPS && <FrameRate source={fps} value={options.fps} onChange={(value) => onOptions({ fps: value })} />}

      {sizeLimit && (
        <Row label={t("options.sizeLimit")}>
          <div className="flex items-center gap-2">
            <Input
              type="number"
              inputMode="decimal"
              min={0}
              step="any"
              value={options.maxMb ?? ""}
              onChange={(e) => {
                const mb = Number(e.target.value);
                onOptions({ maxMb: e.target.value && mb > 0 ? mb : null });
              }}
              placeholder={t("options.noLimit")}
              aria-label={t("options.sizeLimit")}
              className="min-w-0 flex-1"
            />
            <span className="text-muted-foreground">{t("options.mb")}</span>
          </div>
        </Row>
      )}
    </>
  );
}

const MIN_FPS = 5;

/** Frames per second, from 5 up to the source's; the source's means no change. */
function FrameRate({ source, value, onChange }: { source: number; value: number | null; onChange: (fps: number | null) => void }) {
  const { t } = useTranslation();
  const max = Math.round(source);
  const shown = value === null ? max : Math.min(value, max);
  return (
    <SliderRow
      label={t("options.fps")}
      shown={t("options.fpsValue", { fps: shown })}
      min={MIN_FPS}
      max={max}
      step={1}
      value={[shown]}
      onValueChange={([fps]) => onChange(fps >= max ? null : fps)}
    />
  );
}
