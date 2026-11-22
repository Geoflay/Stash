import { readText } from "@tauri-apps/plugin-clipboard-manager";
import { ClipboardPaste, Download, Link2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ActionBar } from "@/components/action-bar";
import { Card, TabColumns } from "@/components/card";
import { EditableTitle } from "@/components/editable-title";
import { ExtractPlayer, MiniPlayer } from "@/components/media-preview";
import { Busy, ErrorNote } from "@/components/notice";
import { EditOptions, formatName, FormatOptions, panelClass, Row } from "@/components/options-panel";
import { PresetRow } from "@/components/preset-row";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { formatDuration, formatSize, joinDetails } from "@/lib/format";
import { useSettings } from "@/features/settings/settings";
import { PhotoOptions } from "./PhotoOptions";
import {
  analyze,
  applyPreset,
  buildRequest,
  cutLength,
  cutsOf,
  download,
  estimate,
  isAudioFormat,
  isLossless,
  outputOf,
  presetOptions,
  renameMedia,
  resultDuration,
  selectedChoice,
  setOptions,
  soundFile,
  useDownloads,
  type Media,
} from "./store";

/** The link's thumbnail, title, author and length. */
function MediaCard({ media }: { media: Media }) {
  const { t } = useTranslation();
  const details = joinDetails([
    media.author,
    media.duration !== null && formatDuration(media.duration),
    media.photos.length > 0 && t("download.photoCount", { count: media.photos.length }),
  ]);
  return (
    <div className="flex gap-3">
      {media.thumbnail && (
        <img
          src={media.thumbnail}
          alt=""
          referrerPolicy="no-referrer"
          className="aspect-video w-28 shrink-0 rounded-md bg-muted object-cover sm:w-36"
        />
      )}
      <div className="min-w-0 flex-1">
        <EditableTitle value={media.title} onRename={renameMedia} />
        <p className="truncate text-sm text-muted-foreground">{details}</p>
      </div>
    </div>
  );
}

