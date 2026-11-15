import { useTranslation } from "react-i18next";
import { Choice, rowClass } from "@/components/options-panel";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { setOptions, slideSeconds, slideSound, useDownloads, type PhotoMode } from "./store";

const MODES: PhotoMode[] = ["slideshow", "single", "photos", "sound"];

/** What to save from a TikTok photo post, in place of format and quality. */
export function PhotoOptions() {
  const { t } = useTranslation();
  const state = useDownloads();
  const { media, photoMode, photoIndex, photoSeconds } = state;
  if (!media) return null;
  const photos = media.photos;
  const auto = slideSeconds(photos.length, slideSound(state), null);

  function setMode(photoMode: PhotoMode) {
    useDownloads.setState({ photoMode });
    // The length of the result changes: start the extract over.
    setOptions({ start: null, end: null });
  }

  return (
    <>
      <Choice
        label={t("options.saveAs")}
        value={photoMode}
        onChange={setMode}
        items={MODES.filter((mode) => mode !== "sound" || media.audio).map((mode) => ({
          value: mode,
          label: <span className="whitespace-nowrap">{t(`options.photoModes.${mode}`)}</span>,
        }))}
      />

      {photoMode === "single" && photos.length > 1 && (
        <div role="group" aria-label={t("options.photoChoice")} className="grid grid-cols-4 gap-2">
          {photos.map((photo, i) => (
            <button
              key={photo.url}
              type="button"
              aria-label={t("options.photo", { n: i + 1 })}
              aria-pressed={i === photoIndex}
              onClick={() => useDownloads.setState({ photoIndex: i })}
              className={cn(
                "aspect-square min-h-field overflow-hidden rounded-md border-2 border-transparent bg-muted",
                i === photoIndex && "border-primary",
              )}
            >
              <img src={photo.url} alt="" referrerPolicy="no-referrer" className="size-full object-cover" />
            </button>
          ))}
        </div>
      )}

      {photoMode === "slideshow" && (
        <label className={rowClass}>
          <span className="font-medium">{t("options.secondsPerPhoto")}</span>
          <Input
            type="number"
            inputMode="decimal"
            min={0.5}
            step={0.5}
            value={photoSeconds ?? ""}
            onChange={(e) => {
              const seconds = Number(e.target.value);
              useDownloads.setState({ photoSeconds: e.target.value && seconds > 0 ? seconds : null });
              setOptions({ start: null, end: null });
            }}
            placeholder={t("options.auto", { seconds: Math.round(auto * 10) / 10 })}
          />
        </label>
      )}
    </>
  );
}
