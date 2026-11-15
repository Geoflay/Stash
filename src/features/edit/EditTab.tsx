import { convertFileSrc } from "@tauri-apps/api/core";
import { ChevronRight, FilePlus, FileVideo, RotateCcw, Save, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { ActionBar } from "@/components/action-bar";
import { EditableTitle } from "@/components/editable-title";
import { ExtractPlayer, MiniPlayer } from "@/components/media-preview";
import { Busy, ErrorNote } from "@/components/notice";
import { Choice, EditOptions, FormatOptions, panelClass } from "@/components/options-panel";
import { PresetRow } from "@/components/preset-row";
import { Button } from "@/components/ui/button";
import { formatDuration, formatPercent, formatSize } from "@/lib/format";
import { isAndroid } from "@/lib/platform";
import { cn } from "@/lib/utils";
import { AUDIO_FORMATS, isAudioFormat, isLossless, outputOf, VIDEO_FORMATS, type Options } from "@/features/download/store";
import { useSettings, type Settings } from "@/features/settings/settings";
import {
  applyPreset,
  copyOf,
  estimate,
  fixPreview,
  HEIGHTS,
  lowerHeights,
  mediaSoundFile,
  pickFiles,
  presetOptions,
  removeFile,
  renameEditFile,
  resetFile,
  saveCopies,
  selectFile,
  setEditChoice,
  setEditOptions,
  setFileOptions,
  shortSide,
  useEdit,
  type Choices,
  type Copy,
  type EditFile,
  type EditFormat,
  type EditState,
} from "./store";

const MIN_FPS = 5;

/** The kind of file and size, then the edits, of one file: `onChange` gets what changes. */
function FileOptions({
  copy,
  settings,
  onChange,
}: {
  copy: Copy;
  settings: Settings | null;
  onChange: (patch: Partial<Choices>) => void;
}) {
  const { t } = useTranslation();
  const { media, format, height, options } = copy;
  const video = media.width !== null && !isAudioFormat(format);
  return (
    <>
      <FormatOptions
        format={format}
        onFormat={(format) => onChange({ format })}
        soundFile={mediaSoundFile(media, settings)}
        qualities={[
          { value: "original", label: t("edit.original", { height: shortSide(media) ?? 0 }), size: null },
          ...lowerHeights(media).map((h) => ({ value: String(h), label: t("options.height", { height: h }), size: null })),
        ]}
        quality={height === null ? "original" : String(height)}
        onQuality={(value) => onChange({ height: value === "original" ? null : Number(value) })}
        canVideo={media.width !== null}
        canAudio={media.audio}
      />
      <EditOptions
        duration={media.duration}
        options={options}
        onOptions={onChange}
        canMute={video && media.audio}
        fps={video ? media.fps : null}
        sizeLimit={!isLossless(outputOf(format, settings))}
      />
    </>
  );
}

/** What a copy is saved as, in short: kind of file, size, frame rate and estimate. */
function summaryOf(t: TFunction, copy: Copy, size: number | null) {
  const { media, format, height, options } = copy;
  const video = media.width !== null && !isAudioFormat(format);
  const short = shortSide(media);
  const fps = options.fps ?? media.fps;
  return [
    t(`options.formats.${format}`),
    video && short && t("options.height", { height: height ?? short }),
    video && fps && t("options.fpsValue", { fps: Math.round(fps) }),
    size !== null && t("download.estimate", { size: formatSize(size) }),
  ];
}

/** The name the copy is saved as, the file's length, picture size and size, with
 * `children` (the buttons) beside them. */
function FileCard({ media, title, children }: { media: EditFile["media"]; title: string; children: ReactNode }) {
  const details = [
    formatDuration(media.duration),
    media.width !== null && media.height !== null && `${media.width} × ${media.height}`,
    formatSize(media.size),
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className="flex flex-col gap-2 min-[480px]:flex-row min-[480px]:items-center">
      <div className="min-w-0 min-[480px]:flex-1">
        <EditableTitle value={title} onRename={(title) => renameEditFile(0, title)} />
        <p className="truncate text-sm text-muted-foreground">{details}</p>
      </div>
      <div className="flex gap-2">{children}</div>
    </div>
  );
}

/** A file among several: its name and what it is saved as, then its own options, folded. */
function FileRow({
  index,
  file,
  copy,
  size,
  settings,
  onExtract,
}: {
  index: number;
  file: EditFile;
  copy: Copy;
  size: number | null;
  settings: Settings | null;
  /** The extract moved: its edge shows in the preview. */
  onExtract: () => void;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const own = Object.keys(file.own).length > 0;
  const summary = [formatDuration(file.media.duration), ...summaryOf(t, copy, size)].filter(Boolean).join(" · ");
  return (
    <li className="grid gap-2 py-2 first:pt-0 last:pb-0">
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="icon"
          className="-ml-2 shrink-0"
          aria-expanded={expanded}
          aria-label={t("edit.fileOptions", { name: file.title })}
          onClick={() => {
            setExpanded(!expanded);
            selectFile(index);
          }}
        >
          <ChevronRight className={cn("text-foreground transition-transform", expanded && "rotate-90")} />
        </Button>
        <div className="min-w-0 flex-1">
          <EditableTitle value={file.title} onRename={(title) => renameEditFile(index, title)} className="text-sm" />
          <p className="truncate text-xs text-muted-foreground">
            {own && <span className="font-medium text-foreground">{t("edit.own")} · </span>}
            {summary}
          </p>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="-mr-2 shrink-0"
          aria-label={t("edit.remove", { name: file.title })}
          onClick={() => removeFile(index)}
        >
          <X className="text-foreground" />
        </Button>
      </div>
      {expanded && (
        <div className={cn(panelClass, "rounded-lg border p-3")}>
          <FileOptions
            copy={copy}
            settings={settings}
            onChange={(patch) => {
              setFileOptions(index, patch);
              if ("start" in patch) onExtract();
            }}
          />
          <div>
            <Button variant="ghost" size="sm" disabled={!own} onClick={() => resetFile(index)}>
              <RotateCcw className="text-foreground" />
              {t("edit.reset")}
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}

const ALL_FORMATS: EditFormat[] = ["original", ...VIDEO_FORMATS, ...AUDIO_FORMATS];

/** The options several files share; each file settles them for itself. No extract: each
 * file has its own. */
function CommonOptions({ state, settings }: { state: EditState; settings: Settings | null }) {
  const { t } = useTranslation();
  const { files, format, height, options, preset } = state;
  const sound = format !== "original" && isAudioFormat(format);
  const videos = files.filter((f) => f.media.width !== null);
  // Up to the highest: a file with fewer frames keeps its own.
  const fps = Math.max(0, ...videos.map((f) => f.media.fps ?? 0)) || null;
  const showFps = !sound && fps !== null && fps > MIN_FPS;
  return (
    <div className={panelClass}>
      <PresetRow selected={preset} current={presetOptions(state)} onApply={applyPreset} />
      <Choice
        label={t("options.format")}
        value={format}
        onChange={(format) => setEditChoice({ format })}
        items={ALL_FORMATS.map((f) => ({ value: f, label: f === "original" ? t("edit.originalFormat") : t(`options.formats.${f}`) }))}
      />
      {!sound && videos.length > 0 && (
        <Choice
          label={t("options.quality")}
          value={height === null ? "original" : String(height)}
          onChange={(value) => setEditChoice({ height: value === "original" ? null : Number(value) })}
          items={[
            { value: "original", label: t("edit.originalSize") },
            ...HEIGHTS.map((h) => ({ value: String(h), label: t("options.height", { height: h }) })),
          ]}
        />
      )}
      <EditOptions
        duration={null}
        options={options}
        onOptions={setEditOptions}
        canMute={!sound && videos.some((f) => f.media.audio)}
        fps={sound ? null : fps}
        sizeLimit={format === "original" || !isLossless(outputOf(format, settings))}
      />
      {showFps && videos.some((f) => (f.media.fps ?? 0) < fps - 0.01) && (
        <p className="text-xs text-muted-foreground">{t("edit.fpsHint")}</p>
      )}
    </div>
  );
}

export function EditTab() {
  const { t } = useTranslation();
  const state = useEdit();
  const { files, selected, opening, error, preset, dragging, previewPath, preparing, prepared } = state;
  const settings = useSettings((s) => s.settings);
  // The file the mini-player shows; another file closes it.
  const [previewed, setPreviewed] = useState<string | null>(null);
  const copies = files.map((f) => copyOf(state, f, settings));
  const sizes = copies.map((c) => estimate(c, settings));
  const batch = files.length > 1;
  const shown = copies[selected] ?? copies[0];
  const previewing = shown !== undefined && previewed === shown.media.path;
  const total = sizes.every((s) => s !== null) ? sizes.reduce((sum, s) => sum + (s ?? 0), 0) : null;
  const details = batch
    ? [
        state.format === "original" ? t("edit.originalFormat") : t(`options.formats.${state.format}`),
        total !== null && t("edit.total", { size: formatSize(total) }),
      ]
    : shown && summaryOf(t, shown, sizes[0]);

  const button = (label: string, icon: ReactNode, onClick: () => void, className: string) => (
    <Button variant="outline" className={cn("rounded-full", className)} disabled={opening} onClick={onClick}>
      {icon}
      {label}
    </Button>
  );
  const addButton = (className: string) =>
    button(t("edit.addFiles"), <FilePlus className="text-foreground" />, () => void pickFiles({ add: true }), className);

  const showExtract = (path: string) => setPreviewed(path);

  return (
    <div className={cn("mx-auto grid w-full grid-cols-1 gap-3 lg:items-start", shown ? "max-w-5xl lg:grid-cols-2" : "max-w-2xl")}>
      <section
        className={cn("grid gap-3 rounded-xl border bg-card p-3 sm:p-4", dragging && "border-primary bg-primary/5")}
        aria-busy={opening}
      >
        {/* Once files are open, their names take the place of the prompt. */}
        {batch ? (
          <>
            <ul className="grid divide-y">
              {files.map((file, i) => (
                <FileRow
                  key={file.media.path}
                  index={i}
                  file={file}
                  copy={copies[i]}
                  size={sizes[i]}
                  settings={settings}
                  onExtract={() => {
                    selectFile(i);
                    showExtract(file.media.path);
                  }}
                />
              ))}
            </ul>
            {addButton("w-full min-[480px]:w-auto min-[480px]:justify-self-start")}
          </>
        ) : shown ? (
          <FileCard media={shown.media} title={shown.title}>
            {addButton("flex-1 min-[480px]:flex-none")}
            {button(t("edit.replace"), <FileVideo className="text-foreground" />, () => void pickFiles({ single: true }), "flex-1 min-[480px]:flex-none")}
          </FileCard>
        ) : (
          <div className="flex flex-col gap-2 min-[480px]:flex-row min-[480px]:items-center">
            <p className="text-sm text-muted-foreground min-[480px]:flex-1">
              {isAndroid ? t("edit.empty") : t("edit.emptyDrop")}
            </p>
            {button(t("edit.open"), <FileVideo className="text-foreground" />, () => void pickFiles(), "w-full min-[480px]:w-auto")}
          </div>
        )}

        {error && <ErrorNote error={error} />}

        {opening && <Busy>{t("edit.opening")}</Busy>}
      </section>

      {shown && (
        <section className="grid gap-3 rounded-xl border bg-card p-3 sm:p-4">
          {batch ? (
            <>
              <h2 className="text-sm font-medium">{t("edit.common")}</h2>
              <CommonOptions state={state} settings={settings} />
            </>
          ) : (
            <div className={panelClass}>
              <PresetRow selected={preset} current={presetOptions(state)} onApply={applyPreset} />
              <FileOptions
                copy={shown}
                settings={settings}
                onChange={({ format, height, ...options }) => {
                  if (format !== undefined) setEditChoice({ format });
                  if (height !== undefined) setEditChoice({ height });
                  if (Object.keys(options).length) setEditOptions(options as Partial<Options>);
                  // Moving the extract shows its edge in the preview.
                  if ("start" in options) showExtract(shown.media.path);
                }}
              />
            </div>
          )}
        </section>
      )}

      {shown && previewing && (
        <MiniPlayer onClose={() => setPreviewed(null)}>
          {preparing ? (
            <div className="p-3 pr-10">
              <Busy>{prepared > 0 ? t("edit.preparingPercent", { percent: formatPercent(prepared) }) : t("edit.preparing")}</Busy>
            </div>
          ) : previewPath ? (
            <ExtractPlayer
              src={convertFileSrc(previewPath)}
              video={shown.media.width !== null && !isAudioFormat(shown.format)}
              start={shown.options.start ?? 0}
              end={shown.options.end ?? shown.media.duration}
              speed={shown.options.speed}
              muted={shown.options.mute && shown.media.width !== null && !isAudioFormat(shown.format)}
              height={shown.height}
              fps={shown.options.fps}
              sourceFps={shown.media.fps}
              onUnplayable={() => void fixPreview()}
            />
          ) : (
            <p className="p-3 pr-10 text-sm text-muted-foreground">{t("edit.noPreview")}</p>
          )}
        </MiniPlayer>
      )}

      {shown && (
        <ActionBar
          title={batch ? t("edit.fileCount", { count: files.length }) : shown.title}
          details={(details ?? []).filter(Boolean).join(" · ")}
          onPreview={() => setPreviewed(previewing ? null : shown.media.path)}
          previewing={previewing}
        >
          <Button className="rounded-full" onClick={() => void saveCopies()}>
            <Save />
            {batch ? t("edit.saveAll", { count: files.length }) : t("edit.save")}
          </Button>
        </ActionBar>
      )}
    </div>
  );
}