export function DownloadTab() {
  const { t } = useTranslation();
  const state = useDownloads();
  const settings = useSettings((s) => s.settings);
  const { url, media, analyzing, error, format, quality, options, photoMode, preset } = state;
  const output = outputOf(format, settings);
  const sound = isAudioFormat(format);
  const cuts = cutsOf(state, settings);
  const size = estimate(state, output, cuts);
  const photoPost = Boolean(media?.photos.length);
  // A stream that would not play: no preview.
  const [unplayable, setUnplayable] = useState<string | null>(null);
  const preview = media?.preview && media.preview !== unplayable ? media.preview : null;
  // The stream the mini-player shows; another link closes it.
  const [previewed, setPreviewed] = useState<string | null>(null);
  const previewing = preview !== null && previewed === preview;

  // "Best" (the highest height), then the lower ones.
  const [best, ...lower] = media?.qualities ?? [];
  const qualities = best
    ? [
        { value: "best", label: best.height ? t("options.best", { height: best.height }) : t("options.bestUnknown"), size: best.size },
        ...lower.map((q) => ({ value: String(q.height), label: t("options.height", { height: q.height }), size: q.size })),
      ]
    : [];

  const choice = selectedChoice(state);
  const details = joinDetails([
    photoPost ? t(`options.photoModes.${photoMode}`) : formatName(t, format),
    !photoPost && !sound && choice?.height && t("options.height", { height: choice.height }),
    size !== null && t("download.estimate", { size: formatSize(size) }),
  ]);

  async function paste() {
    const text = await readText().catch(() => "");
    if (!text.trim()) return;
    useDownloads.setState({ url: text.trim() });
    await analyze();
  }

  // Ctrl+V with a link, in the link field or outside any field, analyzes it.
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => {
    function onPaste(e: ClipboardEvent) {
      const text = e.clipboardData?.getData("text").trim() ?? "";
      const target = e.target instanceof Element ? e.target.closest("input, textarea, [contenteditable]") : null;
      if (!/^https?:\/\/\S+$/i.test(text) || (target && target !== field.current)) return;
      e.preventDefault();
      useDownloads.setState({ url: text });
      void analyze();
    }
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, []);

  return (
    <TabColumns wide={media !== null}>
      <Card>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void analyze();
          }}
        >
          <div className="relative min-w-0 flex-1">
            <Link2 aria-hidden className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              ref={field}
              type="url"
              inputMode="url"
              value={url}
              onChange={(e) => useDownloads.setState({ url: e.target.value })}
              placeholder="https://…"
              aria-label={t("download.url")}
              className="rounded-full pl-9"
            />
          </div>
          <Button type="button" variant="outline" size="icon" className="rounded-full" aria-label={t("download.paste")} onClick={paste}>
            <ClipboardPaste className="text-foreground" />
          </Button>
          <Button type="submit" className="rounded-full" disabled={analyzing || !url.trim()}>
            {t("download.analyze")}
          </Button>
        </form>

        {error && <ErrorNote error={error} />}

        {analyzing && <Busy>{t("download.analyzing")}</Busy>}

        {media ? (
          <MediaCard media={media} />
        ) : (
          !analyzing && <p className="text-sm text-muted-foreground">{t("download.empty")}</p>
        )}
      </Card>

      {media && (
        <Card>
          <div className={panelClass}>
            {photoPost ? (
              <PhotoOptions />
            ) : (
              <PresetRow selected={preset} current={presetOptions(state)} onApply={applyPreset} />
            )}
            {!photoPost && (
              <FormatOptions
                format={format}
                onFormat={(format) => useDownloads.setState({ format, preset: null })}
                soundFile={soundFile(settings)}
                qualities={qualities}
                quality={String(quality)}
                onQuality={(value) => useDownloads.setState({ quality: value === "best" ? "best" : Number(value), preset: null })}
                canVideo={media.qualities.length > 0}
                canAudio={media.audio !== null}
              />
            )}
            {!(photoPost && photoMode === "photos") && (
              <EditOptions
                duration={resultDuration(state)}
                options={options}
                onOptions={(patch) => {
                  setOptions(patch);
                  // Moving the extract shows its edge in the preview.
                  if ("start" in patch && preview) setPreviewed(preview);
                }}
                canMute={photoPost ? photoMode !== "sound" : !sound}
                fps={photoPost || sound ? null : (choice?.fps ?? null)}
                sizeLimit={photoPost || !isLossless(output)}
              />
            )}
            {cuts.length > 0 && (
              <Row label={t("download.sponsorBlock")}>
                <span className="text-muted-foreground tabular-nums">
                  {t("download.cuts", { count: cuts.length, duration: formatDuration(cutLength(cuts)) })}
                </span>
              </Row>
            )}
          </div>
        </Card>
      )}

      {media && previewing && (
        <MiniPlayer onClose={() => setPreviewed(null)}>
          <ExtractPlayer
            src={preview}
            video={!sound}
            start={options.start ?? 0}
            end={options.end ?? media.duration ?? Number.POSITIVE_INFINITY}
            cuts={cuts}
            speed={options.speed}
            muted={options.mute && !sound}
            height={choice?.height ?? null}
            fps={options.fps}
            sourceFps={choice?.fps ?? null}
            onUnplayable={() => setUnplayable(preview)}
          />
        </MiniPlayer>
      )}

      {media && (
        <ActionBar
          thumbnail={media.thumbnail}
          title={media.title}
          details={details}
          onPreview={preview ? () => setPreviewed(previewing ? null : preview) : undefined}
          previewing={previewing}
        >
          <Button className="rounded-full" disabled={!buildRequest(state, output, cuts)} onClick={download}>
            <Download />
            {t("download.download")}
          </Button>
        </ActionBar>
      )}
    </TabColumns>
  );
}
